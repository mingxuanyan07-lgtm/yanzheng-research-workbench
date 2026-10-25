"use strict";
// VM tests of the actual app closure: no browser, network, app.py or real DB.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../../..");

function appHarness() {
  let busy = false;
  let controllerOptions;
  const calls = [];
  const downloads = [];
  const requests = [];
  let fetchResponse;
  let unsavedEntry;
  const store = new Map();
  const targets = new Map();
  targets.set("#experiments-content", {innerHTML:""});
  targets.set("#initial-idea", { addEventListener() {} });
  targets.set("#mobile-menu", { addEventListener() {} });
  targets.set("#global-message", { textContent: "", hidden: true, classList: { toggle() {} } });
  targets.set("#toast", { textContent: "", hidden: true, classList: { toggle() {} } });
  const events = {};
  const controller = {
    isBusy: () => busy,
    render: project => `<section id="lean-workspace" data-project-id="${project.id}">fixed cases</section>`,
    load: async project => { calls.push(["load", project.id]); return { ok: true }; },
    run: async project => { calls.push(["run", project.id]); return { ok: true }; },
    chooseCase: (...args) => calls.push(["choose", ...args]),
    invalidate: projectId => calls.push(["invalidate", projectId]),
    exportUnsaved: (projectId, runId) => { calls.push(["exportUnsaved", projectId, runId]); return unsavedEntry; },
  };
  const document = {
    querySelector: selector => targets.get(selector) || null,
    querySelectorAll: selector => targets.get("all:" + selector) || [],
    addEventListener: (name, listener) => { events[name] = listener; },
    activeElement: null,
    body: { appendChild() {} },
    createElement: () => {
      const anchor = { click() { downloads.push({ href: this.href, filename: this.download }); }, remove() {} };
      return anchor;
    },
  };
  const sandbox = {
    document,
    window: { YanzhengLean: { create: options => { controllerOptions = options; return controller; } }, addEventListener() {} },
    localStorage: { getItem: key => store.get(key) || null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) },
    crypto: require("node:crypto").webcrypto, console, URLSearchParams, URL: class extends URL {
      static createObjectURL(blob) { downloads.push({ blob }); return "blob:ui-qa"; }
      static revokeObjectURL() {}
    }, Blob, Intl, Date, setTimeout, clearTimeout,
    fetch: async (url, options) => { requests.push({url, options}); return typeof fetchResponse === "function" ? fetchResponse(url, options) : fetchResponse; },
    FormData: class { constructor(form) { return Object.entries(form.fields || {})[Symbol.iterator](); } },
    location: { hash: "#experiments" }, history: { replaceState() {} },
  };
  let source = fs.readFileSync(path.join(root, "webapp/static/app.js"), "utf8").replace(/\r\n/g, "\n");
  const cut = source.lastIndexOf("\n  paintIcons();\n  restoreIntakeDraft();");
  assert.ok(cut > 0, "Find actual bootstrap before replacing it for isolated tests");
  source = source.slice(0, cut) + `\nwindow.appTest = {renderArtifactFact,inspectArtifact,renderArtifactAccess,artifactViews,unsavedArtifactFacts,changeCheckReferences,renderChecking,checkDraft,checkDrafts,checkMessages,captureCheckDraft,saveCheck,rebaseCheck,state,api,downloadLeanUnsaved,renderProofRoutes,renderTheory,renderTheoryProposalHistory,renderTheoryFeedback,renderNextContext,theoryContextPreviews,inspectNextContext,saveFailureReview,failureDrafts,failureDraft,renderTheoryFailures,theoryProposalVersion,theoryProposalSource,captureTheoryDraft,syncMutationControls,performLean,patchLeanWorkspace,renderDirectionProposal,renderEvidence,renderDecisionTrajectory,createPublicSample,renderSource,renderSources,download,validAttachmentName,researchProgress,renderResearchJourney,renderResearchActions,renderReferenceImport,renderImportedMetadata,projectFilters,filteredProjects,renderProjectCards,renderProjectResults,renderBackupControls,restoreBackup,backupFeedback,renderLiteratureOverview,documentKey,documentContext,documentViews,documentDraftBackups,inspectDocumentAnchor,sameDocumentAnchor,documentOriginalStatus,renderDocumentText,renderPaperDocuments,renderReadingNotes,selectedDocumentAnchor,putDocumentExcerpt,openDocumentAnchor,uploadPaperDocument,captureReadingDraft,renderNoteCandidateLinks,renderDocumentAnchorLink,declaredFingerprintText,uploadedDocumentSelection};\n})();`;
  vm.runInNewContext(source, sandbox, { filename: "webapp/static/app.js" });
  return { ...sandbox.window.appTest, targets, document, calls, events, controllerOptions, downloads, requests, store,
    setFetchResponse: value => { fetchResponse = value; }, setUnsavedEntry: value => { unsavedEntry = value; },
    setBusy: value => { busy = value; } };
}

const route = { title: "公开路线", steps: ["核对量词"], obligations: [{ id: "review", statement: "独立检查", depends_on: [] }], gaps: [], evidence: [] };
const card = { title: "正确命题", domain: "[0,1]", statement: "连续函数列一致极限连续。", definitions: [], assumptions: ["一致收敛"], target: "审阅与解释" };

test("saved unbound support remains visible without being relabeled as bound evidence",()=>{
  const app=appHarness();
  const source={id:"old-source",title:"旧来源",url:"https://example.org/source",verification:"abstract_checked",locator:"Abstract"};
  const entry={source_id:source.id,relation:"supports",claim:"历史声明 <script>bad</script>"};
  const topic={evidence:[entry]};
  const before=JSON.stringify(topic);
  const html=app.renderEvidence(topic,{sources:[source]});
  assert.match(html,/历史关联 · 未绑定阅读笔记/);
  assert.match(html,/不等于已核验证据/);
  assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);
  assert.doesNotMatch(html,/已绑定用户确认阅读笔记/);
  assert.equal(JSON.stringify(topic),before);
  entry.relation="background";
  assert.doesNotMatch(app.renderEvidence(topic,{sources:[source]}),/历史关联 · 未绑定阅读笔记/);
  entry.relation="supports";
  entry.note_id="note-1";
  entry.note_snapshot={note_id:"note-1",level:"abstract_checked",locator:"Abstract",note_sha256:"a".repeat(64),content_sha256:"b".repeat(64),caveat:"快照不是程序核验。"};
  const bound=app.renderEvidence(topic,{sources:[source]});
  assert.match(bound,/已绑定用户确认阅读笔记/);
  assert.doesNotMatch(bound,/历史关联 · 未绑定阅读笔记/);
});

function checkingHarness() {
  const app=appHarness();
  const target={target_key:"t",card_version:2,card_id:"alpha:theory-card",card_sha256:"h",card,route:{title:"反例核查"},obligation_id:"domain",obligation:{statement:"定义域"}};
  const checking={revision:0,context_token:"old-context",targets:[target],notes:[],records:[]};
  app.state.project={id:"alpha",theory:{card,card_version:2,checking}};
  app.state.validationMode="theory";
  app.targets.set("#toast",{classList:{toggle(){}}});
  return {app,checking,target};
}

function addCaseTarget(checking) {
  const entry={case_id:"known-result",case_key:"case-key",version:1,title:"QA existing case",source_version:"frozen-v1",declared_source_sha256:"declared-bytes",case_source_metadata_sha256:"server-metadata",source_reference:"QA source",historical:false};
  const target={target_scope:"case",target_key:"case-target",case_key:entry.case_key,case_id:entry.case_id,case:entry,card:null,card_version:null,card_sha256:null,obligation_id:null,route:null};
  checking.cases=[entry];checking.case_revision=1;checking.targets.push(target);return target;
}

test("an accidentally added empty reference can be removed without losing earlier draft sources",()=>{
  const {app,checking}=checkingHarness();const editor={innerHTML:""};
  const form={dataset:{projectId:"alpha",checkId:"new"},fields:{claim_text:"kept",ref_0_artifact_id:"first"},querySelector:()=>editor};
  const d=app.checkDraft("alpha","new",checking);d.ref_count=1;
  app.changeCheckReferences(form,1);assert.equal(d.ref_count,2);assert.match(editor.innerHTML,/ref_1_artifact_id/);
  form.fields.ref_1_label="accidental source";
  app.changeCheckReferences(form,-1);
  assert.equal(d.ref_count,1);assert.equal(d.ref_0_artifact_id,"first");assert.equal(d.claim_text,"kept");assert.equal(d.ref_1_label,undefined);
  assert.doesNotMatch(editor.innerHTML,/ref_1_artifact_id/);assert.equal(app.requests.length,0);
});

test("case target selection explicitly clears card association and persists the owned source",async()=>{
  const {app,checking}=checkingHarness();const target=addCaseTarget(checking);
  const fields=new Map([[".checking-target",{innerHTML:""}],["[name=\"case_key\"]",{value:""}],["[name=\"card_proposition\"]",{value:"P1"}]]);
  const form={dataset:{projectId:"alpha",checkId:"new"},fields:{target_key:target.target_key,card_proposition:"P1"},querySelector:s=>fields.get(s)};
  const element={name:"target_key",value:target.target_key,closest:s=>s===".checking-form"?form:null};
  app.events.change({target:element});
  const d=app.checkDraft("alpha","new",checking);
  assert.equal(d.case_key,"case-key");assert.equal(d.card_proposition,"");
  assert.match(fields.get(".checking-target").innerHTML,/案例级材料/);
  assert.doesNotMatch(fields.get(".checking-target").innerHTML,/卡 v0|义务 null/);
  assert.equal(JSON.parse(app.store.get("yanzheng.checkDraft.alpha:new")).case_key,"case-key");
});

