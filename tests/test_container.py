"""Opt-in checks against a disposable Docker Compose stack (never the default app stack).

Start it first:  SOUS_PORT=5051 docker compose -p sous-verify up -d --build
Run:             SOUS_CONTAINER_TESTS=1 python -m unittest tests.test_container -v
Live Ollama:     TMPDIR=/tmp PLAYWRIGHT_BROWSERS_PATH=/tmp/sous-webkit SOUS_CONTAINER_TESTS=1 \
                 SOUS_LIVE_OLLAMA=1 .venv/bin/python -m unittest tests.test_container -v
                 (needs qwen3-vl:8b on the host Ollama and Playwright WebKit installed under /tmp).
Only the sous-verify project on 127.0.0.1:5051 is ever touched; the check runs before any mutation.
"""

import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = "sous-verify"
PORT = "5051"  # reserved for the disposable stack; the default app uses 5050
BASE_URL = os.environ.get("SOUS_CONTAINER_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
LIVE_MODEL = "qwen3-vl:8b"
_verified_fixture = False


def compose(*args, **kwargs):
    """Run docker compose for the disposable project only (argument array, no shell)."""
    return subprocess.run(["docker", "compose", "-p", PROJECT, *args], cwd=ROOT,
                          env={**os.environ, "SOUS_PORT": PORT}, **kwargs)


def verify_fixture_identity():
    """Refuse to mutate anything unless BASE_URL is the sous-verify stack's loopback port 5051."""
    global _verified_fixture
    if _verified_fixture:
        return
    url = urlparse(BASE_URL)
    if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1") or url.port != int(PORT):
        raise AssertionError(f"SOUS_CONTAINER_URL must be an http loopback URL on port {PORT} (the disposable "
                             "stack), never the default app port")
    result = compose("port", "sous", "5000", capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or result.stdout.strip() != f"127.0.0.1:{PORT}":
        raise AssertionError(f"project {PROJECT} is not publishing 127.0.0.1:{PORT}; start it with: "
                             f"SOUS_PORT={PORT} docker compose -p {PROJECT} up -d")
    _verified_fixture = True


@unittest.skipUnless(os.environ.get("SOUS_CONTAINER_TESTS") == "1", "set SOUS_CONTAINER_TESTS=1 to run")
class ContainerTestCase(unittest.TestCase):
    def setUp(self):
        verify_fixture_identity()
        self.http = requests.Session()  # keeps the signed session cookie like a browser
        self.token = self.load_index()
        self.addCleanup(self.end_active_session)
        self.end_active_session()  # the disposable stack may hold a session from an earlier run

    def load_index(self):
        page = self.http.get(f"{BASE_URL}/", timeout=10)
        self.assertEqual(page.status_code, 200)
        return re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)

    def api(self, method, path, **kwargs):
        headers = {"X-CSRF-Token": self.token} if method == "POST" else {}
        return self.http.request(method, f"{BASE_URL}{path}", headers=headers,
                                 timeout=kwargs.pop("timeout", 15), **kwargs)

    def end_active_session(self):
        active = self.api("GET", "/api/sessions/active").json()["session"]
        if active and active["status"] == "active":
            self.api("POST", f"/api/sessions/{active['id']}/actions", json={"action": "end"})

    def start_session(self, provider="ollama", model="test-model"):
        resp = self.api("POST", "/api/sessions", json={
            "recipe_id": self.api("GET", "/api/config").json()["recipes"][0]["id"],
            "provider": provider, "model": model, "mode": "monitoring"})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def act(self, session_id, action, **fields):
        resp = self.api("POST", f"/api/sessions/{session_id}/actions", json={"action": action, **fields})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()


class ContainerPersistenceTests(ContainerTestCase):
    def test_served_assets_and_state_survive_container_restart(self):
        page = self.http.get(f"{BASE_URL}/", timeout=10).text
        for asset in re.findall(r'(?:href|src)="(/static/[^"]+)"', page):
            self.assertEqual(self.http.get(f"{BASE_URL}{asset}", timeout=10).status_code, 200, asset)
        config = self.api("GET", "/api/config").json()
        self.assertTrue(config["recipes"])
        self.assertNotIn("sk-", str(config))

        session = self.start_session()
        sid = session["id"]
        self.act(sid, "resume")
        self.act(sid, "next")
        self.act(sid, "correct", step_index=0, note="container test")
        before = self.act(sid, "timer_set", seconds=3600)

        compose("restart", "sous", check=True, timeout=300,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.wait_until_ready()

        after = self.api("GET", "/api/sessions/active").json()["session"]  # same cookie as before the restart
        self.assertEqual(after["id"], sid)
        self.assertEqual(after["step_index"], before["step_index"])
        self.assertEqual(after["timer_deadline"], before["timer_deadline"])
        self.assertEqual(after["recipe"], before["recipe"])
        self.assertFalse(after["monitoring"])
        self.assertGreater(after["revision"], before["revision"])
        # Same cookie + token still accepted: the generated secret key persisted in the volume.
        self.assertEqual(self.act(sid, "timer_cancel")["timer_deadline"], None)

    def wait_until_ready(self):
        for _ in range(40):
            try:
                if self.http.get(f"{BASE_URL}/api/config", timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(1.5)
        self.fail("container did not become ready after restart")


@unittest.skipUnless(os.environ.get("SOUS_LIVE_OLLAMA") == "1", "set SOUS_LIVE_OLLAMA=1 for the live Ollama check")
class ContainerLiveOllamaTests(ContainerTestCase):
    def neutral_frame(self):
        """A plain generated canvas image (gray counter, white plate); no camera or personal browser."""
        tmp_root = Path("/tmp").resolve()
        browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if not browsers or not Path(browsers).resolve().is_relative_to(tmp_root) or not Path(browsers).is_dir():
            self.fail("live test needs PLAYWRIGHT_BROWSERS_PATH pointing to an existing WebKit install under /tmp "
                      "(e.g. /tmp/sous-webkit); no other browser runtime is used")
        scratch = os.environ.get("TMPDIR", "")
        if not scratch or not Path(scratch).resolve().is_relative_to(tmp_root) \
                or not Path(tempfile.gettempdir()).resolve().is_relative_to(tmp_root):
            self.fail("live test needs TMPDIR under /tmp so the browser's temporary profile stays there")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.webkit.launch()  # ephemeral profile; runtime verified above
            try:
                page = browser.new_page()
                return page.evaluate("""() => {
                    const c = document.createElement('canvas'); c.width = 640; c.height = 480;
                    const g = c.getContext('2d');
                    g.fillStyle = '#9a9a9a'; g.fillRect(0, 0, 640, 480);
                    g.fillStyle = '#f5f5f5'; g.beginPath(); g.arc(320, 240, 120, 0, 7); g.fill();
                    return c.toDataURL('image/jpeg', 0.75);
                }""")
            finally:
                browser.close()

    def test_container_reaches_host_ollama(self):
        frame = self.neutral_frame()
        self.assertTrue(frame.startswith("data:image/jpeg;base64,"))
        session = self.start_session(model=LIVE_MODEL)
        resumed = self.act(session["id"], "resume")
        resp = self.api("POST", f"/api/sessions/{session['id']}/observations", timeout=195,
                        json={"image": frame, "revision": resumed["revision"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        obs = resp.json()["latest_observation"]
        self.assertIsInstance(obs["activity"], str)
        self.assertIsInstance(obs["evidence"], str)
        self.assertTrue(0 <= obs["confidence"] <= 1)
        self.assertIsInstance(obs["step_complete"], bool)
        self.assertTrue(obs["alert"] is None or isinstance(obs["alert"], str))


if __name__ == "__main__":
    unittest.main()
