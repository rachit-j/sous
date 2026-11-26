import json
import logging
import os
import stat
import sys
import types
import unittest
from unittest import mock

from botocore.exceptions import ClientError

from app import user_vault
from settings import BAD_UNLOCK, BedrockConnection, SettingsError, merge_bedrock
from tests.test_app import SousTestCase, observation

# Synthetic sentinels only; never real credentials.
PASSPHRASE = "correct horse battery staple"
KEY_ID = "AKIASENTINEL0000TEST"
SECRET = "sentinel-secret/Zq9+abcdefghijklmnopqrstu"
TOKEN = "sentinel-session-token-0123456789abcdef"
SENTINELS = (PASSPHRASE, KEY_ID, SECRET, TOKEN)
CONNECTION = {"region": "us-west-2", "model": "", "access_key_id": KEY_ID, "secret_access_key": SECRET}
GOOD_TEXT = json.dumps(observation())


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


class SettingsTestCase(SousTestCase):
    def setUp(self):
        super().setUp()
        self.vault_path = self.vault().path  # the signed-in fixture account's own vault file
        self.legacy_vault_path = os.path.join(self.tmp.name, "settings.vault")  # V1 location, never used
        root = logging.getLogger()
        self.logs = LogCapture()
        old_level = root.level
        root.addHandler(self.logs)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(root.removeHandler, self.logs)

    def send(self, method, path, body=None, client=None, token=None, **headers):
        headers.setdefault("X-CSRF-Token", token or self.token)
        return (client or self.client).open(path, method=method, json=body, headers=headers)

    def settings(self, client=None):
        resp = (client or self.client).get("/api/settings")
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()

    def init_vault(self):
        resp = self.post("/api/settings/vault", {"passphrase": PASSPHRASE})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()

    def save_bedrock(self, expect=200, **body):
        resp = self.send("PUT", "/api/settings/bedrock", {**CONNECTION, **body})
        self.assertEqual(resp.status_code, expect, resp.get_json())
        return resp.get_json()

    def restart(self):
        self.app = self.make_app()
        self.client, self.token = self.browser(self.app)

    def vault(self):
        return user_vault(self.app, self.user_id)

    def assert_no_secrets(self, text):
        for value in SENTINELS:
            self.assertNotIn(value, text)