test("case form submits metadata declaration to cases endpoint with its own revision",async()=>{
  const {app,checking}=checkingHarness();checking.case_revision=2;
  const form={dataset:{projectId:"alpha",checkId:"case-source"},fields:{case_id:"known-result",title:"QA",source_version:"external-v1",source_sha256:"a".repeat(64),source_reference:"QA public source"}};
  app.setFetchResponse(url=>url.endsWith("/cases")?{ok:false,status:409,json:async()=>({detail:"登记版本已更新"})}:{ok:true,json:async()=>({card,checking:{...checking,case_revision:3}})});
  await app.saveCheck(form);
  assert.match(app.requests[0].url,/\/theory\/cases$/);
  const body=JSON.parse(app.requests[0].options.body);assert.equal(body.expected_revision,2);assert.equal(body.source_sha256,"a".repeat(64));
  assert.equal(body.card_sha256,undefined);assert.equal(body.confirm_record_only,undefined);
  const draft=app.checkDrafts["alpha:case-source"];assert.equal(draft.source_version,"external-v1");
  await app.rebaseCheck("case-source");assert.equal(draft.expected_revision,3);assert.equal(draft.source_version,"external-v1");
  assert.equal(app.requests.filter(r=>r.options.method==="POST").length,1);
});

test("case scoped record renders exact source declaration without an invented obligation",()=>{
  const {app,checking}=checkingHarness();const target=addCaseTarget(checking);checking.targets=[target];
  checking.records=[{id:"case-record",target,case_snapshot:target.case,material:{claim_id:"C11",proposition:"P2",card_proposition:null,claim_text:"连续极限并不要求一致收敛",derivation:"QA"},review_revision:0,reviews:[],human_review_status:"pending"}];
  const html=app.renderChecking({checking},app.state.project);
  assert.match(html,/C11 · P2 · 案例级/);assert.match(html,/无绑定义务 · not_applicable/);
  assert.match(html,/声明的源文件字节 SHA256/);assert.match(html,/来源元数据 SHA256（不是文件字节）/);
  assert.doesNotMatch(html,/卡 v0|义务 null|接受为已证明/);
});

test("native source references remain separate from the primary and carry each support boundary",()=>{
  const {app,checking,target}=checkingHarness();
  checking.records=[{id:"multi",target,material:{claim_id:"C10",proposition:"P1",derivation:"QA",artifact_refs:[
    {artifact_id:"first",label:"Historical <script>x</script>",source_version:"v1",locator:"D1",support_scope:"仅原错误背景",origin:"historical_model",declared_original_bytes_sha256:null},
    {artifact_id:"second",label:"Source",source_version:"v2",locator:"p.5",support_scope:"仅定义",origin:"source_snapshot",declared_original_bytes_sha256:"known-declaration"}]},review_revision:0,reviews:[]}];
  const html=app.renderChecking({checking},app.state.project);
  for(const value of ["逐项材料引用（外部声明） · 2 项","historical_model","source_snapshot","仅原错误背景","仅定义","unknown / not_connected","&lt;script&gt;"])assert.ok(html.includes(value),value);
  assert.doesNotMatch(html,/<script>/);
});

test("case material POST preserves all declared references and null application-card label",async()=>{
  const {app,checking}=checkingHarness();const target=addCaseTarget(checking);
  const form={dataset:{projectId:"alpha",checkId:"new"},fields:{target_key:target.target_key,case_key:"case-key",card_proposition:"",proposition:"P2",claim_id:"C11",claim_text:"P2 original",artifact_id:"primary",original_bytes_sha256:"b".repeat(64),ref_0_artifact_id:"source-1",ref_0_label:"QA source",ref_0_source_version:"v1",ref_0_locator:"D6",ref_0_support_scope:"boundary only",ref_0_origin:"assistant_check"}};
  app.checkDraft("alpha","new",checking).ref_count=1;
  app.setFetchResponse(url=>url.endsWith("/checks")?{ok:false,status:409,json:async()=>({detail:"stale"})}:{ok:true,json:async()=>({card,checking})});
  await app.saveCheck(form);const body=JSON.parse(app.requests[0].options.body);
  assert.equal(body.target_scope,"case");assert.equal(body.case_key,"case-key");assert.equal(body.card_proposition,null);
  assert.equal(body.artifact.artifact_id,"primary");assert.equal(body.artifact_refs[0].artifact_id,"source-1");
  assert.equal(body.artifact_refs[0].original_bytes_sha256,null);assert.equal(body.artifact_refs[0].acquired_at,null);
  assert.equal(app.checkDrafts["alpha:new"].ref_0_support_scope,"boundary only");
});

test("initial project render does not pin an undefined checking revision before workspace load",()=>{
  const {app,checking}=checkingHarness();
  app.renderChecking({card},app.state.project);
  assert.equal(app.checkDrafts["alpha:new"],undefined);
  app.renderChecking({checking},app.state.project);
  assert.equal(app.checkDrafts["alpha:new"].expected_revision,0);
  assert.equal(app.checkDrafts["alpha:new"].context_token,"old-context");
});

test("checking material form binds actual target and escapes original material and review",()=>{
  const {app,checking,target}=checkingHarness();
  checking.records=[{id:"r",target,material:{claim_id:"C01",proposition:"P0",claim_text:"<script>original</script>",derivation:"<img onerror=x>",support_scope:"仅反例"},
    association_warning:"不同对象",stale_reasons:["card_changed"],review_revision:1,reviews:[{kind:"human_review",reviewer:"<svg onload=x>",scope:"D1",opinion:"<script>opinion</script>"}]}];
  const html=app.renderChecking({checking},app.state.project);
  assert.match(html,/所属卡、路线与义务/); assert.match(html,/实际论证对象/);
  assert.match(html,/&lt;script&gt;original/); assert.match(html,/&lt;script&gt;opinion/);
  assert.match(html,/旧关联/); assert.match(html,/义务 open · not_formally_verified/);
  assert.match(html,/checks\/r\/export\?format=json/);assert.match(html,/checks\/r\/export\?format=md/);
  assert.doesNotMatch(html,/<script>|<img onerror|<svg onload|data-action="theory-accept"/);
});

test("checking input persists project-specific draft across a fresh app instance",()=>{
  const {app,checking}=checkingHarness();
  const form={dataset:{projectId:"alpha",checkId:"new"},fields:{claim_text:"恢复原文",claim_id:"C02"}};
  app.captureCheckDraft(form);
  const fresh=appHarness(); fresh.store.set("yanzheng.checkDraft.alpha:new",app.store.get("yanzheng.checkDraft.alpha:new"));
  assert.equal(fresh.checkDraft("alpha","new",checking).claim_text,"恢复原文");
  assert.equal(fresh.checkDraft("beta","new",checking).claim_text,undefined);
  assert.equal(fresh.checkDraft("alpha","new",checking).request_id,app.checkDraft("alpha","new",checking).request_id);
});

test("checking 409 retains draft then explicit rebase GET updates token without submitting",async()=>{
  const {app,checking}=checkingHarness();
  const form={dataset:{projectId:"alpha",checkId:"new"},fields:{claim_text:"不能丢失",claim_id:"C03"}};
  const newer={...checking,revision:1,context_token:"new-context"};
  app.setFetchResponse(url=>url.endsWith("/checks")?{ok:false,status:409,json:async()=>({detail:"上下文已变化"})}:{ok:true,json:async()=>({card,card_version:2,checking:newer})});
  await app.saveCheck(form);
  const d=app.checkDraft("alpha","new",newer),id=d.request_id;
  assert.equal(d.claim_text,"不能丢失"); assert.equal(d.context_token,"old-context");
  assert.match(app.checkMessages.alpha,/草稿保留/);
  await app.rebaseCheck("new");
  assert.equal(d.claim_text,"不能丢失");assert.equal(d.context_token,"new-context");assert.equal(d.expected_revision,1);assert.notEqual(d.request_id,id);
  assert.equal(app.requests.filter(r=>r.options.method==="POST").length,1);
});

test("checking save clears only saved draft and prevents duplicate in-flight POST",async()=>{
  const {app,checking}=checkingHarness();
  app.checkDraft("beta","new",checking).claim_text="another project";
  const form={dataset:{projectId:"alpha",checkId:"new"},fields:{claim_id:"C01",claim_text:"正文",note_id:"",artifact_id:"",derivation:"推导"}};
  let release;
  app.setFetchResponse(url=>url.endsWith("/checks")?new Promise(resolve=>{release=resolve;}):{ok:true,json:async()=>({card,checking:{...checking,revision:1}})});
  const running=app.saveCheck(form); await app.saveCheck(form);
  assert.equal(app.requests.length,1);
  const body=JSON.parse(app.requests[0].options.body);assert.equal(body.artifact,null);assert.equal(body.note_id,null);
  release({ok:true,json:async()=>app.state.project});await running;
  assert.equal(app.store.has("yanzheng.checkDraft.alpha:new"),false);
  assert.equal(app.checkDrafts["beta:new"].claim_text,"another project");
});

