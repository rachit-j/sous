import asyncio
import base64
import json
import os
import queue
import sqlite3
import threading
import time
from contextlib import closing
from unittest import mock

from simple_websocket import ConnectionClosed

import accounts
import voice
from app import get_db, serve_voice
from settings import BedrockConnection
from tests.test_settings import KEY_ID, PASSPHRASE, SECRET, TOKEN, SettingsTestCase

CLOSE = object()
PCM = b"\x01\x00" * 160  # 10 ms of synthetic PCM16 silence-ish samples; never real audio
SPECULATIVE = json.dumps({"generationStage": "SPECULATIVE"})
FINAL = json.dumps({"generationStage": "FINAL"})


def start_message(token):
    return json.dumps({"type": "start", "csrf_token": token})


def run(fn):
    """Script step: run a side effect once, then continue."""
    def step(_ws):
        fn()
        return True
    return step


def after(seconds):
    began = []

    def step(_ws):
        began.append(time.monotonic()) if not began else None
        return time.monotonic() - began[0] >= seconds
    return step


def event(name, **body):
    return {"event": {name: body}}


class FakeWS:
    """Scripted browser socket: str/bytes messages, wait-condition callables, or CLOSE."""

    def __init__(self, *script):
        self.script = list(script)
        self.sent = []
        self.deadline = time.monotonic() + 8

    def receive(self, timeout=None):
        if time.monotonic() > self.deadline:
            raise ConnectionClosed()  # guard against a hung test
        if not self.script:
            time.sleep(0.005)
            return None
        item = self.script[0]
        if item is CLOSE:
            raise ConnectionClosed()
        if callable(item):
            if item(self):
                self.script.pop(0)
            else:
                time.sleep(0.005)
            return None
        self.script.pop(0)
        return item

    def send(self, data):
        self.sent.append(json.loads(data))

    def types(self):
        return [m["type"] for m in self.sent]


class FakeStream:
    """Stands in for SonicStream on the bridge's event loop; no network."""

    def __init__(self):
        self.sent = []
        self.incoming = queue.Queue()
        self.closed = threading.Event()
        self.ready_started = threading.Event()
        self.block_ready = False
        self.block = False

    async def send(self, item):
        if self.block:
            await asyncio.sleep(3600)
        self.sent.append(item)

    async def receive(self):
        while True:
            try:
                item = self.incoming.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)
                continue
            if isinstance(item, BaseException):
                raise item
            return item

    async def wait_ready(self):
        self.ready_started.set()
        if self.block_ready:
            await asyncio.sleep(3600)

    async def close(self):
        self.closed.set()

    def push(self, *items):
        for item in items:
            self.incoming.put(item)

    def kinds(self):
        return [next(iter(e["event"])) for e in self.sent]

    def payload(self, kind):
        return [e["event"][kind] for e in self.sent if kind in e["event"]]

    def texts(self, role):
        names = {s["contentName"] for s in self.payload("contentStart") if s.get("role") == role}
        return [t["content"] for t in self.payload("textInput") if t["contentName"] in names]


class VoiceTestCase(SettingsTestCase):
    def setUp(self):
        super().setUp()
        async def deny_unmocked_voice(_connection):
            raise AssertionError("Voice test must install a synthetic upstream stream.")
        self.app.extensions["sous_voice"]["open_stream"] = deny_unmocked_voice
        patcher = mock.patch.multiple(voice, START_TIMEOUT=0.2, CONNECT_TIMEOUT=1.0, POLL_INTERVAL=0.05,
                                      IDLE_TIMEOUT=5.0, MAX_STREAM_SECONDS=10.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, stream=None, error=None, hang=False):
        calls = []

        async def open_stream(connection):
            calls.append(connection)
            if hang:
                await asyncio.sleep(3600)
            if error is not None:
                raise error
            return stream

        self.app.extensions["sous_voice"]["open_stream"] = open_stream
        return calls

    def ready_app(self, **connection):
        self.init_vault()
        self.save_bedrock(session_token=TOKEN, **connection)
        self.session = self.start()

    def serve(self, ws, session_id=None, expect_thread_exit=True, client=None):
        """Run the voice controller with a browser's real signed session cookie (login resolved
        server-side by app.serve_voice), as the route does after its pre-upgrade checks."""
        sid = session_id or self.session["id"]
        cookie = (client or self.client).get_cookie("session")
        with self.app.test_request_context(f"/api/sessions/{sid}/voice",
                                           headers={"Cookie": f"session={cookie.value}"}):
            serve_voice(ws, sid)
        if expect_thread_exit:
            self.assertEqual([t for t in threading.enumerate() if t.name == "sous-voice"], [])
        return ws.sent

    def converse(self, *steps, stream=None):
        stream = stream or FakeStream()
        calls = self.install(stream)
        ws = FakeWS(start_message(self.token), lambda w: "ready" in w.types(), *steps)
        sent = self.serve(ws)
        return sent, stream, calls

    def sql(self, statement, params=()):
        with closing(sqlite3.connect(self.db_path, isolation_level=None)) as db:
            db.execute(statement, params)


