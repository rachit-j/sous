"""Sous: Flask + SQLite backend for webcam-assisted cooking sessions on a private multi-user instance."""

import base64
import binascii
import hmac
import json
import math
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import NamedTuple

from flask import Flask, current_app, g, jsonify, render_template, request, session
from flask_sock import Sock
from werkzeug.exceptions import HTTPException

import accounts
import cooking
import library
import recipe_ai
import voice
from accounts import AccountError, csrf_token
from cooking import CookingError
from library import LibraryError
from recipes import load_recipes
from settings import BedrockConnection, SettingsError, Vault, merge_bedrock, validate_model_id

MODES = ("monitoring", "assistive")
ACTIONS = (
    "next", "back", "correct", "pause", "resume", "mode",
    "timer_set", "timer_cancel", "timer_ack", "finish", "end",
)
RECIPE_ACTIONS = ("next", "back", "correct", "finish", "resume")  # need the session's recipe
# (id, display name). Ollama is the shared local option (OLLAMA_MODEL default). Cloud providers need
# the account's own credentials: Bedrock uses the account's vault; OpenAI and Anthropic have no
# per-account credentials yet and are never configured from shared server environment keys.
PROVIDERS = (
    ("ollama", "Ollama (local)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("bedrock", "AWS Bedrock"),
)
SHARED_PROVIDERS = ("ollama",)
OWNER_FIELDS = ("user_id", "owner_id", "owner", "user", "username")  # identity comes from login only
SAMPLE_INTERVAL = 10  # default; users may save 1..MAX_SAMPLE_INTERVAL seconds
MIN_SAMPLE_INTERVAL, MAX_SAMPLE_INTERVAL = 1, 15
MAX_SETTINGS_BYTES = 16 * 1024
DISCOVERY_PATH = "/api/settings/bedrock/models"
LOCKED_BEDROCK = "Bedrock settings are locked. Unlock them in Settings; manual controls still work."
NO_BEDROCK = "Save an AWS connection in Settings first; manual controls still work."
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_JPEG_BYTES = 1024 * 1024
JPEG_PREFIX = "data:image/jpeg;base64,"
COMPLETION_CONFIDENCE = 0.85
COMPLETION_STREAK = 2
MAX_TIMER_SECONDS = 86400
MAX_NOTE = 500
MAX_TEXT = 1000
EVENT_LIMIT = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    step_index INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    monitoring INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    step_started_at REAL NOT NULL,
    timer_deadline REAL,
    timer_alerted INTEGER NOT NULL DEFAULT 0,
    completion_streak INTEGER NOT NULL DEFAULT 0,
    latest_observation TEXT,
    recipe_snapshot TEXT,
    user_id INTEGER,
    note TEXT,  -- private cook note (history)
    checklist TEXT  -- JSON {item_id: true} for the snapshot's preparation checklist
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, id);
CREATE TABLE IF NOT EXISTS settings (  -- V1 instance-wide preferences, preserved but unused
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    revision INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER,
    activity TEXT,
    evidence TEXT,
    confidence REAL,
    step_complete INTEGER,
    alert TEXT,
    error TEXT,
    created_at REAL NOT NULL
);
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    if test_config is None:
        from dotenv import load_dotenv
        load_dotenv()
    app.config.from_mapping(
        DATABASE=os.environ.get("SOUS_DATABASE") or os.path.join(app.instance_path, "sous.sqlite3"),
        SECRET_KEY=os.environ.get("SOUS_SECRET_KEY"),
        SECRET_KEY_FILE=os.path.join(app.instance_path, "secret_key"),
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=accounts.SESSION_TTL,  # the server-side login expires at the same age
        # Local-only app: reject other Host headers (DNS rebinding). Ports are ignored.
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        CLOCK=time.time,
        RECIPE_DIR=None,  # None: bundled recipes/ directory
        # V1 single-user vault path (None: settings.vault next to DATABASE). V2 never reads or writes
        # it; account vaults live under its directory in users/<id>/settings.vault.
        SETTINGS_VAULT=None,
        SOCK_SERVER_OPTIONS={"max_message_size": voice.MAX_SOCKET_MESSAGE, "ping_interval": 25},
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = load_secret_key(app.config["SECRET_KEY_FILE"])
    app.extensions["sous_observations"] = {"lock": threading.Lock(), "busy": set()}
    app.extensions["sous_recipes"] = load_recipes(app.config["RECIPE_DIR"])
    legacy_vault = app.config["SETTINGS_VAULT"] or os.path.join(
        os.path.dirname(os.path.abspath(app.config["DATABASE"])), "settings.vault")
    app.extensions["sous_vaults"] = {"lock": threading.Lock(), "vaults": {},  # all start locked
                                     "root": os.path.join(os.path.dirname(os.path.abspath(legacy_vault)), "users")}
    app.extensions["sous_discovery"] = threading.Lock()
    # One voice stream instance-wide for this private release; tests may replace "open_stream".
    app.extensions["sous_voice"] = voice.new_state()
    app.extensions["sous_generation"] = recipe_ai.Limiter(app.config["CLOCK"])

    init_db(app.config["DATABASE"], app.config["CLOCK"](), app.extensions["sous_recipes"])
    app.teardown_appcontext(close_db)
    app.register_error_handler(ApiError, lambda e: (jsonify(error=e.message), e.status))
    app.register_error_handler(SettingsError, lambda e: (jsonify(error=e.message), e.status))
    app.register_error_handler(AccountError, lambda e: (jsonify(error=e.message), e.status))
    app.register_error_handler(LibraryError, lambda e: (jsonify(error=e.message), e.status))
    app.register_error_handler(CookingError, lambda e: (jsonify(error=e.message), e.status))
    app.register_error_handler(HTTPException, http_error)
    app.register_error_handler(Exception, unexpected_error)
    app.before_request(check_origin_and_csrf)
    app.before_request(require_login)
    app.after_request(security_headers)
    register_routes(app)
    accounts.register_routes(app, get_db, transaction, json_body,
                             lambda user_id: lock_user_vault(app, user_id))
    accounts.register_cli(app, get_db, transaction, lambda user_id: lock_user_vault(app, user_id))
    register_settings_routes(app)
    register_library_routes(app)
    register_cooking_routes(app)
    register_voice_routes(app, Sock(app))
    return app


def user_vault(app, user_id):
    """The account's own vault at <data dir>/users/<id>/settings.vault (server integer id only)."""
    registry = app.extensions["sous_vaults"]
    user_id = int(user_id)
    with registry["lock"]:
        vault = registry["vaults"].get(user_id)
        if vault is None:
            path = os.path.join(registry["root"], str(user_id), "settings.vault")
            vault = registry["vaults"][user_id] = Vault(path, app.config["CLOCK"])
        return vault


def lock_user_vault(app, user_id):
    with app.extensions["sous_vaults"]["lock"]:
        vault = app.extensions["sous_vaults"]["vaults"].get(int(user_id))
    if vault is not None:
        vault.lock()


def load_secret_key(path):
    try:
        with open(path) as fh:
            key = fh.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key)
    return key