test("review confirmation can be unchecked and review type stays explicit",async()=>{
  const {app,checking,target}=checkingHarness();checking.records=[{id:"r",target,review_revision:0,material:{},reviews:[]}];
  const form={dataset:{projectId:"alpha",checkId:"r"},fields:{kind:"human_review",scope:"D1",reviewer:"声称身份",opinion:"待核查",confirm_record_only:"on"}};
  app.captureCheckDraft(form);assert.equal(app.checkDrafts["alpha:r"].confirm_record_only,true);
  delete form.fields.confirm_record_only;app.captureCheckDraft(form);
  assert.equal(app.checkDrafts["alpha:r"].confirm_record_only,false);
  app.setFetchResponse(url=>url.endsWith("/reviews")?{ok:false,status:400,json:async()=>({detail:"必须确认"})}:{ok:true,json:async()=>({card,checking})});
  await app.saveCheck(form);
  assert.equal(JSON.parse(app.requests[0].options.body).kind,"human_review");
  assert.equal(JSON.parse(app.requests[0].options.body).confirm_record_only,false);
  assert.equal(app.checkDrafts["alpha:r"].opinion,"待核查");
});

test("checking preview remains local-only and cross-project form cannot save",async()=>{
  const {app}=checkingHarness();
  app.theoryContextPreviews.alpha={messages:[{role:"user",content:"原始消息"}],feedback_dispositions:[],context_characters:10,limit_characters:50000};
  const html=app.renderNextContext("alpha");assert.match(html,/核查材料与其审阅仅本地保存，不进入本次模型消息/);
  await app.saveCheck({dataset:{projectId:"beta",checkId:"new"},fields:{claim_text:"cross"}});
  assert.equal(app.requests.length,0);
});

test("C projection displays each source boundary, dependency and historical time without losing original",()=>{
  const {app,checking,target}=checkingHarness();
  const raw=JSON.stringify({format:"c-preserved-claim-fields-v1",derivation_verbatim:"取同一个 N",counterexample_check:"n≥N",depends_on_claim_ids:["C06"],unresolved_questions:["独立复核 pending"],historical_reviewed_at:"2026-10-17T20:02:04+08:00",evidence_refs:[
    {original_path:"DERIVATION.md",locator:{text:"D5.1"},support_boundary:"助手推导，非模型",byte_sha256:"a".repeat(64)},
    {original_path:"UW.html",locator:{text:"text15–20",locator_artifact_id:"extract-1"},support_boundary:"仅定义",historical_model_input_membership:"not_asserted_by_reference"}]});
  checking.records=[{id:"c",target,review_revision:0,reviews:[],material:{claim_id:"C07",proposition:"P1",derivation:raw}}];
  const html=app.renderChecking({checking},app.state.project);
  for (const value of ["取同一个 N","独立复核 pending","C06","D5.1","仅定义","extract-1","not_asserted_by_reference","原核查时间"])
    assert.ok(html.includes(value),value);
  assert.match(html,/完整依据、哈希和历史快照/);
  assert.match(html,/<details><summary>来源版本与原始定位声明<\/summary>/);
});

test("empty gaps, open obligations and formal status remain distinct", () => {
  const app = appHarness();
  const html = app.renderProofRoutes([route]);
  assert.match(html, /未识别额外数学缺口，仍待审阅/);
  assert.match(html, /待核查的证明义务/);
  assert.match(html, /义务是审查清单/);
  assert.match(html, /未形式核验/);
  assert.doesNotMatch(html, /已证明|需要解决的引理/);
});

test("proposal version belongs to that proposal or its own call, never latest workspace", () => {
  const app = appHarness();
  const project = { theory: { prompt_version: "latest-do-not-retrofit" }, model_calls: [{ id: "old", prompt_version: "historical-v1" }] };
  assert.equal(app.theoryProposalVersion({ source: "model", model_call_id: "old" }, project), "historical-v1");
  assert.equal(app.theoryProposalVersion({ source: "model", prompt_version: "v1.2", model_call_id: "old" }, project), "v1.2");
  assert.equal(app.theoryProposalVersion({ source: "model" }, project), "未记录");
  assert.match(app.theoryProposalVersion({ source: "curated_example" }, project), /不适用/);
  assert.equal(app.theoryProposalSource({}), "来源未记录");
});

test("rejected original steps and unverified review are inspectable and escaped", () => {
  const app = appHarness();
  const item = { id: "reviewed", status: "rejected", source: "model", prompt_version: '<img src=x onerror="bad()">',
    revised_card: card, proof_routes: [{ ...route, steps: ["原错误：任意δ取1−δ/2 <script>bad()</script>"] }],
    review_reason: '<svg onload="bad()">定义域需要复核', revision_reason: "原建议保留" };
  const html = app.renderTheoryProposalHistory({ proposals: [item] }, {});
  assert.match(html, /已拒绝/);
  assert.match(html, /原错误：任意δ取1−δ\/2/);
  assert.match(html, /历史建议/);
  assert.match(html, /不等于程序完成数学核验/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|<svg onload|<img src=x/);
});

test("review history shows last two explicit reasons while preview controls actual replay", () => {
  const app = appHarness();
  const proposals = ["old", "second", "third"].map((value, index) => ({ id: value, status: "rejected", researcher_reason: value, base_card_version: index + 1 }));
  proposals.push({ id: "accepted", status: "accepted", researcher_reason: "do-not-send" });
  const html = app.renderTheoryFeedback({ proposals });
  assert.match(html, /second/); assert.match(html, /third/);
  assert.doesNotMatch(html, />old<|do-not-send/);
  assert.match(html, /下一轮实际带入范围以材料预览为准/);
  app.theoryContextPreviews.alpha = {messages: [], feedback_dispositions: [
    {record_kind:"proposal_rejection", record_id:"second", included:false, exclusion:"stale_context"},
    {record_kind:"legacy_rejected_output", record_id:"third", included:false, exclusion:"missing_context_basis"}]};
  const preview = app.renderNextContext("alpha");
  assert.match(preview, /研究背景或证据上下文已变化/);
  assert.match(preview, /缺少生成时上下文依据/);
  assert.doesNotMatch(preview, /将带入原文及具体意见/);
  assert.match(html, /不是程序认定的数学事实/);
});

test("theory includes Lean in its existing screen and preserves saved problem values", () => {
  const app = appHarness();
  const target = { innerHTML: "" };
  app.targets.set("#experiments-content", target);
  app.state.project = { id: "alpha", theory: { card_version: 2, card, proof_routes: [route], proposals: [], evidence_notes: [] } };
  app.state.projects = [{ id: "alpha", title: "测试项目" }];
  app.renderTheory();
  assert.match(target.innerHTML, /id="lean-workspace"/);
  assert.match(target.innerHTML, /研究与审阅目标/);
  assert.match(target.innerHTML, /审阅与解释/);
  assert.doesNotMatch(target.innerHTML, /本轮尚未接入 Lean|data-view="lean"/);
});

test("late callbacks for other projects never replace active Lean panel", () => {
  const app = appHarness();
  app.state.project = { id: "alpha" };
  app.state.view = "experiments"; app.state.validationMode = "theory";
  const node = { outerHTML: "unchanged" };
  app.targets.set("#lean-workspace", node);
  app.controllerOptions.onChange("beta");
  assert.equal(node.outerHTML, "unchanged");
});

test("Lean result refresh updates only its panel, preserving the draft form", () => {
  const app = appHarness();
  app.state.project = { id: "alpha" }; app.state.view = "experiments"; app.state.validationMode = "theory";
  const node = { outerHTML: "before", contains: () => false, querySelectorAll: () => [] };
  const form = { fields: { title: "尚未保存的命题" }, innerHTML: "untouched" };
  app.targets.set("#lean-workspace", node); app.targets.set("#theory-card-form", form);
  app.controllerOptions.onChange("alpha");
  assert.match(node.outerHTML, /alpha/);
  assert.equal(form.fields.title, "尚未保存的命题"); assert.equal(form.innerHTML, "untouched");
});

test("busy Lean does not collect disabled form values into an empty draft", () => {
  const app = appHarness();
  app.state.project = { id: "alpha" }; app.setBusy(true);
  const draft = { card, baseVersion: 2 };
  app.state.theoryDrafts.alpha = draft;
  app.targets.set("#theory-card-form", { fields: {} });
  assert.equal(app.captureTheoryDraft(), draft);
});

test("app locks restore only app-owned disabled state", () => {
  const app = appHarness();
  const unavailable = { disabled: true, dataset: {} }, available = { disabled: false, dataset: {} };
  app.targets.set('all:#lean-workspace [data-action], #lean-case-picker', [unavailable, available]);
  app.state.mutating = true; app.syncMutationControls();
  assert.equal(available.disabled, true);
  app.state.mutating = false; app.syncMutationControls();
  assert.equal(available.disabled, false); assert.equal(unavailable.disabled, true);
});

test("Lean actions ignore mismatched project controls, active mutation and duplicate run", async () => {
  const app = appHarness();
  app.state.project = { id: "alpha" };
  await app.performLean("lean-run", { dataset: { projectId: "beta" } });
  app.state.mutating = true;
  await app.performLean("lean-run", { dataset: { projectId: "alpha" } });
  app.state.mutating = false; app.setBusy(true);
  await app.performLean("lean-run", { dataset: { projectId: "alpha" } });
  assert.deepEqual(app.calls, []);
});

