"""Load and validate structured recipe definitions (format: docs/recipes.md).

Recipes are plain data: nothing in them is executed, templated or sent anywhere except
as text in the model prompt and UI. Invalid bundled files fail loudly at startup. The same
validate_recipe checks library saves/edits and AI-generated drafts.
"""

import json
import math
import os
import re

RECIPE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipes")
MAX_FIELD = 2000
MAX_INGREDIENTS = 64
MAX_EQUIPMENT = 32
MAX_STEPS = 64
MIN_SERVINGS, MAX_SERVINGS = 1, 24
MAX_QUANTITY = 100000
MAX_UNIT = 40
# Markup-like text is refused outright. The browser still renders recipe text as text; this is
# defense in depth, not a substitute for that.
MARKUP = re.compile(r"<\s*[A-Za-z!/?]|javascript\s*:", re.IGNORECASE)


class RecipeError(ValueError):
    pass


def load_recipes(directory=None):
    """Return {id: recipe} for every *.json file in filename order."""
    directory = directory or RECIPE_DIR
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except OSError as exc:
        raise RecipeError(f"Cannot read recipe directory {directory}: {exc.strerror}") from None
    recipes, sources = {}, {}
    for name in names:
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise RecipeError(f"{name}: invalid JSON ({exc.msg} at line {exc.lineno})") from None
        except (OSError, UnicodeDecodeError) as exc:
            raise RecipeError(f"{name}: cannot read file ({exc})") from None
        recipe = validate_recipe(data, name)
        if recipe["id"] in recipes:
            raise RecipeError(f"{name}: duplicate recipe id '{recipe['id']}' (also in {sources[recipe['id']]})")
        recipes[recipe["id"]] = recipe
        sources[recipe["id"]] = name
    if not recipes:
        raise RecipeError(f"No recipe files (*.json) found in {directory}")
    return recipes


def validate_recipe(data, source, require_id=True, fill_defaults=False):
    """Return a normalized copy containing only the documented fields.

    require_id: bundled files carry their id; library and generated recipes get one from the
    server, so any supplied id is ignored. Optional servings/equipment/ingredient_details are
    validated when present and, with fill_defaults, always included (servings 1, equipment []).
    Error messages name fields only, never submitted values.
    """
    def text(value, where, limit=MAX_FIELD):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise RecipeError(f"{source}: {where} must be a nonempty string (max {limit} characters)")
        if MARKUP.search(value):
            raise RecipeError(f"{source}: {where} must be plain text without markup")
        return value.strip()

    def text_list(value, where, limit):
        if not isinstance(value, list) or len(value) > limit:
            raise RecipeError(f"{source}: {where} must be a list of at most {limit} strings")
        return [text(item, f"{where}[{i}]") for i, item in enumerate(value)]

    if not isinstance(data, dict):
        raise RecipeError(f"{source}: must contain one recipe object")
    ingredients, steps = data.get("ingredients"), data.get("steps")
    if not isinstance(ingredients, list):
        raise RecipeError(f"{source}: ingredients must be a list")
    if not isinstance(steps, list) or not steps:
        raise RecipeError(f"{source}: steps must be a nonempty list")
    if len(steps) > MAX_STEPS:
        raise RecipeError(f"{source}: steps must have at most {MAX_STEPS} entries")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise RecipeError(f"{source}: steps[{i}] must be an object")
    recipe = {}
    if require_id:
        recipe["id"] = text(data.get("id"), "id")
    recipe.update({
        "name": text(data.get("name"), "name"),
        "description": text(data.get("description"), "description"),
        "ingredients": text_list(ingredients, "ingredients", MAX_INGREDIENTS),
        "steps": [
            {key: text(step.get(key), f"steps[{i}].{key}") for key in ("title", "instruction", "visual_cue")}
            for i, step in enumerate(steps)
        ],
    })
    if "servings" in data or fill_defaults:
        servings = data.get("servings", MIN_SERVINGS)
        if type(servings) is not int or not MIN_SERVINGS <= servings <= MAX_SERVINGS:
            raise RecipeError(f"{source}: servings must be an integer from {MIN_SERVINGS} to {MAX_SERVINGS}")
        recipe["servings"] = servings
    if "equipment" in data or fill_defaults:
        recipe["equipment"] = text_list(data.get("equipment", []), "equipment", MAX_EQUIPMENT)
    if data.get("ingredient_details") is not None:
        recipe["ingredient_details"] = validate_details(data["ingredient_details"], len(recipe["ingredients"]),
                                                        source, text)
    return recipe


def validate_details(details, count, source, text):
    """Structured amounts aligned one-to-one with ingredients: {name, quantity: number|null, unit}."""
    if not isinstance(details, list) or len(details) != count:
        raise RecipeError(f"{source}: ingredient_details must list one entry per ingredient")
    result = []
    for i, entry in enumerate(details):
        where = f"ingredient_details[{i}]"
        if not isinstance(entry, dict) or set(entry) != {"name", "quantity", "unit"}:
            raise RecipeError(f"{source}: {where} must have exactly name, quantity and unit")
        quantity = entry["quantity"]
        if quantity is not None and (type(quantity) not in (int, float) or not math.isfinite(quantity)
                                     or not 0 < quantity <= MAX_QUANTITY):
            raise RecipeError(f"{source}: {where}.quantity must be null or a number above 0 and at most"
                              f" {MAX_QUANTITY}")
        unit = entry["unit"]
        if not isinstance(unit, str) or len(unit) > MAX_UNIT or MARKUP.search(unit):
            raise RecipeError(f"{source}: {where}.unit must be plain text of at most {MAX_UNIT} characters")
        result.append({"name": text(entry["name"], f"{where}.name"), "quantity": quantity, "unit": unit.strip()})
    return result
