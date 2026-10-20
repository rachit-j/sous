"""V2.1 identity and per-user isolation (docs/v2.md), written before implementation.

Endpoint contract exercised here (fields may be added; names are stable):
  GET  /api/auth -> {"user": null | {"id", "username", "is_admin"}, "setup_required", "csrf_token"}
  POST /api/auth/setup {username, password} -> 201 auth shape, first administrator logged in; 409 afterwards
  POST /api/auth/login {username, password} -> 200 auth shape; 401 generic failure; 429 throttled
  POST /api/auth/logout -> 200 auth shape with user null
  POST /api/auth/password {current_password, new_password} -> 200; 403 wrong current password
  GET  /api/users -> {"users": [...]}; POST /api/users {username, password} -> 201 {"user": {...}}
  PUT  /api/users/<id>/password {password} -> 200
Account administration is admin-only (403). Unauthenticated protected endpoints return 401;
another user's session returns the same 403/404 as a missing one. Usernames are ASCII 3-32
[A-Za-z0-9_-], stored lowercase; passwords are 12-1024 characters.

Synthetic credentials only. providers, boto3 and the voice upstream are faked; no AWS or network.
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from contextlib import closing
from unittest import mock

from jinja2 import DictLoader

import accounts
import app as sous_app
from app import create_app
from settings import Vault
from tests.test_app import FAKE_JPEG, INDEX, LEGACY_SCHEMA, data_url, observation

ALICE_PW = "alice-login-password-0001"
BOB_PW = "bob-login-password-0002"
NEW_PW = "replacement-password-0003"
ALICE_VAULT = "alice vault passphrase A"
BOB_VAULT = "bob vault passphrase B"
ALICE_KEY, ALICE_SECRET = "AKIAALICESENTINEL000", "alice-sentinel-secret-0123456789"
BOB_KEY, BOB_SECRET = "AKIABOBSENTINEL00000", "bob-sentinel-secret-0123456789"
ENV_AWS = {"AWS_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "AKIAENVIRONMENT00000",
           "AWS_SECRET_ACCESS_KEY": "env-secret-should-not-be-used", "AWS_PROFILE": "",
           "BEDROCK_MODEL_ID": "env-model"}
DISCOVERY = "/api/settings/bedrock/models"
HASH_PREFIX = r"(scrypt|pbkdf2):"
BAD_USERNAMES = ("ab", "a" * 33, "has space", "ünïcode", "semi;colon", "", None, 123, ["alice"])
BAD_PASSWORDS = ("x" * 11, "x" * 1025, "", None, 123456789012345, ["x" * 20], "\ud800" * 12)


class Cook:
    """One browser: a test client and its current CSRF token from GET /api/auth."""

    def __init__(self, case, client):
        self.case, self.client = case, client
        self.token = self.user = None
        self.refresh()

    def refresh(self):
        resp = self.client.get("/api/auth")
        self.case.assertEqual(resp.status_code, 200, "GET /api/auth must exist and be public")
        data = resp.get_json()
        self.token, self.user = data["csrf_token"], data["user"]
        return data

    def get(self, path, **headers):
        return self.client.get(path, headers=headers)

    def send(self, method, path, body=None, token=None, **headers):
        headers.setdefault("X-CSRF-Token", token or self.token)
        return self.client.open(path, method=method, json=body, headers=headers)


class AccountTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "sous.sqlite3")
        self.legacy_vault = os.path.join(self.tmp.name, "settings.vault")  # V1 location next to DATABASE
        self.clock = 1_000_000.0
        self.analyses = []  # (provider, model, explicit Bedrock connection or None)
        self.discoveries = []
        self.voice_opens = []
        self.on_analyze = None
        fake = types.ModuleType("providers")
        fake.ProviderError = type("ProviderError", (Exception,), {"public_message": "failed"})
        fake.analyze = self.fake_analyze
        fake.list_bedrock_models = lambda connection: self.discoveries.append(connection) or {"models": []}
        patcher = mock.patch.dict(sys.modules, {"providers": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()

    def make_app(self):
        app = create_app({"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "test-secret",
                          "CLOCK": lambda: self.clock})
        app.jinja_env.loader = DictLoader({"index.html": INDEX})

        async def deny_voice(connection):
            self.voice_opens.append(connection)
            raise AssertionError("voice upstream must not open in these tests")
        app.extensions["sous_voice"]["open_stream"] = deny_voice
        return app

    def fake_analyze(self, provider, model, jpeg, context, **extra):
        self.analyses.append((provider, model, extra.get("bedrock")))
        if self.on_analyze:
            self.on_analyze()
        return observation()

    # --- accounts

    def anon(self):
        return Cook(self, self.app.test_client())

    def setup_admin(self, username="alice", password=ALICE_PW):
        cook = self.anon()
        resp = cook.send("POST", "/api/auth/setup", {"username": username, "password": password})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        cook.refresh()
        return cook

    def login(self, username, password, expect=200):
        cook = self.anon()
        resp = cook.send("POST", "/api/auth/login", {"username": username, "password": password})
        self.assertEqual(resp.status_code, expect, resp.get_json())
        cook.refresh()
        return cook

    def create_user(self, admin, username, password):
        resp = admin.send("POST", "/api/users", {"username": username, "password": password})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()["user"]

    def pair(self):
        alice = self.setup_admin()
        self.create_user(alice, "bob", BOB_PW)
        return alice, self.login("bob", BOB_PW)

    def cookie_jar(self, cook):
        return list(cook.client._cookies.values())  # werkzeug test Cookie objects

    def replay(self, jar):
        client = self.app.test_client()
        for c in jar:
            client.set_cookie(c.decoded_key, c.decoded_value, domain=c.domain, path=c.path)
        return Cook(self, client)

    # --- cooking and settings

    def start_body(self, cook, **body):
        recipe_id = cook.get("/api/config").get_json()["recipes"][0]["id"]
        return {"recipe_id": recipe_id, "provider": "ollama", "model": "llava", "mode": "monitoring", **body}

    def start(self, cook, expect=201, **body):
        resp = cook.send("POST", "/api/sessions", self.start_body(cook, **body))
        self.assertEqual(resp.status_code, expect, resp.get_json())
        return resp.get_json()

    def act(self, cook, sid, action, expect=200, **fields):
        resp = cook.send("POST", f"/api/sessions/{sid}/actions", {"action": action, **fields})
        self.assertEqual(resp.status_code, expect, resp.get_json())
        return resp.get_json()

    def observe(self, cook, session):
        return cook.send("POST", f"/api/sessions/{session['id']}/observations",
                         {"image": data_url(FAKE_JPEG), "revision": session["revision"]})

    def settings(self, cook):
        resp = cook.get("/api/settings")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()

    def put_prefs(self, cook, **prefs):
        resp = cook.send("PUT", "/api/settings/preferences", prefs)
        self.assertEqual(resp.status_code, 200, resp.get_json())

    def init_vault(self, cook, passphrase):
        resp = cook.send("POST", "/api/settings/vault", {"passphrase": passphrase})
        self.assertEqual(resp.status_code, 201, resp.get_json())

    def save_bedrock(self, cook, key_id, secret):
        resp = cook.send("PUT", "/api/settings/bedrock", {"region": "us-west-2", "model": "",
                                                          "access_key_id": key_id, "secret_access_key": secret})
        self.assertEqual(resp.status_code, 200, resp.get_json())

    def bedrock_entry(self, cook):
        return next(p for p in cook.get("/api/config").get_json()["providers"] if p["id"] == "bedrock")

    def active_id(self, cook):
        session = cook.get("/api/sessions/active").get_json()["session"]
        return session and session["id"]

    def db_dump(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            return "\n".join(db.iterdump())

    def assert_denied(self, resp, msg=None):
        self.assertIn(resp.status_code, (403, 404), msg)
        self.assertIn("error", resp.get_json() or {}, msg)

    def assert_protected(self, cook, sid):
        """Every non-auth API rejects a browser without a login, before any side effect."""
        for path in ("/api/config", "/api/settings", "/api/sessions/active", f"/api/sessions/{sid}", "/api/users"):
            resp = cook.get(path)
            self.assertEqual(resp.status_code, 401, path)
            self.assertIn("error", resp.get_json(), path)
        writes = (("POST", "/api/sessions", {"recipe_id": "any-recipe", "provider": "ollama", "model": "m",
                                              "mode": "monitoring"}),
                  ("POST", f"/api/sessions/{sid}/actions", {"action": "end"}),
                  ("POST", f"/api/sessions/{sid}/observations", {"image": data_url(FAKE_JPEG), "revision": 1}),
                  ("POST", "/api/settings/vault", {"passphrase": ALICE_VAULT}),
                  ("PUT", "/api/settings/preferences", {"sample_interval": 5}),
                  ("GET", DISCOVERY, None),
                  ("POST", "/api/auth/password", {"current_password": ALICE_PW, "new_password": NEW_PW}),
                  ("POST", "/api/users", {"username": "mallory", "password": BOB_PW}))
        for method, path, body in writes:
            resp = cook.send(method, path, body)  # valid CSRF token, but no login
            self.assertEqual(resp.status_code, 401, (method, path))
        self.assertFalse(os.path.exists(self.legacy_vault))
        self.assertEqual((self.analyses, self.discoveries), ([], []))


class SetupAndLoginTests(AccountTestCase):
    def test_bootstrap_metadata_is_public_and_everything_else_requires_login(self):
        anon = self.anon()
        data = anon.refresh()
        self.assertEqual((data["user"], data["setup_required"]), (None, True))
        self.assertIsInstance(data["csrf_token"], str)
        self.assertGreaterEqual(len(data["csrf_token"]), 16)
        self.assertEqual(anon.get("/").status_code, 200)  # the app shell stays public
        self.assert_protected(anon, 1)

        alice = self.setup_admin()
        sid = self.start(alice)["id"]
        anon = self.anon()
        self.assertFalse(anon.refresh()["setup_required"])
        self.assert_protected(anon, sid)
        # The voice socket is authorized before any upgrade or upstream stream.
        self.assertEqual(anon.get(f"/api/sessions/{sid}/voice", Origin="http://localhost",
                                  Upgrade="websocket", Connection="Upgrade").status_code, 401)
        self.assertEqual(self.voice_opens, [])
        self.assertEqual(self.act(alice, sid, "next")["step_index"], 1)  # owner unaffected

    def test_first_admin_setup_validates_hashes_and_runs_once(self):
        anon = self.anon()
        body = {"username": "Alice", "password": ALICE_PW}
        self.assertEqual(anon.send("POST", "/api/auth/setup", body, token="wrong").status_code, 403)
        self.assertEqual(anon.send("POST", "/api/auth/setup", body, Origin="http://evil.example").status_code, 403)
        for bad in BAD_USERNAMES:
            resp = anon.send("POST", "/api/auth/setup", {**body, "username": bad})
            self.assertEqual(resp.status_code, 400, bad)
        for bad in BAD_PASSWORDS:
            resp = anon.send("POST", "/api/auth/setup", {**body, "password": bad})
            self.assertEqual(resp.status_code, 400, bad)
        self.assertTrue(anon.refresh()["setup_required"])

        resp = anon.send("POST", "/api/auth/setup", body)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        user = resp.get_json()["user"]
        self.assertEqual((user["username"], user["is_admin"]), ("alice", True))
        self.assertNotIn(ALICE_PW, resp.get_data(as_text=True))
        cookies = resp.headers.getlist("Set-Cookie")
        self.assertTrue(cookies)
        for cookie in cookies:
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite", cookie)
            self.assertNotIn(ALICE_PW, cookie)
        data = anon.refresh()
        self.assertEqual((data["user"]["id"], data["setup_required"]), (user["id"], False))
        self.settings(anon)  # setup logs the administrator in

        other = self.anon()  # once only, from any browser
        resp = other.send("POST", "/api/auth/setup", {"username": "mallory", "password": BOB_PW})
        self.assertEqual(resp.status_code, 409)
        self.assertIsNone(other.refresh()["user"])
        self.login("mallory", BOB_PW, expect=401)

        dump = self.db_dump()
        self.assertNotIn(ALICE_PW, dump)
        self.assertRegex(dump, HASH_PREFIX)  # Werkzeug password hash

    def test_login_is_case_insensitive_generic_throttled_and_rotates_csrf(self):
        self.setup_admin()
        anon = self.anon()
        before = anon.token
        resp = anon.send("POST", "/api/auth/login", {"username": "ALICE", "password": ALICE_PW})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["user"]["username"], "alice")
        self.assertNotIn(ALICE_PW, resp.get_data(as_text=True))
        self.assertNotEqual(anon.refresh()["csrf_token"], before)
        stale = anon.send("PUT", "/api/settings/preferences", {"sample_interval": 5}, token=before)
        self.assertEqual(stale.status_code, 403)  # the pre-login token is rotated out
        self.put_prefs(anon, sample_interval=5)
        self.assertEqual(anon.send("POST", "/api/auth/logout").status_code, 200)

        anon = self.anon()
        wrong = anon.send("POST", "/api/auth/login", {"username": "alice", "password": "wrong-password-000"})
        unknown = anon.send("POST", "/api/auth/login", {"username": "nobody", "password": ALICE_PW})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual((wrong.status_code, wrong.get_json()), (unknown.status_code, unknown.get_json()))
        self.assertIsNone(anon.refresh()["user"])

        statuses = [anon.send("POST", "/api/auth/login",
                              {"username": "alice", "password": "wrong-password-000"}).status_code
                    for _ in range(10)]
        self.assertLessEqual(set(statuses), {401, 429})
        self.assertIn(429, statuses)
        throttled = anon.send("POST", "/api/auth/login", {"username": "alice", "password": ALICE_PW})
        self.assertEqual(throttled.status_code, 429)  # even the right password waits
        self.assertIsNone(anon.refresh()["user"])
        self.clock += 3600
        self.login("alice", ALICE_PW)


class AccountAdministrationTests(AccountTestCase):
    def test_admin_creates_and_lists_accounts_and_others_are_denied(self):
        alice = self.setup_admin()
        for bad in ({"username": "ab", "password": BOB_PW}, {"username": "bob", "password": "short"},
                    {"username": "bob"}, {"username": "bob bob", "password": BOB_PW}):
            self.assertEqual(alice.send("POST", "/api/users", bad).status_code, 400, bad)
        resp = alice.send("POST", "/api/users", {"username": "bob", "password": BOB_PW}, token="wrong")
        self.assertEqual(resp.status_code, 403)

        bob_user = self.create_user(alice, "Bob", BOB_PW)
        self.assertEqual((bob_user["username"], bob_user["is_admin"]), ("bob", False))
        for taken in ("BOB", "Alice"):
            resp = alice.send("POST", "/api/users", {"username": taken, "password": NEW_PW})
            self.assertEqual(resp.status_code, 409, taken)
        listing = alice.get("/api/users")
        self.assertEqual(listing.status_code, 200)
        users = listing.get_json()["users"]
        self.assertEqual(sorted(u["username"] for u in users), ["alice", "bob"])
        text = listing.get_data(as_text=True)
        for secret in (ALICE_PW, BOB_PW):
            self.assertNotIn(secret, text)
        self.assertNotRegex(text, HASH_PREFIX)
        for user in users:
            self.assertFalse([k for k in user if "password" in k or "hash" in k], user)

        bob = self.login("bob", BOB_PW)
        self.assertFalse(bob.user["is_admin"])
        self.assertEqual(bob.get("/api/users").status_code, 403)
        resp = bob.send("POST", "/api/users", {"username": "carol", "password": NEW_PW, "is_admin": True})
        self.assertEqual(resp.status_code, 403)
        resp = bob.send("PUT", f"/api/users/{alice.user['id']}/password", {"password": NEW_PW})
        self.assertEqual(resp.status_code, 403)
        self.login("alice", ALICE_PW)
        self.login("carol", NEW_PW, expect=401)

    def test_password_changes_revoke_sessions_and_lock_vault(self):
        alice, bob = self.pair()
        bob_id = bob.user["id"]
        resp = bob.send("POST", "/api/auth/password", {"current_password": "not-bobs-password", "new_password": NEW_PW})
        self.assertEqual(resp.status_code, 403)
        resp = bob.send("POST", "/api/auth/password", {"current_password": BOB_PW, "new_password": "short"})
        self.assertEqual(resp.status_code, 400)
        resp = bob.send("POST", "/api/auth/password", {"current_password": BOB_PW, "new_password": NEW_PW})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertNotIn(NEW_PW, resp.get_data(as_text=True))
        self.login("bob", BOB_PW, expect=401)
        bob2 = self.login("bob", NEW_PW)
        self.init_vault(bob2, BOB_VAULT)  # unlocked

        reset = f"/api/users/{bob_id}/password"
        self.assertEqual(alice.send("PUT", reset, {"password": BOB_PW}, token="wrong").status_code, 403)
        self.assertEqual(alice.send("PUT", "/api/users/999999/password", {"password": BOB_PW}).status_code, 404)
        self.assertEqual(alice.send("PUT", reset, {"password": "short"}).status_code, 400)
        resp = alice.send("PUT", reset, {"password": BOB_PW})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertNotIn(BOB_PW, resp.get_data(as_text=True))
        for stale in (bob, bob2):  # every existing Bob login is revoked
            self.assertEqual(stale.get("/api/settings").status_code, 401)
        self.login("bob", NEW_PW, expect=401)
        bob3 = self.login("bob", BOB_PW)
        self.assertEqual(self.settings(bob3)["vault"], {"initialized": True, "locked": True})
        # The vault passphrase is separate from the login password.
        self.assertEqual(bob3.send("POST", "/api/settings/unlock", {"passphrase": BOB_VAULT}).status_code, 200)
        self.settings(alice)  # the administrator's own login is unaffected


class IsolationTests(AccountTestCase):
    def test_each_user_has_own_active_session_preferences_and_vault(self):
        alice, bob = self.pair()
        a, b = self.start(alice), self.start(bob)  # one active session per user, not per instance
        self.assertNotEqual(a["id"], b["id"])
        self.start(alice, expect=409)
        self.start(bob, expect=409)
        self.assertEqual((self.active_id(alice), self.active_id(bob)), (a["id"], b["id"]))

        self.put_prefs(alice, sample_interval=5, voice_id="matthew")
        self.put_prefs(bob, sample_interval=12)
        self.assertEqual((self.settings(alice)["sample_interval"], self.settings(bob)["sample_interval"]), (5, 12))
        self.assertEqual((self.settings(alice)["voice"]["voice_id"], self.settings(bob)["voice"]["voice_id"]),
                         ("matthew", "tiffany"))
        self.assertEqual(bob.get("/api/config").get_json()["sample_interval"], 12)

        self.init_vault(alice, ALICE_VAULT)
        self.save_bedrock(alice, ALICE_KEY, ALICE_SECRET)
        self.assertEqual(self.settings(bob)["vault"], {"initialized": False, "locked": True})
        self.assertFalse(self.bedrock_entry(bob)["configured"])
        self.init_vault(bob, BOB_VAULT)  # not 409: Bob's vault is separate
        self.assertFalse(self.settings(bob)["bedrock"]["has_credentials"])
        self.assertTrue(self.settings(alice)["bedrock"]["has_credentials"])

        for cook, foreign in ((alice, BOB_VAULT), (bob, ALICE_VAULT)):
            self.assertEqual(cook.send("POST", "/api/settings/lock", {}).status_code, 200)
            resp = cook.send("POST", "/api/settings/unlock", {"passphrase": foreign})
            self.assertEqual(resp.status_code, 403)
        self.assertEqual(alice.send("POST", "/api/settings/unlock", {"passphrase": ALICE_VAULT}).status_code, 200)
        self.assertTrue(self.settings(alice)["bedrock"]["has_credentials"])
        self.assertTrue(self.settings(bob)["vault"]["locked"])
        self.assertFalse(os.path.exists(self.legacy_vault))  # per-user vaults never use the V1 file

        self.act(alice, a["id"], "end")
        self.assertEqual(self.active_id(bob), b["id"])

    def test_cross_user_session_ids_are_rejected(self):
        alice, bob = self.pair()
        session = self.act(alice, self.start(alice)["id"], "resume")
        sid = session["id"]
        before = alice.get(f"/api/sessions/{sid}").get_json()

        fetched = bob.get(f"/api/sessions/{sid}")
        self.assert_denied(fetched)
        missing = bob.get("/api/sessions/999999")
        self.assertEqual((fetched.status_code, fetched.get_json()), (missing.status_code, missing.get_json()))
        for body in ({"action": "next"}, {"action": "pause"}, {"action": "timer_set", "seconds": 30},
                     {"action": "timer_ack"}, {"action": "end"}):
            self.assert_denied(bob.send("POST", f"/api/sessions/{sid}/actions", body), body)
        self.assert_denied(self.observe(bob, session))
        self.assert_denied(bob.get(f"/api/sessions/{sid}/voice", Origin="http://localhost",
                                   Upgrade="websocket", Connection="Upgrade"))
        self.assertIsNone(self.active_id(bob))
        self.assertEqual((self.analyses, self.voice_opens), ([], []))
        self.assertEqual(alice.get(f"/api/sessions/{sid}").get_json(), before)

        self.assertEqual(self.observe(alice, session).status_code, 200)  # owner still can
        self.assertEqual(len(self.analyses), 1)

    def test_forged_owner_fields_cannot_claim_data(self):
        alice, bob = self.pair()
        alice_id = alice.user["id"]
        forged = {"user_id": alice_id, "owner_id": alice_id, "owner": "alice", "username": "alice"}
        resp = bob.send("POST", "/api/sessions", self.start_body(bob, **forged))
        self.assertIn(resp.status_code, (201, 400))
        self.assertIsNone(self.active_id(alice))
        if resp.status_code == 201:
            self.assertEqual(self.active_id(bob), resp.get_json()["id"])

        resp = bob.send("PUT", "/api/settings/preferences", {"sample_interval": 3, "user_id": alice_id})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.settings(alice)["sample_interval"], 10)

        resp = bob.send("POST", "/api/settings/vault", {"passphrase": BOB_VAULT, "user_id": alice_id})
        self.assertIn(resp.status_code, (201, 400))
        self.assertFalse(self.settings(alice)["vault"]["initialized"])

        spoofed = bob.client.get("/api/auth", headers={"X-User-Id": str(alice_id), "X-Username": "alice"})
        self.assertEqual(spoofed.get_json()["user"]["username"], "bob")
        self.assertEqual(bob.get("/api/users", **{"X-User-Id": str(alice_id)}).status_code, 403)


class LogoutTests(AccountTestCase):
    def test_logout_revokes_replayed_cookie_and_locks_only_caller_vault(self):
        alice, bob = self.pair()
        self.init_vault(alice, ALICE_VAULT)
        self.init_vault(bob, BOB_VAULT)
        self.assertEqual(alice.send("POST", "/api/auth/logout", token="wrong").status_code, 403)
        self.settings(alice)  # still logged in

        jar = self.cookie_jar(alice)
        old_token = alice.token
        resp = alice.send("POST", "/api/auth/logout")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["user"])
        self.assertNotEqual(alice.refresh()["csrf_token"], old_token)
        self.assertEqual(alice.get("/api/settings").status_code, 401)

        replayed = self.replay(jar)  # the server, not the browser, ends the login
        self.assertIsNone(replayed.user)
        for path in ("/api/settings", "/api/config", "/api/sessions/active"):
            self.assertEqual(replayed.get(path).status_code, 401, path)

        self.assertEqual(self.settings(bob)["vault"], {"initialized": True, "locked": False})
        again = self.login("alice", ALICE_PW)
        self.assertEqual(self.settings(again)["vault"], {"initialized": True, "locked": True})

    def test_logout_during_analysis_discards_late_result(self):
        alice = self.setup_admin()
        session = self.act(alice, self.start(alice)["id"], "resume")
        self.on_analyze = lambda: alice.send("POST", "/api/auth/logout")
        resp = self.observe(alice, session)
        self.assertIn(resp.status_code, (401, 409))
        self.assertEqual(len(self.analyses), 1)

        alice = self.login("alice", ALICE_PW)
        current = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertEqual((current["latest_observation"], current["completion_streak"]), (None, 0))


class ProviderIsolationTests(AccountTestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, ENV_AWS)
        env.start()
        self.addCleanup(env.stop)
        patcher = mock.patch("boto3.Session")
        self.session_cls = patcher.start()
        self.addCleanup(patcher.stop)

    def assert_no_environment_bedrock(self, cook):
        bedrock = self.bedrock_entry(cook)
        self.assertFalse(bedrock["configured"])
        self.assertNotEqual(bedrock.get("source"), "environment")
        self.assertNotEqual(self.settings(cook)["bedrock"]["source"], "environment")
        resp = cook.send("POST", "/api/sessions", self.start_body(cook, provider="bedrock", model="vision-model"))
        self.assertIn(resp.status_code, (400, 409))

    def test_account_users_never_use_ambient_aws_credentials(self):
        alice, bob = self.pair()
        for cook in (alice, bob):
            self.assert_no_environment_bedrock(cook)
            self.assertEqual(cook.get(DISCOVERY, **{"X-CSRF-Token": cook.token}).status_code, 409)

        self.init_vault(alice, ALICE_VAULT)
        self.save_bedrock(alice, ALICE_KEY, ALICE_SECRET)
        self.init_vault(bob, BOB_VAULT)
        self.save_bedrock(bob, BOB_KEY, BOB_SECRET)
        for cook in (alice, bob):
            session = self.act(cook, self.start(cook, provider="bedrock", model="vision-model")["id"], "resume")
            self.assertEqual(self.observe(cook, session).status_code, 200)
        self.assertEqual([(p, c.access_key_id, c.secret_access_key) for p, _, c in self.analyses],
                         [("bedrock", ALICE_KEY, ALICE_SECRET), ("bedrock", BOB_KEY, BOB_SECRET)])

        # Removing a saved connection never falls back to the server environment.
        self.act(bob, self.active_id(bob), "end")
        self.assertEqual(bob.send("DELETE", "/api/settings/bedrock").status_code, 200)
        self.assert_no_environment_bedrock(bob)
        self.session_cls.assert_not_called()
        self.assertEqual(self.discoveries, [])


class PersistenceTests(AccountTestCase):
    def test_restart_locks_user_vaults_and_keeps_preferences(self):
        alice, bob = self.pair()
        self.put_prefs(alice, sample_interval=7, voice_id="matthew")
        self.put_prefs(bob, sample_interval=3)
        self.init_vault(alice, ALICE_VAULT)
        self.save_bedrock(alice, ALICE_KEY, ALICE_SECRET)
        sid = self.start(alice)["id"]

        self.app = self.make_app()  # restart
        self.assertFalse(self.anon().refresh()["setup_required"])
        alice, bob = self.login("alice", ALICE_PW), self.login("bob", BOB_PW)
        a, b = self.settings(alice), self.settings(bob)
        self.assertEqual((a["vault"], a["sample_interval"], a["voice"]["voice_id"], a["bedrock"]["region"]),
                         ({"initialized": True, "locked": True}, 7, "matthew", None))
        self.assertEqual((b["vault"], b["sample_interval"], b["voice"]["voice_id"]),
                         ({"initialized": False, "locked": True}, 3, "tiffany"))
        self.assertEqual((self.active_id(alice), self.active_id(bob)), (sid, None))

        self.assertEqual(alice.send("POST", "/api/settings/unlock", {"passphrase": ALICE_VAULT}).status_code, 200)
        self.assertTrue(self.settings(alice)["bedrock"]["has_credentials"])
        self.assertFalse(self.settings(bob)["vault"]["initialized"])

    def test_legacy_rows_settings_and_vault_are_preserved_but_unclaimed(self):
        self.db_path = os.path.join(self.tmp.name, "legacy.sqlite3")
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript(LEGACY_SCHEMA + "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
            db.execute("INSERT INTO sessions (recipe_id, provider, model, mode, step_index, status, monitoring,"
                       " revision, created_at, updated_at, step_started_at) VALUES"
                       " ('legacy-recipe', 'ollama', 'llava', 'monitoring', 1, 'active', 0, 3, ?, ?, ?)",
                       (self.clock, self.clock, self.clock))
            db.execute("INSERT INTO events (session_id, kind, message, created_at) VALUES (1, 'note', 'Legacy event', ?)",
                       (self.clock,))
            for key, value in (("sample_interval", 7), ("voice_id", "matthew"),
                               ("defaults", {"provider": "openai", "model": "legacy-model"})):
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            db.commit()
        legacy_bytes = json.dumps({"version": 1, "kdf": {"name": "scrypt", "n": 32768, "r": 8, "p": 1},
                                   "salt": "c3ludGhldGljLXNhbHQxMg==", "token": "synthetic-legacy-ciphertext"}).encode()
        with open(self.legacy_vault, "wb") as fh:
            fh.write(legacy_bytes)

        self.app = self.make_app()
        self.assertTrue(self.anon().refresh()["setup_required"])  # legacy data is not an account
        alice = self.setup_admin()
        self.assertIsNone(self.active_id(alice))
        self.assert_denied(alice.get("/api/sessions/1"))
        self.assert_denied(alice.send("POST", "/api/sessions/1/actions", {"action": "end"}))
        self.assert_denied(alice.send("POST", "/api/sessions/1/observations",
                                      {"image": data_url(FAKE_JPEG), "revision": 3}))
        data = self.settings(alice)
        self.assertEqual((data["sample_interval"], data["voice"]["voice_id"], data["defaults"]),
                         (10, "tiffany", {"provider": None, "model": None}))
        self.assertEqual(data["vault"], {"initialized": False, "locked": True})
        resp = alice.send("POST", "/api/settings/unlock", {"passphrase": ALICE_VAULT})
        self.assertEqual(resp.status_code, 409)  # Alice has no vault; the legacy file is not hers

        self.start(alice)  # the unclaimed legacy active row does not block her
        self.init_vault(alice, ALICE_VAULT)
        with open(self.legacy_vault, "rb") as fh:
            self.assertEqual(fh.read(), legacy_bytes)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT status, step_index FROM sessions WHERE id = 1").fetchone(),
                             ("active", 1))
        dump = self.db_dump()
        for kept in ("Legacy event", "legacy-model", "matthew"):
            self.assertIn(kept, dump)
        self.assertEqual(self.analyses, [])


class AccountRaceTests(AccountTestCase):
    """Pin credential revocation across slow password checks, hashing and provider calls."""

    def test_login_verified_before_admin_reset_cannot_mint_a_new_login(self):
        admin = self.setup_admin()
        stale_login = self.anon()
        original_check = accounts.check_password
        reset = {"done": False}

        def check_then_reset(password_hash, password):
            valid = original_check(password_hash, password)
            if valid and password == ALICE_PW and not reset["done"]:
                reset["done"] = True
                response = admin.send("PUT", f"/api/users/{admin.user['id']}/password",
                                      {"password": NEW_PW})
                self.assertEqual(response.status_code, 200, response.get_json())
            return valid

        with mock.patch("accounts.check_password", side_effect=check_then_reset):
            response = stale_login.send("POST", "/api/auth/login",
                                        {"username": "alice", "password": ALICE_PW})

        self.assertEqual(response.status_code, 401, response.get_json())
        stale_login.refresh()
        self.assertIsNone(stale_login.user)
        self.login("alice", NEW_PW)
        self.assertEqual(self.login("alice", ALICE_PW, expect=401).user, None)

    def test_password_change_verified_before_admin_reset_cannot_overwrite_reset(self):
        alice = self.setup_admin()
        self.assertEqual(alice.send("POST", "/api/users",
                                    {"username": "bob", "password": BOB_PW, "is_admin": True}).status_code,
                         201)
        bob = self.login("bob", BOB_PW)
        self_change = "self-change-password-0004"
        original_check = accounts.check_password
        reset = {"done": False}

        def check_then_reset(password_hash, password):
            valid = original_check(password_hash, password)
            if valid and password == ALICE_PW and not reset["done"]:
                reset["done"] = True
                response = bob.send("PUT", f"/api/users/{alice.user['id']}/password",
                                    {"password": NEW_PW})
                self.assertEqual(response.status_code, 200, response.get_json())
            return valid

        with mock.patch("accounts.check_password", side_effect=check_then_reset):
            response = alice.send("POST", "/api/auth/password",
                                  {"current_password": ALICE_PW, "new_password": self_change})

        self.assertIn(response.status_code, (401, 403), response.get_json())
        self.login("alice", NEW_PW)
        self.assertEqual(self.login("alice", self_change, expect=401).user, None)

    def test_vault_lock_during_provider_call_discards_observation(self):
        alice = self.setup_admin()
        self.init_vault(alice, ALICE_VAULT)
        self.save_bedrock(alice, ALICE_KEY, ALICE_SECRET)
        session = self.act(alice, self.start(alice, provider="bedrock", model="vision-model")["id"],
                           "resume")
        self.on_analyze = lambda: self.assertEqual(alice.send("POST", "/api/settings/lock", {}).status_code, 200)

        response = self.observe(alice, session)

        self.assertEqual(response.status_code, 409, response.get_json())
        current = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertIsNone(current["latest_observation"])
        self.assertEqual(current["completion_streak"], 0)
        with closing(sqlite3.connect(self.db_path)) as db:  # invalidated credentials: no audit row either
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)

    def test_vault_lock_waits_for_a_result_already_being_committed(self):
        alice = self.setup_admin()
        self.init_vault(alice, ALICE_VAULT)
        self.save_bedrock(alice, ALICE_KEY, ALICE_SECRET)
        session = self.act(alice, self.start(alice, provider="bedrock", model="vision-model")["id"], "resume")
        other = self.login("alice", ALICE_PW)  # a second browser of the same account
        vault = sous_app.user_vault(self.app, alice.user["id"])
        in_commit, release, lock_called = threading.Event(), threading.Event(), threading.Event()
        original_apply, original_lock = sous_app.apply_observation, Vault.lock
        results = {}

        def held_apply(fresh, obs, at):  # runs inside the guarded critical section, before COMMIT
            in_commit.set()
            release.wait(5)
            return original_apply(fresh, obs, at)

        def signalled_lock(vault_self):
            lock_called.set()
            original_lock(vault_self)

        def run(name, call):
            try:
                results[name] = call()
            except BaseException as exc:  # surfaced by the assertions below
                results[name] = exc

        observing = threading.Thread(target=run, args=("observe", lambda: self.observe(alice, session)))
        locking = threading.Thread(target=run, args=("lock", lambda: other.send("POST", "/api/settings/lock", {})))
        with mock.patch("app.apply_observation", held_apply), mock.patch.object(Vault, "lock", signalled_lock):
            observing.start()
            try:
                self.assertTrue(in_commit.wait(5))
                locking.start()
                self.assertTrue(lock_called.wait(5))
                locking.join(0.2)
                # The explicit lock cannot complete while an accepted result is uncommitted.
                self.assertTrue(locking.is_alive())
                self.assertFalse(vault.locked)
            finally:
                release.set()
                observing.join(5)
                if locking.ident is not None:
                    locking.join(5)
        self.assertFalse(observing.is_alive() or locking.is_alive())
        self.assertEqual(results["observe"].status_code, 200, results["observe"])
        self.assertEqual(results["lock"].status_code, 200, results["lock"])
        current = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertIsNotNone(current["latest_observation"])  # committed before the lock completed
        self.assertTrue(vault.locked)

    def test_admin_signed_out_while_hashing_cannot_create_account(self):
        admin = self.setup_admin()
        original_hash = accounts.generate_password_hash

        def hash_then_sign_out(password, *args, **kwargs):
            value = original_hash(password, *args, **kwargs)
            if password == BOB_PW:
                self.assertEqual(admin.send("POST", "/api/auth/logout").status_code, 200)
            return value

        with mock.patch("accounts.generate_password_hash", side_effect=hash_then_sign_out):
            response = admin.send("POST", "/api/users", {"username": "bob", "password": BOB_PW})

        self.assertEqual(response.status_code, 401, response.get_json())
        self.login("bob", BOB_PW, expect=401)


if __name__ == "__main__":
    unittest.main()