class VoiceSettingsTests(VoiceTestCase):
    def test_metadata_reflects_vault_without_calling_aws(self):
        calls = self.install()
        meta = self.settings()["voice"]
        self.assertEqual(meta, {"model": voice.MODEL_ID, "voice_id": "tiffany", "available": False,
                                "reason": voice.NO_CONNECTION})
        self.init_vault()
        self.assertEqual(self.settings()["voice"]["reason"], voice.NO_CONNECTION)
        self.save_bedrock()  # us-west-2
        self.assertEqual(self.settings()["voice"], {"model": voice.MODEL_ID, "voice_id": "tiffany",
                                                    "available": True})
        meta = self.save_bedrock(region="eu-central-1", access_key_id="", secret_access_key="")["voice"]
        self.assertEqual((meta["available"], meta["reason"], meta["supported_regions"]),
                         (False, voice.UNSUPPORTED_REGION, list(voice.SUPPORTED_REGIONS)))
        meta = self.post("/api/settings/lock", {}).get_json()["voice"]
        self.assertEqual((meta["model"], meta["voice_id"], meta["available"], meta["reason"]),
                         (voice.MODEL_ID, "tiffany", False, voice.LOCKED))
        self.assertEqual(calls, [])

    def test_voice_preference_validation_and_persistence(self):
        def put(body, **kwargs):
            return self.send("PUT", "/api/settings/preferences", body, **kwargs)

        for bad in ("Tiffany", "", None, 1, ["matthew"], "joanna"):
            self.assertEqual(put({"voice_id": bad}).status_code, 400, bad)
        self.assertEqual(put({"voice_id": "matthew", "extra": 1}).status_code, 400)
        self.assertEqual(put({}).status_code, 400)
        self.assertEqual(put({"voice_id": "matthew", "sample_interval": 99}).status_code, 400)
        self.assertEqual(self.settings()["voice"]["voice_id"], "tiffany")  # nothing partially saved
        self.assertEqual(put({"voice_id": "matthew"}, token="wrong").status_code, 403)

        data = put({"voice_id": "matthew"}).get_json()  # no vault needed
        self.assertEqual((data["voice"]["voice_id"], data["sample_interval"]), ("matthew", 10))
        data = put({"sample_interval": 4}).get_json()  # interval-only contract unchanged
        self.assertEqual((data["voice"]["voice_id"], data["sample_interval"]), ("matthew", 4))
        data = put({"voice_id": "tiffany", "sample_interval": 6}).get_json()
        self.assertEqual((data["voice"]["voice_id"], data["sample_interval"]), ("tiffany", 6))
        put({"voice_id": "matthew"})
        self.restart()
        self.assertEqual(self.settings()["voice"]["voice_id"], "matthew")


