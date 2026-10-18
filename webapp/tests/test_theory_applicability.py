"""Offline applicability contracts; no provider or Lean execution."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import theory
from model_service import capture_text
from storage import Store
from test_theory import ready_project, proposal, model_call, evidence


def reviewed(kind, project=None):
    p = project or ready_project()
    raw = proposal()
    raw["proof_routes"][0]["steps"][0] = "  原错误步骤 <script>test</script>\n\t"
    if kind == "proposal_rejection":
        theory.store_proposal(p, raw, model_call("proposal-call"))
        theory.reject_proposal(p, p["theory"]["pending_proposal"]["id"], "具体审阅量词")
        record = p["theory"]["proposals"][-1]
    elif kind == "legacy_rejected_output":
        theory.apply_failure(p, model_call("legacy-call", status="failed", error_code="invalid_theory"), input_project=copy.deepcopy(p))
        raw["proof_routes"][0]["id"] = "Bad-ID"
        theory.record_rejected_output(p, raw, "legacy-call", "具体审阅量词", base_card_version=1)
        record = p["theory"]["rejected_outputs"][-1]
    else:
        theory.archive_failure(p, model_call(status="failed", error_code="invalid_json", source="test_double"),
            capture_text("原错误步骤 <script>test</script>\n\t"), copy.deepcopy(p), theory.build_messages(p), "不是JSON")
        record = p["theory"]["failure_records"][-1]
        theory.review_failure(p, record["id"], "具体审阅量词", 0, "request-1")
        record = p["theory"]["failure_records"][-1]
    return p, record


KINDS = ("proposal_rejection", "legacy_rejected_output", "failure_record")


class ApplicabilityTest(unittest.TestCase):
    def test_all_kinds_same_card_changed_background_or_evidence_excluded(self):
        for kind in KINDS:
            for change in ("background", "evidence"):
                with self.subTest(kind=kind, change=change):
                    p, record = reviewed(kind)
                    before = theory.next_context(p)
                    self.assertEqual(len(before["review_feedback"]), 1)
                    original = copy.deepcopy(record)
                    if change == "background":
                        p["direction_map"]["initial_idea"] = "新研究背景"
                    else:
                        evidence(p)
                    after = theory.next_context(p)
                    self.assertEqual(p["theory"]["card_version"], 1)
                    self.assertEqual(after["review_feedback"], [])
                    self.assertEqual(after["feedback_dispositions"][0]["exclusion"], "stale_context")
                    self.assertNotIn("具体审阅量词", after["messages"][1]["content"])
                    self.assertNotEqual(before["context_token"], after["context_token"])
                    self.assertEqual(record, original)

    def test_all_kinds_unchanged_basis_and_full_original_survive_review_and_reopen(self):
        with tempfile.TemporaryDirectory(prefix="applicability-") as tmp:
            db = Path(tmp) / "test.db"
            for kind in KINDS:
                with self.subTest(kind=kind):
                    p, record = reviewed(kind)
                    basis = record["feedback_context"]
                    self.assertEqual(basis["sha256"], theory.base_context_fingerprint(p))
                    self.assertNotIn("review_feedback", basis["snapshot"])
                    preview = theory.next_context(p)
                    self.assertTrue(preview["feedback_dispositions"][0]["included"])
                    f = preview["review_feedback"][0]
                    if kind == "failure_record":
                        self.assertEqual(f["raw_content"], record["capture"]["text"])
                        theory.review_failure(p, record["id"], "第二条意见", 1, "request-2")
                        self.assertEqual(theory.next_context(p)["review_feedback"][0]["review_reason"], "第二条意见")
                        self.assertEqual(theory.base_context_fingerprint(p), basis["sha256"])
                    else:
                        self.assertEqual(f["rejected_payload"], record.get("submitted_payload", record.get("payload")))
                    Store(db).create(p)
                    restored = Store(db).get(p["id"])
                    self.assertEqual(restored, p)
                    self.assertEqual(theory.next_context(restored), theory.next_context(p))
                    self.assertIn(basis["sha256"], theory.render_markdown(restored))

    def test_old_records_excluded_without_retroactive_fingerprint_or_content_rewrite(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                p, record = reviewed(kind)
                record.pop("feedback_context")
                record.pop("base_context_fingerprint", None)
                before = copy.deepcopy(p)
                preview = theory.next_context(p)
                self.assertEqual(preview["feedback_dispositions"][0]["exclusion"], "missing_context_basis")
                self.assertEqual(preview["review_feedback"], [])
                theory.render_markdown(p)
                self.assertEqual(before, p)

    def test_v1_server_failure_fingerprint_remains_compatible(self):
        p, record = reviewed("failure_record")
        record.pop("feedback_context")
        self.assertTrue(theory.next_context(p)["failures"][0]["included"])

    def test_legacy_review_cannot_capture_changed_current_context(self):
        for recorded_basis in (False, True):
            p = ready_project()
            kwargs = {"input_project": copy.deepcopy(p)} if recorded_basis else {}
            theory.apply_failure(p, model_call(status="failed", error_code="invalid_theory"), **kwargs)
            p["direction_map"]["initial_idea"] = "审阅前已改变背景"
            theory.record_rejected_output(p, proposal(), "theory-fixture-1", "后来审阅", base_card_version=1)
            entry = p["theory"]["rejected_outputs"][-1]
            self.assertEqual("feedback_context" in entry, recorded_basis)
            exclusion = theory.next_context(p)["feedback_dispositions"][0]["exclusion"]
            self.assertEqual(exclusion, "stale_context" if recorded_basis else "missing_context_basis")

    def test_all_kinds_cross_project_and_tampered_basis_excluded(self):
        for kind in KINDS:
            for change in ("project", "snapshot"):
                with self.subTest(kind=kind, change=change):
                    p, record = reviewed(kind)
                    if change == "project":
                        p["id"] = "another-project"
                    else:
                        record["feedback_context"]["snapshot"]["direction_map"] = {}
                    reason = theory.next_context(p)["feedback_dispositions"][0]["exclusion"]
                    self.assertEqual(reason, "cross_project" if change == "project" else "invalid_context_basis")

    def test_missing_original_proposal_not_reconstructed_from_normalized_content(self):
        p, record = reviewed("proposal_rejection")
        del record["submitted_payload"]
        self.assertEqual(theory.next_context(p)["feedback_dispositions"][0]["exclusion"], "no_original_content")

    def test_last_two_applicable_across_all_three_kinds_and_budget_is_not_truncation(self):
        p = ready_project()
        for index, kind in enumerate(KINDS):
            p, record = reviewed(kind, p)
            if kind == "failure_record":
                record["reviews"][-1]["reviewed_at"] = "2026-10-17T03:00:00Z"
            else:
                record["reviewed_at"] = f"2026-10-17T0{index+1}:00:00Z"
        preview = theory.next_context(p)
        self.assertEqual([d["included"] for d in preview["feedback_dispositions"]], [False, True, True])
        self.assertEqual(preview["feedback_dispositions"][0]["exclusion"], "older_than_latest_two")
        failure = p["theory"]["failure_records"][0]
        failure["capture"] = capture_text("x" * 49500)
        before = copy.deepcopy(p)
        blocked = theory.next_context(p)
        self.assertTrue(blocked["blocked"])
        self.assertFalse(blocked["model_request_sent"])
        self.assertIsNone(blocked["context_token"])
        self.assertEqual(blocked["messages"], [])
        self.assertEqual(blocked["review_feedback"][-1]["raw_content"], "x" * 49500)
        self.assertEqual(p, before)


if __name__ == "__main__":
    unittest.main()
