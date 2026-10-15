"""No network or credentials: explicit generation, persistence and race boundaries."""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import create_app
from model_service import ModelServiceError
import engine
from fastapi.testclient import TestClient


def payload():
    topics = []
    for index in range(3):
        topics.append({
            "id": f"qwen-{index + 1}", "title": f"对照方向 {index + 1}",
            "question": f"在固定资源下，条件 {index + 1} 对误差有何影响？",
            "known_work": "尚缺经过内容核查的来源，需要继续检索与阅读。",
            "potential_difference": "先以复现和条件对照确定问题是否成立。",
            "novelty_status": "needs_review", "evidence": [],
            "first_validation": {"steps": ["明确输入与资源预算。", "复现基线并记录误差。"],
                                 "baseline": "相同输入下的参考方法。", "metrics": ["误差", "耗时"],
                                 "success_signal": "可复现一项条件性观察。"},
            "resources": ["普通笔记本，一周起步。"],
            "stop_conditions": ["基线无法复现时暂停比较。"],
            "risks": ["当前没有新执行实验或证明。"],
            "open_questions": ["原始资料是否覆盖该条件？"], "readiness": "provisional"
        })
    return {"topics": topics, "recommendation": {"topic_id": "qwen-1", "reason": "首先复现基线。", "would_change_if": ["资料或资源改变。"]}}


class FakeModel:
    def __init__(self):
        self.calls = 0
        self.last_call = None
        self.callback = None
        self.result = payload()

    def status(self):
        return {"provider": "qwen", "configured": True, "model": "qwen-plus",
                "state": "verified" if self.last_call and self.last_call["status"] == "success" else "configured",
                "last_call": self.last_call}

    def complete(self, messages):
        self.calls += 1
        if self.callback:
            self.callback()
        record = {"id": f"fixture-call-{self.calls}", "provider": "qwen", "model": "qwen-plus",
                  "requested_model": "qwen-plus", "status": "success", "attempts": 1,
                  "started_at": engine.now(), "finished_at": engine.now(), "duration_ms": 2,
                  "request_id": "fixture-response", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                  "prompt_version": "direction-v1", "error_code": None, "summary": "模型调用完成。"}
        self.last_call = copy.deepcopy(record)
        return copy.deepcopy(self.result), record

    def record_result(self, record):
        self.last_call = copy.deepcopy(record)


class ModelApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary_root = BASE / "tests/.tmp"
        self.temporary_root.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(dir=self.temporary_root))
        self.model = FakeModel()
        self.application = create_app(self.directory / "test.db", model_client=self.model)
        self.client = TestClient(self.application)

    def tearDown(self):
        self.client.close()
        self.assertTrue(self.directory.resolve().is_relative_to(self.temporary_root.resolve()))
        shutil.rmtree(self.directory)

    def create(self):
        return self.client.post("/api/projects", json={"initial_idea": "随机低秩逼近"}).json()

    def generate(self, project_id):
        return self.client.post(f"/api/projects/{project_id}/generate", json={})

    def test_explicit_only_generation_persists_version_and_export_facts(self):
        project = self.create()
        self.assertEqual(self.model.calls, 0)
        self.client.get("/api/status")
        self.assertEqual(self.model.calls, 0)
        response = self.generate(project["id"])
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(saved["direction_map"]["provenance"]["generator"], "qwen")
        self.assertEqual(saved["model_calls"][-1]["usage"]["total_tokens"], 30)
        self.assertEqual(saved["model_calls"][-1]["application_status"], "applied")
        self.assertEqual(len(saved["direction_versions"]), 1)
        self.assertEqual(engine.validate(saved["direction_map"]), [])
        selected = self.client.post(f'/api/projects/{project["id"]}/select', json={"topic_id": "qwen-1"})
        self.assertEqual(selected.status_code, 200)
        plan = self.client.get(f'/api/projects/{project["id"]}/plan').text
        self.assertIn("qwen-plus", plan)
        self.assertNotIn("未调用千问", plan)
        self.assertEqual(self.client.get(f'/api/projects/{project["id"]}/export').json()["model_calls"][-1]["usage"]["total_tokens"], 30)

    def test_invalid_output_keeps_current_candidates_and_logs_failure(self):
        project = self.create()
        self.model.result["selected_topic_id"] = "qwen-1"
        response = self.generate(project["id"])
        self.assertEqual(response.status_code, 502, response.text)
        saved = self.client.get(f'/api/projects/{project["id"]}').json()
        self.assertEqual(saved["direction_map"], project["direction_map"])
        self.assertEqual(saved["model_calls"][-1]["status"], "failed")
        self.assertEqual(saved["model_calls"][-1]["error_code"], "invalid_plan")
        self.assertEqual(saved["model_calls"][-1]["attempts"], 1)

    def test_network_outside_write_lock_and_concurrent_edit_not_overwritten(self):
        project = self.create()
        self.model.callback = lambda: self.application.state.store.update(project["id"], lambda current: engine.add_event(current, "concurrent_edit", "另一项研究操作"))
        response = self.generate(project["id"])
        self.assertEqual(response.status_code, 409, response.text)
        saved = self.client.get(f'/api/projects/{project["id"]}').json()
        self.assertEqual(saved["direction_map"], project["direction_map"])
        self.assertIn("concurrent_edit", [event["action"] for event in saved["history"]])
        self.assertEqual(saved["model_calls"][-1]["application_status"], "discarded_conflict")
        self.assertEqual(saved["model_calls"][-1]["status"], "success")

    def test_unknown_or_selected_project_never_calls_model(self):
        self.assertEqual(self.generate("missing").status_code, 404)
        project = self.create()
        self.client.post(f'/api/projects/{project["id"]}/select', json={"topic_id": "budget"})
        self.assertEqual(self.generate(project["id"]).status_code, 409)
        self.assertEqual(self.model.calls, 0)

    def test_provider_failure_records_attempt_and_preserves_candidates(self):
        project = self.create()
        record = FakeModel().complete([])[1]
        record.update({"status": "failed", "usage": None, "error_code": "authentication_failed", "summary": "请检查 API Key、地域和调用权限。"})

        def fail():
            raise ModelServiceError(record["summary"], 502, record)

        self.model.callback = fail
        response = self.generate(project["id"])
        self.assertEqual(response.status_code, 502, response.text)
        saved = self.client.get(f'/api/projects/{project["id"]}').json()
        self.assertEqual(saved["direction_map"], project["direction_map"])
        self.assertEqual(saved["model_calls"][-1]["attempts"], 1)
        self.assertEqual(saved["model_calls"][-1]["error_code"], "authentication_failed")

    def test_rejected_duplicate_does_not_discard_real_inflight_generation(self):
        project = self.create()
        record = FakeModel().complete([])[1]
        record.update({"status": "failed", "attempts": 0, "error_code": "busy", "summary": "已有千问调用正在进行，本次未发请求。"})

        def busy(*args):
            raise ModelServiceError(record["summary"], 409, record)

        def duplicate():
            with patch.object(self.model, "complete", side_effect=busy):
                self.assertEqual(self.generate(project["id"]).status_code, 409)

        self.model.callback = duplicate
        response = self.generate(project["id"])
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["direction_map"]["provenance"]["generator"], "qwen")
        self.assertEqual(len(saved["model_calls"]), 1)


if __name__ == "__main__":
    unittest.main()
