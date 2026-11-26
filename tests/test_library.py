"""V2.2 recipe library, profile, AI recipe drafts and serving drafts (docs/v2-api.md), written first.

Contract choices pinned here (additive metadata is allowed everywhere):
  GET  /api/recipes -> {"recipes": [...]}; each record has id, scope, can_edit, can_delete plus
       the recipe fields. Bundled recipes are listed first with can_edit/can_delete false.
  POST /api/recipes {recipe, scope:"personal"|"global"} -> 201 {"recipe"}; server-assigned id,
       owner fields rejected (400), non-admin global 403, invalid recipe 400, body > 64 KiB 413.
  GET/PUT/DELETE /api/recipes/<id> -> {"recipe"} / {"recipe"} / 200; PUT keeps id and scope.
       Not accessible -> 404 (same as missing); accessible but read-only -> 403.
  POST /api/recipes/<id>/copy {} -> 201 {"recipe"} personal copy with a new id.
  POST /api/recipes/<id>/scale {servings} -> 200 {"recipe", "warnings"}; nothing saved.
  GET/PUT /api/profile -> {pantry, equipment, servings, time_budget_minutes}; defaults [], [], 2, 30.
  POST /api/recipes/generate {dish, ingredients, pantry?, equipment, servings, time_budget_minutes}
       -> 200 {"recipe", "warnings"} unsaved. Bad input 400 before AWS; no usable own Bedrock
       connection/model 409; unusable model output 502; logout during the call 401; vault
       lock/change during the call 409; per-user in-flight or shared cap of two 409/429; more
       than six accepted generations per user within 60 s of the app CLOCK 429 (rejections
       before the AWS call do not count).
Model choice: saved default {provider: bedrock, model} wins; otherwise the connection's model.

boto3.Session and boto3.client are always mocked; no AWS, network or real credentials.
"""

import copy
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from jinja2 import DictLoader

from app import create_app
from tests.test_accounts import ALICE_PW, BOB_PW, ENV_AWS, Cook
from tests.test_app import INDEX

CAROL_PW = "carol-login-password-0005"
VAULT_PASS = {"alice": "alice vault passphrase A", "bob": "bob vault passphrase B",
              "carol": "carol vault passphrase C"}
KEYS = {"alice": ("AKIAALICESENTINEL000", "alice-sentinel-secret-0123456789"),
        "bob": ("AKIABOBSENTINEL00000", "bob-sentinel-secret-0123456789"),
        "carol": ("AKIACAROLSENTINEL000", "carol-sentinel-secret-0123456789")}
BUNDLED_ID = "scrambled-eggs"  # first bundled sample; only used as a catalog fixture

OMELETTE = {
    "name": "Weeknight Omelette",
    "description": "A quick folded omelette.",
    "ingredients": ["2 eggs", "1 tbsp butter", "salt to taste"],
    "servings": 2,
    "equipment": ["nonstick pan", "whisk"],
    "ingredient_details": [
        {"name": "eggs", "quantity": 2, "unit": ""},
        {"name": "butter", "quantity": 1, "unit": "tbsp"},
        {"name": "salt to taste", "quantity": None, "unit": ""},
    ],
    "steps": [
        {"title": "Whisk", "instruction": "Whisk the eggs with salt.", "visual_cue": "Eggs are smooth and yellow."},
        {"title": "Cook", "instruction": "Cook at 180 C for 3 minutes, then fold.",
         "visual_cue": "The omelette is set and folded."},
    ],
}
GENERATED = {
    "name": "Tomato Toast",
    "description": "Crisp toast topped with tomato.",
    "ingredients": ["2 slices bread", "1 tomato"],
    "servings": 2,
    "equipment": ["toaster"],
    "ingredient_details": [{"name": "slices bread", "quantity": 2, "unit": ""},
                           {"name": "tomato", "quantity": 1, "unit": ""}],
    "steps": [
        {"title": "Toast", "instruction": "Toast the bread.", "visual_cue": "Bread is golden."},
        {"title": "Top", "instruction": "Slice the tomato onto the toast.", "visual_cue": "Tomato covers the toast."},
    ],
}
GENERATE = {"dish": "tomato toast", "ingredients": ["bread", "tomato"], "pantry": ["salt"],
            "equipment": ["toaster"], "servings": 2, "time_budget_minutes": 15}


def converse_reply(text):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": "end_turn"}


class LibraryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "sous.sqlite3")
        self.clock = 1_000_000.0
        self.replies = []  # model texts returned in order; default is a valid GENERATED recipe
        self.converse_calls = []
        self.on_converse = None
        # Whatever helper the implementation uses, AWS clients come from these two mocks.
        session = mock.patch("boto3.Session")
        self.session_cls = session.start()
        self.addCleanup(session.stop)
        client = mock.patch("boto3.client")
        self.client_fn = client.start()
        self.addCleanup(client.stop)
        for factory in (self.session_cls.return_value.client, self.client_fn):
            factory.return_value.converse.side_effect = self.fake_converse
        self.app = self.make_app()

    def make_app(self):
        app = create_app({"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "test-secret",
                          "CLOCK": lambda: self.clock})
        app.jinja_env.loader = DictLoader({"index.html": INDEX})
        return app

    def fake_converse(self, **kwargs):
        self.converse_calls.append(kwargs)
        if self.on_converse:
            hook, self.on_converse = self.on_converse, None  # one-shot; nested calls see None
            hook()
        text = self.replies.pop(0) if self.replies else json.dumps(GENERATED)
        return converse_reply(text)

    def aws_key_ids(self):
        calls = self.session_cls.call_args_list + self.client_fn.call_args_list
        return [c.kwargs.get("aws_access_key_id") for c in calls]

    # --- accounts (real APIs)

    def anon(self):
        return Cook(self, self.app.test_client())

    def setup_admin(self):
        cook = self.anon()
        resp = cook.send("POST", "/api/auth/setup", {"username": "alice", "password": ALICE_PW})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        cook.refresh()
        return cook

    def login(self, username, password):
        cook = self.anon()
        resp = cook.send("POST", "/api/auth/login", {"username": username, "password": password})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        cook.refresh()
        return cook

    def add_user(self, admin, username, password):
        resp = admin.send("POST", "/api/users", {"username": username, "password": password})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return self.login(username, password)

    def pair(self):
        alice = self.setup_admin()
        return alice, self.add_user(alice, "bob", BOB_PW)

    def connect_aws(self, cook, name, model="conn-model"):
        resp = cook.send("POST", "/api/settings/vault", {"passphrase": VAULT_PASS[name]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        key_id, secret = KEYS[name]
        resp = cook.send("PUT", "/api/settings/bedrock", {"region": "us-west-2", "model": model,
                                                          "access_key_id": key_id, "secret_access_key": secret})
        self.assertEqual(resp.status_code, 200, resp.get_json())

    # --- library

    def library(self, cook):
        resp = cook.get("/api/recipes")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()["recipes"]

    def ids(self, cook):
        return [r["id"] for r in self.library(cook)]

    def create(self, cook, recipe=None, scope="personal", expect=201):
        resp = cook.send("POST", "/api/recipes", {"recipe": recipe or copy.deepcopy(OMELETTE), "scope": scope})
        self.assertEqual(resp.status_code, expect, resp.get_json())
        return resp.get_json().get("recipe") if expect == 201 else resp

    def fetch(self, cook, rid):
        return cook.get(f"/api/recipes/{rid}")

    def start(self, cook, recipe_id, expect=201):
        resp = cook.send("POST", "/api/sessions", {"recipe_id": recipe_id, "provider": "ollama",
                                                   "model": "llava", "mode": "monitoring"})
        self.assertEqual(resp.status_code, expect, resp.get_json())
        return resp.get_json()

    def generate(self, cook, **body):
        return cook.send("POST", "/api/recipes/generate", {**GENERATE, **body})

    def assert_recipe_fields(self, record, recipe):
        for key in ("name", "description", "ingredients", "steps"):
            self.assertEqual(record[key], recipe[key], key)

    def assert_warnings(self, data):
        self.assertIsInstance(data["warnings"], list)
        self.assertTrue(data["warnings"])
        self.assertTrue(all(isinstance(w, str) and w for w in data["warnings"]))


class CatalogTests(LibraryTestCase):
    def test_bundled_recipes_are_listed_read_only_and_copyable(self):
        alice, bob = self.pair()
        for cook in (alice, bob):  # administrators cannot edit bundled files either
            recipes = self.library(cook)
            bundled = next(r for r in recipes if r["id"] == BUNDLED_ID)
            self.assertEqual((bundled["can_edit"], bundled["can_delete"]), (False, False))
            self.assertEqual(bundled["scope"], "global")  # shared by everyone, but read-only
            self.assertEqual(self.fetch(cook, BUNDLED_ID).get_json()["recipe"]["scope"], "global")
            self.assertEqual(self.fetch(cook, BUNDLED_ID).get_json()["recipe"]["id"], BUNDLED_ID)
            self.assertEqual(cook.send("PUT", f"/api/recipes/{BUNDLED_ID}", {"recipe": OMELETTE}).status_code, 403)
            self.assertEqual(cook.send("DELETE", f"/api/recipes/{BUNDLED_ID}").status_code, 403)
        original = self.fetch(alice, BUNDLED_ID).get_json()["recipe"]

        resp = bob.send("POST", f"/api/recipes/{BUNDLED_ID}/copy", {})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        mine = resp.get_json()["recipe"]
        self.assertNotEqual(mine["id"], BUNDLED_ID)
        self.assertEqual((mine["scope"], mine["can_edit"], mine["can_delete"]), ("personal", True, True))
        self.assert_recipe_fields(mine, original)
        self.assertIn(mine["id"], self.ids(bob))
        self.assertNotIn(mine["id"], self.ids(alice))
        self.assertEqual(self.ids(bob)[0], self.ids(alice)[0])  # bundled order stays first and stable

    def test_personal_recipe_crud_assigns_server_id_and_preserves_identity(self):
        alice, _ = self.pair()
        made = self.create(alice)
        rid = made["id"]
        self.assertIsInstance(rid, str)
        self.assertTrue(rid)
        self.assertEqual((made["scope"], made["can_edit"], made["can_delete"]), ("personal", True, True))
        self.assert_recipe_fields(made, OMELETTE)
        self.assertEqual((made["servings"], made["equipment"]), (2, ["nonstick pan", "whisk"]))
        self.assertEqual(self.fetch(alice, rid).get_json()["recipe"]["id"], rid)

        edited = {**copy.deepcopy(OMELETTE), "id": "something-else", "name": "Sunday Omelette"}
        resp = alice.send("PUT", f"/api/recipes/{rid}", {"recipe": edited})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        saved = resp.get_json()["recipe"]
        self.assertEqual((saved["id"], saved["scope"], saved["name"]), (rid, "personal", "Sunday Omelette"))
        self.assertEqual(self.fetch(alice, "something-else").status_code, 404)

        # A client-supplied bundled id never shadows the bundled recipe.
        resp = alice.send("POST", "/api/recipes", {"recipe": {**OMELETTE, "id": BUNDLED_ID}, "scope": "personal"})
        self.assertIn(resp.status_code, (201, 400))
        if resp.status_code == 201:
            self.assertNotEqual(resp.get_json()["recipe"]["id"], BUNDLED_ID)
        self.assertEqual(self.ids(alice).count(BUNDLED_ID), 1)
        self.assertEqual((self.fetch(alice, BUNDLED_ID).get_json()["recipe"]["can_edit"]), False)

        self.assertEqual(alice.send("DELETE", f"/api/recipes/{rid}").status_code, 200)
        self.assertEqual(self.fetch(alice, rid).status_code, 404)
        self.assertNotIn(rid, self.ids(alice))
        self.assertEqual(alice.send("PUT", f"/api/recipes/{rid}", {"recipe": OMELETTE}).status_code, 404)

    def test_personal_recipes_are_private_to_their_owner(self):
        alice, bob = self.pair()  # alice is an administrator: still no access to Bob's rows
        bobs = self.create(bob)["id"]
        alices = self.create(alice)["id"]
        self.assertNotIn(bobs, self.ids(alice))
        self.assertNotIn(alices, self.ids(bob))
        self.assertNotIn(bobs, [r["id"] for r in alice.get("/api/config").get_json()["recipes"]])
        self.assertIn(bobs, [r["id"] for r in bob.get("/api/config").get_json()["recipes"]])

        missing = self.fetch(alice, "no-such-recipe")
        for cook, rid in ((alice, bobs), (bob, alices)):
            denied = self.fetch(cook, rid)
            self.assertEqual((denied.status_code, denied.get_json()), (missing.status_code, missing.get_json()))
            self.assertEqual(denied.status_code, 404)
            for method, path, body in (("PUT", f"/api/recipes/{rid}", {"recipe": OMELETTE}),
                                       ("DELETE", f"/api/recipes/{rid}", None),
                                       ("POST", f"/api/recipes/{rid}/copy", {}),
                                       ("POST", f"/api/recipes/{rid}/scale", {"servings": 4})):
                self.assertEqual(cook.send(method, path, body).status_code, 404, (method, path))
            self.start(cook, rid, expect=400)  # cannot cook someone else's recipe
        self.assertEqual(self.fetch(bob, bobs).get_json()["recipe"]["name"], OMELETTE["name"])
        self.assertEqual(self.fetch(alice, alices).get_json()["recipe"]["name"], OMELETTE["name"])

    def test_forged_owner_and_scope_fields_cannot_claim_or_publish(self):
        alice, bob = self.pair()
        alice_id = alice.user["id"]
        before = self.ids(alice)
        for forged in ({"owner_id": alice_id}, {"user_id": alice_id}):
            resp = bob.send("POST", "/api/recipes", {"recipe": OMELETTE, "scope": "personal", **forged})
            self.assertEqual(resp.status_code, 400, forged)
        for scope in ("global", "public", "bundled", None, ["personal"]):
            resp = bob.send("POST", "/api/recipes", {"recipe": OMELETTE, "scope": scope})
            self.assertIn(resp.status_code, (400, 403), scope)
        self.assertEqual(bob.send("POST", "/api/recipes", {"recipe": OMELETTE, "scope": "global"}).status_code, 403)

        resp = bob.send("POST", "/api/recipes", {"recipe": {**OMELETTE, "owner_id": alice_id}, "scope": "personal"})
        self.assertIn(resp.status_code, (201, 400))  # nested owner data is never trusted
        self.assertEqual(self.ids(alice), before)
        if resp.status_code == 201:
            rid = resp.get_json()["recipe"]["id"]
            self.assertEqual(self.fetch(alice, rid).status_code, 404)
            self.assertIn(rid, self.ids(bob))

        rid = self.create(bob)["id"]
        resp = bob.send("PUT", f"/api/recipes/{rid}", {"recipe": OMELETTE, "scope": "global"})
        self.assertIn(resp.status_code, (400, 403))
        self.assertEqual(self.fetch(bob, rid).get_json()["recipe"]["scope"], "personal")
        self.assertNotIn(rid, self.ids(alice))

    def test_admin_global_recipes_are_shared_but_only_admins_edit(self):
        alice, bob = self.pair()
        shared = self.create(alice, scope="global")
        rid = shared["id"]
        self.assertEqual((shared["scope"], shared["can_edit"]), ("global", True))
        seen = next(r for r in self.library(bob) if r["id"] == rid)
        self.assertEqual((seen["scope"], seen["can_edit"], seen["can_delete"]), ("global", False, False))
        self.assertEqual(bob.send("PUT", f"/api/recipes/{rid}", {"recipe": OMELETTE}).status_code, 403)
        self.assertEqual(bob.send("DELETE", f"/api/recipes/{rid}").status_code, 403)
        self.assertEqual(self.start(bob, rid)["recipe"]["name"], OMELETTE["name"])  # everyone may cook it

        copied = bob.send("POST", f"/api/recipes/{rid}/copy", {})
        self.assertEqual(copied.status_code, 201)
        self.assertEqual(copied.get_json()["recipe"]["scope"], "personal")
        self.assertNotIn(copied.get_json()["recipe"]["id"], self.ids(alice))

        resp = alice.send("PUT", f"/api/recipes/{rid}", {"recipe": {**OMELETTE, "name": "House Omelette"}})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["recipe"]["scope"], "global")
        self.assertEqual(self.fetch(bob, rid).get_json()["recipe"]["name"], "House Omelette")
        self.assertEqual(alice.send("DELETE", f"/api/recipes/{rid}").status_code, 200)
        self.assertNotIn(rid, self.ids(bob))

    def test_schema_bounds_and_unsafe_text_are_rejected_without_saving(self):
        alice, _ = self.pair()
        before = self.ids(alice)
        step = OMELETTE["steps"][0]
        bad = [
            {"servings": 0}, {"servings": 25}, {"servings": 1.5}, {"servings": True}, {"servings": "2"},
            {"equipment": "pan"}, {"equipment": ["pan"] * 33}, {"equipment": [""]}, {"equipment": [5]},
            {"ingredients": ["egg"] * 65}, {"ingredients": "eggs"}, {"ingredients": ["x" * 2001]},
            {"steps": []}, {"steps": [step] * 65}, {"steps": [{**step, "visual_cue": ""}]},
            {"name": ""}, {"name": "x" * 2001}, {"description": None},
            {"name": "<script>alert(1)</script>"},
            {"ingredient_details": OMELETTE["ingredient_details"][:2]},  # not aligned
            {"ingredient_details": [{"name": "eggs", "quantity": -2, "unit": ""}] + OMELETTE["ingredient_details"][1:]},
            {"ingredient_details": [{"name": "eggs", "quantity": 0, "unit": ""}] + OMELETTE["ingredient_details"][1:]},
            {"ingredient_details": [{"name": "eggs", "quantity": 100001, "unit": ""}]
             + OMELETTE["ingredient_details"][1:]},
            {"ingredient_details": [{"name": "eggs", "quantity": "2", "unit": ""}] + OMELETTE["ingredient_details"][1:]},
            {"ingredient_details": [{"name": "eggs", "quantity": True, "unit": ""}] + OMELETTE["ingredient_details"][1:]},
            {"ingredient_details": [{"name": "eggs", "quantity": 2}] + OMELETTE["ingredient_details"][1:]},
        ]
        for change in bad:
            with self.subTest(change=str(change)[:60]):
                self.create(alice, {**copy.deepcopy(OMELETTE), **change}, expect=400)
        for body in ([], {"recipe": OMELETTE}, {"recipe": "text", "scope": "personal"},
                     {"recipe": OMELETTE, "scope": "personal", "extra": 1}):
            self.assertEqual(alice.send("POST", "/api/recipes", body).status_code, 400, body)
        huge = {"recipe": OMELETTE, "scope": "personal", "pad": "x" * (65 * 1024)}
        self.assertEqual(alice.send("POST", "/api/recipes", huge).status_code, 413)
        self.assertEqual(self.ids(alice), before)

        minimal = {k: OMELETTE[k] for k in ("name", "description", "ingredients", "steps")}
        made = self.create(alice, minimal)  # V1 fields alone stay valid with documented defaults
        self.assertEqual((made["servings"], made["equipment"]), (1, []))

    def test_cooking_snapshot_survives_library_edit_and_delete(self):
        alice, _ = self.pair()
        rid = self.create(alice)["id"]
        session = self.start(alice, rid)  # local Ollama cook; no model call is made
        self.assertEqual(session["recipe"]["name"], OMELETTE["name"])
        steps = session["recipe"]["steps"]

        resp = alice.send("PUT", f"/api/recipes/{rid}", {"recipe": {**OMELETTE, "name": "Renamed",
                                                                    "steps": OMELETTE["steps"][:1]}})
        self.assertEqual(resp.status_code, 200)
        current = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertEqual((current["recipe"]["name"], current["recipe"]["steps"]), (OMELETTE["name"], steps))
        self.assertEqual(alice.send("DELETE", f"/api/recipes/{rid}").status_code, 200)
        current = alice.get("/api/sessions/active").get_json()["session"]
        self.assertEqual((current["id"], current["recipe"]["steps"]), (session["id"], steps))
        moved = alice.send("POST", f"/api/sessions/{session['id']}/actions", {"action": "next"})
        self.assertEqual(moved.get_json()["step_index"], 1)  # still bound to the snapshot's two steps

        alice.send("POST", f"/api/sessions/{session['id']}/actions", {"action": "end"})
        self.start(alice, rid, expect=400)  # a deleted recipe cannot start a new cook


class ProfileTests(LibraryTestCase):
    def test_profile_defaults_validation_isolation_and_persistence(self):
        alice, bob = self.pair()
        defaults = {"pantry": [], "equipment": [], "servings": 2, "time_budget_minutes": 30}
        self.assertEqual({k: alice.get("/api/profile").get_json()[k] for k in defaults}, defaults)
        good = {"pantry": ["rice", "olive oil"], "equipment": ["oven"], "servings": 4, "time_budget_minutes": 45}
        bad = [{**good, "servings": 0}, {**good, "servings": 25}, {**good, "servings": "4"},
               {**good, "time_budget_minutes": 0}, {**good, "time_budget_minutes": 1441},
               {**good, "pantry": ["x"] * 65}, {**good, "pantry": ["x" * 201]}, {**good, "pantry": [3]},
               {**good, "equipment": ["x"] * 33}, {**good, "equipment": "oven"},
               {k: v for k, v in good.items() if k != "pantry"}, {**good, "user_id": bob.user["id"]}, []]
        for body in bad:
            self.assertEqual(alice.send("PUT", "/api/profile", body).status_code, 400, str(body)[:60])
        self.assertEqual(alice.send("PUT", "/api/profile", good, token="wrong").status_code, 403)
        resp = alice.send("PUT", "/api/profile", good)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual({k: resp.get_json()[k] for k in good}, good)
        self.assertEqual({k: bob.get("/api/profile").get_json()[k] for k in defaults}, defaults)

        self.app = self.make_app()  # restart
        alice = self.login("alice", ALICE_PW)
        self.assertEqual({k: alice.get("/api/profile").get_json()[k] for k in good}, good)
        self.assertEqual(self.anon().get("/api/profile").status_code, 401)


class ScaleTests(LibraryTestCase):
    def test_scaling_multiplies_known_quantities_only_and_never_saves(self):
        alice, _ = self.pair()
        rid = self.create(alice)["id"]
        session = self.start(alice, rid)
        before_library = self.library(alice)

        resp = alice.send("POST", f"/api/recipes/{rid}/scale", {"servings": 4})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        draft = data["recipe"]
        self.assert_warnings(data)  # salt has no quantity
        self.assertEqual(draft["servings"], 4)
        self.assertEqual([d["quantity"] for d in draft["ingredient_details"]], [4, 2, None])
        self.assertEqual([d["unit"] for d in draft["ingredient_details"]], ["", "tbsp", ""])
        eggs, butter, salt = draft["ingredients"]
        self.assertRegex(eggs, r"(^|\D)4(\.0+)?\s+eggs")
        self.assertRegex(butter, r"(^|\D)2(\.0+)?\s+tbsp\s+butter")
        self.assertEqual(eggs.count("eggs"), 1)  # amount is not duplicated into the name
        self.assertEqual(salt, "salt to taste")  # unknown quantities stay unchanged
        self.assertEqual(draft["steps"], OMELETTE["steps"])  # times and temperatures never scaled
        self.assertEqual(draft["equipment"], OMELETTE["equipment"])

        self.assertEqual(self.library(alice), before_library)  # nothing saved or changed
        self.assertEqual(self.fetch(alice, rid).get_json()["recipe"]["ingredients"], OMELETTE["ingredients"])
        current = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertEqual((current["recipe"]["ingredients"], current["recipe"].get("servings", 2)),
                         (OMELETTE["ingredients"], 2))

        half = alice.send("POST", f"/api/recipes/{rid}/scale", {"servings": 1}).get_json()["recipe"]
        self.assertEqual([d["quantity"] for d in half["ingredient_details"]], [1, 0.5, None])

    def test_scaling_unknown_quantities_bounds_and_access(self):
        alice, bob = self.pair()
        resp = alice.send("POST", f"/api/recipes/{BUNDLED_ID}/scale", {"servings": 3})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        original = self.fetch(alice, BUNDLED_ID).get_json()["recipe"]
        self.assert_warnings(data)  # no structured quantities: review warning
        self.assertEqual((data["recipe"]["ingredients"], data["recipe"]["steps"]),
                         (original["ingredients"], original["steps"]))
        self.assertEqual(data["recipe"]["servings"], 3)

        rid = self.create(alice)["id"]
        for servings in (0, 25, 2.5, "4", None, True):
            self.assertEqual(alice.send("POST", f"/api/recipes/{rid}/scale", {"servings": servings}).status_code,
                             400, servings)
        self.assertEqual(alice.send("POST", f"/api/recipes/{rid}/scale", {"servings": 4, "x": 1}).status_code, 400)
        self.assertEqual(bob.send("POST", f"/api/recipes/{rid}/scale", {"servings": 4}).status_code, 404)

        big = copy.deepcopy(OMELETTE)
        big["servings"] = 1
        big["ingredient_details"][0]["quantity"] = 100000
        big["ingredients"][0] = "100000 eggs"
        rid = self.create(alice, big)["id"]
        resp = alice.send("POST", f"/api/recipes/{rid}/scale", {"servings": 24})
        self.assertIn(resp.status_code, (200, 400))
        if resp.status_code == 200:  # never returns quantities outside the schema bound
            for detail in resp.get_json()["recipe"]["ingredient_details"]:
                self.assertTrue(detail["quantity"] is None or 0 < detail["quantity"] <= 100000)


class GenerationTests(LibraryTestCase):
    def ready(self, cook, name, model="conn-model"):
        self.connect_aws(cook, name, model)
        return cook

    def test_generation_validates_input_strictly_before_any_aws_call(self):
        alice, _ = self.pair()
        self.ready(alice, "alice")
        bad = [{"dish": ""}, {"dish": "x" * 1001}, {"dish": None}, {"ingredients": ["x"] * 65},
               {"ingredients": ["x" * 201]}, {"ingredients": "bread"}, {"pantry": ["x"] * 65},
               {"pantry": [1]}, {"equipment": ["x"] * 33}, {"servings": 0}, {"servings": 25},
               {"servings": "2"}, {"servings": True}, {"time_budget_minutes": 0},
               {"time_budget_minutes": 1441}, {"model": "other-model"}, {"provider": "bedrock"},
               {"user_id": alice.user["id"]}]
        for change in bad:
            self.assertEqual(self.generate(alice, **change).status_code, 400, str(change)[:60])
        missing = {k: v for k, v in GENERATE.items() if k != "dish"}
        self.assertEqual(alice.send("POST", "/api/recipes/generate", missing).status_code, 400)
        self.assertEqual(self.generate(alice, dish="x", pad="y" * (65 * 1024)).status_code, 413)
        self.assertEqual(alice.send("POST", "/api/recipes/generate", GENERATE, token="wrong").status_code, 403)
        self.assertEqual(self.anon().send("POST", "/api/recipes/generate", GENERATE).status_code, 401)
        self.assertEqual(self.converse_calls, [])
        no_pantry = {k: v for k, v in GENERATE.items() if k != "pantry"}  # pantry is optional
        self.assertEqual(alice.send("POST", "/api/recipes/generate", no_pantry).status_code, 200)

    def test_generation_requires_own_unlocked_connection_and_model(self):
        with mock.patch.dict(os.environ, ENV_AWS):
            alice, bob = self.pair()
            self.assertEqual(self.generate(alice).status_code, 409)  # no vault: never the environment
            alice.send("POST", "/api/settings/vault", {"passphrase": VAULT_PASS["alice"]})
            self.assertEqual(self.generate(alice).status_code, 409)  # unlocked but no connection

            self.ready(bob, "bob", model="")
            put = bob.send("PUT", "/api/settings/defaults", {"provider": "ollama", "model": "llava"})
            self.assertEqual(put.status_code, 200)
            self.assertEqual(self.generate(bob).status_code, 409)  # no Bedrock model selected anywhere
            self.assertEqual(bob.send("POST", "/api/settings/lock", {}).status_code, 200)
            put = bob.send("POST", "/api/settings/unlock", {"passphrase": VAULT_PASS["bob"]})
            self.assertEqual(put.status_code, 200)
            bob.send("PUT", "/api/settings/defaults", {"provider": "bedrock", "model": "default-model"})
            bob.send("POST", "/api/settings/lock", {})
            resp = self.generate(bob)
            self.assertEqual(resp.status_code, 409)  # locked
            self.assertIn("error", resp.get_json())
        self.assertEqual((self.converse_calls, self.aws_key_ids()), ([], []))

    def test_generation_uses_callers_credentials_and_model_and_returns_unsaved_draft(self):
        alice, bob = self.pair()
        self.ready(alice, "alice", model="conn-model")
        self.ready(bob, "bob", model="bob-conn-model")
        bob.send("PUT", "/api/settings/defaults", {"provider": "bedrock", "model": "bob-default-model"})
        alice.send("PUT", "/api/settings/defaults", {"provider": "ollama", "model": "llava"})
        before = (self.ids(alice), self.ids(bob))

        self.replies = ["Here is your recipe:\n```json\n" + json.dumps(GENERATED) + "\n```"]
        resp = self.generate(alice)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assert_recipe_fields(data["recipe"], GENERATED)
        self.assertEqual(data["recipe"]["servings"], 2)
        self.assert_warnings(data)  # always asks the cook to review AI output
        resp = self.generate(bob)
        self.assertEqual(resp.status_code, 200, resp.get_json())

        self.assertEqual(self.aws_key_ids(), [KEYS["alice"][0], KEYS["bob"][0]])
        self.assertEqual([c["modelId"] for c in self.converse_calls], ["conn-model", "bob-default-model"])
        prompt = json.dumps(self.converse_calls[0]["messages"])
        for item in ("tomato toast", "bread", "salt", "toaster"):
            self.assertIn(item, prompt)  # explicit inputs, including the submitted pantry
        for message in self.converse_calls[0]["messages"]:
            self.assertTrue(all("image" not in part for part in message["content"]))  # text only
        self.assertEqual((self.ids(alice), self.ids(bob)), before)  # never saved automatically
        draft_id = data["recipe"].get("id")
        if draft_id:
            self.assertEqual(self.fetch(alice, draft_id).status_code, 404)

        saved = self.create(alice, {k: v for k, v in data["recipe"].items()
                                    if k in ("name", "description", "ingredients", "steps", "servings",
                                             "equipment", "ingredient_details")})
        self.assertIn(saved["id"], self.ids(alice))  # explicit save uses the same schema

    def test_malformed_model_output_is_rejected_and_never_saved(self):
        alice, _ = self.pair()
        self.ready(alice, "alice")
        before = self.ids(alice)
        outputs = ["Sorry, I cannot help with that.", "{not json", json.dumps([GENERATED]),
                   json.dumps({**GENERATED, "steps": []}),
                   json.dumps({**GENERATED, "servings": 99}),
                   json.dumps({**GENERATED, "name": "<script>alert(1)</script>"}),
                   json.dumps({**GENERATED, "ingredients": ["x"] * 65}),
                   json.dumps(GENERATED) + "\n" + json.dumps(GENERATED),
                   json.dumps({**GENERATED, "description": "x" * 40000})]
        for text in outputs:
            with self.subTest(text=text[:40]):
                self.clock += 61  # stay under the per-minute limit; each attempt reaches the model
                self.replies = [text]
                resp = self.generate(alice)
                self.assertEqual(resp.status_code, 502, resp.get_json())
                self.assertNotIn("<script", resp.get_data(as_text=True))
        self.assertEqual(len(self.converse_calls), len(outputs))
        self.assertEqual(self.ids(alice), before)

    def test_logout_or_vault_lock_during_generation_discards_draft(self):
        alice, _ = self.pair()
        self.ready(alice, "alice")
        self.on_converse = lambda: self.assertEqual(alice.send("POST", "/api/settings/lock", {}).status_code, 200)
        resp = self.generate(alice)
        self.assertEqual(resp.status_code, 409, resp.get_json())
        self.assertNotIn("recipe", resp.get_json())

        alice.send("POST", "/api/settings/unlock", {"passphrase": VAULT_PASS["alice"]})
        self.on_converse = lambda: self.assertEqual(alice.send("POST", "/api/auth/logout").status_code, 200)
        resp = self.generate(alice)
        self.assertEqual(resp.status_code, 401, resp.get_json())
        self.assertNotIn("recipe", resp.get_json())
        self.assertEqual(len(self.converse_calls), 2)

    def test_generation_concurrency_and_rate_limits(self):
        alice, bob = self.pair()
        carol = self.add_user(alice, "carol", CAROL_PW)
        for cook, name in ((alice, "alice"), (bob, "bob"), (carol, "carol")):
            self.ready(cook, name)
        nested = {}

        def during_bob():  # two generations are in flight: Alice's and Bob's
            resp = self.generate(carol)
            nested["carol"] = (resp.status_code, resp.get_data(as_text=True))

        def during_alice():
            nested["alice_again"] = self.generate(alice).status_code  # same user: one in flight
            self.on_converse = during_bob
            nested["bob"] = self.generate(bob).status_code  # another user may run concurrently

        self.on_converse = during_alice
        self.assertEqual(self.generate(alice).status_code, 200)
        self.assertIn(nested["alice_again"], (409, 429))
        self.assertEqual(nested["bob"], 200)
        status, text = nested["carol"]
        self.assertIn(status, (409, 429))  # shared cap of two
        self.assertFalse(re.search(r"alice|bob", text, re.IGNORECASE))  # busy reveals no one else
        self.assertEqual(len(self.converse_calls), 2)

        # Six accepted generations per user per minute; Bob already used one above.
        statuses = [self.generate(bob).status_code for _ in range(6)]
        self.assertEqual(statuses, [200] * 5 + [429])
        self.assertEqual(len(self.converse_calls), 7)
        self.assertEqual(self.generate(carol).status_code, 200)  # limits are per user
        self.clock += 61
        self.assertEqual(self.generate(bob).status_code, 200)


if __name__ == "__main__":
    unittest.main()
