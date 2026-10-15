"""Business boundaries for untrusted LLM proposals; no model/network calls."""

import copy
import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import engine
import planner


def project(low_rank=False):
    result = engine.create_project({
        "initial_idea": "研究随机低秩逼近" if low_rank else "研究公交拥堵",
        "goal": "experimental", "resources": "laptop",
        "time_horizon": "week", "context": "已有少量观察记录。",
    })
    if result["stage"] == "clarifying":
        engine.answer_questions(result, {
            "research_object": "公交站点拥堵", "desired_result": "比较两条现有方法",
            "available_material": "公开样例和普通笔记本",
        })
    return result


def payload():
    record = project()["direction_map"]
    topics = copy.deepcopy(record["topics"])
    for topic in topics:
        topic["known_work"] = "待检索同类问题的诊断、基线与条件分析方法。"
    return {"topics": topics, "recommendation": {
        "topic_id": topics[0]["id"], "reason": "先形成小规模可核查的基线。",
        "would_change_if": ["若无法取得公开样例，收窄对象。"],
    }}


def call(status="success", **extra):
    facts = {
        "id": "call-1", "provider": "dashscope", "model": "qwen-plus-returned",
        "requested_model": "qwen-plus", "status": status,
        "started_at": "2026-10-15T01:00:00+00:00",
        "finished_at": "2026-10-15T01:00:01+00:00", "duration_ms": 1000,
        "request_id": "request-1", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "prompt_version": planner.PROMPT_VERSION,
    }
    facts.update(extra)
    return facts