class VoiceAuthTests(VoiceTestCase):
    def setUp(self):
        super().setUp()
        self.ready_app()
        self.calls = self.install(FakeStream())

    def assert_rejected(self, sent, message):
        self.assertEqual(sent, [{"type": "error", "message": message}])
        self.assertEqual(self.calls, [])

    def test_cross_origin_or_missing_origin_rejected_before_upgrade(self):
        path = f"/api/sessions/{self.session['id']}/voice"
        upgrade = {"Upgrade": "websocket", "Connection": "Upgrade"}
        for origin in ("http://evil.example", None):
            headers = dict(upgrade, **({"Origin": origin} if origin else {}))
            resp = self.client.get(path, headers=headers)
            self.assertEqual(resp.status_code, 403, origin)
            self.assertEqual(resp.get_json(), {"error": "Cross-origin request rejected."})
        self.assertEqual(self.calls, [])

    def test_bad_start_messages_never_open_aws(self):
        cases = {
            "wrong token": start_message("wrong"),
            "missing token": json.dumps({"type": "start"}),
            "extra field": json.dumps({"type": "start", "csrf_token": self.token, "voice": "x"}),
            "wrong type": json.dumps({"type": "stop", "csrf_token": self.token}),
            "token type": json.dumps({"type": "start", "csrf_token": 5}),
            "surrogate": json.dumps({"type": "start", "csrf_token": "\ud800"}),
            "not json": "start",
            "array": "[]",
            "binary": PCM,
            "oversized": json.dumps({"type": "start", "csrf_token": "x" * 5000}),
        }
        for name, message in cases.items():
            with self.subTest(name):
                self.assert_rejected(self.serve(FakeWS(message)), voice.AUTH_FAILED)
        with self.subTest("timeout"):
            self.assert_rejected(self.serve(FakeWS()), voice.AUTH_FAILED)
        with self.subTest("disconnect"):
            self.assertEqual(self.serve(FakeWS(CLOSE)), [])
            self.assertEqual(self.calls, [])

    def test_not_ready_states_never_open_aws(self):
        start = start_message(self.token)
        self.assert_rejected(self.serve(FakeWS(start), session_id=999), voice.NOT_ACTIVE)
        self.post("/api/settings/lock", {})
        self.assert_rejected(self.serve(FakeWS(start)), voice.LOCKED)
        self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        self.save_bedrock(region="eu-central-1", access_key_id="", secret_access_key="")
        self.assert_rejected(self.serve(FakeWS(start)), voice.UNSUPPORTED_REGION)
        self.send("DELETE", "/api/settings/bedrock")
        self.assert_rejected(self.serve(FakeWS(start)), voice.NO_CONNECTION)
        self.save_bedrock()
        self.act(self.session["id"], "end")
        self.assert_rejected(self.serve(FakeWS(start)), voice.NOT_ACTIVE)

    def test_duplicate_busy_and_rate_limited_starts_never_open_aws(self):
        state = self.app.extensions["sous_voice"]
        start = start_message(self.token)
        with state["active"]:
            self.assert_rejected(self.serve(FakeWS(start)), voice.ALREADY_ACTIVE)
        for _ in range(voice.MAX_PENDING):
            state["pending"].acquire()
        try:
            self.assert_rejected(self.serve(FakeWS(start)), voice.BUSY)
        finally:
            for _ in range(voice.MAX_PENDING):
                state["pending"].release()
        state["starts"].extend([time.monotonic()] * voice.START_LIMIT)
        self.assert_rejected(self.serve(FakeWS(start)), voice.TOO_MANY)

    def test_signed_out_or_other_account_never_opens_aws(self):
        resp = self.post("/api/users", {"username": "other-cook", "password": "other-cook-password-01"})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        other = self.app.test_client()
        token = other.get("/api/auth").get_json()["csrf_token"]
        resp = other.post("/api/auth/login", json={"username": "other-cook", "password": "other-cook-password-01"},
                          headers={"X-CSRF-Token": token})
        self.assertEqual(resp.status_code, 200)
        other_token = resp.get_json()["csrf_token"]
        # Another account's socket for this session: same answer as a missing session.
        self.assert_rejected(self.serve(FakeWS(start_message(other_token)), client=other), voice.NOT_ACTIVE)
        upgrade = {"Upgrade": "websocket", "Connection": "Upgrade", "Origin": "http://localhost"}
        resp = other.get(f"/api/sessions/{self.session['id']}/voice", headers=upgrade)
        self.assertEqual(resp.status_code, 404)  # refused before the upgrade

        signed_out = self.app.test_client()
        signed_out.get("/api/auth")
        resp = signed_out.get(f"/api/sessions/{self.session['id']}/voice", headers=upgrade)
        self.assertEqual(resp.status_code, 401)
        self.assert_rejected(self.serve(FakeWS(start_message(self.token)), client=signed_out), voice.AUTH_FAILED)

        jar = self.client.get_cookie("session")
        self.assertEqual(self.post("/api/auth/logout", {}).status_code, 200)
        replay = self.app.test_client()
        replay.set_cookie("session", jar.value)
        self.assert_rejected(self.serve(FakeWS(start_message(self.token)), client=replay), voice.AUTH_FAILED)


