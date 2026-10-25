"""Cloud-only ASGI boundary. Never mount a local research database.

Run one worker: uvicorn cloud_app:app --host 0.0.0.0 --port 7860 --workers 1
PUBLIC_ORIGIN must be the exact HTTPS origin of the public runtime. All state
below is disposable demonstration state; export before ending a session.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import FileResponse, HTMLResponse, JSONResponse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WEBAPP = ROOT / "webapp"
SESSION_HEADER = b"x-yanzheng-session"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MODEL_KEYS = (
    "YANZHENG_MODEL_PROVIDER", "DASHSCOPE_API_KEY", "QWEN_BASE_URL",
    "QWEN_MODEL", "QWEN_TIMEOUT_SECONDS", "QWEN_MAX_TOKENS",
)
DISABLED_MESSAGE = "在线试用不执行 Lean、数值子进程或外部文献检索；可查看公开材料与记录。"


def _bounded_integer(env, key, default, minimum, maximum):
    value = env.get(key, str(default))
    if not re.fullmatch(r"[0-9]{1,8}", str(value)):
        raise ValueError(f"{key} must be an integer")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{key} is outside its permitted range")
    return number


def _origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("PUBLIC_ORIGIN must be one exact HTTPS origin")
    # Accessing port also validates malformed port syntax.
    port = parsed.port
    authority = parsed.hostname.lower()
    if ":" in authority:
        authority = f"[{authority}]"
    if port not in (None, 443):
        authority += f":{port}"
    return f"https://{authority}"


def _load_core(bootstrap_path):
    """app.py creates a database at import time: redirect it before importing."""
    sys.path.insert(0, str(WEBAPP)) if str(WEBAPP) not in sys.path else None
    previous = os.environ.get("YANZHENG_DB_PATH")
    os.environ["YANZHENG_DB_PATH"] = str(bootstrap_path)
    try:
        return importlib.import_module("app")
    finally:
        if previous is None:
            os.environ.pop("YANZHENG_DB_PATH", None)
        else:
            os.environ["YANZHENG_DB_PATH"] = previous


def _private_directory(parent, prefix):
    # Python 3.13's Windows 0700 ACL excludes the sandbox SID. Inherit the
    # enclosing trusted directory ACL on Windows; cloud Linux stays mode 0700.
    directory = parent / (prefix + secrets.token_hex(16))
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    return directory


class BoundedModel:
    """Keep input and enablement checks without a model request count quota."""

    def __init__(self, inner, error_class, input_limit=120000, enabled=False):
        self.inner = inner
        self.error_class, self.input_limit = error_class, input_limit
        self.calls = 0
        self.lock = threading.Lock()
        self.enabled = enabled

    def status(self):
        result = self.inner.status()
        if not self.enabled:
            result.update(configured=False, state="not_configured", last_call=None,
                          configuration_error="作者尚未开放在线模型调用；可先体验公开样例，密钥由作者在平台设置。")
        result["demo_call_limit"] = None
        result["demo_calls_reserved"] = self.calls
        return result

    def record_result(self, record):
        return self.inner.record_result(record)

    def _refuse(self, code, message, status=429):
        stamp = datetime.now(timezone.utc).isoformat()
        model = self.inner.status()["model"]
        raise self.error_class(message, status, {
            "id": uuid.uuid4().hex, "provider": "qwen", "model": model, "requested_model": model,
            "status": "failed", "attempts": 0, "started_at": stamp, "finished_at": stamp,
            "duration_ms": 0, "request_id": None,
            "usage": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
            "prompt_version": "cloud-demo-v1", "error_code": code, "summary": message,
        })

    def complete(self, messages, *, capture_response=False):
        if not self.enabled:
            self._refuse("cloud_model_not_enabled", "作者尚未开放在线模型调用；没有发出模型请求。", 503)
        if len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > self.input_limit:
            self._refuse("demo_input_limit", "本次上下文超过在线试用上限；没有发出模型请求。", 413)
        # Count explicit requests for status only; no per-visitor or daily quota.
        if self.inner.status().get("configured"):
            with self.lock:
                self.calls += 1
        if capture_response:
            return self.inner.complete_for_theory(messages)
        return self.inner.complete(messages)

    def complete_for_theory(self, messages):
        return self.complete(messages, capture_response=True)


class DisabledEvidence:
    def __init__(self, unavailable):
        self.unavailable = unavailable

    def metadata(self):
        return {"contract": "theory-evidence-access-v1", "available": False,
                "availability_reason": "cloud_original_reader_disabled",
                "automatic_read": False, "production_module_accepted": False,
                "limitation": "云端不执行本地 Windows 原件读取；公共材料包可独立下载核对。"}

    def describe(self, *args, **kwargs):
        raise self.unavailable()

    def read(self, *args, **kwargs):
        raise self.unavailable()


def _disabled_lean(*args, **kwargs):
    raise RuntimeError("Lean execution is disabled in the cloud demonstration")


@dataclass
class Session:
    application: object
    model: BoundedModel
    directory: Path
    created: float
    touched: float
    requests: int = 0
    mutations: int = 0
    active: int = 0
    mutation_gate: object = field(default_factory=threading.Lock)


class CloudDemo:
    def __init__(self, public_origin, runtime_root, *, environ=None, model_factory=None, clock=time.monotonic):
        self.origin = _origin(public_origin)
        self.clock = clock
        env = os.environ if environ is None else environ
        self.runtime_root = Path(runtime_root).resolve()
        if self.runtime_root == ROOT or self.runtime_root.is_relative_to(WEBAPP):
            raise ValueError("Cloud state must not be placed in the local application directory")
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.process_root = _private_directory(self.runtime_root, "sessions-")
        self.core = _load_core(self.process_root / "bootstrap.sqlite")
        self.evidence = DisabledEvidence(self.core.evidence_access.Unavailable)
        self.model_enabled = bool(_bounded_integer(env, "YANZHENG_DEMO_MODEL_ENABLED", 0, 0, 1))
        # Legacy YANZHENG_DEMO_*_CALLS settings no longer impose request quotas.
        self.max_sessions = _bounded_integer(env, "YANZHENG_DEMO_MAX_SESSIONS", 64, 1, 256)
        self.ttl = _bounded_integer(env, "YANZHENG_DEMO_IDLE_SECONDS", 3600, 60, 21600)
        self.project_limit = _bounded_integer(env, "YANZHENG_DEMO_MAX_PROJECTS", 3, 1, 10)
        self.request_limit = _bounded_integer(env, "YANZHENG_DEMO_MAX_REQUESTS", 1000, 10, 3000)
        self.mutation_limit = _bounded_integer(env, "YANZHENG_DEMO_MAX_MUTATIONS", 100, 1, 300)
        self.body_limit = 64 * 1024
        self.sessions = {}
        self.issued = []
        self.guard = threading.RLock()
        self.model_environment = {key: env[key] for key in MODEL_KEYS if key in env}
        # An absent file in a newly minted directory can never be the local .env.
        self.absent_env = self.process_root / "platform-environment-only.absent"
        self.model_factory = model_factory or (
            lambda: self.core.ModelService(env_file=self.absent_env, environ=self.model_environment))
        public_model = self.core.ModelService(env_file=self.absent_env, environ={"YANZHENG_MODEL_PROVIDER": "demo"})
        self.public_app = self.core.create_app(
            self.process_root / "public.sqlite", model_client=public_model,
            lean_client=_disabled_lean, evidence_client=self.evidence)

    def _cleanup(self):
        now = self.clock()
        for token, session in list(self.sessions.items()):
            if not session.active and (now - session.touched > self.ttl or now - session.created > 21600):
                del self.sessions[token]
                # Every directory is minted internally, never from a cookie/header.
                directory = session.directory.resolve()
                if directory.parent == self.process_root.resolve():
                    shutil.rmtree(directory)
        self.issued = [stamp for stamp in self.issued if now - stamp < 3600]

    def _new_session(self):
        with self.guard:
            self._cleanup()
            if len(self.sessions) >= self.max_sessions or len(self.issued) >= 120:
                return None
            token = secrets.token_urlsafe(32)
            directory = _private_directory(self.process_root, "visitor-")
            model = BoundedModel(self.model_factory(), self.core.ModelServiceError,
                                 enabled=self.model_enabled)
            application = self.core.create_app(
                directory / "demo.sqlite", model_client=model,
                lean_client=_disabled_lean, evidence_client=self.evidence)
            now = self.clock()
            self.sessions[token] = Session(application, model, directory, now, now)
            self.issued.append(now)
            return token

    def _headers(self, scope):
        result = {}
        for key, value in scope.get("headers", []):
            result.setdefault(key.lower(), []).append(value)
        return result

    def _token(self, headers):
        values = headers.get(SESSION_HEADER, [])
        if len(values) != 1:
            return None
        value = values[0].decode("ascii", errors="replace")
        return value if TOKEN.fullmatch(value) else None

    def _child_scope(self, scope, path=None):
        # The configured origin, never untrusted Forwarded/X-Forwarded-Host, is
        # the canonical authority used by the core same-origin policy.
        origin = urlsplit(self.origin)
        child = dict(scope, scheme="https", server=(origin.hostname, origin.port or 443))
        child["headers"] = [(k, v) for k, v in scope["headers"]
                            if k.lower() not in (b"host", b"forwarded", b"x-forwarded-host", b"x-forwarded-proto")]
        child["headers"].append((b"host", origin.netloc.encode("ascii")))
        if path is not None:
            child["path"] = path
            child["raw_path"] = path.encode("ascii")
        return child

    async def _response(self, scope, receive, send, data, status=200):
        await JSONResponse(data, status_code=status)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        original_send = send
        load_routes = {"/": "page", "/demo/cloud-demo.js": "cloud-adapter",
                       "/static/app.js": "workbench-script", "/static/styles.css": "styles",
                       "/static/workbench-polish.css": "theme"}
        load_route = load_routes.get(scope["path"])

        async def protected_send(message):
            if message["type"] == "http.response.start":
                if load_route:
                    print(f"cloud-load {load_route} status={message['status']}", flush=True)
                message = dict(message)
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in (b"cache-control", b"referrer-policy", b"x-content-type-options")]
                message["headers"] = headers + [
                    (b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff")]
            await original_send(message)
            if load_route and message["type"] == "http.response.body" and not message.get("more_body", False):
                print(f"cloud-load {load_route} complete", flush=True)
        send = protected_send
        path = scope["path"].rstrip("/") or "/"
        method = scope["method"]
        headers = self._headers(scope)

        async def answer(data, status=200):
            await self._response(scope, receive, send, data, status)

        if method in WRITE_METHODS:
            origins = headers.get(b"origin", [])
            if (origins != [self.origin.encode("ascii")]
                    or headers.get(b"sec-fetch-site") == [b"cross-site"]):
                await answer({"detail": "请从在线试用网页发起操作。", "code": "same_origin_required"}, 403)
                return
        if method == "OPTIONS":
            await answer({"detail": "跨站访问未启用。"}, 403)
            return
        if method == "GET" and path == "/":
            html = (WEBAPP / "static/index.html").read_text(encoding="utf-8")
            # Preserve the adapter-before-app ordering without blocking HTML parsing.
            html = html.replace("<head>", '<head>\n  <script src="/demo/cloud-demo.js" defer></script>', 1)
            await HTMLResponse(html)(scope, receive, send)
            return
        if method == "GET" and path == "/demo/cloud-demo.js":
            await FileResponse(HERE / "static/cloud-demo.js", media_type="text/javascript")(scope, receive, send)
            return
        if method == "GET" and path == "/demo/public-evidence.zip":
            artifact = HERE / "public-evidence.zip"
            if not artifact.is_file():
                await answer({"detail": "当前部署未附公共原件包。"}, 404)
            else:
                await FileResponse(artifact, filename="yanzheng-public-evidence.zip",
                                   media_type="application/zip")(scope, receive, send)
            return
        if method == "GET" and path == "/demo/info":
            await answer({"mode": "isolated_cloud_demo", "session_call_limit": None,
                          "daily_call_limit": None, "idle_seconds": self.ttl,
                          "max_projects": self.project_limit, "local_data_connected": False,
                          "model_calls_enabled": self.model_enabled,
                          "lean_execution": False, "numeric_execution": False,
                          "external_literature_search": False,
                          "model_call_limit_policy": "unlimited"})
            return
        if method == "GET" and path == "/api/lean/status":
            await answer({"environment": {"available": False, "error_code": "cloud_execution_disabled",
                                          "automatic_downloads": False, "mathlib_used": False}})
            return
        if method == "GET" and (path.startswith("/static/") or path == "/api/library"
                                or path.startswith("/api/library/artifacts/") or path == "/api/lean/cases"):
            await self.public_app(self._child_scope(scope), receive, send)
            return

        async def read_body():
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    raise ConnectionError()
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > self.body_limit:
                    raise OverflowError()
                chunks.append(chunk)
                if not message.get("more_body", False):
                    return b"".join(chunks)
        lengths = headers.get(b"content-length", [])
        if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or int(lengths[0]) > self.body_limit)):
            await answer({"detail": "请求正文超过在线试用大小上限。"}, 413)
            return
        try:
            body = await asyncio.wait_for(read_body(), timeout=15)
        except OverflowError:
            await answer({"detail": "请求正文超过在线试用大小上限。"}, 413)
            return
        except (asyncio.TimeoutError, ConnectionError):
            await answer({"detail": "请求接收未完成。"}, 408)
            return
        if path == "/demo/session" and method == "POST":
            if headers.get(b"content-type") != [b"application/json"] or body not in (b"{}", b""):
                await answer({"detail": "初始化会话需发送空 JSON 对象。"}, 400)
                return
            token = self._token(headers)
            with self.guard:
                self._cleanup()
                if SESSION_HEADER in headers and token not in self.sessions:
                    await answer({"detail": "会话已过期。请导出仍可访问的记录后，点击开始新会话。",
                                  "code": "session_expired"}, 401)
                    return
                token = token or self._new_session()
            if token is None:
                await answer({"detail": "当前在线试用人数较多，请稍后再试。"}, 429)
            else:
                await answer({"session": token, "idle_seconds": self.ttl, "max_projects": self.project_limit})
            return
        token = self._token(headers)
        with self.guard:
            self._cleanup()
            session = self.sessions.get(token)
            if session:
                session.active += 1
                session.requests += 1
                session.touched = self.clock()
        if not session:
            await answer({"detail": "需要有效的在线试用会话，请回到首页初始化。",
                          "code": "session_required"}, 401)
            return
        locked = False
        try:
            if session.requests > self.request_limit:
                # Export remains available after the general request cap.
                if method != "GET" or not re.fullmatch(r"/api/projects/[^/]+/(export|plan|decision-package)", path):
                    await answer({"detail": "本会话请求次数已达上限；请导出项目后结束试用。"}, 429)
                    return
            if method in WRITE_METHODS:
                locked = session.mutation_gate.acquire(blocking=False)
                if not locked:
                    await answer({"detail": "本会话已有操作正在处理，请等待完成。"}, 409)
                    return
                if session.mutations >= self.mutation_limit:
                    await answer({"detail": "本会话操作次数已达上限；仍可导出已有记录。"}, 429)
                    return
                session.mutations += 1
                if (path.endswith("/literature-search") or path.endswith("/experiments")
                        or path.endswith("/lean/runs")):
                    await answer({"detail": DISABLED_MESSAGE, "code": "cloud_execution_disabled"}, 503)
                    return
                if path in ("/api/projects", "/api/theory/examples"):
                    if len(session.application.state.store.list()) >= self.project_limit:
                        await answer({"detail": f"每个试用会话最多创建 {self.project_limit} 个项目。"}, 429)
                        return
            if method == "GET" and path == "/api/status":
                model = session.model.status()
                await answer({
                    "mode": "qwen" if model["configured"] else "demo",
                    "model_connected": model["state"] == "verified", "model": model,
                    "storage": "isolated_temporary_sqlite", "version": "0.5.0-cloud-demo",
                    "cloud_demo": True,
                    "capabilities": {
                        "direction_generation": "qwen_available" if model["configured"] else "demo",
                        "literature_search": "unavailable_in_cloud", "model": model["state"],
                        "experiments": "unavailable_in_cloud", "skill_workflow": True,
                        "reading_notes": True, "theory_workspace": True,
                        "theory_revision_review": True, "formal_verification": False,
                        "lean_fixed_cases": False, "lean_run_storage": False}})
                return
            # The old direct-apply model entry is routed through preview/review.
            target = path[:-len("/generate")] + "/advance" if (
                method == "POST" and re.fullmatch(r"/api/projects/[^/]+/generate", path)) else None
            delivered = False

            async def replay_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            await session.application(self._child_scope(scope, target), replay_receive, send)
        finally:
            if locked:
                session.mutation_gate.release()
            with self.guard:
                session.active -= 1
                session.touched = self.clock()


def build_app(*, public_origin=None, runtime_root=None, environ=None, model_factory=None, clock=time.monotonic):
    env = os.environ if environ is None else environ
    origin = public_origin or env.get("PUBLIC_ORIGIN", "")
    root = runtime_root or env.get("YANZHENG_DEMO_STATE_ROOT") or Path(tempfile.gettempdir()) / "yanzheng-cloud-demo"
    return CloudDemo(origin, root, environ=env, model_factory=model_factory, clock=clock)


class LazyApp:
    """Importing this deployment module has no database/config/model side effects."""
    def __init__(self):
        self.application = None
        self.lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        if self.application is None:
            with self.lock:
                if self.application is None:
                    self.application = build_app()
        await self.application(scope, receive, send)


app = LazyApp()