class PlannerTest(unittest.TestCase):
    def test_prompt_sends_only_user_brief_and_explicit_json_rules(self):
        item = project(True)
        item["model_calls"] = [{"api_key": "secret-not-in-prompt"}]
        item["direction_map"]["sources"][0]["title"] = "archived-source-not-in-prompt"
        messages = planner.build_messages(item)
        combined = json.dumps(messages, ensure_ascii=False)
        self.assertEqual([entry["role"] for entry in messages], ["system", "user"])
        self.assertIn("JSON", combined)
        self.assertIn("first_validation", combined)
        self.assertIn("公交", planner.build_messages(project())[1]["content"])
        self.assertNotIn("secret-not-in-prompt", combined)
        self.assertNotIn("archived-source-not-in-prompt", combined)

    def test_result_preserves_user_input_and_sources_and_archives_old_version(self):
        item = project(True)
        old = copy.deepcopy(item["direction_map"])
        intake = copy.deepcopy(item["intake_form"])
        proposal = payload()
        planner.apply_result(item, proposal, call(input_revision=item["updated_at"], input_sha256="a" * 64))
        record = item["direction_map"]
        self.assertEqual(record["initial_idea"], old["initial_idea"])
        self.assertEqual(record["intake"], old["intake"])
        self.assertEqual(item["intake_form"], intake)
        self.assertEqual(record["sources"], old["sources"])
        self.assertEqual(record["search_log"], old["search_log"])
        self.assertTrue(all(topic["evidence"] == [] for topic in record["topics"]))
        self.assertTrue(all(topic["readiness"] == "provisional" for topic in record["topics"]))
        self.assertTrue(all(topic["novelty_status"] == "needs_review" for topic in record["topics"]))
        self.assertEqual(item["direction_versions"][0]["direction_map"], old)
        self.assertEqual(record["provenance"]["generator"], "qwen")
        self.assertTrue(record["provenance"]["external_model_api_called"])
        self.assertFalse(record["provenance"]["live_search_performed"])
        self.assertFalse(record["provenance"]["new_experiments_executed"])
        self.assertIsNone(record["selected_topic_id"])
        self.assertEqual(engine.validate(record), [])
        self.assertEqual(proposal, payload(), "应用结果不应修改调用者的模型响应对象")
        planner.apply_result(item, payload(), call(id="call-2"))
        self.assertEqual(len(item["direction_versions"]), 2)
        self.assertEqual(item["direction_versions"][0]["direction_map"], old)
        self.assertNotEqual(item["direction_map"]["provenance"]["generation_id"], item["direction_versions"][1]["direction_map"]["provenance"]["generation_id"])

    def test_untrusted_output_cannot_change_sources_selection_or_execution(self):
        mutations = [
            lambda result: result.update({"sources": [{"verification": "fulltext_checked"}]}),
            lambda result: result.update({"selected_topic_id": "compare"}),
            lambda result: result.update({"provenance": {"new_experiments_executed": True}}),
            lambda result: result["topics"][0].update({"evidence": [{"source_id": "prior_1", "relation": "supports", "claim": "已验证"}]}),
            lambda result: result["topics"][0].update({"readiness": "ready_to_plan"}),
            lambda result: result["topics"][0].update({"source": "虚构来源"}),
            lambda result: result["topics"][0].update({"known_work": "论文 https://made-up.invalid 提供证据"}),
            lambda result: result["topics"][0].update({"known_work": "已由 10.1234/fake 证明"}),
            lambda result: result["topics"][0].update({"known_work": "依据 [1] 可以确认"}),
            lambda result: result["topics"][0].update({"question": "超" * 801}),
            lambda result: result["topics"][1].update({"id": result["topics"][0]["id"]}),
            lambda result: result["recommendation"].update({"topic_id": "unknown"}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                item = project(True)
                before = copy.deepcopy(item)
                result = payload()
                mutate(result)
                with self.assertRaises(ValueError):
                    planner.apply_result(item, result, call())
                self.assertEqual(item, before)

    def test_selected_topic_cannot_be_replaced_and_reopen_is_explicit(self):
        item = project()
        engine.select_topic(item, item["direction_map"]["topics"][0]["id"], "我的选题理由")
        before = copy.deepcopy(item)
        with self.assertRaises(ValueError):
            planner.apply_result(item, payload(), call())
        self.assertEqual(item, before)
        engine.reopen_project(item)
        planner.apply_result(item, payload(), call())
        self.assertNotIn("selection_reason", item["direction_map"]["intake"]["known"])
        self.assertIsNone(item["direction_map"]["selected_topic_id"])
        self.assertTrue(any("我的选题理由" in event["summary"] for event in item["history"]))

    def test_failure_and_conflict_save_call_facts_without_changing_map(self):
        item = project()
        engine.select_topic(item, item["direction_map"]["topics"][0]["id"], "保留我的选择")
        before = copy.deepcopy(item["direction_map"])
        planner.apply_failure(item, call("failed", error_code="timeout", attempts=1, usage=None,
                                          summary="上游超时", application_status="rejected",
                                          raw_response="private-raw-response", api_key="private-key"))
        self.assertEqual(item["direction_map"], before)
        self.assertNotIn("direction_versions", item)
        self.assertEqual(item["model_calls"][0]["usage"], None)
        self.assertEqual(item["model_calls"][0]["attempts"], 1)
        self.assertNotIn("private", json.dumps(item["model_calls"]))
        planner.apply_failure(item, call(id="call-2", application_status="discarded_conflict",
                                           usage={"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}))
        self.assertEqual(item["direction_map"], before)
        self.assertEqual(len(item["model_calls"]), 2)
        self.assertEqual(item["model_calls"][1]["status"], "success")
        self.assertIn("未应用", item["history"][-1]["summary"])

    def test_plan_reports_recorded_model_and_usage_without_new_execution_claims(self):
        item = project()
        planner.apply_result(item, payload(), call())
        engine.select_topic(item, item["direction_map"]["topics"][0]["id"])
        rendered = engine.render_plan(item)
        self.assertIn("qwen-plus-returned", rendered)
        self.assertIn("请求模型：qwen-plus", rendered)
        self.assertIn("request-1", rendered)
        self.assertIn("输入 10、输出 20、总计 30 tokens", rendered)
        self.assertIn("未执行新实验", rendered)
        self.assertIn("尚未建立该方向的文献支持", rendered)
        self.assertIn("模型提出的待检索背景", rendered)
        self.assertNotIn("未调用千问", rendered)


if __name__ == "__main__":
    unittest.main()