class VoiceStreamTests(VoiceTestCase):
    def setUp(self):
        super().setUp()
        self.ready_app()

    def test_conversation_relay_interruption_and_clean_stop(self):
        self.send("PUT", "/api/settings/preferences", {"voice_id": "matthew"})
        stream = FakeStream()
        audio_out = base64.b64encode(b"\x00\x01" * 100).decode()
        upstream = [
            event("contentStart", role="ASSISTANT", type="TEXT", additionalModelFields=SPECULATIVE),
            event("textOutput", role="ASSISTANT", content="Crack the eggs\x07 now."),
            event("textOutput", role="ASSISTANT", content="Crack the eggs\x07 now."),  # duplicate
            event("contentStart", role="ASSISTANT", type="TEXT", additionalModelFields=FINAL),
            event("textOutput", role="ASSISTANT", content="Crack the eggs\x07 now."),
            event("contentStart", role="USER", type="TEXT"),
            event("textOutput", role="USER", content=f"{voice.UPDATE_PREFIX} app context echo"),
            event("textOutput", role="USER", content="What comes next?"),
            event("audioOutput", content=audio_out),
            event("audioOutput", content="not base64!"),
            event("audioOutput", content=base64.b64encode(b"\x00\x01\x02").decode()),  # odd length
            event("usageEvent", totalTokens=5),
            {"unexpected": True},
            event("textOutput", role="ASSISTANT", content='{ "interrupted" : true }'),
            event("contentEnd", type="AUDIO", stopReason="INTERRUPTED"),  # same barge-in
        ]
        sent, stream, calls = self.converse(
            PCM,
            lambda w: "audioInput" in stream.kinds(),
            run(lambda: stream.push(*upstream)),
            lambda w: "interrupted" in w.types(),
            json.dumps({"type": "stop"}),
            stream=stream,
        )
        self.assertEqual(calls, [BedrockConnection("us-west-2", KEY_ID, SECRET, TOKEN)])
        self.assertEqual(sent[0], {"type": "ready", "model": "amazon.nova-2-sonic-v1:0", "voice": "matthew",
                                   "input_sample_rate": 16000, "output_sample_rate": 24000})
        self.assertEqual(sent[1:], [
            {"type": "transcript", "role": "assistant", "text": "Crack the eggs  now.", "final": False},
            {"type": "transcript", "role": "assistant", "text": "Crack the eggs  now.", "final": True},
            {"type": "transcript", "role": "user", "text": "What comes next?", "final": True},
            {"type": "audio", "audio": audio_out},
            {"type": "interrupted"},
            {"type": "stopped", "reason": "client_stop"},
        ])

        kinds = stream.kinds()
        self.assertEqual(kinds[:9], ["sessionStart", "promptStart", "contentStart", "textInput", "contentEnd",
                                     "contentStart", "contentStart", "textInput", "contentEnd"])
        self.assertEqual(kinds[-3:], ["contentEnd", "promptEnd", "sessionEnd"])
        prompt = stream.payload("promptStart")[0]
        self.assertEqual(prompt["audioOutputConfiguration"]["voiceId"], "matthew")
        self.assertEqual(prompt["audioOutputConfiguration"]["sampleRateHertz"], 24000)
        self.assertEqual([tool["toolSpec"]["name"] for tool in prompt["toolConfiguration"]["tools"]],
                         [voice.TOOL_NAME])  # only the bounded read-only context lookup
        starts = stream.payload("contentStart")
        self.assertEqual([(s["type"], s["role"], s["interactive"]) for s in starts[:3]],
                         [("TEXT", "SYSTEM", False), ("AUDIO", "USER", True), ("TEXT", "USER", True)])
        self.assertEqual(starts[1]["audioInputConfiguration"]["sampleRateHertz"], 16000)
        recipe = self.session["recipe"]
        system = stream.texts("SYSTEM")[0]
        self.assertIn(recipe["name"], system)
        self.assertIn("cannot see the kitchen", system)
        self.assertIn(recipe["steps"][0]["title"], stream.texts("USER")[0])
        audio = stream.payload("audioInput")[0]
        self.assertEqual((audio["contentName"], base64.b64decode(audio["content"])),
                         (starts[1]["contentName"], PCM))
        self.assertTrue(stream.closed.is_set())
        self.assert_no_secrets(json.dumps(sent) + "\n".join(self.logs.lines))

    def test_invalid_client_messages_stop_with_error(self):
        cases = {"odd": (b"\x01\x02\x03", voice.BAD_AUDIO), "empty": (b"", voice.BAD_AUDIO),
                 "large": (b"\x00" * (voice.MAX_AUDIO_FRAME + 2), voice.BAD_AUDIO),
                 "unknown": (json.dumps({"type": "pause"}), voice.BAD_MESSAGE),
                 "text": ("hello", voice.BAD_MESSAGE),
                 "long": (json.dumps({"type": "stop", "pad": "x" * 3000}), voice.BAD_MESSAGE)}
        for name, (message, error) in cases.items():
            with self.subTest(name):
                sent, stream, _ = self.converse(message)
                self.assertEqual(sent[-1], {"type": "error", "message": error})
                self.assertTrue(stream.closed.is_set())
                self.assertEqual(stream.kinds()[-1], "sessionEnd")

    def test_input_queue_limit_stops_instead_of_buffering(self):
        stream = FakeStream()
        with mock.patch.object(voice, "INPUT_QUEUE_MAX", 2):
            sent, _, _ = self.converse(run(lambda: setattr(stream, "block", True)), *[PCM] * 6, stream=stream)
        self.assertEqual(sent[-1], {"type": "error", "message": voice.SLOW})
        self.assertTrue(stream.closed.is_set())

    def test_disconnect_closes_upstream_without_terminal_message(self):
        sent, stream, _ = self.converse(PCM, CLOSE)
        self.assertEqual([m["type"] for m in sent], ["ready"])
        self.assertTrue(stream.closed.is_set())
        self.assertEqual(stream.kinds()[-1], "sessionEnd")

    def test_lock_settings_change_and_session_end_stop_stream(self):
        sent, stream, _ = self.converse(run(lambda: self.vault().lock()))
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "locked"})
        self.assertTrue(stream.closed.is_set())

        self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        sent, _, _ = self.converse(run(lambda: self.save_bedrock(access_key_id="", secret_access_key="",
                                                                 clear_session_token=True)))
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "settings_changed"})

        sent, stream, _ = self.converse(run(lambda: self.act(self.session["id"], "end")))
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "session_ended"})
        self.assertTrue(stream.closed.is_set())

    def test_lock_then_unlock_with_same_credentials_stops_stream(self):
        # Both happen within one browser-message turn, before the next poll: the connection is
        # equal afterwards, but the lease that opened the stream was revoked.
        def relock():
            self.vault().lock()
            self.vault().unlock(PASSPHRASE)
        sent, stream, calls = self.converse(run(relock))
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "settings_changed"})
        self.assertTrue(stream.closed.is_set())
        self.assertEqual(len(calls), 1)  # no automatic restart
        self.assertEqual(self.vault().connection(), calls[0])  # same keys, still a new lease

    def test_expired_login_or_logout_stops_stream(self):
        # The login expires server-side while the vault stays unlocked: the poll stops the stream.
        sent, stream, _ = self.converse(run(lambda: setattr(self, "clock", self.clock + accounts.SESSION_TTL + 1)))
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "signed_out"})
        self.assertTrue(stream.closed.is_set())

        self.client, self.token = self.browser(self.app)
        self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        sent, stream, _ = self.converse(run(lambda: self.post("/api/auth/logout", {})))
        self.assertIn(sent[-1], ({"type": "stopped", "reason": "signed_out"}, {"type": "stopped", "reason": "locked"}))
        self.assertTrue(stream.closed.is_set())

    def test_stream_deadline_idle_and_upstream_end(self):
        with mock.patch.object(voice, "MAX_STREAM_SECONDS", 0.3):
            sent, stream, _ = self.converse()
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "time_limit"})
        self.assertTrue(stream.closed.is_set())
        with mock.patch.object(voice, "IDLE_TIMEOUT", 0.2):
            sent, _, _ = self.converse()
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "idle"})
        stream = FakeStream()
        sent, _, _ = self.converse(run(lambda: stream.push(None)), stream=stream)
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "upstream_closed"})
        self.assertNotIn("sessionEnd", stream.kinds())  # no events after AWS ended the stream
        self.assertTrue(stream.closed.is_set())

    def test_connect_timeout_and_sanitized_upstream_failures(self):
        from aws_sdk_bedrock_runtime.models import AccessDeniedException

        self.install(hang=True)
        with mock.patch.object(voice, "CONNECT_TIMEOUT", 0.2):
            sent = self.serve(FakeWS(start_message(self.token)))
        self.assertEqual(sent, [{"type": "error", "message": voice.CONNECT_FAILED}])

        self.install(error=AccessDeniedException(message=f"denied for {KEY_ID} {SECRET}"))
        sent = self.serve(FakeWS(start_message(self.token)))
        self.assertEqual(sent, [{"type": "error", "message": voice.UPSTREAM_ERRORS[0][1]}])

        stream = FakeStream()
        error = voice.VoiceUpstreamError("InvokeModelWithBidirectionalStreamOutputThrottlingException")
        sent, _, _ = self.converse(run(lambda: stream.push(error)), stream=stream)
        self.assertEqual(sent[-1]["message"], "AWS is throttling voice requests. Try again in a moment.")
        self.assertTrue(stream.closed.is_set())
        self.assert_no_secrets(json.dumps(sent) + "\n".join(self.logs.lines))

    def test_context_updates_on_relevant_changes_only(self):
        sid = self.session["id"]
        recipe = self.session["recipe"]
        stream = FakeStream()

        def updates():
            return [t for t in stream.texts("USER") if t.startswith(voice.UPDATE_PREFIX)][1:]  # skip greeting

        counts = {}
        observation = json.dumps({"activity": "whisking", "evidence": "bowl", "confidence": 0.5,
                                  "step_complete": False, "alert": None, "created_at": self.clock})
        sent, _, _ = self.converse(
            run(lambda: self.act(sid, "next")),
            lambda w: any(recipe["steps"][1]["title"] in t for t in updates()),
            run(lambda: counts.setdefault("after_step", len(updates()))),
            run(lambda: self.sql("UPDATE sessions SET latest_observation = ? WHERE id = ?", (observation, sid))),
            after(0.3),  # several polls: a plain observation is not narrated
            run(lambda: counts.setdefault("after_observation", len(updates()))),
            run(lambda: self.sql("INSERT INTO events (session_id, kind, message, created_at)"
                                 " VALUES (?, 'alert', 'Pan is smoking', 0)", (sid,))),
            lambda w: any("Pan is smoking" in t for t in updates()),
            run(lambda: self.act(sid, "timer_set", seconds=90)),
            lambda w: any("A timer was set for 1 min 30 s" in t for t in updates()),
            run(lambda: setattr(self, "clock", self.clock + 91)),
            lambda w: any("The timer just finished" in t for t in updates()),
            json.dumps({"type": "stop"}),
            stream=stream,
        )
        self.assertEqual(sent[-1], {"type": "stopped", "reason": "client_stop"})
        self.assertEqual(counts["after_step"], 1)
        self.assertEqual(counts["after_observation"], 1)
        alert = next(t for t in updates() if "Pan is smoking" in t)
        self.assertIn("not live", alert)
        self.assertIn("you cannot see the kitchen now", alert)
        self.assertEqual(len(updates()), 4)

    def test_timer_expiry_survives_browser_ack_and_is_not_repeated_on_start(self):
        sid = self.session["id"]
        self.act(sid, "timer_set", seconds=1)
        with self.app.test_request_context("/"):
            db = get_db()
            row = db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            recipe = json.loads(row["recipe_snapshot"])
            watcher = voice.SessionWatcher(db, sid, recipe, row, self.clock)
            self.clock += 2
            db.execute("UPDATE sessions SET timer_alerted = 1 WHERE id = ?", (sid,))
            update, end = watcher.poll(self.clock)
            self.assertIsNone(end)
            self.assertIn("timer just finished", update)
            self.assertIsNone(watcher.poll(self.clock)[0])
            row = db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            late_watcher = voice.SessionWatcher(db, sid, recipe, row, self.clock)
            self.assertIsNone(late_watcher.poll(self.clock)[0])

    def test_tool_reads_fresh_context_after_greeting_without_writing_session(self):
        sid = self.session["id"]
        stream = FakeStream()
        observation = json.dumps({"activity": "whisking", "evidence": "rapid movement in the bowl",
                                  "confidence": 0.6, "step_complete": False, "alert": None,
                                  "created_at": self.clock})

        def update_context():
            self.sql("UPDATE sessions SET timer_deadline = ?, latest_observation = ? WHERE id = ?",
                     (self.clock + 90, observation, sid))

        def ask_context():
            stream.push(
                event("contentStart", contentId="tool-content-1", type="TOOL", role="ASSISTANT"),
                event("toolUse", contentId="tool-content-1", toolName=voice.TOOL_NAME,
                      toolUseId="tool_use_1", content="{"),
                event("toolUse", contentId="tool-content-1", content="}"),
                event("contentEnd", contentId="tool-content-1", type="TOOL", stopReason="TOOL_USE"),
            )

        before = None

        def capture_before():
            nonlocal before
            with self.app.test_request_context("/"):
                row = get_db().execute("SELECT step_index, timer_deadline, latest_observation, timer_alerted"
                                       " FROM sessions WHERE id = ?", (sid,)).fetchone()
                before = tuple(row)

        ws = FakeWS(start_message(self.token), lambda w: "ready" in w.types(), run(update_context),
                    run(capture_before), run(ask_context),
                    lambda w: "toolResult" in stream.kinds(), json.dumps({"type": "stop"}))
        calls = self.install(stream)
        sent = self.serve(ws)
        result_event = stream.payload("toolResult")[0]
        result = json.loads(result_event["content"])
        self.assertIn("whisking", result["context"])
        self.assertIn("1 min 30 s left", result["context"])
        self.assertNotIn(voice.CONTEXT_REQUEST, [item["type"] for item in sent])
        with self.app.test_request_context("/"):
            row = get_db().execute("SELECT step_index, timer_deadline, latest_observation, timer_alerted"
                                   " FROM sessions WHERE id = ?", (sid,)).fetchone()
            self.assertEqual(tuple(row), before)

    def test_unknown_and_malformed_tools_return_static_error_without_execution(self):
        bridge = voice.VoiceBridge(None, None, "tiffany", "", "")
        for index, (name, content) in enumerate((
                ("unknown_tool", "{}"), (voice.TOOL_NAME, "{not-json"),
                (voice.TOOL_NAME, '{"action":"finish"}'))):
            key = f"content-{index}"
            bridge.handle(event("contentStart", contentId=key, type="TOOL", role="ASSISTANT"))
            bridge.handle(event("toolUse", contentId=key, toolName=name, toolUseId=f"id_{name}",
                                content=content))
            bridge.handle(event("contentEnd", contentId=key, type="TOOL", stopReason="TOOL_USE"))
        bridge.handle(event("contentStart", contentId="incomplete", type="TOOL", role="ASSISTANT"))
        bridge.handle(event("toolUse", contentId="incomplete", toolName=voice.TOOL_NAME,
                            toolUseId="id_incomplete", content="{}"))
        bridge.handle(event("contentEnd", contentId="incomplete", type="TOOL", stopReason="CANCELLED"))
        messages = bridge.drain()
        self.assertEqual(len(messages), 3)
        self.assertTrue(all(item["type"] == voice.CONTEXT_REQUEST and "result" in item for item in messages))
        self.assertTrue(all(item["result"] == {"error": "Unsupported or invalid read-only request."}
                            for item in messages))

    def test_output_handshake_timeout_has_no_ready_and_closes_stream(self):
        stream = FakeStream()
        stream.block_ready = True
        self.install(stream)
        with mock.patch.object(voice, "CONNECT_TIMEOUT", 0.15):
            sent = self.serve(FakeWS(start_message(self.token)))
        self.assertEqual(sent, [{"type": "error", "message": voice.CONNECT_FAILED}])
        self.assertTrue(stream.ready_started.is_set())
        self.assertTrue(stream.closed.is_set())

    def test_lingering_bridge_fails_closed_until_prior_thread_exits(self):
        release = threading.Event()
        calls = []

        async def slow_open(_connection):
            calls.append("open")
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                while not release.is_set():
                    try:
                        await asyncio.sleep(0.01)
                    except asyncio.CancelledError:
                        continue
                raise

        self.app.extensions["sous_voice"]["open_stream"] = slow_open
        with mock.patch.object(voice, "CONNECT_TIMEOUT", 0.05), \
                mock.patch.object(voice, "CLOSE_STEP_TIMEOUT", 0.25), \
                mock.patch.object(voice, "SHUTDOWN_TIMEOUT", 0.01):
            first = self.serve(FakeWS(start_message(self.token), lambda _w: bool(calls),
                                      json.dumps({"type": "stop"})), expect_thread_exit=False)
            self.assertEqual(first, [{"type": "stopped", "reason": "client_stop"}])
            lingering = self.app.extensions["sous_voice"]["lingering"]
            self.assertIsNotNone(lingering)
            self.assertTrue(lingering._thread.is_alive())
            second = self.serve(FakeWS(start_message(self.token)), expect_thread_exit=False)
            self.assertEqual(second, [{"type": "error", "message": voice.STILL_STOPPING}])
            self.assertEqual(calls, ["open"])
            release.set()
            lingering._thread.join(2)
            self.assertFalse(lingering._thread.is_alive())
            self.assertEqual([t for t in threading.enumerate() if t.name == "sous-voice"], [])


