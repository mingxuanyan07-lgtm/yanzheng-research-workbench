"""Synthetic legacy-map API lifecycle; use scripts/run_tests.py for import isolation."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
# app creates a Store on import. Refuse accidental discovery against a real DB.
_import_db = os.environ.get("YANZHENG_DB_PATH")
if not _import_db or not Path(_import_db).resolve().is_relative_to((BASE / "tests" / ".tmp").resolve()):
    raise RuntimeError("Run through scripts/run_tests.py with its isolated import database.")

from app import create_app
from fastapi.testclient import TestClient
from test_direction_legacy import legacy_project


class SavedDirectionAPITest(unittest.TestCase):
    def setUp(self):
        scratch = BASE / "tests" / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="legacy-api-", dir=scratch)
        self.application = create_app(
            Path(self.directory.name) / "synthetic.db",
            model_client=object(), lean_client=object(), evidence_client=object(),
        )
        self.client = TestClient(self.application)
        self.item = legacy_project()
        self.application.state.store.create(copy.deepcopy(self.item))
        self.url = f'/api/projects/{self.item["id"]}'

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_read_export_select_reopen_retains_raw_evidence(self):
        original_map = copy.deepcopy(self.item["direction_map"])
        original_evidence = copy.deepcopy(original_map["topics"][0]["evidence"])
        for suffix in ("", "/export"):
            response = self.client.get(self.url + suffix)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["direction_map"], original_map)
        self.assertEqual(self.application.state.store.get(self.item["id"]), self.item)

        selected = self.client.post(self.url + "/select", json={"topic_id": original_map["topics"][0]["id"]})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(selected.json()["direction_map"]["topics"][0]["evidence"], original_evidence)
        plan = self.client.get(self.url + "/plan")
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertIn("历史关联：未绑定阅读笔记", plan.text)
        before_reads = self.application.state.store.get(self.item["id"])
        exported = self.client.get(self.url + "/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(exported.json()["direction_map"], before_reads["direction_map"])
        self.assertEqual(self.application.state.store.get(self.item["id"]), before_reads)
        reopened = self.client.post(self.url + "/reopen")
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["direction_map"]["topics"][0]["evidence"], original_evidence)
        self.assertEqual(reopened.json()["direction_map"]["stage"], "directions_ready")


if __name__ == "__main__":
    unittest.main()
