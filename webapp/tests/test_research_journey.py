"""Exact metadata imports and single-shot research actions; no live model/network."""

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import engine
import literature_service as literature
import workflow
from app import create_app
from test_direction_proposal import ready
from test_model_api import FakeModel

REAL_CLIENT = httpx.Client
DOI = "10.1137/090771806"
ITEM = {"DOI": DOI, "title": ["Finding structure with randomness"],
        "author": [{"given": "Nathan", "family": "Halko"}],
        "published": {"date-parts": [[2011]]}, "URL": "https://doi.org/" + DOI,
        "abstract": "<p>A public metadata abstract.</p>"}
ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/0909.4061v2</id><title>Finding structure with randomness</title>
<author><name>Nathan Halko</name></author><published>2009-09-22T00:00:00Z</published>
<summary>Metadata is not confirmed reading.</summary></entry></feed>'''


def search_record(idea, query, provider="crossref", limit=3):
    stamp = engine.now()
    record = literature._failed(idea, query, provider, limit, "")
    record["id"] = uuid.uuid4().hex
    source, _ = literature._client.parse_crossref(json.dumps({"message": {"items": [ITEM]}}),
                                                "Q001", "https://api.crossref.org/works", stamp)
    source[0]["id"] = "S001"
    record.update(sources=source, limitations=["题录不表示已读。"], next_action="请核对原文。")
    record["provenance"]["live_search_performed"] = True
    record["search_log"][0].update(status="success", attempts=1, searched_at=stamp, result_count=1, error=None)
    return record


class ResearchJourneyTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="journey-"))
        self.model = FakeModel()
        self.model.result = ready()
        self.database = self.directory / "research.db"
        self.app = create_app(self.database, model_client=self.model)
        self.client = TestClient(self.app)
        self.project = self.client.post("/api/projects", json={"initial_idea": "随机低秩逼近的误差与预算"}).json()
        self.base = "/api/projects/" + self.project["id"]
        self.requests = []

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.directory)

    def provider(self, response=None, status=200, atom=None):
        def handle(request):
            self.requests.append(str(request.url))
            return httpx.Response(status, content=atom if atom is not None else json.dumps(response or {"message": ITEM}),
                                  headers={"Content-Type": "application/atom+xml" if atom else "application/json"})
        return patch("literature_service.httpx.Client", side_effect=lambda **kw: REAL_CLIENT(
            transport=httpx.MockTransport(handle), **kw))

    def saved(self):
        return self.client.get(self.base).json()

    def plan(self):
        response = self.client.post(self.base + "/research-plan", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["research_plan"]

    def run_action(self, plan, action="search-1", request_id="request-0001"):
        return self.client.post(self.base + "/research-plan/actions/" + action + "/run",
                                json={"plan_id": plan["id"], "request_id": request_id})

    def read(self, run_id):
        response = self.client.post(self.base + "/reading-notes", json={"run_id": run_id, "source_id": "S001",
            "level": "abstract_checked", "content": "记录摘要中的适用范围。", "locator": "摘要", "confirmed": True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_reference_identifiers_and_url_rejection(self):
        for value in (DOI, "doi:" + DOI, "https://doi.org/" + DOI, "doi.org/" + DOI):
            self.assertEqual(literature.parse_reference(value)["identifier"], DOI)
        for value in ("0909.4061v2", "arXiv:0909.4061v2", "https://arxiv.org/pdf/0909.4061v2.pdf", "arxiv.org/abs/0909.4061v2"):
            self.assertEqual(literature.parse_reference(value)["identifier"], "0909.4061v2")
        for value in ("https://localhost/a", "http://127.0.0.1/a", "file:///secret", "https://doi.org@evil.test/" + DOI,
                      "https://doi.org/" + DOI + "?x=1", "https://arxiv.org:8080/abs/0909.4061", "bad-id"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                literature.parse_reference(value)

    def test_exact_doi_import_hash_duplicate_and_reading_snapshot(self):
        with self.provider():
            response = self.client.post(self.base + "/literature-import", json={"reference": DOI})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["import_status"], "success")
            source = result["project"]["literature_searches"][0]["sources"][0]
            self.assertEqual(source["verification"], "metadata_checked")
            self.assertFalse(result["project"].get("reading_notes"))
            self.assertEqual(source["import_metadata"]["response_sha256"], hashlib.sha256(json.dumps({"message": ITEM}).encode()).hexdigest())
            again = self.client.post(self.base + "/literature-import", json={"reference": "https://doi.org/" + DOI}).json()
            self.assertTrue(again["duplicate"])
            self.assertEqual(len(self.requests), 1)
            project = self.read(result["run_id"])
            note = project["reading_notes"][0]
            self.assertEqual(note["bibliographic_snapshot"]["import_metadata"], source["import_metadata"])
            self.assertNotIn("source_sha256", note)
            self.assertEqual(len(workflow.research_reading_notes(project)), 1)

    def test_arxiv_requested_version_is_preserved_and_mismatch_rejected(self):
        with self.provider(atom=ATOM), patch("literature_service.time.sleep"):
            record = literature.import_reference("研究", "0909.4061v2")
            self.assertEqual(record["sources"][0]["versions"], ["0909.4061v2"])
            self.assertIn("id_list=0909.4061v2", self.requests[0])
            mismatch = literature.import_reference("研究", "0909.4061v1")
            self.assertEqual(mismatch["search_log"][0]["status"], "failed")
            self.assertFalse(mismatch["sources"])

    def test_import_failure_and_not_found_are_saved_without_sources(self):
        for status, data in ((404, None), (503, None), (200, {"message": ITEM | {"DOI": "10.9999/wrong"}})):
            with self.subTest(status=status), self.provider(data, status):
                result = self.client.post(self.base + "/literature-import", json={"reference": DOI}).json()
                self.assertIn(result["import_status"], ("empty", "failed"))
                self.assertFalse(result["project"]["literature_searches"][-1]["sources"])
        self.assertEqual(len(self.saved()["literature_searches"]), 3)
        self.assertFalse(self.model.calls)

    def test_malformed_atom_and_non_official_links_never_become_sources(self):
        with self.provider(atom="<broken"), patch("literature_service.time.sleep"):
            record = literature.import_reference("研究", "0909.4061")
            self.assertEqual(record["search_log"][0]["status"], "failed")
        with patch("literature_service.import_reference") as fetch:
            response = self.client.post(self.base + "/literature-import", json={"reference": "https://private.test/paper"})
            self.assertEqual(response.status_code, 400)
            fetch.assert_not_called()

    def test_new_plan_is_saved_without_tools_and_executes_once_after_restart(self):
        with patch("literature_service.search", side_effect=search_record) as search:
            plan = self.plan()
            search.assert_not_called()
            self.assertFalse(self.model.calls)
            self.assertEqual(self.run_action(plan).status_code, 200)
            self.assertEqual(self.run_action(plan, request_id="request-0002").status_code, 200)
            self.assertEqual(search.call_count, 1)
            with TestClient(create_app(self.database, model_client=self.model)) as fresh:
                response = fresh.post(self.base + "/research-plan/actions/search-1/run", json={"plan_id": plan["id"], "request_id": "request-after-restart"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["research_plan"]["used"]["search_operations"], 1)
            self.assertEqual(search.call_count, 1)

    def test_pause_stale_and_budget_block_execution(self):
        with patch("literature_service.search") as search:
            plan = self.plan()
            self.client.post(self.base + "/research-plan/pause", json={})
            self.assertEqual(self.run_action(plan).status_code, 409)
            plan = self.plan()
            topic = self.saved()["direction_map"]["topics"][0]["id"]
            self.client.post(self.base + "/select", json={"topic_id": topic})
            self.assertTrue(self.saved()["research_plan"]["context_stale"])
            self.assertEqual(self.run_action(plan).status_code, 409)
            plan = self.plan()
            self.app.state.store.update(self.project["id"], lambda p: p["research_plan"]["limits"].update(search_operations=0))
            self.assertEqual(self.run_action(plan).status_code, 400)
            search.assert_not_called()

    def test_revision_requires_actual_reading_and_creates_review_without_applying(self):
        plan = self.plan()
        self.assertEqual(self.run_action(plan, "revise").status_code, 400)
        self.assertEqual(self.saved()["research_plan"]["used"]["model_operations"], 0)
        with patch("literature_service.search", side_effect=search_record):
            self.run_action(plan)
        run = self.saved()["literature_searches"][0]
        self.read(run["id"])
        self.assertFalse(self.saved()["research_plan"]["context_stale"])
        before = self.saved()["direction_map"]
        result = self.run_action(plan, "revise")
        self.assertEqual(result.status_code, 200, result.text)
        project = result.json()
        self.assertEqual(project["direction_map"], before)
        self.assertIsNotNone(project["workflow"]["pending_proposal"])
        self.assertEqual(project["research_plan"]["actions"][-1]["status"], "awaiting_review")
        self.assertEqual(self.model.calls, 1)
        self.run_action(plan, "revise", "request-duplicate")
        self.assertEqual(self.model.calls, 1)
        pending = project["workflow"]["pending_proposal"]
        self.assertFalse(workflow.pending_proposal_is_stale(self.app.state.store.get(project["id"])))
        response = self.client.post(self.base + "/direction/proposals/" + pending["id"] + "/accept", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.saved()["research_plan"]["context_stale"])

    def test_curated_notes_do_not_unlock_evidence_revision(self):
        with patch("literature_service.search", side_effect=search_record):
            plan = self.plan(); self.run_action(plan)
        self.read(self.saved()["literature_searches"][0]["id"])
        self.app.state.store.update(self.project["id"], lambda p: p["reading_notes"][0].update(origin="curated_example"))
        self.assertEqual(self.run_action(plan, "revise").status_code, 400)
        self.assertFalse(self.model.calls)

    def test_imported_reading_binding_survives_accept_and_rejects_malformed_metadata(self):
        with self.provider():
            self.client.post(self.base + "/literature-import", json={"reference": DOI})
        run = self.saved()["literature_searches"][0]
        self.read(run["id"])
        note = self.saved()["reading_notes"][0]
        source = workflow.context_sources(self.saved())[0]
        self.model.result["topics"][0]["evidence"] = [{"source_id": source["id"], "note_id": note["id"],
            "relation": "supports", "claim": "摘要提供背景，具体结论仍需核查。"}]
        plan = self.plan()
        pending = self.run_action(plan, "revise").json()["workflow"]["pending_proposal"]
        response = self.client.post(self.base + "/direction/proposals/" + pending["id"] + "/accept", json={})
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()["direction_map"]
        snapshot = record["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(snapshot["bibliographic_snapshot"], note["bibliographic_snapshot"])
        self.assertFalse(engine.validate(record))
        snapshot["bibliographic_snapshot"]["import_metadata"]["response_sha256"] = "invalid"
        self.assertTrue(engine.validate(record))

    def test_running_action_is_unknown_after_explicit_replan_not_retried(self):
        plan = self.plan()
        self.app.state.store.update(self.project["id"], lambda p: p["research_plan"]["actions"][0].update(status="running"))
        with patch("literature_service.search") as search:
            self.run_action(plan)
            new = self.plan()
            search.assert_not_called()
        project = self.saved()
        self.assertNotEqual(new["id"], plan["id"])
        self.assertEqual(project["research_plan_history"][0]["actions"][0]["status"], "interrupted_result_unknown")

    def test_exports_preserve_metadata_and_actual_action_results(self):
        with self.provider():
            self.client.post(self.base + "/literature-import", json={"reference": DOI})
        plan = self.plan()
        with patch("literature_service.search", side_effect=search_record):
            self.run_action(plan)
        topic = self.saved()["direction_map"]["topics"][0]["id"]
        self.client.post(self.base + "/select", json={"topic_id": topic})
        exported = self.client.get(self.base + "/export").json()
        self.assertEqual(exported["research_plan"]["actions"][0]["status"], "succeeded")
        markdown = self.client.get(self.base + "/decision-package").text
        self.assertIn("不是全文指纹", markdown)
        self.assertIn("本轮行动与人工确认", markdown)
        self.assertIn("独立检索记录", markdown)