class BridgeMappingTests(SettingsTestCase):
    def bridge(self):
        return voice.VoiceBridge(None, None, "tiffany", "", "")

    def test_interrupt_detection_variants(self):
        for text in ('{ "interrupted" : true }', '{"interrupted":true}', ' {\n"interrupted": true } '):
            self.assertTrue(voice.is_interrupted(text), text)
        for text in ('{"interrupted": false}', "interrupted", '{"interrupted": "true"}'):
            self.assertFalse(voice.is_interrupted(text), text)

    def test_interruptions_are_deduplicated_until_new_audio(self):
        bridge = self.bridge()
        audio = base64.b64encode(b"\x00\x00").decode()
        for message in (event("contentEnd", stopReason="INTERRUPTED"),
                        event("textOutput", role="ASSISTANT", content='{"interrupted":true}'),
                        event("audioOutput", content=audio),
                        event("contentEnd", stopReason="INTERRUPTED"),
                        event("contentEnd", stopReason="END_TURN")):
            bridge.handle(message)
        self.assertEqual([m["type"] for m in bridge.drain()], ["interrupted", "audio", "interrupted"])

    def test_generation_stage_parsing(self):
        self.assertEqual(voice.generation_stage(SPECULATIVE), "SPECULATIVE")
        self.assertEqual(voice.generation_stage({"generationStage": "FINAL"}), "FINAL")
        for bad in ("not json", "[]", None, 5):
            self.assertIsNone(voice.generation_stage(bad))

    def test_interleaved_content_ids_keep_generation_stage_attached(self):
        bridge = self.bridge()
        bridge.handle(event("contentStart", contentId="spec", role="ASSISTANT", type="TEXT",
                            additionalModelFields=SPECULATIVE))
        bridge.handle(event("contentStart", contentId="final", role="ASSISTANT", type="TEXT",
                            additionalModelFields=FINAL))
        bridge.handle(event("textOutput", contentId="spec", content="Draft"))
        bridge.handle(event("textOutput", contentId="final", content="Answer"))
        self.assertEqual([item["final"] for item in bridge.drain()], [False, True])

    def test_public_errors_use_type_names_only(self):
        class AccessDeniedException(Exception):
            pass
        self.assertNotIn(SECRET, voice.public_error(AccessDeniedException(SECRET)))
        self.assertEqual(voice.public_error(RuntimeError(SECRET)), voice.UNAVAILABLE)
        self.assertEqual(voice.public_error(TimeoutError()), voice.CONNECT_FAILED)