class VaultTests(SettingsTestCase):
    def test_initial_settings_are_locked_with_defaults(self):
        data = self.settings()
        self.assertEqual(data, {
            "vault": {"initialized": False, "locked": True},
            "bedrock": {"region": None, "model": None, "has_credentials": None, "has_session_token": None,
                        "source": data["bedrock"]["source"]},
            "defaults": {"provider": None, "model": None},
            "sample_interval": 10,
            "voice": {"model": "amazon.nova-2-sonic-v1:0", "voice_id": "tiffany", "available": False,
                      "reason": "Save an AWS connection in Settings to use voice."},
        })
        self.assertFalse(os.path.exists(self.vault_path))

    def test_passphrase_bounds_and_single_initialization(self):
        for bad in ("short", "x" * 11, "x" * 1025, 123456789012, None, ["x" * 20], "\ud800" * 12):
            resp = self.post("/api/settings/vault", {"passphrase": bad})
            self.assertEqual(resp.status_code, 400, bad)
        self.assertFalse(os.path.exists(self.vault_path))
        data = self.init_vault()
        self.assertEqual(data["vault"], {"initialized": True, "locked": False})
        self.assertEqual(self.post("/api/settings/vault", {"passphrase": PASSPHRASE + "!"}).status_code, 409)

    def test_encrypted_at_rest_and_restart_starts_locked(self):
        self.init_vault()
        data = self.save_bedrock(model="anthropic.example-v1:0", session_token=TOKEN)
        self.assertEqual(data["bedrock"], {"region": "us-west-2", "model": "anthropic.example-v1:0",
                                           "has_credentials": True, "has_session_token": True, "source": "managed"})
        with open(self.vault_path, encoding="utf-8") as fh:
            stored = fh.read()
        self.assert_no_secrets(stored)
        self.assertNotIn("us-west-2", stored)
        self.assertEqual(set(json.loads(stored)), {"version", "kdf", "salt", "token"})
        self.assertEqual(stat.S_IMODE(os.stat(self.vault_path).st_mode), 0o600)
        vault_dir = os.path.dirname(self.vault_path)
        self.assertEqual(vault_dir, os.path.join(self.tmp.name, "users", str(self.user_id)))
        self.assertEqual(stat.S_IMODE(os.stat(vault_dir).st_mode) & 0o077, 0)  # no group/other access
        self.assertEqual([f for f in os.listdir(vault_dir) if f.endswith(".tmp")], [])
        self.assertFalse(os.path.exists(self.legacy_vault_path))
        with open(self.db_path, "rb") as fh:
            self.assert_no_secrets(fh.read().decode("latin-1"))

        self.restart()
        data = self.settings()
        self.assertEqual(data["vault"], {"initialized": True, "locked": True})
        self.assertEqual(data["bedrock"]["region"], None)
        self.assertEqual(data["bedrock"]["source"], "managed")
        bedrock = next(p for p in self.client.get("/api/config").get_json()["providers"] if p["id"] == "bedrock")
        self.assertEqual((bedrock["configured"], bedrock["locked"], bedrock["default_model"]), (False, True, ""))

        resp = self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["bedrock"]["model"], "anthropic.example-v1:0")
        self.assertEqual(self.vault().connection(),
                         BedrockConnection("us-west-2", KEY_ID, SECRET, TOKEN))

        locked = self.post("/api/settings/lock", {}).get_json()
        self.assertEqual(locked["vault"]["locked"], True)
        self.assertIsNone(self.vault().connection())
        self.assertIsNone(self.vault()._fernet)

    def test_wrong_passphrase_is_generic_and_throttled(self):
        self.init_vault()
        self.restart()
        for _ in range(5):
            resp = self.post("/api/settings/unlock", {"passphrase": "wrong passphrase!"})
            self.assertEqual((resp.status_code, resp.get_json()), (403, {"error": BAD_UNLOCK}))
        resp = self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        self.assertEqual(resp.status_code, 429)  # even the correct passphrase waits
        self.assertTrue(self.settings()["vault"]["locked"])
        self.clock += 61
        self.assertEqual(self.post("/api/settings/unlock", {"passphrase": PASSPHRASE}).status_code, 200)
        self.assertEqual(len(self.vault()._failures), 0)

    def test_unlock_requires_initialized_vault(self):
        self.assertEqual(self.post("/api/settings/unlock", {"passphrase": PASSPHRASE}).status_code, 409)

    def test_tampered_or_damaged_vault_fails_generically_and_closed(self):
        self.init_vault()
        self.save_bedrock()
        with open(self.vault_path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        token = envelope["token"]
        middle = len(token) // 2
        envelope["token"] = token[:middle] + ("A" if token[middle] != "A" else "B") + token[middle + 1:]
        for damaged in (json.dumps(envelope), json.dumps({**envelope, "managed_bedrock": True, "salt": "!!"}),
                        "not json", ""):
            with self.subTest(damaged=damaged[:20]):
                with open(self.vault_path, "w", encoding="utf-8") as fh:
                    fh.write(damaged)
                self.restart()
                resp = self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
                self.assertEqual((resp.status_code, resp.get_json()), (403, {"error": BAD_UNLOCK}))
                self.assertEqual(self.settings()["bedrock"]["source"], "managed")  # no env fallback

    def test_settings_mutations_require_csrf_and_same_origin(self):
        self.init_vault()
        mutations = (("POST", "/api/settings/vault", {"passphrase": PASSPHRASE}),
                     ("POST", "/api/settings/unlock", {"passphrase": PASSPHRASE}),
                     ("POST", "/api/settings/lock", {}),
                     ("PUT", "/api/settings/bedrock", CONNECTION),
                     ("DELETE", "/api/settings/bedrock", None),
                     ("PUT", "/api/settings/defaults", {"provider": "openai", "model": "gpt-x"}),
                     ("PUT", "/api/settings/preferences", {"sample_interval": 5}),
                     ("GET", "/api/settings/bedrock/models", None))
        for method, path, body in mutations:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.send(method, path, body, token="wrong").status_code, 403)
                self.assertEqual(self.send(method, path, body, Origin="http://evil.example").status_code, 403)
        data = self.settings()
        self.assertEqual((data["vault"]["locked"], data["bedrock"]["has_credentials"], data["sample_interval"]),
                         (False, False, 10))

    def test_security_headers(self):
        for path in ("/api/settings", "/api/config"):
            resp = self.client.get(path)
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
            self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")

    def test_oversized_settings_body_is_rejected(self):
        resp = self.post("/api/settings/vault", {"passphrase": PASSPHRASE, "pad": "x" * 20000})
        self.assertEqual(resp.status_code, 413)
        self.assertFalse(os.path.exists(self.vault_path))


