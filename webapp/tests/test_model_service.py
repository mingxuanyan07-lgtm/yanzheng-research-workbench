"""Mock-only coverage for explicit opt-in, bounded calls and safe provenance."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import model_service
from model_service import ModelService, ModelServiceError

KEY = "sk-fixture-do-not-use-0123456789"
URL = "https://fixture-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def good_response(**updates):
    body = {
        "id": "chatcmpl-fixture", "model": "qwen-plus-2026-01-01",
        "choices": [{"finish_reason": "stop", "message": {"content": '{"topics": []}'}}],
        "usage": {"prompt_tokens": 37, "completion_tokens": 12, "total_tokens": 49},
    }
    body.update(updates)
    return body


class ModelServiceTest(unittest.TestCase):
    def setUp(self):
        temp_root = BASE / "tests/.tmp"
        temp_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.env_file = Path(self.temporary.name) / ".env"
        self.environ = {
            "YANZHENG_MODEL_PROVIDER": "qwen", "DASHSCOPE_API_KEY": KEY,
            "QWEN_BASE_URL": URL,
        }
        self.messages = [{"role": "user", "content": "Return a JSON research draft."}]

    def tearDown(self):
        self.temporary.cleanup()

    def service(self, handler=None, environ=None):
        handler = handler or (lambda request: httpx.Response(200, json=good_response()))
        return ModelService(env_file=self.env_file,
                            environ=self.environ if environ is None else environ,
                            transport=httpx.MockTransport(handler))

    def assert_secret_absent(self, value):
        self.assertNotIn(KEY, str(value))
        self.assertNotIn(URL, str(value))

    def test_demo_default_does_not_send_or_assume_region(self):
        calls = []
        service = self.service(lambda request: calls.append(request), environ={})
        status = service.status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["state"], "not_configured")
        with self.assertRaises(ModelServiceError) as raised:
            service.complete(self.messages)
        self.assertEqual(raised.exception.record["attempts"], 0)
        self.assertEqual(calls, [])
        self.assertIsNone(service.status()["last_call"])

    def test_credentials_without_explicit_qwen_mode_do_not_send(self):
        self.environ.pop("YANZHENG_MODEL_PROVIDER")
        service = self.service()
        self.assertFalse(service.status()["configured"])
        with self.assertRaises(ModelServiceError):
            service.complete(self.messages)

    def test_file_is_data_environment_wins_and_changes_clear_verification(self):
        self.env_file.write_text(
            "# local only\nYANZHENG_MODEL_PROVIDER=qwen\n"
            "DASHSCOPE_API_KEY=sk-wrong-fixture-12345\n"
            "QWEN_MODEL=qwen-turbo\nQWEN_BASE_URL=" + URL + "\n"
            "UNKNOWN=$(must-never-execute)\n", encoding="utf-8")
        service = self.service(environ={"DASHSCOPE_API_KEY": KEY})
        self.assertEqual(service.status()["model"], "qwen-turbo")
        service.complete(self.messages)
        self.assertEqual(service.status()["state"], "verified")
        self.env_file.write_text(self.env_file.read_text().replace("qwen-turbo", "qwen-plus"))
        status = service.status()
        self.assertEqual(status["state"], "configured")
        self.assertIsNone(status["last_call"])
        self.assert_secret_absent(status)

    def test_key_change_requires_new_verification(self):
        service = self.service()
        service.complete(self.messages)
        self.environ["DASHSCOPE_API_KEY"] = "sk-new-fixture-key-12345"
        self.assertEqual(service.status()["state"], "configured")

    def test_refresh_during_old_call_cannot_verify_new_key(self):
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer " + KEY)
            self.environ["DASHSCOPE_API_KEY"] = "sk-replaced-during-request-01234"
            self.assertEqual(service.status()["state"], "configured")
            return httpx.Response(200, json=good_response())
        service = self.service(handler)
        _, record = service.complete(self.messages)
        self.assertEqual(record["status"], "success")
        self.assertEqual(service.status()["state"], "configured")
        self.assertIsNone(service.status()["last_call"])

    def test_official_domains_only_and_no_url_tricks(self):
        valid = [
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/",
            "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
            URL,
            "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        ]
        for url in valid:
            with self.subTest(valid=url):
                self.environ["QWEN_BASE_URL"] = url
                self.assertTrue(self.service().status()["configured"])
        invalid = [
            "", "http://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com.evil.example/compatible-mode/v1",
            "https://evil-dashscope.aliyuncs.com/compatible-mode/v1",
            "https://user:pass@dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com:444/compatible-mode/v1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1?",
            "https://dashscope.aliyuncs.com/compatible-mode/v1#",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "https://one.two.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "https://workspace.unknown-region.maas.aliyuncs.com/compatible-mode/v1",
            "https://127.0.0.1/compatible-mode/v1",
            "https://dashscope.aliyuncs.com\\@127.0.0.1/compatible-mode/v1",
        ]
        for url in invalid:
            with self.subTest(invalid=url):
                self.environ["QWEN_BASE_URL"] = url
                status = self.service().status()
                self.assertFalse(status["configured"])
                self.assertIn("configuration_error", status)
                self.assert_secret_absent(status)

    def test_success_request_has_fixed_cost_controls_and_real_usage(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(str(request.url), URL + "/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer " + KEY)
            payload = json.loads(request.content)
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            for flag in ("enable_thinking", "enable_search", "stream"):
                self.assertIs(payload[flag], False)
            self.assertEqual(payload["max_tokens"], 3500)
            self.assertEqual(payload["temperature"], 0.4)
            return httpx.Response(200, json=good_response(), headers={"x-request-id": "fixture-id"})
        service = self.service(handler)
        self.assertEqual(service.status()["state"], "configured")
        parsed, record = service.complete(self.messages)
        self.assertEqual(parsed, {"topics": []})
        self.assertEqual(record["usage"], {"prompt_tokens": 37, "completion_tokens": 12, "total_tokens": 49})
        self.assertEqual(record["request_id"], "fixture-id")
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["requested_model"], "qwen-plus")
        self.assertEqual(record["model"], "qwen-plus-2026-01-01")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["prompt_version"], "direction-v1")
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.status()["state"], "verified")
        self.assert_secret_absent(service.status())
        self.assert_secret_absent(repr(service))

    def test_provider_errors_are_redacted_and_usage_retained(self):
        for code in (400, 401, 403, 404, 429, 500):
            with self.subTest(status=code):
                service = self.service(lambda request: httpx.Response(code, json={
                    "message": "secret " + KEY + " " + URL,
                    "usage": {"prompt_tokens": 42, "completion_tokens": 0, "total_tokens": 42},
                }))
                with self.assertRaises(ModelServiceError) as raised:
                    service.complete(self.messages)
                error = raised.exception
                self.assertEqual(error.record["attempts"], 1)
                self.assertEqual(error.record["usage"]["total_tokens"], 42)
                self.assertEqual(service.status()["state"], "error")
                self.assert_secret_absent(str(error))
                self.assert_secret_absent(error.record)
                self.assert_secret_absent(service.status())

    def test_no_redirect_is_followed(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(307, headers={"location": "https://outside.example/steal"})
        service = self.service(handler)
        with self.assertRaises(ModelServiceError):
            service.complete(self.messages)
        self.assertEqual(len(calls), 1)

    def test_transport_timeout_is_safe_and_never_retried(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("raw secret " + KEY, request=request)
        service = self.service(handler)
        with self.assertRaises(ModelServiceError) as raised:
            service.complete(self.messages)
        self.assertEqual(raised.exception.http_status, 504)
        self.assertEqual(raised.exception.record["error_code"], "timeout")
        self.assertEqual(len(calls), 1)
        self.assert_secret_absent(raised.exception)

    def test_non_stop_json_and_missing_choices_never_apply(self):
        variants = [
            (good_response(choices=[{"finish_reason": "length", "message": {"content": "{}"}}]), "incomplete_response"),
            (good_response(choices=[{"finish_reason": "content_filter", "message": {"content": "{}"}}]), "incomplete_response"),
            (good_response(choices=[]), "invalid_response"),
            (good_response(choices=[{"finish_reason": "stop", "message": {"content": "not json"}}]), "invalid_json"),
            (good_response(choices=[{"finish_reason": "stop", "message": {"content": "[]"}}]), "invalid_json"),
            (good_response(choices=[{"finish_reason": "stop", "message": {"content": '{"score": NaN}'}}]), "invalid_json"),
            (good_response(choices=[{"finish_reason": "stop", "message": {"content": {}, "refusal": "no"}}]), "invalid_response"),
        ]
        for body, error_code in variants:
            with self.subTest(error_code=error_code, body=body):
                service = self.service(lambda request: httpx.Response(200, json=body))
                with self.assertRaises(ModelServiceError) as raised:
                    service.complete(self.messages)
                self.assertEqual(raised.exception.record["error_code"], error_code)
                self.assertEqual(raised.exception.record["usage"]["total_tokens"], 49)

    def test_response_limit_is_enforced(self):
        service = self.service(lambda request: httpx.Response(200, content=b"x" * (model_service.MAX_RESPONSE_BYTES + 1)))
        with self.assertRaises(ModelServiceError) as raised:
            service.complete(self.messages)
        self.assertEqual(raised.exception.record["error_code"], "response_too_large")

    def test_usage_only_accepts_real_nonnegative_integers(self):
        service = self.service(lambda request: httpx.Response(200, json=good_response(
            usage={"prompt_tokens": True, "completion_tokens": -2, "total_tokens": "10"})))
        _, record = service.complete(self.messages)
        self.assertEqual(record["usage"], {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None})

    def test_gate_allows_only_one_call_across_instances(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def handler(request):
            entered.set()
            release.wait(5)
            return httpx.Response(200, json=good_response())
        first, second = self.service(handler), self.service()
        thread = threading.Thread(target=lambda: result.append(first.complete(self.messages)))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(ModelServiceError) as raised:
                second.complete(self.messages)
            self.assertEqual(raised.exception.record["error_code"], "busy")
            self.assertEqual(raised.exception.record["attempts"], 0)
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(len(result), 1)

    def test_application_rejection_clears_success_without_losing_usage(self):
        service = self.service()
        _, record = service.complete(self.messages)
        record.update(status="failed", error_code="invalid_plan", application_status="rejected",
                      summary="方向草案未通过业务校验。")
        service.record_result(record)
        status = service.status()
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["last_call"]["application_status"], "rejected")
        self.assertEqual(status["last_call"]["usage"]["total_tokens"], 49)
        status["last_call"]["usage"]["total_tokens"] = 0
        self.assertEqual(service.status()["last_call"]["usage"]["total_tokens"], 49)

    def test_configuration_limits_and_invalid_messages_skip_network(self):
        for key, value in (("QWEN_MAX_TOKENS", "6001"), ("QWEN_TIMEOUT_SECONDS", "NaN"),
                           ("QWEN_TIMEOUT_SECONDS", "0"), ("QWEN_MAX_TOKENS", "$(echo oops)")):
            with self.subTest(key=key, value=value):
                environment = dict(self.environ, **{key: value})
                service = self.service(environ=environment)
                with self.assertRaises(ModelServiceError) as raised:
                    service.complete(self.messages)
                self.assertEqual(raised.exception.record["attempts"], 0)
        with self.assertRaises(ModelServiceError) as raised:
            self.service().complete([{"role": "tool", "content": "bad input"}])
        self.assertEqual(raised.exception.record["error_code"], "invalid_messages")

    def test_provider_echo_cannot_expose_key_as_metadata(self):
        body = good_response(model=KEY, request_id=KEY)
        service = self.service(lambda request: httpx.Response(200, json=body,
                                                            headers={"x-request-id": KEY}))
        _, record = service.complete(self.messages)
        self.assertEqual(record["model"], "qwen-plus")
        self.assertIsNone(record["request_id"])
        self.assert_secret_absent(service.status())


if __name__ == "__main__":
    unittest.main()
