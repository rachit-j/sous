"""Isolated WebKit checks for the in-app settings workflow.

Run with SOUS_BROWSER_TESTS=1 and PLAYWRIGHT_BROWSERS_PATH set to the
isolated WebKit installation under /tmp. AWS discovery is always synthetic.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.serving import make_server

import providers
from app import create_app


PASSPHRASE = "synthetic kitchen vault phrase"
ACCESS_KEY = "AKIA1234567890TEST"
SECRET_KEY = "synthetic-secret-access-key"


class SettingsBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("SOUS_BROWSER_TESTS") != "1":
            raise unittest.SkipTest("Set SOUS_BROWSER_TESTS=1 to run isolated WebKit UI tests.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install requirements-dev.txt before enabling browser tests.") from exc
        browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        resolved = Path(browser_path).resolve() if browser_path else None
        if not resolved or not resolved.is_dir() or Path("/tmp").resolve() not in resolved.parents:
            raise RuntimeError("PLAYWRIGHT_BROWSERS_PATH must point to the isolated WebKit runtime under /tmp.")
        cls.sync_playwright = staticmethod(sync_playwright)

    def setUp(self):
        Path("/private/tmp/sous-webkit-artifacts").mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="sous-settings-browser-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        recipes = Path(self.temp.name) / "recipes"
        recipes.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1] / "recipes" / "00-scrambled-eggs.json", recipes / "00-scrambled-eggs.json")
        shutil.copyfile(Path(__file__).resolve().parents[1] / "recipes" / "10-desk-demo.json", recipes / "10-desk-demo.json")
        app = create_app({
            "TESTING": True,
            "DATABASE": os.path.join(self.temp.name, "sous.sqlite3"),
            "SETTINGS_VAULT": os.path.join(self.temp.name, "settings.vault"),
            "SECRET_KEY": "settings-browser-test-secret",
            "RECIPE_DIR": str(recipes),
        })
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.patch_models = patch.object(providers, "list_bedrock_models", return_value={
            "models": [
                {"id": "us.test.vision-v1", "name": "Test vision model", "kind": "foundation_model"},
                {"id": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test-profile", "name": "Test profile", "kind": "inference_profile"},
            ],
            "warning": None,
        })
        self.patch_models.start()
        self.addCleanup(self.patch_models.stop)
        self.playwright = self.sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.webkit.launch()
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.context.add_init_script("""
            (() => {
              const nativeSetInterval = window.setInterval.bind(window);
              window.__intervalDelays = [];
              window.setInterval = (callback, delay, ...args) => {
                window.__intervalDelays.push(Number(delay));
                return nativeSetInterval(callback, delay, ...args);
              };
              Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
                getUserMedia: async () => {
                  const canvas = document.createElement('canvas');
                  canvas.width = 320; canvas.height = 240;
                  const stream = canvas.captureStream(1);
                  window.__cameraTracks = window.__cameraTracks || [];
                  stream.getTracks().forEach((track) => window.__cameraTracks.push(track));
                  return stream;
                }
              }});
            })();
        """)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page_errors = []
        self.console_errors = []
        self.settings_responses = []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.on("response", self._record_settings_response)
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}", wait_until="networkidle")
        if self.page.locator("#auth-panel").is_visible():
            self.page.locator("#setup-username").fill("settings-admin")
            self.page.locator("#setup-password").fill("settings-admin-test-password")
            self.page.locator("#admin-setup-form button[type=submit]").click()
        self.page.locator("#setup-panel").wait_for(state="visible")

    def _record_settings_response(self, response):
        if "/api/settings" in response.url:
            try:
                self.settings_responses.append(response.text())
            except Exception:
                pass

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=3)
        self.server.server_close()

    def _open_settings(self):
        self.page.locator("#nav-settings").click()
        self.page.locator("#settings-panel").wait_for(state="visible")

    def _initialize_and_save_connection(self):
        page = self.page
        self._open_settings()
        page.locator("#vault-init-passphrase").fill(PASSPHRASE)
        page.locator("#vault-confirm-passphrase").fill(PASSPHRASE)
        page.locator("#vault-init-form button[type=submit]").click()
        page.locator("#vault-lock-button").wait_for(state="visible")
        page.locator("#bedrock-region").fill("us-east-1")
        page.locator("#bedrock-access-key").fill(ACCESS_KEY)
        page.locator("#bedrock-secret-key").fill(SECRET_KEY)
        page.locator("#save-bedrock").click()
        page.get_by_text("Saved connection · us-east-1").wait_for()

    def test_vault_credentials_model_defaults_and_interval(self):
        page = self.page
        self._initialize_and_save_connection()

        self.assertEqual(page.locator("#bedrock-access-key").input_value(), "")
        self.assertEqual(page.locator("#bedrock-secret-key").input_value(), "")
        self.assertEqual(page.locator("#bedrock-session-token").input_value(), "")
        self.assertNotIn(SECRET_KEY, page.content())
        self.assertNotIn(PASSPHRASE, page.content())
        browser_storage = page.evaluate("() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])")
        self.assertNotIn(SECRET_KEY, browser_storage)
        self.assertNotIn(PASSPHRASE, browser_storage)
        self.assertFalse(any(SECRET_KEY in text or PASSPHRASE in text for text in self.settings_responses))

        page.locator("#discover-models").click()
        page.locator("#bedrock-model-picker option[value='us.test.vision-v1']").wait_for(state="attached")
        page.locator("#bedrock-model-picker").select_option("us.test.vision-v1")
        page.locator("#default-bedrock-model").click()
        page.get_by_text("Saved. Sous has not tested AWS access.").wait_for()
        self.assertEqual(page.locator("#bedrock-model").input_value(), "us.test.vision-v1")
        self.assertEqual(page.locator("#default-provider").input_value(), "bedrock")
        self.assertEqual(page.locator("#default-model").input_value(), "us.test.vision-v1")

        page.locator("#sample-interval").fill("15")
        page.locator("#save-interval").click()
        page.get_by_text("Saved. New checks use this interval.").wait_for()
        config = page.evaluate("async () => (await fetch('/api/config')).json()")
        self.assertEqual(config["sample_interval"], 15)
        self.assertEqual(config["defaults"], {"provider": "bedrock", "model": "us.test.vision-v1"})
        page.locator("#sample-interval").evaluate("element => { element.value = '16'; element.dispatchEvent(new Event('input', { bubbles: true })); }")
        self.assertFalse(page.locator("#sample-interval").evaluate("element => element.checkValidity()"))
        page.locator("#sample-interval").fill("1")
        page.locator("#save-interval").click()
        page.get_by_text("Saved. New checks use this interval.").wait_for()
        config = page.evaluate("async () => (await fetch('/api/config')).json()")
        self.assertEqual(config["sample_interval"], 1)

        page.screenshot(path="/private/tmp/sous-webkit-artifacts/settings-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/settings-mobile.png", full_page=True)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)

        page.locator("#vault-lock-button").click()
        page.locator("#vault-unlock-form").wait_for(state="visible")
        self.assertEqual(page.locator("#sample-interval").input_value(), "1")
        page.locator("#vault-unlock-passphrase").fill("incorrect synthetic phrase")
        before_errors = len(self.console_errors)
        with page.expect_response(lambda response: "/api/settings/unlock" in response.url and response.status == 403):
            page.locator("#vault-unlock-form button[type=submit]").click()
        page.get_by_text("Incorrect passphrase or damaged settings data.").wait_for()
        self.assertEqual(self.console_errors[before_errors:], [
            "Failed to load resource: the server responded with a status of 403 (FORBIDDEN)"
        ])
        del self.console_errors[before_errors:]
        self.assertEqual(page.locator("#vault-unlock-passphrase").input_value(), "")
        page.locator("#vault-unlock-passphrase").fill(PASSPHRASE)
        page.locator("#vault-unlock-form button[type=submit]").click()
        page.locator("#vault-lock-button").wait_for(state="visible")
        self.assertFalse(page.locator("#vault-lock-button").is_disabled())
        page.locator("#nav-cooking").click()
        self.assertFalse(page.locator("#start-session").is_disabled())
        self.assertEqual(page.locator("#provider-select").input_value(), "bedrock")
        self.assertEqual(page.locator("#model-input").input_value(), "us.test.vision-v1")
        page.locator("#start-session").click()
        page.locator("#session-panel").wait_for(state="visible")
        page.locator("#monitoring-toggle").click()
        page.get_by_text("Guidance on").wait_for()
        page.locator("#nav-settings").click()
        page.locator("#settings-panel").wait_for(state="visible")
        page.locator("#vault-lock-button").click()
        page.locator("#vault-unlock-form").wait_for(state="visible")
        page.locator("#nav-cooking").click()
        page.locator("#bedrock-session-locked-notice").wait_for(state="visible")
        self.assertTrue(page.locator("#monitoring-toggle").is_disabled())
        self.assertTrue(page.locator("#timer-minutes").is_enabled())
        page.locator("#session-unlock-link").click()
        page.locator("#vault-unlock-passphrase").fill(PASSPHRASE)
        page.locator("#vault-unlock-form button[type=submit]").click()
        page.locator("#vault-lock-button").wait_for(state="visible")
        page.locator("#nav-cooking").click()
        self.assertFalse(page.locator("#monitoring-toggle").is_disabled())
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_model_discovery_error_keeps_custom_id_and_unsaved_fields(self):
        page = self.page
        self._initialize_and_save_connection()
        before_errors = len(self.console_errors)
        with patch.object(providers, "list_bedrock_models", side_effect=providers.ProviderError("Synthetic discovery failure.")):
            with page.expect_response(lambda response: "/api/settings/bedrock/models" in response.url and response.status == 502):
                page.locator("#discover-models").click()
            page.get_by_text("Synthetic discovery failure.", exact=False).wait_for()
        expected = "Failed to load resource: the server responded with a status of 502 (BAD GATEWAY)"
        self.assertEqual(self.console_errors[before_errors:], [expected])
        del self.console_errors[before_errors:]
        page.locator("#bedrock-model").fill("custom.account.profile")
        page.locator("#save-model").click()
        page.get_by_text("Saved. Sous has not tested AWS access.").wait_for()
        settings = page.evaluate("async () => (await fetch('/api/settings')).json()")
        self.assertEqual(settings["bedrock"]["model"], "custom.account.profile")
        self.assertEqual(page.locator("#bedrock-access-key").input_value(), "")
        self.assertEqual(page.locator("#bedrock-secret-key").input_value(), "")
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_settings_navigation_pauses_and_keeps_manual_cooking_controls(self):
        page = self.page
        page.locator("#provider-overrides > summary").click()
        page.locator("#model-input").fill("synthetic-model")
        page.locator("#start-session").click()
        page.locator("#session-panel").wait_for(state="visible")
        page.locator("#monitoring-toggle").click()
        page.get_by_text("Guidance on").wait_for()
        page.locator("#nav-settings").click()
        page.locator("#settings-panel").wait_for(state="visible")
        session = page.evaluate("async () => (await (await fetch('/api/sessions/active')).json()).session")
        self.assertFalse(session["monitoring"])
        page.locator("#nav-cooking").click()
        self.assertTrue(page.locator("#session-panel").is_visible())
        self.assertTrue(page.locator("#monitoring-toggle").inner_text().startswith("Resume guidance"))
        self.assertTrue(page.locator("#timer-minutes").is_enabled())
        self.assertFalse(page.locator("#camera-video").is_visible())
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_changed_defaults_apply_to_new_session_without_mutating_active_session(self):
        page = self.page
        self._initialize_and_save_connection()
        page.locator("#nav-cooking").click()
        page.locator("#provider-overrides > summary").click()
        page.locator("#recipe-select").select_option("desk-demo")
        page.locator("#model-input").fill("old-manual-model")
        page.locator("#start-session").click()
        page.locator("#session-panel").wait_for(state="visible")
        active_before = page.evaluate("async () => (await (await fetch('/api/sessions/active')).json()).session")
        self.assertEqual(active_before["provider"], "ollama")
        self.assertEqual(active_before["model"], "old-manual-model")
        self.assertEqual(active_before["recipe"]["id"], "desk-demo")

        self._open_settings()
        page.locator("#default-provider").select_option("bedrock")
        page.locator("#default-model").fill("us.test.vision-v1")
        page.locator("#save-defaults").click()
        page.get_by_text("Saved for new sessions.").wait_for()
        page.locator("#nav-cooking").click()
        active_after = page.evaluate("async () => (await (await fetch('/api/sessions/active')).json()).session")
        self.assertEqual((active_after["id"], active_after["provider"], active_after["model"]),
                         (active_before["id"], "ollama", "old-manual-model"))

        page.locator("#end-session").click()
        page.locator("#completion-panel").wait_for(state="visible")
        page.locator("#new-session").click()
        self.assertEqual(page.locator("#recipe-select").input_value(), "desk-demo")
        self.assertEqual(page.locator("#provider-select").input_value(), "bedrock")
        self.assertEqual(page.locator("#model-input").input_value(), "us.test.vision-v1")
        page.locator("#start-session").click()
        page.locator("#session-panel").wait_for(state="visible")
        next_session = page.evaluate("async () => (await (await fetch('/api/sessions/active')).json()).session")
        self.assertEqual(next_session["provider"], "bedrock")
        self.assertEqual(next_session["model"], "us.test.vision-v1")
        self.assertEqual(next_session["recipe"]["id"], "desk-demo")
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_interval_scheduler_and_settings_camera_privacy(self):
        page = self.page
        self._initialize_and_save_connection()
        page.locator("#default-provider").select_option("bedrock")
        page.locator("#default-model").fill("us.test.vision-v1")
        page.locator("#save-defaults").click()
        page.get_by_text("Saved for new sessions.").wait_for()
        page.locator("#sample-interval").fill("15")
        page.locator("#save-interval").click()
        page.get_by_text("Saved. New checks use this interval.").wait_for()
        page.locator("#nav-cooking").click()
        page.locator("#start-session").click()
        page.locator("#session-panel").wait_for(state="visible")
        page.locator("#camera-toggle").click()
        page.locator("#camera-video").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#camera-video').readyState >= 2 && document.querySelector('#camera-video').videoWidth > 0")
        page.evaluate("""() => {
          const originalFetch = window.fetch.bind(window);
          window.__observationFetches = 0;
          window.fetch = (input, init = {}) => {
            const url = typeof input === 'string' ? input : input.url;
            if (!url.includes('/observations')) return originalFetch(input, init);
            window.__observationFetches += 1;
            return new Promise((resolve, reject) => {
              window.__observationStarted = true;
              init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
            });
          };
        }""")
        page.locator("#monitoring-toggle").click()
        page.get_by_text("Guidance on").wait_for()
        page.wait_for_function("window.__intervalDelays.includes(15000)")
        page.locator("#analyze-now").click()
        page.wait_for_function("window.__observationStarted === true")
        page.locator("#nav-settings").click()
        page.locator("#settings-panel").wait_for(state="visible")
        self.assertFalse(page.locator("#analysis-dots").is_visible())
        self.assertNotEqual(page.locator("#observation-panel").get_attribute("aria-busy"), "true")
        self.assertEqual(page.evaluate("window.__observationFetches"), 1)
        page.locator("#sample-interval").fill("1")
        page.locator("#save-interval").click()
        page.get_by_text("Saved. New checks use this interval.").wait_for()
        page.wait_for_timeout(1200)
        self.assertEqual(page.evaluate("window.__observationFetches"), 1)
        page.locator("#nav-cooking").click()
        self.assertTrue(page.locator("#monitoring-toggle").inner_text().startswith("Resume guidance"))
        page.locator("#monitoring-toggle").click()
        page.get_by_text("Guidance on").wait_for()
        page.wait_for_function("window.__intervalDelays.includes(1000)")
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_settings_navigation_invalidates_pending_camera_permission(self):
        page = self.page
        page.locator("#provider-overrides > summary").click()
        page.locator("#model-input").fill("synthetic-model")
        page.locator("#start-session").click()
        page.locator("#session-panel").wait_for(state="visible")
        page.evaluate("""() => {
          window.__resolveCameraRequest = null;
          navigator.mediaDevices.getUserMedia = () => new Promise((resolve) => { window.__resolveCameraRequest = resolve; });
        }""")
        page.locator("#camera-toggle").click()
        page.wait_for_function("typeof window.__resolveCameraRequest === 'function'")
        page.locator("#nav-settings").click()
        page.locator("#settings-panel").wait_for(state="visible")
        page.evaluate("""() => {
          const canvas = document.createElement('canvas');
          canvas.width = 8; canvas.height = 8;
          const stream = canvas.captureStream(1);
          window.__lateCameraTrack = stream.getTracks()[0];
          window.__resolveCameraRequest(stream);
        }""")
        page.wait_for_function("window.__lateCameraTrack.readyState === 'ended'")
        self.assertTrue(page.locator("#camera-video").is_hidden())
        page.locator("#nav-cooking").click()
        self.assertTrue(page.locator("#monitoring-toggle").inner_text().startswith("Resume guidance"))
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])


if __name__ == "__main__":
    unittest.main()