class BedrockSettingsTests(SettingsTestCase):
    def setUp(self):
        super().setUp()
        self.init_vault()

    def test_validation_and_secret_retention(self):
        for bad in ({"access_key_id": "", "secret_access_key": ""},  # first save needs the pair
                    {"secret_access_key": ""}, {"access_key_id": ""},
                    {"region": "us_west_2"}, {"region": ""}, {"region": None}, {"region": "x" * 40},
                    {"access_key_id": "lowercase-key-id-1"}, {"secret_access_key": "has space in it 1234"},
                    {"session_token": 12}, {"clear_session_token": "yes"}, {"model": "bad model id"},
                    {"model": "m" * 201}):
            with self.subTest(bad=bad):
                resp = self.send("PUT", "/api/settings/bedrock", {**CONNECTION, **bad})
                self.assertEqual(resp.status_code, 400)
                self.assert_no_secrets(resp.get_data(as_text=True))
        self.assertFalse(self.settings()["bedrock"]["has_credentials"])

        self.save_bedrock(session_token=TOKEN)
        data = self.save_bedrock(region="eu-central-1", access_key_id="", secret_access_key="", model="m-1")
        self.assertEqual(data["bedrock"], {"region": "eu-central-1", "model": "m-1", "has_credentials": True,
                                           "has_session_token": True, "source": "managed"})
        self.assertEqual(self.vault().connection(), BedrockConnection("eu-central-1", KEY_ID, SECRET, TOKEN))
        self.save_bedrock(expect=400, access_key_id="AKIAOTHERKEY00000000", secret_access_key="")
        self.save_bedrock(expect=400, session_token=TOKEN, clear_session_token=True)

        data = self.save_bedrock(region="eu-central-1", access_key_id="", secret_access_key="")
        self.assertEqual(data["bedrock"]["model"], "")  # explicit blank model clears it
        self.assertTrue(self.save_bedrock(access_key_id="", secret_access_key="")["bedrock"]["has_session_token"])
        data = self.save_bedrock(access_key_id="", secret_access_key="", clear_session_token=True)
        self.assertFalse(data["bedrock"]["has_session_token"])
        self.save_bedrock(access_key_id="", secret_access_key="", session_token=TOKEN)
        data = self.save_bedrock(access_key_id="AKIAOTHERKEY00000000", secret_access_key="other-secret-0123456789")
        self.assertFalse(data["bedrock"]["has_session_token"])  # a new key pair drops the old token
        self.assertEqual(self.vault().connection().access_key_id, "AKIAOTHERKEY00000000")

    def test_locked_or_uninitialized_mutations_are_conflicts(self):
        self.post("/api/settings/lock", {})
        self.assertEqual(self.send("PUT", "/api/settings/bedrock", CONNECTION).status_code, 409)
        self.assertEqual(self.send("DELETE", "/api/settings/bedrock").status_code, 409)
        self.assertEqual(self.send("PUT", "/api/settings/defaults",
                                   {"provider": "openai", "model": "gpt-x"}).status_code, 409)
        self.assertEqual(self.client.get("/api/settings/bedrock/models",
                                         headers={"X-CSRF-Token": self.token}).status_code, 409)

    def test_delete_connection(self):
        self.save_bedrock()
        with mock.patch.dict(os.environ, {"AWS_REGION": ""}):
            data = self.send("DELETE", "/api/settings/bedrock").get_json()
        self.assertEqual(data["bedrock"], {"region": "", "model": "", "has_credentials": False,
                                           "has_session_token": False, "source": "none"})
        self.assertIsNone(self.vault().connection())
        self.restart()
        self.assertTrue(self.vault().managed_bedrock)  # fail closed until decrypted
        self.assertEqual(self.post("/api/settings/unlock", {"passphrase": PASSPHRASE}).status_code, 200)
        self.assertFalse(self.vault().managed_bedrock)  # authenticated payload has no connection

    def test_no_secret_in_responses_or_logs(self):
        bodies = [self.save_bedrock(session_token=TOKEN), self.settings(),
                  self.client.get("/api/config").get_json()]
        with mock.patch("app.merge_bedrock", side_effect=RuntimeError(f"boom {SECRET} {KEY_ID}")):
            resp = self.send("PUT", "/api/settings/bedrock", CONNECTION)
        self.assertEqual(resp.status_code, 500)
        bodies.append(resp.get_json())
        self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        self.assert_no_secrets(json.dumps(bodies))
        self.assert_no_secrets("\n".join(self.logs.lines))
        self.assertTrue(any("RuntimeError" in line for line in self.logs.lines))
        self.assert_no_secrets(repr(self.vault().connection()))


