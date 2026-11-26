"""Cooking tools: named timers, preparation checklist, private history notes, advisory safety
alerts and monitoring freshness (docs/v2-api.md).

Helpers here take an open database connection and an already owner-checked session row or id;
callers in app.py do the authorization. Nothing here mutates steps: timers, checklist entries
and alerts never advance a recipe. Alerts are advisory model output, never a safety verdict,
and the absence of alerts proves nothing.
"""

import json

MAX_LIVE_TIMERS = 8  # running or elapsed, not yet acknowledged/cancelled, per session
MAX_TIMERS = 64  # all timers ever created per session
MAX_TIMER_NAME = 80
MAX_TIMER_SECONDS = 86400
MAX_CHECKLIST = 96
MAX_NOTE = 4000
HISTORY_DEFAULT, HISTORY_MAX = 20, 100
MAX_ALERT_TITLE, MAX_ALERT_EVIDENCE = 160, 1000
ALERT_CATEGORIES = ("hazard", "possible_emergency")
MAX_ACTIVE_ALERTS = 20
ALERT_DEDUP_SECONDS = 60
ALERT_LIST = 20
STALE_MIN_SECONDS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    name TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    created_at REAL NOT NULL,
    deadline_at REAL NOT NULL,  -- absolute; survives restarts and step changes
    acknowledged_at REAL,
    cancelled_at REAL
);
CREATE INDEX IF NOT EXISTS session_timers_by_session ON session_timers(session_id, id);
CREATE TABLE IF NOT EXISTS session_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    category TEXT NOT NULL CHECK (category IN ('hazard', 'possible_emergency')),
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at REAL NOT NULL,
    acknowledged_at REAL
);
CREATE INDEX IF NOT EXISTS session_alerts_by_session ON session_alerts(session_id, id);
"""
SESSION_COLUMNS = (("note", "TEXT"), ("checklist", "TEXT"))  # added to sessions by app.init_db

HEALTH_MESSAGES = {
    "paused": "Camera checks are paused.",
    "error": "The last camera check failed. Manual controls still work.",
    "stale": "No recent camera check. Snapshots are intermittent and cannot confirm safety.",
    "obscured": "The camera view looked blocked or unclear in the last check.",
    "recent": "Last camera check was recent. Snapshots are intermittent and cannot confirm safety.",
}


class CookingError(Exception):
    """Client-facing failure with a static message."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# --- Observation safety fields (shared by providers.parse_observation and app) ---

def normalize_safety(safety_alert, view_obstructed):
    """Return (alert dict or None, bool). Missing values default to (None, False); anything else
    invalid raises ValueError. Older model responses without these fields stay valid."""
    if view_obstructed is None:
        view_obstructed = False
    if type(view_obstructed) is not bool:
        raise ValueError("view_obstructed must be boolean")
    if safety_alert is None:
        return None, view_obstructed
    if not isinstance(safety_alert, dict):
        raise ValueError("safety_alert must be an object or null")
    category, title, evidence = (safety_alert.get(k) for k in ("category", "title", "evidence"))
    if category not in ALERT_CATEGORIES:
        raise ValueError("safety_alert.category is not allowed")
    for value, limit in ((title, MAX_ALERT_TITLE), (evidence, MAX_ALERT_EVIDENCE)):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError("safety_alert text is missing or too long")
    return {"category": category, "title": title.strip(), "evidence": evidence.strip()}, view_obstructed


# --- Named timers ------------------------------------------------------------

def timer_status(row, at):
    if row["cancelled_at"] is not None:
        return "cancelled"
    if row["acknowledged_at"] is not None:
        return "acknowledged"
    return "elapsed" if at >= row["deadline_at"] else "running"


def timers_json(db, session_id, at):
    rows = db.execute("SELECT * FROM session_timers WHERE session_id = ? ORDER BY id LIMIT ?",
                      (session_id, MAX_TIMERS)).fetchall()
    return [{"id": r["id"], "name": r["name"], "duration_seconds": r["duration_seconds"],
             "deadline_at": r["deadline_at"], "status": timer_status(r, at)} for r in rows]


def validate_timer(body):
    name, seconds = body.get("name"), body.get("duration_seconds")
    if set(body) != {"name", "duration_seconds"}:
        raise CookingError(400, "Send exactly name and duration_seconds.")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_TIMER_NAME:
        raise CookingError(400, f"Timer name must be 1 to {MAX_TIMER_NAME} characters.")
    if type(seconds) is not int or not 1 <= seconds <= MAX_TIMER_SECONDS:
        raise CookingError(400, f"duration_seconds must be an integer from 1 to {MAX_TIMER_SECONDS}.")
    return name.strip(), seconds


