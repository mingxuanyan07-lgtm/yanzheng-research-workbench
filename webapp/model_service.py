"""Bounded, explicit opt-in access to the official Bailian Qwen endpoint.

No credentials, prompts, provider response bodies or endpoint URLs are logged.
The service reads local configuration on each operation without executing it.
"""

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

BASE = Path(__file__).resolve().parent
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CAPTURE_CHARACTERS = 50000


def capture_text(text, *, kind="model_content", complete=True, secret=""):
    """Bound actual response text before parsing; never fabricate a JSON draft."""
    if not isinstance(text, str):
        return {"kind": "missing", "text": "", "complete": False,
                "truncated": False, "sha256": None, "limit_characters": MAX_CAPTURE_CHARACTERS}
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest() if complete else None
    redacted = bool(secret and secret in text)
    safe = text.replace(secret, "[REDACTED_CREDENTIAL]") if redacted else text
    return {"kind": kind, "text": safe[:MAX_CAPTURE_CHARACTERS],
            "text_representation": "provider_message_string" if kind == "model_content" else "response_body_decoded_utf8_with_replacement",
            "hash_scope": "UTF-8 encoded text, not HTTP wire bytes",
            "complete": complete, "truncated": len(safe) > MAX_CAPTURE_CHARACTERS or not complete,
            "original_characters": len(text) if complete else None,
            "sha256": digest, "redacted": redacted,
            "saved_sha256": hashlib.sha256(safe[:MAX_CAPTURE_CHARACTERS].encode("utf-8", errors="replace")).hexdigest(),
            "limit_characters": MAX_CAPTURE_CHARACTERS}
PROMPT_VERSION = "direction-v1"
_REQUEST_GATE = threading.Lock()
_ALLOWED_KEYS = frozenset({
    "YANZHENG_MODEL_PROVIDER", "DASHSCOPE_API_KEY", "QWEN_BASE_URL",
    "QWEN_MODEL", "QWEN_TIMEOUT_SECONDS", "QWEN_MAX_TOKENS",
})
_MODEL_RE = re.compile(r"[A-Za-z0-9._:/-]{1,100}\Z")
_WORKSPACE_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
    r"(?:cn-beijing|ap-southeast-1|us-east-1|ap-northeast-1|cn-hongkong|eu-central-1)"
    r"\.maas\.aliyuncs\.com\Z"
)
_OLD_HOSTS = frozenset({
    "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
})


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_id(value, secret=""):
    if (isinstance(value, str) and not (secret and secret in value)
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)):
        return value
    return None


def _usage(value):
    value = value if isinstance(value, dict) else {}
    return {
        key: item if type(item := value.get(key)) is int and item >= 0 else None
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _valid_base_url(value):
    """Return a normalized exact official compatible-mode root, or None."""
    if not isinstance(value, str) or not value or re.search(r"[\s\\]", value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)
                or parsed.path.rstrip("/") != "/compatible-mode/v1"
                or not (host in _OLD_HOSTS or _WORKSPACE_HOST_RE.fullmatch(host))):
            return None
        # Reject delimiters even when urllib represents their empty values alike.
        if "?" in value or "#" in value or "@" in parsed.netloc:
            return None
        return f"https://{host}/compatible-mode/v1"
    except (ValueError, TypeError):
        return None


class ModelServiceError(Exception):
    """An explicitly safe public error; never holds raw provider exceptions."""

    def __init__(self, public_message, http_status, record):
        super().__init__(public_message)
        self.public_message = public_message
        self.http_status = http_status
        self.record = copy.deepcopy(record)