class PreferenceTests(SettingsTestCase):
    def put_interval(self, value, **kwargs):
        return self.send("PUT", "/api/settings/preferences", {"sample_interval": value}, **kwargs)

    def test_interval_bounds_and_persistence_without_vault(self):
        for bad in (0, 16, -1, True, False, 1.5, 10.0, "10", None, [10]):
            self.assertEqual(self.put_interval(bad).status_code, 400, bad)
        self.assertEqual(self.send("PUT", "/api/settings/preferences", {}).status_code, 400)
        for good in (1, 15, 7):
            self.assertEqual(self.put_interval(good).get_json()["sample_interval"], good)
        self.assertEqual(self.client.get("/api/config").get_json()["sample_interval"], 7)
        self.restart()
        self.assertTrue(self.settings()["vault"]["locked"])
        self.assertEqual(self.settings()["sample_interval"], 7)
        self.assertEqual(self.client.get("/api/config").get_json()["sample_interval"], 7)

    def test_defaults_require_unlock_and_do_not_change_sessions(self):
        session = self.start()
        body = {"provider": "openai", "model": "gpt-x"}
        self.assertEqual(self.send("PUT", "/api/settings/defaults", body).status_code, 409)  # no vault
        self.init_vault()
        for bad in ({"provider": "mystery"}, {"provider": ["openai"]}, {"model": ""}, {"model": None},
                    {"model": 5}, {"model": "x" * 201}):
            self.assertEqual(self.send("PUT", "/api/settings/defaults", {**body, **bad}).status_code, 400, bad)
        data = self.send("PUT", "/api/settings/defaults", body).get_json()
        self.assertEqual(data["defaults"], body)
        config = self.client.get("/api/config").get_json()
        self.assertEqual(config["defaults"], body)
        self.assertEqual(next(p for p in config["providers"] if p["id"] == "openai")["default_model"], "gpt-x")
        self.assertEqual(self.client.get(f"/api/sessions/{session['id']}").get_json()["model"], "llava")

        self.restart()  # locked, but nonsecret defaults stay readable
        self.assertEqual(self.settings()["defaults"], body)
        self.assertEqual(self.client.get("/api/config").get_json()["defaults"], body)


