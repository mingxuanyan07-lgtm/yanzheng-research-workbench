"""Contract and failure boundaries for the optional fixed-case Lean adapter.

These tests import neither the FastAPI application nor its real database.  No
model endpoint is called, and the tests never update an elan toolchain.
"""

import copy
import hashlib
import io
import json
import math
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import lean_service
import lean_worker


def registered_request():
    return {"case_id": lean_service.list_cases()[0]["case_id"]}


def clean_audit():
    case = lean_worker.load_cases()["nat_add_zero"]
    return {"schema_version": "1.0", "target": case["target"],
            "elaborated_type": "∀ (n : Nat), Eq.{1} Nat (Nat.add n Nat.zero) n",
            "declaration_kind": "theorem", "audit_complete": True,
            "axioms": [], "dependencies": [case["target"]],
            "unsafe_dependencies": [], "partial_dependencies": [],
            "extra_trust_dependencies": [], "missing_dependencies": []}


def passing_payload():
    """A transport fixture based on reviewed bytes, with no Lean process call."""
    case = lean_worker.load_cases()["nat_add_zero"]
    elaborated = clean_audit()["elaborated_type"]
    source = ((lean_worker.PROJECT / "Audit.lean").read_text(encoding="utf-8").rstrip() + "\n\n"
              + (lean_worker.PROJECT / case["file"]).read_text(encoding="utf-8").rstrip()
              + "\n\n#yanzheng_audit " + case["target"] + "\n")
    return {
        "worker_protocol": "lean-fixed-v1", "case_id": case["case_id"], "status": "passed",
        "formal_statement": {"source": case["statement"], "target": case["target"],
                             "elaborated": elaborated,
                             "elaborated_sha256": hashlib.sha256(elaborated.encode("utf-8")).hexdigest()},
        "source": {"sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                   "files": lean_worker.reviewed_sources(case), "reviewed_sources_match": True},
        "environment": {"available": True, "toolchain": lean_worker.TOOLCHAIN,
                        "lean_version": "fixture: no compiler invoked", "lean_commit": lean_worker.LEAN_COMMIT,
                        "dependencies": [{"name": "Lean core / bundled Std", "version": "4.33.1"}]},
        "diagnostics": [], "duration_ms": 1.0, "trust": lean_worker._trust(clean_audit()),
        "execution": {"compiler_started": True, "exit_code": 0, "timed_out": False,
                      "output_limited": False, "limiter_applied": True,
                      "resource_limits": {"wall_seconds": 10.0, "is_general_security_sandbox": False}},
    }


class LeanWorkerResultContractTest(unittest.TestCase):
    def test_complete_passing_transport_gets_an_independent_immutable_run_record(self):
        payload = passing_payload()
        original = copy.deepcopy(payload)
        request = {"case_id": "nat_add_zero"}
        with patch.object(lean_service, "_invoke_worker", return_value=payload) as invoke:
            first = lean_service.run(request)
            second = lean_service.run(request)
        self.assertEqual(first["status"], "passed", first.get("error"))
        self.assertEqual(first["formal_statement"], payload["formal_statement"])
        self.assertEqual(first["record_type"], "research_lean_verification")
        self.assertNotEqual(first["id"], second["id"])
        self.assertFalse(first["provenance"]["application_state_modified"])
        self.assertFalse(first["provenance"]["external_model_api_called"])
        self.assertFalse(first["provenance"]["arbitrary_code_accepted"])
        self.assertFalse(first["provenance"]["natural_language_equivalence_verified"])
        self.assertEqual(payload, original)
        first["trust"]["axioms"].append("should-not-change-worker")
        self.assertEqual(payload, original)
        self.assertEqual(invoke.call_count, 2)
        json.dumps(second, allow_nan=False)

    def test_exit_zero_without_trust_statement_and_source_audit_cannot_pass(self):
        payload = {"worker_protocol": "lean-fixed-v1", "case_id": "nat_add_zero",
                   "status": "passed", "execution": {"exit_code": 0}}
        with patch.object(lean_service, "_invoke_worker", return_value=payload):
            record = lean_service.run({"case_id": "nat_add_zero"})
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error"]["code"], "invalid_worker_result")
        self.assertFalse(record["trust"]["passed"])

    def test_claimed_pass_is_rejected_for_tampered_provenance_or_trust(self):
        mutations = [
            ("formal_statement", "target", "Wrong.target"),
            ("formal_statement", "source", "False"),
            ("formal_statement", "elaborated", "False"),
            ("source", "sha256", "0" * 64),
            ("source", "files", []),
            ("environment", "toolchain", "leanprover/lean4:nightly"),
            ("environment", "lean_commit", "0" * 40),
            ("execution", "exit_code", 1),
            ("execution", "exit_code", False),
            ("execution", "compiler_started", False),
            ("execution", "timed_out", True),
            ("execution", "output_limited", True),
            ("execution", "limiter_applied", False),
            ("trust", "audit_completed", False),
            ("trust", "policy_id", "unreviewed-policy"),
            ("trust", "axioms", ["Fixture.unknownAxiom"]),
            ("trust", "sorry_axioms", ["sorryAx"]),
            ("trust", "unapproved_axioms", ["Fixture.assumption"]),
            ("trust", "unsafe_dependencies", ["Fixture.unsafeCode"]),
            ("trust", "partial_dependencies", ["Fixture.partialCode"]),
            ("trust", "extra_dependencies", ["Lean.ofReduceBool"]),
            ("trust", "missing_dependencies", ["Fixture.missing"]),
        ]
        case = lean_worker.load_cases()["nat_add_zero"]
        for section, key, value in mutations:
            payload = passing_payload()
            payload[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                lean_service._validate_worker_result(payload, case)

    def test_busy_run_does_not_launch_another_worker(self):
        lean_service._RUN_LOCK.acquire()
        try:
            with patch.object(lean_service, "_invoke_worker") as invoke:
                result = lean_service.run({"case_id": "nat_add_zero"})
                invoke.assert_not_called()
        finally:
            lean_service._RUN_LOCK.release()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error"]["code"], "busy")
        self.assertFalse(result["execution"]["compiler_started"])
        self.assertFalse(result["trust"]["passed"])

    def test_isolated_worker_command_input_credentials_and_outer_resource_caps(self):
        response = {"timed_out": False, "start_error": None, "limiter_applied": True,
                    "output_limited": False, "exit_code": 0, "limiter": "fixture",
                    "duration_ms": 1.0, "stdout": b'{"worker_protocol":"lean-fixed-v1"}', "stderr": b""}
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "fixture-secret", "PYTHONPATH": "unreviewed"}), \
                patch.object(lean_worker, "_lean_candidate", return_value=None), \
                patch.object(lean_worker, "bounded_process", return_value=response) as invoke:
            lean_service._invoke_worker({"case_id": "nat_add_zero"}, 0.5)
        args, options = invoke.call_args
        self.assertEqual(args[0], [sys.executable, "-I", "-B", str(Path(lean_worker.__file__).resolve())])
        self.assertEqual(Path(options["cwd"]).resolve(), BASE)
        self.assertEqual(json.loads(options["input_bytes"]), {"case_id": "nat_add_zero", "timeout_seconds": 0.5})
        self.assertNotIn("DASHSCOPE_API_KEY", options["env"])
        self.assertNotIn("PYTHONPATH", options["env"])
        self.assertLessEqual(options["timeout_seconds"], 10.5)
        self.assertLessEqual(options["max_bytes"], 1_048_576)

    def test_outer_timeout_start_and_output_failures_never_become_passed(self):
        base = {"timed_out": False, "start_error": None, "limiter_applied": True,
                "output_limited": False, "exit_code": 0, "limiter": "fixture",
                "duration_ms": 1.0, "stdout": b"{}", "stderr": b"", "started": True}
        failures = [
            ({"timed_out": True}, "timeout", "worker_timeout"),
            ({"start_error": "OSError", "started": False, "limiter_applied": False},
             "unavailable", "worker_or_resource_limiter_unavailable"),
            ({"limiter_applied": False}, "unavailable", "worker_or_resource_limiter_unavailable"),
            ({"output_limited": True}, "error", "worker_transport_failed"),
            ({"exit_code": 1}, "error", "worker_transport_failed"),
            ({"stdout": b"not-json"}, "error", "invalid_worker_result"),
            ({"stdout": b"[]"}, "error", "invalid_worker_result"),
        ]
        for changes, status, code in failures:
            response = dict(base, **changes)
            with self.subTest(changes=changes), patch.object(lean_worker, "_lean_candidate", return_value=None), \
                    patch.object(lean_worker, "bounded_process", return_value=response):
                record = lean_service.run({"case_id": "nat_add_zero"})
            self.assertEqual(record["status"], status)
            self.assertEqual(record["error"]["code"], code)
            self.assertFalse(record["trust"]["passed"])
            self.assertFalse(record["trust"]["audit_completed"])
            self.assertFalse(record["provenance"]["external_model_api_called"])


