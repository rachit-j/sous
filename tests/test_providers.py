import base64
import json
import os
import unittest
from unittest import mock

import requests
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

import providers
from providers import InvalidOutput, ProviderError, analyze, parse_observation
from recipes import load_recipes
from settings import BedrockConnection

SCRAMBLED_EGGS = load_recipes()["scrambled-eggs"]  # bundled sample used as a fixture

JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 32 + b"\xff\xd9"
JPEG_B64 = base64.b64encode(JPEG).decode()
SECRET = "sk-test-secret-123"
GOOD = {"activity": "whisking eggs", "evidence": "uniform yellow liquid in bowl",
        "confidence": 0.9, "step_complete": True, "alert": None}
GOOD_TEXT = json.dumps(GOOD)
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
ENV = {
    "OLLAMA_BASE_URL": "http://ollama.local:11434/",
    "OPENAI_API_KEY": SECRET,
    "ANTHROPIC_API_KEY": SECRET,
    "AWS_REGION": "us-west-2",
}


def http_response(body=None, status=200, malformed=False):
    resp = mock.Mock(status_code=status)
    if malformed:
        resp.json.side_effect = ValueError(f"Expecting value: {SECRET}")
    else:
        resp.json.return_value = body
    return resp


OLLAMA_OK = {"model": "qwen3-vl:8b", "message": {"role": "assistant", "content": GOOD_TEXT},
             "done": True, "done_reason": "stop"}
OPENAI_OK = {
    "id": "resp_1", "object": "response", "status": "completed",
    "output": [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
         "content": [{"type": "output_text", "text": GOOD_TEXT, "annotations": []}]},
    ],
}
ANTHROPIC_OK = {
    "id": "msg_1", "type": "message", "role": "assistant", "stop_reason": "end_turn",
    "content": [{"type": "thinking", "thinking": "private reasoning", "signature": "sig"},
                {"type": "text", "text": "```json\n" + GOOD_TEXT + "\n```"}],
}
HTTP_CASES = (("ollama", OLLAMA_OK), ("openai", OPENAI_OK), ("anthropic", ANTHROPIC_OK))


class ProviderTestCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)
        post = mock.patch("providers.requests.post")
        self.post = post.start()
        self.addCleanup(post.stop)

    def call(self, provider, body=None, model="test-model", **response):
        self.post.return_value = http_response(body, **response)
        return analyze(provider, model, JPEG, CONTEXT)

    def sent(self):
        args, kwargs = self.post.call_args
        return args[0], kwargs["headers"], kwargs["json"], kwargs["timeout"]

    def assert_safe_failure(self, provider, message=None):
        with self.assertRaises(ProviderError) as caught:
            analyze(provider, "test-model", JPEG, CONTEXT)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__ or caught.exception.__context__ is None)
        if message:
            self.assertIn(message, caught.exception.public_message)
        return caught.exception