class SdkTests(SettingsTestCase):
    def test_installed_sdk_config_uses_crt_http2_static_credentials_and_no_host_sources(self):
        from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient
        from smithy_aws_core.config.filesystem import DefaultFileSystem
        from smithy_aws_core.identity import StaticCredentialsResolver
        from smithy_http.aio.crt import AWSCRTHTTPClient

        connection = BedrockConnection("eu-north-1", KEY_ID, SECRET, TOKEN)
        host = {"AWS_PROFILE": "host-profile", "AWS_REGION": "us-east-1", "AWS_MAX_ATTEMPTS": "9",
                "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "https://example.invalid",
                "AWS_ACCESS_KEY_ID": "AKIAHOSTKEY000000000", "AWS_SECRET_ACCESS_KEY": "host-secret"}
        results = {}

        async def check():
            config = await voice.build_config(connection)
            try:
                results["config"] = config
                results["identity"] = await config.aws_credentials_identity_resolver.get_identity(properties={})
                client = AsyncBedrockRuntimeClient(config=config)
                resolved = await client._ensure_setup()  # no network: setup only
                results["shared"] = (resolved.transport is config.transport,
                                     resolved.aws_credentials_identity_resolver
                                     is config.aws_credentials_identity_resolver)
                await client.close()
            finally:
                await config.transport.close()

        with mock.patch.dict(os.environ, host), \
                mock.patch.object(DefaultFileSystem, "read_file", side_effect=AssertionError("host file read")):
            asyncio.run(check())
        config, identity = results["config"], results["identity"]
        self.assertIsInstance(config.transport, AWSCRTHTTPClient)
        self.assertTrue(config.transport.SUPPORTS_DUPLEX_STREAMING)
        self.assertTrue(config.transport._config.force_http_2)
        self.assertIs(type(config.aws_credentials_identity_resolver), StaticCredentialsResolver)
        self.assertEqual((identity.access_key_id, identity.secret_access_key, identity.session_token),
                         (KEY_ID, SECRET, TOKEN))
        self.assertEqual((config.region, config.endpoint_uri, config.max_attempts, config.retry_mode),
                         ("eu-north-1", "https://bedrock-runtime.eu-north-1.amazonaws.com", 1, "standard"))
        self.assertIsNone(config.aws_access_key_id)
        self.assertIsNone(config.sdk_ua_app_id)
        self.assertEqual(results["shared"], (True, True))
        self.assert_no_secrets(repr(config))

        with self.assertRaises(ValueError):
            asyncio.run(voice.build_config(BedrockConnection("eu-central-1", KEY_ID, SECRET)))

    def test_sonic_stream_wraps_sdk_event_stream(self):
        from aws_sdk_bedrock_runtime.models import (
            BidirectionalOutputPayloadPart, InvokeModelWithBidirectionalStreamInputChunk,
            InvokeModelWithBidirectionalStreamOutputChunk,
            InvokeModelWithBidirectionalStreamOutputThrottlingException, ThrottlingException)

        closed = []

        class Closer:
            def __init__(self, name):
                self.name = name

            async def close(self):
                closed.append(self.name)

        class Publisher(Closer):
            sent = []

            async def send(self, item):
                self.sent.append(item)

        class Receiver(Closer):
            items = [
                InvokeModelWithBidirectionalStreamOutputChunk(
                    value=BidirectionalOutputPayloadPart(bytes_=b'{"event": {"completionEnd": {}}}')),
                InvokeModelWithBidirectionalStreamOutputChunk(value=BidirectionalOutputPayloadPart(bytes_=b"nope")),
                InvokeModelWithBidirectionalStreamOutputThrottlingException(value=ThrottlingException(message=SECRET)),
                None,
            ]

            async def receive(self):
                return self.items.pop(0)

        publisher, receiver = Publisher("input"), Receiver("output")

        class Duplex:
            input_stream = publisher

            async def await_output(self):
                return None, receiver

        stream = voice.SonicStream(Closer("client"), Closer("transport"), Duplex())
        outcomes = {}

        async def check():
            await stream.send({"event": {"sessionEnd": {}}})
            outcomes["first"] = await stream.receive()
            outcomes["second"] = await stream.receive()
            try:
                await stream.receive()
            except voice.VoiceUpstreamError as exc:
                outcomes["error"] = exc
            outcomes["end"] = await stream.receive()
            await stream.close()

        asyncio.run(check())
        chunk = publisher.sent[0]
        self.assertIsInstance(chunk, InvokeModelWithBidirectionalStreamInputChunk)
        self.assertEqual(json.loads(chunk.value.bytes_), {"event": {"sessionEnd": {}}})
        self.assertEqual((outcomes["first"], outcomes["second"], outcomes["end"]),
                         ({"event": {"completionEnd": {}}}, {}, None))
        self.assertEqual(voice.public_error(outcomes["error"]),
                         "AWS is throttling voice requests. Try again in a moment.")
        self.assertNotIn(SECRET, str(outcomes["error"]))
        self.assertEqual(closed, ["input", "output", "client", "transport"])