test("API preserves structured unsaved result without turning 503 into success", async () => {
  const app = appHarness();
  const body = { code: "lean_save_failed", persisted: false, unsaved_entry: { id: "public-result" } };
  app.setFetchResponse({ ok: false, status: 503, json: async () => body });
  await assert.rejects(app.api("/api/projects/alpha/lean/runs"), error => {
    assert.equal(error.status, 503);
    assert.equal(error.body, body);
    return true;
  });
});

test("unsaved JSON download stays local and refuses another project's entry", async () => {
  const app = appHarness();
  app.state.project = { id: "alpha" };
  const entry = { id: "run_1", context: { project_id: "alpha" }, verification: { status: "passed" } };
  app.setUnsavedEntry(entry);
  app.targets.set("#toast", { classList: { toggle() {} } });
  app.downloadLeanUnsaved({ dataset: { projectId: "beta", runId: "run_1" } });
  assert.equal(app.downloads.length, 0);
  app.downloadLeanUnsaved({ dataset: { projectId: "alpha", runId: "run_1" } });
  assert.equal(app.downloads[1].filename, "lean-run_1-unsaved.json");
  assert.deepEqual(JSON.parse(await app.downloads[0].blob.text()), entry);
  assert.match(app.targets.get("#toast").textContent, /不代表结果已存入项目历史/);
  app.setUnsavedEntry({ ...entry, context: { project_id: "beta" } });
  app.downloadLeanUnsaved({ dataset: { projectId: "alpha", runId: "run_1" } });
  assert.equal(app.downloads.length, 2);
});

test("preview renders all three dispositions, escapes content and stays project scoped", () => {
  const app = appHarness();
  app.theoryContextPreviews.alpha = { messages: [{role:"user", content:'<script>raw</script> 原完整步骤'}],
    feedback_dispositions: [
      {record_kind:"proposal_rejection", record_id:"p", included:true},
      {record_kind:"legacy_rejected_output", record_id:"old", included:false, exclusion:"missing_context_basis"},
      {record_kind:"failure_record", source_call_id:'<img src=x>', included:false, exclusion:"stale_context"}]};
  const html = app.renderNextContext("alpha");
  assert.match(html, /提议审阅/); assert.match(html, /旧结构拒绝/); assert.match(html, /失败材料/);
  assert.match(html, /将带入原文及具体意见/); assert.match(html, /缺少生成时上下文依据/);
  assert.match(html, /&lt;script&gt;raw/); assert.doesNotMatch(html, /<script>|<img/);
  assert.doesNotMatch(app.renderNextContext("beta"), /原完整步骤|将带入/);
});

test("budget block disables generation and does not label selected material as sent", () => {
  const app = appHarness(); app.state.project = {id:"alpha"}; app.state.status = {model:{configured:true}};
  const button = {}; app.targets.set('[data-action="theory-propose"]', button);
  app.theoryContextPreviews.alpha = {blocked:true, detail:"超出50000字符", messages:[], feedback_dispositions:[
    {record_kind:"failure_record", record_id:"f", included:true}]};
  app.syncMutationControls(); assert.equal(button.disabled, true);
  assert.match(app.renderNextContext("alpha"), /适用，但总预算阻止发送/);
  assert.doesNotMatch(app.renderNextContext("alpha"), /将带入原文及具体意见/);
  assert.equal(app.requests.length, 0);
});

test("inspection performs GET only and draft prevents even that request", async () => {
  const app = appHarness(); app.state.project = {id:"alpha",theory:{card_version:1,card}};
  app.targets.set("#toast", {classList:{toggle(){}}});
  const button = {innerHTML:"检查",isConnected:true};
  app.state.theoryDrafts.alpha = {card,baseVersion:1};
  await app.inspectNextContext(button); assert.equal(app.requests.length, 0);
  delete app.state.theoryDrafts.alpha;
  const response = {blocked:false, messages:[{role:"user",content:"actual input"}],context_token:"token"};
  app.setFetchResponse({ok:true,json:async()=>response});
  await app.inspectNextContext(button);
  assert.equal(app.requests.length, 1); assert.match(app.requests[0].url, /next-context$/);
  assert.equal(app.requests[0].options.method, undefined);
  assert.equal(app.theoryContextPreviews.alpha, response);
});

test("feedback conflict preserves original draft id and reason; explicit rebase retains text", async () => {
  const app = appHarness();
  const record = {id:"f",review_revision:0,reviews:[],capture:{text:"original"}};
  app.state.project = {id:"alpha",theory:{card_version:1,card,failure_records:[record]}};
  app.state.validationMode = "theory";
  app.targets.set("#toast", {classList:{toggle(){}}});
  const draft = {reason:'具体错误 <script>x</script>', expected_revision:0,request_id:"same-submit"};
  app.failureDrafts["alpha:f"] = draft;
  app.store.set("yanzheng.failureDraft.alpha:f", JSON.stringify(draft));
  app.setFetchResponse(url => url.endsWith("/reviews")
    ? {ok:false,status:409,json:async()=>({detail:"审阅已更新"})}
    : {ok:true,json:async()=>({card_version:1,card,failure_records:[{...record,review_revision:1}]})});
  await app.saveFailureReview({dataset:{failureId:"f"}});
  assert.equal(app.failureDrafts["alpha:f"], draft);
  assert.deepEqual(JSON.parse(app.store.get("yanzheng.failureDraft.alpha:f")), draft);
  assert.equal(app.requests.filter(r=>r.options.method === "POST").length,1);
  assert.equal(JSON.parse(app.requests[0].options.body).request_id,"same-submit");
  assert.match(app.renderTheoryFailures(app.state.theoryWorkspaces.alpha, app.state.project), /保留草稿并同步审阅版本/);
  await app.saveFailureReview({dataset:{failureId:"f"}}, true);
  assert.equal(draft.reason,'具体错误 <script>x</script>'); assert.equal(draft.expected_revision,1);
  assert.notEqual(draft.request_id,"same-submit");
  assert.equal(app.requests.filter(r=>r.options.method === "POST").length,1);
});

test("saved feedback clears only its draft and preview; duplicate in-flight click is ignored", async () => {
  const app = appHarness(); const record = {id:"f",review_revision:0};
  app.state.project = {id:"alpha",theory:{card,failure_records:[record]}};
  app.targets.set("#toast", {classList:{toggle(){}}});
  app.failureDrafts["alpha:f"] = {reason:"reason",expected_revision:0,request_id:"id"};
  app.failureDrafts["beta:f"] = {reason:"other"};
  app.theoryContextPreviews.alpha = {context_token:"old"};
  let release;
  app.setFetchResponse(url => url.endsWith("/reviews") ? new Promise(resolve=>{release=resolve;})
    : {ok:true,json:async()=>({failure_records:[]})});
  const running = app.saveFailureReview({dataset:{failureId:"f"}});
  await app.saveFailureReview({dataset:{failureId:"f"}});
  assert.equal(app.requests.length,1);
  release({ok:true,json:async()=>app.state.project}); await running;
  assert.equal(app.failureDrafts["alpha:f"] ?? null,null);
  assert.equal(app.store.has("yanzheng.failureDraft.alpha:f"),false); assert.equal(app.theoryContextPreviews.alpha,undefined);
  assert.equal(app.failureDrafts["beta:f"].reason,"other");
});

test("feedback local recovery is keyed by project and original never has acceptance control", () => {
  const app = appHarness();
  app.store.set("yanzheng.failureDraft.alpha:f", JSON.stringify({reason:"恢复草稿",expected_revision:0,request_id:"id"}));
  const item = {id:"f",project_id:"alpha",source:"test_double",category:"structural_failure",review_revision:0,
    capture:{kind:"model_content",complete:true,text:"<script>原错误</script>"},reviews:[]};
  assert.equal(app.failureDraft("alpha", item).reason,"恢复草稿");
  assert.equal(app.failureDraft("beta", item).reason,"");
  const html = app.renderTheoryFailures({failure_records:[item]}, {id:"alpha"});
  assert.match(html, /恢复草稿/); assert.match(html, /&lt;script&gt;原错误/);
  assert.doesNotMatch(html, /data-action="theory-accept"|<script>/);
  assert.match(html, /\/api\/projects\/alpha\/theory\/failures\/f\/export/);
  assert.match(html, /\/export\?format=md/);
  assert.match(html, /导出本条 Markdown/);
});
const artifactRecord = () => ({id:"record-qa",material:{claim_id:"C11",proposition:"P1",claim_text:"QA",assumptions:"QA",support_scope:"QA",derivation:"QA",artifact:null,artifact_refs:[{artifact_id:"qa-html",declared_original_bytes_sha256:null,declared_acquired_at:null}]},target:{target_scope:"card",card_version:2,card_id:"alpha:theory-card",card_sha256:"h",card},reviews:[],review_revision:0,stale_reasons:[]});
const artifactButton = action => ({dataset:{action,projectId:"alpha",checkId:"record-qa",artifactId:"qa-html"}});
const artifactFact = status => ({id:"read-1",artifact_id:"qa-html",recorded_at:"2026-10-18T18:00:00+08:00",result:{status,byte_match:status === "verified",actual:{byte_sha256:"a".repeat(64),bytes:41},acquired_at:null,acquired_at_status:"unknown_not_inferred_from_mtime",preview:{status:"utf8_plain_text",text:'<script>window.qaExecuted=true</script>',truncated:false}}});

