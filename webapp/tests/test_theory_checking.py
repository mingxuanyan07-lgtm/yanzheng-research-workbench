"""API behaviors on disposable SQLite. No model, source fetch or Lean runs."""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from app import create_app
from model_service import ModelService
import theory
import theory_examples
import theory_review


class CheckingTest(unittest.TestCase):
    def setUp(self, *, temp_dir=None):
        self.tmp = tempfile.TemporaryDirectory(dir=temp_dir)
        self.db = Path(self.tmp.name) / "checking.db"
        self.model = ModelService(env_file=Path(self.tmp.name)/"absent", environ={"YANZHENG_MODEL_PROVIDER":"demo"})
        self.model.complete_for_theory = lambda *_: self.fail("No model calls")
        self.app = create_app(self.db, model_client=self.model, lean_client=lambda *_: self.fail("No Lean"))
        self.client = TestClient(self.app)
        self.p = self.create()
        self.base = f"/api/projects/{self.p['id']}/theory"

    def create(self):
        p = theory_examples.create_example()
        theory.apply_proposal(p, p["theory"]["pending_proposal"]["id"])
        self.app.state.store.create(p)
        return p

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def view(self):
        return self.client.get(self.base).json()["checking"]

    def body(self, **extra):
        w = self.view()
        return {"request_id":"request-1", "expected_revision":w["revision"], "context_token":w["context_token"],
            "target_key":w["targets"][0]["target_key"], "claim_id":"C01", "proposition":"P0", "card_proposition":"P1",
            "claim_text":"  原文 <script>test</script>\n", "assumptions":"n≥1，x∈[0,1]", "support_scope":"只支持指定条件",
            "derivation":"因式分解；尚待独立审阅。", "record_origin":"assistant_check", "source_reference":"公开 QA 副本 v1",
            "note_id":w["notes"][0]["note_id"], "locator":"D1", "artifact":None, **extra}

    def save(self, body=None):
        response = self.client.post(self.base+"/checks", json=body or self.body())
        self.assertEqual(response.status_code, 200, response.text)
        return self.view()["records"][-1]

    def opinion(self, **extra):
        return {"request_id":"review-1", "expected_revision":0, "kind":"human_review", "scope":"仅检查 D1 定义域",
            "opinion":"测试人工意见，仍需核查。", "reviewer":"QA 声明身份", "confirm_record_only":True, **extra}

    def test_save_snapshots_unknown_provenance_and_separate_target(self):
        b = self.body(artifact={"artifact_id":"historic-unknown","original_bytes_sha256":None,"acquired_at":None})
        r = self.save(b)
        self.assertEqual(r["material"]["claim_text"], b["claim_text"])
        self.assertEqual(r["target"]["project_id"], self.p["id"])
        self.assertEqual(r["target"]["card_sha256"], theory._hash(self.p["theory"]["card"]))
        self.assertEqual(r["note_sha256"], theory._hash(self.p["reading_notes"][0]))
        self.assertEqual(r["material"]["artifact"]["verification"], "unknown")
        self.assertIsNone(r["material"]["artifact"]["declared_original_bytes_sha256"])
        self.assertIsNotNone(r["association_warning"])
        self.assertEqual(r["human_review_status"], "pending")

    def test_nonexistent_and_cross_project_targets_notes_and_reviews(self):
        other = self.create()
        targets = theory_review.workspace(other)
        for extra in ({"target_key":"f"*64},{"target_key":targets["targets"][0]["target_key"]},
                {"note_id":"missing"},{"note_id":targets["notes"][0]["note_id"]}):
            response = self.client.post(self.base+"/checks",json=self.body(**extra))
            self.assertEqual(response.status_code,400,response.text)
        r = self.save()
        obase = f"/api/projects/{other['id']}/theory/checks/{r['id']}"
        self.assertEqual(self.client.post(obase+"/reviews",json=self.opinion()).status_code,404)
        self.assertEqual(self.client.get(obase+"/export").status_code,404)
        self.assertEqual(self.client.get("/api/projects/missing/theory").status_code,404)

    def test_idempotency_and_conflicting_duplicate_never_replaces_original(self):
        b = self.body(); r = self.save(b)
        self.save(b)
        self.assertEqual(len(self.view()["records"]),1)
        self.assertEqual(self.client.post(self.base+"/checks",json={**b,"claim_text":"replacement"}).status_code,409)
        self.assertEqual(self.view()["records"][0],r)
        stale = {**b,"request_id":"request-2"}
        self.assertEqual(self.client.post(self.base+"/checks",json=stale).status_code,409)

    def test_changed_card_note_source_or_background_conflicts_and_preserves_history(self):
        r = self.save(); stored = copy.deepcopy(self.app.state.store.get(self.p["id"])["theory"]["checking_records"][0])
        for kind in ("note","source","background","card"):
            b = self.body(request_id=kind)
            def change(p):
                if kind == "note": p["reading_notes"][0]["content"] += " 更新"
                elif kind == "source": p["literature_searches"][0]["sources"][0]["year_or_version"] = "new-source-version"
                elif kind == "background": p["direction_map"]["initial_idea"] += " 新背景"
                else: theory.save_card(p,{**p["theory"]["card"],"title":"新卡"},p["theory"]["card_version"])
            self.app.state.store.update(self.p["id"],change)
            self.assertEqual(self.client.post(self.base+"/checks",json=b).status_code,409)
            self.assertTrue(self.view()["records"][0]["stale_reasons"])
            self.assertEqual(self.app.state.store.get(self.p["id"])["theory"]["checking_records"][0],stored)
        fresh = self.body(request_id="explicit-old-card")
        saved = self.save(fresh)
        self.assertTrue(saved["target"]["historical"])
        self.assertIn("card_changed",saved["stale_reasons"])

    def test_review_is_append_only_and_does_not_invalidate_own_context_or_close_obligation(self):
        original_routes = copy.deepcopy(self.p["theory"]["proof_routes"])
        r = self.save(); b = self.opinion(); url=self.base+f"/checks/{r['id']}/reviews"
        before = self.view()["context_token"]
        self.assertEqual(self.client.post(url,json=b).status_code,200)
        self.assertEqual(self.client.post(url,json=b).status_code,200)
        self.assertEqual(self.client.post(url,json={**b,"opinion":"replace"}).status_code,409)
        self.assertEqual(self.client.post(url,json={**b,"request_id":"other"}).status_code,409)
        item=self.view()["records"][0]
        self.assertEqual(item["material"],r["material"])
        self.assertEqual(len(item["reviews"]),1)
        self.assertEqual(item["human_review_status"],"recorded_unverified")
        self.assertEqual(item["independent_mathematical_review"],"pending")
        self.assertFalse(item["reviews"][0]["identity_verified"])
        self.assertEqual(item["formal_status"],"not_formally_verified")
        self.assertEqual(item["obligation_status"],"open")
        self.assertEqual(item["stale_reasons"],[])
        self.assertEqual(self.view()["context_token"],before)
        self.assertEqual(self.client.get(self.base).json()["proof_routes"],original_routes)

    def test_review_requires_explicit_confirmation_and_assistant_does_not_become_human(self):
        r=self.save(); url=self.base+f"/checks/{r['id']}/reviews"
        self.assertEqual(self.client.post(url,json=self.opinion(confirm_record_only=False)).status_code,400)
        self.assertEqual(self.client.post(url,json=self.opinion(kind="assistant_check")).status_code,200)
        self.assertEqual(self.view()["records"][0]["human_review_status"],"pending")

    def test_messages_token_and_budget_unchanged_by_checking_or_review(self):
        before=self.client.get(self.base+"/next-context").json()
        r=self.save(); self.client.post(self.base+f"/checks/{r['id']}/reviews",json=self.opinion())
        after=self.client.get(self.base+"/next-context").json()
        self.assertEqual(before["messages"],after["messages"])
        self.assertEqual(before["context_token"],after["context_token"])
        self.assertEqual(before["context_characters"],after["context_characters"])
        self.assertEqual(after["local_checking_materials"]["count"],1)
        self.assertFalse(after["local_checking_materials"]["included"])

    def test_restart_and_single_whole_json_markdown_exports(self):
        r=self.save(); self.client.post(self.base+f"/checks/{r['id']}/reviews",json=self.opinion())
        expected=self.view()
        self.client.close()
        self.app=create_app(self.db,model_client=self.model,lean_client=lambda *_:self.fail("No Lean"))
        self.client=TestClient(self.app)
        self.assertEqual(self.view(),expected)
        single=self.client.get(self.base+f"/checks/{r['id']}/export?format=json").json()
        full=self.client.get(self.base+"/export?format=json").json()
        self.assertEqual(single,full["checking"]["records"][0])
        for path in (f"/checks/{r['id']}/export", "/export"):
            response=self.client.get(self.base+path+"?format=md")
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn("attachment",response.headers["content-disposition"])
            self.assertIn("C01",response.text)
            self.assertIn("not_formally_verified",response.text)
            self.assertIn("QA 声明身份",response.text)
            self.assertNotIn("<script>",response.text)
            blocks=re.findall(r"```json\n(.*?)\n```",response.text,re.S)
            self.assertTrue(blocks)
            self.assertEqual(json.loads(blocks[-1]),single)

    def test_limits_unknown_artifact_and_extra_status_cannot_be_injected(self):
        for extra, status in (({"claim_text":"x"*8001},422),({"formal_status":"verified"},422),
            ({"artifact":{"artifact_id":"a","original_bytes_sha256":"bad","acquired_at":None}},400),
            ({"artifact":{"artifact_id":"a","original_bytes_sha256":None,"acquired_at":"2026-10-17"}},400)):
            response=self.client.post(self.base+"/checks",json=self.body(**extra))
            self.assertEqual(response.status_code,status,response.text)
        self.assertEqual(self.view()["records"],[])


if __name__ == "__main__":
    unittest.main()
