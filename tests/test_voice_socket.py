"""Opt-in real-localhost Flask-Sock integration with a synthetic upstream.

Enable with SOUS_VOICE_SOCKET_TESTS=1. This binds only to loopback and never calls AWS.
"""

import asyncio
import base64
import http.cookiejar
import json
import os
import queue
import re
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from simple_websocket import Client
from werkzeug.serving import make_server

from app import create_app


class PortAwareClient(Client):
    """Keep the non-default loopback port in the WebSocket Host header."""

    def handshake(self):
        self.host = f"{self.host}:{self.port}"
        try:
            return super().handshake()
        except Exception:
            try:
                self.sock.close()
            except Exception:
                pass
            raise


class SyntheticSonicStream:
    def __init__(self):
        self.sent = queue.Queue()
        self.incoming = queue.Queue()
        self.audio_received = threading.Event()
        self.closed = threading.Event()

    async def wait_ready(self):
        return None

    async def send(self, item):
        self.sent.put(item)
        if "audioInput" in item.get("event", {}):
            self.audio_received.set()

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

    async def close(self):
        self.closed.set()

    def push(self, item):
        self.incoming.put(item)


class VoiceSocketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("SOUS_VOICE_SOCKET_TESTS") != "1":
            raise unittest.SkipTest("Set SOUS_VOICE_SOCKET_TESTS=1 to enable localhost WebSocket integration.")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sous-voice-socket-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({
            "TESTING": True,
            "DATABASE": str(Path(self.temp.name) / "sous.sqlite3"),
            "SETTINGS_VAULT": str(Path(self.temp.name) / "settings.vault"),
            "SECRET_KEY": "synthetic-voice-socket-test-secret",
        })
        self.streams = []

        async def open_stream(_connection):
            stream = SyntheticSonicStream()
            self.streams.append(stream)
            return stream

        self.app.extensions["sous_voice"]["open_stream"] = open_stream
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.cookies = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        with self.http.open(self.origin + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
        self.csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)
        self.cookie_header = "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookies)
        self.request("POST", "/api/settings/vault", {"passphrase": "synthetic socket vault passphrase"})
        self.request("PUT", "/api/settings/bedrock", {
            "region": "us-west-2", "access_key_id": "AKIA1234567890TEST",
            "secret_access_key": "synthetic-secret-access-key", "session_token": "",
        })
        self.session = self.request("POST", "/api/sessions", {
            "recipe_id": "scrambled-eggs", "provider": "ollama", "model": "synthetic-model", "mode": "monitoring",
        })

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=3)
        self.server.server_close()

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.origin + path, data=data, method=method, headers={
            "Origin": self.origin,
            "X-CSRF-Token": self.csrf,
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        with self.http.open(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def connect_voice(self):
        return PortAwareClient.connect(
            f"ws://127.0.0.1:{self.server.server_port}/api/sessions/{self.session['id']}/voice",
            headers={"Origin": self.origin, "Cookie": self.cookie_header},
        )

    def test_rejects_cross_origin_websocket(self):
        with self.assertRaises(Exception):
            PortAwareClient.connect(
                f"ws://127.0.0.1:{self.server.server_port}/api/sessions/{self.session['id']}/voice",
                headers={"Origin": "http://evil.example", "Cookie": self.cookie_header},
            )
        self.assertEqual(self.streams, [])

    def wait_for_voice_slot(self):
        deadline = time.monotonic() + 3
        state = self.app.extensions["sous_voice"]["active"]
        while time.monotonic() < deadline:
            if state.acquire(blocking=False):
                state.release()
                return
            time.sleep(0.01)
        self.fail("voice slot was not released after stream teardown")

    @staticmethod
    def receive_json(client, timeout=3):
        message = client.receive(timeout=timeout)
        if isinstance(message, str):
            return json.loads(message)
        return message

    def test_localhost_duplex_stream_manual_action_and_disconnect_cleanup(self):
        client = self.connect_voice()
        try:
            client.send(json.dumps({"type": "start", "csrf_token": self.csrf}))
            self.assertEqual(self.receive_json(client), {"type": "ready", "model": "amazon.nova-2-sonic-v1:0",
                                                          "voice": "tiffany", "input_sample_rate": 16000,
                                                          "output_sample_rate": 24000})
            stream = self.streams[0]
            client.send(b"\x01\x00" * 512)
            self.assertTrue(stream.audio_received.wait(3), "binary browser audio did not reach fake upstream")

            audio = base64.b64encode(b"\x01\x00" * 240).decode("ascii")
            stream.push({"event": {"audioOutput": {"content": audio}}})
            self.assertEqual(self.receive_json(client), {"type": "audio", "audio": audio})
            stream.push({"event": {"contentEnd": {"type": "AUDIO", "stopReason": "INTERRUPTED"}}})
            self.assertEqual(self.receive_json(client), {"type": "interrupted"})

            action = self.request("POST", f"/api/sessions/{self.session['id']}/actions", {
                "action": "timer_set", "seconds": 45,
            })
            self.assertEqual(action["timer_deadline"] - action["updated_at"], 45)
            client.send(json.dumps({"type": "stop"}))
            self.assertEqual(self.receive_json(client), {"type": "stopped", "reason": "client_stop"})
            self.assertTrue(stream.closed.wait(3))
        finally:
            try:
                client.close()
            except Exception:
                pass

        self.wait_for_voice_slot()
        disconnected = self.connect_voice()
        try:
            disconnected.send(json.dumps({"type": "start", "csrf_token": self.csrf}))
            self.assertEqual(self.receive_json(disconnected)["type"], "ready")
            second_stream = self.streams[1]
            disconnected.close()
            self.assertTrue(second_stream.closed.wait(3), "abrupt browser disconnect did not close fake upstream")
        finally:
            try:
                disconnected.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
