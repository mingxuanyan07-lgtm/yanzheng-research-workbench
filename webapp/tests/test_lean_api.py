"""Lean API persistence and concurrency, using only temporary databases.

The runner is an offline adapter-record fixture.  These tests never compile
Lean, contact a model, or initialize the application's ordinary database.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
SCRATCH = BASE / "tests" / ".tmp"
SCRATCH.mkdir(parents=True, exist_ok=True)
# app.py also creates a module-level app.  Shield its first import even when
# this test is executed directly rather than through scripts/run_tests.py.
with tempfile.TemporaryDirectory(prefix="lean-api-import-", dir=SCRATCH) as import_folder:
    with patch.dict(os.environ, {"YANZHENG_DB_PATH": str(Path(import_folder) / "import-only.db")}):
        from app import create_app

from fastapi.testclient import TestClient
import engine
import lean_api
import lean_service
import theory


class NoModel:
    def __init__(self):
        self.calls = 0

    def status(self):
        return {"configured": False, "state": "unconfigured", "model": None,
                "provider": "offline-test", "last_call": None}

    def complete(self, messages):
        self.calls += 1
        raise AssertionError("Lean API must not request a model")


def problem_card(**changes):
    result = {"title": "自然数加零", "statement": "对任意自然数 n，n + 0 = n。",
              "domain": "自然数", "definitions": ["自然数采用 Lean 的 Nat 类型。"],
              "assumptions": [], "target": "检查准确的形式命题 n + 0 = n。"}
    result.update(changes)
    return result


def adapter_record(case_id="nat_add_zero", status="passed", error_code=None):
    """An explicitly offline transport fixture, never claimed as real Lean."""
    case = next(item for item in lean_service.list_cases() if item["case_id"] == case_id)
    result = {
        "schema_version": "1.0", "record_type": "research_lean_verification",
        "adapter_version": "lean-fixed-v1", "id": "fixture-" + uuid.uuid4().hex,
        "case_id": case_id, "status": status, "created_at": "2026-10-16T01:00:00+00:00",
        "finished_at": "2026-10-16T01:00:01+00:00", "duration_ms": 1.0,
        "formal_statement": {"target": case["target"], "source": case["statement"],
                             "elaborated": "offline fixture; no Lean executed"},
        "source": {"sha256": hashlib.sha256(b"offline-api-fixture").hexdigest(), "files": []},
        "environment": {"toolchain": "leanprover/lean4:v4.33.1", "lean_version": "offline fixture",
                        "lean_commit": "offline fixture", "dependencies": []},
        "diagnostics": [] if status == "passed" else [{"severity": "error", "message": "offline failure fixture"}],
        "trust": {"audit_completed": status not in ("timeout", "unavailable"), "axioms": [],
                  "sorry_axioms": ["sorryAx"] if status == "incomplete" else [],
                  "unapproved_axioms": [], "extra_dependencies": [],
                  "policy_id": "lean-core-standard-axioms-v1", "passed": status == "passed"},
        "execution": {"compiler_started": status != "unavailable", "exit_code": 0 if status == "passed" else None,
                      "resource_limits": {"is_general_security_sandbox": False}},
        "provenance": {"external_model_api_called": False, "arbitrary_code_accepted": False,
                       "natural_language_equivalence_verified": False,
                       "application_state_modified": False, "test_fixture": True},
        "limitations": ["Offline API test fixture; no Lean compiler or model was called."],
    }
    if error_code or status != "passed":
        result["error"] = {"code": error_code or {"error": "lean_error", "incomplete": "sorry_dependency",
                                                   "timeout": "wall_timeout", "unavailable": "toolchain_missing"}[status]}
    return result


class RecordingRunner:
    def __init__(self):
        self.calls = []
        self.results = []
        self.callback = None
        self.status = "passed"
        self.error_code = None

    def __call__(self, request):
        self.calls.append(copy.deepcopy(request))
        if self.callback:
            self.callback()
        result = adapter_record(request["case_id"], self.status, self.error_code)
        self.results.append(copy.deepcopy(result))
        return result


class LeanApiTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="lean-api-", dir=SCRATCH))
        self.database = self.directory / "lean-api.db"
        self.runner = RecordingRunner()
        self.model = NoModel()
        self.environment_patch = patch.object(lean_service, "environment", return_value={
            "available": False, "toolchain": "leanprover/lean4:v4.33.1", "error_code": "offline_api_fixture",
            "automatic_downloads": False, "mathlib_used": False})
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.application = create_app(self.database, model_client=self.model, lean_client=self.runner)
        self.client = TestClient(self.application)

    def tearDown(self):
        self.client.close()
        self.assertTrue(self.directory.resolve().is_relative_to(SCRATCH.resolve()))
        shutil.rmtree(self.directory)

    def create(self, with_card=True):
        response = self.client.post("/api/projects", json={"initial_idea": "研究自然数短引理的准确形式陈述", "goal": "theory"})
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()
        if with_card:
            response = self.client.post(f'/api/projects/{item["id"]}/theory/card',
                                        json={"card": problem_card(), "expected_version": 0})
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()
        return item

    def saved(self, item):
        return self.application.state.store.get(item["id"])

    def path(self, item):
        return f'/api/projects/{item["id"]}/lean/runs'

    def execute(self, item, case_id="nat_add_zero", client=None):
        return (client or self.client).post(self.path(item), json={"case_id": case_id})

    def assert_entry(self, entry, item, *, status="passed"):
        self.assertEqual(set(entry), {"id", "verification", "context", "association_status", "context_matches_current"})
        self.assertEqual(entry["id"], entry["verification"]["id"])
        self.assertEqual(entry["verification"]["status"], status)
        self.assertEqual(entry["context"]["project_id"], item["id"])
        self.assertEqual(entry["association_status"], "context_only_not_semantically_verified")
        self.assertFalse(entry["verification"]["provenance"]["natural_language_equivalence_verified"])

    def test_status_cases_and_empty_history_are_read_only_and_never_execute(self):
        item = self.create()
        before = self.saved(item)
        status = self.client.get("/api/lean/status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["environment"]["error_code"], "offline_api_fixture")
        cases = self.client.get("/api/lean/cases")
        self.assertEqual(cases.status_code, 200, cases.text)
        self.assertIn("nat_add_zero", {case["case_id"] for case in cases.json()["cases"]})
        response = self.client.get(self.path(item))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"runs": []})
        self.assertEqual(self.saved(item), before)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.model.calls, 0)

    def test_strict_request_validation_and_unknown_resources_do_not_execute(self):
        item = self.create()
        before = self.saved(item)
        for body in ({}, {"case_id": None}, {"case_id": True}, {"case_id": 1},
                     {"case_id": "unknown_case"}, {"case_id": "../Proof.lean"},
                     {"case_id": "nat_add_zero", "source": "by sorry"},
                     {"case_id": "nat_add_zero", "path": "D:/unreviewed.lean"},
                     {"case_id": "nat_add_zero", "url": "https://untrusted.invalid/Proof.lean"},
                     {"case_id": "nat_add_zero", "timeout_seconds": 1000}):
            with self.subTest(body=body):
                response = self.client.post(self.path(item), json=body)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/projects/missing/lean/runs").status_code, 404)
        self.assertEqual(self.client.post("/api/projects/missing/lean/runs", json={"case_id": "nat_add_zero"}).status_code, 404)
        self.assertEqual(self.client.get(self.path(item) + "/missing/export?format=json").status_code, 404)
        self.assertEqual(self.saved(item), before)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.model.calls, 0)

    def test_record_persists_and_exports_after_restart_without_proving_theory(self):
        item = self.create()
        self.application.state.store.update(item["id"], engine.generate_directions)
        generated = self.saved(item)
        selected_id = generated["direction_map"]["topics"][0]["id"]
        self.application.state.store.update(item["id"], lambda current: engine.select_topic(current, selected_id, "测试人工选择。"))
        before = self.saved(item)
        response = self.execute(item)
        self.assertEqual(response.status_code, 200, response.text)
        entry = response.json()["entry"]
        self.assert_entry(entry, item)
        self.assertTrue(entry["context_matches_current"])
        self.assertEqual(entry["context"], lean_api.card_context(before))
        saved = self.saved(item)
        self.assertEqual(saved["direction_map"], before["direction_map"])
        self.assertEqual(saved["theory"], before["theory"])
        self.assertEqual(saved["updated_at"], before["updated_at"])
        self.assertEqual(saved["history"], before["history"])
        self.assertEqual(theory.workspace(saved)["proof_status"], "not_formally_verified")
        self.assertEqual(saved["lean_attempts"][-1]["state"], "completed")
        self.assertEqual(saved["lean_attempts"][-1]["verification_id"], entry["id"])
        self.assertEqual(saved["lean_runs"], [entry])
        with TestClient(create_app(self.database, model_client=self.model, lean_client=self.runner)) as fresh:
            self.assertEqual(fresh.get(self.path(item)).json(), {"runs": [entry]})
            exported = fresh.get(self.path(item) + f'/{entry["id"]}/export?format=json')
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual(exported.json(), entry)
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertEqual(fresh.get(f'/api/projects/{item["id"]}').json()["lean_runs"], [entry])
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.model.calls, 0)

    def test_all_actual_adapter_statuses_are_http_200_and_kept_for_inspection(self):
        for status in ("passed", "error", "incomplete", "timeout", "unavailable"):
            with self.subTest(status=status):
                item = self.create()
                before = self.saved(item)
                self.runner.status = status
                response = self.execute(item)
                self.assertEqual(response.status_code, 200, response.text)
                entry = response.json()["entry"]
                self.assert_entry(entry, item, status=status)
                self.assertEqual(self.client.get(self.path(item)).json()["runs"], [entry])
                self.assertEqual(self.saved(item)["theory"], before["theory"])
                self.assertEqual(self.saved(item)["updated_at"], before["updated_at"])
                if status != "passed":
                    self.assertFalse(entry["verification"]["trust"]["passed"])
                    self.assertTrue(entry["verification"]["error"]["code"])
        self.assertEqual(len(self.runner.calls), 5)
        self.assertEqual(self.model.calls, 0)

    def test_short_window_duplicate_survives_restart_and_never_reexecutes(self):
        item = self.create()
        first = self.execute(item)
        self.assertEqual(first.status_code, 200, first.text)
        before = self.saved(item)
        second = self.execute(item)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), first.json())
        with TestClient(create_app(self.database, model_client=self.model, lean_client=self.runner)) as fresh:
            third = self.execute(item, client=fresh)
            self.assertEqual(third.status_code, 200, third.text)
            self.assertEqual(third.json(), first.json())
        self.assertEqual(self.saved(item), before)
        self.assertEqual(len(self.runner.calls), 1)

    def test_card_version_and_hash_both_limit_deduplication_and_dynamic_context(self):
        item = self.create()
        first = self.execute(item).json()["entry"]
        self.application.state.store.update(item["id"], lambda current: theory.save_card(
            current, problem_card(title="修改后的自然数问题"), expected_version=1))
        changed = self.saved(item)
        second_response = self.execute(item)
        self.assertEqual(second_response.status_code, 200, second_response.text)
        second = second_response.json()["entry"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["context"]["card_version"], 2)
        # A restored/imported card could preserve its version: its hash must
        # still prevent accidental reuse of a run for different input.
        self.application.state.store.update(item["id"], lambda current: current["theory"]["card"].update(target="独立审阅另一个目标。"))
        before_get = self.saved(item)
        runs = self.client.get(self.path(item)).json()["runs"]
        self.assertEqual([run["context_matches_current"] for run in runs], [False, False])
        self.assertEqual(self.saved(item), before_get)
        exported = self.client.get(self.path(item) + f'/{second["id"]}/export?format=json')
        self.assertFalse(exported.json()["context_matches_current"])
        third = self.execute(item).json()["entry"]
        self.assertEqual(third["context"]["card_version"], changed["theory"]["card_version"])
        self.assertNotEqual(third["context"]["card_sha256"], second["context"]["card_sha256"])
        self.assertTrue(third["context_matches_current"])
        self.assertEqual(len(self.runner.calls), 3)

    def test_without_card_is_context_only_with_null_matching_status(self):
        item = self.create(with_card=False)
        before = self.saved(item)
        response = self.execute(item)
        self.assertEqual(response.status_code, 200, response.text)
        entry = response.json()["entry"]
        self.assert_entry(entry, item)
        self.assertEqual(entry["context"], {"project_id": item["id"], "card_version": None, "card_sha256": None})
        self.assertIsNone(entry["context_matches_current"])
        self.assertEqual(self.saved(item).get("theory"), before.get("theory"))
        self.assertIsNone(self.client.get(self.path(item)).json()["runs"][0]["context_matches_current"])

    def test_card_changed_during_execution_is_preserved_and_result_keeps_start_context(self):
        item = self.create()
        original_context = lean_api.card_context(item)
        updated = problem_card(statement="审阅运行期间由用户修改的另一个命题。")
        self.runner.callback = lambda: self.application.state.store.update(item["id"], lambda current:
            theory.save_card(current, updated, expected_version=1))
        response = self.execute(item)
        self.assertEqual(response.status_code, 200, response.text)
        entry = response.json()["entry"]
        self.assertEqual(entry["context"], original_context)
        self.assertFalse(entry["context_matches_current"])
        saved = self.saved(item)
        self.assertEqual(saved["theory"]["card"], updated)
        self.assertEqual(saved["theory"]["card_version"], 2)
        self.assertEqual(self.model.calls, 0)

    def test_execution_in_progress_rejects_duplicate_and_other_case_without_retry(self):
        item = self.create()
        other_project = self.create()
        entered, release = threading.Event(), threading.Event()
        responses, failures = [], []

        def block():
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release its offline runner")

        def execute_first():
            try:
                responses.append(self.execute(item))
            except BaseException as error:
                failures.append(error)

        self.runner.callback = block
        thread = threading.Thread(target=execute_first, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(3), "First offline runner did not start")
            running = self.saved(item)["lean_attempts"][-1]
            self.assertEqual(running["state"], "running")
            self.assertEqual(self.client.get(self.path(item)).json(), {"runs": []})
            for project, case_id in ((item, "nat_add_zero"), (item, "implication_identity"),
                                     (other_project, "nat_add_zero")):
                response = self.execute(project, case_id)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["code"], "lean_busy")
            self.assertEqual(len(self.runner.calls), 1)
        finally:
            release.set()
            thread.join(6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(responses[0].status_code, 200, responses[0].text)
        self.assertEqual(len(self.saved(item)["lean_runs"]), 1)
        self.assertEqual(self.saved(other_project).get("lean_runs", []), [])
        self.assertEqual(len(self.runner.calls), 1)

    def test_active_and_expired_persistent_reservations_never_fabricate_results(self):
        item = self.create()
        initial = self.saved(item)
        attempt = {"id": "prior-process-fixture", "owner": "prior-process", "state": "running",
                   "case_id": "nat_add_zero", "context": lean_api.card_context(item),
                   "reserved_at": lean_api.now(), "lease_expires_epoch": time.time() + 90,
                   "verification_id": None}
        self.application.state.store.update(item["id"], lambda current: current.update(lean_attempts=[attempt]))
        with TestClient(create_app(self.database, model_client=self.model, lean_client=self.runner)) as fresh:
            self.assertEqual(fresh.get(self.path(item)).json(), {"runs": []})
            self.assertEqual(self.execute(item, client=fresh).status_code, 409)
            self.assertEqual(self.runner.calls, [])
            self.application.state.store.update(item["id"], lambda current:
                current["lean_attempts"][0].update(lease_expires_epoch=time.time() - 1))
            self.assertEqual(fresh.get(self.path(item)).json(), {"runs": []})
            self.assertEqual(self.saved(item)["lean_attempts"][0]["state"], "running")
            response = self.execute(item, "implication_identity", client=fresh)
            self.assertEqual(response.status_code, 200, response.text)
        saved = self.saved(item)
        self.assertEqual(saved["lean_attempts"][0]["state"], "interrupted_result_unknown")
        self.assertIsNone(saved["lean_attempts"][0]["verification_id"])
        self.assertEqual(len(saved["lean_runs"]), 1)
        self.assertEqual(saved["lean_runs"][0]["verification"]["case_id"], "implication_identity")
        self.assertEqual(saved["updated_at"], initial["updated_at"])
        self.assertEqual(saved["history"], initial["history"])
        self.assertEqual(len(self.runner.calls), 1)

    def test_adapter_reported_unavailable_busy_is_preserved_as_http_200(self):
        item = self.create()
        self.runner.status = "unavailable"
        self.runner.error_code = "busy"
        response = self.execute(item)
        self.assertEqual(response.status_code, 200, response.text)
        entry = response.json()["entry"]
        self.assert_entry(entry, item, status="unavailable")
        self.assertEqual(entry["verification"]["error"]["code"], "busy")
        self.assertFalse(entry["verification"]["execution"]["compiler_started"])
        self.assertEqual(self.saved(item)["lean_runs"], [entry])
        self.assertEqual(self.saved(item)["lean_attempts"][-1]["state"], "completed")
        self.assertEqual(len(self.runner.calls), 1)

    def test_short_window_is_bounded_and_a_later_explicit_request_can_run_again(self):
        item = self.create()
        first = self.execute(item).json()["entry"]
        self.application.state.store.update(item["id"], lambda current: current["lean_attempts"][-1].update(
            finished_epoch=time.time() - lean_api.DEDUPE_SECONDS - 1))
        response = self.execute(item)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(response.json()["entry"]["id"], first["id"])
        self.assertEqual(len(self.saved(item)["lean_runs"]), 2)
        self.assertEqual(len(self.runner.calls), 2)

    def test_unexpected_adapter_failure_keeps_attempt_without_inventing_verification(self):
        item = self.create()
        before = self.saved(item)

        def fail():
            raise RuntimeError("offline runner transport failure")

        self.runner.callback = fail
        response = self.execute(item)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["code"], "lean_result_unavailable")
        saved = self.saved(item)
        self.assertEqual(saved.get("lean_runs", []), [])
        self.assertEqual(saved["lean_attempts"][-1]["state"], "failed_result_unknown")
        self.assertIsNone(saved["lean_attempts"][-1]["verification_id"])
        self.assertEqual(saved["theory"], before["theory"])
        self.assertEqual(saved["updated_at"], before["updated_at"])
        self.assertEqual(self.client.get(self.path(item)).json(), {"runs": []})
        self.assertEqual(len(self.runner.calls), 1)

    def test_save_failure_does_not_return_a_durable_entry_or_automatically_rerun(self):
        item = self.create()
        store = self.application.state.store
        real_update = store.update
        updates = []

        def fail_commit(project_id, mutate):
            updates.append(project_id)
            if len(updates) == 2:
                raise OSError("offline simulated final persistence failure")
            return real_update(project_id, mutate)

        with patch.object(store, "update", side_effect=fail_commit):
            response = self.execute(item)
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual(body["code"], "lean_save_failed")
        self.assertFalse(body["persisted"])
        self.assertNotIn("entry", body)
        self.assert_entry(body["unsaved_entry"], item)
        self.assertEqual(body["unsaved_entry"]["context"], lean_api.card_context(item))
        self.assertEqual(body["unsaved_entry"]["verification"], self.runner.results[0])
        saved = self.saved(item)
        self.assertEqual(saved.get("lean_runs", []), [])
        self.assertEqual(saved["lean_attempts"][-1]["state"], "running")
        self.assertIsNone(saved["lean_attempts"][-1]["verification_id"])
        self.assertEqual(body["reservation_id"], saved["lean_attempts"][-1]["id"])
        self.assertEqual(self.client.get(self.path(item)).json(), {"runs": []})
        self.assertEqual(self.execute(item).status_code, 409)
        self.assertEqual(len(self.runner.calls), 1)

    def test_adapter_failure_and_failed_error_accounting_return_unknown_503_without_fake_record(self):
        item = self.create()
        store = self.application.state.store
        real_update = store.update
        updates = []

        def fail_runner():
            raise RuntimeError("offline runner crash")

        def fail_accounting(project_id, mutate):
            updates.append(project_id)
            if len(updates) == 2:
                raise OSError("offline error-accounting commit failure")
            return real_update(project_id, mutate)

        self.runner.callback = fail_runner
        with patch.object(store, "update", side_effect=fail_accounting):
            response = self.execute(item)
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual(body["code"], "lean_save_failed")
        self.assertFalse(body["persisted"])
        self.assertIsNone(body["unsaved_entry"])
        saved = self.saved(item)
        self.assertEqual(saved.get("lean_runs", []), [])
        self.assertEqual(saved["lean_attempts"][-1]["state"], "running")
        self.assertEqual(saved["lean_attempts"][-1]["id"], body["reservation_id"])
        self.assertEqual(self.runner.results, [])
        self.assertEqual(len(self.runner.calls), 1)

    def test_lean_record_during_offline_model_proposal_does_not_create_false_input_conflict(self):
        item = self.create()
        lean_responses = []
        proposal = {
            "revised_card": problem_card(), "revision_reason": "保留原命题，单独组织待审阅路线。",
            "proof_routes": [{"id": "nat-route", "title": "自然数规则路线", "steps": ["核对加法定义与量词。"],
                              "obligations": [{"id": "add-zero", "statement": "核对自然数加零规则。", "depends_on": []}],
                              "gaps": [], "evidence": []}], "search_leads": []}

        def complete(messages):
            self.model.calls += 1
            lean_responses.append(self.execute(item))
            stamp = engine.now()
            return proposal, {"id": "offline-model-fixture", "provider": "offline-test", "model": "fixture",
                              "requested_model": "fixture", "status": "success", "started_at": stamp,
                              "finished_at": stamp, "duration_ms": 1, "request_id": "offline-fixture",
                              "attempts": 1, "usage": None, "prompt_version": theory.PROMPT_VERSION}

        with patch.object(self.model, "complete", side_effect=complete), \
                patch.object(self.model, "record_result", create=True):
            response = self.client.post(f'/api/projects/{item["id"]}/theory/propose', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(lean_responses[0].status_code, 200, lean_responses[0].text)
        saved = self.saved(item)
        self.assertIsNotNone(saved["theory"]["pending_proposal"])
        self.assertEqual(saved["theory"]["card"], problem_card())
        self.assertEqual(saved["model_calls"][-1]["application_status"], "pending_review")
        self.assertEqual(len(saved["lean_runs"]), 1)
        self.assertEqual(saved["theory"]["proof_status"], "not_formally_verified")
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.model.calls, 1)  # A local callback fixture, no remote request.


if __name__ == "__main__":
    unittest.main()
