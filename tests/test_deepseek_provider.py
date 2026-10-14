import importlib.util
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from extract_repo_skills.contracts import ValidationError
from extract_repo_skills.deepseek_provider import BASE_URL, assess


@unittest.skipUnless(importlib.util.find_spec("openai"), "Install the optional llm extra to test the SDK adapter")
class DeepSeekProviderTests(unittest.TestCase):
    def setUp(self):
        from openai import OpenAI

        self.real_client_class = OpenAI
        self.prepared = {
            "request": {
                "messages": [{"role": "system", "content": "Assess changes as JSON"},
                             {"role": "user", "content": "{}"}],
                "response_format": {"type": "json_object"},
            },
            "prompt_version": "test", "input_sha256": "digest", "commit": "a" * 40,
            "report_context": {"catalog": {"id": "test", "version": "1", "sha256": "digest"}},
        }
        self.message = SimpleNamespace(content=json.dumps({"assessments": []}), tool_calls=None, refusal=None)
        self.choice = SimpleNamespace(finish_reason="stop", message=self.message)
        self.response = SimpleNamespace(
            choices=[self.choice],
            model="resolved-test-model", id="test-response", usage=None,
        )
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        sdk = patch("openai.OpenAI")
        self.client_class = sdk.start()
        self.addCleanup(sdk.stop)
        self.create = self.client_class.return_value.__enter__.return_value.chat.completions.create
        self.create.return_value = self.response

    def test_requests_json_from_deepseek_without_retries(self):
        result, run = assess(self.prepared, "chosen-model")
        self.assertEqual(result, {"assessments": []})
        request = self.create.call_args.kwargs
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(request["max_tokens"], 8000)
        self.assertEqual(request["model"], "chosen-model")
        self.assertEqual(self.client_class.call_args.kwargs["max_retries"], 0)
        self.assertEqual(self.client_class.call_args.kwargs["base_url"], BASE_URL)
        self.assertEqual(self.client_class.call_args.kwargs["api_key"], "test-key")
        self.assertEqual(run["provider"], "deepseek")
        self.assertEqual(run["returned_model"], "resolved-test-model")
        self.assertTrue(run["human_review_required"])
        self.assertNotIn("git_evidence_verified", run)

    def test_repository_preparation_records_multiple_commits(self):
        del self.prepared["commit"]
        self.prepared["commits"] = ["a" * 40, "b" * 40]
        result, run = assess(self.prepared, "chosen-model")
        self.assertEqual(result, {"assessments": []})
        self.assertEqual(run["commits"], self.prepared["commits"])
        self.assertNotIn("commit", run)

    def test_incomplete_refused_empty_and_invalid_output_are_rejected(self):
        for reason, refusal, text in (
            ("length", None, '{"assessments": []}'),
            ("content_filter", None, ""),
            ("stop", "Declined", ""),
            ("stop", None, ""),
            ("stop", None, "not JSON"),
            ("stop", None, '{"assessments": [], "assessments": []}'),
        ):
            with self.subTest(reason=reason, text=text):
                self.choice.finish_reason = reason
                self.message.refusal, self.message.content = refusal, text
                with self.assertRaises(ValidationError):
                    assess(self.prepared, "chosen-model")

    def test_missing_credentials_and_invalid_settings_fail_before_request(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaisesRegex(ValidationError, "DEEPSEEK_API_KEY"):
                assess(self.prepared, "chosen-model")
        with self.assertRaisesRegex(ValidationError, "Choose a model"):
            assess(self.prepared, "")
        with self.assertRaisesRegex(ValidationError, "positive"):
            assess(self.prepared, "chosen-model", max_output_tokens=0)
        self.create.assert_not_called()

    def test_api_failure_does_not_echo_private_request_data(self):
        from openai import APIConnectionError

        self.create.side_effect = APIConnectionError(message="private source text", request=SimpleNamespace())
        with self.assertRaisesRegex(ValidationError, "DeepSeek request failed") as caught:
            assess(self.prepared, "chosen-model")
        self.assertNotIn("private source text", str(caught.exception))

    def test_real_sdk_serialization_and_response_parsing_with_mock_transport(self):
        import httpx2

        requests = []

        def handle(request):
            requests.append(request)
            return httpx2.Response(200, json={
                "id": "mock-completion", "object": "chat.completion", "created": 0,
                "model": "test-model", "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"assessments": []}'},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })

        client = self.real_client_class(
            api_key="test-key", base_url=BASE_URL, max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
        )
        self.client_class.return_value = client
        result, run = assess(self.prepared, "chosen-model")
        self.assertEqual(result, {"assessments": []})
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), BASE_URL + "/chat/completions")
        body = json.loads(requests[0].content)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(run["usage"]["total_tokens"], 15)
