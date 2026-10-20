import base64
import json
import os
import re
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from unittest import mock

import requests
from jinja2 import DictLoader

import providers
from app import create_app

# Minimal bytes with JPEG SOI/EOI markers; providers are faked so it is never decoded.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
INDEX = '<meta name="csrf-token" content="{{ csrf_token }}">'
START = {"recipe_id": "scrambled-eggs", "provider": "ollama", "model": "llava", "mode": "monitoring"}
# Synthetic fixture account: the first browser sets it up through POST /api/auth/setup, later
# browsers (other tabs, restarts) sign in through POST /api/auth/login.
COOK = {"username": "cook", "password": "synthetic-cook-password-01"}


def data_url(raw):
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


def observation(complete=True, confidence=0.9, alert=None):
    return {"activity": "whisking", "evidence": "yellow eggs in bowl",
            "confidence": confidence, "step_complete": complete, "alert": alert}


class SousTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "sous.sqlite3")
        self.clock = 1_000_000.0
        self.app = self.make_app()
        self.client, self.token = self.browser(self.app)

    def make_app(self, **overrides):
        config = {"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "test-secret",
                  "CLOCK": lambda: self.clock, **getattr(self, "app_config", {}), **overrides}
        app = create_app(config)
        app.jinja_env.loader = DictLoader({"index.html": INDEX})  # stand-in for Luna's template
        return app

    def browser(self, app):
        """A new browser signed in as the fixture cook through the real auth API."""
        client = app.test_client()
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        token = re.search(r'content="([^"]+)"', page.get_data(as_text=True)).group(1)
        setup = client.get("/api/auth").get_json()["setup_required"]
        resp = client.post("/api/auth/setup" if setup else "/api/auth/login", json=COOK,
                           headers={"X-CSRF-Token": token})
        self.assertEqual(resp.status_code, 201 if setup else 200, resp.get_json())
        data = resp.get_json()
        self.user_id = data["user"]["id"]
        return client, data["csrf_token"]  # rotated on sign-in

    def post(self, path, body, client=None, token=None, **headers):
        headers.setdefault("X-CSRF-Token", token or self.token)
        return (client or self.client).post(path, json=body, headers=headers)

    def start(self, **body):
        resp = self.post("/api/sessions", {**START, **body})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()

    def act(self, sid, action, expect=200, **fields):
        resp = self.post(f"/api/sessions/{sid}/actions", {"action": action, **fields})
        self.assertEqual(resp.status_code, expect, resp.get_json())
        return resp.get_json()

    def observe(self, session, image=None):
        return self.post(f"/api/sessions/{session['id']}/observations",
                         {"image": image or data_url(FAKE_JPEG), "revision": session["revision"]})


class SecurityTests(SousTestCase):
    def test_config_lists_recipe_and_providers_without_secrets(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-very-secret", "OPENAI_MODEL": "gpt-x",
                                          "OLLAMA_MODEL": "llava"}):
            resp = self.client.get("/api/config")
            refused = self.post("/api/sessions", {**START, "provider": "openai", "model": "gpt-x"})
        data = resp.get_json()
        self.assertNotIn("sk-very-secret", resp.get_data(as_text=True))
        self.assertEqual(data["sample_interval"], 10)
        self.assertEqual(data["recipes"][0]["id"], "scrambled-eggs")  # first bundled sample
        # V2: shared server cloud keys never configure a provider for an account; Ollama stays shared.
        openai = next(p for p in data["providers"] if p["id"] == "openai")
        self.assertEqual(openai, {"id": "openai", "name": "OpenAI", "configured": False, "default_model": ""})
        ollama = next(p for p in data["providers"] if p["id"] == "ollama")
        self.assertEqual((ollama["configured"], ollama["default_model"]), (True, "llava"))
        self.assertEqual(refused.status_code, 400)

    def test_post_without_or_with_wrong_csrf_token_is_rejected(self):
        resp = self.client.post("/api/sessions", json=START)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("error", resp.get_json())
        self.assertEqual(self.post("/api/sessions", START, token="wrong").status_code, 403)
        self.assertIsNone(self.client.get("/api/sessions/active").get_json()["session"])

    def test_cross_origin_post_is_rejected(self):
        resp = self.post("/api/sessions", START, Origin="http://evil.example")
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(self.client.get("/api/sessions/active").get_json()["session"])
        self.assertEqual(self.post("/api/sessions", START, Origin="http://localhost").status_code, 201)

    def test_non_ascii_csrf_token_is_rejected_cleanly(self):
        resp = self.post("/api/sessions", START, token="tökén")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("error", resp.get_json())
        self.assertIsNone(self.client.get("/api/sessions/active").get_json()["session"])

    def test_untrusted_host_is_rejected(self):
        resp = self.client.get("/api/config", base_url="http://rebind.example")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())
        for host in ("http://127.0.0.1:5000", "http://localhost:8000"):
            # Public auth state: the test client's cookie is scoped to "localhost", not these hosts.
            self.assertEqual(self.client.get("/api/auth", base_url=host).status_code, 200, host)

    def test_secret_key_is_generated_once_and_reused(self):
        key_file = os.path.join(self.tmp.name, "instance", "secret_key")
        first = self.make_app(SECRET_KEY=None, SECRET_KEY_FILE=key_file)
        second = self.make_app(SECRET_KEY=None, SECRET_KEY_FILE=key_file)
        self.assertTrue(first.config["SECRET_KEY"])
        self.assertEqual(first.config["SECRET_KEY"], second.config["SECRET_KEY"])