class WireShapeTests(ProviderTestCase):
    def test_ollama_chat_request_and_response(self):
        self.assertEqual(self.call("ollama", OLLAMA_OK, model="qwen3-vl:8b"), GOOD)
        url, _headers, body, timeout = self.sent()
        self.assertEqual(url, "http://ollama.local:11434/api/chat")
        self.assertEqual(timeout, (5, 180))
        self.assertIs(body["stream"], False)
        self.assertEqual(body["model"], "qwen3-vl:8b")
        self.assertEqual(body["format"], providers.OBSERVATION_SCHEMA)
        system, user = body["messages"]
        self.assertEqual(system["role"], "system")
        self.assertEqual(user["images"], [JPEG_B64])
        for expected in (SCRAMBLED_EGGS["steps"][1]["visual_cue"], "still whisking",
                         "shells on counter", "1m 15s", "step 2 of 6"):
            self.assertIn(expected, user["content"])

    def test_ollama_defaults_to_localhost(self):
        del os.environ["OLLAMA_BASE_URL"]
        self.call("ollama", OLLAMA_OK)
        self.assertEqual(self.sent()[0], "http://localhost:11434/api/chat")

    def test_openai_responses_request_and_output_parsing(self):
        self.assertEqual(self.call("openai", OPENAI_OK), GOOD)
        url, headers, body, timeout = self.sent()
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(headers, {"Authorization": f"Bearer {SECRET}"})
        self.assertEqual(timeout, (5, 60))
        self.assertIs(body["store"], False)
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        text, image = body["input"][0]["content"]
        self.assertEqual(text["type"], "input_text")
        self.assertEqual(image, {"type": "input_image", "image_url": "data:image/jpeg;base64," + JPEG_B64})

    def test_anthropic_messages_request_and_text_only_parsing(self):
        self.assertEqual(self.call("anthropic", ANTHROPIC_OK), GOOD)
        url, headers, body, timeout = self.sent()
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers, {"x-api-key": SECRET, "anthropic-version": "2023-06-01"})
        self.assertEqual(timeout, (5, 60))
        image, text = body["messages"][0]["content"]
        self.assertEqual(image, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                             "data": JPEG_B64}})
        self.assertEqual(text["type"], "text")
        self.assertIn(SCRAMBLED_EGGS["steps"][1]["visual_cue"], text["text"])

    @mock.patch("boto3.Session")
    def test_bedrock_converse_request_and_response(self, session_cls):
        os.environ["AWS_PROFILE"] = "cook"
        client = session_cls.return_value.client.return_value
        client.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [{"text": GOOD_TEXT}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        }
        arn = "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.example.model-v1:0"
        self.assertEqual(analyze("bedrock", arn, JPEG, CONTEXT), GOOD)
        session_cls.assert_called_once_with(profile_name="cook", region_name="us-west-2")
        (service,), kwargs = session_cls.return_value.client.call_args
        config = kwargs["config"]
        self.assertEqual(service, "bedrock-runtime")
        self.assertEqual((config.connect_timeout, config.read_timeout), (5, 60))
        self.assertEqual(config.retries, {"max_attempts": 2, "mode": "standard"})
        sent = client.converse.call_args.kwargs
        self.assertEqual(sent["modelId"], arn)
        image, text = sent["messages"][0]["content"]
        self.assertEqual(image, {"image": {"format": "jpeg", "source": {"bytes": JPEG}}})
        self.assertIn("still whisking", text["text"])
        self.post.assert_not_called()

    @mock.patch("boto3.Session")
    def test_bedrock_managed_connection_maps_explicit_credentials(self, session_cls):
        os.environ.update(AWS_PROFILE="cook", AWS_REGION="eu-west-1")
        env_before = dict(os.environ)
        client = session_cls.return_value.client.return_value
        client.converse.return_value = {"output": {"message": {"content": [{"text": GOOD_TEXT}]}},
                                        "stopReason": "end_turn"}
        conn = BedrockConnection("us-west-2", "AKIASENTINEL0000TEST", SECRET, None)
        self.assertEqual(analyze("bedrock", "vision-model", JPEG, CONTEXT, bedrock=conn), GOOD)
        session_cls.assert_called_once_with(aws_access_key_id="AKIASENTINEL0000TEST", aws_secret_access_key=SECRET,
                                            aws_session_token=None, region_name="us-west-2")
        self.assertEqual(dict(os.environ), env_before)
        self.assertNotIn(SECRET, repr(conn))
        with self.assertRaises(ProviderError):
            analyze("openai", "gpt-x", JPEG, CONTEXT, bedrock=conn)  # never sent to another provider
        self.post.assert_not_called()


