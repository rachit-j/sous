"""Recipe library, profile and serving drafts (docs/v2-api.md, docs/recipes.md).

Bundled recipes (recipes/*.json) are read-only and visible to everyone. Database recipes are
either personal (owner_id = the signed-in account) or global (owner_id NULL, administrators
edit). Every query below carries the access rule in SQL, so a row the caller may not see is
indistinguishable from a missing one. Deleting archives a row: cooking snapshots already copied
the recipe and are never touched. IDs are opaque server values that never shadow bundled ids.
"""

import copy
import json
import secrets

from recipes import MAX_QUANTITY, MAX_SERVINGS, MIN_SERVINGS, RecipeError, validate_recipe

MAX_PERSONAL = 200  # active personal recipes per account
MAX_GLOBAL = 200  # active global database recipes
MAX_BODY = 64 * 1024
SCOPES = ("personal", "global")
PROFILE_DEFAULTS = {"pantry": [], "equipment": [], "servings": 2, "time_budget_minutes": 30}
MAX_PANTRY, MAX_PROFILE_EQUIPMENT, MAX_ITEM = 64, 32, 200
MAX_MINUTES = 1440

SCHEMA = """
CREATE TABLE IF NOT EXISTS library_recipes (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('personal', 'global')),
    owner_id INTEGER REFERENCES users(id),  -- personal: the account; global: NULL
    recipe TEXT NOT NULL,  -- canonical validate_recipe JSON without id
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL,  -- deleted from the library; kept for history
    CHECK ((scope = 'personal') = (owner_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS library_by_owner ON library_recipes(owner_id, archived_at, created_at);
"""

# Rows the user may see: active global rows and their own active personal rows.
VISIBLE = "archived_at IS NULL AND (scope = 'global' OR (scope = 'personal' AND owner_id = :user))"


class LibraryError(Exception):
    """Client-facing failure with a static message."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


NOT_FOUND = "Recipe not found."


def canonical(data, source="recipe"):
    """validate_recipe for stored and generated recipes: server ids, documented defaults."""
    try:
        return validate_recipe(data, source, require_id=False, fill_defaults=True)
    except RecipeError as exc:
        raise LibraryError(400, str(exc)) from None


def _record(rid, recipe, scope, can_edit):
    return {**recipe, "id": rid, "scope": scope, "can_edit": can_edit, "can_delete": can_edit}


def _can_edit(row, user):
    return row["scope"] == "personal" or bool(user["is_admin"])  # personal rows here are always own


def _row_record(row, user):
    return _record(row["id"], json.loads(row["recipe"]), row["scope"], _can_edit(row, user))


def visible_rows(db, user):
    return db.execute(f"SELECT * FROM library_recipes WHERE {VISIBLE}"
                      " ORDER BY scope = 'personal', created_at, id", {"user": user["id"]}).fetchall()


def list_records(db, user, bundled):
    """Bundled first (file order), then global, then the caller's personal recipes."""
    records = [_record(rid, {k: v for k, v in r.items() if k != "id"}, "global", False)  # shared, read-only
               for rid, r in bundled.items()]
    return records + [_row_record(row, user) for row in visible_rows(db, user)]


def cookable(db, user, bundled):
    """{id: recipe} the caller may cook, in library order (the session snapshot source)."""
    recipes = dict(bundled)
    for row in visible_rows(db, user):
        recipes[row["id"]] = {"id": row["id"], **json.loads(row["recipe"])}
    return recipes


def find_record(db, user, bundled, rid):
    """Accessible record or LibraryError 404 (another account's or archived rows look missing)."""
    if not isinstance(rid, str):
        raise LibraryError(404, NOT_FOUND)
    if rid in bundled:
        return _record(rid, {k: v for k, v in bundled[rid].items() if k != "id"}, "global", False)
    row = db.execute(f"SELECT * FROM library_recipes WHERE id = :id AND {VISIBLE}",
                     {"id": rid, "user": user["id"]}).fetchone()
    if row is None:
        raise LibraryError(404, NOT_FOUND)
    return _row_record(row, user)


def editable_row(db, user, bundled, rid):
    record = find_record(db, user, bundled, rid)
    if not record["can_edit"]:
        raise LibraryError(403, "This recipe is read-only. Copy it to your library to change it.")
    return record


def strip_meta(record):
    return {k: v for k, v in record.items() if k not in ("id", "scope", "can_edit", "can_delete")}


def new_id(db, bundled):
    while True:
        rid = "r-" + secrets.token_urlsafe(12)
        if rid not in bundled and db.execute("SELECT 1 FROM library_recipes WHERE id = ?", (rid,)).fetchone() is None:
            return rid


def insert(db, user, bundled, recipe, scope, at):
    """Insert inside the caller's write transaction; enforces catalog size limits."""
    if scope == "personal":
        count = db.execute("SELECT COUNT(*) FROM library_recipes WHERE scope = 'personal' AND owner_id = ?"
                           " AND archived_at IS NULL", (user["id"],)).fetchone()[0]
        limit = MAX_PERSONAL
    else:
        count = db.execute("SELECT COUNT(*) FROM library_recipes WHERE scope = 'global'"
                           " AND archived_at IS NULL").fetchone()[0]
        limit = MAX_GLOBAL
    if count >= limit:
        raise LibraryError(409, f"The {scope} library is full ({limit} recipes). Delete one before adding more.")
    rid = new_id(db, bundled)
    db.execute("INSERT INTO library_recipes (id, scope, owner_id, recipe, created_at, updated_at)"
               " VALUES (?, ?, ?, ?, ?, ?)",
               (rid, scope, user["id"] if scope == "personal" else None, json.dumps(recipe), at, at))
    return _record(rid, recipe, scope, True)