class SessionTests(SousTestCase):
    def test_create_validation_singleton_and_recovery(self):
        bad_json = self.client.post("/api/sessions", data="{not json", content_type="application/json",
                                    headers={"X-CSRF-Token": self.token})
        self.assertEqual(bad_json.status_code, 400)
        for bad in ({"recipe_id": "toast"}, {"provider": "mystery"}, {"model": ""}, {"mode": "auto"},
                    {"recipe_id": ["scrambled-eggs"]}, {"recipe_id": {"a": 1}},
                    {"provider": ["ollama"]}, {"provider": {"a": 1}}, {"mode": ["monitoring"]}):
            self.assertEqual(self.post("/api/sessions", {**START, **bad}).status_code, 400, bad)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.post("/api/sessions", {**START, "provider": "openai"}).status_code, 400)

        session = self.start()
        self.assertEqual((session["step_index"], session["status"], session["monitoring"]), (0, "active", False))
        self.assertEqual(self.post("/api/sessions", START).status_code, 409)
        self.assertEqual(self.client.get("/api/sessions/active").get_json()["session"]["id"], session["id"])
        self.assertEqual(self.client.get(f"/api/sessions/{session['id']}").get_json()["id"], session["id"])
        self.assertEqual(self.client.get("/api/sessions/9999").status_code, 404)

    def test_navigation_corrections_and_invalid_actions_do_not_mutate(self):
        s = self.start()
        sid, rev = s["id"], s["revision"]
        self.act(sid, "back", expect=409)
        self.act(sid, "fly", expect=400)
        self.act(sid, "correct", expect=400, step_index=6)
        self.act(sid, "correct", expect=400, step_index=True)
        self.act(sid, "correct", expect=400, step_index=1, note="x" * 501)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").get_json()["revision"], rev)

        s = self.act(sid, "next")
        self.assertEqual((s["step_index"], s["revision"]), (1, rev + 1))
        s = self.act(sid, "correct", step_index=1, note="still whisking")
        self.assertEqual((s["step_index"], s["revision"], s["completion_streak"]), (1, rev + 2, 0))
        self.assertEqual(s["events"][0]["kind"], "correction")
        self.assertIn("still whisking", s["events"][0]["message"])

        self.act(sid, "finish", expect=409)
        s = self.act(sid, "correct", step_index=5)
        self.act(sid, "next", expect=409)  # next on the final step never completes
        timer = self.act(sid, "timer_set", seconds=30)
        s = self.act(sid, "finish")
        self.assertEqual((s["status"], s["monitoring"], s["timer_deadline"]), ("completed", False, None))
        self.assertGreater(s["revision"], timer["revision"])
        self.act(sid, "back", expect=409)
        self.act(sid, "timer_set", expect=409, seconds=30)
        self.assertIsNone(self.client.get("/api/sessions/active").get_json()["session"])
        self.start()  # a new session may begin once the previous one is completed

    def test_pause_resume_mode_and_end(self):
        s = self.start()
        sid, rev = s["id"], s["revision"]
        s = self.act(sid, "resume")
        self.assertEqual((s["monitoring"], s["revision"]), (True, rev + 1))
        s = self.act(sid, "mode", mode="assistive")
        self.assertEqual((s["mode"], s["revision"]), ("assistive", rev + 2))
        self.act(sid, "mode", expect=400, mode="loud")
        s = self.act(sid, "pause")
        self.assertEqual((s["monitoring"], s["revision"]), (False, rev + 3))
        self.act(sid, "timer_set", seconds=60)
        s = self.act(sid, "end")
        self.assertEqual((s["status"], s["monitoring"], s["timer_deadline"]), ("ended", False, None))
        self.act(sid, "resume", expect=409)

    def test_timer_is_absolute_and_ack_is_deduplicated(self):
        sid = self.start()["id"]
        for bad in (0, 86401, "60", True, 1.5):
            self.act(sid, "timer_set", expect=400, seconds=bad)
        self.act(sid, "timer_ack", expect=409)  # no timer

        rev = self.client.get(f"/api/sessions/{sid}").get_json()["revision"]
        s = self.act(sid, "timer_set", seconds=60)
        self.assertEqual((s["timer_deadline"], s["timer_alerted"], s["revision"]), (self.clock + 60, False, rev))
        self.clock += 59
        self.act(sid, "timer_ack", expect=409)
        self.clock += 2
        s = self.act(sid, "timer_ack")
        self.assertTrue(s["timer_alerted"])
        self.assertEqual(s["events"][0]["message"], "Timer finished.")
        self.clock += 5
        self.assertEqual(self.act(sid, "timer_ack"), s)  # duplicate ack changes nothing

        s = self.act(sid, "timer_set", seconds=120)
        self.assertEqual((s["timer_deadline"], s["timer_alerted"]), (self.clock + 120, False))
        s = self.act(sid, "timer_cancel")
        self.assertEqual((s["timer_deadline"], s["timer_alerted"]), (None, False))

    def test_state_persists_across_restart_and_monitoring_is_paused(self):
        sid = self.start()["id"]
        self.act(sid, "next")
        self.act(sid, "timer_set", seconds=300)
        before = self.act(sid, "resume")

        restarted = self.make_app()
        client, token = self.browser(restarted)
        after = client.get("/api/sessions/active").get_json()["session"]
        self.assertEqual(after["id"], sid)
        self.assertEqual(after["step_index"], 1)
        self.assertEqual(after["timer_deadline"], before["timer_deadline"])
        self.assertFalse(after["monitoring"])
        self.assertEqual(after["revision"], before["revision"] + 1)
        resp = self.post(f"/api/sessions/{sid}/actions", {"action": "resume"}, client=client, token=token)
        self.assertTrue(resp.get_json()["monitoring"])


