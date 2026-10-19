"""Load structured recipe definitions from recipes/*.json (format: docs/recipes.md).

Recipes are plain data: nothing in them is executed, templated or sent anywhere except
as text in the model prompt and UI. Invalid files fail loudly at startup.
"""

import json
import os

RECIPE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipes")
MAX_FIELD = 2000


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


def validate_recipe(data, source):
    """Return a normalized copy containing only the documented fields."""
    def text(value, where):
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD:
            raise RecipeError(f"{source}: {where} must be a nonempty string (max {MAX_FIELD} characters)")
        return value.strip()

    if not isinstance(data, dict):
        raise RecipeError(f"{source}: file must contain one recipe object")
    ingredients, steps = data.get("ingredients"), data.get("steps")
    if not isinstance(ingredients, list):
        raise RecipeError(f"{source}: ingredients must be a list")
    if not isinstance(steps, list) or not steps:
        raise RecipeError(f"{source}: steps must be a nonempty list")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise RecipeError(f"{source}: steps[{i}] must be an object")
    return {
        "id": text(data.get("id"), "id"),
        "name": text(data.get("name"), "name"),
        "description": text(data.get("description"), "description"),
        "ingredients": [text(item, f"ingredients[{i}]") for i, item in enumerate(ingredients)],
        "steps": [
            {key: text(step.get(key), f"steps[{i}].{key}") for key in ("title", "instruction", "visual_cue")}
            for i, step in enumerate(steps)
        ],
    }