test("artifact metadata requires explicit action and never causes read or history mutation",async()=>{
  const {app,checking}=checkingHarness();const item=artifactRecord();checking.records=[item];checking.evidence_access={available:true};
  const initial=app.renderArtifactAccess(item,app.state.project,checking.evidence_access);
  assert.equal(app.requests.length,0);assert.match(initial,/已保存的原件核查历史 · 0/);
  app.setFetchResponse({ok:true,json:async()=>({status:"registered_metadata_only",read_status:"not_checked"})});
  await app.inspectArtifact(artifactButton("artifact-describe"));
  assert.equal(app.requests.length,1);assert.match(app.requests[0].url,/\/artifacts\/describe$/);
  assert.deepEqual(JSON.parse(app.requests[0].options.body),{artifact_id:"qa-html"});
  assert.equal(item.artifact_reads,undefined);assert.equal(app.downloads.length,0);
  assert.match(app.renderArtifactAccess(item,app.state.project,checking.evidence_access),/未读取字节/);
});

test("explicit artifact read refreshes saved history while preserving unsaved review draft",async()=>{
  const {app,checking}=checkingHarness();const item=artifactRecord(),fact=artifactFact("verified");checking.records=[item];
  const form={dataset:{projectId:"alpha",checkId:item.id},fields:{opinion:"尚未保存的具体审阅",reviewer:"QA",confirm_record_only:"on"}};
  app.targets.set("all:.checking-form",[form]);
  app.setFetchResponse(url=>url.endsWith("/read")?{ok:true,json:async()=>({saved:true,fact})}:{ok:true,json:async()=>({card,checking:{...checking,records:[{...item,artifact_reads:[fact]}]}})});
  await app.inspectArtifact(artifactButton("artifact-read"));
  assert.equal(app.requests.length,2);assert.match(app.requests[1].url,/\/theory$/);
  assert.equal(app.checkDrafts["alpha:record-qa"].opinion,"尚未保存的具体审阅");
  const saved=app.state.theoryWorkspaces.alpha.checking.records[0];assert.equal(saved.artifact_reads[0].id,fact.id);
  const html=app.renderArtifactAccess(saved,app.state.project,{available:true});
  assert.match(html,/已保存的历史读取事实：verified/);assert.match(html,/取得时间：unknown/);
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|<iframe/);
  assert.equal(app.downloads.length,0);
});

test("unsaved artifact fact survives new app instance and remains outside project saved history",async()=>{
  const {app,checking}=checkingHarness();const item=artifactRecord();checking.records=[item];
  app.setFetchResponse({ok:false,status:503,json:async()=>({detail:"未保存",saved:false,fact:artifactFact("verified")})});
  await app.inspectArtifact(artifactButton("artifact-read"));
  assert.equal(app.requests.length,1);assert.equal(item.artifact_reads,undefined);
  const fresh=appHarness();for(const [k,v] of app.store)fresh.store.set(k,v);
  const html=fresh.renderArtifactAccess(item,{id:"alpha"},{available:true});
  assert.match(html,/未保存到项目 · 本机保留结果/);assert.match(html,/已保存的原件核查历史 · 0/);
  assert.equal(fresh.unsavedArtifactFacts("beta",item.id).length,0);assert.equal(fresh.requests.length,0);
});

test("saved registry failure refreshes history without retrying read",async()=>{
  const {app,checking}=checkingHarness();const item=artifactRecord();checking.records=[item];const fact=artifactFact("registry_unavailable");
  app.setFetchResponse(url=>url.endsWith("/read")?{ok:false,status:503,json:async()=>({detail:"注册器不可用；失败事实已保存",saved:true,fact})}:{ok:true,json:async()=>({card,checking:{...checking,records:[{...item,artifact_reads:[fact]}]}})});
  await app.inspectArtifact(artifactButton("artifact-read"));
  assert.equal(app.requests.filter(r=>r.options.method==="POST").length,1);
  assert.equal(app.state.theoryWorkspaces.alpha.checking.records[0].artifact_reads[0].result.status,"registry_unavailable");
  assert.equal(app.unsavedArtifactFacts("alpha",item.id).length,0);
});

test("artifact actions reject cross-project buttons and duplicate in-flight reads",async()=>{
  const {app,checking}=checkingHarness();checking.records=[artifactRecord()];
  const other=artifactButton("artifact-read");other.dataset.projectId="beta";await app.inspectArtifact(other);assert.equal(app.requests.length,0);
  let release;app.setFetchResponse(()=>new Promise(resolve=>{release=resolve;}));
  const first=app.inspectArtifact(artifactButton("artifact-describe"));
  await app.inspectArtifact(artifactButton("artifact-read"));assert.equal(app.requests.length,1);
  release({ok:true,json:async()=>({status:"unregistered"})});await first;
  assert.equal(app.state.mutating,false);assert.equal(app.requests.length,1);
});

test("only explicit native or primary references get controls; source prose grants no artifact association",()=>{
  const {app}=checkingHarness();const item=artifactRecord();item.material.artifact_refs=[];item.material.source_reference="qa-html";
  let html=app.renderArtifactAccess(item,app.state.project,{available:true});assert.doesNotMatch(html,/data-action="artifact-read"/);
  item.material.artifact={artifact_id:'qa-<img src=x onerror=bad()>'};
  html=app.renderArtifactAccess(item,app.state.project,{available:true});assert.match(html,/data-action="artifact-read"/);
  assert.doesNotMatch(html,/<img src=x/);assert.equal(app.requests.length,0);
});

test("failed artifact result is saved as failure and never reported as a successful read",async()=>{
  const {app,checking}=checkingHarness();const item=artifactRecord();checking.records=[item];
  const fact=artifactFact("path_rejected");fact.result.byte_match=false;fact.result.preview={status:"unavailable",text:null};
  app.setFetchResponse(url=>url.endsWith("/read")?{ok:true,json:async()=>({saved:true,fact})}:{ok:true,json:async()=>({card,checking:{...checking,records:[{...item,artifact_reads:[fact]}]}})});
  await app.inspectArtifact(artifactButton("artifact-read"));
  const view=app.artifactViews["alpha:record-qa:qa-html"];
  assert.match(view.message,/核对未通过/);assert.match(view.message,/失败事实已保存/);
  assert.doesNotMatch(view.message,/读取成功|字节核对通过/);
  assert.equal(app.requests.filter(r=>r.options.method==="POST").length,1);
  const html=app.renderArtifactFact(fact);assert.match(html,/核对未通过/);
  assert.match(html,/核查时间/);assert.doesNotMatch(html,/实际读取：/);
});

test("an unverified failure cannot render an original preview even in collapsed JSON details",()=>{
  const app=appHarness(),fact=artifactFact("unreadable");
  fact.result.byte_match=false;fact.result.preview.text="DO_NOT_DISPLAY_PARTIAL_ORIGINAL";
  const html=app.renderArtifactFact(fact);
  assert.doesNotMatch(html,/artifact-preview|DO_NOT_DISPLAY_PARTIAL_ORIGINAL/);
  assert.equal(fact.result.preview.text,"DO_NOT_DISPLAY_PARTIAL_ORIGINAL","history object is not mutated by rendering");
});

test("contradictory verified history does not display an attested preview",()=>{
  const app=appHarness(),fact=artifactFact("verified");fact.result.byte_match=false;
  const html=app.renderArtifactFact(fact);assert.doesNotMatch(html,/artifact-preview/);
  assert.match(html,/核对未通过/);assert.doesNotMatch(html,/字节核对通过/);
});

test("metadata exception stays a lookup failure without reading or erasing the review draft",async()=>{
  const {app,checking}=checkingHarness();const item=artifactRecord();checking.records=[item];
  const form={dataset:{projectId:"alpha",checkId:item.id},fields:{opinion:"kept on metadata error",reviewer:"QA"}};
  app.targets.set("all:.checking-form",[form]);
  app.setFetchResponse({ok:false,status:503,json:async()=>({detail:"固定原件注册器不可用。",status:"registry_unavailable"})});
  await app.inspectArtifact(artifactButton("artifact-describe"));
  assert.equal(app.requests.length,1);assert.match(app.requests[0].url,/\/describe$/);
  assert.match(app.artifactViews["alpha:record-qa:qa-html"].message,/元数据查询未完成/);
  assert.equal(app.checkDrafts["alpha:record-qa"].opinion,"kept on metadata error");
  assert.equal(item.artifact_reads,undefined);assert.equal(app.downloads.length,0);
});

test("read conflict preserves draft and never automatically repeats the request",async()=>{
  const {app,checking}=checkingHarness();const item=artifactRecord();checking.records=[item];
  app.targets.set("all:.checking-form",[{dataset:{projectId:"alpha",checkId:item.id},fields:{opinion:"409 draft retained"}}]);
  app.setFetchResponse({ok:false,status:409,json:async()=>({detail:"上下文已变化"})});
  await app.inspectArtifact(artifactButton("artifact-read"));
  assert.equal(app.requests.length,1);assert.equal(app.checkDrafts["alpha:record-qa"].opinion,"409 draft retained");
  assert.doesNotMatch(app.artifactViews["alpha:record-qa:qa-html"].message,/字节核对通过/);
  assert.equal(item.artifact_reads,undefined);
});

