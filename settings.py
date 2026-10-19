"""Passphrase-encrypted settings vault for the managed AWS Bedrock connection.

The vault file holds a random salt, KDF parameters and one Fernet token (AES-CBC + HMAC,
authenticated). The key is derived from the user's passphrase with Scrypt and lives only in
this process's memory while unlocked; a restart or explicit lock() requires unlocking again.
This protects stored data at rest, not a compromised running machine. Python cannot reliably
wipe memory, so lock() drops references rather than zeroing bytes.

Nonsecret preferences (defaults, sample interval) are stored in SQLite by app.py, not here.
"""

import base64
import binascii
import collections
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VAULT_VERSION = 1
SALT_BYTES = 16
SCRYPT = {"name": "scrypt", "n": 2 ** 15, "r": 8, "p": 1}  # ~32 MiB per derivation
PASSPHRASE_MIN = 12
PASSPHRASE_MAX = 1024
UNLOCK_ATTEMPTS = 5  # failures allowed per window
UNLOCK_WINDOW = 60.0
MAX_VAULT_BYTES = 64 * 1024

MAX_MODEL = 200
REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d{1,2}")
ACCESS_KEY_ID = re.compile(r"[A-Z0-9]{16,128}")
PRINTABLE = re.compile(r"[!-~]+")  # printable ASCII, no whitespace
MODEL_ID = re.compile(r"[A-Za-z0-9._:/\-]+")
SECRET_MAX = 256
SESSION_TOKEN_MAX = 8192
BAD_UNLOCK = "Incorrect passphrase or damaged settings data."