class LeanAuditPolicyTest(unittest.TestCase):
    def test_standard_core_axioms_are_reported_and_explicitly_approved(self):
        audit = clean_audit()
        audit["axioms"] = ["Classical.choice", "propext", "Quot.sound"]
        trust = lean_worker._trust(audit)
        self.assertTrue(trust["audit_completed"])
        self.assertTrue(trust["passed"])
        self.assertEqual(set(trust["axioms"]), set(audit["axioms"]))
        self.assertEqual(trust["unapproved_axioms"], [])
        self.assertTrue(trust["policy_id"])

    def test_sorry_and_unapproved_axiom_fail_even_with_finished_audit(self):
        for axiom in ("sorryAx", "Fixture.unapprovedAssumption"):
            audit = clean_audit()
            audit["axioms"] = [axiom]
            with self.subTest(axiom=axiom):
                trust = lean_worker._trust(audit)
                self.assertFalse(trust["passed"])
                key = "sorry_axioms" if axiom == "sorryAx" else "unapproved_axioms"
                self.assertIn(axiom, trust[key])

    def test_unsafe_partial_extra_or_missing_dependencies_fail_closed(self):
        for key in ("unsafe_dependencies", "partial_dependencies", "extra_trust_dependencies", "missing_dependencies"):
            audit = clean_audit()
            audit[key] = ["Fixture.untrusted"]
            with self.subTest(key=key):
                self.assertFalse(lean_worker._trust(audit)["passed"])
        audit = clean_audit()
        audit["audit_complete"] = False
        self.assertFalse(lean_worker._trust(audit)["passed"])


