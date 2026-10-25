"""Meaningful checks for persistence, transitions and honest provenance."""

import concurrent.futures
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import create_app
from engine import validate
from fastapi.testclient import TestClient


class BackendTest(unittest.TestCase):
    def setUp(self):
        temporary_root = BASE / "tests/.tmp"
        temporary_root.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(dir=temporary_root))
        self.database = self.directory / "test.db"
        self.client = TestClient(create_app(self.database))

    def tearDown(self):
        self.client.close()
        self.assertTrue(self.directory.resolve().is_relative_to((BASE / "tests/.tmp").resolve()))
        shutil.rmtree(self.directory)

    def create(self, idea="我想研究人工智能"):
        response = self.client.post("/api/projects", json={"initial_idea": idea, "goal": "experimental", "resources": "laptop", "time_horizon": "week", "context": ""})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def assert_map(self, project):
        self.assertEqual(validate(project["direction_map"]), [])
        self.assertEqual(project["stage"], project["direction_map"]["stage"])

    def test_complete_lifecycle_and_restart(self):
        project = self.create()
        project_id = project["id"]
        self.assertEqual(project["stage"], "clarifying")
        self.assertEqual(len(project["direction_map"]["questions"]), 3)
        self.assert_map(project)
        project = self.client.post(f"/api/projects/{project_id}/answers", json={"answers": {"research_object": "中文短文本分类"}}).json()
        self.assertEqual(len(project["direction_map"]["questions"]), 2)
        project = self.client.post(f"/api/projects/{project_id}/answers", json={"answers": {"desired_result": "比较现有方法", "available_material": "暂时没有公开数据集"}}).json()
        self.assertEqual(project["stage"], "directions_ready")
        self.assertEqual(len(project["direction_map"]["topics"]), 3)
        self.assert_map(project)
        topic_id = project["direction_map"]["topics"][0]["id"]
        project = self.client.post(f"/api/projects/{project_id}/select", json={"topic_id": topic_id}).json()
        self.assertEqual(project["stage"], "topic_selected")
        self.assertIn("用户通过网页点击选择", project["direction_map"]["intake"]["known"]["selection_basis"])
        self.assert_map(project)
        with TestClient(create_app(self.database)) as fresh:
            persisted = fresh.get(f"/api/projects/{project_id}").json()
            self.assertEqual(persisted, project)
            self.assertEqual(fresh.get("/api/projects").json()["projects"][0]["selected_topic_title"], project["direction_map"]["topics"][0]["title"])
        exported = self.client.get(f"/api/projects/{project_id}/export")
        self.assertEqual(exported.json(), project)
        self.assertIn("attachment", exported.headers["content-disposition"])
        plan = self.client.get(f"/api/projects/{project_id}/plan")
        self.assertEqual(plan.status_code, 200)
        self.assertIn("未调用千问", plan.text)
        self.assertIn("中文短文本分类", plan.text)
        history_count = len(project["history"])
        project = self.client.post(f"/api/projects/{project_id}/reopen").json()
        self.assertEqual(project["stage"], "directions_ready")
        self.assertIsNone(project["direction_map"]["selected_topic_id"])
        self.assertEqual(len(project["history"]), history_count + 1)
        self.assert_map(project)

    def test_seed_adapter_and_provenance(self):
        project = self.create("我希望研究随机低秩逼近")
        self.assertEqual(project["stage"], "directions_ready")
        record = project["direction_map"]
        self.assertEqual(record["intake"]["assumptions"], [])
        self.assertIsNone(record["selected_topic_id"])
        self.assertEqual(record["provenance"]["generator"], "local_demo_rules")
        for key in ("live_search_performed", "external_model_api_called", "new_experiments_executed"):
            self.assertIs(record["provenance"][key], False)
        self.assertTrue(record["sources"])
        for source in record["sources"]:
            self.assertEqual(source["origin"], "prior_project")
            self.assertEqual(source["checked_at"], "2026-10-13")
            self.assertEqual(source["verification"], "metadata_checked")
        self.assert_map(project)

    def test_generic_never_fakes_sources_or_svd(self):
        project = self.create("我想研究城市公共交通")
        answers = {question["id"]: "公交站点拥堵；已有一份观察记录" for question in project["direction_map"]["questions"]}
        project = self.client.post(f'/api/projects/{project["id"]}/answers', json={"answers": answers}).json()
        self.assertEqual(project["direction_map"]["sources"], [])
        self.assertEqual(project["direction_map"]["intake"]["assumptions"], [])
        self.assertNotIn("SVD", json.dumps(project["direction_map"], ensure_ascii=False))
        self.assertTrue(all(entry["outcome"] == "planned" for entry in project["direction_map"]["search_log"]))
        self.assert_map(project)

    def test_invalid_inputs_and_ids_leave_state_intact(self):
        self.assertEqual(self.client.post("/api/projects", json={"initial_idea": "   "}).status_code, 422)
        self.assertEqual(self.client.post("/api/projects", json={"initial_idea": "x" * 3001}).status_code, 422)
        self.assertEqual(self.client.get("/api/projects/missing").status_code, 404)
        project = self.create("随机低秩逼近")
        project_id = project["id"]
        self.assertEqual(self.client.post(f"/api/projects/{project_id}/select", json={"topic_id": "missing"}).status_code, 400)
        self.assertEqual(self.client.get(f"/api/projects/{project_id}/plan").status_code, 400)
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").json(), project)
        self.assertEqual(self.client.post(f"/api/projects/{project_id}/select", json={"topic_id": "budget"}, headers={"Origin": "https://untrusted.invalid"}).status_code, 403)

    def test_decision_package_requires_selection_and_exports_markdown(self):
        project = self.create("我想研究随机低秩逼近")
        project_id = project["id"]
        endpoint = f"/api/projects/{project_id}/decision-package"
        self.assertEqual(self.client.get(endpoint).status_code, 400)
        topic_id = project["direction_map"]["topics"][0]["id"]
        selected = self.client.post(
            f"/api/projects/{project_id}/select",
            json={"topic_id": topic_id, "reason": "先复现基线 <script>bad</script>"},
        )
        self.assertEqual(selected.status_code, 200, selected.text)
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/markdown", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("cache-control", response.headers)
        for heading in ("全部候选与推荐", "选择理由", "来源、阅读等级与论断关系", "风险、假设与下一步有效验证", "模型、文献与实验限制"):
            self.assertIn(heading, response.text)
        self.assertIn("先复现基线", response.text)
        self.assertIn("研究记录不证明科学结论成立", response.text)

    def test_concurrent_updates_keep_all_history(self):
        project = self.create("随机低秩逼近")
        project_id = project["id"]
        initial_count = len(project["history"])
        def choose(index):
            return self.client.post(f"/api/projects/{project_id}/select", json={"topic_id": ["budget", "spectrum", "krylov"][index % 3]}).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(choose, range(12)))
        self.assertEqual(results, [200] * 12)
        project = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(len(project["history"]), initial_count + 12)
        self.assert_map(project)


if __name__ == "__main__":
    unittest.main()