class ObservationTests(SousTestCase):
    def setUp(self):
        super().setUp()
        sid = self.start()["id"]
        self.session = self.act(sid, "resume")
        self.outputs = []
        self.on_analyze = None
        fake = types.ModuleType("providers")
        fake.analyze = self.fake_analyze
        patcher = mock.patch.dict(sys.modules, {"providers": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_analyze(self, provider, model, jpeg, context):
        self.assertEqual((provider, model, jpeg), ("ollama", "llava", FAKE_JPEG))
        if self.on_analyze:
            self.on_analyze()
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def current(self):
        return self.client.get(f"/api/sessions/{self.session['id']}").get_json()

    def test_returns_503_when_providers_are_not_installed(self):
        with mock.patch.dict(sys.modules, {"providers": None}):
            resp = self.observe(self.session)
        self.assertEqual(resp.status_code, 503)
        self.act(self.session["id"], "timer_set", seconds=10)  # manual controls still work

    def test_rejects_bad_images_paused_and_stale_requests(self):
        self.assertEqual(self.observe(self.session, image="data:image/png;base64,AAAA").status_code, 400)
        self.assertEqual(self.observe(self.session, image="data:image/jpeg;base64,***").status_code, 400)
        self.assertEqual(self.observe(self.session, image=data_url(b"GIF89a....")).status_code, 400)
        big = data_url(b"\xff\xd8\xff" + b"\x00" * (1024 * 1024))
        self.assertEqual(self.observe(self.session, image=big).status_code, 413)
        huge = self.client.post(f"/api/sessions/{self.session['id']}/observations",
                                data="x" * (2 * 1024 * 1024 + 1), content_type="application/json",
                                headers={"X-CSRF-Token": self.token})
        self.assertEqual(huge.status_code, 413)
        stale = {**self.session, "revision": self.session["revision"] - 1}
        self.assertEqual(self.observe(stale).status_code, 409)
        paused = self.act(self.session["id"], "pause")
        self.assertEqual(self.observe(paused).status_code, 409)
        self.assertEqual(self.outputs, [])  # provider never called

    def test_two_confident_completions_advance_one_step(self):
        self.outputs = [observation(), observation(confidence=0.84), observation(), observation(alert="Pan is smoking")]
        s = self.observe(self.session).get_json()
        self.assertEqual((s["step_index"], s["completion_streak"]), (0, 1))
        self.assertEqual(s["latest_observation"]["evidence"], "yellow eggs in bowl")
        s = self.observe(s).get_json()
        self.assertEqual((s["step_index"], s["completion_streak"]), (0, 0))  # low confidence resets
        s = self.observe(s).get_json()
        rev = s["revision"]
        s = self.observe(s).get_json()
        self.assertEqual((s["step_index"], s["completion_streak"], s["revision"]), (1, 0, rev + 1))
        self.assertEqual([e["kind"] for e in s["events"][:2]], ["progress", "alert"])

    def test_final_step_never_auto_completes(self):
        s = self.act(self.session["id"], "correct", step_index=5)
        self.outputs = [observation(), observation(), observation()]
        for _ in range(3):
            s = self.observe(s).get_json()
        self.assertEqual((s["status"], s["step_index"], s["completion_streak"]), ("active", 5, 2))
        self.assertEqual(sum(e["kind"] == "progress" for e in s["events"]), 1)

    def test_provider_failure_and_invalid_output_do_not_mutate(self):
        self.outputs = [RuntimeError("upstream said key sk-leak is bad"), {**observation(), "confidence": float("nan")},
                        {**observation(), "step_complete": "yes"}, ["not", "a", "dict"]]
        before = self.current()
        for _ in range(4):
            resp = self.observe(self.session)
            self.assertEqual(resp.status_code, 502)
            self.assertNotIn("sk-leak", resp.get_data(as_text=True))
        self.assertEqual(self.current(), before)
        with closing(sqlite3.connect(self.db_path)) as db:
            errors = [r[0] for r in db.execute("SELECT error FROM observations ORDER BY id")]
        self.assertEqual(errors[0], "RuntimeError")
        self.assertTrue(all(e.startswith("invalid_output") for e in errors[1:]))

    def test_late_observation_after_correction_is_discarded(self):
        def correct_meanwhile():
            with closing(sqlite3.connect(self.db_path)) as db:
                db.execute("UPDATE sessions SET revision = revision + 1, step_index = 3")
                db.commit()
        self.on_analyze = correct_meanwhile
        self.outputs = [observation()]
        self.assertEqual(self.observe(self.session).status_code, 409)
        s = self.current()
        self.assertEqual((s["completion_streak"], s["latest_observation"], s["step_index"]), (0, None, 3))
        self.assertEqual(self.recorded(), [(0, self.session["revision"], "stale")])

    def test_late_provider_failure_after_correction_is_stale_not_502(self):
        def correct_meanwhile():
            self.act(self.session["id"], "correct", step_index=3)
        self.on_analyze = correct_meanwhile
        self.outputs = [RuntimeError("upstream said key sk-leak is bad")]
        resp = self.observe(self.session)
        self.assertEqual(resp.status_code, 409)
        self.assertNotIn("sk-leak", resp.get_data(as_text=True))
        s = self.current()
        self.assertEqual((s["step_index"], s["completion_streak"], s["latest_observation"]), (3, 0, None))
        self.assertEqual(self.recorded(), [(0, self.session["revision"], "stale: RuntimeError")])

    def test_late_provider_failure_after_pause_is_stale(self):
        self.on_analyze = lambda: self.act(self.session["id"], "pause")
        self.outputs = [{**observation(), "step_complete": "yes"}]
        self.assertEqual(self.observe(self.session).status_code, 409)
        self.assertFalse(self.current()["monitoring"])
        (step, revision, error), = self.recorded()
        self.assertEqual((step, revision), (0, self.session["revision"]))
        self.assertTrue(error.startswith("stale: invalid_output"))

    def recorded(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            return list(db.execute("SELECT step_index, revision, error FROM observations ORDER BY id"))

    def test_only_one_analysis_runs_per_session(self):
        other, other_token = self.browser(self.app)
        inner = []
        self.on_analyze = lambda: inner.append(self.post(
            f"/api/sessions/{self.session['id']}/observations",
            {"image": data_url(FAKE_JPEG), "revision": self.session["revision"]},
            client=other, token=other_token).status_code)
        self.outputs = [observation()]
        self.assertEqual(self.observe(self.session).status_code, 200)
        self.assertEqual(inner, [409])


class ProviderIntegrationTests(SousTestCase):
    """Observation route through the real providers module with HTTP mocked (Ollama wire shape)."""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"OLLAMA_BASE_URL": ""})
        env.start()
        self.addCleanup(env.stop)
        post = mock.patch("providers.requests.post")
        self.http_post = post.start()
        self.addCleanup(post.stop)
        sid = self.start(model="qwen3-vl:8b")["id"]
        self.session = self.act(sid, "resume")

    def test_ollama_observation_updates_session(self):
        reply = mock.Mock(status_code=200)
        reply.json.return_value = {"message": {"role": "assistant", "content": json.dumps(observation())},
                                   "done": True, "done_reason": "stop"}
        self.http_post.return_value = reply
        s = self.observe(self.session).get_json()
        self.assertEqual((s["completion_streak"], s["latest_observation"]["activity"]), (1, "whisking"))
        args, kwargs = self.http_post.call_args
        self.assertEqual(args[0], "http://localhost:11434/api/chat")
        self.assertEqual(kwargs["json"]["model"], "qwen3-vl:8b")
        self.assertEqual(kwargs["json"]["messages"][1]["images"], [base64.b64encode(FAKE_JPEG).decode()])

    def test_provider_failure_is_502_sanitized_and_recorded(self):
        self.http_post.side_effect = requests.ConnectionError("http://internal?token=sk-leak")
        before = self.client.get(f"/api/sessions/{self.session['id']}").get_json()
        resp = self.observe(self.session)
        self.assertEqual(resp.status_code, 502)
        self.assertNotIn("sk-leak", resp.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/api/sessions/{self.session['id']}").get_json(), before)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT error FROM observations").fetchone()[0],
                             "Could not reach the provider.")