class LeanRequestContractTest(unittest.TestCase):
    def test_only_a_registered_case_identifier_is_accepted(self):
        request = registered_request()
        original = copy.deepcopy(request)
        self.assertEqual(lean_service.validate_request(request), request)
        self.assertEqual(request, original)
        for invalid in (
            None, [], "case", {}, {"case_id": None}, {"case_id": True},
            {"case_id": 12}, {"case_id": ""}, {"case_id": "unknown_case"},
            {"case_id": "../unreviewed.lean"},
            {"case_id": "D:\\arbitrary\\Proof.lean"},
            {"case_id": "https://untrusted.invalid/Proof.lean"},
        ):
            with self.subTest(request=invalid), self.assertRaises(ValueError):
                lean_service.validate_request(invalid)

    def test_arbitrary_code_paths_urls_and_execution_options_are_rejected(self):
        for key, value in (
            ("source", "axiom anything : False"),
            ("code", "theorem injected : False := by sorry"),
            ("path", "D:\\unreviewed.lean"),
            ("url", "https://untrusted.invalid/Proof.lean"),
            ("command", ["lean", "unreviewed.lean"]),
            ("imports", ["Unreviewed"]),
            ("timeout_seconds", 9999),
            ("toolchain", "leanprover/lean4:nightly"),
        ):
            request = dict(registered_request(), **{key: value})
            with self.subTest(key=key), patch.object(lean_service, "_invoke_worker") as invoke:
                with self.assertRaises(ValueError):
                    lean_service.run(request)
                invoke.assert_not_called()


