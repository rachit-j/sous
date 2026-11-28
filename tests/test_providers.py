import json
import os
import unittest
from unittest import mock

from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

import providers
from providers import InvalidOutput, ProviderError, analyze, parse_observation
from recipes import load_recipes
from settings import BedrockConnection

SCRAMBLED_EGGS = load_recipes()["scrambled-eggs"]  # bundled sample used as a fixture

JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 32 + b"\xff\xd9"
SECRET = "sk-test-secret-123"
GOOD = {"activity": "whisking eggs", "evidence": "uniform yellow liquid in bowl",
        "confidence": 0.9, "step_complete": True, "alert": None}
GOOD_TEXT = json.dumps(GOOD)
# Older replies omit the advisory fields; the parser adds their defaults.
PARSED = {**GOOD, "safety_alert": None, "view_obstructed": False}
CONTEXT = {
    "recipe": SCRAMBLED_EGGS,
    "step_index": 1,
    "step": SCRAMBLED_EGGS["steps"][1],
    "is_final_step": False,
    "mode": "monitoring",
    "elapsed_seconds": 75.4,
    "recent_observations": [{"activity": "cracking", "evidence": "shells on counter",
                             "confidence": 0.4, "step_complete": False, "created_at": 1.0}],
    "last_correction": "Corrected to step 2: Crack and whisk. Note: still whisking",
}
CONN = BedrockConnection("us-west-2", "AKIASENTINEL0000TEST", SECRET, None)


def converse_reply(text=GOOD_TEXT, stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": stop}


class ProviderTestCase(unittest.TestCase):
    def setUp(self):
        session = mock.patch("boto3.Session")
        self.session_cls = session.start()
        self.addCleanup(session.stop)
        self.client = self.session_cls.return_value.client.return_value

    def assert_safe_failure(self, message=None, provider="bedrock", conn=CONN):
        with self.assertRaises(ProviderError) as caught:
            analyze(provider, "test-model", JPEG, CONTEXT, bedrock=conn)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__ or caught.exception.__context__ is None)
        if message:
            self.assertIn(message, caught.exception.public_message)
        return caught.exception


class WireShapeTests(ProviderTestCase):
    def test_bedrock_converse_request_and_response(self):
        self.client.converse.return_value = converse_reply()
        arn = "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.example.model-v1:0"
        self.assertEqual(analyze("bedrock", arn, JPEG, CONTEXT, bedrock=CONN), PARSED)
        (service,), kwargs = self.session_cls.return_value.client.call_args
        config = kwargs["config"]
        self.assertEqual(service, "bedrock-runtime")
        self.assertEqual((config.connect_timeout, config.read_timeout), (5, 60))
        self.assertEqual(config.retries, {"max_attempts": 2, "mode": "standard"})
        sent = self.client.converse.call_args.kwargs
        self.assertEqual(sent["modelId"], arn)
        image, text = sent["messages"][0]["content"]
        self.assertEqual(image, {"image": {"format": "jpeg", "source": {"bytes": JPEG}}})
        self.assertIn("still whisking", text["text"])
        self.assertIn(SCRAMBLED_EGGS["steps"][1]["visual_cue"], text["text"])

    def test_bedrock_shared_connection_maps_explicit_credentials(self):
        with mock.patch.dict(os.environ, {"AWS_PROFILE": "cook", "AWS_REGION": "eu-west-1"}):
            env_before = dict(os.environ)
            self.client.converse.return_value = converse_reply()
            self.assertEqual(analyze("bedrock", "vision-model", JPEG, CONTEXT, bedrock=CONN), PARSED)
            self.session_cls.assert_called_once_with(
                aws_access_key_id="AKIASENTINEL0000TEST", aws_secret_access_key=SECRET,
                aws_session_token=None, region_name="us-west-2")
            self.assertEqual(dict(os.environ), env_before)
        self.assertNotIn(SECRET, repr(CONN))


