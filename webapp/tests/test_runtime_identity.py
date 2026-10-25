"""Public source identities distinguish stale services without reading user data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
from app import create_app
from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location("workbench_doctor", engine.ROOT / "scripts/check_workbench.py")
doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doctor)


class RuntimeIdentityTest(unittest.TestCase):
    def test_running_process_captures_build_and_does_not_relabel_after_source_change(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(engine, "runtime_build_identity", return_value={"id": "at-start", "scheme": "fixture"}):
                application = create_app(Path(folder) / "isolated.db")
            with TestClient(application) as client, patch.object(engine, "runtime_build_identity", return_value={"id": "new-source"}):
                self.assertEqual(client.get("/api/status").json()["build"]["id"], "at-start")

    def test_runtime_changes_update_identity_but_credentials_and_data_do_not(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "static").mkdir()
            (base / "app.py").write_text("public source", encoding="utf-8")
            (base / "static/app.js").write_text("public UI", encoding="utf-8")
            before = engine.runtime_build_identity(base)
            (base / ".env").write_text("private fixture value", encoding="utf-8")
            (base / "research.db").write_bytes(b"private research fixture")
            self.assertEqual(before, engine.runtime_build_identity(base))
            (base / "static/app.js").write_text("updated UI", encoding="utf-8")
            self.assertNotEqual(before, engine.runtime_build_identity(base))

    def response(self, payload):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, *_): return json.dumps(payload).encode()
        return Response()

    def test_doctor_distinguishes_old_current_and_unrecognized_without_exposing_status(self):
        for identity, expected in ((None, "restart_required"), ({"id": "old"}, "restart_required"),
                                    ({"id": "local"}, "current")):
            payload = {"storage": "sqlite", "mode": "demo", "private_fixture": "must not be returned"}
            if identity is not None:
                payload["build"] = identity
            with patch.object(doctor, "build_opener") as opener:
                opener.return_value.open.return_value = self.response(payload)
                result = doctor.running_service(8097, {"id": "local"})
            self.assertEqual(result["state"], expected)
            self.assertNotIn("private_fixture", json.dumps(result))
        with patch.object(doctor, "build_opener") as opener:
            opener.return_value.open.return_value = self.response({"other_service": True})
            self.assertEqual(doctor.running_service(8097, {"id": "local"})["state"], "unrecognized")
