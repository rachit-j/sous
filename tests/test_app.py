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

from app import create_app

# Minimal bytes with JPEG SOI/EOI markers; providers are faked so it is never decoded.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
INDEX = '<meta name="csrf-token" content="{{ csrf_token }}">'
START = {"recipe_id": "scrambled-eggs", "provider": "ollama", "model": "llava", "mode": "monitoring"}


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
                  "CLOCK": lambda: self.clock, **overrides}
        app = create_app(config)
        app.jinja_env.loader = DictLoader({"index.html": INDEX})  # stand-in for Luna's template
        return app

    def browser(self, app):
        client = app.test_client()
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        return client, re.search(r'content="([^"]+)"', page.get_data(as_text=True)).group(1)

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
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-very-secret", "OPENAI_MODEL": "gpt-x"}):
            resp = self.client.get("/api/config")
        data = resp.get_json()
        self.assertNotIn("sk-very-secret", resp.get_data(as_text=True))
        self.assertEqual(data["sample_interval"], 10)
        self.assertEqual(len(data["recipes"][0]["steps"]), 6)
        openai = next(p for p in data["providers"] if p["id"] == "openai")
        self.assertEqual(openai, {"id": "openai", "name": "OpenAI", "configured": True, "default_model": "gpt-x"})

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
            self.assertEqual(self.client.get("/api/config", base_url=host).status_code, 200, host)

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
                db.execute("UPDATE sessions SET revision = revision + 1")
                db.commit()
        self.on_analyze = correct_meanwhile
        self.outputs = [observation()]
        self.assertEqual(self.observe(self.session).status_code, 409)
        s = self.current()
        self.assertEqual((s["completion_streak"], s["latest_observation"]), (0, None))

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


if __name__ == "__main__":
    unittest.main()
