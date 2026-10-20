"""Accounts for a few trusted users on one private instance (docs/v2.md, docs/v2-api.md).

Passwords use Werkzeug hashing. A login is a random opaque token: the signed Flask session cookie
carries it and SQLite stores only its SHA-256, so logout, password changes, resets and expiry
revoke it on the server. The current user is resolved per request into flask.g; clients never
supply identity. Login, password and account bodies are never logged or echoed.
"""

import collections
import hashlib
import re
import secrets
import sqlite3
import threading

from flask import current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

USERNAME = re.compile(r"[A-Za-z0-9_-]{3,32}")
PASSWORD_MIN, PASSWORD_MAX = 12, 1024
SESSION_TTL = 12 * 3600
LOGIN_WINDOW = 900.0
LOGIN_ATTEMPTS = 5  # failures per (address, username) per window
ADDRESS_ATTEMPTS = 20  # failures per address per window, across usernames
MAX_THROTTLE_KEYS = 1024
BAD_LOGIN = "Incorrect username or password."
THROTTLED = "Too many sign-in attempts. Wait a few minutes and try again."
SIGN_IN = "Sign in to continue."
NOT_ADMIN = "Administrator access is required."
# Everything else, including the voice socket, requires a server-validated login.
PUBLIC_ENDPOINTS = frozenset({"index", "static", "auth_state", "auth_setup", "auth_login", "auth_logout"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_sessions_by_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""


class AccountError(Exception):
    """Client-facing failure. message is static and never contains submitted values."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# --- Validation and hashing -----------------------------------------------

def validate_username(value):
    if not isinstance(value, str) or not USERNAME.fullmatch(value):
        raise AccountError(400, "Username must be 3 to 32 letters, digits, '_' or '-'.")
    return value.lower()


def validate_password(value):
    if not isinstance(value, str) or not PASSWORD_MIN <= len(value) <= PASSWORD_MAX:
        raise AccountError(400, f"Password must be {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise AccountError(400, "Password contains invalid characters.") from None
    return value


_dummy = {}


def check_password(password_hash, password):
    """Constant-shape check: unknown users still pay for one hash comparison."""
    if not isinstance(password, str) or len(password) > PASSWORD_MAX:
        return False
    if password_hash is None:
        if "hash" not in _dummy:
            _dummy["hash"] = generate_password_hash(secrets.token_urlsafe(16))
        password_hash = _dummy["hash"]
        password = "x" + password  # never matches
    try:
        return check_password_hash(password_hash, password)
    except (UnicodeEncodeError, ValueError):
        return False


def hash_token(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def public_user(row):
    return {"id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"])}


def now():
    return current_app.config["CLOCK"]()


# --- Throttling ------------------------------------------------------------

class LoginThrottle:
    """Bounded in-memory failure timestamps keyed by (address, username) and by address."""

    def __init__(self, clock):
        self.clock = clock
        self._lock = threading.Lock()
        self._failures = collections.OrderedDict()

    @staticmethod
    def keys(username):
        address = request.remote_addr or ""
        return (address, username[:64]), (address, None)

    def _recent(self, key, at):
        times = self._failures.get(key)
        while times and at - times[0] >= LOGIN_WINDOW:
            times.popleft()
        if times is not None and not times:
            del self._failures[key]
        return times or ()

    def blocked(self, keys):
        at = self.clock()
        with self._lock:
            pair, address = keys
            return (len(self._recent(pair, at)) >= LOGIN_ATTEMPTS
                    or len(self._recent(address, at)) >= ADDRESS_ATTEMPTS)

    def fail(self, keys):
        at = self.clock()
        with self._lock:
            for key, limit in zip(keys, (LOGIN_ATTEMPTS, ADDRESS_ATTEMPTS)):
                times = self._failures.setdefault(key, collections.deque(maxlen=limit))
                times.append(at)
                self._failures.move_to_end(key)
            while len(self._failures) > MAX_THROTTLE_KEYS:
                self._failures.popitem(last=False)

    def clear(self, keys):
        with self._lock:
            self._failures.pop(keys[0], None)


# --- Login state -----------------------------------------------------------

def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
    return session["csrf_token"]


def load_auth(db):
    """Resolve the signed cookie's token against auth_sessions into g.user / g.auth_token_hash."""
    g.user = g.auth_token_hash = None
    token = session.get("auth_token")
    if not isinstance(token, str) or not token:
        return None
    token_hash = hash_token(token)
    row = db.execute(
        "SELECT u.id, u.username, u.is_admin FROM auth_sessions a JOIN users u ON u.id = a.user_id"
        " WHERE a.token_hash = ? AND a.expires_at > ?", (token_hash, now())).fetchone()
    if row is not None:
        g.user, g.auth_token_hash = public_user(row), token_hash
    return g.user


def auth_valid(db, user_id, token_hash, at):
    """Whether a captured login is still live (used after slow work and by open voice streams)."""
    return db.execute("SELECT 1 FROM auth_sessions WHERE token_hash = ? AND user_id = ? AND expires_at > ?",
                      (token_hash, user_id, at)).fetchone() is not None


def end_login(db):
    """Revoke this browser's token (if any) and rotate its cookie state."""
    token = session.get("auth_token")
    if isinstance(token, str) and token:
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (hash_token(token),))
    session.clear()
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True


def start_login(db, user_id):
    """Replace any previous login of this browser with a fresh token and CSRF token."""
    end_login(db)
    at = now()
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (at,))
    db.execute("INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
               (hash_token(token), user_id, at, at + SESSION_TTL))
    session["auth_token"] = token