def converse_reply():
    return {"output": {"message": {"role": "assistant", "content": [{"text": GOOD_TEXT}]}},
            "stopReason": "end_turn"}


class ManagedBedrockTests(SettingsTestCase):
    ENV = {"AWS_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "AKIAENVIRONMENT00000",
           "AWS_SECRET_ACCESS_KEY": "env-secret-should-not-be-used", "BEDROCK_MODEL_ID": "env-model",
           "AWS_PROFILE": ""}

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, self.ENV)
        env.start()
        self.addCleanup(env.stop)
        patcher = mock.patch("boto3.Session")
        self.session_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.client_mock = self.session_cls.return_value.client.return_value
        self.client_mock.converse.return_value = converse_reply()
        self.init_vault()
        self.save_bedrock(model="saved-model", session_token=TOKEN)

    def test_legacy_false_marker_cannot_enable_environment_fallback_while_locked(self):
        with open(self.vault_path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        # Simulate editing the unauthenticated marker in a legacy version-1 envelope.
        envelope["managed_bedrock"] = False
        with open(self.vault_path, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh)

        self.restart()
        bedrock = next(p for p in self.client.get("/api/config").get_json()["providers"] if p["id"] == "bedrock")
        self.assertEqual((bedrock["configured"], bedrock["locked"], bedrock["source"]),
                         (False, True, "managed"))
        resp = self.post("/api/sessions", {"recipe_id": "scrambled-eggs", "provider": "bedrock",
                                           "model": "saved-model", "mode": "monitoring"})
        self.assertEqual(resp.status_code, 409)
        self.session_cls.assert_not_called()

        # The authenticated encrypted connection takes over after a successful unlock.
        resp = self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.vault().managed_bedrock)

    def test_empty_vault_never_falls_back_to_environment(self):
        self.assertEqual(self.send("DELETE", "/api/settings/bedrock").status_code, 200)
        self.restart()
        bedrock = next(p for p in self.client.get("/api/config").get_json()["providers"] if p["id"] == "bedrock")
        self.assertEqual((bedrock["configured"], bedrock["locked"], bedrock["source"]),
                         (False, True, "managed"))
        self.assertEqual(self.post("/api/sessions", {"recipe_id": "scrambled-eggs", "provider": "bedrock",
                                                       "model": "env-model", "mode": "monitoring"}).status_code,
                         409)
        self.session_cls.assert_not_called()

        # V2: an unlocked vault without a connection is simply unconfigured, never the environment.
        resp = self.post("/api/settings/unlock", {"passphrase": PASSPHRASE})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.vault().managed_bedrock)
        self.assertEqual(resp.get_json()["bedrock"]["source"], "none")
        bedrock = next(p for p in self.client.get("/api/config").get_json()["providers"] if p["id"] == "bedrock")
        self.assertEqual((bedrock["configured"], bedrock["locked"], bedrock["source"]), (False, False, "none"))
        self.assertEqual(self.post("/api/sessions", {"recipe_id": "scrambled-eggs", "provider": "bedrock",
                                                     "model": "env-model", "mode": "monitoring"}).status_code, 400)
        self.session_cls.assert_not_called()

    def test_config_reflects_managed_connection(self):
        bedrock = next(p for p in self.client.get("/api/config").get_json()["providers"] if p["id"] == "bedrock")
        self.assertEqual(bedrock, {"id": "bedrock", "name": "AWS Bedrock", "configured": True,
                                   "default_model": "saved-model", "source": "managed", "locked": False})

    def test_observation_uses_explicit_credentials_and_locked_never_falls_back(self):
        session = self.act(self.start(provider="bedrock", model="vision-model")["id"], "resume")
        env_before = dict(os.environ)
        resp = self.observe(session)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.session_cls.assert_called_once_with(aws_access_key_id=KEY_ID, aws_secret_access_key=SECRET,
                                                 aws_session_token=TOKEN, region_name="us-west-2")
        self.assertEqual(self.client_mock.converse.call_args.kwargs["modelId"], "vision-model")
        self.assertEqual(dict(os.environ), env_before)

        session = resp.get_json()
        self.post("/api/settings/lock", {})
        self.session_cls.reset_mock()
        resp = self.observe(session)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("locked", resp.get_json()["error"])
        self.session_cls.assert_not_called()  # env credentials are never tried
        self.act(session["id"], "timer_set", seconds=30)  # manual controls keep working
        self.act(session["id"], "end")
        resp = self.post("/api/sessions", {"recipe_id": "scrambled-eggs", "provider": "bedrock",
                                           "model": "vision-model", "mode": "monitoring"})
        self.assertEqual(resp.status_code, 409)

        self.restart()  # still locked after restart; still no fallback
        self.assertEqual(self.post("/api/sessions", {"recipe_id": "scrambled-eggs", "provider": "bedrock",
                                                     "model": "vision-model", "mode": "monitoring"}).status_code, 409)
        self.session_cls.assert_not_called()

    def test_other_providers_keep_four_argument_analyze(self):
        calls = []
        fake = types.ModuleType("providers")
        fake.analyze = lambda provider, model, jpeg, context: calls.append(provider) or observation()
        with mock.patch.dict(sys.modules, {"providers": fake}):
            session = self.act(self.start()["id"], "resume")
            self.assertEqual(self.observe(session).status_code, 200)
        self.assertEqual(calls, ["ollama"])

    def test_deleted_connection_never_uses_environment_credentials(self):
        session = self.act(self.start(provider="bedrock", model="vision-model")["id"], "resume")
        self.send("DELETE", "/api/settings/bedrock")
        resp = self.observe(session)  # a session started earlier cannot fall back either
        self.assertEqual(resp.status_code, 409)
        self.act(session["id"], "end")
        resp = self.post("/api/sessions", {"recipe_id": "scrambled-eggs", "provider": "bedrock",
                                           "model": "vision-model", "mode": "monitoring"})
        self.assertEqual(resp.status_code, 400)
        self.session_cls.assert_not_called()