TOAST = {
    "id": "toast", "name": "Buttered Toast", "description": "Toast with butter.",
    "ingredients": ["Bread", "Butter"],
    "steps": [
        {"title": "Toast bread", "instruction": "Toast the bread until golden.",
         "visual_cue": "A golden-brown slice is out of the toaster."},
        {"title": "Butter", "instruction": "Spread butter on the toast.",
         "visual_cue": "Butter is spread across the toast surface."},
    ],
}
TEA = {
    "id": "tea", "name": "Mug of Tea", "description": "One mug of black tea.", "ingredients": ["Tea bag"],
    "steps": [{"title": "Steep", "instruction": "Steep the tea bag for three minutes.",
               "visual_cue": "A mug of dark tea with the bag removed."}],
}


def write_recipe(directory, filename, recipe):
    with open(os.path.join(directory, filename), "w", encoding="utf-8") as fh:
        json.dump(recipe, fh)


class CustomRecipeTests(SousTestCase):
    """The engine follows whichever recipe the session started with, not the bundled sample."""

    def setUp(self):
        recipe_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(recipe_tmp.cleanup)
        self.recipe_dir = recipe_tmp.name
        write_recipe(self.recipe_dir, "10-toast.json", TOAST)
        write_recipe(self.recipe_dir, "20-tea.json", TEA)
        self.app_config = {"RECIPE_DIR": self.recipe_dir}
        super().setUp()
        self.contexts = []
        fake = types.ModuleType("providers")
        fake.analyze = lambda provider, model, jpeg, context: self.contexts.append(context) or observation()
        patcher = mock.patch.dict(sys.modules, {"providers": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_config_lists_current_recipes_in_file_order(self):
        self.assertEqual([r["id"] for r in self.client.get("/api/config").get_json()["recipes"]], ["toast", "tea"])

    def test_manual_and_observed_progression_follow_selected_recipe(self):
        s = self.start(recipe_id="toast")
        sid = s["id"]
        self.assertEqual(s["recipe"], TOAST)
        self.assertNotIn("recipe_snapshot", s)
        self.assertFalse(s["monitoring"])
        self.act(sid, "correct", expect=400, step_index=2)
        self.act(sid, "next")
        self.act(sid, "next", expect=409)
        self.act(sid, "back")

        s = self.act(sid, "resume")
        s = self.observe(self.observe(s).get_json()).get_json()
        self.assertEqual(s["step_index"], 1)
        context = self.contexts[0]
        self.assertEqual(context["step"], TOAST["steps"][0])
        prompt = providers.build_prompt(context)
        self.assertIn(TOAST["steps"][0]["visual_cue"], prompt)
        self.assertIn("step 1 of 2", prompt)
        self.assertNotIn("egg", (prompt + providers.SYSTEM_PROMPT).lower())

        for _ in range(3):  # the final step never completes itself
            s = self.observe(s).get_json()
        self.assertEqual((s["status"], s["step_index"]), ("active", 1))
        self.assertTrue(self.contexts[-1]["is_final_step"])
        self.assertEqual(self.act(sid, "finish")["status"], "completed")

    def test_session_keeps_snapshot_when_definition_changes_or_is_removed(self):
        sid = self.start(recipe_id="toast")["id"]
        self.act(sid, "next")
        edited = {**TOAST, "steps": TOAST["steps"] + [{"title": "Plate", "instruction": "Plate it.",
                                                        "visual_cue": "Toast on a plate."}]}
        write_recipe(self.recipe_dir, "10-toast.json", edited)

        client, token = self.browser(self.make_app())
        session = client.get("/api/sessions/active").get_json()["session"]
        self.assertEqual((session["recipe"], session["step_index"]), (TOAST, 1))
        config = client.get("/api/config").get_json()["recipes"]
        self.assertEqual(len(config[0]["steps"]), 3)  # new sessions would get the edited version
        action = lambda body: self.post(f"/api/sessions/{sid}/actions", body, client=client, token=token)
        self.assertEqual(action({"action": "next"}).status_code, 409)  # bound from the snapshot

        os.remove(os.path.join(self.recipe_dir, "10-toast.json"))
        client, token = self.browser(self.make_app())
        session = client.get("/api/sessions/active").get_json()["session"]
        self.assertEqual(session["recipe"], TOAST)
        self.assertEqual([r["id"] for r in client.get("/api/config").get_json()["recipes"]], ["tea"])
        self.assertEqual(action({"action": "correct", "step_index": 0}).status_code, 200)
        resumed = action({"action": "resume"}).get_json()
        resp = self.post(f"/api/sessions/{sid}/observations",
                         {"image": data_url(FAKE_JPEG), "revision": resumed["revision"]}, client=client, token=token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.contexts[-1]["step"], TOAST["steps"][0])


LEGACY_SCHEMA = """
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id TEXT NOT NULL, provider TEXT NOT NULL,
    model TEXT NOT NULL, mode TEXT NOT NULL, step_index INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
    monitoring INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
    updated_at REAL NOT NULL, step_started_at REAL NOT NULL, timer_deadline REAL,
    timer_alerted INTEGER NOT NULL DEFAULT 0, completion_streak INTEGER NOT NULL DEFAULT 0,
    latest_observation TEXT
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES sessions(id),
    kind TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL
);
"""


class LegacyDatabaseTests(SousTestCase):
    """V1 databases upgrade in place without losing state. Their rows stay on disk, unowned:
    no account can see or act on them (docs/v2.md)."""

    def legacy_db(self, recipe_id):
        path = os.path.join(self.tmp.name, "legacy.sqlite3")
        with closing(sqlite3.connect(path)) as db:
            db.executescript(LEGACY_SCHEMA)
            db.execute(
                "INSERT INTO sessions (recipe_id, provider, model, mode, step_index, status, monitoring, revision,"
                " created_at, updated_at, step_started_at, timer_deadline) VALUES"
                " (?, 'ollama', 'llava', 'monitoring', 1, 'active', 1, 3, ?, ?, ?, ?)",
                (recipe_id, self.clock, self.clock, self.clock, self.clock + 300),
            )
            db.execute("INSERT INTO events (session_id, kind, message, created_at) VALUES (1, 'note', 'Legacy event', ?)",
                       (self.clock,))
            db.commit()
        return path

    def legacy_row(self, path):
        with closing(sqlite3.connect(path)) as db:
            db.row_factory = sqlite3.Row
            row = dict(db.execute("SELECT * FROM sessions WHERE id = 1").fetchone())
            row["events"] = [r[0] for r in db.execute("SELECT message FROM events WHERE session_id = 1")]
        return row

    def test_legacy_database_upgrades_in_place_and_stays_unclaimed(self):
        path = self.legacy_db("scrambled-eggs")
        client, token = self.browser(self.make_app(DATABASE=path))  # the first account on the upgraded file
        row = self.legacy_row(path)
        self.assertEqual(json.loads(row["recipe_snapshot"])["id"], "scrambled-eggs")  # snapshot backfilled
        self.assertEqual((row["step_index"], row["timer_deadline"], row["status"]), (1, self.clock + 300, "active"))
        self.assertEqual((row["monitoring"], row["revision"]), (0, 4))  # paused on restart, as before
        self.assertIsNone(row["user_id"])  # never silently assigned to the new account
        self.assertIn("Legacy event", row["events"])

        self.assertIsNone(client.get("/api/sessions/active").get_json()["session"])
        self.assertEqual(client.get("/api/sessions/1").status_code, 404)
        self.assertEqual(self.post("/api/sessions/1/actions", {"action": "next"},
                                   client=client, token=token).status_code, 404)
        own = self.post("/api/sessions", START, client=client, token=token)
        self.assertEqual(own.status_code, 201)  # the unowned active row does not block the account
        self.assertNotEqual(own.get_json()["id"], 1)

        self.make_app(DATABASE=path)  # second startup is a no-op upgrade
        row = self.legacy_row(path)
        self.assertEqual((row["step_index"], row["revision"], row["status"], row["user_id"]), (1, 4, "active", None))

    def test_session_without_definition_can_still_end(self):
        path = self.legacy_db("retired-recipe")
        self.make_app(DATABASE=path)
        self.assertIsNone(self.legacy_row(path)["recipe_snapshot"])  # nothing substituted on upgrade

        # The same rule for an owned session whose snapshot is unavailable.
        sid = self.start()["id"]
        self.act(sid, "next")
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE sessions SET recipe_snapshot = NULL WHERE id = ?", (sid,))
            db.commit()
        action = lambda body: self.post(f"/api/sessions/{sid}/actions", body)
        s = self.client.get("/api/sessions/active").get_json()["session"]
        self.assertIsNone(s["recipe"])
        for body in ({"action": "next"}, {"action": "resume"}, {"action": "correct", "step_index": 0}):
            resp = action(body)
            self.assertEqual(resp.status_code, 409, body)
            self.assertIn("no longer available", resp.get_json()["error"])
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").get_json()["step_index"], 1)
        self.assertEqual(action({"action": "timer_cancel"}).status_code, 200)
        self.assertEqual(action({"action": "end"}).get_json()["status"], "ended")


if __name__ == "__main__":
    unittest.main()
