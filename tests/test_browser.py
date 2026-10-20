"""Optional UI integration tests. Requires isolated Playwright WebKit under /tmp.

Run with SOUS_BROWSER_TESTS=1 and PLAYWRIGHT_BROWSERS_PATH pointing at the
isolated WebKit runtime. The tests never use a persistent browser profile or a
physical camera/microphone.
"""

import os
import json
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import create_app


class SousBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("SOUS_BROWSER_TESTS") != "1":
            raise unittest.SkipTest("Set SOUS_BROWSER_TESTS=1 to run isolated WebKit UI tests.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install requirements-dev.txt before enabling SOUS_BROWSER_TESTS=1.") from exc
        browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        resolved_browser_path = Path(browser_path).resolve() if browser_path else None
        temp_root = Path("/tmp").resolve()
        if (
            not resolved_browser_path
            or not resolved_browser_path.is_dir()
            or temp_root not in resolved_browser_path.parents
        ):
            raise RuntimeError(
                "Set PLAYWRIGHT_BROWSERS_PATH to the isolated /tmp WebKit runtime before enabling browser tests."
            )
        cls.sync_playwright = staticmethod(sync_playwright)

    def setUp(self):
        Path("/private/tmp/sous-webkit-artifacts").mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="sous-browser-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        db_path = os.path.join(self.temp.name, "sous.sqlite3")
        recipe_dir = Path(self.temp.name) / "recipes"
        recipe_dir.mkdir()
        shutil.copyfile(
            Path(__file__).resolve().parents[1] / "recipes" / "00-scrambled-eggs.json",
            recipe_dir / "00-scrambled-eggs.json",
        )
        (recipe_dir / "10-herbed-rice.json").write_text(json.dumps({
            "id": "herbed-rice",
            "name": "Herbed rice",
            "description": "A short test recipe with its own cues.",
            "ingredients": ["Rice", "Herbs"],
            "steps": [
                {"title": "Rinse rice", "instruction": "Rinse until the water runs clear.", "visual_cue": "The water looks mostly clear."},
                {"title": "Simmer gently", "instruction": "Cover and simmer until the grains are tender.", "visual_cue": "Small bubbles rise beneath the lid."},
                {"title": "Fold in herbs", "instruction": "Turn off the heat and fold through the herbs.", "visual_cue": "Green herbs are spread through the rice."},
            ],
        }), encoding="utf-8")
        app = create_app({
            "TESTING": True,
            "DATABASE": db_path,
            "SECRET_KEY": "browser-test-secret",
            "RECIPE_DIR": str(recipe_dir),
        })
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.playwright = self.sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.webkit.launch()
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page_errors = []
        self.console_errors = []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.goto(self.base_url, wait_until="networkidle")
        if self.page.locator("#auth-panel").is_visible():
            self.page.locator("#setup-username").fill("browser-admin")
            self.page.locator("#setup-password").fill("browser-admin-test-password")
            self.page.locator("#admin-setup-form button[type=submit]").click()
        self.page.locator("#setup-panel").wait_for(state="visible")

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=3)
        self.server.server_close()

    def start_session(self, model="llava", recipe_id=None):
        if recipe_id:
            self.page.locator("#recipe-select").select_option(recipe_id)
        if not self.page.locator("#provider-overrides").evaluate("element => element.open"):
            self.page.locator("#provider-overrides > summary").click()
        self.page.locator("#model-input").fill(model)
        self.page.locator("#start-session").click()
        self.page.locator("#session-panel").wait_for(state="visible")

    def assert_clean_browser(self):
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def prepare_page_top(self):
        result = self.page.evaluate("""
          async () => {
            document.activeElement?.blur();
            window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
            await new Promise(requestAnimationFrame);
            await new Promise(requestAnimationFrame);
            return window.scrollY;
          }
        """)
        self.assertEqual(result, 0, f"Expected page to settle at the top, got scrollY={result}")

    def assert_cook_column_starts_with_workspace(self):
        layout = self.page.evaluate("""
          () => {
            const workspace = document.querySelector('.workspace').getBoundingClientRect();
            const cook = document.querySelector('.cook-column').getBoundingClientRect();
            return { scrollY: window.scrollY, workspaceTop: workspace.top, cookTop: cook.top };
          }
        """)
        self.assertEqual(layout["scrollY"], 0, f"Desktop layout check requires page top: {layout}")
        self.assertLessEqual(
            abs(layout["cookTop"] - layout["workspaceTop"]), 1,
            f"Cook column should align with the workspace at page top: {layout}",
        )

    def assert_no_horizontal_overflow(self, label):
        result = self.page.evaluate("""
          () => {
            const root = document.documentElement;
            const width = root.clientWidth;
            const offenders = [...document.querySelectorAll('body *')]
              .filter(element => {
                if (element.closest('.visually-hidden, .skip-link, [hidden]')) return false;
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && (rect.right > width + 1 || rect.left < -1);
              })
              .slice(0, 5)
              .map(element => `${element.tagName.toLowerCase()}#${element.id}.${element.className}`);
            return { scrollWidth: Math.max(root.scrollWidth, document.body.scrollWidth), clientWidth: width, offenders };
          }
        """)
        self.assertLessEqual(
            result["scrollWidth"], result["clientWidth"],
            f"{label} overflows horizontally: {result}",
        )
        self.assertEqual(result["offenders"], [], f"{label} has elements outside the viewport: {result}")

    def assert_correct_form_fully_visible(self, label):
        page = self.page
        page.locator("#correct-details summary").click()
        page.locator("#correct-form").wait_for(state="visible")
        page.locator("#correct-form").scroll_into_view_if_needed()
        visible = page.evaluate("""
          () => {
            const form = document.querySelector('#correct-form');
            const rect = form.getBoundingClientRect();
            const width = document.documentElement.clientWidth;
            const inside = rect.left >= 0 && rect.right <= width && rect.width > 0 && rect.height > 0;
            const points = [
              [rect.left + 4, rect.top + 4], [rect.right - 4, rect.top + 4],
              [rect.left + 4, rect.bottom - 4], [rect.right - 4, rect.bottom - 4],
            ];
            const unclipped = points.every(([x, y]) => {
              const hit = document.elementFromPoint(x, y);
              // A transient toast is fixed above content by design; it does not clip the form.
              return hit && (form.contains(hit) || Boolean(hit.closest('#toast')));
            });
            return { inside, unclipped, rect: [rect.left, rect.top, rect.right, rect.bottom], width };
          }
        """)
        self.assertTrue(visible["inside"] and visible["unclipped"], f"{label} correct-step form is clipped: {visible}")
        page.locator("#correct-details summary").click()
        page.wait_for_function("!document.querySelector('#correct-details').open")

    def test_setup_layout_previews_recipe_without_horizontal_overflow(self):
        page = self.page
        page.locator("#recipe-peek .peek-title").wait_for()
        page.locator("#recipe-select").select_option("herbed-rice")
        page.wait_for_function("document.querySelector('#recipe-peek .peek-title')?.textContent === 'Herbed rice'")
        self.assertIn("3 steps", page.locator("#recipe-peek .peek-meta").inner_text())
        self.assertEqual(page.locator("#recipe-peek .peek-steps li").all_text_contents(), ["Rinse rice", "Simmer gently", "Fold in herbs"])
        page.locator("#recipe-select").select_option("scrambled-eggs")
        page.wait_for_function("document.querySelector('#recipe-peek .peek-title')?.textContent !== 'Herbed rice'")

        self.prepare_page_top()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/setup-desktop.png", full_page=True)
        self.assert_no_horizontal_overflow("Setup at desktop width")

        page.set_viewport_size({"width": 390, "height": 844})
        self.prepare_page_top()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/setup-mobile.png", full_page=True)
        self.assert_no_horizontal_overflow("Setup at 390px")
        self.assertTrue(page.locator("#start-session").is_visible())
        self.assertTrue(page.locator("#start-session").is_enabled())
        self.assert_clean_browser()

    def test_skip_link_is_hidden_until_keyboard_focus(self):
        link = self.page.locator(".skip-link")
        hit_testable = self.page.evaluate("""
          () => {
            const link = document.querySelector('.skip-link');
            const rect = link.getBoundingClientRect();
            const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2));
            const y = Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2));
            const hit = document.elementFromPoint(x, y);
            return link === document.activeElement || link === hit || link.contains(hit);
          }
        """)
        self.assertFalse(hit_testable, "Unfocused skip link should not cover visible page content.")

        # macOS WebKit includes links in keyboard navigation with Option+Tab.
        self.page.keyboard.press("Alt+Tab")
        self.assertTrue(
            link.evaluate("element => element === document.activeElement"),
            f"Keyboard navigation should focus the skip link; active element is {self.page.evaluate('document.activeElement?.outerHTML')}",
        )
        self.assertTrue(link.evaluate("""
          element => {
            const rect = element.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            const hit = document.elementFromPoint(x, y);
            return rect.width > 0 && rect.height > 0 && hit === element;
          }
        """))

    def test_manual_cooking_timer_recovery_and_completion(self):
        page = self.page
        self.start_session()
        page.locator("#next-button").click()
        page.get_by_role("heading", name="Crack and whisk", level=2).wait_for()
        page.locator("#back-button").click()
        page.get_by_role("heading", name="Gather ingredients", level=2).wait_for()

        page.locator("#correct-details summary").click()
        page.locator("#correct-select").select_option("2")
        page.locator("#correct-note").fill("Butter is already melted")
        page.get_by_role("button", name="Update step").click()
        page.get_by_role("heading", name="Warm pan and add butter", level=2).wait_for()

        page.locator("#command-input").fill("set timer for 1 second")
        page.locator("#command-form button[type=submit]").click()
        page.wait_for_function("document.querySelector('#timer-status').textContent === 'Counting down'")
        page.wait_for_function("document.querySelector('#timer-status').textContent === 'Timer finished'", timeout=5000)

        self.prepare_page_top()
        self.assert_cook_column_starts_with_workspace()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/cooking-desktop.png", full_page=True)
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/cooking-desktop-viewport.png")
        self.assert_no_horizontal_overflow("Cooking at desktop width")
        self.assert_correct_form_fully_visible("Desktop")
        page.set_viewport_size({"width": 390, "height": 844})
        self.prepare_page_top()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/cooking-mobile.png", full_page=True)
        self.assert_no_horizontal_overflow("Cooking at 390px")
        self.assert_correct_form_fully_visible("Mobile")

        page.reload(wait_until="networkidle")
        page.locator("#session-panel").wait_for(state="visible")
        page.get_by_text("Welcome back.").wait_for()
        page.locator("#restore-resume").wait_for(state="visible")
        page.locator("#correct-details summary").click()
        page.locator("#correct-select").select_option("5")
        page.get_by_role("button", name="Update step").click()
        page.get_by_role("heading", name="Transfer to plate", level=2).wait_for()
        page.locator("#finish-button").click()
        page.locator("#completion-panel").wait_for(state="visible")
        page.get_by_role("heading", name="Cooking complete.").wait_for()
        page.set_viewport_size({"width": 1280, "height": 900})
        self.prepare_page_top()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/completion-desktop.png", full_page=True)
        self.assert_no_horizontal_overflow("Completion at desktop width")

        page.set_viewport_size({"width": 390, "height": 844})
        self.prepare_page_top()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/completion-mobile.png", full_page=True)
        self.assert_no_horizontal_overflow("Completion at 390px")
        self.assertTrue(page.locator("#new-session").is_visible())
        page.set_viewport_size({"width": 1280, "height": 900})

        page.get_by_role("button", name="Start another session").click()
        self.start_session()
        page.locator("#end-session").click()
        page.locator("#completion-panel").wait_for(state="visible")
        page.get_by_role("heading", name="Session ended for now.").wait_for()
        self.assert_clean_browser()

    def test_camera_permission_failure_keeps_manual_controls(self):
        self.page.evaluate("""
            Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
              getUserMedia: async () => { const error = new Error('blocked'); error.name = 'NotAllowedError'; throw error; }
            }});
        """)
        self.assertIn("NotAllowedError", self.page.evaluate("navigator.mediaDevices.getUserMedia.toString()"))
        self.start_session()
        self.page.locator("#camera-toggle").click()
        self.page.wait_for_function(
            "document.querySelector('#camera-message').textContent.includes('Camera permission was declined.')"
        )
        self.page.locator("#next-button").click()
        self.page.get_by_role("heading", name="Crack and whisk", level=2).wait_for()
        self.assert_clean_browser()

    def test_recipe_snapshot_survives_catalog_change_and_finishes_at_any_step_count(self):
        page = self.page
        self.start_session(recipe_id="herbed-rice")
        self.assertEqual(page.locator("#session-recipe-name").text_content().strip(), "Herbed rice")
        self.assertEqual(page.locator("#step-count").inner_text(), "Step 1 of 3")
        self.assertEqual(page.locator("#step-title").inner_text(), "Rinse rice")
        self.assertEqual(page.locator("#step-instruction").inner_text(), "Rinse until the water runs clear.")
        self.assertEqual(page.locator("#visual-cue-text").inner_text(), "The water looks mostly clear.")
        page.locator("#next-button").click()
        page.get_by_role("heading", name="Simmer gently", level=2).wait_for()
        self.assertEqual(page.locator("#step-title").inner_text(), "Simmer gently")

        changed_catalog = page.evaluate("fetch('/api/config').then(response => response.json())")
        changed_catalog["recipes"] = [recipe for recipe in changed_catalog["recipes"] if recipe["id"] != "herbed-rice"]
        page.route(
            "**/api/config",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(changed_catalog)),
        )
        page.reload(wait_until="networkidle")
        page.locator("#restore-notice").wait_for(state="visible")
        self.assertEqual(page.locator("#session-recipe-name").text_content().strip(), "Herbed rice")
        self.assertEqual(page.locator("#step-count").inner_text(), "Step 2 of 3")
        self.assertEqual(page.locator("#step-instruction").inner_text(), "Cover and simmer until the grains are tender.")
        self.assertEqual(page.locator("#visual-cue-text").inner_text(), "Small bubbles rise beneath the lid.")
        page.locator("#next-button").click()
        page.get_by_role("heading", name="Fold in herbs", level=2).wait_for()
        self.assertEqual(page.locator("#step-count").inner_text(), "Step 3 of 3")
        self.assertEqual(page.locator("#step-title").inner_text(), "Fold in herbs")
        page.locator("#finish-button").click()
        page.locator("#completion-panel").wait_for(state="visible")
        self.assert_clean_browser()

    def test_delayed_camera_permission_cannot_attach_to_replacement_session(self):
        page = self.page
        page.evaluate("""
            window.__cameraResolve = null;
            window.__cameraStopped = false;
            window.__fakeCameraTrack = { stop: () => { window.__cameraStopped = true; } };
            Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
              getUserMedia: () => new Promise(resolve => { window.__cameraResolve = resolve; })
            }});
        """)
        self.start_session()
        page.locator("#camera-toggle").click()
        page.wait_for_function("typeof window.__cameraResolve === 'function'")
        page.locator("#end-session").click()
        page.locator("#completion-panel").wait_for(state="visible")
        page.get_by_role("button", name="Start another session").click()
        page.locator("#setup-panel").wait_for(state="visible")
        self.start_session()
        page.evaluate("window.__cameraResolve({ getTracks: () => [window.__fakeCameraTrack] })")
        page.wait_for_function("window.__cameraStopped === true")
        self.assertEqual(page.locator("#camera-toggle").inner_text(), "Turn on camera")
        self.assertTrue(page.locator("#camera-video").is_hidden())
        self.assertTrue(page.locator("#camera-video").evaluate("video => video.srcObject === null"))
        self.assert_clean_browser()

    def test_synthetic_camera_observation_uses_api_and_renders_result(self):
        if not self.page.evaluate("typeof HTMLCanvasElement.prototype.captureStream === 'function'"):
            self.skipTest("This WebKit runtime does not support synthetic canvas captureStream.")

        sentinel = object()
        previous = sys.modules.get("providers", sentinel)
        fake = types.ModuleType("providers")
        fake.analyze = lambda provider, model, jpeg, context: {
            "activity": "Synthetic browser test frame",
            "evidence": "A generated color field is visible in the test camera.",
            "confidence": 0.62,
            "step_complete": False,
            "alert": None,
        }
        sys.modules["providers"] = fake
        self.addCleanup(self._restore_provider_module, previous, sentinel)
        self.page.evaluate("""
            Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
              getUserMedia: async () => {
                const canvas = document.createElement('canvas');
                canvas.width = 320; canvas.height = 180;
                const context = canvas.getContext('2d');
                context.fillStyle = '#8aaf8d'; context.fillRect(0, 0, 320, 180);
                return canvas.captureStream(2);
              }
            }});
        """)
        self.assertIn("captureStream", self.page.evaluate("navigator.mediaDevices.getUserMedia.toString()"))
        self.start_session()
        self.page.locator("#camera-toggle").click()
        self.page.locator("#camera-video").wait_for(state="visible")
        self.page.wait_for_function("document.querySelector('#camera-video').readyState >= 2", timeout=5000)
        self.page.locator("#monitoring-toggle").click()
        self.page.get_by_text("Guidance on").wait_for()
        self.page.locator("#timer-minutes").fill("0.02")
        self.page.locator("#timer-form button[type=submit]").click()
        self.page.wait_for_function("document.querySelector('#timer-status').textContent === 'Counting down'")
        self.page.evaluate("""
          (() => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, init) => {
              const url = typeof input === 'string' ? input : input.url;
              return originalFetch(input, init).then(async response => {
                if (url.includes('/observations')) await new Promise(resolve => setTimeout(resolve, 1600));
                return response;
              });
            };
          })();
        """)
        self.page.locator("#analyze-now").click()
        self.page.get_by_text("Synthetic browser test frame").wait_for(timeout=10000)
        self.page.wait_for_function("document.querySelector('#timer-status').textContent === 'Timer finished'", timeout=5000)
        self.page.wait_for_function("document.querySelector('#camera-message').textContent === 'Look complete.'", timeout=10000)
        self.assertIn("Look complete", self.page.locator("#camera-message").inner_text())
        event_messages = self.page.locator("#event-list li p").all_text_contents()
        self.assertEqual(event_messages.count("Timer finished."), 1)
        self.assert_clean_browser()

    def test_pending_observation_shows_thinking_dots_until_terminal_state(self):
        page = self.page
        if not page.evaluate("typeof HTMLCanvasElement.prototype.captureStream === 'function'"):
            self.skipTest("This WebKit runtime does not support synthetic canvas captureStream.")

        sentinel = object()
        previous = sys.modules.get("providers", sentinel)
        fake = types.ModuleType("providers")
        fake.analyze = lambda provider, model, jpeg, context: {
            "activity": "Synthetic pending frame",
            "evidence": "A generated color field is visible in the test camera.",
            "confidence": 0.55,
            "step_complete": False,
            "alert": None,
        }
        sys.modules["providers"] = fake
        self.addCleanup(self._restore_provider_module, previous, sentinel)
        page.evaluate("""
            Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
              getUserMedia: async () => {
                const canvas = document.createElement('canvas');
                canvas.width = 320; canvas.height = 180;
                const context = canvas.getContext('2d');
                context.fillStyle = '#9aa3ad'; context.fillRect(0, 0, 320, 180);
                return canvas.captureStream(2);
              }
            }});
        """)
        self.start_session()
        page.locator("#camera-toggle").click()
        page.locator("#camera-video").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#camera-video').readyState >= 2", timeout=5000)
        # Camera warmup is not model analysis.
        self.assertTrue(page.locator("#analysis-dots").is_hidden())
        page.locator("#monitoring-toggle").click()
        page.get_by_text("Guidance on").wait_for()

        # Hold each observation request until the test releases it, fails it, or the app aborts it.
        page.evaluate("""
          (() => {
            const originalFetch = window.fetch.bind(window);
            window.__holds = [];
            window.fetch = (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (!url.includes('/observations')) return originalFetch(input, init);
              return new Promise((resolve, reject) => {
                const signal = init.signal;
                const onAbort = () => reject(new DOMException('Aborted', 'AbortError'));
                signal?.addEventListener('abort', onAbort, { once: true });
                window.__holds.push({
                  signal,
                  release: () => { signal?.removeEventListener('abort', onAbort); originalFetch(input, init).then(resolve, reject); },
                  fail: () => resolve(new Response(JSON.stringify({ error: 'Synthetic provider failure.' }), {
                    status: 502, headers: { 'Content-Type': 'application/json' },
                  })),
                });
              });
            };
          })();
        """)

        def start_pending(count):
            page.wait_for_function("!document.querySelector('#analyze-now').disabled", timeout=5000)
            page.locator("#analyze-now").click()
            page.wait_for_function(f"window.__holds.length === {count}")
            self.assertTrue(page.locator("#analysis-dots").is_visible())
            self.assertEqual(page.locator("#analysis-dots").get_attribute("aria-hidden"), "true")
            self.assertEqual(page.locator("#camera-caption").inner_text(), "Analyzing")
            self.assertEqual(page.locator("#observation-panel").get_attribute("aria-busy"), "true")
            self.assertIn("Analyzing", page.locator("#camera-message").inner_text())
            self.assertTrue(page.locator("#analyze-now").is_disabled())

        def assert_idle():
            self.assertTrue(page.locator("#analysis-dots").is_hidden())
            self.assertIsNone(page.locator("#observation-panel").get_attribute("aria-busy"))
            self.assertNotEqual(page.locator("#camera-caption").inner_text(), "Analyzing")

        # Pending, then success.
        start_pending(1)
        self.assertEqual(
            page.locator("#analysis-dots span").first.evaluate("dot => getComputedStyle(dot).animationName"),
            "thinking",
        )
        page.locator(".camera-panel").scroll_into_view_if_needed()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/analysis-desktop.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator(".camera-panel").scroll_into_view_if_needed()
        page.screenshot(path="/private/tmp/sous-webkit-artifacts/analysis-mobile.png")
        self.assert_no_horizontal_overflow("Pending analysis at 390px")
        page.set_viewport_size({"width": 1280, "height": 900})
        self.assertTrue(page.locator("#analysis-dots").is_visible())
        page.evaluate("window.__holds[0].release()")
        page.wait_for_function(
            "document.querySelector('#observation-content .observation-text')?.textContent === 'Synthetic pending frame'",
            timeout=10000,
        )
        page.wait_for_function("document.querySelector('#camera-message').textContent === 'Look complete.'", timeout=10000)
        assert_idle()
        self.assertEqual(page.locator("#camera-caption").inner_text(), "Camera preview")

        # Pending keeps the latest observation, then provider failure.
        observation = page.locator("#observation-content .observation-text")
        start_pending(2)
        self.assertEqual(observation.inner_text(), "Synthetic pending frame")
        page.evaluate("window.__holds[1].fail()")
        page.wait_for_function("document.querySelector('#camera-message').textContent.includes('Synthetic provider failure.')")
        assert_idle()
        self.assertEqual(observation.inner_text(), "Synthetic pending frame")

        # Pending, then cancelled by turning off the camera.
        start_pending(3)
        page.locator("#camera-toggle").click()
        page.wait_for_function("window.__holds[2].signal.aborted")
        page.wait_for_function("document.querySelector('#camera-caption').textContent === 'Camera is off'")
        assert_idle()
        self.assertEqual(page.locator("#camera-message").inner_text(), "")
        self.assertTrue(page.locator("#analyze-now").is_disabled())
        page.locator("#camera-toggle").click()
        page.locator("#camera-video").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#camera-video').readyState >= 2", timeout=5000)

        # Pending, then cancelled by pausing guidance.
        start_pending(4)
        page.locator("#monitoring-toggle").click()
        page.get_by_text("Guidance paused").wait_for()
        page.wait_for_function("document.querySelector('#camera-caption').textContent === 'Camera preview'")
        assert_idle()
        self.assertEqual(page.locator("#camera-message").inner_text(), "")
        self.assertEqual(observation.inner_text(), "Synthetic pending frame")

        # Reduced motion keeps static dots; ending the session clears them.
        page.emulate_media(reduced_motion="reduce")
        page.locator("#monitoring-toggle").click()
        page.get_by_text("Guidance on").wait_for()
        start_pending(5)
        self.assertEqual(
            page.locator("#analysis-dots span").first.evaluate("dot => getComputedStyle(dot).animationName"),
            "none",
        )
        page.locator("#end-session").click()
        page.locator("#completion-panel").wait_for(state="visible")
        self.assertTrue(page.locator("#analysis-dots").is_hidden())
        self.assertIsNone(page.locator("#observation-panel").get_attribute("aria-busy"))
        self.assert_clean_browser()

    def test_optional_live_local_ollama_observation(self):
        if os.environ.get("SOUS_LIVE_OLLAMA") != "1":
            self.skipTest("Set SOUS_LIVE_OLLAMA=1 to call the configured local Ollama model.")
        if not self.page.evaluate("typeof HTMLCanvasElement.prototype.captureStream === 'function'"):
            self.skipTest("This WebKit runtime does not support synthetic canvas captureStream.")
        self.page.evaluate("""
            Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
              getUserMedia: async () => {
                const canvas = document.createElement('canvas');
                canvas.width = 320; canvas.height = 180;
                const context = canvas.getContext('2d');
                context.fillStyle = '#c7b69a'; context.fillRect(0, 0, 320, 180);
                return canvas.captureStream(2);
              }
            }});
        """)
        self.assertIn("captureStream", self.page.evaluate("navigator.mediaDevices.getUserMedia.toString()"))
        self.start_session(model="qwen3-vl:8b")
        self.page.locator("#camera-toggle").click()
        self.page.locator("#camera-video").wait_for(state="visible")
        self.page.wait_for_function("document.querySelector('#camera-video').readyState >= 2", timeout=5000)
        self.page.locator("#monitoring-toggle").click()
        self.page.get_by_text("Guidance on").wait_for()
        self.page.locator("#analyze-now").click()
        self.page.wait_for_function("""
          (() => {
            const observation = document.querySelector('#observation-content .observation-text');
            const status = document.querySelector('#camera-message')?.textContent || '';
            return Boolean(observation?.textContent.trim()) || /failed|unusable|not available/i.test(status);
          })()
        """, timeout=195000)
        observation = self.page.locator("#observation-content .observation-text")
        self.assertTrue(
            observation.is_visible(),
            f"Local Ollama did not return a usable observation: {self.page.locator('#camera-message').inner_text()}",
        )
        self.assertTrue(self.page.locator("#confidence-pill").is_visible())
        self.assert_clean_browser()

    @staticmethod
    def _restore_provider_module(previous, sentinel):
        if previous is sentinel:
            sys.modules.pop("providers", None)
        else:
            sys.modules["providers"] = previous


if __name__ == "__main__":
    unittest.main()