test("direction preview panel exposes accept/reject, hashes and bound note without mutating the pending proposal",()=>{
  const app=appHarness();
  const pending={id:"proposal-1",source:"model",status:"pending",context_sha256:"c".repeat(64),base_map_sha256:"b".repeat(64),input_sha256:"i".repeat(64),
    diff:{stage:{before:"clarifying",after:"directions_ready"},questions:{before:[],after:[]},
      topics:{before:[{id:"old",title:"旧候选"}],after:[{id:"new-1",title:"新候选一"},{id:"new-2",title:"新候选二"}]},
      recommendation:{before:{topic_id:null,reason:""},after:{topic_id:"new-1",reason:"先复现基线。"}},
      search_queries:["randomized svd"],revision_note:"依据已确认阅读收窄。",
      bound_notes:[{topic_id:"new-1",source_id:"lit_run_S001",relation:"supports",note_id:"note-1",level:"abstract_checked",locator:"Abstract",note_sha256:"a".repeat(64)}]}};
  const project={id:"alpha",workflow:{pending_proposal:pending}};
  const html=app.renderDirectionProposal(project);
  assert.match(html,/待确认的方向修订/);
  assert.match(html,/data-action="direction-accept"/);
  assert.match(html,/data-action="direction-reject"/);
  assert.match(html,/旧候选/);assert.match(html,/新候选一/);
  assert.match(html,/note-1/);assert.match(html,/笔记 SHA256/);
  assert.match(html,/未登记原文版本\/指纹/);
  assert.match(html,new RegExp("c{64}"));assert.match(html,new RegExp("b{64}"));
  assert.equal(pending.status,"pending");assert.equal(project.direction_map,undefined);
  assert.equal(app.renderDirectionProposal({id:"alpha",workflow:{}}),"");
});

test("stale pending preview disables accept but keeps reject and labels the stale context",()=>{
  const app=appHarness();
  const pending={id:"proposal-stale",source:"model",status:"pending",context_sha256:"c".repeat(64),base_map_sha256:"b".repeat(64),input_sha256:"i".repeat(64),
    diff:{stage:{before:"clarifying",after:"directions_ready"},questions:{before:[],after:[]},
      topics:{before:[],after:[{id:"new-1",title:"新候选一"}]},recommendation:{before:{topic_id:null,reason:""},after:{topic_id:"new-1",reason:"理由。"}},
      search_queries:[],revision_note:"",bound_notes:[{note_id:"note-fp",relation:"supports",level:"abstract_checked",locator:"Abstract",note_sha256:"a".repeat(64),
        source_version:"arXiv v2",source_sha256:"d".repeat(64),source_sha256_scope:"user_declared_fulltext_file",source_fingerprint_caveat:"用户声明；程序未认证。"}]}};
  const staleHtml=app.renderDirectionProposal({id:"alpha",workflow:{pending_proposal:pending,pending_proposal_stale:true}});
  assert.match(staleHtml,/上下文已变化/);assert.match(staleHtml,/is-stale/);
  assert.match(staleHtml,/data-action="direction-accept"[^>]*disabled/);
  assert.match(staleHtml,/data-action="direction-reject"/);assert.doesNotMatch(staleHtml,/data-action="direction-reject"[^>]*disabled/);
  assert.match(staleHtml,/用户声明原文版本：arXiv v2/);assert.match(staleHtml,new RegExp("d{64}"));
  const freshHtml=app.renderDirectionProposal({id:"alpha",workflow:{pending_proposal:pending,pending_proposal_stale:false}});
  assert.doesNotMatch(freshHtml,/上下文已变化/);assert.doesNotMatch(freshHtml,/data-action="direction-accept"[^>]*disabled/);
});


test("decision trajectory keeps the full trail and escapes user supplied HTML", () => {
  const app = appHarness();
  const html = app.renderDecisionTrajectory({id:"alpha", stage:"topic_selected", direction_map:{
    selected_topic_id:"a", recommendation:{topic_id:"b"},
    intake:{known:{selection_reason:"<script>bad()</script>"}},
    topics:[{id:"a",title:"<img src=x>方向甲",question:"问题甲"}, {id:"b",title:"方向乙",question:"问题乙"}]
  }});
  assert.match(html,/候选 01/); assert.match(html,/候选 02/);
  assert.match(html,/推荐/); assert.match(html,/已选择/);
  assert.match(html,/最终选择/); assert.match(html,/&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.match(html,/&lt;img src=x&gt;方向甲/);
  assert.doesNotMatch(html,/<script>|<img src=x>/);
});

test("source rendering puts excerpts before bibliography and marks unbound relations", () => {
  const app = appHarness();
  const source = {id:"s1",title:"公开论文",authors:"研究者",year_or_version:"2026",relation:"supports",verification:"metadata_checked",origin:"curated_example"};
  const note = {source_id:"s1",content:"<b>摘录</b>",level:"abstract_checked",origin:"curated_example",locator:"Abstract"};
  const bound = app.renderSource(source,[note]);
  assert.ok(bound.indexOf("已记录阅读摘录") < bound.indexOf("研究者"));
  assert.match(bound,/&lt;b&gt;摘录&lt;\/b&gt;/); assert.doesNotMatch(bound,/<b>摘录<\/b>/);
  assert.doesNotMatch(bound,/未绑定阅读笔记/);
  const unbound = app.renderSource(source,[]);
  assert.match(unbound,/未绑定阅读笔记/); assert.match(unbound,/尚无已记录阅读摘录/);
});

test("direction sources bind local note IDs within their exact literature run", () => {
  const app = appHarness();
  const source = {id:"lit_run-a_S001",source_id:"S001",run_id:"run-a",title:"真实来源",verification:"fulltext_checked"};
  app.state.project = {reading_notes:[
    {source_id:"S001",run_id:"run-a",content:"当前检索摘录",level:"fulltext_checked",locator:"PDF p26"},
    {source_id:"S001",run_id:"run-b",content:"其他检索摘录"},
    {source_id:"S001",content:"未限定检索摘录"}
  ]};
  const html = app.renderSources({sources:[source]});
  assert.match(html,/当前检索摘录/);
  assert.doesNotMatch(html,/尚无已记录阅读摘录|其他检索摘录|未限定检索摘录/);
  assert.doesNotMatch(app.renderSource({...source,run_id:"run-c"},app.state.project.reading_notes),/当前检索摘录|其他检索摘录|未限定检索摘录/);
});

test("decision package download uses the dedicated route and sanitizes server filenames", async () => {
  const app = appHarness();
  app.state.project = {id:"alpha"};
  app.setFetchResponse({ok:true, headers:{get:()=>'attachment; filename="../../unsafe.md"'}, blob:async()=>new Blob(["# package"],{type:"text/markdown"})});
  const button = {innerHTML:"导出",disabled:false,isConnected:false};
  await app.download("decision-package", button);
  assert.equal(app.requests.length,1);
  assert.equal(app.requests[0].url,"/api/projects/alpha/decision-package");
  const anchor = app.downloads.find(item=>item.filename);
  assert.equal(anchor.filename,"yanzheng-decision-package-alpha.md");
  assert.equal(await app.downloads.find(item=>item.blob).blob.text(),"# package");
  assert.equal(app.validAttachmentName("safe.md"),"safe.md");
  assert.equal(app.validAttachmentName("../bad.md"),null);
  assert.equal(app.targets.get("#toast").textContent,"研究决策包已导出。");
});


test("research journey counts only confirmed located notes owned by the project",()=>{
  const app=appHarness();
  const project={id:"alpha",stage:"directions_ready",direction_map:{topics:[]},literature_searches:[{id:"run",sources:[{id:"S001"}]}],reading_notes:[
    {run_id:"run",source_id:"S001",origin:"curated_example",confirmed:true,level:"abstract_checked",locator:"Abstract",content:"fixture"},
    {run_id:"other",source_id:"S001",confirmed:true,level:"abstract_checked",locator:"Abstract",content:"wrong project"}]};
  assert.equal(app.researchProgress(project).notes.length,0);
  assert.equal(app.researchProgress(project).current,2);
  project.reading_notes.push({run_id:"run",source_id:"S001",origin:"synthetic",confirmed:true,level:"abstract_checked",locator:"Abstract",content:"synthetic",checked_at:"2026-10-23T10:00:00Z"});
  assert.equal(app.researchProgress(project).notes.length,0);
  project.reading_notes.push({run_id:"run",source_id:"S001",origin:"user_attested",confirmed:true,level:"abstract_checked",locator:"Abstract",content:"confirmed",checked_at:"2026-10-23T10:00:00Z"});
  assert.equal(app.researchProgress(project).notes.length,1);
  assert.equal(app.researchProgress(project).current,3);
  project.workflow={pending_proposal:{id:"p"}};
  assert.equal(app.researchProgress(project).current,5);
});

test("action plan rendering stops stale paused and unreviewed actions and escapes queries",()=>{
  const app=appHarness();app.state.status={model:{configured:true}};
  const project={id:"alpha",stage:"directions_ready",direction_map:{topics:[]},research_plan:{id:"p",status:"paused",context_stale:true,
    used:{search_operations:1,model_operations:0},limits:{search_operations:3,model_operations:1},actions:[
      {id:"search-1",kind:"literature_search",title:"query",query:"<script>bad</script>",provider:"crossref",limit:3,status:"planned"},
      {id:"revise",kind:"direction_revision",title:"revise",status:"planned"}]}};
  const before=JSON.stringify(project),html=app.renderResearchActions(project);
  assert.match(html,/已暂停/);assert.match(html,/旧计划已停止/);assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);
  assert.doesNotMatch(html,/<script>bad/);
  assert.equal((html.match(/data-action="research-action-run"[^>]*disabled/g)||[]).length,2);
  assert.equal(JSON.stringify(project),before);
});

