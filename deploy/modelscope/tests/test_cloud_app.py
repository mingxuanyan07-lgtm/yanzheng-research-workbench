"""Isolated cloud boundary checks. No real models, research DBs or subprocesses."""
import copy
import importlib.util
import json
import sys
import shutil
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

DEPLOY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("yanzheng_cloud_test_target", DEPLOY / "cloud_app.py")
cloud = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cloud
spec.loader.exec_module(cloud)
ORIGIN = "https://demo.example.test"


def proposal():
    topics = []
    for index in range(3):
        topics.append({
            "id": f"qwen-{index + 1}", "title": f"随机低秩逼近对照方向 {index + 1}",
            "question": "相同计算预算下，过采样如何影响误差？",
            "known_work": "尚缺经核查原始来源。",
            "potential_difference": "先复现基线和比较条件，不宣称新成果。",
            "novelty_status": "needs_review", "evidence": [],
            "first_validation": {"steps": ["固定合成矩阵。", "记录误差与耗时。"],
                                 "baseline": "相同矩阵的参考方法。",
                                 "metrics": ["误差", "耗时"], "success_signal": "一项可复核条件观察。"},
            "resources": ["个人电脑"], "stop_conditions": ["基线无法复现时停止。"],
            "risks": ["未运行实验。"], "open_questions": ["待核查文献。"], "readiness": "provisional"})
    return {"topics": topics, "stage": "directions_ready", "questions": [],
            "assumptions": [], "search_queries": ["randomized low rank approximation"],
            "revision_note": "公开数据条件下的待审阅候选。",
            "recommendation": {"topic_id": "qwen-1", "reason": "先复现。",
                               "would_change_if": ["资源或条件改变。"]}}


class FakeModel:
    def __init__(self):
        self.calls = 0
        self.last = None
        self.result = proposal()

    def status(self):
        return {"provider": "qwen", "configured": True, "model": "qwen-plus",
                "state": "verified" if self.last else "configured", "last_call": self.last}

    def complete(self, messages):
        self.calls += 1
        self.messages = copy.deepcopy(messages)
        stamp = datetime.now(timezone.utc).isoformat()
        record = {"id": f"fixture-{id(self)}-{self.calls}", "provider": "qwen", "model": "qwen-plus", "requested_model": "qwen-plus",
                  "status": "success", "attempts": 1, "started_at": stamp, "finished_at": stamp,
                  "duration_ms": 1, "request_id": "fixture",
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                  "prompt_version": "test", "error_code": None, "summary": "Isolated model fixture"}
        self.last = copy.deepcopy(record)
        return copy.deepcopy(self.result), record

    def complete_for_theory(self, messages):
        return self.complete(messages)

    def record_result(self, record):
        self.last = copy.deepcopy(record)


