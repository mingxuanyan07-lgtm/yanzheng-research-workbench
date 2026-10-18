"""No network: search facts, separate contracts and archive boundaries."""

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
from model_service import ModelService
import engine
import literature_service
from fastapi.testclient import TestClient


def search_record(status="success"):
    stamp = "2026-10-15T02:00:00+00:00"
    record = literature_service._failed("随机低秩逼近", "randomized svd", "crossref", 5, "HTTP 503")
    record["created_at"] = stamp
    record["provenance"]["live_search_performed"] = True
    log = record["search_log"][0]
    log.update({"status": status, "attempts": 1, "searched_at": stamp,
                "error": "HTTP 503" if status == "failed" else None,
                "result_count": 1 if status == "success" else 0})
    if status == "success":
        source = literature_service._client.new_source(
            "A fixture paper", ["Fixture Author"], 2024, "https://doi.org/10.1000/fixture",
            {"doi": "10.1000/fixture", "arxiv_id": None, "openalex_id": None},
            "Returned abstract; nobody has read it.", literature_service._client.empty_access(),
            "Q001", "crossref", "https://api.crossref.org/works?query=randomized+svd", stamp)
        record["sources"] = literature_service._client.deduplicate_sources([source])
        record["limitations"] = ["API metadata only; abstract not reviewed."]
    return record


