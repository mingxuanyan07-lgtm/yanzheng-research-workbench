"""Integrated Skill stages use mocked models; fixed numerical runs are real.

Model direction results are saved as pending previews (Gap B) and only mutate the
direction map after an explicit accept by the researcher.
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import create_app
from fastapi.testclient import TestClient
from model_service import ModelServiceError
from test_model_api import FakeModel, payload
from test_literature_api import search_record
import engine
import literature_service


def clarifying():
    return {"stage": "clarifying", "questions": [{"id": "target", "question": "你希望改善哪个具体问题？", "why_it_matters": "决定检索与验证对象。"}],
            "topics": [], "recommendation": {"topic_id": None, "reason": "需先明确对象。", "would_change_if": ["对象与材料已明确。"]},
            "assumptions": [], "search_queries": [], "revision_note": "先澄清研究对象。"}


def ready():
    return payload() | {"stage": "directions_ready", "questions": [], "assumptions": [],
                        "search_queries": ["randomized low rank approximation"], "revision_note": "依据已给约束形成候选，仍需文献核查。"}


class WorkflowApiTest(unittest.TestCase):
    def setUp(self):
        self.root = BASE / "tests/.tmp"
        self.root.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(dir=self.root))
        self.model = FakeModel()
        self.model.result = clarifying()
        self.app = create_app(self.directory / "test.db", model_client=self.model)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        assert self.directory.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.directory)

    def create(self, mode="skill", idea="我想研究AI"):
        response = self.client.post("/api/projects", json={"initial_idea": idea, "planning_mode": mode})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def pending(self, project_id):
        project = self.client.get(f"/api/projects/{project_id}").json()
        return project["workflow"].get("pending_proposal")

    def accept(self, project_id, proposal_id=None):
        if proposal_id is None:
            proposal_id = self.pending(project_id)["id"]
        response = self.client.post(f"/api/projects/{project_id}/direction/proposals/{proposal_id}/accept", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def reject(self, project_id, proposal_id=None, reason=""):
        if proposal_id is None:
            proposal_id = self.pending(project_id)["id"]
        response = self.client.post(f"/api/projects/{project_id}/direction/proposals/{proposal_id}/reject",
                                    json={"reason": reason})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_ambiguous_then_answers_route_and_recommendation_never_selects(self):
        project = self.create()
        self.assertEqual(project["stage"], "clarifying")
        self.assertEqual(len(project["direction_map"]["questions"]), 3)  # placeholder map, unchanged
        pending = project["workflow"]["pending_proposal"]
        self.assertEqual(pending["payload"]["stage"], "clarifying")
        self.assertEqual(pending["payload"]["questions"][0]["id"], "target")
        self.assertEqual(engine.validate(project["direction_map"]), [])

        accepted = self.accept(project["id"])
        self.assertEqual(accepted["stage"], "clarifying")
        self.assertIsNone(accepted["workflow"].get("pending_proposal"))
        self.assertEqual([question["id"] for question in accepted["direction_map"]["questions"]], ["target"])
        self.assertEqual(accepted["model_calls"][-1]["application_status"], "applied")

        self.model.result = ready()
        response = self.client.post(f'/api/projects/{project["id"]}/answers', json={"answers": {"target": "随机SVD同预算下的误差和耗时"}})
        self.assertEqual(response.status_code, 200, response.text)
        staged = response.json()
        self.assertEqual(staged["stage"], "clarifying")  # preview is not applied yet
        self.assertIsNotNone(staged["workflow"]["pending_proposal"])
        self.assertEqual(staged["workflow"]["pending_proposal"]["payload"]["stage"], "directions_ready")
        saved = self.accept(project["id"])
        self.assertEqual(saved["stage"], "directions_ready")
        self.assertEqual(saved["direction_map"]["recommendation"]["topic_id"], "qwen-1")
        self.assertIsNone(saved["direction_map"]["selected_topic_id"])
        self.assertEqual(engine.validate(saved["direction_map"]), [])

    def test_sufficient_input_goes_directly_to_candidates_and_failure_keeps_input(self):
        self.model.result = ready()
        project = self.create(idea="随机低秩逼近；已有合成矩阵，比较误差和耗时，一周复现基线。")
        self.assertEqual(project["stage"], "clarifying")  # pending preview has not changed the map
        self.assertEqual(project["workflow"]["pending_proposal"]["payload"]["stage"], "directions_ready")
        accepted = self.accept(project["id"])
        self.assertEqual(accepted["stage"], "directions_ready")
        self.assertEqual(len(accepted["direction_map"]["topics"]), 3)
        before = copy.deepcopy(accepted["direction_map"])
        call = FakeModel().complete([])[1]
        call.update(id="timeout-fixture", status="failed", error_code="timeout", summary="模型超时，本次输入已保留。")
        with patch.object(self.model, "complete", side_effect=ModelServiceError(call["summary"], 504, call)):
            response = self.client.post(f'/api/projects/{project["id"]}/advance', json={"material": "改用公开的慢衰减谱，内存只有4GB。"})
        self.assertEqual(response.status_code, 504)
        saved = self.client.get(f'/api/projects/{project["id"]}').json()
        self.assertEqual(saved["direction_map"], before)
        self.assertIn("内存只有4GB", json.dumps(saved["workflow"], ensure_ascii=False))
        self.assertEqual(saved["workflow"]["state"], "failed")
        # The legacy URL must not bypass Skill context or discard pending input.
        response = self.client.post(f'/api/projects/{project["id"]}/generate', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["direction_map"]["selected_topic_id"])

    def test_reading_confirmation_and_literature_revision(self):
        self.model.result = ready()
        project = self.create()
        self.accept(project["id"])
        with patch.object(literature_service, "run_search", return_value=search_record()):
            result = self.client.post(f'/api/projects/{project["id"]}/literature-search', json={"query": "randomized svd"}).json()
        run = result["literature_searches"][0]
        source = run["sources"][0]
        note = {"run_id": run["id"], "source_id": source["id"], "level": "abstract_checked", "content": "摘要只讨论特定条件，没有一般耗时结论。", "locator": "Abstract", "confirmed": False}
        self.assertEqual(self.client.post(f'/api/projects/{project["id"]}/reading-notes', json=note).status_code, 422)
        note["confirmed"] = True
        response = self.client.post(f'/api/projects/{project["id"]}/reading-notes', json=note)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["literature_searches"][0]["sources"][0]["verification"], "metadata_checked")
        response = self.client.post(f'/api/projects/{project["id"]}/advance', json={"material": "基于以上摘要缩小问题。"})
        self.assertEqual(response.status_code, 200, response.text)
        staged = response.json()
        self.assertEqual(staged["stage"], "directions_ready")
        self.assertIsNotNone(staged["workflow"]["pending_proposal"])
        self.assertEqual(staged["model_calls"][-1]["application_status"], "pending_review")
        saved = self.accept(project["id"])
        self.assertTrue(any(source["verification"] == "abstract_checked" for source in saved["direction_map"]["sources"]))
        self.assertIsNone(saved["direction_map"]["selected_topic_id"])
        self.assertEqual(engine.validate(saved["direction_map"]), [])

    def test_real_experiment_requires_user_selection_and_exports_exact_results(self):
        project = self.create(mode="demo", idea="随机低秩逼近")
        path = f'/api/projects/{project["id"]}/experiments'
        config = {"rows": 40, "cols": 20, "rank": 4, "p_values": [2], "q_values": [0], "repeats": 1, "data_seed": 20261016}
        self.assertFalse(self.client.get(path).json()["supported"])
        self.assertEqual(self.client.post(path, json=config).status_code, 409)
        self.client.post(f'/api/projects/{project["id"]}/select', json={"topic_id": "budget", "reason": "实验接入验证"})
        self.assertTrue(self.client.get(path).json()["supported"])
        response = self.client.post(path, json=config)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        run = saved["experiments"][-1]
        self.assertEqual(run["status"], "success", run)
        self.assertTrue(run["provenance"]["new_experiments_executed"])
        self.assertFalse(saved["direction_map"]["provenance"]["new_experiments_executed"])
        self.assertEqual(self.client.get(f'{path}/{run["id"]}/export').json(), run)
        self.assertIn("csv", self.client.get(f'{path}/{run["id"]}/export?format=csv').headers["content-type"])


if __name__ == "__main__":
    unittest.main()