class SettingsError(Exception):
    """Client-facing failure. message is static and never contains submitted values."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class BedrockConnection:
    """Immutable per-request snapshot of the managed connection. Secrets are kept out of repr."""
    region: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)


# --- Validation -----------------------------------------------------------

def validate_passphrase(value):
    if not isinstance(value, str) or not PASSPHRASE_MIN <= len(value) <= PASSPHRASE_MAX:
        raise SettingsError(400, f"Passphrase must be {PASSPHRASE_MIN} to {PASSPHRASE_MAX} characters.")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        raise SettingsError(400, "Passphrase contains invalid characters.") from None


def validate_model_id(value, required):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SettingsError(400, "model must be a string.")
    value = value.strip()
    if not value:
        if required:
            raise SettingsError(400, "A model ID is required.")
        return ""
    if len(value) > MAX_MODEL or not MODEL_ID.fullmatch(value):
        raise SettingsError(400, f"model must be a model or inference profile ID of at most {MAX_MODEL} characters.")
    return value


def _optional_secret(body, key, pattern, max_len, message):
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SettingsError(400, message)
    value = value.strip()
    if value and (len(value) > max_len or not pattern.fullmatch(value)):
        raise SettingsError(400, message)
    return value


def merge_bedrock(existing, body):
    """Return the new stored connection dict for PUT /api/settings/bedrock, or raise SettingsError.

    Blank secrets keep saved values. A new key pair must be supplied together and replaces any
    previously saved session token unless a new token is supplied with it.
    """
    existing = existing or {}
    region = body.get("region")
    if not isinstance(region, str) or len(region.strip()) > 32 or not REGION.fullmatch(region.strip()):
        raise SettingsError(400, "region must be an AWS region such as us-east-1.")
    model = existing.get("model", "") if "model" not in body else validate_model_id(body["model"], required=False)
    key_id = _optional_secret(body, "access_key_id", ACCESS_KEY_ID, 128,
                              "access_key_id must be 16-128 uppercase letters or digits.")
    secret = _optional_secret(body, "secret_access_key", PRINTABLE, SECRET_MAX,
                              f"secret_access_key must be printable text of at most {SECRET_MAX} characters.")
    token = _optional_secret(body, "session_token", PRINTABLE, SESSION_TOKEN_MAX,
                             f"session_token must be printable text of at most {SESSION_TOKEN_MAX} characters.")
    clear = body.get("clear_session_token", False)
    if type(clear) is not bool:
        raise SettingsError(400, "clear_session_token must be a boolean.")
    if clear and token:
        raise SettingsError(400, "Provide a new session token or clear it, not both.")
    if bool(key_id) != bool(secret):
        raise SettingsError(400, "Enter both the access key ID and secret access key to replace them.")

    if key_id:
        session_token = token or None
    else:
        if not existing.get("access_key_id"):
            raise SettingsError(400, "An access key ID and secret access key are required.")
        key_id, secret = existing["access_key_id"], existing["secret_access_key"]
        session_token = token or existing.get("session_token")
    if clear:
        session_token = None
    return {"region": region.strip(), "model": model, "access_key_id": key_id,
            "secret_access_key": secret, "session_token": session_token}


def _valid_payload(data):
    if not isinstance(data, dict) or set(data) != {"bedrock"}:
        return False
    conn = data["bedrock"]
    if conn is None:
        return True
    strings = ("region", "model", "access_key_id", "secret_access_key")
    return (isinstance(conn, dict) and all(isinstance(conn.get(k), str) for k in strings)
            and conn["access_key_id"] and conn["secret_access_key"]
            and (conn.get("session_token") is None or isinstance(conn["session_token"], str)))


# --- Vault ----------------------------------------------------------------

class Vault:
    """Thread-safe encrypted store. One instance per app; starts locked."""

    def __init__(self, path, clock):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self._fernet = self._data = self._salt = None
        self._failures = collections.deque(maxlen=UNLOCK_ATTEMPTS)  # bounded failure timestamps

    @property
    def initialized(self):
        return os.path.exists(self.path)

    @property
    def locked(self):
        return self._data is None

    @property
    def managed_bedrock(self):
        """Whether managed settings suppress environment fallback.

        The encrypted payload is authoritative when unlocked. While locked, any existing vault
        is treated as managed because an envelope marker cannot be trusted before decryption.
        """
        with self._lock:
            if self._data is not None:
                return self._data["bedrock"] is not None
            return self.initialized

    def state(self):
        """Atomic (locked, connection dict copy or None). Never raises."""
        with self._lock:
            if self._data is None:
                return True, None
            conn = self._data["bedrock"]
            return False, dict(conn) if conn else None

    def bedrock(self):
        """Copy of the decrypted connection dict, or None (no connection). Raises 409 if locked."""
        with self._lock:
            if self._data is None:
                raise SettingsError(409, "Settings are locked. Unlock them first.")
            conn = self._data["bedrock"]
            return dict(conn) if conn else None

    def connection(self):
        """BedrockConnection snapshot, or None when locked or no connection is saved."""
        with self._lock:
            conn = self._data and self._data["bedrock"]
            if not conn:
                return None
            return BedrockConnection(conn["region"], conn["access_key_id"], conn["secret_access_key"],
                                     conn.get("session_token") or None)

    def create(self, passphrase):
        secret = validate_passphrase(passphrase)
        with self._lock:
            if self.initialized:
                raise SettingsError(409, "Settings vault is already set up.")
            salt = os.urandom(SALT_BYTES)
            fernet = Fernet(derive_key(secret, salt))
            data = {"bedrock": None}
            self._write(salt, fernet, data)
            self._fernet, self._data = fernet, data

    def unlock(self, passphrase):
        secret = validate_passphrase(passphrase)
        with self._lock:
            now = self.clock()
            if len(self._failures) == UNLOCK_ATTEMPTS and now - self._failures[0] < UNLOCK_WINDOW:
                raise SettingsError(429, "Too many unlock attempts. Wait a minute and try again.")
            envelope = self._read_envelope()
            if envelope is None:
                raise SettingsError(409, "Settings vault is not set up yet.")
            try:
                salt = base64.b64decode(envelope["salt"], validate=True)
                if len(salt) != SALT_BYTES or envelope.get("kdf") != SCRYPT or not isinstance(envelope.get("token"), str):
                    raise ValueError
                fernet = Fernet(derive_key(secret, salt))
                data = json.loads(fernet.decrypt(envelope["token"].encode("ascii")))
                if not _valid_payload(data):
                    raise ValueError
            except (InvalidToken, KeyError, TypeError, ValueError, binascii.Error, UnicodeError):
                self._failures.append(now)
                raise SettingsError(403, BAD_UNLOCK) from None
            self._failures.clear()
            self._fernet, self._data, self._salt = fernet, data, salt

    def lock(self):
        with self._lock:
            self._fernet = self._data = self._salt = None

    def save_bedrock(self, conn):
        """Persist a new connection dict (or None to delete it). Requires unlocked vault."""
        with self._lock:
            if self._data is None:
                raise SettingsError(409, "Settings are locked. Unlock them first.")
            data = {**self._data, "bedrock": dict(conn) if conn else None}
            self._write(self._salt, self._fernet, data)
            self._data = data

    # New files omit source metadata: only the authenticated payload determines whether a managed
    # connection exists. Older version-1 files may contain managed_bedrock; it is ignored.

    def _write(self, salt, fernet, data):
        token = fernet.encrypt(json.dumps(data).encode("utf-8")).decode("ascii")
        envelope = {"version": VAULT_VERSION, "kdf": SCRYPT, "salt": base64.b64encode(salt).decode("ascii"),
                    "token": token}
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=directory)  # created 0600
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(envelope, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        self._salt = salt

    def _read_envelope(self):
        """Parsed envelope dict, None if missing; raises 403 (generic) if unreadable."""
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read(MAX_VAULT_BYTES + 1)
        except FileNotFoundError:
            return None
        try:
            if len(raw) > MAX_VAULT_BYTES:
                raise ValueError
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or envelope.get("version") != VAULT_VERSION:
                raise ValueError
        except ValueError:
            raise SettingsError(403, BAD_UNLOCK) from None
        return envelope

def derive_key(secret, salt):
    kdf = Scrypt(salt=salt, length=32, n=SCRYPT["n"], r=SCRYPT["r"], p=SCRYPT["p"])
    return base64.urlsafe_b64encode(kdf.derive(secret))