MODELS = {"modelSummaries": [
    {"modelId": "vendor.vision-a-v1:0", "modelName": "Vision A", "inputModalities": ["TEXT", "IMAGE"],
     "outputModalities": ["TEXT"], "modelLifecycle": {"status": "ACTIVE"}, "inferenceTypesSupported": ["ON_DEMAND"]},
    {"modelId": "vendor.text-only-v1:0", "modelName": "Text only", "inputModalities": ["TEXT"],
     "outputModalities": ["TEXT"], "modelLifecycle": {"status": "ACTIVE"}, "inferenceTypesSupported": ["ON_DEMAND"]},
    {"modelId": "vendor.legacy-vision-v1:0", "modelName": "Legacy", "inputModalities": ["IMAGE"],
     "outputModalities": ["TEXT"], "modelLifecycle": {"status": "LEGACY"}, "inferenceTypesSupported": ["ON_DEMAND"]},
    {"modelId": "vendor.vision-b-v2:0", "modelName": "Vision B", "inputModalities": ["TEXT", "IMAGE"],
     "outputModalities": ["TEXT"], "modelLifecycle": {"status": "ACTIVE"},
     "inferenceTypesSupported": ["INFERENCE_PROFILE"]},
    "not a dict",
]}
ARN = "arn:aws:bedrock:us-west-2::foundation-model/"


def profile(pid, model, status="ACTIVE", kind="SYSTEM_DEFINED"):
    return {"inferenceProfileId": pid, "inferenceProfileName": f"Profile {pid}", "status": status, "type": kind,
            "inferenceProfileArn": f"arn:aws:bedrock:us-west-2:123456789012:application-inference-profile/{pid}",
            "models": [{"modelArn": ARN + model}]}


