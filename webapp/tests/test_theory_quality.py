"""Offline regression for observed quality failures, not a mathematics prover."""
import copy
import json
import unittest
from test_theory import ready_project, card, proposal, model_call, evidence
import theory


def context(project):
    return json.loads(theory.build_messages(project)[1]["content"].split("\n", 1)[1])


class TheoryQualityTest(unittest.TestCase):
    def test_unchanged_saved_math_notation_and_review_target_produce_empty_diff(self):
        item = ready_project()
        correct = card(statement="For the 1 by 1 real matrix A = [1], det(A) = 1.",
                       definitions=["The real matrix [1] has one entry."],
                       assumptions=["A = [1]."], target="审阅、解释此正确命题。")
        theory.save_card(item, correct, 1)
        result = proposal()
        result["revised_card"] = copy.deepcopy(correct)
        theory.store_proposal(item, result, model_call())
        pending = theory.workspace(item)["pending_proposal"]
        self.assertEqual(pending["diff"], [])
        self.assertEqual(pending["revised_card"], correct)
        self.assertEqual(item["theory"]["card"], correct)

    def test_changed_generated_field_still_cannot_invent_reference(self):
        item = ready_project()
        for field, text in (("statement", "Result from https://example.org/invented"),
                            ("target", "Use DOI 10.1000/invented")):
            result = proposal()
            result["revised_card"][field] = text
            before = copy.deepcopy(item)
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    theory.store_proposal(item, result, model_call())
                self.assertEqual(item, before)

    def test_no_identified_gap_is_allowed_without_promoting_proof_status(self):
        item = ready_project()
        result = proposal()
        result["proof_routes"][0]["gaps"] = []
        theory.store_proposal(item, result, model_call())
        pending = theory.workspace(item)["pending_proposal"]
        route = pending["proof_routes"][0]
        self.assertEqual(route["gaps"], [])
        self.assertEqual(route["proof_status"], "not_formally_verified")
        self.assertTrue(all(value["status"] == "open" for value in route["obligations"]))
        self.assertIn("未列出额外数学缺口", theory.render_markdown(item))
        self.assertEqual(theory.PAYLOAD_SCHEMA["properties"]["proof_routes"]["items"]["properties"]["gaps"]["minItems"], 0)

    def test_feedback_contains_the_exact_rejected_step_but_no_credential_logs(self):
        item = ready_project()
        note = evidence(item)
        result = proposal(note["id"])
        bad_step = "对任意 δ>0，取 x=1−δ/2 并声称 x∈[0,1)。"
        result["proof_routes"][0]["steps"] = [bad_step]
        theory.store_proposal(item, result, model_call())
        pid = item["theory"]["pending_proposal"]["id"]
        theory.reject_proposal(item, pid, "请独立审查大参数下的定义域；本条反馈不是已核验结论。")
        archived = copy.deepcopy(item["theory"]["proposals"][-1])
        item["theory"]["proposals"][-1]["private_log"] = "credential-fixture-never-send"
        item["model_calls"][-1]["api_key"] = "credential-fixture-never-send"
        feedback = context(item)["review_feedback"][0]
        replay = feedback["rejected_payload"]
        self.assertEqual(replay, result)
        self.assertEqual(feedback["proposal_id"], pid)
        self.assertEqual(feedback["base_card_version"], 1)
        self.assertNotIn("credential-fixture-never-send", json.dumps(context(item)))
        self.assertNotIn("snapshot", replay["proof_routes"][0]["evidence"][0])
        self.assertEqual(item["theory"]["proposals"][-1]["submitted_payload"], archived["submitted_payload"])

    def test_feedback_changes_invalidate_inflight_context_without_mutating_card(self):
        item = ready_project()
        theory.store_proposal(item, proposal(), model_call())
        theory.reject_proposal(item, item["theory"]["pending_proposal"]["id"], "请复核量词。")
        theory.store_proposal(item, proposal(), model_call("second"))
        pid = item["theory"]["pending_proposal"]["id"]
        saved = copy.deepcopy(item["theory"]["card"])
        item["theory"]["proposals"][0]["researcher_reason"] = "新增了对上一轮推导的审阅意见。"
        with self.assertRaises(ValueError):
            theory.apply_proposal(item, pid)
        self.assertEqual(item["theory"]["card"], saved)

    def test_exact_submitted_json_and_its_prompt_version_survive_normalization(self):
        item = ready_project()
        result = proposal()
        result["revision_reason"] = "  这是模型返回的原始 JSON 字符串值。  "
        theory.store_proposal(item, result, model_call(prompt_version="quality-test-version"))
        result["revision_reason"] = "later mutation"
        stored = item["theory"]["pending_proposal"]
        self.assertEqual(stored["submitted_payload"]["revision_reason"], "  这是模型返回的原始 JSON 字符串值。  ")
        self.assertEqual(stored["revision_reason"], "这是模型返回的原始 JSON 字符串值。")
        self.assertEqual(stored["prompt_version"], "quality-test-version")
        restored = json.loads(json.dumps(item, ensure_ascii=False))
        self.assertEqual(restored["theory"]["proposals"][-1]["submitted_payload"], stored["submitted_payload"])

    def test_legacy_proposals_without_original_remain_archived_but_excluded(self):
        item = ready_project()
        result = proposal()
        theory.store_proposal(item, result, model_call())
        pid = item["theory"]["pending_proposal"]["id"]
        theory.reject_proposal(item, pid, "复核旧版本。")
        del item["theory"]["proposals"][-1]["submitted_payload"]
        del item["theory"]["proposals"][-1]["prompt_version"]
        before = copy.deepcopy(item)
        self.assertEqual(context(item)["review_feedback"], [])
        self.assertEqual(theory.next_context(item)["feedback_dispositions"][0]["exclusion"], "no_original_content")
        self.assertEqual(item, before)
        self.assertIn("复核旧版本", theory.render_markdown(item))

    def test_structural_validation_is_explicitly_not_a_semantic_prover(self):
        item = ready_project()
        result = proposal()
        result["proof_routes"][0]["steps"] = ["对所有 δ>0，x=1−δ/2 总在 [0,1)。"]
        value = theory.validate_payload(result, item)
        self.assertEqual(value["proof_routes"][0]["proof_status"], "not_formally_verified")
        # The actual mathematical counterexample to that assertion is δ=4.
        # This case-specific arithmetic is a test oracle, not production parsing.
        self.assertLess(1 - 4 / 2, 0)

    def test_mixed_case_or_math_symbol_ids_are_rejected_without_repair(self):
        item = ready_project()
        for identifier in ("f_N_continuity", "delta_δ", "步骤"):
            result = proposal()
            result["proof_routes"][0]["obligations"][0]["id"] = identifier
            before = copy.deepcopy(item)
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    theory.store_proposal(item, result, model_call())
                self.assertEqual(item, before)

    def test_reject_regenerate_and_explicit_accept_remains_possible(self):
        item = ready_project()
        theory.store_proposal(item, proposal(), model_call("first"))
        theory.reject_proposal(item, item["theory"]["pending_proposal"]["id"], "请检查证明路线。")
        theory.store_proposal(item, proposal(), model_call("second"))
        before = copy.deepcopy(item["direction_map"])
        theory.apply_proposal(item, item["theory"]["pending_proposal"]["id"])
        self.assertEqual(item["theory"]["card_version"], 2)
        self.assertEqual(item["direction_map"], before)

    def test_legacy_context_fingerprint_is_retained_but_requires_regeneration(self):
        item = ready_project()
        theory.store_proposal(item, proposal(), model_call())
        state = item["theory"]
        legacy = theory._hash({"card_version": state["card_version"], "card": state["card"],
            "direction_map": item["direction_map"], "evidence_notes": theory.evidence_notes(item),
            "example_provenance": item.get("example_provenance")})
        state["pending_proposal"]["context_fingerprint"] = legacy
        pid = state["pending_proposal"]["id"]
        before = copy.deepcopy(item)
        with self.assertRaises(ValueError):
            theory.apply_proposal(item, pid)
        self.assertEqual(item, before)
        theory.store_proposal(item, proposal(), model_call("regenerated"))
        self.assertEqual(item["theory"]["proposals"][0]["status"], "stale")
        theory.apply_proposal(item, item["theory"]["pending_proposal"]["id"])

    def test_changed_previous_routes_invalidate_their_generated_followup(self):
        item = ready_project()
        theory.store_proposal(item, proposal(), model_call("first"))
        theory.apply_proposal(item, item["theory"]["pending_proposal"]["id"])
        theory.store_proposal(item, proposal(), model_call("followup"))
        pid = item["theory"]["pending_proposal"]["id"]
        item["theory"]["proof_routes"][0]["steps"].append("研究者修改了下一轮所依赖的原路线。")
        before = copy.deepcopy(item)
        with self.assertRaises(ValueError):
            theory.apply_proposal(item, pid)
        self.assertEqual(item, before)

    def test_large_replayed_feedback_stops_instead_of_silently_dropping_errors(self):
        item = ready_project()
        for index in range(2):
            result = proposal()
            route = result["proof_routes"][0]
            route["steps"] = [f"{step}：" + "待核对的数学推导。" * 120 for step in range(8)]
            result["proof_routes"] = [copy.deepcopy(route) for _ in range(3)]
            for number, value in enumerate(result["proof_routes"]):
                value["id"] = "route_" + str(number)
            theory.store_proposal(item, result, model_call("large-" + str(index)))
            theory.reject_proposal(item, item["theory"]["pending_proposal"]["id"], "逐步复核全部原推导，不要截断出错步骤。")
        before = copy.deepcopy(item)
        with self.assertRaisesRegex(ValueError, "50000"):
            theory.build_messages(item)
        self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
