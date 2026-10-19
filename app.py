"""Sous: Flask + SQLite backend for a webcam-assisted cooking session."""

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

from flask import Flask, current_app, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from recipes import load_recipes

MODES = ("monitoring", "assistive")
ACTIONS = (
    "next", "back", "correct", "pause", "resume", "mode",
    "timer_set", "timer_cancel", "timer_ack", "finish", "end",
)
RECIPE_ACTIONS = ("next", "back", "correct", "finish", "resume")  # need the session's recipe
# (id, display name, env var required for "configured" or None, default model env var)
PROVIDERS = (
    ("ollama", "Ollama (local)", None, "OLLAMA_MODEL"),
    ("openai", "OpenAI", "OPENAI_API_KEY", "OPENAI_MODEL"),
    ("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
    ("bedrock", "AWS Bedrock", "AWS_REGION", "BEDROCK_MODEL_ID"),
)
SAMPLE_INTERVAL = 10
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
    recipe_snapshot TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON sessions(status) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, id);
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
        # Local-only app: reject other Host headers (DNS rebinding). Ports are ignored.
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        CLOCK=time.time,
        RECIPE_DIR=None,  # None: bundled recipes/ directory
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = load_secret_key(app.config["SECRET_KEY_FILE"])
    app.extensions["sous_observations"] = {"lock": threading.Lock(), "busy": set()}
    app.extensions["sous_recipes"] = load_recipes(app.config["RECIPE_DIR"])

    init_db(app.config["DATABASE"], app.config["CLOCK"](), app.extensions["sous_recipes"])
    app.teardown_appcontext(close_db)
    app.register_error_handler(ApiError, lambda e: (jsonify(error=e.message), e.status))
    app.register_error_handler(HTTPException, http_error)
    app.register_error_handler(Exception, unexpected_error)
    app.before_request(check_origin_and_csrf)
    register_routes(app)
    return app


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
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = connect(path)
    try:
        db.executescript(SCHEMA)
        if "recipe_snapshot" not in {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}:
            db.execute("ALTER TABLE sessions ADD COLUMN recipe_snapshot TEXT")
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
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def now():
    return current_app.config["CLOCK"]()


def load_session(db, session_id):
    row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
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
    data = dict(row)
    data["monitoring"] = bool(data["monitoring"])
    data["timer_alerted"] = bool(data["timer_alerted"])
    data["latest_observation"] = json.loads(data["latest_observation"]) if data["latest_observation"] else None
    snapshot = data.pop("recipe_snapshot", None)
    data["recipe"] = json.loads(snapshot) if snapshot else None
    data["events"] = [dict(e) for e in db.execute(
        "SELECT id, kind, message, created_at FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (row["id"], EVENT_LIMIT),
    )]
    return data


def recipe_for_session(row):
    """The recipe a session started with. Never substitutes a different current definition."""
    if not row["recipe_snapshot"]:
        raise ApiError(409, "This session's recipe is no longer available. End the session to start a new one.")
    return json.loads(row["recipe_snapshot"])


def current_recipes():
    return current_app.extensions["sous_recipes"]


def respond_session(session_id, status=200):
    db = get_db()
    return jsonify(session_json(db, load_session(db, session_id))), status


# --- Request validation ---------------------------------------------------

def http_error(exc):
    if exc.code is None or exc.code < 400:  # e.g. routing redirects
        return exc
    messages = {404: "Not found.", 405: "Method not allowed.", 413: "Request is too large."}
    return jsonify(error=messages.get(exc.code, exc.name)), exc.code


def unexpected_error(exc):
    current_app.logger.exception("Unhandled error")
    return jsonify(error="Internal server error."), 500


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
    return session["csrf_token"]


def check_origin_and_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
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


def provider_catalog():
    return [
        {
            "id": pid,
            "name": name,
            "configured": required is None or bool(os.environ.get(required)),
            "default_model": os.environ.get(model_var, ""),
        }
        for pid, name, required, model_var in PROVIDERS
    ]


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
    return {
        "activity": activity.strip()[:MAX_TEXT],
        "evidence": evidence.strip()[:MAX_TEXT],
        "confidence": float(confidence),
        "step_complete": complete,
        "alert": (alert.strip()[:MAX_TEXT] or None) if alert else None,
    }


