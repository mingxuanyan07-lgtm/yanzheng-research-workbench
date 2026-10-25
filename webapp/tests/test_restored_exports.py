"""Standalone exports retain imported-record identity outside the workspace UI."""
import csv
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from app import create_app
import experiments


class RestoredExportTest(unittest.TestCase):
    def test_markdown_plan_and_decision_package_carry_external_backup_scope(self):
        with tempfile.TemporaryDirectory() as folder, TestClient(create_app(Path(folder) / "qa.db")) as client:
            project = client.post("/api/projects", json={"initial_idea": "随机低秩逼近"}).json()
            base = "/api/projects/" + project["id"]
            client.post(base + "/select", json={"topic_id": project["direction_map"]["topics"][0]["id"]})
            package = client.get("/api/backups/export").content
            with TestClient(create_app(Path(folder) / "restored.db")) as restored:
                response = restored.post("/api/backups/restore", content=package, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 200, response.text)
                for route in ("/plan", "/decision-package"):
                    text = restored.get(base + route).text
                    self.assertIn("从外部备份恢复", text)
                    self.assertIn("不代表本次新执行", text)

    def test_csv_preserves_historical_numbers_and_labels_imported_execution(self):
        record = {"id": "historical", "rows": [{"dataset_id": "A", "relative_error": 0.25}],
                  "backup_restore": {"restored_at": "2026-10-23T10:00:00+00:00"}}
        rows = list(csv.DictReader(io.StringIO(experiments.export_csv(record))))
        self.assertEqual(rows[0]["relative_error"], "0.25")
        self.assertEqual(rows[0]["record_origin"], "external_backup_snapshot")
        self.assertEqual(rows[0]["revalidated_on_this_machine"], "False")
        record.pop("backup_restore")
        self.assertNotIn("external_backup_snapshot", experiments.export_csv(record))