test("reference import presents metadata hash without implying fulltext review",()=>{
  const app=appHarness();
  const html=app.renderImportedMetadata({id:"S001",versions:["0909.4061v2"],abstract:"<untrusted>",import_metadata:{response_sha256:"a".repeat(64),retrieved_at:"2026-10-23T10:00:00Z",caveat:"仅题录，不是全文指纹。"}},{id:"run"},"alpha");
  assert.match(html,/0909.4061v2/);assert.match(html,/待核对草稿/);assert.match(html,/不是全文指纹/);
  assert.doesNotMatch(html,/已确认阅读|<untrusted>/);
  app.store.set("yanzheng.referenceDraft.alpha",'" autofocus onfocus="bad');
  const form=app.renderReferenceImport({id:"alpha"});
  assert.match(form,/&quot; autofocus onfocus=&quot;bad/);
  assert.match(form,/不下载全文或调用模型/);
});


test("project search and stage filters preserve research drafts and escape card labels",()=>{
  const app=appHarness();
  app.state.project={id:"current"};
  app.state.readingDrafts={current:{content:"keep my unfinished reading"}};
  app.state.projects=[
    {id:"current",title:"<img src=x> SVD",initial_idea:"randomized low rank",stage:"directions_ready",topic_count:3},
    {id:"next",title:"论文阅读",initial_idea:"citation accuracy",stage:"clarifying"},
    {id:"done",title:"SVD plan",stage:"topic_selected",selected_topic_title:"幂迭代"}];
  const before=JSON.stringify(app.state.readingDrafts);
  app.projectFilters.query="sVd";
  assert.deepEqual(Array.from(app.filteredProjects(),p=>p.id),["current","done"]);
  app.projectFilters.stage="directions_ready";
  assert.equal(app.filteredProjects().length,1);
  const html=app.renderProjectCards(app.filteredProjects());
  assert.match(html,/is-active-project/);assert.match(html,/&lt;img src=x&gt; SVD/);
  assert.doesNotMatch(html,/<img src=x>/);
  app.projectFilters.query="no matching research";
  assert.match(app.renderProjectCards(app.filteredProjects()),/清除筛选/);
  assert.equal(JSON.stringify(app.state.readingDrafts),before);
  assert.equal(app.state.project.id,"current");assert.equal(app.requests.length,0);
});

test("project filtering updates only result and count nodes",()=>{
  const app=appHarness();app.state.projects=[{id:"a",title:"Alpha",stage:"clarifying"},{id:"b",title:"Beta",stage:"topic_selected"}];
  const input={value:"Alpha",selectionStart:3,focused:true},results={innerHTML:""},count={textContent:""};
  app.targets.set("#project-search",input);app.targets.set("#project-results",results);app.targets.set("#project-filter-count",count);
  app.projectFilters.query="Alpha";app.renderProjectResults();
  assert.equal(count.textContent,"显示 1 / 2 个研究");assert.match(results.innerHTML,/Alpha/);assert.doesNotMatch(results.innerHTML,/Beta/);
  assert.equal(app.targets.get("#project-search"),input);assert.equal(input.selectionStart,3);assert.equal(input.focused,true);
});

test("research journey has one primary next action and honest reading progress",()=>{
  const app=appHarness();
  const project={id:"a",stage:"directions_ready",direction_map:{topics:[]},literature_searches:[{id:"r",sources:[{id:"s"}]}],reading_notes:[{origin:"curated_example",confirmed:true,run_id:"r",source_id:"s",content:"fixture",locator:"Abstract",level:"abstract_checked",checked_at:"2026-10-23T00:00:00Z"}]};
  const html=app.renderResearchJourney(project);
  assert.equal((html.match(/button button-primary/g)||[]).length,1);
  assert.match(html,/data-action="journey-read"/);assert.match(html,/0 条已确认笔记/);
  const overview=app.renderLiteratureOverview(project);
  assert.match(overview,/1 条公开案例预置笔记/);assert.match(overview,/<strong>0<\/strong><span>已确认阅读笔记/);
});

test("workspace restore rejects oversized and malformed files without sending or switching projects",async()=>{
  const app=appHarness();app.state.project={id:"kept"};
  const feedback={textContent:"",hidden:true,classList:{toggle(){}}};app.targets.set("#backup-feedback",feedback);
  const input={value:"chosen.json"};
  await app.restoreBackup({size:20*1024*1024+1,text:async()=>{throw Error("must not read");}},input);
  assert.match(feedback.textContent,/超过 20 MiB/);assert.equal(input.value,"");
  await app.restoreBackup({size:5,text:async()=>"{bad}"},input);
  assert.match(feedback.textContent,/不是可读取的 JSON/);assert.equal(app.requests.length,0);assert.equal(app.state.project.id,"kept");
  assert.equal(app.state.mutating,false);
});

test("workspace restore refreshes summaries without replacing the current project or drafts",async()=>{
  const app=appHarness();
  const current={id:"current",direction_map:{sources:[]}};app.state.project=current;
  app.state.readingDrafts={current:{content:"keep"}};app.state.answerDrafts={current:{q:"unfinished"}};
  const feedback={textContent:"",hidden:true,classList:{toggle(){}}};app.targets.set("#backup-feedback",feedback);
  for(const id of ["nav-project-count","top-project-count","stat-projects","stat-selected","stat-sources"])app.targets.set("#"+id,{textContent:""});
  app.setFetchResponse((url)=>({ok:true,json:async()=>url.endsWith("/restore")?{status:"restored",imported_count:1,skipped_count:2,warnings:["原行动计划已暂停"]}:{projects:[{id:"restored",title:"imported",stage:"clarifying"}]}}));
  const input={value:"backup.json"};await app.restoreBackup({size:42,text:async()=>'{"format":"qa","projects":[]}'},input);
  assert.equal(app.requests.length,2);assert.equal(app.requests[0].url,"/api/backups/restore");assert.equal(app.requests[1].url,"/api/projects");
  assert.match(feedback.textContent,/已恢复 1 个研究，跳过 2 个已有研究/);assert.match(feedback.textContent,/原行动计划已暂停/);
  assert.equal(app.state.project,current);assert.equal(app.state.readingDrafts.current.content,"keep");assert.equal(app.state.answerDrafts.current.q,"unfinished");
  assert.equal(input.value,"");assert.equal(app.state.mutating,false);
  const html=app.renderBackupControls();assert.match(html,/包含研究内容，请妥善保管/);assert.match(html,/不含应用配置中的模型密钥、原件文件/);assert.match(html,/同 ID 的现有记录保留/);
});

test("workspace restore sends original JSON so duplicate fields remain rejectable by the server",async()=>{
  const app=appHarness();app.state.project={id:"current"};
  const feedback={textContent:"",hidden:true,classList:{toggle(){}}};app.targets.set("#backup-feedback",feedback);
  const raw='{\n  "format": "first", "format": "second", "projects": []\n}\n';
  app.setFetchResponse({ok:false,status:400,json:async()=>({detail:"备份含重复字段，未写入任何研究。"})});
  await app.restoreBackup({size:raw.length,text:async()=>raw},{value:"duplicate.json"});
  assert.equal(app.requests.length,1);assert.equal(app.requests[0].url,"/api/backups/restore");
  assert.equal(app.requests[0].options.body,raw);
  assert.match(feedback.textContent,/重复字段/);assert.doesNotMatch(feedback.textContent,/已恢复/);
  assert.equal(app.state.project.id,"current");assert.equal(app.state.mutating,false);
});


function paperFixture(app) {
  const page={number:1,text:"A😀证据 <script>bad()</script>\n第二段",text_sha256:"b".repeat(64)};
  const doc={id:"doc-1",run_id:"run",source_id:"S001",filename:"论文 <img>.txt",media_type:"text/plain",byte_count:60,file_sha256:"a".repeat(64),text_sha256:"c".repeat(64),pages:[page],extraction:{kind:"utf8_text",version:1},original_available:true};
  const anchor={document_id:doc.id,page:1,start:1,end:3,quote:"😀证",file_sha256:doc.file_sha256,page_sha256:page.text_sha256};
  const project={id:"alpha",stage:"directions_ready",paper_documents:[doc],reading_notes:[],literature_searches:[{id:"run",sources:[{id:"S001",title:"Title"}]}],direction_map:{topics:[]}};
  app.state.project=project;app.state.projects=[{id:"alpha",title:"Title",stage:"directions_ready"}];
  return {page,doc,anchor,project};
}

