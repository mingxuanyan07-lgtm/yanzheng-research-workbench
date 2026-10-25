"""Recover actual project contracts atomically; never use live tools or user DBs."""

import concurrent.futures
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import engine
import storage
import theory_examples
import workflow
from app import create_app
from test_direction_legacy import legacy_project
from test_model_api import FakeModel
from test_research_journey import search_record
from test_workflow import call, directions


class LocalBackupTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="local-backup-")
        self.root = Path(self.folder.name)
        self.source = create_app(self.root / "source.db", model_client=FakeModel())
        self.destination = create_app(self.root / "destination.db", model_client=FakeModel())
        self.client = TestClient(self.source)
        self.target = TestClient(self.destination)

    def tearDown(self):
        self.client.close()
        self.target.close()
        self.folder.cleanup()

    def create(self, idea="备份恢复的科研记录"):
        response = self.client.post("/api/projects", json={"initial_idea": idea})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def exported(self):
        response = self.client.get("/api/backups/export")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("attachment", response.headers["content-disposition"])
        return response.json()

    def restore(self, document):
        return self.target.post("/api/backups/restore", json=document)

    def rehash(self, document):
        document["project_count"] = len(document["projects"])
        document["projects_sha256"] = hashlib.sha256(storage._canonical(document["projects"])).hexdigest()
        return document

    def test_empty_database_round_trip_and_format(self):
        document = self.exported()
        self.assertEqual(document["format"], "yanzheng-local-backup")
        self.assertEqual(document["version"], 1)
        response = self.restore(document)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported_count"], 0)
        self.assertEqual(self.target.get("/api/projects").json()["projects"], [])

    def test_round_trip_preserves_identifiers_reading_metadata_history_and_selection(self):
        project = self.create("随机低秩逼近")
        record = search_record(project["intake_form"]["initial_idea"], "randomized svd")
        record["sources"][0]["import_metadata"] = {"response_sha256": "c" * 64,
            "scope": "official_metadata_response_only", "versions": ["v2"]}
        self.source.state.store.update(project["id"], lambda item: engine.add_literature_search(item, record))
        note_response = self.client.post("/api/projects/" + project["id"] + "/reading-notes", json={
            "run_id": record["id"], "source_id": "S001", "level": "abstract_checked",
            "content": "仅对摘要范围作出阅读声明。", "locator": "摘要", "confirmed": True})
        self.assertEqual(note_response.status_code, 200, note_response.text)
        self.client.post("/api/projects/" + project["id"] + "/select", json={
            "topic_id": project["direction_map"]["topics"][0]["id"], "reason": "先检查文献条件"})
        before = self.source.state.store.get(project["id"])
        response = self.restore(self.exported())
        self.assertEqual(response.status_code, 200, response.text)
        after = self.destination.state.store.get(project["id"])
        for key in ("id", "created_at", "direction_map", "reading_notes", "stage"):
            self.assertEqual(after[key], before[key])
        restored_search = copy.deepcopy(after["literature_searches"][0])
        self.assertEqual(restored_search.pop("backup_restore")["origin"], "imported_snapshot_not_reverified")
        self.assertEqual(restored_search, before["literature_searches"][0])
        self.assertEqual(after["history"][:-1], before["history"])
        self.assertEqual(after["history"][-1]["action"], "backup_restored")
        self.assertEqual(after["backup_restore"]["origin"], "imported_snapshot_not_reverified")
        self.assertEqual(after["backup_restore"]["status"], "imported_external_snapshot")
        self.assertEqual(after["backup_restore"]["revalidation_status"], "not_revalidated")
        self.assertFalse(after["backup_restore"]["original_files_included"])
        self.assertNotIn("source_sha256", after["reading_notes"][0])
        self.assertEqual(self.target.get("/api/projects/" + project["id"] + "/decision-package").status_code, 200)

    def test_same_id_skipped_without_overwrite_or_duplicate_events(self):
        project = self.create()
        document = self.exported()
        self.assertEqual(self.restore(document).json()["imported_count"], 1)
        self.destination.state.store.update(project["id"], lambda item: item.update(title="本机更新的标题"))
        before = self.destination.state.store.snapshot()
        result = self.restore(document).json()
        self.assertEqual((result["imported_count"], result["skipped_count"]), (0, 1))
        self.assertEqual(result["skipped_ids"], [project["id"]])
        self.assertEqual(self.destination.state.store.snapshot(), before)

    def test_mixed_conflicts_import_only_missing_and_report_exact_ids(self):
        first = self.create("已有研究")
        self.restore(self.exported())
        second = self.create("新增研究")
        result = self.restore(self.exported()).json()
        self.assertEqual(result["imported_ids"], [second["id"]])
        self.assertEqual(result["skipped_ids"], [first["id"]])
        self.assertEqual(result["total_count"], 2)
        self.assertEqual([item["id"] for item in result["projects"]], [second["id"]])

    def test_bad_last_project_or_digest_does_not_partially_write(self):
        self.create("有效研究")
        self.create("将被破坏的研究")
        document = self.exported()
        document["projects"][-1]["direction_map"]["topics"] = "bad"
        for altered in (document, self.rehash(copy.deepcopy(document))):
            response = self.restore(altered)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("备份未恢复", response.json()["detail"])
            self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_duplicate_project_id_unknown_version_scope_and_single_export_rejected(self):
        project = self.create()
        document = self.exported()
        cases = []
        duplicate = copy.deepcopy(document)
        duplicate["projects"].append(copy.deepcopy(duplicate["projects"][0]))
        cases.append(self.rehash(duplicate))
        for field, value in (("version", 2), ("scope", "full_disk"), ("project_count", True)):
            changed = copy.deepcopy(document)
            changed[field] = value
            cases.append(changed)
        cases.append(project)
        cases.append({"path": "C:/some/file.json"})
        for altered in cases:
            with self.subTest(altered=list(altered)):
                response = self.restore(altered)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_dot_segment_ids_are_rejected_before_creating_unreachable_projects(self):
        self.create()
        original = self.exported()
        for identifier in (".", ".."):
            document = copy.deepcopy(original)
            document["projects"][0]["id"] = identifier
            response = self.restore(self.rehash(document))
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_unsafe_json_duplicate_keys_nonfinite_and_depth_rejected(self):
        values = [b'{"format":"one","format":"two"}', b'{"n":NaN}', b'{"n":Infinity}',
                  b'{"n":1e309}', b'{"n":"\\ud800"}',
                  b'[' * 90 + b'0' + b']' * 90, b'\xff', b'{broken']
        for content in values:
            response = self.target.post("/api/backups/restore", content=content,
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_malformed_nested_field_types_are_chinese_errors_without_partial_write(self):
        self.create()
        original = self.exported()
        for key, value in (("goal", {}), ("resources", []), ("time_horizon", {})):
            document = copy.deepcopy(original)
            document["projects"][0]["intake_form"][key] = value
            response = self.restore(self.rehash(document))
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("备份未恢复", response.json()["detail"])
        self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_size_checked_for_content_length_and_chunked_body(self):
        with patch.object(storage, "MAX_BYTES", 100):
            response = self.target.post("/api/backups/restore", content=b'x' * 101,
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 413)
            response = self.target.post("/api/backups/restore", content=iter([b'x' * 60, b'x' * 60]),
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 413)
        self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_cross_site_mutation_and_non_json_are_rejected(self):
        response = self.target.post("/api/backups/restore", json={}, headers={"Origin": "https://evil.test"})
        self.assertEqual(response.status_code, 403)
        response = self.target.post("/api/backups/restore", content="{}")
        self.assertEqual(response.status_code, 415)

    def test_running_operations_pause_and_unknown_without_network_or_model(self):
        project = self.create()
        def running(item):
            workflow.create_research_plan(item)
            workflow.reserve_research_action(item, item["research_plan"]["id"], "search-1", "request-001")
            item["lean_attempts"] = [{"id": "attempt-1", "state": "running"}]
        self.source.state.store.update(project["id"], running)
        document = self.exported()
        with patch("literature_service.search", side_effect=AssertionError("network")), \
             patch("model_service.ModelService.complete", side_effect=AssertionError("model")), \
             patch("experiments.run", side_effect=AssertionError("experiment")), \
             patch("lean_service.run", side_effect=AssertionError("compiler"), create=True):
            response = self.restore(document)
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.destination.state.store.get(project["id"])
        plan = saved["research_plan"]
        self.assertEqual(plan["id"], document["projects"][0]["research_plan"]["id"])
        self.assertEqual(plan["status"], "paused")
        self.assertEqual(plan["actions"][0]["status"], "interrupted_result_unknown")
        self.assertEqual(plan["used"], document["projects"][0]["research_plan"]["used"])
        self.assertEqual(saved["lean_attempts"][0]["state"], "interrupted_result_unknown")
        denied = self.target.post("/api/projects/" + project["id"] + "/research-plan/actions/revise/run",
                                 json={"plan_id": plan["id"], "request_id": "request-002"})
        self.assertEqual(denied.status_code, 409)

    def test_pending_direction_and_theory_preserved_but_cannot_accept_imported_preview(self):
        form = {"initial_idea": "普通科研问题", "goal": "experimental", "resources": "laptop", "time_horizon": "week", "context": ""}
        project = workflow.initialize_project(form)
        workflow.store_proposal(project, directions(), call())
        self.source.state.store.create(project)
        curated = theory_examples.create_example()
        self.source.state.store.create(curated)
        response = self.restore(self.exported())
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.destination.state.store.get(project["id"])
        pending = saved["workflow"]["pending_proposal"]
        self.assertEqual(pending["id"], project["workflow"]["pending_proposal"]["id"])
        self.assertEqual(pending["payload"], project["workflow"]["pending_proposal"]["payload"])
        self.assertTrue(workflow.pending_proposal_is_stale(saved))
        denied = self.target.post("/api/projects/" + project["id"] + "/direction/proposals/" + pending["id"] + "/accept", json={})
        self.assertEqual(denied.status_code, 409, denied.text)
        theory_pending = self.destination.state.store.get(curated["id"])["theory"]["pending_proposal"]
        denied = self.target.post("/api/projects/" + curated["id"] + "/theory/proposals/" + theory_pending["id"] + "/accept", json={})
        self.assertEqual(denied.status_code, 400, denied.text)

    def test_legacy_map_is_restored_without_upgrading_unbound_claims(self):
        project = legacy_project()
        self.source.state.store.create(project)
        response = self.restore(self.exported())
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.destination.state.store.get(project["id"])
        self.assertEqual(saved["direction_map"], project["direction_map"])
        self.assertEqual(engine.validate(saved["direction_map"], allow_saved_unbound_evidence=True), [])
        self.assertTrue(engine.validate(saved["direction_map"]))

    def test_individual_lean_export_retains_import_origin_without_upgrading_status(self):
        project = self.create()
        entry = {"id": "lean-snapshot-1", "context": {"project_id": project["id"], "card_version": None, "card_sha256": None},
                 "verification": {"id": "lean-snapshot-1", "status": "passed"},
                 "association_status": "context_only_not_semantically_verified"}
        self.source.state.store.update(project["id"], lambda item: item.update(lean_runs=[entry]))
        response = self.restore(self.exported())
        self.assertEqual(response.status_code, 200, response.text)
        exported = self.target.get("/api/projects/" + project["id"] + "/lean/runs/lean-snapshot-1/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        value = exported.json()
        self.assertEqual(value["verification"], entry["verification"])
        self.assertEqual(value["backup_restore"]["origin"], "imported_snapshot_not_reverified")
        self.assertIn("未重新", value["backup_restore"]["caveat"])
        # A later local record has its own provenance, without inheriting this
        # marker merely because its enclosing project came from a backup.
        fresh = copy.deepcopy(entry)
        fresh["id"] = "lean-local-2"
        self.destination.state.store.update(project["id"], lambda item: item["lean_runs"].append(fresh))
        exported = self.target.get("/api/projects/" + project["id"] + "/lean/runs/lean-local-2/export").json()
        self.assertNotIn("backup_restore", exported)

    def test_checking_record_ownership_is_checked_without_matching_current_revision(self):
        from test_theory_checking import CheckingTest
        fixture = CheckingTest()
        fixture.setUp()
        try:
            fixture.save()
            project = fixture.app.state.store.get(fixture.p["id"])
        finally:
            fixture.tearDown()
        self.source.state.store.create(project)
        document = self.exported()
        for field in (None, "target", "context_basis"):
            altered = copy.deepcopy(document)
            item = altered["projects"][0]["theory"]["checking_records"][0]
            owner = item if field is None else item[field]
            owner["project_id"] = "another-project"
            response = self.restore(self.rehash(altered))
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.destination.state.store.snapshot(), [])
        response = self.restore(document)
        self.assertEqual(response.status_code, 200, response.text)
        restored = self.destination.state.store.get(project["id"])
        item = restored["theory"]["checking_records"][0]
        self.assertEqual(item["context_basis"], project["theory"]["checking_records"][0]["context_basis"])
        self.assertEqual(item["backup_restore"]["origin"], "imported_snapshot_not_reverified")

    def test_cross_project_note_binding_rejected_before_any_insert(self):
        self.create()
        document = self.exported()
        document["projects"][0]["reading_notes"] = [{"id": "orphan", "run_id": "another-research", "source_id": "S001",
            "level": "fulltext_checked", "content": "外部声明", "locator": "第1页", "confirmed": True}]
        response = self.restore(self.rehash(document))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_write_failure_rolls_back_prior_insert_and_api_reports_failure(self):
        self.create("第一份")
        self.create("第二份")
        document = self.exported()
        blocked_id = document["projects"][1]["id"]
        connection = self.destination.state.store._connect()
        try:
            connection.execute("CREATE TRIGGER reject_backup BEFORE INSERT ON projects "
                               "WHEN NEW.id = '" + blocked_id + "' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
            connection.commit()
        finally:
            connection.close()
        response = self.restore(document)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("没有恢复任何研究", response.json()["detail"])
        self.assertEqual(self.destination.state.store.snapshot(), [])

    def test_two_concurrent_restores_add_each_identity_once(self):
        self.create("并发恢复A")
        self.create("并发恢复B")
        document = self.exported()
        projects = storage.restored_projects(document)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.destination.state.store.restore_missing(projects), range(2)))
        self.assertEqual(sum(len(imported) for imported, _ in results), 2)
        self.assertEqual(sum(len(skipped) for _, skipped in results), 2)
        saved = self.destination.state.store.snapshot()
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(item["history"][-1]["action"] == "backup_restored" for item in saved))


if __name__ == "__main__":
    unittest.main()
