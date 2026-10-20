"""Real account-cookie UI flow with synthetic camera tracks and no provider calls."""

import os
import base64
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path


@unittest.skipUnless(os.environ.get("SOUS_BROWSER_TESTS") == "1", "Set SOUS_BROWSER_TESTS=1 to run isolated WebKit UI tests.")
class SousV2AccountBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if not browser_path or Path("/tmp").resolve() not in Path(browser_path).resolve().parents:
            raise RuntimeError("Set PLAYWRIGHT_BROWSERS_PATH to the isolated /tmp WebKit runtime.")
        from playwright.sync_api import expect, sync_playwright
        cls.sync_playwright = staticmethod(sync_playwright)
        cls.expect = staticmethod(expect)

    def setUp(self):
        from app import create_app
        from werkzeug.serving import make_server

        self.temp = tempfile.TemporaryDirectory(prefix="sous-v2-accounts-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        recipe_dir = Path(self.temp.name) / "recipes"
        recipe_dir.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1] / "recipes" / "00-scrambled-eggs.json", recipe_dir / "00-scrambled-eggs.json")
        self.app = create_app({"TESTING": True, "DATABASE": os.path.join(self.temp.name, "accounts.sqlite3"), "SECRET_KEY": "synthetic-account-browser-key", "RECIPE_DIR": str(recipe_dir)})
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        self.track_stops = 0
        self.playwright = self.sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.webkit.launch()
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.context.expose_binding("notifySyntheticTrackStopped", lambda _source: self._track_stopped())
        self.context.add_init_script("""
          (() => {
            Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
              getUserMedia: async () => {
                const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 240;
                const stream = canvas.captureStream(1);
                stream.getTracks().forEach(track => {
                  const stop = track.stop.bind(track);
                  track.stop = () => { window.notifySyntheticTrackStopped(); stop(); };
                });
                return stream;
              }
            }});
          })();
        """)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page_errors = []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}", wait_until="networkidle")

    def _track_stopped(self):
        self.track_stops += 1

    def stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=3)
        self.server.server_close()

    def create_first_admin(self):
        self.page.locator("#admin-setup-form").wait_for(state="visible")
        self.page.locator("#setup-username").fill("kitchen-admin")
        self.page.locator("#setup-password").fill("kitchen-admin-password-long")
        self.page.locator("#admin-setup-form button[type=submit]").click()
        self.page.locator("#setup-panel").wait_for(state="visible")

    def login(self, username, password):
        self.page.locator("#login-form").wait_for(state="visible")
        self.page.locator("#login-username").fill(username)
        self.page.locator("#login-password").fill(password)
        self.page.locator("#login-form button[type=submit]").click()
        self.page.locator("#account-name").filter(has_text=username).wait_for(state="visible")

    def logout(self):
        self.page.locator("#logout-button").click()
        self.page.locator("#login-form").wait_for(state="visible")

    def test_real_account_setup_switching_private_data_and_media_cleanup(self):
        self.create_first_admin()

        self.page.locator("#nav-settings").click()
        self.page.locator("#new-user-name").fill("second-cook")
        self.page.locator("#new-user-password").fill("second-cook-password-long")
        self.page.locator("#user-create-form button[type=submit]").click()
        self.page.get_by_text("second-cook", exact=True).wait_for()

        self.page.locator("#nav-recipes").click()
        self.page.locator("#new-recipe-button").click()
        self.page.locator("#edit-recipe-name").fill("Admin private supper")
        self.page.locator("#edit-recipe-description").fill("A private account recipe.")
        self.page.locator(".ingredient-row:first-child .ingredient-name").fill("Rice")
        self.page.locator(".step-editor-row:first-child .step-name").fill("Cook rice")
        self.page.locator(".step-editor-row:first-child .step-instruction-edit").fill("Simmer until tender.")
        self.page.locator(".step-editor-row:first-child .step-cue").fill("The grains are tender.")
        self.page.locator(".step-editor-row:nth-child(2) [data-action='remove-row']").click()
        self.page.locator("#recipe-form button[type=submit]").click()
        self.page.locator("#recipe-library .recipe-entry h2", has_text="Admin private supper").wait_for()

        self.logout()
        self.login("second-cook", "second-cook-password-long")
        self.page.locator("#nav-recipes").click()
        self.page.wait_for_function("!document.querySelector('#recipes-panel').hidden")
        self.assertNotIn("Admin private supper", self.page.locator("#recipe-library").inner_text())
        self.page.locator("#nav-settings").click()
        self.assertTrue(self.page.locator("#user-admin-panel").is_hidden())

        self.logout()
        self.login("kitchen-admin", "kitchen-admin-password-long")
        self.page.locator("#nav-recipes").click()
        self.page.locator("#recipe-library .recipe-entry h2", has_text="Admin private supper").wait_for()
        self.assertEqual(self.page.evaluate("() => JSON.stringify([Object.keys(localStorage), Object.keys(sessionStorage)])"), "[[],[]]")

        self.page.locator("#nav-cooking").click()
        if self.page.locator("#setup-panel").is_visible():
            self.page.locator("#recipe-select").select_option(label="Admin private supper")
            if not self.page.locator("#provider-overrides").evaluate("element => element.open"):
                self.page.locator("#provider-overrides > summary").click()
            self.page.locator("#model-input").fill("synthetic-no-call")
            self.page.locator("#start-session").click()
        self.page.locator("#session-panel").wait_for(state="visible")
        self.page.locator("#camera-toggle").click()
        self.page.locator("#camera-video").wait_for(state="visible")
        self.logout()
        self.assertGreaterEqual(self.track_stops, 1, "Logout must stop every active synthetic camera track.")
        self.assertEqual(self.page_errors, [])

    def test_real_cooking_tools_advisory_and_history_persist(self):
        expect = self.expect
        self.create_first_admin()
        if not self.page.locator("#provider-overrides").evaluate("element => element.open"):
            self.page.locator("#provider-overrides > summary").click()
        self.page.locator("#provider-select").select_option("ollama")
        self.page.locator("#model-input").fill("synthetic-no-call")
        self.page.locator("#start-session").click()
        expect(self.page.locator("#session-panel")).to_be_visible()

        origin = self.page.evaluate("location.origin")
        csrf = self.page.locator('meta[name="csrf-token"]').get_attribute("content")
        headers = {"X-CSRF-Token": csrf}
        active_response = self.page.request.get(f"{origin}/api/sessions/active")
        self.assertEqual(active_response.status, 200)
        session = active_response.json()["session"]
        session_id = session["id"]
        checklist = self.page.locator("#prep-checklist input[type=checkbox]")
        expect(checklist.first).to_be_visible()
        with self.page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith(f"/api/sessions/{session_id}/checklist")):
            checklist.first.check()
        checklist_response = self.page.request.get(f"{origin}/api/sessions/{session_id}/checklist")
        self.assertTrue(any(item["checked"] for item in checklist_response.json()["checklist"]))

        self.page.locator("#named-timer-name").fill("Tea")
        self.page.locator("#named-timer-minutes").fill("1")
        self.page.locator("#named-timer-form button[type=submit]").click()
        tea = self.page.locator("#named-timer-list li").filter(has_text="Tea")
        expect(tea).to_contain_text("remaining")
        tea.get_by_role("button", name="Cancel").click()
        expect(tea).to_contain_text("Cancelled")
        timer_response = self.page.request.get(f"{origin}/api/sessions/{session_id}/timers")
        self.assertEqual(next(item for item in timer_response.json()["timers"] if item["name"] == "Tea")["status"], "cancelled")

        resume_response = self.page.request.post(
            f"{origin}/api/sessions/{session_id}/actions", data={"action": "resume"}, headers=headers
        )
        self.assertEqual(resume_response.status, 200)
        revision = resume_response.json()["revision"]
        observation = {
            "activity": "A pan is on the counter.",
            "evidence": "The current step is not visibly complete.",
            "confidence": 0.2,
            "step_complete": False,
            "alert": None,
            "safety_alert": {
                "category": "hazard",
                "title": "Pan handle near a towel",
                "evidence": "The pan handle appears close to a hanging towel.",
            },
            "view_obstructed": True,
        }
        with patch("providers.analyze", return_value=observation):
            observation_response = self.page.request.post(
                f"{origin}/api/sessions/{session_id}/observations",
                data={"revision": revision, "image": "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffsynthetic-frame").decode("ascii")},
                headers=headers,
            )
        self.assertEqual(observation_response.status, 200, observation_response.text())
        observed_session = observation_response.json()
        self.page.evaluate("session => document.dispatchEvent(new CustomEvent('sous:session', { detail: { session } }))", observed_session)
        expect(self.page.locator("#monitoring-health")).to_have_attribute("data-state", "camera_off")
        alert = self.page.locator("#advisory-alerts .advisory-alert")
        expect(alert).to_contain_text("Possible hazard")
        expect(alert).to_contain_text("Pan handle near a towel")
        expect(alert).to_contain_text("The pan handle appears close to a hanging towel.")

        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("#session-panel")).to_be_visible()
        expect(self.page.locator("#advisory-alerts .advisory-alert")).to_contain_text("Pan handle near a towel")
        expect(self.page.locator("#prep-checklist input[type=checkbox]:checked")).to_have_count(1)
        tea = self.page.locator("#named-timer-list li").filter(has_text="Tea")
        expect(tea).to_contain_text("Cancelled")
        alert_button = self.page.locator("#advisory-alerts").get_by_role("button", name="Acknowledge alert")
        alert_button.click()
        expect(self.page.locator("#advisory-alerts .advisory-alert")).to_have_count(0)
        active_response = self.page.request.get(f"{origin}/api/sessions/active")
        saved_alert = active_response.json()["session"]["alerts"][0]
        self.assertIsNotNone(saved_alert["acknowledged_at"])

        self.page.locator("#end-session").click()
        expect(self.page.locator("#completion-panel")).to_be_visible()
        self.page.locator("#nav-history").click()
        history_entry = self.page.locator("#history-list button").filter(has_text="Scrambled Eggs")
        expect(history_entry).to_be_visible()
        history_entry.click()
        expect(self.page.locator("#history-detail")).to_be_visible()
        self.page.locator("#history-note").fill("Synthetic browser note for this cook.")
        self.page.locator("#history-note-form button[type=submit]").click()
        expect(self.page.locator("#history-note-status")).to_contain_text("Note saved privately")
        self.page.reload(wait_until="networkidle")
        self.page.locator("#nav-history").click()
        history_entry = self.page.locator("#history-list button").filter(has_text="Scrambled Eggs")
        expect(history_entry).to_be_visible()
        history_entry.click()
        expect(self.page.locator("#history-note")).to_have_value("Synthetic browser note for this cook.")
        self.assertEqual(self.page_errors, [])


if __name__ == "__main__":
    unittest.main()