def apply_observation(row, obs, at):
    """Return (changes, events) for a validated observation on a fresh session row."""
    steps = recipe_for_session(row)["steps"]
    last = len(steps) - 1
    step = row["step_index"]
    qualifies = obs["step_complete"] and obs["confidence"] >= COMPLETION_CONFIDENCE
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
        return jsonify(recipes=list(current_recipes().values()), providers=provider_catalog(), sample_interval=SAMPLE_INTERVAL)

    @app.post("/api/sessions")
    def create_session():
        body = json_body()
        recipe_id, provider = body.get("recipe_id"), body.get("provider")
        model, mode = body.get("model"), body.get("mode")
        if not isinstance(recipe_id, str) or recipe_id not in current_recipes():
            raise ApiError(400, "Unknown recipe.")
        recipe = current_recipes()[recipe_id]
        catalog = {p["id"]: p for p in provider_catalog()}
        if not isinstance(provider, str) or provider not in catalog:
            raise ApiError(400, "Unknown provider.")
        if not catalog[provider]["configured"]:
            raise ApiError(400, "That provider is not configured on the server.")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ApiError(400, "A model ID is required.")
        if mode not in MODES:
            raise ApiError(400, "mode must be 'monitoring' or 'assistive'.")
        at = now()
        db = get_db()
        try:
            with transaction(db):
                if db.execute("SELECT 1 FROM sessions WHERE status = 'active'").fetchone():
                    raise ApiError(409, "Another session is already active.")
                session_id = db.execute(
                    "INSERT INTO sessions (recipe_id, provider, model, mode, status, created_at, updated_at,"
                    " step_started_at, recipe_snapshot) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                    (recipe_id, provider, model.strip(), mode, at, at, at, json.dumps(recipe)),
                ).lastrowid
                log_event(db, session_id, "session", f"Started {recipe['name']} in {mode} mode.", at)
        except sqlite3.IntegrityError:
            raise ApiError(409, "Another session is already active.") from None
        return respond_session(session_id, 201)

    @app.get("/api/sessions/active")
    def active_session():
        db = get_db()
        row = db.execute("SELECT * FROM sessions WHERE status = 'active'").fetchone()
        return jsonify(session=session_json(db, row) if row else None)

    @app.get("/api/sessions/<int:session_id>")
    def get_session(session_id):
        return respond_session(session_id)

    @app.post("/api/sessions/<int:session_id>/actions")
    def session_action(session_id):
        body = json_body()
        at = now()
        db = get_db()
        with transaction(db):
            changes, event = apply_action(load_session(db, session_id), body, at)
            if changes:
                update_session(db, session_id, {**changes, "updated_at": at})
            if event:
                log_event(db, session_id, *event, at)
        return respond_session(session_id)

    @app.post("/api/sessions/<int:session_id>/observations")
    def post_observation(session_id):
        body = json_body()
        revision = body.get("revision")
        if not is_int(revision):
            raise ApiError(400, "revision must be an integer.")
        jpeg = decode_jpeg(body.get("image"))
        db = get_db()
        row = load_session(db, session_id)
        check_observable(row, revision)
        try:
            import providers  # P1 hook: providers.analyze(provider, model, jpeg_bytes, context) -> dict
        except ImportError:
            raise ApiError(503, "Visual monitoring is not available yet. Manual controls still work.") from None

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
                    providers.analyze(row["provider"], row["model"], jpeg, context))
            except ValueError as exc:
                error = f"invalid_output: {exc}"[:300]
            except Exception as exc:  # provider failure; never echo upstream text to the client
                error = str(getattr(exc, "public_message", None) or type(exc).__name__)[:300]
                current_app.logger.warning("Provider %s failed: %s", row["provider"], error)
            latency_ms = int((time.monotonic() - started) * 1000)
            at = now()

            with transaction(db):
                # Fresh state decides staleness and receives a valid result; the audit row keeps
                # the metadata (step, provider, model) the request actually started with.
                fresh = load_session(db, session_id)
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
        finally:
            with busy["lock"]:
                busy["busy"].discard(session_id)

        if stale:
            raise ApiError(409, "Observation is stale; refresh session state.")
        if error:
            if error.startswith("invalid_output"):
                raise ApiError(502, "The model returned an unusable observation. Try again.")
            raise ApiError(502, "Analysis failed. Manual controls still work; try again.")
        return respond_session(session_id)