class FailureTests(ProviderTestCase):
    def test_only_bedrock_with_an_explicit_connection(self):
        # V3 removed local and other cloud adapters; no ambient-credential fallback exists.
        for provider in ("ollama", "openai", "anthropic", "mystery"):
            with self.subTest(provider=provider):
                self.assert_safe_failure("Unknown provider", provider=provider)
        self.assert_safe_failure("Unknown provider", conn=None)
        self.session_cls.assert_not_called()
        for name in ("ollama_chat", "openai_responses", "anthropic_messages"):
            self.assertFalse(hasattr(providers, name))

    def test_refusals_and_incomplete_responses(self):
        for stop, message in (("guardrail_intervened", "declined"), ("content_filtered", "declined"),
                              ("max_tokens", "cut off")):
            with self.subTest(stop=stop):
                self.client.converse.return_value = converse_reply("{", stop)
                self.assert_safe_failure(message)

    def test_empty_or_prose_model_output_is_invalid(self):
        for text in ("", f"Sure! {GOOD_TEXT}"):
            with self.subTest(text=text[:10]):
                self.client.converse.return_value = converse_reply(text)
                with self.assertRaises(InvalidOutput):
                    analyze("bedrock", "m", JPEG, CONTEXT, bedrock=CONN)

    def test_bedrock_errors_are_sanitized(self):
        converse = self.client.converse
        cases = (
            (ClientError({"Error": {"Code": "AccessDeniedException", "Message": SECRET}}, "Converse"),
             "credentials"),
            (ClientError({"Error": {"Code": "ValidationException", "Message": SECRET}}, "Converse"), "model ID"),
            (NoCredentialsError(), "credentials are not available"),
            (ReadTimeoutError(endpoint_url=f"https://bedrock/{SECRET}"), "timed out"),
        )
        for error, message in cases:
            with self.subTest(message=message):
                converse.side_effect = error
                self.assert_safe_failure(message)


class ParseTests(unittest.TestCase):
    def test_accepts_plain_or_single_fenced_json_and_drops_extra_keys(self):
        self.assertEqual(parse_observation(GOOD_TEXT), PARSED)
        self.assertEqual(parse_observation("```json\n" + GOOD_TEXT + "\n```"), PARSED)
        self.assertEqual(parse_observation(json.dumps({**GOOD, "next_step": 5})), PARSED)

    def test_safety_fields_are_normalized_and_strictly_validated(self):
        alert = {"category": "possible_emergency", "title": "  Smoke-like haze  ",
                 "evidence": "Gray haze above the pan; uncertain", "extra": "dropped"}
        parsed = parse_observation(json.dumps({**GOOD, "safety_alert": alert, "view_obstructed": True}))
        self.assertEqual(parsed["safety_alert"], {"category": "possible_emergency", "title": "Smoke-like haze",
                                                  "evidence": "Gray haze above the pan; uncertain"})
        self.assertIs(parsed["view_obstructed"], True)
        explicit_none = parse_observation(json.dumps({**GOOD, "safety_alert": None, "view_obstructed": False}))
        self.assertEqual(explicit_none, PARSED)
        for bad in ({"safety_alert": "fire"}, {"safety_alert": []},
                    {"safety_alert": {"category": "hazard", "title": "", "evidence": "x"}},
                    {"safety_alert": {"category": "hazard", "title": "x"}},
                    {"safety_alert": {"category": "HAZARD", "title": "x", "evidence": "y"}},
                    {"view_obstructed": "yes"}, {"view_obstructed": 1}):
            with self.subTest(bad=bad), self.assertRaises(InvalidOutput):
                parse_observation(json.dumps({**GOOD, **bad}))

    def test_schema_and_prompts_describe_advisory_safety_fields(self):
        schema = providers.OBSERVATION_SCHEMA
        self.assertEqual(set(schema["required"]), set(providers.FIELDS + providers.SAFETY_FIELDS))
        self.assertEqual(schema["properties"]["view_obstructed"], {"type": "boolean"})
        alert_object = schema["properties"]["safety_alert"]["anyOf"][1]
        self.assertEqual(alert_object["properties"]["category"]["enum"], ["hazard", "possible_emergency"])
        system = providers.SYSTEM_PROMPT.lower()
        for phrase in ("possible_emergency", "uncertain", "null never means it is"):
            self.assertIn(phrase, system)
        self.assertIn("view_obstructed", providers.build_prompt(CONTEXT))

    def test_rejects_non_strict_output(self):
        for text in (None, "   ", f"Answer: {GOOD_TEXT}", f"{GOOD_TEXT} trailing",
                     "```json\n" + GOOD_TEXT + "\n```\n```json\n{}\n```",
                     GOOD_TEXT.replace("0.9", "NaN"), json.dumps([GOOD]),
                     json.dumps({k: v for k, v in GOOD.items() if k != "alert"})):
            with self.subTest(text=str(text)[:30]), self.assertRaises(InvalidOutput):
                parse_observation(text)


if __name__ == "__main__":
    unittest.main()
