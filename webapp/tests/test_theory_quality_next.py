"""Offline regression for exact rejected mathematics and safe review replay.

These fixtures exercise contracts, not mathematical correctness or provider calls.
The only persistence check creates and removes its own temporary SQLite database.
"""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import theory
from storage import Store
from test_theory import card, model_call, proposal, ready_project


def failed_call(item, call_id="rejected-fixture"):
    theory.apply_failure(item, model_call(call_id, status="failed", error_code="invalid_theory"),
                         input_project=copy.deepcopy(item))


def rejected_payload():
    value = proposal()
    value["revised_card"]["statement"] = "  被拒原命题：所有参数均满足条件。\n"
    value["proof_routes"][0]["id"] = "Bad-ID"
    value["proof_routes"][0]["steps"][0] = "  未核查的原推导\n\t不能在归档时修好。  "
    value["proof_routes"][0]["obligations"][1]["depends_on"] = ["missing-obligation"]
    value["proof_routes"][0]["evidence"] = [
        {"note_id": "unknown-note", "relation": "unsupported-relation", "claim": "原输出声称文献[1]支持。"}
    ]
    return value


def archive(item, payload=None, call_id="rejected-fixture", reason="请检查原推导的全部参数范围。"):
    return theory.record_rejected_output(
        item, payload if payload is not None else rejected_payload(), call_id, reason,
        base_card_version=1,
    )


def prompt_context(item):
    return json.loads(theory.build_messages(item)[1]["content"].split("\n", 1)[1])


