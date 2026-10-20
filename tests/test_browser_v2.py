"""V2 UI smoke coverage with temporary API fixtures; no real credentials or model calls."""

import os
import unittest
from pathlib import Path

@unittest.skipUnless(os.environ.get("SOUS_BROWSER_TESTS") == "1", "Set SOUS_BROWSER_TESTS=1 to run isolated WebKit UI tests.")
class SousV2BrowserTests(unittest.TestCase):
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
        import tempfile
        import threading
        self.temp = tempfile.TemporaryDirectory(prefix="sous-v2-browser-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        app = create_app({"TESTING": True, "DATABASE": os.path.join(self.temp.name, "v2.sqlite3"), "SECRET_KEY": "synthetic-v2-browser-key"})
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop_server)
        self.playwright = self.sync_playwright().start(); self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.webkit.launch(); self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844}); self.addCleanup(self.context.close)
        self.logged_in = False; self.account_created = False; self.saved = False; self.page = self.context.new_page()
        self.cooking_catalog = [{
            "id": "scrambled-eggs", "name": "Scrambled Eggs", "description": "A synthetic test recipe.",
            "ingredients": ["Eggs"], "steps": [{"title": "Cook", "instruction": "Cook gently.", "visual_cue": "The eggs are set."}],
        }]
        import time
        self.timer_fixture = [
            {"id": "timer-rice", "name": "Rice", "deadline_at": 1, "status": "elapsed", "acknowledged_at": None},
            {"id": "timer-pasta", "name": "Pasta", "deadline_at": time.time() + 3600, "status": "running", "acknowledged_at": None},
            {"id": "timer-soup", "name": "Soup", "deadline_at": time.time() + 5.5, "status": "running", "acknowledged_at": None},
        ]
        self.page_errors = []; self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.add_init_script("""(() => {
          const nativeFetch = window.fetch.bind(window);
          window.__timerFetchStats = { active: 0, maxConcurrent: 0, total: 0 };
          window.__logoutRequestStarted = false; window.__logoutResponseReturned = false;
          window.__delayCoreConfig = false; window.__coreConfigStarted = false; window.__releaseCoreConfig = null;
          window.fetch = async (input, init = {}) => {
            const url = typeof input === 'string' ? input : input.url;
            const method = (init.method || 'GET').toUpperCase();
            if (method === 'POST' && url.endsWith('/api/auth/logout')) {
              window.__logoutRequestStarted = true;
              await new Promise(resolve => setTimeout(resolve, 2000));
              const response = await nativeFetch(input, init);
              window.__logoutResponseReturned = true;
              return response;
            }
            if (method === 'GET' && url.endsWith('/api/config') && window.__delayCoreConfig) {
              window.__coreConfigStarted = true;
              return new Promise(resolve => {
                window.__releaseCoreConfig = () => resolve(new Response('{"recipes":[]}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
              });
            }
            if (method === 'GET' && /\\/api\\/sessions\\/timer-session\\/timers(?:\\?|$)/.test(url)) {
              const stats = window.__timerFetchStats;
              stats.active += 1; stats.total += 1; stats.maxConcurrent = Math.max(stats.maxConcurrent, stats.active);
              try { await new Promise(resolve => setTimeout(resolve, 1250)); return await nativeFetch(input, init); }
              finally { stats.active -= 1; }
            }
            return nativeFetch(input, init);
          };
        })();""")
        self.context.route("**/api/auth", self.auth_fixture)
        self.context.route("**/api/auth/setup", self.auth_setup_fixture)
        self.context.route("**/api/auth/logout", self.auth_logout_fixture)
        self.context.route("**/api/settings", self.settings_fixture)
        self.context.route("**/api/config", self.config_fixture)
        self.context.route("**/api/sessions/active", lambda route: route.fulfill(json={"session": None}))
        self.context.route("**/api/recipes/generate", self.generate_fixture)
        self.context.route("**/api/recipes", self.recipe_fixture)
        self.context.route("**/api/profile", lambda route: route.fulfill(json={"pantry": ["Rice"], "equipment": ["Pot"], "servings": 2, "time_budget_minutes": 30}))
        self.context.route("**/api/users", lambda route: route.fulfill(json={"users": [{"id": "u1", "username": "cook", "is_admin": True}]}))
        self.context.route("**/api/sessions/timer-session/timers**", self.timer_fixture_route)
        self.context.route("**/api/sessions/timer-session/timers/*/ack", self.timer_action_fixture)
        self.context.route("**/api/sessions/timer-session/timers/*/cancel", self.timer_action_fixture)
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}", wait_until="networkidle")

    def stop_server(self):
        self.server.shutdown(); self.thread.join(timeout=3); self.server.server_close()

    def auth_fixture(self, route):
        if route.request.method == "GET":
            if self.logged_in:
                route.fulfill(json={"user": {"id": "u1", "username": "cook", "is_admin": True}, "setup_required": False, "csrf_token": "csrf-2"})
            else:
                route.fulfill(json={"user": None, "setup_required": not self.account_created, "csrf_token": "csrf-1"})
        else:
            route.fulfill(json={"user": None, "csrf_token": "csrf-3"})

    def auth_setup_fixture(self, route):
        self.logged_in = True
        self.account_created = True
        route.fulfill(json={"user": {"id": "u1", "username": "cook", "is_admin": True}, "csrf_token": "csrf-2"})

    def auth_logout_fixture(self, route):
        self.logged_in = False
        self.account_created = True
        route.fulfill(json={"user": None, "csrf_token": "csrf-3"})

    def capture_responsive(self, name, selector):
        root = Path("/private/tmp/sous-webkit-artifacts")
        root.mkdir(parents=True, exist_ok=True)
        for label, width, height in (("desktop", 1280, 900), ("mobile", 390, 844)):
            self.page.evaluate("() => document.activeElement instanceof HTMLElement && document.activeElement.blur()")
            self.page.set_viewport_size({"width": width, "height": height})
            self.page.locator(selector).wait_for(state="visible")
            y = self.page.evaluate("""() => {
              const root = document.documentElement;
              const priorBehavior = root.style.scrollBehavior;
              root.style.scrollBehavior = "auto";
              return new Promise(resolve => {
                let stableFrames = 0;
                let frames = 0;
                const settle = () => {
                  window.scrollTo({ top: 0, left: 0, behavior: "instant" });
                  frames += 1;
                  stableFrames = window.scrollY === 0 ? stableFrames + 1 : 0;
                  if (stableFrames >= 3 || frames >= 20) {
                    const result = window.scrollY;
                    root.style.scrollBehavior = priorBehavior;
                    resolve(result);
                  } else requestAnimationFrame(settle);
                };
                requestAnimationFrame(settle);
              });
            }""")
            self.assertEqual(y, 0, f"{name} screenshot must start at scrollY=0")
            has_horizontal_overflow = self.page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
            self.assertFalse(has_horizontal_overflow, f"{name} view overflows horizontally at {width}px")
            self.page.screenshot(path=str(root / f"v2-{name}-{label}.png"), full_page=True)
        self.page.set_viewport_size({"width": 390, "height": 844})

    def settings_fixture(self, route):
        route.fulfill(json={
            "vault": {"initialized": True, "locked": False},
            "bedrock": {"region": "us-east-1", "model": "synthetic.test-model", "source": "managed", "has_credentials": True, "has_session_token": False},
            "defaults": {"provider": "bedrock", "model": "synthetic.test-model"},
            "sample_interval": 10,
            "voice": {"voice_id": "tiffany", "model": "Amazon Nova 2 Sonic"},
        })

    def config_fixture(self, route):
        route.fulfill(json={
            "recipes": self.cooking_catalog,
            "providers": [
                {"id": "bedrock", "name": "AWS Bedrock", "configured": True, "locked": False, "default_model": "synthetic.test-model"},
                {"id": "ollama", "name": "Ollama (local)", "configured": True, "default_model": "synthetic.ollama-model"},
            ],
            "defaults": {"provider": "bedrock", "model": "synthetic.test-model"},
            "sample_interval": 10,
        })

    def generate_fixture(self, route):
        request = route.request.post_data_json
        self.assertEqual(request["dish"], "Tomato rice")
        route.fulfill(json={"recipe": {"name": "Tomato rice", "description": "A short supper.", "servings": 2, "equipment": ["Pot"], "ingredients": ["1 cup rice", "Tomatoes"], "ingredient_details": [{"name": "Rice", "quantity": 1, "unit": "cup"}, {"name": "Tomatoes", "quantity": None, "unit": ""}], "steps": [{"title": "Cook", "instruction": "Simmer gently.", "visual_cue": "The rice is tender."}]}, "warnings": ["Review the tomato quantity before cooking."]})

    def recipe_fixture(self, route):
        if route.request.method == "GET":
            recipes = [
                {"id": "global-rice", "name": "Rice", "description": "Shared recipe", "scope": "global", "can_edit": False, "ingredients": ["Rice"], "steps": [{"title": "Cook"}]},
                {"id": "global-stew", "name": "Shared stew", "description": "Admin-editable shared recipe", "scope": "global", "can_edit": True, "ingredients": ["Beans"], "steps": [{"title": "Simmer"}]},
            ]
            if self.saved: recipes.append({"id": "personal-tomato-rice", "name": "Tomato rice", "description": "A short supper.", "scope": "personal", "can_edit": True, "ingredients": ["1 cup rice", "Tomatoes"], "steps": [{"title": "Cook"}]})
            route.fulfill(json={"recipes": recipes})
        elif route.request.method == "POST":
            payload = route.request.post_data_json
            self.assertEqual(payload["scope"], "personal")
            self.saved = True
            saved_recipe = {"id": "personal-tomato-rice", **payload["recipe"]}
            self.cooking_catalog.append(saved_recipe)
            route.fulfill(status=201, json={"recipe": saved_recipe})
        else:
            route.fulfill(json={})

    def timer_fixture_route(self, route):
        if route.request.method == "GET":
            route.fulfill(json={"timers": self.timer_fixture})
        else:
            route.fulfill(status=201, json={"timer": {"id": "timer-new", "name": "Timer", "deadline_at": 1, "status": "running"}})

    def timer_action_fixture(self, route):
        timer_id = route.request.url.rsplit("/", 2)[-2]
        action = route.request.url.rsplit("/", 1)[-1]
        for timer in self.timer_fixture:
            if timer["id"] == timer_id:
                timer["status"] = "acknowledged" if action == "ack" else "cancelled"
                if action == "ack": timer["acknowledged_at"] = 1
        route.fulfill(json={"ok": True})

    def test_first_admin_generation_save_and_immediate_cook_without_reload(self):
        expect = self.expect
        expect(self.page.locator("#auth-panel")).to_be_visible()
        self.assertFalse(self.page.locator("#setup-panel").is_visible())
        self.capture_responsive("auth", "#auth-panel")
        self.page.locator("#setup-username").fill("cook")
        self.page.locator("#setup-password").fill("synthetic-browser-password")
        self.page.locator("#admin-setup-form button[type=submit]").click()
        expect(self.page.locator("#setup-panel")).to_be_visible()
        self.page.locator("#provider-overrides > summary").click()
        self.page.locator("#model-input").fill("synthetic-cook-model")
        self.page.locator("#nav-recipes").click()
        expect(self.page.locator("#recipe-library")).to_contain_text("Rice")
        self.capture_responsive("recipes", "#recipes-panel")
        editable_global = self.page.locator(".recipe-entry").filter(has_text="Shared stew")
        expect(editable_global.get_by_role("button", name="Copy to my recipes")).to_be_visible()
        expect(editable_global.get_by_role("button", name="Edit")).to_be_visible()
        self.page.locator("#open-generator").click()
        self.page.locator("#generate-dish").fill("Tomato rice")
        self.page.locator("#generate-pantry").fill("Rice\nTomatoes")
        self.page.locator("#generator-form button[type=submit]").click()
        expect(self.page.locator("#recipe-editor")).to_be_visible()
        self.capture_responsive("editor", "#recipe-editor")
        expect(self.page.locator("#generator-error")).to_contain_text("Review and save")
        self.assertEqual(self.page.locator(".ingredient-row:first-child .ingredient-name").input_value(), "Rice")
        self.page.locator("#add-ingredient").click()
        self.assertEqual(self.page.locator(".ingredient-row").count(), 3)
        self.page.locator(".ingredient-row:last-child [data-action='remove-row']").click()
        self.assertEqual(self.page.locator(".ingredient-row").count(), 2)
        self.page.locator("#add-recipe-step").click()
        self.assertEqual(self.page.locator(".step-editor-row").count(), 2)
        self.page.locator(".step-editor-row:last-child [data-action='remove-row']").click()
        self.assertEqual(self.page.locator(".step-editor-row").count(), 1)
        self.assertFalse(self.saved, "AI generation must not save or publish a recipe automatically.")
        with self.page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/recipes")):
            self.page.locator("#recipe-form button[type=submit]").click()
        self.assertTrue(self.saved)
        expect(self.page.locator("#recipe-library")).to_contain_text("Tomato rice")
        recipe_card = self.page.locator(".recipe-entry").filter(has_text="Tomato rice")
        recipe_card.get_by_role("button", name="Cook this").click()
        expect(self.page.locator("#setup-panel")).to_be_visible()
        expect(self.page.locator("#recipe-select")).to_have_value("personal-tomato-rice")
        self.assertEqual(self.page.locator("#model-input").input_value(), "synthetic-cook-model")
        expect(self.page.locator("#nav-cooking")).to_have_attribute("aria-current", "page")
        self.assertEqual(self.page.evaluate("() => JSON.stringify([Object.keys(localStorage), Object.keys(sessionStorage)])"), "[[],[]]")

        self.page.locator("#nav-recipes").click()
        self.page.locator("#new-recipe-button").click()
        self.page.locator("#edit-recipe-name").fill("Unsaved private draft")
        self.page.locator("#nav-settings").click()
        expect(self.page.locator("#settings-panel")).to_be_visible()
        expect(self.page.locator("#profile-pantry")).to_have_value("Rice")
        expect(self.page.locator("#bedrock-region")).to_have_value("us-east-1")
        self.page.locator("#profile-pantry").fill("Private pantry"); self.page.locator("#profile-equipment").fill("Private oven")
        self.page.locator("#bedrock-region").fill("us-west-2"); self.page.locator("#bedrock-model").fill("private-model")
        self.page.locator("#default-model").fill("private-default-model")
        self.page.evaluate("""() => {
          window.__delayCoreConfig = true;
          window.__coreConfigSettled = false;
          window.__coreConfigResult = new Promise((resolve, reject) => document.dispatchEvent(new CustomEvent('sous:recipes-changed', { detail: { recipeId: null, resolve, reject } })))
            .then(() => { window.__coreConfigSettled = true; }, () => { window.__coreConfigSettled = true; });
        }""")
        self.page.wait_for_function("window.__coreConfigStarted === true")
        self.page.evaluate("document.querySelector('#logout-button').click()")
        self.page.wait_for_function("window.__logoutRequestStarted === true")
        self.assertFalse(self.page.evaluate("window.__logoutResponseReturned"), "fixture logout response should still be delayed")
        for selector in ("#setup-panel", "#session-panel", "#completion-panel", "#settings-panel", "#recipes-panel", "#history-panel", "#app-nav", "#account-controls"):
            self.assertTrue(self.page.locator(selector).is_hidden(), f"{selector} must hide before logout settles")
        for selector in ("#edit-recipe-name", "#profile-pantry", "#profile-equipment", "#bedrock-region", "#bedrock-model", "#default-model", "#model-input"):
            self.assertEqual(self.page.locator(selector).input_value(), "", f"{selector} must clear before logout settles")
        self.assertEqual(self.page.locator("#history-events").inner_text(), "")
        self.page.evaluate("window.__releaseCoreConfig()")
        self.page.wait_for_function("window.__coreConfigSettled === true")
        self.assertEqual(self.page.locator("#loading-panel").inner_text(), "Signing out…")
        for selector in ("#setup-panel", "#session-panel", "#completion-panel", "#settings-panel", "#recipes-panel", "#history-panel", "#app-nav", "#account-controls"):
            self.assertTrue(self.page.locator(selector).is_hidden(), f"late core response must not reopen {selector}")
        expect(self.page.locator("#login-form")).to_be_visible()
        self.assertEqual(self.page_errors, [])

    def test_named_timers_expire_acknowledge_and_cancel_independently(self):
        expect = self.expect
        self.page.evaluate("""() => {
          document.dispatchEvent(new CustomEvent('sous:ready', { detail: { user: { id: 'u1', username: 'cook', is_admin: true }, settings: {}, sampleInterval: 10 } }));
          document.querySelector('#session-panel').hidden = false;
          document.dispatchEvent(new CustomEvent('sous:session', { detail: { session: { id: 'timer-session', status: 'active', monitoring: false, timers: [
            { id: 'timer-rice', name: 'Rice', deadline_at: 1, status: 'elapsed', acknowledged_at: null },
            { id: 'timer-pasta', name: 'Pasta', deadline_at: Math.floor(Date.now() / 1000) + 3600, status: 'running', acknowledged_at: null }
          ], checklist: [], alerts: [] } } }));
        }""")
        expect(self.page.locator("#named-timer-list")).to_contain_text("Rice · Time is up")
        expect(self.page.locator("#named-timer-list")).to_contain_text("Pasta")
        expect(self.page.locator("#named-timer-list")).to_contain_text("remaining")
        expect(self.page.locator("#named-timer-notice")).to_contain_text("Rice is up.")
        self.page.evaluate("""() => {
          const session = {
          id: 'timer-session', status: 'active', monitoring: false, timers: [
            { id: 'timer-rice', name: 'Rice', deadline_at: 1, status: 'elapsed', acknowledged_at: null },
            { id: 'timer-pasta', name: 'Pasta', deadline_at: Math.floor(Date.now() / 1000) + 3600, status: 'running', acknowledged_at: null }
          ], checklist: [], alerts: []
          };
          document.dispatchEvent(new CustomEvent('sous:session', { detail: { session } }));
        }""")
        self.assertEqual(self.page.locator("#named-timer-notice").inner_text().count("Rice is up."), 1)
        rice = self.page.locator("#named-timer-list li").filter(has_text="Rice")
        rice.locator("[data-action='ack-timer:timer-rice']").click()
        expect(rice).to_contain_text("Acknowledged")
        self.assertNotIn("Time is up", rice.inner_text())
        self.assertEqual(rice.locator("button").count(), 0)
        pasta = self.page.locator("#named-timer-list li").filter(has_text="Pasta")
        pasta.locator("[data-action='cancel-timer:timer-pasta']").click()
        expect(pasta).to_contain_text("Cancelled")
        self.assertNotIn("Time is up", pasta.inner_text())
        self.assertEqual(pasta.locator("button").count(), 0)
        self.page.locator("#app-nav").evaluate("node => node.hidden = false")
        self.page.locator("#nav-recipes").click()
        expect(self.page.locator("#recipes-panel")).to_be_visible()
        expect(self.page.locator("#toast")).to_contain_text("Soup is up.", timeout=8000)
        self.page.locator("#nav-settings").click()
        expect(self.page.locator("#settings-panel")).to_be_visible()
        self.page.evaluate("""() => {
          const session = {
            id: 'timer-session', status: 'active', monitoring: false, timers: [
              { id: 'timer-rice', name: 'Rice', deadline_at: 1, status: 'acknowledged', acknowledged_at: 1 },
              { id: 'timer-pasta', name: 'Pasta', deadline_at: 1, status: 'cancelled', acknowledged_at: null },
              { id: 'timer-soup', name: 'Soup', deadline_at: 1, status: 'elapsed', acknowledged_at: null }
            ], checklist: [], alerts: []
          };
          document.dispatchEvent(new CustomEvent('sous:session', { detail: { session } }));
        }""")
        self.assertEqual(self.page.locator("#toast").inner_text().count("Soup is up."), 1)
        self.page.wait_for_timeout(3200)
        stats = self.page.evaluate("() => window.__timerFetchStats")
        self.assertLessEqual(stats["maxConcurrent"], 1, "timer polling must keep at most one request in flight")
        self.assertGreaterEqual(stats["total"], 2, "the delayed fixture should span multiple polling intervals")
        self.assertEqual(self.page_errors, [])


if __name__ == "__main__":
    unittest.main()
