"""Declared reading-note source version/hash chain: structure, caveat and propagation."""

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
from test_model_api import FakeModel
from test_literature_api import search_record
import engine
import literature_service
import workflow
from test_workflow import add_search, call, directions, project


HASH = "a" * 64


class ReadingFingerprintApiTest(unittest.TestCase):
    def setUp(self):
        self.root = BASE / "tests/.tmp"
        self.root.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(dir=self.root))
        self.app = create_app(self.directory / "test.db", model_client=FakeModel())
        self.client = TestClient(self.app)
        response = self.client.post("/api/projects", json={"initial_idea": "公开文献阅读指纹校验。"})
        self.project = response.json()
        with patch.object(literature_service, "run_search", return_value=search_record()):
            searched = self.client.post(f'/api/projects/{self.project["id"]}/literature-search', json={"query": "randomized svd"}).json()
        self.run = searched["literature_searches"][0]
        self.source = self.run["sources"][0]
        self.base = {"run_id": self.run["id"], "source_id": self.source["id"], "level": "abstract_checked",
                     "content": "摘要只讨论特定条件。", "locator": "Abstract", "confirmed": True}

    def tearDown(self):
        self.client.close()
        assert self.directory.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.directory)

    def reading_notes(self):
        return self.client.get(f'/api/projects/{self.project["id"]}').json().get("reading_notes", [])

    def test_valid_declared_triple_is_persisted_with_strict_caveat(self):
        body = self.base | {"source_version": "v2, 2026-10-19 archive", "source_sha256": HASH,
                            "source_sha256_scope": "user_declared_fulltext_file"}
        response = self.client.post(f'/api/projects/{self.project["id"]}/reading-notes', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        note = response.json()["reading_notes"][-1]
        self.assertEqual(note["source_version"], "v2, 2026-10-19 archive")
        self.assertEqual(note["source_sha256"], HASH)
        self.assertEqual(note["source_sha256_scope"], "user_declared_fulltext_file")
        fingerprint = note["source_fingerprint"]
        self.assertEqual(fingerprint["source_version"], "v2, 2026-10-19 archive")
        self.assertEqual(fingerprint["source_sha256"], HASH)
        self.assertEqual(fingerprint["source_sha256_scope"], "user_declared_fulltext_file")
        self.assertEqual(fingerprint["origin"], "user_declared")
        self.assertIn("程序没有读取/认证原文", fingerprint["caveat"])
        self.assertIn("不证明内容真实性", fingerprint["caveat"])
        self.assertNotIn("已验证", fingerprint["caveat"])
        self.assertNotIn("authenticated", fingerprint["caveat"])
        saved = self.reading_notes()[-1]
        self.assertEqual(saved["source_fingerprint"], fingerprint)

    def test_incoherent_or_malformed_declarations_are_rejected_without_saving(self):
        invalid = [
            {"source_version": "v2"},
            {"source_sha256": HASH},
            {"source_sha256_scope": "user_declared_metadata"},
            {"source_version": "v2", "source_sha256": HASH},
            {"source_version": "v2", "source_sha256": HASH.upper(), "source_sha256_scope": "user_declared_metadata"},
            {"source_version": "v2", "source_sha256": "a" * 63, "source_sha256_scope": "user_declared_metadata"},
            {"source_version": "v2", "source_sha256": "g" * 64, "source_sha256_scope": "user_declared_metadata"},
            {"source_version": "v2", "source_sha256": HASH, "source_sha256_scope": "other_scope"},
            {"source_version": "x" * 301, "source_sha256": HASH, "source_sha256_scope": "user_declared_metadata"},
            {"source_fingerprint": {"source_version": "v2"}},
        ]
        for extra in invalid:
            with self.subTest(extra=extra):
                response = self.client.post(f'/api/projects/{self.project["id"]}/reading-notes',
                                            json=self.base | extra)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.reading_notes(), [])

    def test_old_notes_without_fingerprint_stay_compatible(self):
        response = self.client.post(f'/api/projects/{self.project["id"]}/reading-notes', json=self.base)
        self.assertEqual(response.status_code, 200, response.text)
        note = response.json()["reading_notes"][-1]
        self.assertNotIn("source_fingerprint", note)
        self.assertNotIn("source_version", note)
        self.assertNotIn("source_sha256", note)

    def test_direction_snapshot_and_plan_show_declared_or_unregistered_fingerprint(self):
        # Declared case.
        item = project()
        source_id = add_search(item)
        note = {"id": "note-fingerprint", "run_id": "run1", "source_id": "S001", "level": "abstract_checked",
                "content": "摘要只支持特定条件。", "locator": "Abstract",
                "checked_at": "2026-10-16T03:00:00+00:00", "confirmed": True, "origin": "user_attested",
                "source_version": "arXiv v3", "source_sha256": "b" * 64,
                "source_sha256_scope": "user_declared_fulltext_file",
                "source_fingerprint": {"source_version": "arXiv v3", "source_sha256": "b" * 64,
                                       "source_sha256_scope": "user_declared_fulltext_file",
                                       "caveat": workflow.SOURCE_FINGERPRINT_CAVEAT}}
        item["reading_notes"] = [note]
        proposal = directions()
        proposal["topics"][0]["evidence"] = [{"source_id": source_id, "relation": "supports",
                                              "claim": "摘要仅支持特定条件。", "note_id": "note-fingerprint"}]
        normalized = workflow.validate_payload(proposal, item)
        snapshot = normalized["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(snapshot["source_version"], "arXiv v3")
        self.assertEqual(snapshot["source_sha256"], "b" * 64)
        self.assertEqual(snapshot["source_sha256_scope"], "user_declared_fulltext_file")
        self.assertIn("程序没有读取/认证原文", snapshot["source_fingerprint_caveat"])
        workflow.apply_result(item, proposal, call())
        engine.select_topic(item, item["direction_map"]["topics"][0]["id"], "按声明依据选择。")
        plan = engine.render_plan(item)
        self.assertIn("用户声明原文版本：arXiv v3", plan)
        self.assertIn("b" * 64, plan)
        self.assertIn("程序没有读取/认证原文", plan)
        exported = json.loads(json.dumps(item, ensure_ascii=False))
        exported_snapshot = exported["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(exported_snapshot["source_sha256"], "b" * 64)
        # Missing fingerprint case remains compatible and is marked as unregistered.
        legacy = project()
        legacy_source = add_search(legacy)
        legacy["reading_notes"] = [{"id": "note-legacy", "run_id": "run1", "source_id": "S001",
                                    "level": "abstract_checked", "content": "旧笔记只读摘要。", "locator": "Abstract",
                                    "checked_at": "2026-10-16T03:00:00+00:00", "confirmed": True,
                                    "origin": "user_attested"}]
        legacy_proposal = directions()
        legacy_proposal["topics"][0]["evidence"] = [{"source_id": legacy_source, "relation": "supports",
                                                     "claim": "旧笔记支持有限。", "note_id": "note-legacy"}]
        workflow.apply_result(legacy, legacy_proposal, call("call-legacy"))
        engine.select_topic(legacy, legacy["direction_map"]["topics"][0]["id"], "旧依据。")
        legacy_plan = engine.render_plan(legacy)
        self.assertIn("未登记原文版本/指纹", legacy_plan)
        self.assertNotIn("用户声明原文版本", legacy_plan)


class ReadingFingerprintUnitTest(unittest.TestCase):
    def test_declared_fingerprint_helper_rejects_incoherent_triples(self):
        self.assertIsNone(workflow._declared_source_fingerprint({"source_version": "v2"}))
        self.assertIsNone(workflow._declared_source_fingerprint(
            {"source_version": "v2", "source_sha256": "A" * 64, "source_sha256_scope": "user_declared_metadata"}))
        valid = workflow._declared_source_fingerprint(
            {"source_version": " v2 ", "source_sha256": HASH, "source_sha256_scope": "user_declared_metadata"})
        self.assertEqual(valid["source_version"], "v2")
        self.assertEqual(valid["source_sha256"], HASH)
        self.assertIn("程序没有读取/认证原文", valid["source_fingerprint_caveat"])


if __name__ == "__main__":
    unittest.main()