class LiteratureApiTest(unittest.TestCase):
    def setUp(self):
        temporary_root = BASE / "tests/.tmp"
        temporary_root.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(dir=temporary_root))
        self.database = self.directory / "test.db"
        self.application = create_app(self.database, model_client=ModelService(env_file=self.directory / "absent.env", environ={}))
        self.client = TestClient(self.application)

    def tearDown(self):
        self.client.close()
        self.assertTrue(self.directory.resolve().is_relative_to((BASE / "tests/.tmp").resolve()))
        shutil.rmtree(self.directory)

    def create(self):
        return self.client.post("/api/projects", json={"initial_idea": "随机低秩逼近"}).json()

    def search(self, project_id):
        return self.client.post(f"/api/projects/{project_id}/literature-search",
                                json={"query": "randomized svd"})

    def test_search_outside_lock_persists_separate_record_and_export(self):
        project = self.create()
        original_map = copy.deepcopy(project["direction_map"])
        def fake_search(*args, **kwargs):
            self.assertEqual(kwargs["timeout"], 12)
            self.assertEqual(kwargs["retries"], 0)
            self.assertIsNone(kwargs["api_key"])
            # If the route holds BEGIN IMMEDIATE, this second connection cannot write.
            self.application.state.store.update(project["id"], lambda current: current.update({"lock_probe": True}))
            return search_record()
        with patch.object(literature_service, "run_search", side_effect=fake_search):
            response = self.search(project["id"])
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertTrue(saved["lock_probe"])
        self.assertEqual(saved["direction_map"], original_map)
        record = saved["literature_searches"][0]
        self.assertEqual(literature_service.validate(record), [])
        self.assertEqual(record["sources"][0]["verification"], "metadata_checked")
        self.assertEqual(saved["history"][-1]["action"], "literature_searched")
        self.assertEqual(self.client.get("/api/projects").json()["projects"][0]["literature_search_count"], 1)
        with TestClient(create_app(self.database)) as fresh:
            self.assertEqual(fresh.get(f'/api/projects/{project["id"]}').json(), saved)
            exported = fresh.get(f'/api/projects/{project["id"]}/literature/{record["id"]}/export')
            self.assertEqual(exported.json(), record)
            self.assertIn("attachment", exported.headers["content-disposition"])

    def test_failures_and_empty_results_are_retained_and_plan_is_honest(self):
        project = self.create()
        with patch.object(literature_service, "run_search", side_effect=[search_record("failed"), search_record("empty")]):
            self.assertEqual(self.search(project["id"]).status_code, 200)
            saved = self.search(project["id"]).json()
        self.assertEqual(len(saved["literature_searches"]), 2)
        self.assertNotEqual(saved["literature_searches"][0]["id"], saved["literature_searches"][1]["id"])
        self.assertEqual(saved["literature_searches"][0]["search_log"][0]["error"], "HTTP 503")
        response = self.client.post(f'/api/projects/{project["id"]}/select', json={"topic_id": "budget", "reason": "先复现可信基线，控制本周的计算规模。"})
        self.assertEqual(response.status_code, 200)
        plan = self.client.get(f'/api/projects/{project["id"]}/plan').text
        self.assertIn("计划草案", plan)
        self.assertIn("实际请求 2 次", plan)
        self.assertIn("成功查询 0、未命中 1、失败 1", plan)
        self.assertIn("HTTP 503", plan)
        self.assertIn("未调用千问", plan)
        self.assertIn("先复现可信基线", plan)
        self.assertNotIn("未实时检索", plan)

    def test_unknown_ids_and_invalid_parameters_make_no_requests(self):
        project = self.create()
        with patch.object(literature_service, "run_search") as mocked:
            self.assertEqual(self.search("missing").status_code, 404)
            path = f'/api/projects/{project["id"]}/literature-search'
            for body in ({"query": " "}, {"query": "x" * 301}, {"query": "x", "provider": "openalex"}, {"query": "x", "limit": 6}, {"query": "x", "limit": True}):
                self.assertEqual(self.client.post(path, json=body).status_code, 422)
            mocked.assert_not_called()
        self.assertEqual(self.client.get(f'/api/projects/{project["id"]}/literature/missing/export').status_code, 404)

    def test_plan_handles_unverified_null_date_without_upgrading(self):
        project = self.create()
        source = project["direction_map"]["sources"][0]
        source.update({"verification": "unverified", "checked_at": None, "locator": None, "url": None})
        for topic in project["direction_map"]["topics"]:
            for evidence in topic["evidence"]:
                if evidence["source_id"] == source["id"]:
                    evidence["relation"] = "unverified_lead"
        engine.select_topic(project, "budget")
        self.assertEqual(engine.validate(project["direction_map"]), [])
        plan = engine.render_plan(project)
        self.assertIn("核查日期：未核实", plan)
        self.assertIn("unverified\\_lead", plan)

    def test_library_reads_exact_archived_counts_and_allowlists_downloads(self):
        status = self.client.get("/api/status").json()
        self.assertEqual(status["version"], "0.5.0")
        self.assertEqual(status["capabilities"]["model"], "not_configured")
        library = self.client.get("/api/library").json()
        self.assertEqual(len(library["literature"]), 4)
        self.assertTrue(all(source["origin"] == "prior_project" and source["locator"] for source in library["literature"]))
        experiment = library["experiment"]
        self.assertEqual([experiment[key] for key in ("matrix_count", "configuration_count", "randomized_run_count", "baseline_run_count")], [3, 27, 540, 60])
        self.assertIs(library["provenance"]["new_experiments_executed"], False)
        for artifact in library["artifacts"]:
            response = self.client.get(artifact["download_url"])
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertIsInstance(json.loads(response.text), dict)
        self.assertEqual(self.client.get("/api/library/artifacts/research.db").status_code, 404)

    def test_arxiv_busy_record_has_zero_attempts(self):
        literature_service._arxiv_lock.acquire()
        try:
            with patch.object(literature_service, "run_search") as mocked:
                record = literature_service.search("topic", "randomized svd", "arxiv", 3)
                mocked.assert_not_called()
        finally:
            literature_service._arxiv_lock.release()
        self.assertEqual(literature_service.validate(record), [])
        self.assertEqual(record["search_log"][0]["attempts"], 0)
        self.assertIs(record["provenance"]["live_search_performed"], False)
        self.assertFalse(record.get("execution_state_unknown", False))

    def test_unknown_execution_is_not_reported_as_no_requests(self):
        project = self.create()
        with patch.object(literature_service, "run_search", side_effect=RuntimeError("private-secret-do-not-display")):
            response = self.search(project["id"])
        self.assertEqual(response.status_code, 200)
        saved = response.json()
        record = saved["literature_searches"][0]
        self.assertEqual(literature_service.validate(record), [])
        self.assertIs(record["execution_state_unknown"], True)
        self.assertEqual(record["search_log"][0]["attempts"], 0)
        self.assertIn("执行状态未知", saved["history"][-1]["summary"])
        self.client.post(f'/api/projects/{project["id"]}/select', json={"topic_id": "budget"})
        plan = self.client.get(f'/api/projects/{project["id"]}/plan').text
        self.assertIn("可确认请求 0 次，另有 1 轮执行状态未知", plan)
        self.assertIn("可确认请求次数：0；本轮执行状态未知", plan)
        self.assertNotIn("实际请求 0 次", plan)
        self.assertNotIn("private-secret", response.text + plan)


if __name__ == "__main__":
    unittest.main()