def require_admin():
    """Capture the acting administrator before any slow work: (user_id, token_hash).

    flask.g may be replaced by nested requests in the same app context, so callers keep these
    locals and recheck them with recheck_actor inside their write transaction.
    """
    if not g.get("user") or not g.user["is_admin"]:
        raise AccountError(403, NOT_ADMIN)
    return g.user["id"], g.auth_token_hash


def recheck_actor(db, user_id, token_hash, admin=False):
    """Inside a write transaction: the captured login is still live (and still an administrator)."""
    row = db.execute(
        "SELECT u.is_admin FROM auth_sessions a JOIN users u ON u.id = a.user_id"
        " WHERE a.token_hash = ? AND a.user_id = ? AND a.expires_at > ?", (token_hash, user_id, now())).fetchone()
    if row is None:
        raise AccountError(401, SIGN_IN)
    if admin and not row["is_admin"]:
        raise AccountError(403, NOT_ADMIN)


def password_hash_of(db, user_id):
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["password_hash"] if row else None


def throttle():
    return current_app.extensions["sous_login_throttle"]


# --- Routes ----------------------------------------------------------------

def register_routes(app, get_db, transaction, json_body, lock_vault):
    """lock_vault(user_id) locks that account's in-memory vault without creating one."""
    app.extensions["sous_login_throttle"] = LoginThrottle(app.config["CLOCK"])

    def state(db, status=200):
        setup_required = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
        return jsonify(user=g.get("user"), setup_required=setup_required, csrf_token=csrf_token()), status

    @app.get("/api/auth")
    def auth_state():
        return state(get_db())

    @app.post("/api/auth/setup")
    def auth_setup():
        db = get_db()
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():  # cheap refusal before hashing
            raise AccountError(409, "Setup is already complete. Sign in instead.")
        body = json_body()
        username = validate_username(body.get("username"))
        password_hash = generate_password_hash(validate_password(body.get("password")))
        with transaction(db):  # atomic: exactly one first administrator
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise AccountError(409, "Setup is already complete. Sign in instead.")
            user_id = db.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, 1, ?)",
                (username, password_hash, now())).lastrowid
            start_login(db, user_id)
        load_auth(db)
        return state(db, 201)

    @app.post("/api/auth/login")
    def auth_login():
        body = json_body()
        username, password = body.get("username"), body.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise AccountError(400, "Enter a username and password.")
        name = username.lower()
        keys = LoginThrottle.keys(name)
        if throttle().blocked(keys):
            raise AccountError(429, THROTTLED)
        db = get_db()
        row = None
        if USERNAME.fullmatch(name):
            row = db.execute("SELECT id, password_hash FROM users WHERE username = ?", (name,)).fetchone()
        if not check_password(row["password_hash"] if row else None, password):
            throttle().fail(keys)
            raise AccountError(401, BAD_LOGIN)
        user_id, checked_hash = row["id"], row["password_hash"]
        with transaction(db):
            # The slow check ran outside the write lock: a reset or change committed meanwhile
            # replaced the hash that was verified, so this login must not be minted.
            current = password_hash_of(db, user_id) == checked_hash
            if current:
                start_login(db, user_id)
        if not current:
            throttle().fail(keys)
            raise AccountError(401, BAD_LOGIN)
        throttle().clear(keys)
        load_auth(db)
        return state(db)

    @app.post("/api/auth/logout")
    def auth_logout():
        db = get_db()
        user = g.get("user")
        with transaction(db):
            end_login(db)
        if user:
            lock_vault(user["id"])  # open voice streams see the lock and the revoked token
        g.user = g.auth_token_hash = None
        return state(db)

    @app.post("/api/auth/password")
    def auth_password():
        user_id, username, token_hash = g.user["id"], g.user["username"], g.auth_token_hash  # captured
        body = json_body()
        new_password = validate_password(body.get("new_password"))
        keys = LoginThrottle.keys(username)
        if throttle().blocked(keys):
            raise AccountError(429, THROTTLED)
        db = get_db()
        checked_hash = password_hash_of(db, user_id)
        if not check_password(checked_hash, body.get("current_password")):
            throttle().fail(keys)
            raise AccountError(403, "Current password is incorrect.")
        password_hash = generate_password_hash(new_password)
        with transaction(db):
            # Both slow steps ran outside the write lock: a reset, change or logout committed
            # meanwhile wins, and this request must not overwrite it or mint a login.
            recheck_actor(db, user_id, token_hash)
            if password_hash_of(db, user_id) != checked_hash:
                raise AccountError(403, "Current password is incorrect.")
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            start_login(db, user_id)
        lock_vault(user_id)
        load_auth(db)
        return state(db)

    @app.get("/api/users")
    def list_users():
        require_admin()
        rows = get_db().execute("SELECT id, username, is_admin FROM users ORDER BY id").fetchall()
        return jsonify(users=[public_user(r) for r in rows])

    @app.post("/api/users")
    def create_user():
        admin_id, admin_token = require_admin()
        body = json_body()
        username = validate_username(body.get("username"))
        password = validate_password(body.get("password"))
        is_admin = body.get("is_admin", False)
        if type(is_admin) is not bool:
            raise AccountError(400, "is_admin must be true or false.")
        password_hash = generate_password_hash(password)
        db = get_db()
        try:
            with transaction(db):
                recheck_actor(db, admin_id, admin_token, admin=True)  # hashing ran outside the lock
                user_id = db.execute(
                    "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                    (username, password_hash, int(is_admin), now())).lastrowid
        except sqlite3.IntegrityError:
            raise AccountError(409, "That username is already taken.") from None
        return jsonify(user={"id": user_id, "username": username, "is_admin": is_admin}), 201

    @app.put("/api/users/<int:user_id>/password")
    def reset_password(user_id):
        admin_id, admin_token = require_admin()
        password = validate_password(json_body().get("password"))
        db = get_db()
        row = db.execute("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AccountError(404, "Account not found.")
        password_hash = generate_password_hash(password)
        with transaction(db):
            recheck_actor(db, admin_id, admin_token, admin=True)  # hashing ran outside the lock
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            # Every login of the target ends; an administrator resetting their own keeps this one.
            db.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?",
                       (user_id, admin_token))
        lock_vault(user_id)
        return jsonify(user=public_user(row))