class FailureTests(ProviderTestCase):
    def test_http_failures_are_static_and_sanitized(self):
        failures = (
            (dict(side_effect=requests.Timeout(SECRET)), "timed out"),
            (dict(side_effect=requests.ConnectionError(f"https://x?key={SECRET}")), "Could not reach"),
            (dict(return_value=http_response({"error": SECRET}, status=401)), "credentials"),
            (dict(return_value=http_response({"error": SECRET}, status=404)), "not found"),
            (dict(return_value=http_response({"error": SECRET}, status=500)), "HTTP 500"),
            (dict(return_value=http_response(malformed=True)), "malformed"),
            (dict(return_value=http_response([SECRET])), "malformed"),
        )
        for provider, _ in HTTP_CASES:
            for setup, message in failures:
                with self.subTest(provider=provider, message=message):
                    self.post.reset_mock(side_effect=True, return_value=True)
                    self.post.configure_mock(**setup)
                    self.assert_safe_failure(provider, message)

    def test_refusals_and_incomplete_responses(self):
        cases = (
            ("openai", {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "refusal", "refusal": "I can't help with that."}]}]}, "declined"),
            ("openai", {"status": "incomplete", "output": []}, "incomplete"),
            ("anthropic", {"stop_reason": "refusal", "content": []}, "declined"),
            ("anthropic", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}, "cut off"),
            ("ollama", {"message": {"content": "{"}, "done_reason": "length"}, "cut off"),
        )
        for provider, body, message in cases:
            with self.subTest(provider=provider, message=message):
                self.post.return_value = http_response(body)
                self.assert_safe_failure(provider, message)

    def test_empty_or_prose_model_output_is_invalid(self):
        bodies = {
            "ollama": lambda text: {"message": {"content": text}, "done_reason": "stop"},
            "openai": lambda text: {"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}]},
            "anthropic": lambda text: {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]},
        }
        for provider, body in bodies.items():
            for text in ("", f"Sure! {GOOD_TEXT}"):
                with self.subTest(provider=provider, text=text[:10]):
                    with self.assertRaises(InvalidOutput):
                        self.call(provider, body(text))

    def test_missing_configuration_fails_before_any_request(self):
        for name, provider in (("OPENAI_API_KEY", "openai"), ("ANTHROPIC_API_KEY", "anthropic"),
                               ("AWS_REGION", "bedrock")):
            with self.subTest(provider=provider), mock.patch.dict(os.environ, {name: ""}):
                self.assert_safe_failure(provider, "not configured")
        os.environ["OLLAMA_BASE_URL"] = "file:///etc/passwd"
        self.assert_safe_failure("ollama", "http")
        self.assert_safe_failure("mystery", "Unknown provider")
        self.post.assert_not_called()

    @mock.patch("boto3.Session")
    def test_bedrock_errors_are_sanitized(self, session_cls):
        converse = session_cls.return_value.client.return_value.converse
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
                self.assert_safe_failure("bedrock", message)
        converse.side_effect = None
        converse.return_value = {"output": {"message": {"content": []}}, "stopReason": "guardrail_intervened"}
        self.assert_safe_failure("bedrock", "declined")


class ParseTests(unittest.TestCase):
    def test_accepts_plain_or_single_fenced_json_and_drops_extra_keys(self):
        self.assertEqual(parse_observation(GOOD_TEXT), GOOD)
        self.assertEqual(parse_observation("```json\n" + GOOD_TEXT + "\n```"), GOOD)
        self.assertEqual(parse_observation(json.dumps({**GOOD, "next_step": 5})), GOOD)

    def test_rejects_non_strict_output(self):
        for text in (None, "   ", f"Answer: {GOOD_TEXT}", f"{GOOD_TEXT} trailing",
                     "```json\n" + GOOD_TEXT + "\n```\n```json\n{}\n```",
                     GOOD_TEXT.replace("0.9", "NaN"), json.dumps([GOOD]),
                     json.dumps({k: v for k, v in GOOD.items() if k != "alert"})):
            with self.subTest(text=str(text)[:30]), self.assertRaises(InvalidOutput):
                parse_observation(text)


if __name__ == "__main__":
    unittest.main()