class ModelService:
    def __init__(self, env_file=BASE / ".env", environ=None, transport=None):
        self._env_file = Path(env_file)
        self._environ = os.environ if environ is None else environ
        self._transport = transport
        self._state_lock = threading.Lock()
        self._active_signature = None
        self._last_call = None
        self._last_call_signature = None
        self._outcome = None

    def _configuration(self):
        values = {}
        file_error = None
        try:
            # Bound local input, and never include its contents in an exception.
            with self._env_file.open("r", encoding="utf-8-sig") as handle:
                contents = handle.read(65537)
            if len(contents) > 65536:
                file_error = "本机配置文件过大，请使用配置脚本重新保存。"
            else:
                for line in contents.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, separator, value = line.partition("=")
                    key = key.strip()
                    if key not in _ALLOWED_KEYS:
                        continue
                    if not separator:
                        file_error = "本机配置格式不正确，请使用配置脚本重新保存。"
                        continue
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    values[key] = value
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            file_error = "无法读取本机配置文件，请检查文件权限或重新配置。"
        for key in _ALLOWED_KEYS:
            if key in self._environ:
                values[key] = self._environ[key]

        provider = values.get("YANZHENG_MODEL_PROVIDER", "demo").strip().lower()
        requested_model = values.get("QWEN_MODEL", "qwen-plus").strip()
        key = values.get("DASHSCOPE_API_KEY", "").strip()
        model = requested_model if (_MODEL_RE.fullmatch(requested_model)
                                    and not (key and key in requested_model)) else "qwen-plus"
        raw_url = values.get("QWEN_BASE_URL", "").strip()
        url = _valid_base_url(raw_url)
        error = file_error
        timeout = 120
        max_tokens = 3500
        if provider not in ("demo", "qwen"):
            error = "模型提供方配置不正确，请选择 qwen 或 demo。"
        elif provider == "qwen":
            if not _MODEL_RE.fullmatch(requested_model) or (key and key in requested_model):
                error = "模型名称格式不正确，请复制控制台中的模型名称。"
            elif not key:
                error = "尚未配置百炼 API Key，请先运行本机配置脚本。"
            elif not re.fullmatch(r"[!-~]{12,1024}", key) or any(c in key for c in "\"'"):
                error = "API Key 格式不正确，请通过本机配置脚本重新输入。"
            elif not url:
                error = "请填写控制台提供的百炼官方 HTTPS 兼容接口地址。"
            try:
                timeout = float(values.get("QWEN_TIMEOUT_SECONDS", "120"))
                max_tokens_text = values.get("QWEN_MAX_TOKENS", "3500")
                if not re.fullmatch(r"[0-9]{1,5}", max_tokens_text):
                    raise ValueError
                max_tokens = int(max_tokens_text)
                if not 5 <= timeout <= 180 or not 1 <= max_tokens <= 6000:
                    raise ValueError
            except (TypeError, ValueError):
                error = "调用上限配置不正确：超时须为 5–180 秒，输出上限须为 1–6000 token。"

        # The digest is internal only. It detects local key changes without
        # placing the credential in public status or in object repr output.
        signature = hashlib.sha256(json.dumps(
            [provider, raw_url, requested_model, key], ensure_ascii=True
        ).encode("utf-8")).hexdigest()
        with self._state_lock:
            if signature != self._active_signature:
                self._active_signature = signature
                self._outcome = None
                self._last_call = None
                self._last_call_signature = None
        return {
            "provider": provider, "model": model, "key": key, "base_url": url,
            "timeout": timeout, "max_tokens": max_tokens,
            "configured": provider == "qwen" and error is None,
            "configuration_error": error if provider != "demo" else None,
            "signature": signature,
        }

    def status(self):
        config = self._configuration()
        with self._state_lock:
            outcome = self._outcome
            last_call = copy.deepcopy(self._last_call)
        if not config["configured"]:
            state = "not_configured"
        else:
            state = {"success": "verified", "failed": "error"}.get(outcome, "configured")
        result = {
            "provider": "qwen", "configured": config["configured"],
            "model": config["model"], "state": state, "last_call": last_call,
        }
        if config["configuration_error"]:
            result["configuration_error"] = config["configuration_error"]
        return result

    def record_result(self, call_record):
        """Apply application-level rejection to the latest known service call.

        The caller supplies this service's own record with safe extra status
        fields. Arbitrary keys are not retained in the public service status.
        """
        with self._state_lock:
            if not self._last_call or call_record.get("id") != self._last_call["id"]:
                return
            for key in ("application_status", "status", "error_code", "summary", "prompt_version"):
                if key in call_record:
                    self._last_call[key] = copy.deepcopy(call_record[key])
            if self._last_call_signature == self._active_signature:
                self._outcome = self._last_call["status"]

    def _save(self, record, signature):
        # A skipped request is not evidence that a previously verified key failed.
        if not record["attempts"]:
            return
        with self._state_lock:
            # A settings refresh can run while an older request is in flight.
            # Keep that call in the project's audit log, never as verification
            # for the newly configured key/model/endpoint.
            if signature != self._active_signature:
                return
            self._last_call = copy.deepcopy(record)
            self._last_call_signature = signature
            self._outcome = record["status"]

    def complete_for_theory(self, messages):
        return self.complete(messages, capture_response=True)

    def complete(self, messages, *, capture_response=False):
        config = self._configuration()  # Freeze this call's configuration.
        started = time.monotonic()
        captured = capture_text(None)
        record = {
            "id": uuid.uuid4().hex, "provider": "qwen", "model": config["model"],
            "requested_model": config["model"], "status": "failed",
            "started_at": _stamp(), "finished_at": None, "duration_ms": 0,
            "request_id": None, "usage": _usage(None),
            "prompt_version": PROMPT_VERSION, "attempts": 0,
            "error_code": None, "summary": "",
        }

        def failed(code, message, status=502):
            record.update(error_code=code, summary=message, finished_at=_stamp(),
                          duration_ms=round((time.monotonic() - started) * 1000))
            self._save(record, config["signature"])
            failure_record = {**record, "_response_capture": captured} if capture_response else record
            raise ModelServiceError(message, status, failure_record) from None

        if not config["configured"]:
            failed("not_configured", config["configuration_error"] or
                   "当前为演示模式，尚未启用百炼调用；请先完成本机配置。", 409)
        if (not isinstance(messages, list) or not messages or len(messages) > 32
                or any(not isinstance(item, dict)
                       or item.get("role") not in ("system", "user", "assistant")
                       or not isinstance(item.get("content"), str)
                       for item in messages)):
            failed("invalid_messages", "模型输入格式不正确，本次没有发出请求。", 400)
        if not _REQUEST_GATE.acquire(blocking=False):
            failed("busy", "已有一次千问调用正在进行，本次没有发出请求；请稍后重试。", 409)
        try:
            try:
                payload = {
                    "model": config["model"], "messages": messages,
                    "response_format": {"type": "json_object"},
                    "enable_thinking": False, "enable_search": False,
                    "max_tokens": config["max_tokens"], "temperature": 0.4,
                    "stream": False,
                }
                with httpx.Client(transport=self._transport, timeout=config["timeout"],
                                  follow_redirects=False, trust_env=False) as client:
                    record["attempts"] = 1
                    with client.stream(
                        "POST", config["base_url"] + "/chat/completions", json=payload,
                        headers={"Authorization": "Bearer " + config["key"],
                                 "Content-Type": "application/json"},
                    ) as response:
                        record["request_id"] = _safe_id(response.headers.get("x-request-id"), config["key"]) or _safe_id(
                            response.headers.get("x-dashscope-request-id"), config["key"])
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                prefix = b"".join(chunks) + chunk[:max(0, MAX_RESPONSE_BYTES - sum(map(len, chunks)))]
                                captured = capture_text(prefix.decode("utf-8", errors="replace"),
                                    kind="response_body_prefix", complete=False, secret=config["key"])
                                failed("response_too_large", "模型响应超过安全大小上限，本次结果没有应用。")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        captured = capture_text(body.decode("utf-8", errors="replace"),
                            kind="response_body", secret=config["key"])
                        try:
                            envelope = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                        except (ValueError, UnicodeError, RecursionError):
                            envelope = None
                        if isinstance(envelope, dict):
                            record["usage"] = _usage(envelope.get("usage"))
                            record["request_id"] = record["request_id"] or _safe_id(envelope.get("request_id"), config["key"])
                            returned_model = envelope.get("model")
                            if (isinstance(returned_model, str) and _MODEL_RE.fullmatch(returned_model)
                                    and config["key"] not in returned_model):
                                record["model"] = returned_model
                            response_choices = envelope.get("choices")
                            first = response_choices[0] if isinstance(response_choices, list) and response_choices else None
                            response_message = first.get("message") if isinstance(first, dict) else None
                            if isinstance(response_message, dict) and isinstance(response_message.get("content"), str):
                                captured = capture_text(response_message["content"], secret=config["key"])
                            elif 200 <= response.status_code < 300:
                                captured = capture_text(None)
                        if response.status_code in (401, 403):
                            failed("authentication", "百炼未接受当前凭据或权限，请核对 Key、工作空间和地域配置。", 502)
                        if response.status_code in (402, 429):
                            failed("quota_or_rate_limit", "百炼额度或调用频率受限，请查看控制台账单和限额后再试。", 503)
                        if response.status_code == 404:
                            failed("model_or_endpoint", "未找到模型或接入地址，请核对控制台中的模型名称和接口地址。", 502)
                        if not 200 <= response.status_code < 300:
                            failed("provider_rejected", "百炼未完成本次请求，请核对模型配置或稍后重试。", 502)
                if not isinstance(envelope, dict):
                    failed("invalid_response", "模型没有返回有效响应，本次结果没有应用。")
                choices = envelope.get("choices")
                if (not isinstance(choices, list) or not choices
                        or not isinstance(choices[0], dict)):
                    failed("invalid_response", "模型响应缺少有效结果，本次结果没有应用。")
                choice = choices[0]
                if choice.get("finish_reason") != "stop":
                    failed("incomplete_response", "模型未正常完成输出，可能达到长度上限或被拦截；本次结果没有应用。")
                message = choice.get("message")
                if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                    failed("invalid_response", "模型响应格式不完整，本次结果没有应用。")
                if message.get("refusal"):
                    failed("refused", "模型未提供可用的方向草案，请调整研究描述后再试。")
                try:
                    parsed = json.loads(message["content"], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, RecursionError):
                    failed("invalid_json", "模型输出不是有效 JSON 草案，本次结果没有应用。")
                if not isinstance(parsed, dict):
                    failed("invalid_json", "模型输出不是 JSON 对象草案，本次结果没有应用。")
            except ModelServiceError:
                raise
            except httpx.TimeoutException:
                failed("timeout", "千问调用超时，执行结果未知；请稍后查看控制台后再试。", 504)
            except httpx.RequestError:
                failed("network", "无法完成与百炼的连接，执行结果未知；请检查网络后再试。", 502)
            except Exception:
                failed("client_error", "千问客户端未完成处理，执行结果未知；本次结果没有应用。", 502)
            record.update(status="success", finished_at=_stamp(),
                          duration_ms=round((time.monotonic() - started) * 1000),
                          summary="千问已返回 JSON 草案；仍需校验研究字段并由用户选择。")
            self._save(record, config["signature"])
            result_record = copy.deepcopy(record)
            if capture_response:
                result_record["_response_capture"] = captured
            return parsed, result_record
        finally:
            _REQUEST_GATE.release()