def update(db, user, bundled, rid, recipe, at):
    record = editable_row(db, user, bundled, rid)
    db.execute("UPDATE library_recipes SET recipe = ?, updated_at = ? WHERE id = ?", (json.dumps(recipe), at, rid))
    return _record(rid, recipe, record["scope"], True)


def archive(db, user, bundled, rid, at):
    editable_row(db, user, bundled, rid)
    db.execute("UPDATE library_recipes SET archived_at = ? WHERE id = ?", (at, rid))


# --- Request bodies --------------------------------------------------------

def create_body(body, user):
    if set(body) != {"recipe", "scope"}:
        raise LibraryError(400, "Send exactly recipe and scope. Recipes always belong to the signed-in account.")
    scope = body["scope"]
    if scope not in SCOPES:
        raise LibraryError(400, "scope must be 'personal' or 'global'.")
    if scope == "global" and not user["is_admin"]:
        raise LibraryError(403, "Only administrators can add global recipes.")
    return canonical(body["recipe"]), scope


def update_body(body):
    if set(body) != {"recipe"}:
        raise LibraryError(400, "Send exactly recipe; identity and scope cannot change.")
    return canonical(body["recipe"])


def servings_body(body):
    servings = body.get("servings")
    if set(body) != {"servings"} or type(servings) is not int or not MIN_SERVINGS <= servings <= MAX_SERVINGS:
        raise LibraryError(400, f"Send servings as an integer from {MIN_SERVINGS} to {MAX_SERVINGS}.")
    return servings


# --- Serving drafts --------------------------------------------------------

SCALE_REVIEW = "Review the scaled amounts before cooking. Cooking times, temperatures and steps were not changed."
SCALE_UNKNOWN = "Some ingredients have no known quantity and were left unchanged; adjust them yourself."


def _number(value):
    value = round(value, 3)
    return int(value) if value == int(value) else value


def _display(detail):
    quantity = detail["quantity"]
    amount = str(quantity) if isinstance(quantity, int) else f"{quantity:g}"
    return " ".join(part for part in (amount, detail["unit"], detail["name"]) if part)


def scale(recipe, servings):
    """Unsaved {recipe, warnings} draft for a different serving count.

    Only aligned ingredient_details with numeric quantities change; their display strings are
    rebuilt from name/unit. Everything else, including steps, times and temperatures, is copied.
    """
    draft = copy.deepcopy(strip_meta(recipe))
    factor = servings / draft.get("servings", MIN_SERVINGS)
    draft["servings"] = servings
    draft.setdefault("equipment", [])
    warnings = [SCALE_REVIEW]
    details = draft.get("ingredient_details")
    if not details:
        if draft["ingredients"]:
            warnings.append(SCALE_UNKNOWN)
        return {"recipe": draft, "warnings": warnings}
    unknown = False
    for i, detail in enumerate(details):
        if detail["quantity"] is None:
            unknown = True
            continue
        scaled = _number(detail["quantity"] * factor)
        if scaled > MAX_QUANTITY:
            raise LibraryError(400, f"Scaled amounts would exceed {MAX_QUANTITY}. Choose fewer servings.")
        if scaled <= 0:
            unknown = True  # too small to show; keep the original amount for the cook to adjust
            continue
        detail["quantity"] = scaled
        draft["ingredients"][i] = _display(detail)
    if unknown:
        warnings.append(SCALE_UNKNOWN)
    return {"recipe": draft, "warnings": warnings}


# --- Profile ---------------------------------------------------------------

def validate_profile(body):
    if set(body) != set(PROFILE_DEFAULTS):
        raise LibraryError(400, "Send exactly pantry, equipment, servings and time_budget_minutes.")
    return {
        "pantry": items(body["pantry"], "pantry", MAX_PANTRY),
        "equipment": items(body["equipment"], "equipment", MAX_PROFILE_EQUIPMENT),
        "servings": integer(body["servings"], "servings", MIN_SERVINGS, MAX_SERVINGS),
        "time_budget_minutes": integer(body["time_budget_minutes"], "time_budget_minutes", 1, MAX_MINUTES),
    }


def items(value, name, limit):
    if (not isinstance(value, list) or len(value) > limit
            or not all(isinstance(v, str) and v.strip() and len(v) <= MAX_ITEM for v in value)):
        raise LibraryError(400, f"{name} must be a list of at most {limit} nonempty strings of up to"
                                f" {MAX_ITEM} characters.")
    return [v.strip() for v in value]


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise LibraryError(400, f"{name} must be an integer from {low} to {high}.")
    return value


def stored_profile(value):
    try:
        return validate_profile(value) if isinstance(value, dict) else dict(PROFILE_DEFAULTS)
    except LibraryError:
        return dict(PROFILE_DEFAULTS)