# --- Database -------------------------------------------------------------

def connect(path):
    db = sqlite3.connect(path, isolation_level=None, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db(path, now, recipes):
    """Create/upgrade tables and pause any session that was monitoring before restart.

    Upgrades are additive only: older databases gain recipe_snapshot, backfilled from the
    current definition when one exists. Rows whose recipe is gone keep a NULL snapshot.
    V1 rows gain a NULL user_id and stay unowned (never visible to accounts); the V1 settings
    table is kept as is. One active session per account replaces the instance-wide constraint.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = connect(path)
    try:
        db.executescript(SCHEMA)
        db.executescript(accounts.SCHEMA)
        db.executescript(library.SCHEMA)
        db.executescript(cooking.SCHEMA)
        columns = {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}
        if "recipe_snapshot" not in columns:
            db.execute("ALTER TABLE sessions ADD COLUMN recipe_snapshot TEXT")
        if "user_id" not in columns:
            db.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER")
        for name, kind in cooking.SESSION_COLUMNS:  # internal constants, never user input
            if name not in columns:
                db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {kind}")
        db.execute("DROP INDEX IF EXISTS one_active_session")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_session_per_user ON sessions(user_id)"
                   " WHERE status = 'active' AND user_id IS NOT NULL")
        db.execute("CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id, id)")
        with transaction(db):
            for row in db.execute(
                "SELECT id, recipe_id, step_index FROM sessions WHERE recipe_snapshot IS NULL"
            ).fetchall():
                recipe = recipes.get(row["recipe_id"])
                if recipe and row["step_index"] < len(recipe["steps"]):
                    update_session(db, row["id"], {"recipe_snapshot": json.dumps(recipe)})
            for row in db.execute(
                "SELECT id, revision FROM sessions WHERE status = 'active' AND monitoring = 1"
            ).fetchall():
                update_session(db, row["id"], {
                    "monitoring": 0, "completion_streak": 0,
                    "revision": row["revision"] + 1, "updated_at": now,
                })
                log_event(db, row["id"], "monitoring", "Monitoring paused after server restart.", now)
    finally:
        db.close()


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db, guard=None):
    """BEGIN IMMEDIATE ... COMMIT.

    guard (e.g. Vault.guard()) is entered after the write lock and held through COMMIT, so a
    lease checked inside cannot be invalidated before the writes are durable. Lock order is
    always database first, then guard.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        if guard is None:
            yield db
            db.execute("COMMIT")
        else:
            with guard:
                yield db
                db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def now():
    return current_app.config["CLOCK"]()


def current_user_id():
    return g.user["id"]


def load_session(db, session_id, user_id):
    """The caller's own session. Other accounts' and unowned V1 rows look exactly like missing ones."""
    row = db.execute("SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
    if row is None:
        raise ApiError(404, "Session not found.")
    return row


def update_session(db, session_id, changes):
    # Keys are internal column names, never user input.
    columns = ", ".join(f"{key} = ?" for key in changes)
    db.execute(f"UPDATE sessions SET {columns} WHERE id = ?", (*changes.values(), session_id))


def log_event(db, session_id, kind, message, at):
    db.execute(
        "INSERT INTO events (session_id, kind, message, created_at) VALUES (?, ?, ?, ?)",
        (session_id, kind, message, at),
    )


def session_json(db, row):
    """Owner view of a session, including bounded cooking-tool state for polling clients."""
    data = dict(row)
    data.pop("user_id", None)
    data.pop("checklist", None)
    data["note"] = data.get("note") or ""
    data["monitoring"] = bool(data["monitoring"])
    data["timer_alerted"] = bool(data["timer_alerted"])
    data["latest_observation"] = json.loads(data["latest_observation"]) if data["latest_observation"] else None
    snapshot = data.pop("recipe_snapshot", None)
    data["recipe"] = json.loads(snapshot) if snapshot else None
    data["events"] = [dict(e) for e in db.execute(
        "SELECT id, kind, message, created_at FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (row["id"], EVENT_LIMIT),
    )]
    at = now()
    data["timers"] = cooking.timers_json(db, row["id"], at)
    data["checklist"] = cooking.checklist_json(row, data["recipe"])
    data["alerts"] = cooking.alerts_json(db, row["id"])
    data["monitoring_health"] = cooking.monitoring_health(db, row, data["latest_observation"], at,
                                                          get_sample_interval(db, row["user_id"]))
    return data


def recipe_for_session(row):
    """The recipe a session started with. Never substitutes a different current definition."""
    if not row["recipe_snapshot"]:
        raise ApiError(409, "This session's recipe is no longer available. End the session to start a new one.")
    return json.loads(row["recipe_snapshot"])


def bundled_recipes():
    """Read-only recipes/*.json, shared by every account."""
    return current_app.extensions["sous_recipes"]


def current_recipes():
    """{id: recipe} the signed-in account may cook: bundled first, then visible library recipes."""
    return library.cookable(get_db(), g.user, bundled_recipes())


def respond_session(session_id, user_id, status=200):
    db = get_db()
    return jsonify(session_json(db, load_session(db, session_id, user_id))), status


# --- Request validation ---------------------------------------------------

def http_error(exc):
    if exc.code is None or exc.code < 400:  # e.g. routing redirects
        return exc
    messages = {404: "Not found.", 405: "Method not allowed.", 413: "Request is too large."}
    return jsonify(error=messages.get(exc.code, exc.name)), exc.code


def unexpected_error(exc):
    if request.path.startswith(("/api/settings", "/api/auth", "/api/users", "/api/recipes", "/api/profile",
                                "/api/history")):
        # No traceback: exception text or frames could carry submitted credentials, passwords,
        # recipe requests or model output.
        current_app.logger.error("Unhandled error in %s request: %s", request.path.split("/")[2],
                                 type(exc).__name__)
    else:
        current_app.logger.exception("Unhandled error")
    return jsonify(error="Internal server error."), 500


def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):  # account data, including errors, is never cached
        response.headers["Cache-Control"] = "no-store"
    return response


def require_login():
    """Every endpoint except the app shell and bounded auth actions needs a live server-side login.

    The voice socket is also checked for session ownership here, before any WebSocket upgrade.
    """
    if request.endpoint is None or request.endpoint == "static":
        return None
    user = accounts.load_auth(get_db())
    if request.endpoint in accounts.PUBLIC_ENDPOINTS:
        return None
    if user is None:
        raise AccountError(401, accounts.SIGN_IN)
    if request.endpoint == "voice_socket":
        load_session(get_db(), request.view_args["session_id"], user["id"])
    return None


def check_origin_and_csrf():
    # The voice WebSocket upgrade must come from this origin before it is accepted; its CSRF
    # token arrives in the first message (voice.serve), before any AWS call.
    if request.endpoint == "voice_socket":
        if request.headers.get("Origin") != request.host_url.rstrip("/"):
            raise ApiError(403, "Cross-origin request rejected.")
        return None
    # Model discovery is a GET that makes an outbound AWS call, so it also requires the token.
    if request.method in ("GET", "HEAD", "OPTIONS") and request.path != DISCOVERY_PATH:
        return None
    origin = request.headers.get("Origin")
    if origin is not None and origin != request.host_url.rstrip("/"):
        raise ApiError(403, "Cross-origin request rejected.")
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token", "")
    # Compare bytes: compare_digest raises TypeError for non-ASCII str.
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise ApiError(403, "Missing or invalid CSRF token. Reload the page.")
    return None


def json_body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(400, "Request body must be a JSON object.")
    return body


def limited_body(limit):
    """json_body with a strict byte budget, also for bodies sent without Content-Length."""
    if request.content_length is not None and request.content_length > limit:
        raise ApiError(413, "Request is too large.")
    if len(request.get_data(cache=True)) > limit:
        raise ApiError(413, "Request is too large.")
    return json_body()


def is_int(value):
    return type(value) is int


def decode_jpeg(image):
    if not isinstance(image, str) or not image.startswith(JPEG_PREFIX):
        raise ApiError(400, "image must be a base64 JPEG data URL.")
    try:
        data = base64.b64decode(image[len(JPEG_PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, "image is not valid base64.") from None
    if len(data) > MAX_JPEG_BYTES:
        raise ApiError(413, "Image is too large.")
    if not data.startswith(b"\xff\xd8\xff"):
        raise ApiError(400, "image is not a JPEG.")
    return data


def provider_catalog(db, user_id):
    """Providers with configuration state for one account. Bedrock uses only the account's own
    saved connection: while locked or unsaved it is unconfigured, never the server environment."""
    defaults = get_defaults(db, user_id)
    vault = current_vault()
    catalog = []
    for pid, name in PROVIDERS:
        shared = pid in SHARED_PROVIDERS
        entry = {"id": pid, "name": name, "configured": shared,
                 "default_model": os.environ.get("OLLAMA_MODEL", "") if shared else ""}
        if pid == "bedrock":
            managed = vault.managed_bedrock
            locked, conn = vault.state()
            entry["source"] = "managed" if managed else "none"
            entry["locked"] = managed and locked
            entry["configured"] = conn is not None
            entry["default_model"] = conn["model"] if conn else ""
        if defaults["provider"] == pid:
            entry["default_model"] = defaults["model"]
        catalog.append(entry)
    return catalog


# --- Settings and preferences ---------------------------------------------

def current_vault():
    return user_vault(current_app, current_user_id())


CREDENTIALS_CHANGED = "Settings changed while the model was working; that result was discarded. Try again."


class CredentialLease(NamedTuple):
    """The caller's login and own vault state, captured in the request before slow external work.

    An upstream call that was already sent cannot be recalled; its result is only written if the
    lease is still valid under leased_transaction.
    """
    user_id: int
    token_hash: str
    vault: Vault
    epoch: int
    connection: BedrockConnection | None  # None: the work does not use the vault


def lease_credentials():
    vault = current_vault()
    epoch, connection = vault.lease()
    return CredentialLease(current_user_id(), g.auth_token_hash, vault, epoch, connection)


@contextmanager
def leased_transaction(db, lease):
    """Write transaction that yields None if the lease is still valid, else the error to raise
    after the block (the caller must then write nothing). With a connection, the vault guard is
    held from the check through COMMIT, so a lock/unlock/save either completes first (rejecting
    this result) or waits until the result is committed."""
    guard = lease.vault.guard() if lease.connection is not None else None
    with transaction(db, guard):
        if not accounts.auth_valid(db, lease.user_id, lease.token_hash, now()):
            yield AccountError(401, accounts.SIGN_IN)
        elif lease.connection is not None and lease.vault.epoch != lease.epoch:
            yield ApiError(409, CREDENTIALS_CHANGED)
        else:
            yield None


def get_pref(db, user_id, key):
    row = db.execute("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except ValueError:
        return None


def set_pref(db, user_id, key, value):
    db.execute("INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)"
               " ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (user_id, key, json.dumps(value)))


def valid_interval(value):
    return is_int(value) and MIN_SAMPLE_INTERVAL <= value <= MAX_SAMPLE_INTERVAL


def get_sample_interval(db, user_id):
    value = get_pref(db, user_id, "sample_interval")
    return value if valid_interval(value) else SAMPLE_INTERVAL


def get_voice_id(db, user_id):
    value = get_pref(db, user_id, "voice_id")
    return value if value in voice.VOICES else voice.DEFAULT_VOICE


def voice_json(db, user_id):
    """Safe voice metadata from in-memory state; never calls AWS."""
    available, reason = voice.voice_status(current_vault())
    data = {"model": voice.MODEL_ID, "voice_id": get_voice_id(db, user_id), "available": available}
    if reason:
        data["reason"] = reason
    if reason == voice.UNSUPPORTED_REGION:
        data["supported_regions"] = list(voice.SUPPORTED_REGIONS)
    return data


def get_defaults(db, user_id):
    value = get_pref(db, user_id, "defaults")
    known = {p[0] for p in PROVIDERS}
    if (isinstance(value, dict) and value.get("provider") in known
            and isinstance(value.get("model"), str) and value["model"]):
        return {"provider": value["provider"], "model": value["model"]}
    return {"provider": None, "model": None}


def settings_body():
    if request.content_length is not None and request.content_length > MAX_SETTINGS_BYTES:
        raise ApiError(413, "Request is too large.")
    return json_body()


def settings_json(db, user_id):
    """Safe settings view for one account: never includes credentials or the passphrase."""
    vault = current_vault()
    managed = vault.managed_bedrock
    locked, conn = vault.state()
    if locked:
        bedrock = {"region": None, "model": None, "has_credentials": None, "has_session_token": None}
    else:
        conn = conn or {}
        bedrock = {"region": conn.get("region", ""), "model": conn.get("model", ""),
                   "has_credentials": bool(conn), "has_session_token": bool(conn.get("session_token"))}
    bedrock["source"] = "managed" if managed else "none"  # never the shared server environment
    return {
        "vault": {"initialized": vault.initialized, "locked": locked},
        "bedrock": bedrock,
        "defaults": get_defaults(db, user_id),
        "sample_interval": get_sample_interval(db, user_id),
        "voice": voice_json(db, user_id),
    }


def respond_settings(status=200):
    return jsonify(settings_json(get_db(), current_user_id())), status


# --- State transitions ----------------------------------------------------

def apply_action(row, body, at):
    """Return (changes, (kind, message) | None) for an action, or raise ApiError.

    Pure: callers persist the result. Empty changes means no mutation.
    """
    action = body.get("action")
    if action not in ACTIONS:
        raise ApiError(400, "Unknown action.")
    if row["status"] != "active":
        raise ApiError(409, "This session has already finished.")

    # Actions that don't need recipe steps (end, pause, mode, timers) still work for a
    # legacy session whose recipe definition is unavailable.
    steps = recipe_for_session(row)["steps"] if action in RECIPE_ACTIONS else []
    last = len(steps) - 1
    step = row["step_index"]
    invalidate = {"revision": row["revision"] + 1, "completion_streak": 0}

    def move(index):
        return {**invalidate, "step_index": index, "step_started_at": at}

    if action == "next":
        if step >= last:
            raise ApiError(409, "Already on the final step; use Finish.")
        return move(step + 1), ("navigation", f"Moved to step {step + 2}: {steps[step + 1]['title']}.")
    if action == "back":
        if step <= 0:
            raise ApiError(409, "Already on the first step.")
        return move(step - 1), ("navigation", f"Moved back to step {step}: {steps[step - 1]['title']}.")
    if action == "correct":
        index, note = body.get("step_index"), body.get("note")
        if not is_int(index) or not 0 <= index <= last:
            raise ApiError(400, f"step_index must be an integer from 0 to {last}.")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
            raise ApiError(400, f"note must be a string of at most {MAX_NOTE} characters.")
        changes = move(index) if index != step else invalidate
        message = f"Corrected to step {index + 1}: {steps[index]['title']}."
        if note and note.strip():
            message += f" Note: {note.strip()}"
        return changes, ("correction", message)
    if action == "pause":
        return {**invalidate, "monitoring": 0}, ("monitoring", "Monitoring paused.")
    if action == "resume":
        return {**invalidate, "monitoring": 1}, ("monitoring", "Monitoring resumed.")
    if action == "mode":
        mode = body.get("mode")
        if mode not in MODES:
            raise ApiError(400, "mode must be 'monitoring' or 'assistive'.")
        return {**invalidate, "mode": mode}, ("mode", f"Switched to {mode} mode.")
    if action == "timer_set":
        seconds = body.get("seconds")
        if not is_int(seconds) or not 1 <= seconds <= MAX_TIMER_SECONDS:
            raise ApiError(400, f"seconds must be an integer from 1 to {MAX_TIMER_SECONDS}.")
        return {"timer_deadline": at + seconds, "timer_alerted": 0}, ("timer", f"Timer set for {format_duration(seconds)}.")
    if action == "timer_cancel":
        return {"timer_deadline": None, "timer_alerted": 0}, ("timer", "Timer cancelled.")
    if action == "timer_ack":
        if row["timer_deadline"] is None or at < row["timer_deadline"]:
            raise ApiError(409, "Timer has not finished.")
        if row["timer_alerted"]:
            return {}, None
        return {"timer_alerted": 1}, ("timer", "Timer finished.")
    if action == "finish":
        if step != last:
            raise ApiError(409, "Finish is only available on the final step.")
        return (
            {**invalidate, "status": "completed", "monitoring": 0, "timer_deadline": None, "timer_alerted": 0},
            ("session", "Recipe completed."),
        )
    # end
    return (
        {**invalidate, "status": "ended", "monitoring": 0, "timer_deadline": None, "timer_alerted": 0},
        ("session", "Session ended."),
    )


def format_duration(seconds):
    minutes, secs = divmod(seconds, 60)
    if minutes and secs:
        return f"{minutes} min {secs} s"
    return f"{minutes} min" if minutes else f"{secs} s"


def validate_observation(output):
    """Strictly validate normalized provider output; raise ValueError if unusable."""
    if not isinstance(output, dict):
        raise ValueError("output is not an object")
    activity, evidence = output.get("activity"), output.get("evidence")
    confidence, complete, alert = output.get("confidence"), output.get("step_complete"), output.get("alert")
    if not isinstance(activity, str) or not isinstance(evidence, str):
        raise ValueError("activity/evidence must be strings")
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a finite number in [0, 1]")
    if type(complete) is not bool:
        raise ValueError("step_complete must be boolean")
    if alert is not None and not isinstance(alert, str):
        raise ValueError("alert must be string or null")
    # Optional advisory fields; older outputs without them mean no safety alert, view not obstructed.
    safety_alert, view_obstructed = cooking.normalize_safety(output.get("safety_alert"),
                                                             output.get("view_obstructed"))
    return {
        "activity": activity.strip()[:MAX_TEXT],
        "evidence": evidence.strip()[:MAX_TEXT],
        "confidence": float(confidence),
        "step_complete": complete,
        "alert": (alert.strip()[:MAX_TEXT] or None) if alert else None,
        "safety_alert": safety_alert,
        "view_obstructed": view_obstructed,
    }


def apply_observation(row, obs, at):
    """Return (changes, events) for a validated observation on a fresh session row.

    An obstructed view or an advisory safety concern never counts toward completion, whatever
    the confidence; manual controls are unaffected.
    """
    steps = recipe_for_session(row)["steps"]
    last = len(steps) - 1
    step = row["step_index"]
    qualifies = (obs["step_complete"] and obs["confidence"] >= COMPLETION_CONFIDENCE
                 and not obs.get("view_obstructed") and obs.get("safety_alert") is None)
    streak = row["completion_streak"] + 1 if qualifies else 0
    previous = json.loads(row["latest_observation"]) if row["latest_observation"] else {}
    changes = {"latest_observation": json.dumps({**obs, "created_at": at}), "updated_at": at}
    events = []
    if obs["alert"] and obs["alert"] != previous.get("alert"):
        events.append(("alert", obs["alert"]))
    if streak >= COMPLETION_STREAK and step < last:
        changes.update(step_index=step + 1, step_started_at=at, revision=row["revision"] + 1, completion_streak=0)
        events.append(("progress", f"Step {step + 1} looks complete. Moved to step {step + 2}: {steps[step + 1]['title']}."))
    else:
        if streak >= COMPLETION_STREAK and row["completion_streak"] < COMPLETION_STREAK:
            events.append(("progress", "The final step looks complete. Press Finish when you're done."))
        changes["completion_streak"] = min(streak, COMPLETION_STREAK)
    return changes, events


def record_observation(db, row, revision, at, latency_ms, obs=None, error=None):
    obs = obs or {}
    db.execute(
        "INSERT INTO observations (session_id, revision, step_index, provider, model, latency_ms, activity,"
        " evidence, confidence, step_complete, alert, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["id"], revision, row["step_index"], row["provider"], row["model"], latency_ms,
         obs.get("activity"), obs.get("evidence"), obs.get("confidence"),
         None if "step_complete" not in obs else int(obs["step_complete"]), obs.get("alert"), error, at),
    )


def check_observable(row, revision):
    if row["status"] != "active" or not row["monitoring"]:
        raise ApiError(409, "Monitoring is paused or the session has finished.")
    if revision != row["revision"]:
        raise ApiError(409, "Observation is stale; refresh session state.")


def observation_context(db, row, at):
    """Recipe/state context passed to providers.analyze (no images)."""
    recipe = recipe_for_session(row)
    recent =[dict(r) for r in db.execute(
        "SELECT activity, evidence, confidence, step_complete, created_at FROM observations"
        " WHERE session_id = ? AND step_index = ? AND error IS NULL ORDER BY id DESC LIMIT 3",
        (row["id"], row["step_index"]),
    )]
    correction = db.execute(
        "SELECT message FROM events WHERE session_id = ? AND kind = 'correction' ORDER BY id DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    return {
        "recipe": recipe,
        "step_index": row["step_index"],
        "step": recipe["steps"][row["step_index"]],
        "is_final_step": row["step_index"] == len(recipe["steps"]) - 1,
        "mode": row["mode"],
        "elapsed_seconds": max(0.0, at - row["step_started_at"]),
        "recent_observations": recent,
        "last_correction": correction["message"] if correction else None,
    }


# --- Routes ---------------------------------------------------------------

def register_routes(app):
    @app.get("/")
    def index():
        return render_template("index.html", csrf_token=csrf_token())

    @app.get("/api/config")
    def config():
        db = get_db()
        user_id = current_user_id()
        return jsonify(recipes=list(current_recipes().values()), providers=provider_catalog(db, user_id),
                       defaults=get_defaults(db, user_id), sample_interval=get_sample_interval(db, user_id))

    @app.post("/api/sessions")
    def create_session():
        body = json_body()
        user_id = current_user_id()
        if any(field in body for field in OWNER_FIELDS):
            raise ApiError(400, "Sessions always belong to the signed-in account.")
        recipe_id, provider = body.get("recipe_id"), body.get("provider")
        model, mode = body.get("model"), body.get("mode")
        recipe = current_recipes().get(recipe_id) if isinstance(recipe_id, str) else None
        if recipe is None:  # also another account's, or a deleted library recipe
            raise ApiError(400, "Unknown recipe.")
        catalog = {p["id"]: p for p in provider_catalog(get_db(), user_id)}
        if not isinstance(provider, str) or provider not in catalog:
            raise ApiError(400, "Unknown provider.")
        if catalog[provider].get("locked"):
            raise ApiError(409, LOCKED_BEDROCK)
        if not catalog[provider]["configured"]:
            raise ApiError(400, "That provider is not configured for your account.")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ApiError(400, "A model ID is required.")
        if mode not in MODES:
            raise ApiError(400, "mode must be 'monitoring' or 'assistive'.")
        at = now()
        db = get_db()
        try:
            with transaction(db):  # the per-account unique index also guards concurrent creation
                if db.execute("SELECT 1 FROM sessions WHERE status = 'active' AND user_id = ?",
                              (user_id,)).fetchone():
                    raise ApiError(409, "Another session is already active.")
                session_id = db.execute(
                    "INSERT INTO sessions (recipe_id, provider, model, mode, status, created_at, updated_at,"
                    " step_started_at, recipe_snapshot, user_id) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                    (recipe_id, provider, model.strip(), mode, at, at, at, json.dumps(recipe), user_id),
                ).lastrowid
                log_event(db, session_id, "session", f"Started {recipe['name']} in {mode} mode.", at)
        except sqlite3.IntegrityError:
            raise ApiError(409, "Another session is already active.") from None
        return respond_session(session_id, user_id, 201)

    @app.get("/api/sessions/active")
    def active_session():
        db = get_db()
        row = db.execute("SELECT * FROM sessions WHERE status = 'active' AND user_id = ?",
                         (current_user_id(),)).fetchone()
        return jsonify(session=session_json(db, row) if row else None)

    @app.get("/api/sessions/<int:session_id>")
    def get_session(session_id):
        return respond_session(session_id, current_user_id())

    @app.post("/api/sessions/<int:session_id>/actions")
    def session_action(session_id):
        body = json_body()
        user_id = current_user_id()
        at = now()
        db = get_db()
        with transaction(db):
            changes, event = apply_action(load_session(db, session_id, user_id), body, at)
            if changes:
                update_session(db, session_id, {**changes, "updated_at": at})
            if event:
                log_event(db, session_id, *event, at)
            if changes.get("status") in ("ended", "completed"):
                cooking.cancel_session_timers(db, session_id, at)
        return respond_session(session_id, user_id)

    @app.post("/api/sessions/<int:session_id>/observations")
    def post_observation(session_id):
        # Identity and vault state are leased now and rechecked under the commit guard after
        # inference: logout, revocation or a vault change meanwhile discards the result unwritten.
        lease = lease_credentials()
        user_id = lease.user_id
        body = json_body()
        revision = body.get("revision")
        if not is_int(revision):
            raise ApiError(400, "revision must be an integer.")
        jpeg = decode_jpeg(body.get("image"))
        db = get_db()
        row = load_session(db, session_id, user_id)
        check_observable(row, revision)
        try:
            import providers  # P1 hook: providers.analyze(provider, model, jpeg_bytes, context) -> dict
        except ImportError:
            raise ApiError(503, "Visual monitoring is not available yet. Manual controls still work.") from None
        if row["provider"] not in SHARED_PROVIDERS and row["provider"] != "bedrock":
            raise ApiError(409, "That provider is not available for your account. Manual controls still work.")
        # Bedrock always gets the account's own connection explicitly; there is no environment
        # fallback. Other providers keep the 4-argument call and do not depend on the vault.
        if row["provider"] == "bedrock":
            if lease.connection is None:
                raise ApiError(409, LOCKED_BEDROCK if lease.vault.state()[0] else NO_BEDROCK)
        else:
            lease = lease._replace(connection=None)
        extra = {"bedrock": lease.connection} if lease.connection else {}

        busy = current_app.extensions["sous_observations"]
        with busy["lock"]:
            if session_id in busy["busy"]:
                raise ApiError(409, "Another analysis is already running.")
            busy["busy"].add(session_id)
        try:
            context = observation_context(db, row, now())
            started = time.monotonic()
            obs = error = None
            try:
                obs = validate_observation(
                    providers.analyze(row["provider"], row["model"], jpeg, context, **extra))
            except ValueError as exc:
                error = f"invalid_output: {exc}"[:300]
            except Exception as exc:  # provider failure; never echo upstream text to the client
                error = str(getattr(exc, "public_message", None) or type(exc).__name__)[:300]
                current_app.logger.warning("Provider %s failed: %s", row["provider"], error)
            latency_ms = int((time.monotonic() - started) * 1000)
            at = now()

            stale = False
            with leased_transaction(db, lease) as problem:
                # A revoked login or invalidated credentials write nothing, not even the audit row.
                if problem is None:
                    # Fresh state decides staleness and receives a valid result; the audit row keeps
                    # the metadata (step, provider, model) the request actually started with.
                    fresh = load_session(db, session_id, user_id)
                    stale = (fresh["status"] != "active" or not fresh["monitoring"]
                             or fresh["revision"] != revision)
                    if stale:
                        record_observation(db, row, revision, at, latency_ms, obs,
                                           f"stale: {error}" if error else "stale")
                    else:
                        record_observation(db, row, revision, at, latency_ms, obs, error)
                        if not error:
                            changes, events = apply_observation(fresh, obs, at)
                            update_session(db, session_id, changes)
                            for kind, message in events:
                                log_event(db, session_id, kind, message, at)
                            if obs["safety_alert"] is not None:  # advisory; stored only for a fresh result
                                if cooking.record_alert(db, session_id, obs["safety_alert"], at):
                                    # One event per newly stored alert (not for dedup repeats) lets an
                                    # open voice stream mention it once.
                                    log_event(db, session_id, "safety", cooking.alert_event_message(
                                        obs["safety_alert"]), at)
        finally:
            with busy["lock"]:
                busy["busy"].discard(session_id)

        if problem is not None:
            raise problem
        if stale:
            raise ApiError(409, "Observation is stale; refresh session state.")
        if error:
            if error.startswith("invalid_output"):
                raise ApiError(502, "The model returned an unusable observation. Try again.")
            raise ApiError(502, "Analysis failed. Manual controls still work; try again.")
        return respond_session(session_id, user_id)


def register_settings_routes(app):
    """Settings API (docs/settings.md). Request bodies are never logged or echoed."""

    @app.get("/api/settings")
    def get_settings():
        return respond_settings()

    @app.post("/api/settings/vault")
    def create_vault():
        current_vault().create(settings_body().get("passphrase"))
        return respond_settings(201)

    @app.post("/api/settings/unlock")
    def unlock_vault():
        current_vault().unlock(settings_body().get("passphrase"))
        return respond_settings()

    @app.post("/api/settings/lock")
    def lock_vault():
        current_vault().lock()
        return respond_settings()

    @app.put("/api/settings/bedrock")
    def save_bedrock():
        body = settings_body()
        vault = current_vault()
        vault.save_bedrock(merge_bedrock(vault.bedrock(), body))
        return respond_settings()

    @app.delete("/api/settings/bedrock")
    def delete_bedrock():
        vault = current_vault()
        vault.bedrock()  # 409 when locked
        vault.save_bedrock(None)
        return respond_settings()

    @app.put("/api/settings/defaults")
    def save_defaults():
        body = settings_body()
        current_vault().bedrock()  # unlocked vault required
        provider = body.get("provider")
        if not isinstance(provider, str) or provider not in {p[0] for p in PROVIDERS}:
            raise ApiError(400, "Unknown provider.")
        model = validate_model_id(body.get("model"), required=True)
        db = get_db()
        with transaction(db):
            set_pref(db, current_user_id(), "defaults", {"provider": provider, "model": model})
        return respond_settings()

    @app.put("/api/settings/preferences")
    def save_preferences():
        body = settings_body()
        known = {"sample_interval", "voice_id"}
        if not body or not set(body) <= known:
            raise ApiError(400, "Provide sample_interval and/or voice_id only.")
        if "sample_interval" in body and not valid_interval(body["sample_interval"]):
            raise ApiError(400, f"sample_interval must be an integer from {MIN_SAMPLE_INTERVAL}"
                                f" to {MAX_SAMPLE_INTERVAL}.")
        if "voice_id" in body and body["voice_id"] not in voice.VOICES:
            raise ApiError(400, f"voice_id must be one of: {', '.join(voice.VOICES)}.")
        db = get_db()
        with transaction(db):
            for key in sorted(body):
                set_pref(db, current_user_id(), key, body[key])
        return respond_settings()

    @app.get(DISCOVERY_PATH)
    def discover_bedrock_models():
        vault = current_vault()
        vault.bedrock()  # 409 when locked
        connection = vault.connection()
        if connection is None:
            raise ApiError(409, "Save a Bedrock connection before listing models.")
        import providers
        guard = current_app.extensions["sous_discovery"]
        if not guard.acquire(blocking=False):
            raise ApiError(409, "Model discovery is already running.")
        try:
            result = providers.list_bedrock_models(connection)
        except providers.ProviderError as exc:
            current_app.logger.warning("Bedrock model discovery failed: %s", exc.public_message)
            raise ApiError(502, f"{exc.public_message} You can still enter a model or inference"
                                " profile ID manually.") from None
        finally:
            guard.release()
        return jsonify(result)


TOOL_BODY_BYTES = 64 * 1024


def register_cooking_routes(app):
    """Named timers, preparation checklist, alert acknowledgment and private history
    (docs/v2-api.md). Every route resolves the parent session through the caller's ownership, so
    another account's ids look missing. None of these change steps or monitoring."""

    def snapshot(row):
        return json.loads(row["recipe_snapshot"]) if row["recipe_snapshot"] else None

    @app.get("/api/sessions/<int:session_id>/timers")
    def list_timers(session_id):
        db = get_db()
        load_session(db, session_id, current_user_id())
        return jsonify(timers=cooking.timers_json(db, session_id, now()))

    @app.post("/api/sessions/<int:session_id>/timers")
    def add_timer(session_id):
        user_id, db = current_user_id(), get_db()
        load_session(db, session_id, user_id)  # 404 before reading the body
        name, seconds = cooking.validate_timer(limited_body(TOOL_BODY_BYTES))
        at = now()
        with transaction(db):
            cooking.create_timer(db, load_session(db, session_id, user_id), name, seconds, at)
        return jsonify(timers=cooking.timers_json(db, session_id, at)), 201

    @app.post("/api/sessions/<int:session_id>/timers/<int:timer_id>/<any(ack, cancel):action>")
    def finish_timer(session_id, timer_id, action):
        user_id, db, at = current_user_id(), get_db(), now()
        with transaction(db):  # idempotent; only this session's timer, never a newer session's
            load_session(db, session_id, user_id)
            cooking.finish_timer(db, session_id, timer_id, action, at)
        return jsonify(timers=cooking.timers_json(db, session_id, at))

    @app.get("/api/sessions/<int:session_id>/checklist")
    def get_checklist(session_id):
        row = load_session(get_db(), session_id, current_user_id())
        return jsonify(checklist=cooking.checklist_json(row, snapshot(row)))

    @app.put("/api/sessions/<int:session_id>/checklist")
    def put_checklist(session_id):
        user_id, db = current_user_id(), get_db()
        load_session(db, session_id, user_id)
        body = limited_body(TOOL_BODY_BYTES)
        with transaction(db):  # all items apply or none do
            row = load_session(db, session_id, user_id)
            if row["status"] != "active":
                raise CookingError(409, "The checklist can only change during an active session.")
            state = cooking.updated_checklist(row, snapshot(row), body)
            update_session(db, session_id, {"checklist": json.dumps(state)})
        row = load_session(db, session_id, user_id)
        return jsonify(checklist=cooking.checklist_json(row, snapshot(row)))

    @app.post("/api/sessions/<int:session_id>/alerts/<int:alert_id>/ack")
    def ack_alert(session_id, alert_id):
        user_id, db, at = current_user_id(), get_db(), now()
        with transaction(db):
            load_session(db, session_id, user_id)
            cooking.ack_alert(db, session_id, alert_id, at)
        return respond_session(session_id, user_id)

    @app.get("/api/history")
    def history():
        limit = cooking.history_limit(request.args.get("limit"))
        return jsonify(sessions=cooking.history_summaries(get_db(), current_user_id(), limit))

    @app.get("/api/history/<int:session_id>")
    def history_detail(session_id):
        db = get_db()
        return jsonify(session=session_json(db, load_session(db, session_id, current_user_id())))

    @app.put("/api/history/<int:session_id>/note")
    def history_note(session_id):
        user_id, db = current_user_id(), get_db()
        load_session(db, session_id, user_id)
        note = cooking.validate_note(limited_body(TOOL_BODY_BYTES))
        with transaction(db):
            load_session(db, session_id, user_id)
            update_session(db, session_id, {"note": note})
        return jsonify(session=session_json(db, load_session(db, session_id, user_id)))


NO_AI_CONNECTION = "Save your own AWS connection in Settings to use the recipe maker."
LOCKED_AI = "Unlock your AWS connection in Settings to use the recipe maker."


def register_library_routes(app):
    """Recipe library, profile, serving drafts and AI drafts (docs/v2-api.md). The caller's
    identity always comes from the login; bodies are size-limited and never logged."""

    def user():
        return dict(g.user)  # captured once per request

    @app.get("/api/recipes")
    def list_library():
        return jsonify(recipes=library.list_records(get_db(), user(), bundled_recipes()))

    @app.post("/api/recipes")
    def create_recipe():
        caller = user()
        recipe, scope = library.create_body(limited_body(library.MAX_BODY), caller)
        db = get_db()
        with transaction(db):
            record = library.insert(db, caller, bundled_recipes(), recipe, scope, now())
        return jsonify(recipe=record), 201

    @app.get("/api/recipes/<rid>")
    def get_recipe(rid):
        return jsonify(recipe=library.find_record(get_db(), user(), bundled_recipes(), rid))

    @app.put("/api/recipes/<rid>")
    def update_recipe(rid):
        caller, db = user(), get_db()
        library.editable_row(db, caller, bundled_recipes(), rid)  # 404/403 before reading the body
        recipe = library.update_body(limited_body(library.MAX_BODY))
        with transaction(db):
            record = library.update(db, caller, bundled_recipes(), rid, recipe, now())
        return jsonify(recipe=record)

    @app.delete("/api/recipes/<rid>")
    def delete_recipe(rid):
        caller, db = user(), get_db()
        with transaction(db):  # archived: kept for history; cooking snapshots are separate copies
            library.archive(db, caller, bundled_recipes(), rid, now())
        return jsonify(deleted=True)

    @app.post("/api/recipes/<rid>/copy")
    def copy_recipe(rid):
        caller, db = user(), get_db()
        source = library.find_record(db, caller, bundled_recipes(), rid)
        if limited_body(library.MAX_BODY):
            raise LibraryError(400, "Send an empty object to copy a recipe.")
        recipe = library.canonical(library.strip_meta(source))
        with transaction(db):
            record = library.insert(db, caller, bundled_recipes(), recipe, "personal", now())
        return jsonify(recipe=record), 201

    @app.post("/api/recipes/<rid>/scale")
    def scale_recipe(rid):
        source = library.find_record(get_db(), user(), bundled_recipes(), rid)
        return jsonify(library.scale(source, library.servings_body(limited_body(library.MAX_BODY))))

    @app.get("/api/profile")
    def get_profile():
        return jsonify(library.stored_profile(get_pref(get_db(), current_user_id(), "profile")))

    @app.put("/api/profile")
    def save_profile():
        profile = library.validate_profile(limited_body(library.MAX_BODY))
        db = get_db()
        with transaction(db):
            set_pref(db, current_user_id(), "profile", profile)
        return jsonify(profile)

    @app.post("/api/recipes/generate")
    def generate_recipe():
        # Lease identity and vault state first; after the network call, nothing from flask.g is
        # used (nested work may replace it) and the lease is rechecked before any draft returns.
        lease = lease_credentials()
        user_id = lease.user_id
        request_data = recipe_ai.validate_request(limited_body(library.MAX_BODY))
        if lease.connection is None:
            locked = lease.vault.state()[0] and lease.vault.initialized
            raise LibraryError(409, LOCKED_AI if locked else NO_AI_CONNECTION)
        db = get_db()
        _, connection_settings = lease.vault.state()  # read after the lease: a change fails the recheck
        model = recipe_ai.choose_model(get_defaults(db, user_id), connection_settings)
        if model is None:
            raise LibraryError(409, recipe_ai.NO_MODEL)
        import providers
        limiter = current_app.extensions["sous_generation"]
        limiter.acquire(user_id)  # rejected attempts are not counted
        text = failure = None
        try:
            try:
                text = providers.bedrock_text(model, recipe_ai.SYSTEM_PROMPT, recipe_ai.build_prompt(request_data),
                                              lease.connection, recipe_ai.MAX_OUTPUT_TOKENS,
                                              recipe_ai.READ_TIMEOUT, recipe_ai.MAX_OUTPUT_CHARS)
            except providers.ProviderError as exc:
                failure = exc.public_message  # static text only
            with leased_transaction(db, lease) as problem:
                pass  # nothing is written; the check runs under the vault guard
        finally:
            limiter.release(user_id)
        if problem is not None:
            raise problem
        if failure is not None:
            current_app.logger.warning("Recipe generation failed: %s", failure)
            raise LibraryError(502, recipe_ai.FAILED)
        return jsonify(recipe_ai.parse_output(text))


def register_voice_routes(app, sock):
    """Conversational voice WebSocket (docs/voice.md). Origin, login and session ownership are
    checked in before_request handlers, before the upgrade."""

    @sock.route("/api/sessions/<int:session_id>/voice")
    def voice_socket(ws, session_id):
        serve_voice(ws, session_id)


def serve_voice(ws, session_id):
    """Run one voice socket for the request's signed-in account.

    Resolves the login from the request cookie itself, so callers outside the route still get
    real server-side authentication. voice.serve rechecks ownership and polls the login while
    streaming, so logout or revocation stops the stream.
    """
    db = get_db()
    user = accounts.load_auth(db)
    if user is None:
        ws.send(json.dumps({"type": "error", "message": voice.AUTH_FAILED}))
        return
    user_id, token_hash, clock = user["id"], g.auth_token_hash, current_app.config["CLOCK"]
    voice.serve(ws, session_id, session.get("csrf_token"), db, get_voice_id(db, user_id),
                user_vault(current_app, user_id), user_id,
                lambda: accounts.auth_valid(db, user_id, token_hash, clock()))
