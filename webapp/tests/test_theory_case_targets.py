"""v2 target behaviors. Only explicit disposable projects, no registry/Lean/model."""
import copy
import json
import re
import unittest

from fastapi.testclient import TestClient
from app import create_app
import test_theory_checking as _fixture
from test_theory import card
import theory


class CaseTargetsTest(unittest.TestCase):
    setUp = _fixture.CheckingTest.setUp
    tearDown = _fixture.CheckingTest.tearDown
    create = _fixture.CheckingTest.create
    view = _fixture.CheckingTest.view
    body = _fixture.CheckingTest.body
    save = _fixture.CheckingTest.save
    opinion = _fixture.CheckingTest.opinion

    def register(self, **extra):
        b = {"request_id":"case-1", "expected_revision":self.view()["case_revision"],
            "case_id":"pointwise-limit-continuity-known-result", "title":"test_fixture existing known-result case",
            "source_version":"C frozen source v1", "source_sha256":"a"*64, "source_reference":"test_fixture declaration, not verified bytes", **extra}
        res = self.client.post(self.base+"/cases",json=b)
        self.assertEqual(res.status_code,200,res.text)
        return b, self.view()["cases"][-1]

    def target_body(self, scope, **extra):
        w = self.view()
        t = next(t for t in w["targets"] if t["target_scope"]==scope and not t["historical"])
        fields = {"target_key":t["target_key"],"target_scope":scope,"card_proposition":None if scope=="case" else "P1","proposition":"P2" if scope=="case" else "P1"}
        fields.update(extra)
        return self.body(**fields)

    def test_case_target_keeps_source_identity_separate_from_application_card(self):
        _,c=self.register()
        r=self.save(self.target_body("case",case_key=c["case_key"]))
        self.assertEqual(r["case_snapshot"]["declared_source_sha256"],"a"*64)
        self.assertNotEqual(r["case_snapshot"]["case_source_metadata_sha256"],"a"*64)
        self.assertEqual(r["target"]["case_id"],c["case_id"])
        for k in ("card_id","card_version","card_sha256","route_id","obligation_id"):
            self.assertIsNone(r["target"][k])
        self.assertEqual(r["obligation_status"],"not_applicable")
        self.assertIsNone(r["material"]["card_proposition"])
        self.assertEqual(self.client.get(self.base).json()["card_version"],2)

    def test_card_target_works_without_accepting_a_plan(self):
        p=self.client.post("/api/projects",json={"initial_idea":"test_fixture card only", "goal":"theory"}).json()
        base=f"/api/projects/{p['id']}/theory"
        self.assertEqual(self.client.post(base+"/card",json={"card":card(),"expected_version":0}).status_code,200)
        w=self.client.get(base).json()["checking"]
        self.assertEqual({t["target_scope"] for t in w["targets"]},{"card"})
        b=self.body(target_key=w["targets"][0]["target_key"],context_token=w["context_token"],expected_revision=0,note_id=None,target_scope="card")
        self.assertEqual(self.client.post(base+"/checks",json=b).status_code,200)
        state=self.client.get(base).json()
        self.assertEqual(state["proof_routes"],[])
        self.assertIsNone(state["accepted_proposal_id"])
        self.assertIsNone(state["checking"]["records"][0]["target"]["obligation_id"])

    def test_case_target_works_without_a_problem_card(self):
        p=self.client.post("/api/projects",json={"initial_idea":"test_fixture existing case archive only","goal":"theory"}).json()
        base=f"/api/projects/{p['id']}/theory"
        b,_=self.register(); b["expected_revision"]=0
        self.assertEqual(self.client.post(base+"/cases",json=b).status_code,200)
        w=self.client.get(base).json()["checking"]
        request=self.body(target_key=w["targets"][0]["target_key"],context_token=w["context_token"],expected_revision=0,note_id=None,card_proposition=None,target_scope="case",proposition="HISTORICAL")
        self.assertEqual(self.client.post(base+"/checks",json=request).status_code,200)
        self.assertIsNone(self.client.get(base).json()["card"])
        # Whole Markdown must retain case-only material even without a card.
        self.assertIn("C01",self.client.get(base+"/export?format=md").text)

    def test_case_registration_is_append_only_idempotent_and_version_checked(self):
        body,first=self.register(source_sha256=None)
        self.assertIsNone(first["declared_source_sha256"])
        self.assertEqual(self.client.post(self.base+"/cases",json=body).status_code,200)
        self.assertEqual(self.client.post(self.base+"/cases",json={**body,"title":"replacement"}).status_code,409)
        self.assertEqual(self.client.post(self.base+"/cases",json={**body,"request_id":"new"}).status_code,409)
        self.register(request_id="case-2",source_version="C v2")
        raw=self.app.state.store.get(self.p["id"])["theory"]["review_cases"]
        self.assertEqual(len(raw),2)
        self.assertEqual({**first, "historical":True},self.view()["cases"][0])
        self.assertNotIn("historical",raw[0])
        self.assertEqual(raw[0]["title"],body["title"])

    def test_case_and_scope_forgery_and_cross_project_are_rejected(self):
        _,case=self.register()
        b=self.target_body("case")
        for extra in ({"target_scope":"card"},{"case_key":"f"*64},{"card_proposition":"P2"}):
            self.assertEqual(self.client.post(self.base+"/checks",json={**b,**extra}).status_code,400)
        other=self.create();obase=f"/api/projects/{other['id']}/theory"
        ow=self.client.get(obase).json()["checking"]
        foreign={**b,"target_key":ow["targets"][0]["target_key"],"context_token":ow["context_token"],"target_scope":"obligation","card_proposition":"P1","case_key":case["case_key"],"note_id":None}
        self.assertEqual(self.client.post(obase+"/checks",json=foreign).status_code,400)
        for extra in ({"case_id":"../case"},{"source_sha256":"wrong"}):
            case_body={"request_id":"invalid","expected_revision":1,"case_id":"test-case","title":"QA","source_version":"v1","source_sha256":None,"source_reference":"QA",**extra}
            self.assertEqual(self.client.post(self.base+"/cases",json=case_body).status_code,400)

    def test_old_v1_record_and_idempotent_payload_remain_unchanged_after_registration(self):
        b=self.body();r=self.save(b)
        def make_legacy(p):p["theory"]["checking_records"][0]["target"].pop("target_scope")
        self.app.state.store.update(self.p["id"],make_legacy)
        original=copy.deepcopy(self.app.state.store.get(self.p["id"])["theory"]["checking_records"][0])
        self.register()
        self.assertEqual(self.client.post(self.base+"/checks",json=b).status_code,200)
        self.assertEqual(self.view()["records"][0]["target"]["target_scope"],"obligation")
        self.assertEqual(self.view()["records"][0]["stale_reasons"],[])
        self.assertEqual(self.app.state.store.get(self.p["id"])["theory"]["checking_records"][0],original)
        self.assertEqual(self.view()["records"][0]["material_sha256"],r["material_sha256"])

    def test_new_case_source_invalidates_submission_but_preserves_old_material_and_review(self):
        _,case=self.register()
        b=self.target_body("case",case_key=case["case_key"]);r=self.save(b)
        self.client.post(self.base+f"/checks/{r['id']}/reviews",json=self.opinion(kind="assistant_check"))
        before=copy.deepcopy(self.app.state.store.get(self.p["id"])["theory"]["checking_records"][0])
        stale=self.target_body("case",request_id="after-source-change")
        self.register(request_id="case-2",source_version="source v2")
        self.assertEqual(self.client.post(self.base+"/checks",json=stale).status_code,409)
        self.assertIn("case_source_changed",self.view()["records"][0]["stale_reasons"])
        self.assertEqual(self.app.state.store.get(self.p["id"])["theory"]["checking_records"][0],before)
        fresh=self.target_body("case",request_id="old-source-explicit",target_key=b["target_key"],case_key=case["case_key"])
        saved=self.save(fresh)
        self.assertTrue(saved["target"]["historical"])
        self.assertIn("case_source_changed",saved["stale_reasons"])

    def test_native_multi_sources_preserve_identity_boundaries_and_legacy_primary(self):
        refs=[{"artifact_id":"same-bytes-different-origin", "label":label, "source_version":"frozen source v1", "locator":f"section-{i}",
            "support_scope":f"only scope {i}", "origin":label, "original_bytes_sha256":None, "acquired_at":None} for i,label in enumerate(["historical_model","curated_example","source_snapshot"])]
        r=self.save(self.target_body("card",artifact={"artifact_id":"legacy-primary","original_bytes_sha256":"b"*64,"acquired_at":None},artifact_refs=refs))
        self.assertEqual(len(r["material"]["artifact_refs"]),3)
        self.assertEqual([x["origin"] for x in r["material"]["artifact_refs"]],[x["origin"] for x in refs])
        self.assertEqual(r["material"]["artifact"]["artifact_id"],"legacy-primary")
        self.assertTrue(all(x["access"]=="not_connected" and x["verification"]=="unknown" for x in r["material"]["artifact_refs"]))
        self.assertEqual(self.client.post(self.base+"/checks",json=self.target_body("card",request_id="path-injection",artifact_refs=[{**refs[0],"path":"D:/secret"}])).status_code,422)

    def test_case_sources_and_refs_do_not_expand_model_messages_or_upgrade_math(self):
        before=self.client.get(self.base+"/next-context").json();routes=copy.deepcopy(self.p["theory"]["proof_routes"])
        self.register();r=self.save(self.target_body("case"))
        self.client.post(self.base+f"/checks/{r['id']}/reviews",json=self.opinion())
        after=self.client.get(self.base+"/next-context").json()
        self.assertEqual(before["messages"],after["messages"])
        self.assertEqual(before["context_token"],after["context_token"])
        self.assertEqual(self.client.get(self.base).json()["proof_routes"],routes)
        item=self.view()["records"][0]
        self.assertEqual(item["independent_mathematical_review"],"pending")
        self.assertEqual(item["obligation_status"],"not_applicable")
        self.assertEqual(item["formal_status"],"not_formally_verified")

    def test_whole_and_single_exports_and_restart_keep_full_case_context(self):
        self.register();r=self.save(self.target_body("case"))
        self.client.post(self.base+f"/checks/{r['id']}/reviews",json=self.opinion())
        before=self.view()
        self.client.close();self.app=create_app(self.db,model_client=self.model,lean_client=lambda *_:self.fail("No Lean"));self.client=TestClient(self.app)
        self.assertEqual(self.view(),before)
        for path in (f"/checks/{r['id']}/export","/export"):
            response=self.client.get(self.base+path+"?format=md")
            parsed=[json.loads(s) for s in re.findall(r"```json\n(.*?)\n```",response.text,re.S)]
            self.assertEqual(parsed[-1],before["records"][0])
            self.assertNotIn("路线 / 义务：None",response.text)
        self.assertEqual(self.client.get(self.base+"/export?format=json").json()["checking"],before)