def create_timer(db, row, name, seconds, at):
    """Inside a write transaction, for an owned session row."""
    if row["status"] != "active":
        raise CookingError(409, "Timers can only be added to an active session.")
    live, total = db.execute(
        "SELECT SUM(acknowledged_at IS NULL AND cancelled_at IS NULL), COUNT(*) FROM session_timers"
        " WHERE session_id = ?", (row["id"],)).fetchone()
    if (live or 0) >= MAX_LIVE_TIMERS:
        raise CookingError(409, f"At most {MAX_LIVE_TIMERS} timers can run at once. Dismiss one first.")
    if total >= MAX_TIMERS:
        raise CookingError(409, "This session has reached its timer limit.")
    db.execute("INSERT INTO session_timers (session_id, name, duration_seconds, created_at, deadline_at)"
               " VALUES (?, ?, ?, ?, ?)", (row["id"], name, seconds, at, at + seconds))


def finish_timer(db, session_id, timer_id, action, at):
    """Idempotent ack/cancel of one timer of this session. A finished timer is left alone."""
    timer = db.execute("SELECT * FROM session_timers WHERE id = ? AND session_id = ?",
                       (timer_id, session_id)).fetchone()
    if timer is None:
        raise CookingError(404, "Timer not found.")
    if timer["acknowledged_at"] is None and timer["cancelled_at"] is None:
        column = "acknowledged_at" if action == "ack" else "cancelled_at"
        db.execute(f"UPDATE session_timers SET {column} = ? WHERE id = ?", (at, timer_id))


def cancel_session_timers(db, session_id, at):
    db.execute("UPDATE session_timers SET cancelled_at = ? WHERE session_id = ?"
               " AND acknowledged_at IS NULL AND cancelled_at IS NULL", (at, session_id))


# --- Preparation checklist -----------------------------------------------------

def checklist_entries(recipe):
    """(id, label, kind) derived from the session's recipe snapshot; stable for that snapshot."""
    if not recipe:
        return []
    entries = [(f"ingredient-{i}", text, "ingredient") for i, text in enumerate(recipe.get("ingredients") or [])]
    entries += [(f"equipment-{i}", text, "equipment") for i, text in enumerate(recipe.get("equipment") or [])]
    return entries[:MAX_CHECKLIST]


def _checked(row):
    try:
        state = json.loads(row["checklist"]) if row["checklist"] else {}
    except ValueError:
        state = {}
    return state if isinstance(state, dict) else {}


def checklist_json(row, recipe):
    state = _checked(row)
    return [{"id": item_id, "label": label, "kind": kind, "checked": state.get(item_id) is True}
            for item_id, label, kind in checklist_entries(recipe)]


def updated_checklist(row, recipe, body):
    """New stored state for PUT {items: [{id, checked}]}; rejects the whole request on any error."""
    items = body.get("items")
    if set(body) != {"items"} or not isinstance(items, list) or len(items) > MAX_CHECKLIST:
        raise CookingError(400, f"Send items as a list of at most {MAX_CHECKLIST} {{id, checked}} entries.")
    known = {item_id for item_id, _, _ in checklist_entries(recipe)}
    state, seen = dict(_checked(row)), set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {"id", "checked"} or not isinstance(item["id"], str)
                or item["id"] not in known
                or item["id"] in seen or type(item["checked"]) is not bool):
            raise CookingError(400, "Each item needs a known id, once, and a true/false checked value.")
        seen.add(item["id"])
        state[item["id"]] = item["checked"]
    return {k: v for k, v in state.items() if k in known and v}


# --- Advisory safety alerts ----------------------------------------------------

def alerts_json(db, session_id):
    """Unacknowledged alerts first, then the newest acknowledged ones."""
    rows = db.execute("SELECT id, category, title, evidence, created_at, acknowledged_at FROM session_alerts"
                      " WHERE session_id = ? ORDER BY acknowledged_at IS NOT NULL, id DESC LIMIT ?",
                      (session_id, ALERT_LIST)).fetchall()
    return [dict(r) for r in rows]


def record_alert(db, session_id, alert, at):
    """Store a validated advisory alert inside the observation's leased write transaction.

    The same category/title/evidence within ALERT_DEDUP_SECONDS is not repeated; a different
    category (for example an escalation) is a fresh record. With MAX_ACTIVE_ALERTS unacknowledged
    a new hazard is dropped (the visible ones still need the cook's acknowledgment), but a
    possible_emergency is never lost: it supersedes the oldest unacknowledged hazard (or, with
    only emergencies, the oldest emergency) by deleting that row. That is a bounded replacement,
    not an acknowledgment, and never touches another session. Returns True only when a new
    record was stored, so callers announce each new alert once.
    """
    duplicate = db.execute(
        "SELECT 1 FROM session_alerts WHERE session_id = ? AND category = ? AND title = ? AND evidence = ?"
        " AND created_at >= ?", (session_id, alert["category"], alert["title"], alert["evidence"],
                                 at - ALERT_DEDUP_SECONDS)).fetchone()
    if duplicate:
        return False
    active = db.execute("SELECT COUNT(*) FROM session_alerts WHERE session_id = ? AND acknowledged_at IS NULL",
                        (session_id,)).fetchone()[0]
    if active >= MAX_ACTIVE_ALERTS:
        if alert["category"] != "possible_emergency":
            return False
        oldest = db.execute(
            "SELECT id FROM session_alerts WHERE session_id = ? AND acknowledged_at IS NULL"
            " ORDER BY category = 'possible_emergency', id LIMIT 1", (session_id,)).fetchone()
        db.execute("DELETE FROM session_alerts WHERE id = ? AND session_id = ?", (oldest["id"], session_id))
    db.execute("INSERT INTO session_alerts (session_id, category, title, evidence, created_at)"
               " VALUES (?, ?, ?, ?, ?)", (session_id, alert["category"], alert["title"], alert["evidence"], at))
    return True


