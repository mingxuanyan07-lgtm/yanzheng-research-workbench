"""Real, optional local Lean acceptance using only reviewed fixed fixtures."""

import copy
import json
import os
from pathlib import Path
import re
import sys
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import lean_service


RUN_RECORDS = []


def _run_case(case_id, *, timeout_seconds=10.0):
    record = lean_service.run({"case_id": case_id}, timeout_seconds=timeout_seconds)
    RUN_RECORDS.append(copy.deepcopy(record))
    return record


class LeanFixedCasesIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = lean_service.environment()
        if not cls.environment.get("available"):
            reason = cls.environment.get("error_code", "unknown_probe_failure")
            if reason in ("toolchain_missing", "unsupported_platform") and os.environ.get("YANZHENG_REQUIRE_LEAN") != "1":
                raise unittest.SkipTest("Optional pinned Lean environment is not available on this host: " + reason)
            raise AssertionError("Pinned Lean environment did not pass its probe: " + str(reason))
        cls.valid = _run_case("nat_add_zero")

    def assert_complete_record(self, record, case_id):
        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["record_type"], "research_lean_verification")
        self.assertEqual(record["case_id"], case_id)
        self.assertTrue(record["id"])
        self.assertIn(record["status"], ("passed", "error", "incomplete", "timeout", "unavailable"))
        self.assertTrue({"source", "elaborated", "target"}.issubset(record["formal_statement"]))
        self.assertRegex(record["source"]["sha256"], re.compile(r"^[0-9a-f]{64}$"))
        self.assertTrue(record["source"]["files"])
        for file in record["source"]["files"]:
            self.assertTrue(file["path"])
            self.assertRegex(file["sha256"], re.compile(r"^[0-9a-f]{64}$"))
        self.assertEqual(record["environment"]["toolchain"], "leanprover/lean4:v4.33.1")
        self.assertEqual(record["environment"]["lean_commit"], self.environment["lean_commit"])
        self.assertTrue(record["environment"]["dependencies"])
        self.assertIsInstance(record["diagnostics"], list)
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertTrue({"audit_completed", "axioms", "sorry_axioms", "unapproved_axioms",
                         "extra_dependencies", "policy_id", "passed"}.issubset(record["trust"]))
        self.assertIsInstance(record["execution"]["compiler_started"], bool)
        self.assertIn("exit_code", record["execution"])
        self.assertIsInstance(record["execution"]["resource_limits"], dict)
        limits = record["execution"]["resource_limits"]
        self.assertGreater(limits["wall_seconds"], 0)
        self.assertLessEqual(limits["wall_seconds"], 30)
        self.assertLessEqual(limits["lean_memory_mb"], 512)
        self.assertLessEqual(limits["job_memory_bytes"], 2 * 1024 * 1024 * 1024)
        self.assertLessEqual(limits["process_memory_bytes"], 1536 * 1024 * 1024)
        self.assertLessEqual(limits["output_bytes_per_stream"], 262144)
        self.assertEqual(limits["lean_threads"], 1)
        self.assertEqual(limits["kernel_trust_level"], 1)
        self.assertFalse(record["trust"]["imported_modules_rechecked"])
        self.assertFalse(limits["is_general_security_sandbox"])
        self.assertFalse(record["provenance"]["external_model_api_called"])
        self.assertFalse(record["provenance"]["arbitrary_code_accepted"])
        self.assertFalse(record["provenance"]["natural_language_equivalence_verified"])
        json.dumps(record, ensure_ascii=False, allow_nan=False)

    def test_valid_short_lemmas_record_exact_statement_and_passed_audit(self):
        for record, case_id in ((self.valid, "nat_add_zero"),
                                (_run_case("implication_identity"), "implication_identity")):
            with self.subTest(case_id=case_id):
                self.assert_complete_record(record, case_id)
                self.assertEqual(record["status"], "passed", record.get("error"))
                self.assertEqual(record["execution"]["exit_code"], 0)
                self.assertTrue(record["execution"]["compiler_started"])
                self.assertTrue(record["formal_statement"]["source"])
                self.assertTrue(record["formal_statement"]["elaborated"])
                self.assertTrue(record["trust"]["audit_completed"])
                self.assertTrue(record["trust"]["passed"])
                self.assertEqual(record["trust"]["sorry_axioms"], [])
                self.assertEqual(record["trust"]["unapproved_axioms"], [])
                self.assertEqual(record["trust"]["extra_dependencies"], [])

    def test_invalid_proof_is_error_and_does_not_claim_statement_is_false(self):
        result = _run_case("invalid_equality")
        self.assert_complete_record(result, "invalid_equality")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "lean_error")
        self.assertNotEqual(result["execution"]["exit_code"], 0)
        self.assertFalse(result["trust"]["passed"])
        self.assertTrue(result["diagnostics"])

    def test_direct_and_transitive_sorry_are_incomplete_even_when_compiler_succeeds(self):
        for case_id in ("direct_sorry", "indirect_sorry"):
            with self.subTest(case_id=case_id):
                result = _run_case(case_id)
                self.assert_complete_record(result, case_id)
                self.assertEqual(result["execution"]["exit_code"], 0)
                self.assertEqual(result["status"], "incomplete")
                self.assertTrue(result["trust"]["audit_completed"])
                self.assertFalse(result["trust"]["passed"])
                self.assertIn("sorryAx", result["trust"]["sorry_axioms"])

    def test_direct_and_transitive_unapproved_axioms_are_rejected(self):
        for case_id in ("direct_axiom", "indirect_axiom"):
            with self.subTest(case_id=case_id):
                result = _run_case(case_id)
                self.assert_complete_record(result, case_id)
                self.assertEqual(result["execution"]["exit_code"], 0)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error"]["code"], "unapproved_axiom")
                self.assertTrue(result["trust"]["audit_completed"])
                self.assertTrue(result["trust"]["unapproved_axioms"])
                self.assertFalse(result["trust"]["passed"])

    def test_extra_native_trust_dependency_does_not_pass(self):
        result = _run_case("native_decide_trust")
        self.assert_complete_record(result, "native_decide_trust")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "trust_dependency")
        self.assertTrue(result["trust"]["audit_completed"])
        self.assertTrue(result["trust"]["extra_dependencies"])
        self.assertFalse(result["trust"]["passed"])

    def test_wall_deadline_stops_timeout_fixture_without_completed_claim(self):
        result = _run_case("timeout_fixture", timeout_seconds=0.3)
        self.assert_complete_record(result, "timeout_fixture")
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["error"]["code"], "wall_timeout")
        self.assertFalse(result["trust"]["passed"])
        self.assertFalse(result["trust"]["audit_completed"])
        # Time includes starting/stopping both the isolated worker and Lean;
        # a broad ceiling detects an escaped child without asserting timings.
        self.assertLess(result["duration_ms"], 10000)


if __name__ == "__main__":
    unittest.main()
