"""Isolated WebKit checks for the opt-in Nova voice companion.

These tests use a fake same-origin WebSocket and a synthetic oscillator stream.
They never access a physical microphone or make an AWS request.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import create_app


class VoiceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("SOUS_BROWSER_TESTS") != "1":
            raise unittest.SkipTest("Set SOUS_BROWSER_TESTS=1 to run isolated WebKit UI tests.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install requirements-dev.txt before enabling SOUS_BROWSER_TESTS=1.") from exc
        browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        resolved = Path(browser_path).resolve() if browser_path else None
        if not resolved or not resolved.is_dir() or Path("/tmp").resolve() not in resolved.parents:
            raise RuntimeError("PLAYWRIGHT_BROWSERS_PATH must point to the isolated WebKit runtime under /tmp.")
        cls.sync_playwright = staticmethod(sync_playwright)

    def setUp(self):
        Path("/private/tmp/sous-webkit-artifacts").mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="sous-voice-browser-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        recipes = Path(self.temp.name) / "recipes"
        recipes.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1] / "recipes" / "00-scrambled-eggs.json", recipes / "00-scrambled-eggs.json")
        app = create_app({
            "TESTING": True,
            "DATABASE": os.path.join(self.temp.name, "sous.sqlite3"),
            "SETTINGS_VAULT": os.path.join(self.temp.name, "settings.vault"),
            "SECRET_KEY": "voice-browser-test-secret",
            "RECIPE_DIR": str(recipes),
        })
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.playwright = self.sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.webkit.launch()
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.context.add_init_script(self._voice_fakes())
        self.addCleanup(self.context.close)
        self.context.route("**/api/settings", self._settings_with_voice_available)
        self.page = self.context.new_page()
        self.page.on("pageerror", self._record_page_error)
        self.page_errors = []
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}", wait_until="networkidle")
        self.page.locator("#setup-panel").wait_for(state="visible")

    def _record_page_error(self, error):
        self.page_errors.append(str(error))

    def _settings_with_voice_available(self, route):
        response = route.fetch()
        payload = response.json()
        payload["vault"] = {"initialized": True, "locked": False}
        payload["voice"]["available"] = True
        payload["voice"].pop("reason", None)
        route.fulfill(response=response, json=payload)

    @staticmethod
    def _voice_fakes():
        return r"""
          (() => {
            const NativeAudioContext = window.AudioContext;
            window.__syntheticTracks = [];
            window.__voiceSockets = [];
            window.__voicePlaybackStarts = 0;
            window.__voicePlaybackStops = 0;
            window.__connectTimeouts = [];
            window.__clearedVoiceTimers = [];
            window.__syntheticStreams = new WeakSet();
            window.__syntheticOscillators = [];
            const nativeSetTimeout = window.setTimeout.bind(window);
            const nativeClearTimeout = window.clearTimeout.bind(window);
            window.setTimeout = (callback, delay, ...args) => {
              const timer = nativeSetTimeout(callback, delay, ...args);
              if (Number(delay) === 20000) window.__connectTimeouts.push(timer);
              return timer;
            };
            window.clearTimeout = timer => {
              if (window.__connectTimeouts.includes(timer)) window.__clearedVoiceTimers.push(timer);
              return nativeClearTimeout(timer);
            };
            window.__makeSyntheticStream = () => {
              const context = new NativeAudioContext();
              const oscillator = context.createOscillator();
              oscillator.frequency.value = 440;
              const destination = context.createMediaStreamDestination();
              oscillator.connect(destination);
              oscillator.start();
              context.resume();
              const stream = destination.stream;
              window.__syntheticStreams.add(stream);
              stream.getTracks().forEach(track => {
                window.__syntheticTracks.push(track);
              });
              return { stream, context, oscillator };
            };
            const nativeCreateMediaStreamSource = NativeAudioContext.prototype.createMediaStreamSource;
            NativeAudioContext.prototype.createMediaStreamSource = function(stream) {
              if (!window.__syntheticStreams.has(stream)) return nativeCreateMediaStreamSource.call(this, stream);
              // WebKit headless does not route MediaStreamDestination back into
              // MediaStreamSource. Drive the real app worklet with a native,
              // synthetic oscillator in its AudioContext instead.
              const source = this.createGain();
              const oscillator = this.createOscillator();
              oscillator.frequency.value = 440;
              oscillator.connect(source);
              oscillator.start();
              window.__syntheticOscillators.push(oscillator);
              const disconnect = source.disconnect.bind(source);
              source.disconnect = (...args) => {
                try { oscillator.stop(); } catch { /* already stopped */ }
                return disconnect(...args);
              };
              return source;
            };
            Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
              getUserMedia: async () => window.__makeSyntheticStream().stream
            }});
            class MockWebSocket {
              static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
              constructor(url) {
                this.url = url; this.readyState = 0; this.sent = []; this.closed = false;
                window.__voiceSockets.push(this);
                setTimeout(() => {
                  if (window.__holdVoiceSocketOpen) return;
                  if (this.readyState !== MockWebSocket.CONNECTING) return;
                  this.readyState = MockWebSocket.OPEN;
                  this.onopen?.();
                }, 0);
              }
              send(data) {
                this.sent.push(data);
                if (typeof data === 'string') {
                  const message = JSON.parse(data);
                  if (message.type === 'start') setTimeout(() => this.receive({ type: 'ready' }), 0);
                }
              }
              receive(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
              close() {
                this.closed = true; this.readyState = MockWebSocket.CLOSED;
                setTimeout(() => this.onclose?.(), 0);
              }
            }
            window.WebSocket = MockWebSocket;
            const sourceStart = AudioBufferSourceNode.prototype.start;
            const sourceStop = AudioBufferSourceNode.prototype.stop;
            AudioBufferSourceNode.prototype.start = function(...args) {
              window.__voicePlaybackStarts += 1; return sourceStart.apply(this, args);
            };
            AudioBufferSourceNode.prototype.stop = function(...args) {
              window.__voicePlaybackStops += 1; return sourceStop.apply(this, args);
            };
          })();
        """

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=3)
        self.server.server_close()

    def start_session(self):
        if not self.page.locator("#provider-overrides").evaluate("element => element.open"):
            self.page.locator("#provider-overrides > summary").click()
        self.page.locator("#model-input").fill("synthetic-vision-model")
        self.page.locator("#start-session").click()
        self.page.locator("#session-panel").wait_for(state="visible")

    def start_voice(self):
        self.start_session()
        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Listening. Ask about this recipe, step, or latest camera observation.").wait_for()

    def test_start_audio_worklet_transcript_interruption_and_stop(self):
        self.start_voice()
        self.page.wait_for_function("window.__voiceSockets.length === 1 && window.__voiceSockets[0].sent.some(x => x instanceof ArrayBuffer)")
        self.assertTrue(self.page.evaluate("window.__voiceSockets[0].sent.some(x => typeof x === 'string' && JSON.parse(x).type === 'start')"))
        signal = self.page.evaluate("""() => {
          const packet = window.__voiceSockets[0].sent.find(x => x instanceof ArrayBuffer);
          if (!packet) return null;
          const samples = new Int16Array(packet);
          let crossings = 0, square = 0;
          for (let index = 0; index < samples.length; index += 1) {
            square += samples[index] * samples[index];
            if (index && (samples[index - 1] < 0) !== (samples[index] < 0)) crossings += 1;
          }
          return { bytes: packet.byteLength, crossings, rms: Math.sqrt(square / samples.length) };
        }""")
        self.assertEqual(signal["bytes"], 1024)
        self.assertGreater(signal["rms"], 1000)
        self.assertGreaterEqual(signal["crossings"], 20)
        self.assertLessEqual(signal["crossings"], 38)
        self.page.evaluate("""() => {
          const socket = window.__voiceSockets[0];
          const pcm = new Int16Array(24000 * 3);
          for (let index = 0; index < pcm.length; index += 1) pcm[index] = Math.round(Math.sin(index * Math.PI * 2 * 220 / 24000) * 10000);
          let binary = '';
          new Uint8Array(pcm.buffer).forEach(value => binary += String.fromCharCode(value));
          socket.receive({ type: 'audio', audio: btoa(binary) });
          socket.receive({ type: 'transcript', role: 'assistant', text: 'Hello from Sous', final: false });
          socket.receive({ type: 'transcript', role: 'assistant', text: 'Hello from Sous.', final: true });
        }""")
        self.page.get_by_text("Hello from Sous.").wait_for()
        self.page.wait_for_function("window.__voicePlaybackStarts > 0")
        self.page.evaluate("window.__voiceSockets[0].receive({ type: 'interrupted' })")
        self.page.wait_for_function("window.__voicePlaybackStops > 0")
        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Voice is off. Camera guidance and manual controls work independently.").wait_for()
        self.assertEqual(self.page.locator("#voice-transcript .voice-line").count(), 0)
        self.assertTrue(self.page.evaluate("window.__voiceSockets[0].closed"))
        self.assertTrue(self.page.evaluate("window.__syntheticTracks.every(track => track.readyState === 'ended')"))
        self.assertEqual(self.page_errors, [])

    def test_microphone_denial_and_socket_error_keep_text_controls_available(self):
        self.start_session()
        self.page.evaluate("""() => { navigator.mediaDevices.getUserMedia = async () => { const error = new Error('denied'); error.name = 'NotAllowedError'; throw error; }; }""")
        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Microphone permission was declined. Text and cooking controls are still available.").wait_for()
        self.assertFalse(self.page.locator("#command-input").is_disabled())
        self.assertFalse(self.page.locator("#next-button").is_disabled())
        self.page_errors = []

        self.page.evaluate("""() => {
          navigator.mediaDevices.getUserMedia = async () => window.__makeSyntheticStream().stream;
          const original = window.WebSocket;
          window.WebSocket = class extends original {
            constructor(url) { super(url); setTimeout(() => { this.onerror?.(); this.onclose?.(); }, 0); }
          };
        }""")
        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Voice disconnected. Start it again when you’re ready.").wait_for()
        self.assertFalse(self.page.locator("#command-input").is_disabled())
        self.assertEqual(self.page_errors, [])

    def test_stop_during_permission_prompt_stops_late_stream(self):
        self.start_session()
        self.page.evaluate("""() => {
          navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { window.__resolveVoicePermission = resolve; });
        }""")
        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Requesting microphone access…").wait_for()
        self.page.locator("#companion-toggle").click()
        self.page.evaluate("""() => {
          const { stream } = window.__makeSyntheticStream();
          window.__lateVoiceStream = stream;
          window.__resolveVoicePermission(stream);
        }""")
        self.page.wait_for_function("window.__lateVoiceStream?.getTracks().every(track => track.readyState === 'ended')")
        self.assertEqual(self.page.evaluate("window.__voiceSockets.length"), 0)
        self.assertEqual(self.page_errors, [])

    def test_websocket_connect_cancellation_clears_handshake_timer(self):
        self.start_session()
        self.page.evaluate("""() => {
          window.__holdVoiceSocketOpen = true;
        }""")
        self.page.locator("#companion-toggle").click()
        self.page.wait_for_function("window.__voiceSockets.length === 1 && window.__connectTimeouts.length === 1")
        timer = self.page.evaluate("window.__connectTimeouts[0]")
        self.page.locator("#companion-toggle").click()
        self.page.wait_for_function("window.__clearedVoiceTimers.length === 1")
        self.assertEqual(self.page.evaluate("window.__clearedVoiceTimers[0]"), timer)
        self.assertTrue(self.page.evaluate("window.__voiceSockets[0].closed"))
        self.assertEqual(self.page_errors, [])

    def test_slow_input_and_playback_overflow_stop_with_visible_error(self):
        self.start_voice()
        self.page.evaluate("window.__voiceSockets[0].bufferedAmount = 65537")
        self.page.get_by_text("Voice connection is too slow to keep up. Stop and try again when the connection improves.").wait_for()
        self.assertTrue(self.page.evaluate("window.__voiceSockets[0].closed"))

        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Listening. Ask about this recipe, step, or latest camera observation.").wait_for()
        self.page.evaluate("""() => {
          const raw = String.fromCharCode(0).repeat(30 * 24000 * 2 + 2);
          window.__voiceSockets.at(-1).receive({ type: 'audio', audio: btoa(raw) });
        }""")
        self.page.get_by_text("Voice audio arrived too quickly to play safely. Stop voice and start a fresh conversation.").wait_for()
        self.assertTrue(self.page.evaluate("window.__voiceSockets.at(-1).closed"))
        self.assertFalse(self.page.locator("#command-input").is_disabled())
        self.assertEqual(self.page_errors, [])

    def test_settings_hidden_end_and_vault_lock_stop_voice(self):
        self.start_voice()
        self.page.locator("#nav-settings").click()
        self.page.locator("#settings-panel").wait_for(state="visible")
        self.assertTrue(self.page.evaluate("window.__voiceSockets[0].closed"))

        self.page.locator("#nav-cooking").click()
        self.page.locator("#session-panel").wait_for(state="visible")
        self.assertIn("Settings was opened", self.page.locator("#companion-status").text_content())
        self.page.evaluate("""() => {
          Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        self.page.get_by_text("Voice stopped because this tab is hidden.").wait_for()
        self.page.evaluate("Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })")

        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Listening. Ask about this recipe, step, or latest camera observation.").wait_for()
        self.page.evaluate("""async () => {
          const token = document.querySelector('meta[name=csrf-token]').content;
          const passphrase = 'synthetic voice vault passphrase';
          await fetch('/api/settings/vault', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: JSON.stringify({ passphrase }) });
          await fetch('/api/settings/lock', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: '{}' });
          window.__voiceSockets[window.__voiceSockets.length - 1].receive({ type: 'stopped', reason: 'locked' });
        }""")
        self.page.get_by_text("AWS settings were locked. Voice has stopped.").wait_for()
        self.assertTrue(self.page.evaluate("window.__voiceSockets.at(-1).closed"))
        self.assertEqual(self.page.locator("#voice-transcript .voice-line").count(), 0)

        self.page.locator("#companion-toggle").click()
        self.page.get_by_text("Listening. Ask about this recipe, step, or latest camera observation.").wait_for()
        self.page.locator("#end-session").click()
        self.page.locator("#completion-panel").wait_for(state="visible")
        self.assertIn("cooking session ended", self.page.locator("#companion-status").text_content())
        self.assertTrue(self.page.evaluate("window.__voiceSockets.at(-1).closed"))

    def test_voice_preference_persists_and_panel_fits_phone(self):
        self.page.locator("#nav-settings").click()
        self.page.wait_for_function("!document.querySelector('#voice-preference').disabled")
        self.page.locator("#voice-preference").select_option("matthew")
        self.page.locator("#save-voice-preference").click()
        self.page.get_by_text("Saved for the next voice session.").wait_for()
        settings = self.page.evaluate("async () => (await fetch('/api/settings')).json()")
        self.assertEqual(settings["voice"]["voice_id"], "matthew")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator("#nav-cooking").click()
        self.start_session()
        self.page.evaluate("""async () => {
          document.activeElement?.blur();
          window.scrollTo({top: 0, left: 0, behavior: 'instant'});
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }""")
        self.assertEqual(self.page.evaluate("window.scrollY"), 0)
        self.assertLessEqual(abs(self.page.locator(".cook-column").evaluate("element => element.getBoundingClientRect().top") - self.page.locator(".workspace").evaluate("element => element.getBoundingClientRect().top")), 1)
        self.page.screenshot(path="/private/tmp/sous-webkit-artifacts/voice-companion-mobile.png", full_page=True)
        bounds = self.page.locator("#voice-panel").evaluate("element => ({left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right})")
        viewport_width = self.page.evaluate("window.innerWidth")
        self.assertGreaterEqual(bounds["left"], 0)
        self.assertLessEqual(bounds["right"], viewport_width)
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.evaluate("""async () => {
          document.activeElement?.blur();
          window.scrollTo({top: 0, left: 0, behavior: 'instant'});
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }""")
        self.assertEqual(self.page.evaluate("window.scrollY"), 0)
        self.assertLessEqual(abs(self.page.locator(".cook-column").evaluate("element => element.getBoundingClientRect().top") - self.page.locator(".workspace").evaluate("element => element.getBoundingClientRect().top")), 1)
        self.page.screenshot(path="/private/tmp/sous-webkit-artifacts/voice-companion-desktop.png", full_page=True)
        self.assertEqual(self.page_errors, [])


if __name__ == "__main__":
    unittest.main()