def alert_event_message(alert):
    """Short bounded text for the session event that lets an active voice stream mention a new
    advisory alert once. Evidence stays in the alert record."""
    label = "Possible emergency" if alert["category"] == "possible_emergency" else "Hazard"
    return f"{label}: {alert['title']}"[:MAX_ALERT_TITLE + 24]


def ack_alert(db, session_id, alert_id, at):
    found = db.execute("SELECT acknowledged_at FROM session_alerts WHERE id = ? AND session_id = ?",
                       (alert_id, session_id)).fetchone()
    if found is None:
        raise CookingError(404, "Alert not found.")
    if found["acknowledged_at"] is None:
        db.execute("UPDATE session_alerts SET acknowledged_at = ? WHERE id = ?", (at, alert_id))


# --- Monitoring freshness ------------------------------------------------------

def monitoring_health(db, row, latest, at, sample_interval):
    """{last_success_at, last_attempt_at, state, message} from recorded observations.

    States: paused, error, stale, obscured, recent. None of them means the kitchen is safe.
    Stale results recorded after a correction are not attempts at the current state.
    """
    success = db.execute("SELECT MAX(created_at) FROM observations WHERE session_id = ? AND error IS NULL",
                         (row["id"],)).fetchone()[0]
    attempt = db.execute("SELECT created_at, error FROM observations WHERE session_id = ?"
                         " AND (error IS NULL OR error NOT LIKE 'stale%') ORDER BY id DESC LIMIT 1",
                         (row["id"],)).fetchone()
    threshold = max(STALE_MIN_SECONDS, 3 * sample_interval)
    if row["status"] != "active" or not row["monitoring"]:
        state = "paused"
    elif attempt is not None and attempt["error"] is not None:
        state = "error"
    elif success is None or at - success > threshold:
        state = "stale"
    elif isinstance(latest, dict) and latest.get("view_obstructed") is True:
        state = "obscured"
    else:
        state = "recent"
    return {"last_success_at": success, "last_attempt_at": attempt["created_at"] if attempt else None,
            "state": state, "message": HEALTH_MESSAGES[state]}


# --- History ---------------------------------------------------------------------

def history_limit(raw):
    if raw is None:
        return HISTORY_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        raise CookingError(400, f"limit must be an integer from 1 to {HISTORY_MAX}.") from None
    return max(1, min(value, HISTORY_MAX))


def history_summaries(db, user_id, limit):
    rows = db.execute(
        "SELECT id, recipe_id, recipe_snapshot, status, created_at, updated_at, note FROM sessions"
        " WHERE user_id = ? AND status IN ('ended', 'completed') ORDER BY updated_at DESC, id DESC LIMIT ?",
        (user_id, limit)).fetchall()
    summaries = []
    for r in rows:
        try:
            name = json.loads(r["recipe_snapshot"])["name"] if r["recipe_snapshot"] else None
        except (ValueError, KeyError, TypeError):
            name = None
        summaries.append({"id": r["id"], "recipe_id": r["recipe_id"], "recipe_name": name, "status": r["status"],
                          "created_at": r["created_at"], "updated_at": r["updated_at"], "note": r["note"] or ""})
    return summaries


def validate_note(body):
    note = body.get("note")
    if set(body) != {"note"} or not isinstance(note, str) or len(note) > MAX_NOTE:
        raise CookingError(400, f"Send note as text of at most {MAX_NOTE} characters.")
    return note


# --- Voice context (read-only; request thread only) --------------------------------

def voice_context(db, session_id, at, clean, seconds):
    """Sentences about named timers and unacknowledged alerts for the voice tool."""
    parts = []
    timers = [t for t in timers_json(db, session_id, at) if t["status"] in ("running", "elapsed")]
    for t in timers[:MAX_LIVE_TIMERS]:
        name = clean(t["name"], MAX_TIMER_NAME)
        if t["status"] == "running":
            parts.append(f"Named timer {name} has {seconds(t['deadline_at'] - at)} left.")
        else:
            parts.append(f"Named timer {name} has finished.")
    if not timers:
        parts.append("No named timers are running.")
    alerts = [a for a in alerts_json(db, session_id) if a["acknowledged_at"] is None]
    for a in alerts[:5]:
        label = "Possible emergency" if a["category"] == "possible_emergency" else "Hazard"
        parts.append(f"Unacknowledged advisory camera alert ({label}): {clean(a['title'], MAX_ALERT_TITLE)}."
                     " It came from an earlier snapshot and may be wrong.")
    return " ".join(parts)
