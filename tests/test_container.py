"""Opt-in checks against a disposable Docker Compose stack (never the default app stack).

Start it first:  SOUS_PORT=5051 docker compose -p sous-verify up -d --build
Run:             SOUS_CONTAINER_TESTS=1 python -m unittest tests.test_container -v
Only the sous-verify project on 127.0.0.1:5051 is ever touched; the check runs before any mutation.
"""

import os
import json
import re
import subprocess
import time
import unittest
from urllib.parse import urlparse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = "sous-verify"
PORT = "5051"  # reserved for the disposable stack; the default app uses 5050
BASE_URL = os.environ.get("SOUS_CONTAINER_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
SYNTHETIC_PASSPHRASE = "sous verify synthetic passphrase"
SYNTHETIC_ACCESS_KEY = "AKIA1234567890TEST"
SYNTHETIC_SECRET_KEY = "synthetic-container-secret-key"
# Synthetic shared connection saved through the admin API; saving never contacts AWS.
SYNTHETIC_CONNECTION = {"region": "us-east-1", "model": "amazon.nova-lite-v1:0",
                        "access_key_id": "AKIASYNTHETIC0000001", "secret_access_key": "synthetic-secret-access-key"}
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
        self.wait_for_health()  # Gunicorn may still be starting right after `compose up -d`
        self.token = self.load_index()
        self.authenticate_fixture_admin()
        self.addCleanup(self.end_active_session)
        self.end_active_session()  # the disposable stack may hold a session from an earlier run

    def load_index(self):
        response = self.http.get(f"{BASE_URL}/api/auth", timeout=10)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    def auth_request(self, client, method, path, token, body=None):
        return client.request(method, f"{BASE_URL}{path}", json=body,
                              headers={"X-CSRF-Token": token}, timeout=15)

    def authenticate_fixture_admin(self):
        """Use a known synthetic account; never reset or adopt an unknown persisted fixture."""
        state = self.http.get(f"{BASE_URL}/api/auth", timeout=10).json()
        if state["setup_required"]:
            response = self.auth_request(self.http, "POST", "/api/auth/setup", self.token,
                                         {"username": "sousverify", "password": "sous-verify-login-passphrase"})
        else:
            response = self.auth_request(self.http, "POST", "/api/auth/login", self.token,
                                         {"username": "sousverify", "password": "sous-verify-login-passphrase"})
        self.assertEqual(response.status_code, 201 if state["setup_required"] else 200,
                         "sous-verify has an unknown persisted account; refusing to reset or mutate it")
        self.token = response.json()["csrf_token"]
        self.assertEqual(response.json()["user"]["username"], "sousverify")

    def api(self, method, path, **kwargs):
        headers = {"X-CSRF-Token": self.token} if method != "GET" else {}
        return self.http.request(method, f"{BASE_URL}{path}", headers=headers,
                                 timeout=kwargs.pop("timeout", 15), **kwargs)

    def login_fixture_user(self, username, password):
        client = requests.Session()
        state = client.get(f"{BASE_URL}/api/auth", timeout=10).json()
        response = self.auth_request(client, "POST", "/api/auth/login", state["csrf_token"],
                                     {"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return client, response.json()["csrf_token"]

    def end_active_session(self):
        active = self.api("GET", "/api/sessions/active").json()["session"]
        if active and active["status"] == "active":
            self.api("POST", f"/api/sessions/{active['id']}/actions", json={"action": "end"})

    def ensure_shared_connection(self):
        """Create or unlock the shared vault and save the synthetic connection (admin fixture)."""
        vault = self.api("GET", "/api/settings").json()["vault"]
        path = "/api/settings/unlock" if vault["initialized"] else "/api/settings/vault"
        if vault["locked"]:
            resp = self.api("POST", path, json={"passphrase": SYNTHETIC_PASSPHRASE})  # one shared vault
            self.assertIn(resp.status_code, (200, 201), resp.text)
        resp = self.api("PUT", "/api/settings/bedrock", json=SYNTHETIC_CONNECTION)
        self.assertEqual(resp.status_code, 200, resp.text)

    def start_session(self, provider="bedrock", model="amazon.nova-lite-v1:0"):
        self.ensure_shared_connection()
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
        allowed_provider_fields = {"id", "name", "configured", "default_model", "source", "locked"}
        for provider in config["providers"]:
            self.assertLessEqual(set(provider), allowed_provider_fields)
        serialized_config = json.dumps(config)
        sensitive_names = {"access_key_id", "secret_access_key", "session_token", "passphrase", "password", "credentials"}
        found_names = set()
        def collect_field_names(value):
            if isinstance(value, dict):
                found_names.update(key.lower() for key in value)
                for nested in value.values():
                    collect_field_names(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_field_names(nested)
        collect_field_names(config)
        self.assertFalse(found_names & sensitive_names)
        self.assertNotIn(SYNTHETIC_SECRET_KEY, serialized_config)
        self.assertNotIn(SYNTHETIC_PASSPHRASE, serialized_config)

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

    def test_encrypted_settings_survive_restart_locked_and_interval_remains_available(self):
        passphrase = SYNTHETIC_PASSPHRASE
        access_key = SYNTHETIC_ACCESS_KEY
        secret_key = SYNTHETIC_SECRET_KEY
        settings = self.api("GET", "/api/settings").json()
        if not settings["vault"]["initialized"]:
            response = self.api("POST", "/api/settings/vault", json={"passphrase": passphrase})
            self.assertEqual(response.status_code, 201, response.text)
        else:
            # The disposable fixture is never reset to accommodate this test. It must already
            # use this test's synthetic passphrase so its encrypted state remains user-owned.
            if not settings["vault"]["locked"]:
                self.api("POST", "/api/settings/lock", json={})
            response = self.api("POST", "/api/settings/unlock", json={"passphrase": passphrase})
            self.assertEqual(response.status_code, 200,
                             "sous-verify vault is initialized with a different passphrase; refusing to reset it")

        saved = self.api("PUT", "/api/settings/bedrock", json={
            "region": "us-east-1", "model": "us.synthetic.vision-v1",
            "access_key_id": access_key, "secret_access_key": secret_key,
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn(secret_key, saved.text)
        self.assertNotIn(passphrase, saved.text)
        preference = self.api("PUT", "/api/settings/preferences", json={"sample_interval": 15})
        self.assertEqual(preference.status_code, 200, preference.text)
        self.assertEqual(preference.json()["sample_interval"], 15)

        compose("restart", "sous", check=True, timeout=300,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.wait_until_ready()
        locked = self.api("GET", "/api/settings").json()
        self.assertTrue(locked["vault"]["initialized"])
        self.assertTrue(locked["vault"]["locked"])
        self.assertIsNone(locked["bedrock"]["region"])
        self.assertIsNone(locked["bedrock"]["has_credentials"])
        self.assertEqual(locked["sample_interval"], 15)
        self.assertNotIn(secret_key, str(locked))

        unlocked = self.api("POST", "/api/settings/unlock", json={"passphrase": passphrase})
        self.assertEqual(unlocked.status_code, 200, unlocked.text)
        state = unlocked.json()
        self.assertEqual(state["bedrock"]["region"], "us-east-1")
        self.assertEqual(state["bedrock"]["model"], "us.synthetic.vision-v1")
        self.assertTrue(state["bedrock"]["has_credentials"])
        self.assertNotIn(secret_key, unlocked.text)
        self.assertNotIn(passphrase, unlocked.text)
        self.api("POST", "/api/settings/lock", json={})

    def test_account_owned_recipe_session_and_named_timer_persist_and_stay_private(self):
        users = self.api("GET", "/api/users")
        self.assertEqual(users.status_code, 200, users.text)
        fixture_bob = next((item for item in users.json()["users"] if item["username"] == "sousbob"), None)
        if fixture_bob is None:
            created_user = self.api("POST", "/api/users", json={
                "username": "sousbob", "password": "sous-verify-bob-login-passphrase"})
            self.assertEqual(created_user.status_code, 201, created_user.text)
            bob_id = created_user.json()["user"]["id"]
        else:
            bob_id = fixture_bob["id"]
            # A reused fixture is accepted only if its known synthetic account still authenticates.
            self.login_fixture_user("sousbob", "sous-verify-bob-login-passphrase")

        recipe = {
            "name": "Verify Fixture Recipe", "description": "Synthetic container persistence fixture.",
            "servings": 2, "equipment": ["bowl"],
            "ingredients": ["1 cup oats"],
            "steps": [{"title": "Mix", "instruction": "Mix the oats.", "visual_cue": "Oats are mixed."}],
        }
        saved = self.api("POST", "/api/recipes", json={"recipe": recipe, "scope": "personal"})
        self.assertEqual(saved.status_code, 201, saved.text)
        recipe_id = saved.json()["recipe"]["id"]
        self.assertEqual(saved.json()["recipe"]["scope"], "personal")

        self.ensure_shared_connection()
        started = self.api("POST", "/api/sessions", json={
            "recipe_id": recipe_id, "provider": "bedrock", "model": "amazon.nova-lite-v1:0", "mode": "monitoring"})
        self.assertEqual(started.status_code, 201, started.text)
        session_id = started.json()["id"]
        timer_path = f"/api/sessions/{session_id}/timers"
        timer_resp = self.api("POST", timer_path, json={"name": "Rest", "duration_seconds": 3600})
        self.assertEqual(timer_resp.status_code, 201, timer_resp.text)
        timer_id = next(item["id"] for item in timer_resp.json()["timers"] if item["name"] == "Rest")
        alice_preferences = self.api("PUT", "/api/settings/preferences", json={"sample_interval": 15})
        self.assertEqual(alice_preferences.status_code, 200, alice_preferences.text)

        compose("restart", "sous", check=True, timeout=300,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.wait_until_ready()
        self.token = self.load_index()  # cookie persists, CSRF token may be refreshed by auth state

        restored_recipe = self.api("GET", f"/api/recipes/{recipe_id}")
        self.assertEqual(restored_recipe.status_code, 200, restored_recipe.text)
        restored_session = self.api("GET", f"/api/history/{session_id}")
        self.assertEqual(restored_session.status_code, 200, restored_session.text)
        timers = self.api("GET", timer_path).json()["timers"]
        self.assertEqual([(item["id"], item["status"]) for item in timers], [(timer_id, "running")])

        bob, bob_token = self.login_fixture_user("sousbob", "sous-verify-bob-login-passphrase")
        headers = {"X-CSRF-Token": bob_token}
        bob_library = bob.get(f"{BASE_URL}/api/recipes", timeout=15)
        self.assertEqual(bob_library.status_code, 200, bob_library.text)
        bob_recipes = bob_library.json()["recipes"]
        self.assertNotIn(recipe_id, {item["id"] for item in bob_recipes})
        self.assertEqual(bob.get(f"{BASE_URL}/api/recipes/{recipe_id}", timeout=15).status_code, 404)
        self.assertEqual(bob.get(f"{BASE_URL}/api/history/{session_id}", timeout=15).status_code, 404)
        self.assertEqual(bob.get(f"{BASE_URL}{timer_path}", timeout=15).status_code, 404)
        self.assertEqual(bob.get(f"{BASE_URL}/api/sessions/active", timeout=15).json()["session"], None)
        self.assertEqual(bob.get(f"{BASE_URL}/api/settings", timeout=15).status_code, 200)
        # The two users have separate preference records; Bob starts with the default interval.
        self.assertEqual(bob.get(f"{BASE_URL}/api/settings", timeout=15).json()["sample_interval"], 10)
        self.assertEqual(self.api("GET", "/api/settings").json()["sample_interval"], 15)
        self.assertEqual(bob_id, bob.get(f"{BASE_URL}/api/auth", timeout=15).json()["user"]["id"])
        # Mutations still require Bob's own CSRF token, never Alice's token.
        self.assertEqual(bob.post(f"{BASE_URL}/api/sessions/{session_id}/timers/{timer_id}/ack",
                                  headers=headers, json={}, timeout=15).status_code, 404)

    def wait_for_health(self):
        """Database-free liveness; resets and refusals while the server starts are retried."""
        for _ in range(40):
            try:
                if self.http.get(f"{BASE_URL}/healthz", timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(1.5)
        self.fail("container did not become healthy")

    def wait_until_ready(self):
        for _ in range(40):
            try:
                if self.http.get(f"{BASE_URL}/api/config", timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(1.5)
        self.fail("container did not become ready after restart")


if __name__ == "__main__":
    unittest.main()