class CloudAppTests(unittest.TestCase):
    def setUp(self):
        self.test_root = DEPLOY / "tests/.tmp"
        self.test_root.mkdir(exist_ok=True)
        self.temp_path = self.test_root / uuid.uuid4().hex
        self.temp_path.mkdir()
        self.models = []
        self.now = [1000.0]
        self.subprocess = patch("subprocess.Popen", side_effect=AssertionError("No subprocess is permitted"))
        self.subprocess.start()
        self.application = self.make_app()
        self.client = TestClient(self.application, base_url=ORIGIN)
        self.a = self.session()
        self.b = self.session()

    def tearDown(self):
        self.client.close()
        self.subprocess.stop()
        self.assertEqual(self.temp_path.resolve().parent, self.test_root.resolve())
        shutil.rmtree(self.temp_path)

    def factory(self):
        model = FakeModel()
        self.models.append(model)
        return model

    def make_app(self, **env):
        env.setdefault("YANZHENG_DEMO_MODEL_ENABLED", "1")
        return cloud.build_app(public_origin=ORIGIN, runtime_root=self.temp_path,
                               environ=env, model_factory=self.factory, clock=lambda: self.now[0])

    def session(self):
        response = self.client.post("/demo/session", headers={"Origin": ORIGIN}, json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["session"]

    def get(self, path, token=None):
        return self.client.get(path, headers={"X-Yanzheng-Session": token or self.a})

    def post(self, path, data=None, token=None, **headers):
        return self.client.post(path, json=data if data is not None else {},
                                headers={"Origin": ORIGIN, "X-Yanzheng-Session": token or self.a, **headers})

    def create(self, token=None, mode="skill"):
        response = self.post("/api/projects", {"initial_idea": "随机低秩逼近的误差与耗时比较",
                                              "planning_mode": mode}, token)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_import_has_no_initialized_app_and_bootstrap_is_temporary(self):
        self.assertIsNone(cloud.app.application)
        self.assertTrue(self.application.core.app.state.store.path.is_relative_to(self.test_root))
        self.assertTrue(self.application.public_app.state.store.path.is_relative_to(self.temp_path))
        for session in self.application.sessions.values():
            self.assertTrue(session.application.state.store.path.is_relative_to(self.temp_path))

    def test_initialization_and_public_reads_do_not_call_model(self):
        for path in ("/", "/demo/info", "/demo/cloud-demo.js", "/api/lean/status", "/api/lean/cases"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, (path, response.text))
        self.assertIn("/demo/cloud-demo.js", self.client.get("/").text)
        self.assertEqual(sum(model.calls for model in self.models), 0)

    def test_adapter_is_deferred_before_workbench_and_load_logs_exclude_credentials(self):
        with patch("builtins.print") as printed:
            response = self.client.get("/?studio_token=private-platform-token", headers={
                "X-Yanzheng-Session": "private-session-token", "Authorization": "Bearer private-key"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('<script src="/demo/cloud-demo.js" defer></script>', response.text)
        self.assertLess(response.text.index('/demo/cloud-demo.js'), response.text.index('/static/app.js'))
        logged = " ".join(str(call) for call in printed.call_args_list)
        self.assertIn("cloud-load page status=200", logged)
        self.assertIn("cloud-load page complete", logged)
        self.assertNotIn("private-", logged)

    def test_private_get_and_exports_require_header_and_no_cors(self):
        for path in ("/api/status", "/api/projects", "/api/projects/unknown/export"):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 401)
            self.assertNotIn("access-control-allow-origin", result.headers)
        self.assertEqual(self.client.options("/api/projects",
            headers={"Origin": "https://other.invalid"}).status_code, 403)

    def test_sessions_isolate_lists_details_exports_and_writes(self):
        project = self.create()
        path = f'/api/projects/{project["id"]}'
        self.assertEqual(len(self.get("/api/projects").json()["projects"]), 1)
        self.assertEqual(self.get("/api/projects", self.b).json()["projects"], [])
        for suffix in ("", "/export", "/plan", "/decision-package", "/theory", "/lean/runs"):
            self.assertEqual(self.get(path + suffix, self.b).status_code, 404)
        self.assertEqual(self.post(path + "/advance", token=self.b).status_code, 404)
        self.assertEqual(self.get(path).status_code, 200)

    def test_status_does_not_share_other_visitors_model_call(self):
        self.create()
        self.assertIsNotNone(self.get("/api/status").json()["model"]["last_call"])
        self.assertIsNone(self.get("/api/status", self.b).json()["model"]["last_call"])

    def test_preview_accept_select_and_export(self):
        project = self.create()
        pending = project["workflow"]["pending_proposal"]
        self.assertEqual(project["direction_map"]["topics"], [])
        base = f'/api/projects/{project["id"]}'
        accepted = self.post(base + f'/direction/proposals/{pending["id"]}/accept')
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(len(accepted.json()["direction_map"]["topics"]), 3)
        self.assertIsNone(accepted.json()["direction_map"]["selected_topic_id"])
        selected = self.post(base + "/select", {"topic_id": "qwen-1", "reason": "先复现"})
        self.assertEqual(selected.status_code, 200, selected.text)
        for suffix, media in (("/export", "application/json"), ("/plan", "text/markdown"), ("/decision-package", "text/markdown")):
            response = self.get(base + suffix)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(media, response.headers["content-type"])
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.models[0].calls, 1)

    def test_invalid_model_payload_explains_safe_reason_and_manual_retry_keeps_inputs(self):
        project = self.create(mode="demo")
        model = self.models[0]
        model.result = proposal()
        model.result["topics"][0]["first_validation"]["steps"] = []
        before = self.get(f'/api/projects/{project["id"]}').json()["direction_map"]
        response = self.post(f'/api/projects/{project["id"]}/advance')
        self.assertEqual(response.status_code, 502, response.text)
        reason = "topics[0].first_validation.steps 必须包含 1 至 6 条文本。"
        self.assertIn(reason, response.json()["detail"])
        saved = self.get(f'/api/projects/{project["id"]}').json()
        self.assertEqual(saved["direction_map"], before)
        self.assertEqual(saved["model_calls"][-1]["error_code"], "invalid_workflow")
        calls = model.calls
        model.result = proposal()
        response = self.post(f'/api/projects/{project["id"]}/advance')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.calls, calls + 1)
        context = json.loads(model.messages[1]["content"].split("\n", 1)[1])
        self.assertEqual(context["previous_validation_feedback"], reason)
        self.assertIsNotNone(response.json()["workflow"]["pending_proposal"])
        self.assertEqual(response.json()["direction_map"], before)

    def test_reject_preserves_map_and_call_is_not_repeated(self):
        project = self.create()
        pending = project["workflow"]["pending_proposal"]
        result = self.post(f'/api/projects/{project["id"]}/direction/proposals/{pending["id"]}/reject',
                           {"reason": "先核查文献"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["direction_map"], project["direction_map"])
        self.assertIsNone(result.json()["workflow"].get("pending_proposal"))
        self.assertEqual(self.models[0].calls, 1)

    def test_legacy_generate_is_also_a_pending_review(self):
        project = self.create(mode="demo")
        result = self.post(f'/api/projects/{project["id"]}/generate')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNotNone(result.json()["workflow"]["pending_proposal"])
        self.assertEqual(result.json()["direction_map"], project["direction_map"])

    def test_writes_require_exact_origin_and_reject_forwarded_spoofing(self):
        for headers in ({}, {"Origin": "https://evil.test"}, {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"}):
            response = self.client.post("/demo/session", json={}, headers=headers)
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/projects", json={"initial_idea": "随机低秩逼近"},
            headers={"Origin": "https://evil.test", "X-Yanzheng-Session": self.a,
                     "X-Forwarded-Host": "evil.test", "X-Forwarded-Proto": "https"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post("/api/projects", {"initial_idea": "随机低秩逼近"},
                                  Host="internal:7860").status_code, 201)

    def test_no_lean_numeric_or_external_literature_execution(self):
        project = self.create(mode="demo")
        with patch.object(self.application.core.lean_service, "environment", side_effect=AssertionError()), \
             patch.object(self.application.core.experiments, "run", side_effect=AssertionError()), \
             patch.object(self.application.core.literature_service, "search", side_effect=AssertionError()):
            self.assertFalse(self.client.get("/api/lean/status").json()["environment"]["available"])
            for suffix in ("/lean/runs", "/experiments", "/literature-search"):
                response = self.post(f'/api/projects/{project["id"]}' + suffix)
                self.assertEqual(response.status_code, 503)
        saved = self.get(f'/api/projects/{project["id"]}').json()
        self.assertFalse(saved.get("lean_runs"))
        self.assertFalse(saved.get("experiments"))
        self.assertFalse(saved.get("literature_searches"))

    def test_project_limit_includes_theory_example(self):
        self.application.project_limit = 1
        self.create(mode="demo")
        self.assertEqual(self.post("/api/projects", {"initial_idea": "另一个随机低秩逼近"}).status_code, 429)
        self.assertEqual(self.post("/api/theory/examples", {"name": "uniform-continuity"}).status_code, 429)

    def test_model_requests_continue_past_old_visitor_limit_and_keep_export(self):
        project = self.create()
        for _ in range(4):
            response = self.post(f'/api/projects/{project["id"]}/advance')
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.models[0].calls, 5)
        self.assertIsNone(self.get("/api/status").json()["model"]["demo_call_limit"])
        self.assertEqual(self.get(f'/api/projects/{project["id"]}/export').status_code, 200)

    def test_model_count_policy_is_unlimited_for_all_visitors(self):
        info = self.client.get("/demo/info").json()
        self.assertIsNone(info["session_call_limit"])
        self.assertIsNone(info["daily_call_limit"])
        self.assertEqual(info["model_call_limit_policy"], "unlimited")
        project_a = self.create()
        for _ in range(31):
            response = self.post(f'/api/projects/{project_a["id"]}/advance')
            self.assertEqual(response.status_code, 200, response.text)
        project_b = self.create(self.b)
        self.assertEqual(self.models[1].calls, 1)
        self.assertEqual(project_b["model_calls"][-1]["status"], "success")

    def test_session_capacity_and_idle_expiration(self):
        self.application.max_sessions = 2
        self.assertEqual(self.client.post("/demo/session", json={}, headers={"Origin": ORIGIN}).status_code, 429)
        self.now[0] += self.application.ttl + 1
        self.assertEqual(self.get("/api/projects").status_code, 401)
        self.assertEqual(len(self.application.sessions), 0)
        self.session()

    def test_oversize_body_and_duplicate_session_header(self):
        result = self.client.post("/api/projects", content=b"x" * 65537,
            headers={"Origin": ORIGIN, "X-Yanzheng-Session": self.a, "Content-Type": "application/json"})
        self.assertEqual(result.status_code, 413)
        result = self.client.get("/api/projects", headers=[
            ("X-Yanzheng-Session", self.a), ("X-Yanzheng-Session", self.b)])
        self.assertEqual(result.status_code, 401)

    def test_mutation_and_request_caps_leave_export_available(self):
        project = self.create()
        base = f'/api/projects/{project["id"]}'
        pending = project["workflow"]["pending_proposal"]
        self.assertEqual(self.post(base + f'/direction/proposals/{pending["id"]}/accept').status_code, 200)
        self.assertEqual(self.post(base + "/select", {"topic_id": "qwen-1", "reason": "先复现"}).status_code, 200)
        self.application.sessions[self.a].mutations = self.application.mutation_limit
        self.assertEqual(self.post(base + "/advance").status_code, 429)
        self.application.sessions[self.a].requests = self.application.request_limit
        self.assertEqual(self.get("/api/projects").status_code, 429)
        self.assertEqual(self.get(base + "/export").status_code, 200)
        self.assertEqual(self.get(base + "/decision-package").status_code, 200)

    def test_default_cloud_model_never_reads_dotenv(self):
        other = cloud.build_app(public_origin=ORIGIN, runtime_root=self.temp_path / "no-config", environ={})
        original = Path.open
        def reject_local_env(path, *args, **kwargs):
            if path.name in (".env", "local.env"):
                raise AssertionError("Local configuration must not be read")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", reject_local_env), TestClient(other, base_url=ORIGIN) as client:
            token = client.post("/demo/session", headers={"Origin": ORIGIN}, json={}).json()["session"]
            status = client.get("/api/status", headers={"X-Yanzheng-Session": token})
            self.assertEqual(status.status_code, 200)
            self.assertFalse(status.json()["model"]["configured"])

    def test_model_calls_require_explicit_owner_switch_even_if_secret_is_configured(self):
        other = cloud.build_app(public_origin=ORIGIN, runtime_root=self.temp_path / "disabled",
                                environ={}, model_factory=self.factory)
        with TestClient(other, base_url=ORIGIN) as client:
            token = client.post("/demo/session", headers={"Origin": ORIGIN}, json={}).json()["session"]
            result = client.post("/api/projects",
                headers={"Origin": ORIGIN, "X-Yanzheng-Session": token},
                json={"initial_idea": "随机低秩逼近", "planning_mode": "skill"})
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.json()["model_calls"][-1]["error_code"], "cloud_model_not_enabled")
            self.assertEqual(self.models[-1].calls, 0)

    def test_invalid_origin_and_limits_fail_closed(self):
        for origin in ("", "http://demo.test", "https://demo.test/path", "https://user:pass@demo.test"):
            with self.assertRaises(ValueError):
                cloud.build_app(public_origin=origin, runtime_root=self.temp_path, environ={})
        with self.assertRaises(ValueError):
            self.make_app(YANZHENG_DEMO_MAX_SESSIONS="999999")


if __name__ == "__main__":
    unittest.main(verbosity=2)