class DiscoveryTests(SettingsTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("boto3.Session")
        self.session_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.bedrock = self.session_cls.return_value.client.return_value
        self.bedrock.list_foundation_models.return_value = MODELS
        self.bedrock.list_inference_profiles.side_effect = [
            {"inferenceProfileSummaries": [profile("us.vendor.vision-b-v2:0", "vendor.vision-b-v2:0")],
             "nextToken": "page-2"},
            {"inferenceProfileSummaries": [
                profile("us.vendor.vision-b-v2:0", "vendor.vision-b-v2:0"),  # duplicate
                profile("us.vendor.text-only-v1:0", "vendor.text-only-v1:0"),
                profile("us.vendor.vision-a-v1:0", "vendor.vision-a-v1:0", status="INACTIVE"),
                profile("app1", "vendor.vision-a-v1:0", kind="APPLICATION"),
            ]},
        ]
        self.init_vault()

    def discover(self):
        return self.client.get("/api/settings/bedrock/models", headers={"X-CSRF-Token": self.token})

    def test_requires_saved_connection(self):
        self.assertEqual(self.discover().status_code, 409)
        self.session_cls.assert_not_called()

    def test_filters_paginates_and_deduplicates(self):
        self.save_bedrock()
        resp = self.discover()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        app_arn = "arn:aws:bedrock:us-west-2:123456789012:application-inference-profile/app1"
        self.assertEqual(resp.get_json(), {"models": [
            {"id": "vendor.vision-a-v1:0", "name": "Vision A", "kind": "foundation_model"},
            {"id": app_arn, "name": "Profile app1", "kind": "inference_profile"},
            {"id": "us.vendor.vision-b-v2:0", "name": "Profile us.vendor.vision-b-v2:0", "kind": "inference_profile"},
        ]})
        self.session_cls.assert_called_once_with(aws_access_key_id=KEY_ID, aws_secret_access_key=SECRET,
                                                 aws_session_token=None, region_name="us-west-2")
        self.assertEqual(self.session_cls.return_value.client.call_args.args, ("bedrock",))
        self.bedrock.list_foundation_models.assert_called_once_with(byOutputModality="TEXT")
        first, second = self.bedrock.list_inference_profiles.call_args_list
        self.assertNotIn("nextToken", first.kwargs)
        self.assertEqual(second.kwargs["nextToken"], "page-2")
        self.bedrock.converse.assert_not_called()

    def test_profile_permission_failure_returns_partial_list_with_warning(self):
        self.save_bedrock()
        self.bedrock.list_inference_profiles.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": SECRET}}, "ListInferenceProfiles")
        data = self.discover().get_json()
        self.assertEqual([m["id"] for m in data["models"]], ["vendor.vision-a-v1:0"])
        self.assertIn("manually", data["warning"])
        self.assert_no_secrets(json.dumps(data))

    def test_listing_failure_is_sanitized_502(self):
        self.save_bedrock()
        self.bedrock.list_foundation_models.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": f"denied {SECRET}"}}, "ListFoundationModels")
        resp = self.discover()
        self.assertEqual(resp.status_code, 502)
        self.assertIn("manually", resp.get_json()["error"])
        self.assert_no_secrets(resp.get_data(as_text=True))
        self.assert_no_secrets("\n".join(self.logs.lines))


class MergeBedrockTests(unittest.TestCase):
    def test_pure_merge_rules(self):
        first = merge_bedrock(None, {**CONNECTION, "region": " us-east-1 ", "session_token": f" {TOKEN} "})
        self.assertEqual(first, {"region": "us-east-1", "model": "", "access_key_id": KEY_ID,
                                 "secret_access_key": SECRET, "session_token": TOKEN})
        kept = merge_bedrock(first, {"region": "us-gov-west-1"})
        self.assertEqual({**first, "region": "us-gov-west-1"}, kept)
        with self.assertRaises(SettingsError) as caught:
            merge_bedrock(None, {"region": "us-east-1", "access_key_id": KEY_ID})
        self.assertEqual(caught.exception.status, 400)
        self.assertNotIn(KEY_ID, caught.exception.message)


if __name__ == "__main__":
    unittest.main()