class LeanWorkerBoundaryTest(unittest.TestCase):
    def test_job_assignment_failure_directly_kills_and_waits_for_unassigned_child(self):
        process = SimpleNamespace(_handle=7, stdin=Mock(closed=False), stdout=Mock(closed=False),
                                  stderr=Mock(closed=False), poll=Mock(return_value=None),
                                  kill=Mock(), wait=Mock(return_value=1))
        job = Mock()
        job.assign.side_effect = OSError("fixture assignment denial")
        with patch.object(lean_worker.os, "name", "nt"), \
                patch.object(lean_worker, "_WindowsJob", return_value=job), \
                patch.object(lean_worker.subprocess, "Popen", return_value=process):
            result = lean_worker.bounded_process(["fixed-test-process"], cwd=BASE, env={},
                                                 input_bytes=b"must not be sent", timeout_seconds=0.1)
        self.assertTrue(result["started"])
        self.assertFalse(result["limiter_applied"])
        self.assertEqual(result["start_error"], "OSError")
        process.kill.assert_called_once()
        process.wait.assert_called()
        process.stdin.write.assert_not_called()
        job.close.assert_called_once()

    def test_windows_resource_limiter_failure_does_not_start_an_unlimited_process(self):
        with patch.object(lean_worker.os, "name", "nt"), \
                patch.object(lean_worker, "_WindowsJob", side_effect=OSError("fixture denial")), \
                patch.object(lean_worker.subprocess, "Popen") as invoke:
            result = lean_worker.bounded_process(["fixed-test-process"], cwd=BASE, env={}, timeout_seconds=0.1)
        invoke.assert_not_called()
        self.assertFalse(result["started"])
        self.assertFalse(result["limiter_applied"])
        self.assertEqual(result["start_error"], "OSError")

    def test_live_output_limit_kills_job_and_caps_retained_bytes(self):
        class Process:
            _handle = 7
            returncode = None
            stdin = io.BytesIO()
            stdout = io.BytesIO(b"x" * 8192)
            stderr = io.BytesIO(b"")

            def poll(self):
                return self.returncode

            def wait(self, timeout):
                return self.returncode

        process = Process()

        class Job:
            cleanup = {}

            def assign(self, proc):
                self.proc = proc

            def kill(self):
                self.proc.returncode = 1
                return True

            def wait_for_exit(self, deadline):
                return True

            def close(self):
                pass

        with patch.object(lean_worker.os, "name", "nt"), \
                patch.object(lean_worker, "_WindowsJob", return_value=Job()), \
                patch.object(lean_worker.subprocess, "Popen", return_value=process) as invoke:
            result = lean_worker.bounded_process(["fixed-test-process"], cwd=BASE, env={},
                                                 timeout_seconds=1, max_bytes=4096)
        self.assertTrue(result["started"])
        self.assertTrue(result["limiter_applied"])
        self.assertTrue(result["output_limited"])
        self.assertEqual(len(result["stdout"]), 4096)
        self.assertEqual(result["exit_code"], 1)
        self.assertFalse(invoke.call_args.kwargs["shell"])

    def test_child_environment_is_allowlisted_and_clears_model_credentials(self):
        with patch.dict(os.environ, {
            "DASHSCOPE_API_KEY": "fixture-secret", "QWEN_BASE_URL": "fixture-url",
            "OPENAI_API_KEY": "fixture-secret", "AWS_SESSION_TOKEN": "fixture-token",
            "MY_PASSWORD": "fixture-password", "PYTHONPATH": "unreviewed-imports",
            "PYTHONSTARTUP": "unreviewed.py", "LEAN_PATH": "unreviewed-libraries",
            "LEAN_SRC_PATH": "unreviewed-sources", "ELAN_TOOLCHAIN": "nightly",
            "UNRELATED_SETTING": "not-allowlisted", "PATH": "unreviewed-programs",
        }):
            environment = lean_worker.clean_environment()
        for key in ("DASHSCOPE_API_KEY", "QWEN_BASE_URL", "OPENAI_API_KEY", "AWS_SESSION_TOKEN",
                    "MY_PASSWORD", "PYTHONPATH", "PYTHONSTARTUP", "LEAN_PATH", "LEAN_SRC_PATH",
                    "ELAN_TOOLCHAIN", "UNRELATED_SETTING"):
            self.assertNotIn(key, environment)
        self.assertNotIn("unreviewed-programs", environment["PATH"])
        self.assertEqual(environment["LEAN_NUM_THREADS"], "1")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")

    def test_toolchain_probe_does_not_fall_back_to_download_or_global_lean(self):
        with patch.object(lean_worker.os, "name", "nt"), \
                patch.object(lean_worker, "_lean_candidate", return_value=None), \
                patch.object(lean_worker, "bounded_process") as invoke:
            result = lean_worker.probe_environment()
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "toolchain_missing")
        self.assertFalse(result["automatic_downloads"])
        self.assertFalse(result["mathlib_used"])
        invoke.assert_not_called()

    def test_worker_timeout_contract_matches_service_bounds(self):
        self.assertEqual(lean_worker.validate_timeout(0.05), 0.05)
        self.assertEqual(lean_worker.validate_timeout(30), 30.0)
        for value in (True, False, None, "10", 0, 31, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lean_worker.validate_timeout(value)

    def test_unsupported_platform_fails_closed_without_claiming_resource_isolation(self):
        with patch.object(lean_worker.os, "name", "posix"), \
                patch.object(lean_worker, "_lean_candidate") as candidate, \
                patch.object(lean_worker, "bounded_process") as invoke:
            result = lean_worker.probe_environment()
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "unsupported_platform")
        self.assertFalse(result["automatic_downloads"])
        candidate.assert_not_called()
        invoke.assert_not_called()

    def test_service_deadline_is_finite_bounded_and_rejects_booleans(self):
        for seconds in (None, True, False, "10", -1, 0, 0.049, 30.001, math.nan, math.inf, -math.inf):
            with self.subTest(seconds=seconds), patch.object(lean_service, "_invoke_worker") as invoke:
                with self.assertRaises(ValueError):
                    lean_service.run(registered_request(), timeout_seconds=seconds)
                invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