test("document locators use Unicode codepoints and refuse wrong page hashes bindings and quote text",()=>{
  const app=appHarness(),{anchor,project}=paperFixture(app);
  assert.equal(app.inspectDocumentAnchor(anchor,project,{run_id:"run",source_id:"S001"}).available,true);
  for(const change of [{start:2},{end:99},{quote:"wrong"},{file_sha256:"d".repeat(64)},{page_sha256:""},{page:2},{document_id:"foreign"}])assert.equal(app.inspectDocumentAnchor({...anchor,...change},project).available,false);
  assert.equal(app.inspectDocumentAnchor(anchor,project,{run_id:"wrong",source_id:"S001"}).available,false);
  assert.equal(app.sameDocumentAnchor(anchor,Object.fromEntries(Object.entries(anchor).reverse())),true);
  const html=app.renderDocumentText("A😀证<script>",{start:1,end:3,quote:"😀证"});
  assert.match(html,/A<mark class="document-exact-highlight">😀证<\/mark>&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
});

test("document reader keeps uploads distinct from reading and restored text distinct from local original",()=>{
  const app=appHarness(),{doc,project}=paperFixture(app);
  let html=app.renderPaperDocuments({id:"S001"},{id:"run"},project);
  assert.match(html,/name="binding_confirmed"[^>]*required/);assert.match(html,/上传不算已读，不调用模型/);
  assert.match(html,/name="document_file"/);assert.match(html,/论文 &lt;img&gt;.txt/);assert.match(html,/documents\/doc-1\/original/);
  doc.original_available=false;
  html=app.renderPaperDocuments({id:"S001"},{id:"run"},project);
  assert.match(html,/文字快照/);assert.doesNotMatch(html,/href="[^"]*documents\/doc-1\/original/);
  delete doc.original_available;doc.backup_restore={restored_at:"now"};
  html=app.renderPaperDocuments({id:"S001"},{id:"run"},project);
  assert.match(html,/刷新研究确认原件/);assert.doesNotMatch(html,/href="[^"]*documents\/doc-1\/original/);
  assert.equal(app.requests.length,0);
});

test("editing a reading explanation preserves the exact unconfirmed excerpt independently",()=>{
  const app=appHarness(),{anchor}=paperFixture(app),key="alpha:run:S001";
  app.state.readingDrafts[key]={document_anchor:anchor,content:"previous",confirmed:false};
  const form={dataset:{draftKey:key},fields:{content:"my interpretation",locator:"page one",level:"fulltext_checked"},closest(){return this;}};
  app.captureReadingDraft(form);
  assert.equal(app.state.readingDrafts[key].content,"my interpretation");assert.equal(app.state.readingDrafts[key].document_anchor.quote,"😀证");
  assert.equal(app.state.readingDrafts[key].confirmed,false);
  const html=app.renderReadingNotes({id:"S001"},{id:"run"},"alpha");
  assert.match(html,/待确认的原文摘录/);assert.match(html,/my interpretation/);assert.match(html,/修改下面的解释不会改变原文摘录/);
  assert.doesNotMatch(html,/name="confirmed"[^>]*checked/);
});

function selectionFixture(app) {
  const fixture=paperFixture(app),node={textContent:fixture.page.text};
  const element={textContent:fixture.page.text,contains:value=>value===node};app.targets.set(".document-page-text",element);
  const reader={dataset:{projectId:"alpha",runId:"run",sourceId:"S001",documentId:"doc-1",page:"1"},querySelector:selector=>selector===".document-page-text"?element:null};
  app.document.createRange=()=>({selectNodeContents(){},setEnd(n,offset){this.prefix=n.textContent.slice(0,offset);},toString(){return this.prefix;}});
  const range={startContainer:node,endContainer:node,startOffset:1,endOffset:4,toString:()=>node.textContent.slice(1,4)};
  const selection={rangeCount:1,isCollapsed:false,getRangeAt:()=>range};
  return {...fixture,node,reader,range,selection,element};
}

test("actual-range excerpt conversion counts emoji once and rejects cross-region selections",()=>{
  const app=appHarness(),{reader,range,selection,element}=selectionFixture(app);
  const anchor=app.selectedDocumentAnchor(reader,selection);
  assert.equal(anchor.start,1);assert.equal(anchor.end,3);assert.equal(anchor.quote,"😀证");
  range.endContainer={};assert.throws(()=>app.selectedDocumentAnchor(reader,selection),/同一页/);
  assert.throws(()=>app.selectedDocumentAnchor(reader,{...selection,isCollapsed:true}),/非空文字/);
  range.endContainer=range.startContainer;element.textContent="tampered";
  assert.throws(()=>app.selectedDocumentAnchor(reader,selection),/文字与保存页不一致/);
});

test("placing an excerpt preserves prior explanation and leaves confirmation to the user",()=>{
  const app=appHarness(),{reader,selection}=selectionFixture(app),key="alpha:run:S001";
  const previous={document_id:"doc-1",page:1,start:0,end:1,quote:"A",file_sha256:"a".repeat(64),page_sha256:"b".repeat(64)};
  app.state.readingDrafts[key]={content:"keep existing interpretation",document_anchor:previous,level:"fulltext_checked",confirmed:true};
  app.targets.set("#literature-content",{innerHTML:""});
  app.putDocumentExcerpt(reader,selection);
  assert.equal(app.state.readingDrafts[key].content,"keep existing interpretation");assert.equal(app.state.readingDrafts[key].document_anchor.quote,"😀证");
  assert.equal(app.state.readingDrafts[key].confirmed,false);assert.equal(app.documentDraftBackups[key].document_anchor.quote,"A");
  assert.equal(app.requests.length,0);
});

test("saved notes expose exact quotes and reverse candidate links without relabeling explanation as quote",()=>{
  const app=appHarness(),{anchor,project}=paperFixture(app);
  const note={id:"n",run_id:"run",source_id:"S001",document_anchor:anchor,content:"my inference",level:"fulltext_checked",locator:"p1",confirmed:true,checked_at:"2026-10-23T10:00:00Z"};
  project.reading_notes=[note];project.direction_map={sources:[{id:"s",title:"paper"}],topics:[{id:"t",title:"候选甲",evidence:[{source_id:"s",note_id:"n",relation:"limits",claim:"scope only",note_snapshot:{note_id:"n",level:"fulltext_checked",document_anchor:anchor,content:"historical explanation"}}]}]};
  const html=app.renderReadingNotes({id:"S001"},{id:"run"},"alpha");
  assert.match(html,/原文摘录 · 与你的解释分别保存/);assert.match(html,/<blockquote>😀证<\/blockquote>/);assert.match(html,/my inference/);
  assert.match(html,/关联的候选判断/);assert.match(html,/限制/);assert.match(html,/候选甲/);assert.match(html,/旧上下文快照/);
  const evidence=app.renderEvidence(project.direction_map.topics[0],project.direction_map);
  assert.match(evidence,/historical explanation/);assert.match(evidence,/data-action="document-anchor-open"/);assert.match(evidence,/旧上下文快照/);
  assert.doesNotMatch(app.declaredFingerprintText(note),/未读取/);assert.match(app.declaredFingerprintText(note),/不能认证出版物或研究结论/);
});

test("saving a located note sends the exact quote separately from user interpretation",async()=>{
  const app=appHarness(),{anchor}=paperFixture(app),key="alpha:run:S001";
  app.state.readingDrafts[key]={document_anchor:anchor,content:"interpretation",confirmed:true};
  const button={innerHTML:"save",disabled:false,isConnected:false};
  const form={id:"",classList:{contains:value=>value==="reading-note-form"},dataset:{draftKey:key,runId:"run",sourceId:"S001"},fields:{content:"interpretation",locator:"client preview",level:"fulltext_checked",confirmed:"on"},reportValidity:()=>true,querySelector:()=>button};
  app.setFetchResponse({ok:false,status:400,json:async()=>({detail:"QA stop after payload inspection"})});
  await app.events.submit({target:form,preventDefault(){}});
  assert.equal(app.requests.length,1);
  const body=JSON.parse(app.requests[0].options.body);
  assert.equal(body.document_anchor.quote,"😀证");assert.equal(body.content,"interpretation");assert.equal(body.confirmed,true);assert.equal(body.document_anchor.start,1);assert.equal(body.document_anchor.end,3);
});

test("document uploads reject oversized or unconfirmed bindings before any request",async()=>{
  const app=appHarness();paperFixture(app);
  const feedback={textContent:"",hidden:true,classList:{toggle(){}}};
  const file={size:8*1024*1024+1,name:"paper.txt",type:"text/plain"},check={checked:true};
  const form={dataset:{projectId:"alpha",runId:"run",sourceId:"S001"},querySelector:selector=>selector.includes("document_file")?{files:[file]}:selector.includes("binding_confirmed")?check:feedback};
  await app.uploadPaperDocument(form);assert.match(feedback.textContent,/超过 8 MiB/);assert.equal(app.requests.length,0);
  file.size=5;check.checked=false;await app.uploadPaperDocument(form);assert.match(feedback.textContent,/确认它对应当前题录/);assert.equal(app.requests.length,0);
});


test("duplicate document uploads select matching uploaded bytes rather than the last unrelated document",async()=>{
  const app=appHarness(),bytes=Buffer.from("earlier uploaded original"),hash=require("node:crypto").createHash("sha256").update(bytes).digest("hex");
  const earlier={id:"earlier",file_sha256:hash},later={id:"later",file_sha256:"f".repeat(64)},added={id:"added",file_sha256:"a".repeat(64)};
  const file={arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};
  assert.equal((await app.uploadedDocumentSelection([earlier,later],[earlier,later],file)).id,"earlier");
  assert.equal((await app.uploadedDocumentSelection([earlier,later],[earlier,later,added],{arrayBuffer:()=>{throw Error("new ID should not need fingerprint");}})).id,"added");
  assert.equal(await app.uploadedDocumentSelection([],[],file),null);
});