class TheoryQualityNextTest(unittest.TestCase):
    def test_structurally_rejected_output_keeps_raw_math_without_promoting_state(self):
        item = ready_project()
        failed_call(item)
        before = copy.deepcopy(item)
        payload = rejected_payload()
        with self.assertRaises(ValueError):
            theory.validate_payload(payload, item)
        archive(item, payload)
        state = theory.workspace(item)
        record = state["rejected_outputs"][0]
        self.assertEqual(record["payload"], payload)
        self.assertEqual(record["payload_sha256"], hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest())
        self.assertEqual(record["source_call_id"], "rejected-fixture")
        self.assertEqual(record["prompt_version"], theory.PROMPT_VERSION)
        self.assertEqual(record["source"], "model")
        self.assertEqual(record["feedback_kind"], "structural_rejection")
        self.assertFalse(record["acceptance_allowed"])
        self.assertIsNone(state["pending_proposal"])
        self.assertEqual(state["proposals"], [])
        for key in ("card", "card_version", "state", "proof_routes", "proof_status"):
            self.assertEqual(state[key], before["theory"][key])
        self.assertEqual(item["direction_map"], before["direction_map"])
        payload["proof_routes"][0]["steps"][0] = "caller changed its object"
        self.assertNotEqual(record["payload"], payload)

    def test_call_must_belong_to_project_and_be_invalid_theory_and_duplicate_is_atomic(self):
        wrong_calls = [None, model_call("other-project-call", status="failed", error_code="invalid_theory"),
                       model_call("rejected-fixture"),
                       model_call("rejected-fixture", status="failed", error_code="request_timeout")]
        for wrong in wrong_calls:
            with self.subTest(call=wrong):
                item = ready_project()
                if wrong is not None:
                    item.setdefault("model_calls", []).append(wrong)
                before = copy.deepcopy(item)
                with self.assertRaises(ValueError):
                    archive(item)
                self.assertEqual(item, before)
        item = ready_project()
        failed_call(item)
        archive(item)
        before = copy.deepcopy(item)
        with self.assertRaises(ValueError):
            archive(item, reason="另一条理由也不能重复归档同次调用。")
        self.assertEqual(item, before)

    def test_foreign_shape_non_json_values_invalid_version_and_bad_reason_are_atomic(self):
        mutations = [lambda value: value.update(raw_response="provider log"),
                     lambda value: value["proof_routes"][0].update(api_key="secret"),
                     lambda value: value.update(search_leads={"not": "a JSON array"}),
                     lambda value: value.update(revision_reason=object()),
                     lambda value: value.update(revision_reason=float("nan")),
                     lambda value: value.update(revision_reason="has\x00control"),
                     lambda value: value["revised_card"].update(statement="x" * 4001)]
        for mutate in mutations:
            item, payload = ready_project(), rejected_payload()
            failed_call(item)
            mutate(payload)
            before = copy.deepcopy(item)
            with self.subTest(payload=repr(payload)[:120]):
                with self.assertRaises((ValueError, TypeError)):
                    archive(item, payload)
                self.assertEqual(item, before)
        for version, reason in [(True, "具体反馈"), (0, "具体反馈"), (2, "具体反馈"),
                                (1, " "), (1, []), (1, "x" * 2001)]:
            item = ready_project()
            failed_call(item)
            before = copy.deepcopy(item)
            with self.subTest(version=version, reason=repr(reason)[:40]):
                with self.assertRaises((ValueError, TypeError)):
                    theory.record_rejected_output(item, rejected_payload(), "rejected-fixture", reason,
                                                  base_card_version=version)
                self.assertEqual(item, before)

    def test_total_context_limit_rejects_bounded_fields_without_silent_truncation(self):
        item, payload = ready_project(), proposal()
        failed_call(item)
        route = payload["proof_routes"][0]
        route["steps"] = ["甲" * 1200] * 8
        route["obligations"] = [{"id": "lemma", "statement": "乙" * 1600, "depends_on": []}] * 12
        payload["proof_routes"] = [copy.deepcopy(route) for _ in range(3)]
        self.assertGreater(len(json.dumps(payload, ensure_ascii=False)), 50000)
        before = copy.deepcopy(item)
        with self.assertRaises(ValueError):
            archive(item, payload)
        self.assertEqual(item, before)

    def test_feedback_contains_original_math_and_specific_reason_but_no_call_logs(self):
        item = ready_project()
        raw = proposal()
        raw["revision_reason"] = "  原始理由保留空白。\n"
        raw["proof_routes"][0]["steps"][0] = "  先检查原步骤。\n\t"
        theory.store_proposal(item, raw, model_call("normal-rejected"))
        pending_id = theory.workspace(item)["pending_proposal"]["id"]
        theory.reject_proposal(item, pending_id, "正常提议的具体批评。")
        failed_call(item)
        bad = rejected_payload()
        archive(item, bad, reason="结构错误之外，请核对这条原断言的量词。")
        item["model_calls"][-1].update(api_key="secret-fixture-key", raw_response="private-provider-log")
        feedback = prompt_context(item)["review_feedback"]
        self.assertEqual(len(feedback), 2)
        self.assertEqual(feedback[0]["rejected_payload"], raw)
        self.assertEqual(feedback[1]["rejected_payload"], bad)
        self.assertEqual(feedback[1]["review_reason"], "结构错误之外，请核对这条原断言的量词。")
        context = json.dumps(prompt_context(item), ensure_ascii=False)
        self.assertNotIn("secret-fixture-key", context)
        self.assertNotIn("private-provider-log", context)
        self.assertNotIn("snapshot", json.dumps(feedback, ensure_ascii=False))
        self.assertIn("不是合法提议", feedback[1]["scope"])

    def test_feedback_merges_both_kinds_by_actual_time_and_keeps_last_two(self):
        item = ready_project()
        theory.store_proposal(item, proposal(), model_call("normal-rejected"))
        theory.reject_proposal(item, theory.workspace(item)["pending_proposal"]["id"], "中间时间的审阅。")
        item["theory"]["proposals"][-1]["reviewed_at"] = "2026-10-16T09:00:00+08:00"
        for call_id, stamp in [("newest", "2026-10-16T02:00:00+00:00"),
                               ("oldest", "2026-10-16T08:00:00+08:00")]:
            failed_call(item, call_id)
            archive(item, call_id=call_id, reason=call_id)
            item["theory"]["rejected_outputs"][-1]["reviewed_at"] = stamp
        feedback = prompt_context(item)["review_feedback"]
        self.assertEqual([record["review_reason"] for record in feedback], ["中间时间的审阅。", "newest"])
        self.assertEqual([record["feedback_kind"] for record in feedback],
                         ["proposal_rejection", "structural_rejection"])

    def test_new_feedback_invalidates_acceptance_without_erasing_pending_proposal(self):
        item = ready_project()
        failed_call(item)
        theory.store_proposal(item, proposal(), model_call("still-pending"))
        pending = copy.deepcopy(theory.workspace(item)["pending_proposal"])
        fingerprint = theory.context_fingerprint(item)
        archive(item)
        self.assertNotEqual(theory.context_fingerprint(item), fingerprint)
        self.assertEqual(theory.workspace(item)["pending_proposal"], pending)
        before = copy.deepcopy(item)
        with self.assertRaises(ValueError):
            theory.apply_proposal(item, pending["id"])
        self.assertEqual(item, before)

    def test_temporary_database_reopen_and_json_and_markdown_keep_rejection_provenance(self):
        item = ready_project()
        failed_call(item)
        archive(item)
        temporary_root = BASE / "tests" / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="theory-next-test-", dir=temporary_root) as temporary:
            path = Path(temporary) / "disposable.sqlite3"
            Store(path).create(item)
            restarted = Store(path).get(item["id"])
            exported = json.loads(json.dumps(restarted, ensure_ascii=False))
            self.assertEqual(exported, item)
            self.assertEqual(prompt_context(exported), prompt_context(item))
            text = theory.render_markdown(exported)
            for required in ("结构拒绝材料", "不是可接受提议", "rejected-fixture", theory.PROMPT_VERSION,
                             "Bad-ID", "missing-obligation", "unsupported-relation", "原推导", "全部参数范围"):
                self.assertIn(required, text)
            self.assertIn(item["theory"]["rejected_outputs"][0]["payload_sha256"], text)

    def test_archive_and_replay_are_not_specific_to_historical_three_case_answers(self):
        for statement, domain in [("任意有限树都有唯一简单路径。", "有限无向图"),
                                  ("线性变换的核是子空间。", "任意域上的向量空间")]:
            with self.subTest(statement=statement):
                item = ready_project()
                theory.save_card(item, card(statement=statement, domain=domain), expected_version=1)
                failed_call(item)
                payload = rejected_payload()
                payload["revised_card"]["statement"] = statement
                payload["revised_card"]["domain"] = domain
                theory.record_rejected_output(item, payload, "rejected-fixture", "检查本案例独有的边界。",
                                              base_card_version=2)
                self.assertEqual(prompt_context(item)["review_feedback"][0]["rejected_payload"], payload)
                self.assertEqual(theory.workspace(item)["card"]["statement"], statement)
                self.assertEqual(theory.workspace(item)["card_version"], 2)

    def test_new_mathematical_brackets_and_decimals_are_not_explicit_citations(self):
        # Admission is structural only: neither [n] nor a decimal implies that
        # the mathematics is valid or that every disguised reference is found.
        for text in ("向量的第一分量记为 x[1]。", "取实数 2026.1234 作为参数。"):
            with self.subTest(allowed=text):
                item, payload = ready_project(), proposal()
                payload["revised_card"]["statement"] = text
                payload["proof_routes"][0]["steps"] = [text]
                theory.store_proposal(item, payload, model_call("notation-fixture"))
                self.assertEqual(theory.workspace(item)["proof_status"], "not_formally_verified")
                self.assertEqual(theory.workspace(item)["pending_proposal"]["revised_card"]["statement"], text)
        references = ["https://example.org/paper", "doi: 10.1234/example", "arXiv:2026.1234",
                      "参见 arXiv 2601.12345 的结论", "arXiv math/0401234",
                      "依据[S001]", "依据[source_x]", "文献[1]给出证明", "reference [1] proves it"]
        for text in references:
            for location in ("changed_card", "new_step"):
                with self.subTest(rejected=text, location=location):
                    item, payload = ready_project(), proposal()
                    if location == "changed_card":
                        payload["revised_card"]["statement"] = text
                    else:
                        payload["proof_routes"][0]["steps"] = [text]
                    before = copy.deepcopy(item)
                    with self.assertRaises(ValueError):
                        theory.store_proposal(item, payload, model_call("citation-fixture"))
                    self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
