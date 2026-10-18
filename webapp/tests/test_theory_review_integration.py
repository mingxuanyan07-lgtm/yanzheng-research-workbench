"""Transport-bound theory failure tests. Offline MockTransport, temporary SQLite only."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import create_app
from model_service import ModelService, capture_text, MAX_RESPONSE_BYTES
from storage import Store
from test_theory import card, proposal
from test_model_service import KEY, URL, good_response
import theory


class FailureIntegrationTest(unittest.TestCase):
    def setUp(self):
        scratch = BASE / "tests/.tmp"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.db = Path(self.tmp.name) / "review.db"
        self.count = 0
        self.sent_messages = []
        self.content = json.dumps(proposal(), ensure_ascii=False)
        self.status = 200
        self.envelope = None
        self.raw = None
        self.on_call = None
        def handler(request):
            self.count += 1
            self.sent_messages.append(json.loads(request.content)["messages"])
            if self.on_call:
                self.on_call()
            if self.raw is not None:
                return httpx.Response(self.status, content=self.raw)
            envelope = self.envelope or good_response(choices=[{"finish_reason": "stop", "message": {"content": self.content}}])
            return httpx.Response(self.status, json=envelope, headers={"x-request-id": "offline-request-id"})
        self.model = ModelService(env_file=Path(self.tmp.name)/"absent.env", environ={
            "YANZHENG_MODEL_PROVIDER": "qwen", "DASHSCOPE_API_KEY": KEY, "QWEN_BASE_URL": URL},
            transport=httpx.MockTransport(handler))
        self.app = create_app(self.db, model_client=self.model, lean_client=lambda _: (_ for _ in ()).throw(AssertionError("No Lean")))
        self.client = TestClient(self.app)
        self.project = self.create()
        self.base = f"/api/projects/{self.project['id']}/theory"

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def create(self):
        p = self.client.post("/api/projects", json={"initial_idea": "公开函数收敛测试", "goal": "theory"}).json()
        return self.client.post(f"/api/projects/{p['id']}/theory/card", json={"card": card(), "expected_version": 0}).json()

    def generate_failure(self, content=None):
        self.content = content if content is not None else self.content
        response = self.client.post(self.base + "/propose", json={})
        self.assertIn(response.status_code, (409, 502, 503, 504), response.text)
        return self.client.get(self.base).json()["failure_records"][-1]

    def review(self, item, reason="第一个非法ID保持原样；请独立检查量词。", revision=0, request_id="review-1"):
        return self.client.post(self.base + f"/failures/{item['id']}/reviews", json={
            "reason": reason, "expected_revision": revision, "request_id": request_id})

    def test_illegal_id_original_exact_call_usage_and_snapshot(self):
        payload = proposal()
        payload["proof_routes"][0]["id"] = "Bad_N"
        original = "  " + json.dumps(payload, ensure_ascii=False) + "\n"
        item = self.generate_failure(original)
        self.assertEqual(item["capture"]["text"], original)
        self.assertEqual(item["capture"]["sha256"], hashlib.sha256(original.encode()).hexdigest())
        self.assertIn("小写", item["diagnostic"])
        self.assertEqual(item["category"], "structural_failure")
        self.assertEqual(item["call"]["request_id"], "offline-request-id")
        self.assertEqual(item["call"]["usage"]["total_tokens"], 49)
        self.assertEqual(item["card_sha256"], theory._hash(card()))
        self.assertEqual(item["card"], card())
        self.assertEqual(item["input_sha256"], hashlib.sha256(json.dumps(item["input_messages"], ensure_ascii=False, sort_keys=True).encode()).hexdigest())
        self.assertEqual(item["prompt_version"], theory.PROMPT_VERSION)
        saved = self.app.state.store.get(self.project["id"])
        self.assertEqual(saved["model_calls"][-1]["id"], item["source_call_id"])
        self.assertFalse(item["acceptance_allowed"])
        self.assertIsNone(saved["theory"]["pending_proposal"])
        self.assertNotIn("_response_capture", self.client.get("/api/status").text)

    def test_non_json_feedback_replay_is_exact_and_not_a_proposal(self):
        original = "不是JSON <script>alert(1)</script> & 原文"
        item = self.generate_failure(original)
        self.assertEqual(item["call"]["error_code"], "invalid_json")
        self.assertEqual(self.review(item).status_code, 200)
        preview = self.client.get(self.base + "/next-context").json()
        self.assertFalse(preview["blocked"])
        self.assertTrue(preview["failures"][0]["included"])
        self.assertEqual(preview["review_feedback"][-1]["raw_content"], original)
        self.assertEqual(self.count, 1)
        self.assertEqual(self.client.post(self.base + f"/proposals/{item['id']}/accept", json={}).status_code, 400)
        markdown = self.client.get(self.base + "/export?format=md").text
        self.assertNotIn("<script>", markdown)

    def test_service_failure_and_missing_content_keep_unknown_usage(self):
        self.envelope = {"choices": []}
        item = self.generate_failure()
        self.assertEqual(item["category"], "service_failure")
        self.assertEqual(item["capture"]["kind"], "missing")
        self.assertIsNone(item["call"]["usage"]["total_tokens"])
        self.assertEqual(item["mathematical_status"], "not_reviewed")
        self.review(item)
        p = self.client.get(self.base + "/next-context").json()
        self.assertEqual(p["failures"][0]["exclusion"], "no_model_content")

    def test_content_and_transport_limits_are_explicit(self):
        item = self.generate_failure("x" * 51000)
        self.assertEqual(len(item["capture"]["text"]), 50000)
        self.assertTrue(item["capture"]["truncated"])
        self.assertEqual(item["capture"]["original_characters"], 51000)
        self.review(item)
        self.assertEqual(self.client.get(self.base+"/next-context").json()["failures"][0]["exclusion"], "partial_or_redacted_content")
        self.raw = b"x" * (MAX_RESPONSE_BYTES + 500)
        item = self.generate_failure()
        self.assertEqual(item["capture"]["kind"], "response_body_prefix")
        self.assertIsNone(item["capture"]["sha256"])
        self.assertEqual(item["call"]["error_code"], "response_too_large")

    def test_credential_echo_is_marked_and_not_replayed(self):
        item = self.generate_failure(KEY + "<script>")
        self.assertNotIn(KEY, json.dumps(item))
        self.assertTrue(item["capture"]["redacted"])
        self.review(item)
        self.assertFalse(self.client.get(self.base+"/next-context").json()["failures"][0]["included"])

    def test_idempotent_reviews_conflicts_and_original_immutable(self):
        item = self.generate_failure("invalid")
        self.assertEqual(self.review(item).status_code, 200)
        self.assertEqual(self.review(item).status_code, 200)
        self.assertEqual(self.review(item, reason="不同内容").status_code, 409)
        self.assertEqual(self.review(item, request_id="review-2").status_code, 409)
        self.assertEqual(self.review(item, revision=1, request_id="review-2").status_code, 200)
        saved = self.client.get(self.base+f"/failures/{item['id']}").json()
        self.assertEqual(len(saved["reviews"]), 2)
        self.assertEqual(saved["capture"], item["capture"])
        self.assertEqual(saved["diagnostic"], item["diagnostic"])
        self.assertEqual(saved["mathematical_status"], "human_feedback_unverified")

    def test_no_browser_ingestion_and_cross_project_access(self):
        item = self.generate_failure("invalid")
        other = self.create()
        base = f"/api/projects/{other['id']}/theory/failures/{item['id']}"
        for path in (base, base+"/export"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(base+"/reviews", json={"reason":"fake", "expected_revision":0,"request_id":"x"}).status_code, 404)
        self.assertEqual(self.client.post(self.base+f"/failures/{item['id']}/reviews", json={"reason":"fake", "expected_revision":0,"request_id":"x", "capture":{"text":"forged"}}).status_code, 422)
        self.assertEqual(self.client.post(self.base+"/propose", json={"payload":"forged"}).status_code, 422)

    def test_old_card_and_changed_evidence_excluded_stale_preview_never_calls(self):
        item = self.generate_failure("invalid")
        self.review(item)
        old = self.client.get(self.base+"/next-context").json()
        changed = card(); changed["title"] += "新版本"
        self.client.post(self.base+"/card", json={"card":changed,"expected_version":1})
        response = self.client.post(self.base+"/propose", json={"context_token":old["context_token"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.count, 1)
        current = self.client.get(self.base+"/next-context").json()
        self.assertEqual(current["failures"][0]["exclusion"], "stale_context")
        self.assertEqual(self.client.post(self.base+"/card", json={"card":card(),"expected_version":1}).status_code, 400)

    def test_feedback_changes_invalidate_preview(self):
        item = self.generate_failure("invalid")
        preview = self.client.get(self.base+"/next-context").json()
        self.review(item)
        self.assertEqual(self.client.post(self.base+"/propose", json={"context_token":preview["context_token"]}).status_code, 409)
        self.assertEqual(self.count, 1)

    def test_restart_and_export_read_exact_materials(self):
        item = self.generate_failure("原文\ninvalid")
        self.review(item)
        reopened = Store(self.db).get(self.project["id"])
        exported = self.client.get(self.base+f"/failures/{item['id']}/export").json()
        self.assertEqual(exported, reopened["theory"]["failure_records"][0])
        self.assertEqual(self.client.get(self.base+"/export?format=json").json()["failure_records"][0], exported)
        self.assertIn(item["source_call_id"], self.client.get(self.base+"/export?format=md").text)

    def test_single_json_markdown_exports_preserve_basis_and_escape_without_mutation(self):
        import engine
        item = self.generate_failure('原文 <script>raw</script>\n```\n未核查步骤')
        self.review(item, reason='具体反馈 <img src=x>')
        original = self.app.state.store.get(self.project["id"])
        saved = original["theory"]["failure_records"][0]
        endpoint = self.base + f"/failures/{item['id']}/export"
        self.assertEqual(self.client.get(endpoint).json(), saved)
        self.assertEqual(self.client.get(endpoint + "?format=json").json(), saved)
        response = self.client.get(endpoint + "?format=md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/markdown", response.headers["content-type"])
        self.assertIn('.md"', response.headers["content-disposition"])
        self.assertIn(engine._markdown_text(json.dumps(saved, ensure_ascii=False, indent=2)), response.text)
        self.assertNotIn("<script>", response.text)
        self.assertNotIn("<img", response.text)
        self.assertEqual(self.client.get(endpoint + "?format=html").status_code, 422)
        other = self.create()
        self.assertEqual(self.client.get(f"/api/projects/{other['id']}/theory/failures/{item['id']}/export?format=md").status_code, 404)
        self.assertEqual(self.app.state.store.get(self.project["id"]), original)

    def test_archive_duplicate_is_noop_but_replacement_rejected(self):
        item = self.generate_failure("invalid")
        current = self.app.state.store.get(self.project["id"])
        before = copy.deepcopy(current)
        theory.archive_failure(current, item["call"], item["capture"], self.project, item["input_messages"], item["diagnostic"])
        self.assertEqual(current, before)
        with self.assertRaises(ValueError):
            theory.archive_failure(current, item["call"], capture_text("replacement"), self.project, item["input_messages"], "x")

    def test_context_budget_blocks_without_truncating_and_latest_two(self):
        items = [self.generate_failure(("invalid" + str(i)) * 3250) for i in range(3)]
        for i, item in enumerate(items):
            self.review(item, request_id=f"review-{i}")
        preview = self.client.get(self.base+"/next-context").json()
        self.assertTrue(preview["blocked"])
        self.assertEqual(sum(x["included"] for x in preview["failures"]), 2)
        self.assertEqual(preview["failures"][0]["exclusion"], "older_than_latest_two")
        self.assertEqual(len(self.client.get(self.base).json()["failure_records"][0]["capture"]["text"]), 26000)
        self.assertEqual(self.count, 3)

    def test_inflight_card_change_archives_original_input(self):
        self.on_call = lambda: self.app.state.store.update(self.project["id"], lambda p: theory.save_card(p, {**card(), "title":"changed in flight"}, 1))
        item = self.generate_failure()
        self.assertEqual(item["category"], "context_conflict")
        self.assertEqual(item["base_card_version"], 1)
        self.assertEqual(item["card"], card())
        self.review(item)
        self.assertEqual(self.client.get(self.base+"/next-context").json()["failures"][0]["exclusion"], "stale_context")

    def test_same_timestamp_inflight_change_still_archives_actual_response(self):
        original_stamp = self.app.state.store.get(self.project["id"])["updated_at"]
        def change():
            def mutate(p):
                theory.save_card(p, {**card(), "title":"same-timestamp update"}, 1)
                p["updated_at"] = original_stamp
            self.app.state.store.update(self.project["id"], mutate)
        self.on_call = change
        response = self.client.post(self.base+"/propose", json={})
        self.assertEqual(response.status_code,409,response.text)
        saved=self.client.get(self.base).json()
        self.assertEqual(saved["card"]["title"],"same-timestamp update")
        self.assertIsNone(saved["pending_proposal"])
        item=saved["failure_records"][-1]
        self.assertEqual(item["category"],"context_conflict")
        self.assertEqual(item["capture"]["text"],self.content)
        self.assertEqual(item["base_card_version"],1)

    def test_whitespace_identifier_must_not_be_silently_repaired(self):
        payload = proposal()
        payload["proof_routes"][0]["id"] = " route_id "
        item = self.generate_failure(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(item["category"], "structural_failure")
        self.assertIn(" route_id ", item["capture"]["text"])

    def test_evidence_context_change_excludes_review(self):
        item = self.generate_failure("invalid")
        self.review(item)
        self.app.state.store.update(self.project["id"], lambda p: p["direction_map"].update(initial_idea="changed research context"))
        self.assertEqual(self.client.get(self.base+"/next-context").json()["failures"][0]["exclusion"], "stale_context")

    def test_missing_fields_are_saved_not_repaired(self):
        original = '{"proof_routes": []}'
        item = self.generate_failure(original)
        self.assertEqual(item["capture"]["text"], original)
        self.assertEqual(item["category"], "structural_failure")
        self.assertIn("契约", item["diagnostic"])

    def test_preview_then_explicit_generation_uses_same_messages(self):
        item = self.generate_failure("invalid")
        self.review(item)
        preview = self.client.get(self.base+"/next-context").json()
        self.content = json.dumps(proposal(), ensure_ascii=False)
        response = self.client.post(self.base+"/propose", json={"context_token":preview["context_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.count, 2)
        self.assertEqual(self.sent_messages[-1], preview["messages"])
        self.assertEqual(response.json()["model_calls"][-1]["input_sha256"], hashlib.sha256(json.dumps(preview["messages"], ensure_ascii=False, sort_keys=True).encode()).hexdigest())

    def test_proposal_original_basis_preview_matches_transport_and_stale_context_no_call(self):
        from test_theory import evidence
        for change in ("background", "evidence"):
            p = self.create()
            base = f"/api/projects/{p['id']}/theory"
            before = self.client.get(base + "/next-context").json()
            response = self.client.post(base + "/propose", json={"context_token": before["context_token"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.sent_messages[-1], before["messages"])
            proposal_record = response.json()["theory"]["pending_proposal"]
            basis = copy.deepcopy(proposal_record["feedback_context"])
            self.assertEqual(basis["sha256"], theory.base_context_fingerprint(p))
            response = self.client.post(base + f"/proposals/{proposal_record['id']}/reject", json={"reason":"具体反馈：核对量词"})
            self.assertEqual(response.status_code, 200)
            preview = self.client.get(base + "/next-context").json()
            self.assertEqual(len(preview["review_feedback"]), 1)
            if change == "background":
                self.app.state.store.update(p["id"], lambda item: item["direction_map"].update(initial_idea="新背景"))
            else:
                self.app.state.store.update(p["id"], lambda item: evidence(item))
            count = self.count
            self.assertEqual(self.client.post(base + "/propose", json={"context_token": preview["context_token"]}).status_code, 409)
            self.assertEqual(self.count, count)
            after = self.client.get(base + "/next-context").json()
            self.assertEqual(after["review_feedback"], [])
            self.assertEqual(after["feedback_dispositions"][0]["exclusion"], "stale_context")
            exported = self.client.get(base + "/export?format=json").json()
            self.assertEqual(exported["proposals"][0]["feedback_context"], basis)



if __name__ == "__main__":
    unittest.main()
