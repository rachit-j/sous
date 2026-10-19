import json
import os
import tempfile
import unittest

from recipes import RecipeError, load_recipes

STEP = {"title": "Prepare", "instruction": "Do the thing.", "visual_cue": "The thing is visibly done."}


def recipe(recipe_id, steps=1):
    return {"id": recipe_id, "name": recipe_id.title(), "description": f"About {recipe_id}.",
            "ingredients": ["Something"],
            "steps": [{**STEP, "title": f"Step {i + 1}"} for i in range(steps)]}


class LoaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def write(self, filename, data):
        with open(os.path.join(self.dir, filename), "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    def assert_load_error(self, filename, fragment):
        with self.assertRaises(RecipeError) as caught:
            load_recipes(self.dir)
        self.assertIn(filename, str(caught.exception))
        self.assertIn(fragment, str(caught.exception))

    def test_bundled_sample_loads_first(self):
        self.assertEqual(next(iter(load_recipes())), "scrambled-eggs")

    def test_loads_custom_recipes_in_filename_order_with_any_step_count(self):
        self.write("20-soup.json", recipe("soup", steps=4))
        self.write("10-salad.json", recipe("salad", steps=2))
        self.write("README.md", "not a recipe")
        loaded = load_recipes(self.dir)
        self.assertEqual(list(loaded), ["salad", "soup"])
        self.assertEqual([len(r["steps"]) for r in loaded.values()], [2, 4])
        self.assertEqual(loaded["salad"], recipe("salad", steps=2))

    def test_extra_fields_are_dropped(self):
        self.write("a.json", {**recipe("a"), "script": "rm -rf /"})
        self.assertNotIn("script", load_recipes(self.dir)["a"])

    def test_invalid_json_names_the_file(self):
        self.write("01-ok.json", recipe("ok"))
        self.write("02-broken.json", '{"id": "broken",')
        self.assert_load_error("02-broken.json", "invalid JSON")

    def test_duplicate_ids_name_both_files(self):
        self.write("01-a.json", recipe("same"))
        self.write("02-b.json", recipe("same"))
        self.assert_load_error("02-b.json", "01-a.json")

    def test_bad_fields_fail_with_filename(self):
        good = recipe("x", steps=2)
        cases = (
            ([good], "one recipe object"),
            ({**good, "id": "  "}, "id"),
            ({**good, "name": 5}, "name"),
            ({**good, "description": None}, "description"),
            ({**good, "ingredients": "eggs"}, "ingredients"),
            ({**good, "ingredients": ["", "salt"]}, "ingredients[0]"),
            ({**good, "steps": []}, "steps"),
            ({**good, "steps": ["Stir"]}, "steps[0]"),
            ({**good, "steps": [STEP, {**STEP, "visual_cue": ""}]}, "steps[1].visual_cue"),
            ({**good, "steps": [{"title": "T", "instruction": "I"}]}, "steps[0].visual_cue"),
        )
        for data, fragment in cases:
            with self.subTest(fragment=fragment):
                self.write("bad.json", data)
                self.assert_load_error("bad.json", fragment)

    def test_empty_or_missing_directory_fails(self):
        with self.assertRaises(RecipeError):
            load_recipes(self.dir)
        with self.assertRaises(RecipeError):
            load_recipes(os.path.join(self.dir, "missing"))


if __name__ == "__main__":
    unittest.main()
