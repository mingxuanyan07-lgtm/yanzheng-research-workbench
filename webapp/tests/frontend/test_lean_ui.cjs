"use strict";

// Offline controller tests. These API responses are explicit fixtures, not real Lean execution.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const modulePath = path.resolve(__dirname, "../../static/lean-ui.js");
const { create } = require(modulePath);

const project = id => ({ id, theory: { card: { title: "研究草稿不应被更改" }, card_version: 2, proof_status: "not_formally_verified", proof_routes: [{ obligations: [{ status: "open" }] }] } });
const cases = [
  { case_id: "nat_add_zero", label: "自然数加零", statement: "∀ (n : Nat), n + 0 = n", fixture_kind: "valid_proof" },
  { case_id: "invalid_equality", label: "错误证明夹具", statement: "(0 : Nat) = 1", fixture_kind: "invalid_proof" },
  { case_id: "direct_sorry", label: "直接 sorry 夹具", statement: "(0 : Nat) = 1", fixture_kind: "unfinished_proof" },
  { case_id: "direct_axiom", label: "未认可公理夹具", statement: "(0 : Nat) = 1", fixture_kind: "unapproved_axiom" },
  { case_id: "native_decide_trust", label: "额外信任夹具", statement: "True", fixture_kind: "extra_trust" },
  { case_id: "timeout_fixture", label: "超时夹具", statement: "True", fixture_kind: "timeout" }
];
const environment = { available: true, toolchain: "leanprover/lean4:v4.33.1", lean_commit: "fixture-commit", mathlib_used: false, automatic_downloads: false, limiter_applied: true };
function entry(id = "run-1", projectId = "p", status = "passed", caseId = "nat_add_zero") {
  return {
    id,
    verification: {
      id: `verification-${id}`, case_id: caseId, status, created_at: "2026-10-16T00:00:00Z", duration_ms: 42,
      formal_statement: { source: cases.find(item => item.case_id === caseId)?.statement || "True", elaborated: "∀ n : Nat, n + 0 = n", elaborated_sha256: "formalhash" },
      source: { sha256: "sourcehash", files: [{ path: "cases/NatAddZero.lean", sha256: "filehash" }], reviewed_sources_match: true },
      environment,
      diagnostics: [],
      trust: { audit_completed: status === "passed", passed: status === "passed", policy_id: "fixture-policy", axioms: [], approved_axioms: ["propext"], sorry_axioms: [], unapproved_axioms: [], extra_dependencies: [], unsafe_dependencies: [], partial_dependencies: [], missing_dependencies: [], dependencies: ["Nat"] },
      execution: { compiler_started: true, exit_code: status === "passed" ? 0 : 1, resource_limits: { wall_seconds: 10 } },
      provenance: { execution_state: "confirmed_started" }
    },
    context: { project_id: projectId, card_version: 2, card_sha256: "cardhash" },
    association_status: "context_only_not_semantically_verified",
    context_matches_current: true
  };
}
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function unsavedEntry(id = "temporary-result", projectId = "p", status = "passed", caseId = "nat_add_zero") {
  const value = entry(id, projectId, status, caseId);
  Object.assign(value.verification, { id, record_type: "research_lean_verification", schema_version: "1.0" });
  return value;
}
function savingError(temporary, overrides = {}) {
  return Object.assign(new Error("结果写入失败"), {
    status: 503,
    body: { code: "lean_save_failed", persisted: false, unsaved_entry: temporary, ...overrides }
  });
}
function memoryStorage() { const items = new Map(); return { getItem: key => items.get(key) || null, setItem: (key, value) => items.set(key, value) }; }
function setup(handler, options = {}) {
  const calls = [], changes = [];
  const api = (url, request = {}) => {
    calls.push({ url, request });
    const result = handler?.(url, request, calls);
    if (result !== undefined) return Promise.resolve(result);
    if (url === "/api/lean/status") return Promise.resolve({ environment });
    if (url === "/api/lean/cases") return Promise.resolve({ cases });
    if (request.method === "POST") return Promise.resolve({ entry: entry("new-run", decodeURIComponent(url.split("/")[3]), "passed", JSON.parse(request.body).case_id) });
    return Promise.resolve({ runs: [] });
  };
  const controller = create({ api, onChange: id => changes.push(id), date: value => String(value || ""), storage: options.storage || memoryStorage() });
  return { controller, calls, changes };
}
const tick = () => Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve());

test("UMD exports a browser controller without requiring a document", () => {
  const window = {};
  vm.runInNewContext(fs.readFileSync(modulePath, "utf8"), { window, Map, Set, Promise });
  assert.equal(typeof window.YanzhengLean.create, "function");
  assert.throws(() => create({}), /api/);
});

test("initial and projectless states never imply execution or success", () => {
  const { controller, calls } = setup();
  assert.match(controller.render(null), /先保存并选择/);
  const html = controller.render(project("p"));
  assert.match(html, /环境尚未读取/);
  assert.match(html, /id="lean-case-picker"[^>]*disabled/);
  assert.match(html, /data-action="lean-run"[^>]*disabled/);
  assert.equal(controller.hasLoaded("p"), false);
  assert.equal(calls.length, 0);
});

test("load fetches independent metadata and per-project history once and renders empty state", async () => {
  const { controller, calls, changes } = setup();
  const p = project("p");
  assert.equal((await controller.load(p)).ok, true);
  assert.equal(controller.hasLoaded("p"), true);
  assert.deepEqual(calls.map(item => item.url).sort(), ["/api/lean/cases", "/api/lean/status", "/api/projects/p/lean/runs"].sort());
  assert.match(controller.render(p), /本项目尚无保存的 Lean 运行/);
  assert.match(controller.render(p), /固定工具链可用/);
  assert.ok(changes.length >= 2);
  await controller.load(p);
  assert.equal(calls.length, 3);
});

test("simultaneous loads reuse metadata and same-project requests", async () => {
  const blocked = deferred();
  const { controller, calls } = setup(url => url === "/api/lean/status" ? blocked.promise : undefined);
  const p = project("p");
  const first = controller.load(p), same = controller.load(p), second = controller.load(project("q"));
  assert.equal(first, same);
  await tick();
  assert.equal(calls.filter(item => item.url === "/api/lean/status").length, 1);
  assert.equal(calls.filter(item => item.url === "/api/lean/cases").length, 1);
  assert.equal(calls.filter(item => item.url.endsWith("/p/lean/runs")).length, 1);
  assert.match(controller.render(p), /正在读取环境/);
  blocked.resolve({ environment });
  await Promise.all([first, second]);
  assert.equal(controller.hasLoaded("q"), true);
});

test("environment GET failure does not hide already saved history or automatically retry", async () => {
  const old = entry();
  const { controller, calls } = setup(url => {
    if (url === "/api/lean/status") return Promise.reject(new Error("offline"));
    if (url.endsWith("/p/lean/runs")) return { runs: [old] };
  });
  const p = project("p");
  await controller.load(p);
  const html = controller.render(p);
  assert.match(html, /环境状态读取失败/);
  assert.match(html, /run-1/);
  assert.match(html, /data-action="lean-run"[^>]*disabled/);
  await controller.load(p);
  await controller.load(project("q"));
  assert.equal(calls.filter(item => item.url === "/api/lean/status").length, 1, "switching projects is not an automatic metadata retry");
  await controller.load(p, { force: true });
  assert.equal(calls.filter(item => item.url === "/api/lean/status").length, 2);
});

test("environment unavailable and empty directory prevent execution", async () => {
  const { controller, calls } = setup(url => {
    if (url === "/api/lean/status") return { environment: { available: false, error_code: "toolchain_missing" } };
    if (url === "/api/lean/cases") return { cases: [] };
  });
  await controller.load(project("p"));
  assert.match(controller.render(project("p")), /toolchain_missing/);
  assert.equal((await controller.run(project("p"))).reason, "unavailable");
  assert.equal(calls.filter(item => item.request.method === "POST").length, 0);
});

test("malformed metadata cannot enable execution and every GET settles independently", async () => {
  const { controller } = setup(url => {
    if (url === "/api/lean/status") return { environment: { available: "true" } };
    if (url === "/api/lean/cases") return { cases: [{ case_id: "a" }] };
    if (url.endsWith("/lean/runs")) return { runs: [entry()] };
  });
  await controller.load(project("p"));
  const html = controller.render(project("p"));
  assert.match(html, /环境接口返回格式不完整/);
  assert.match(html, /案例目录接口返回格式不完整/);
  assert.match(html, /最近保存的运行/);
  assert.equal((await controller.run(project("p"))).ok, false);
});

test("each non-proof fixture is visibly labelled as a test example", async () => {
  const { controller } = setup();
  const p = project("p");
  await controller.load(p);
  for (const item of cases.filter(item => item.fixture_kind !== "valid_proof")) {
    assert.equal(controller.chooseCase("p", item.case_id), true);
    assert.match(controller.render(p), /lean-selected-case[^]*测试示例/);
  }
});

test("all five statuses, unknown status, exact statements and elapsed time render distinctly", async () => {
  for (const [status, label] of [["passed", "形式陈述通过"], ["error", "核验错误或信任检查未通过"], ["incomplete", "证明未完成"], ["timeout", "核验超时"], ["unavailable", "执行环境不可用或忙碌"], ["unexpected", "未知状态 · 未确认核验"]]) {
    const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: [entry("run", "p", status)] } : undefined);
    await controller.load(project("p"));
    const html = controller.render(project("p"));
    assert.match(html, new RegExp(label));
    assert.match(html, /∀ \(n : Nat\), n \+ 0 = n/);
    assert.match(html, /42 ms/);
    if (status === "unexpected") assert.doesNotMatch(html, /lean-status-passed">形式陈述通过/);
  }
});

test("passed status with incomplete provenance is downgraded and cannot show a passed badge", async () => {
  const bad = entry();
  bad.verification.trust.passed = false;
  const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: [bad] } : undefined);
  await controller.load(project("p"));
  const html = controller.render(project("p"));
  assert.match(html, /记录待核查 · 不认定通过/);
  assert.doesNotMatch(html, /lean-status-passed">形式陈述通过/);
});

test("context match true, false and null never claim natural language equivalence", async () => {
  for (const [match, expected] of [[true, "已保存命题快照未变化；这不表示已确认数学语义对应"], [false, "历史上下文：当前已保存命题与本次运行快照不一致"], [null, "未关联命题快照"]]) {
    const value = entry();
    value.context_matches_current = match;
    const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: [value] } : undefined);
    await controller.load(project("p"));
    const html = controller.render(project("p"));
    assert.ok(html.includes(expected));
    assert.match(html, /本机草稿不参与判断/);
    assert.match(html, /不会关闭命题卡的待核查义务/);
  }
});

test("all server controlled text is escaped and download remains an encoded same-origin URL", async () => {
  const payload = '<img src=x onerror="alert(1)">';
  const id = 'p/" onmouseover="alert(1)';
  const bad = entry('run/" unsafe', id, "error");
  bad.verification.formal_statement.source = payload;
  bad.verification.formal_statement.elaborated = payload;
  bad.verification.diagnostics = [{ severity: payload, message: payload, pos: { line: payload } }];
  bad.verification.trust.axioms = [payload];
  bad.verification.source.files = [{ path: payload, sha256: payload }];
  bad.verification.error = { code: payload };
  bad.verification.created_at = payload;
  const { controller } = setup(url => {
    if (url === "/api/lean/cases") return { cases: [{ ...cases[0], label: payload, statement: payload }] };
    if (url.endsWith("/lean/runs")) return { runs: [bad] };
    if (url === "/api/lean/status") return { environment: { ...environment, toolchain: payload } };
  });
  await controller.load(project(id));
  const html = controller.render(project(id));
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.ok(html.includes(`/api/projects/${encodeURIComponent(id)}/lean/runs/${encodeURIComponent(bad.id)}/export?format=json`));
  assert.doesNotMatch(html, /href="(?:javascript:|https?:|\/\/)/);
});

test("strict POST contains only case_id and changes no card, obligations or project selection", async () => {
  const p = project("p");
  const original = JSON.stringify(p);
  const { controller, calls } = setup();
  await controller.load(p);
  const result = await controller.run(p);
  assert.equal(result.ok, true);
  const posted = calls.filter(item => item.request.method === "POST");
  assert.equal(posted.length, 1);
  assert.deepEqual(posted[0].request, { method: "POST", body: '{"case_id":"nat_add_zero"}' });
  assert.equal(JSON.stringify(p), original);
  assert.equal(calls.length, 4, "successful run never triggers more requests or a second POST");
});

test("global running guard rejects repeated clicks and another project until result completes", async () => {
  const blocked = deferred();
  const { controller, calls } = setup((url, request) => request.method === "POST" ? blocked.promise : undefined);
  await controller.load(project("p"));
  await controller.load(project("q"));
  const first = controller.run(project("p"));
  assert.equal(controller.isBusy(), true);
  assert.equal(controller.isBusy("p"), true);
  assert.equal(controller.isBusy("q"), false);
  assert.equal((await controller.run(project("p"))).reason, "busy");
  assert.equal((await controller.run(project("q"))).reason, "busy");
  await tick();
  assert.equal(calls.filter(item => item.request.method === "POST").length, 1);
  assert.match(controller.render(project("q")), /另一项目正在执行核验/);
  blocked.resolve({ entry: entry("new", "p") });
  await first;
  assert.equal(controller.isBusy(), false);
});

test("late load and late run responses update only their source project", async () => {
  const history = deferred(), execution = deferred();
  const { controller } = setup((url, request) => {
    if (url.endsWith("/p/lean/runs")) return request.method === "POST" ? execution.promise : history.promise;
    if (url.endsWith("/q/lean/runs")) return { runs: [entry("q-only", "q")] };
  });
  const pending = controller.load(project("p"));
  await controller.load(project("q"));
  history.resolve({ runs: [entry("p-only", "p")] });
  await pending;
  assert.match(controller.render(project("p")), /p-only/);
  assert.doesNotMatch(controller.render(project("q")), /p-only/);
  const running = controller.run(project("p"));
  execution.resolve({ entry: entry("p-new", "p") });
  await running;
  assert.match(controller.render(project("p")), /p-new/);
  assert.doesNotMatch(controller.render(project("q")), /p-new/);
});

test("transport failure preserves selected case and previous output, blocks retry until explicit refresh", async () => {
  let postError = true;
  const { controller, calls } = setup((url, request) => {
    if (request.method === "POST" && postError) return Promise.reject(new Error("connection lost"));
    if (url.endsWith("/lean/runs") && !request.method) return { runs: [entry("old-result")] };
  });
  const p = project("p");
  await controller.load(p);
  controller.chooseCase("p", "direct_sorry");
  const result = await controller.run(p);
  assert.equal(result.reason, "unknown_execution");
  const html = controller.render(p);
  assert.match(html, /执行结果未知，先刷新历史，勿直接重复运行/);
  assert.match(html, /已有历史记录（不代表本次请求结果）/);
  assert.match(html, /old-result/);
  assert.match(html, /value="direct_sorry" selected/);
  assert.equal((await controller.run(p)).reason, "refresh_required");
  await controller.load(p);
  assert.equal(calls.filter(item => item.request.method === "POST").length, 1);
  await controller.load(p, { force: true });
  postError = false;
  assert.equal((await controller.run(p)).ok, true);
});

test("successful compiler error is a saved actual result, not an ambiguous transport error", async () => {
  const { controller } = setup((url, request) => request.method === "POST" ? { entry: entry("failed", "p", "error", JSON.parse(request.body).case_id) } : undefined);
  const p = project("p");
  await controller.load(p);
  const result = await controller.run(p);
  assert.equal(result.ok, true);
  assert.match(controller.render(p), /核验错误或信任检查未通过/);
  assert.doesNotMatch(controller.render(p), /执行结果未知，先刷新历史/);
});

test("wrong project or wrong case in POST response is never added to visible history", async () => {
  for (const bad of [entry("foreign", "q"), entry("foreign", "p", "passed", "direct_sorry"), {}]) {
    const { controller } = setup((url, request) => request.method === "POST" ? { entry: bad } : undefined);
    await controller.load(project("p"));
    assert.equal((await controller.run(project("p"))).reason, "unknown_execution");
    assert.doesNotMatch(controller.render(project("p")), /data-lean-run-id="foreign"/);
  }
});

test("wrong-project history is rejected and never leaks into another project panel", async () => {
  const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: [entry("foreign", "q")] } : undefined);
  await controller.load(project("p"));
  const html = controller.render(project("p"));
  assert.match(html, /运行历史读取失败/);
  assert.doesNotMatch(html, /foreign/);
});

test("failed history refresh keeps previous output and chosen case, and does not auto retry", async () => {
  let fail = false;
  const { controller, calls } = setup(url => url.endsWith("/lean/runs") ? fail ? Promise.reject(new Error("read failed")) : { runs: [entry("saved")] } : undefined);
  const p = project("p");
  await controller.load(p);
  controller.chooseCase("p", "direct_axiom");
  fail = true;
  await controller.load(p, { force: true });
  const before = calls.length;
  const html = controller.render(p);
  assert.match(html, /saved/);
  assert.match(html, /value="direct_axiom" selected/);
  assert.match(html, /已缓存记录保留/);
  await controller.load(p);
  assert.equal(calls.length, before);
  assert.equal((await controller.run(p)).reason, "refresh_required");
});

test("case preferences persist separately per project even if storage is unavailable", async () => {
  const storage = memoryStorage();
  const first = setup(undefined, { storage }).controller;
  await first.load(project("p"));
  await first.load(project("q"));
  first.chooseCase("p", "direct_axiom");
  first.chooseCase("q", "direct_sorry");
  const second = setup(undefined, { storage }).controller;
  await second.load(project("p"));
  await second.load(project("q"));
  assert.match(second.render(project("p")), /value="direct_axiom" selected/);
  assert.match(second.render(project("q")), /value="direct_sorry" selected/);
  const broken = setup(undefined, { storage: { getItem() { throw Error("disabled"); }, setItem() { throw Error("disabled"); } } }).controller;
  await broken.load(project("p"));
  assert.equal(broken.chooseCase("p", "direct_sorry"), true);
});

test("obsolete case selection survives directory change but cannot execute", async () => {
  let smaller = false;
  const { controller } = setup(url => url === "/api/lean/cases" && smaller ? { cases: [cases[0]] } : undefined);
  const p = project("p");
  await controller.load(p);
  controller.chooseCase("p", "direct_sorry");
  smaller = true;
  await controller.load(p, { force: true });
  assert.match(controller.render(p), /原选择 direct_sorry/);
  assert.equal((await controller.run(p)).reason, "unavailable");
});

test("invalidating a card masks stale context and ignores superseded responses", async () => {
  let historyCount = 0;
  const oldResponse = deferred();
  const { controller } = setup(url => url.endsWith("/lean/runs") ? ++historyCount === 1 ? { runs: [entry("initial")] } : historyCount === 2 ? oldResponse.promise : { runs: [entry("fresh")] } : undefined);
  const p = project("p");
  await controller.load(p);
  const pending = controller.load(p, { force: true });
  await tick();
  controller.invalidate("p");
  assert.equal(controller.hasLoaded("p"), false);
  assert.match(controller.render(p), /上下文待重新读取/);
  const fresh = controller.load(p, { force: true });
  await fresh;
  oldResponse.resolve({ runs: [entry("stale")] });
  await pending;
  assert.match(controller.render(p), /fresh/);
  assert.doesNotMatch(controller.render(p), /data-lean-run-id="stale"/);
});

test("history and technical details start folded and have stable identifiers", async () => {
  const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: [entry("older"), entry("latest")] } : undefined);
  await controller.load(project("p"));
  const html = controller.render(project("p"));
  assert.match(html, /data-lean-detail="latest:diagnostics"/);
  assert.match(html, /data-lean-detail="latest:trust"/);
  assert.match(html, /data-lean-detail="latest:technical"/);
  assert.match(html, /data-lean-detail="p:history"/);
  assert.doesNotMatch(html, /<details[^>]*\sopen(?:\s|>)/);
  assert.match(html, /下载本次 JSON/);
  assert.match(html, /工具链二进制 SHA-256/);
  assert.match(html, /完整传递依赖/);
});

test("invalid history record safely degrades without fabricated statement or pass", async () => {
  const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: [{ id: "broken", context: { project_id: "p" } }] } : undefined);
  await controller.load(project("p"));
  const html = controller.render(project("p"));
  assert.match(html, /历史记录格式不完整/);
  assert.doesNotMatch(html, /lean-status-passed">形式陈述通过/);
});

test("synchronously thrown GET exceptions are settled without rejecting the whole load", async () => {
  const { controller } = setup(url => { if (url === "/api/lean/status") throw Error("sync failure"); });
  const result = await controller.load(project("p"));
  assert.equal(result.ok, false);
  assert.match(controller.render(project("p")), /sync failure/);
});

test("prototype-named status and fixture strings safely fall back instead of accessing inherited fields", async () => {
  const value = entry("unrecognized", "p", "constructor");
  const { controller } = setup(url => {
    if (url === "/api/lean/cases") return { cases: [{ ...cases[0], fixture_kind: "__proto__" }] };
    if (url.endsWith("/lean/runs")) return { runs: [value] };
  });
  await controller.load(project("p"));
  const html = controller.render(project("p"));
  assert.match(html, /未知状态 · 未确认核验/);
  assert.match(html, /类型待确认/);
  assert.doesNotMatch(html, /\[object Object\]/);
});

test("unknown POST outcome with empty local history never claims no saved server run", async () => {
  const { controller } = setup((url, request) => request.method === "POST" ? Promise.reject(new Error("HTTP 502 after persistence")) : undefined);
  const p = project("p");
  await controller.load(p);
  assert.equal((await controller.run(p)).reason, "unknown_execution");
  const html = controller.render(p);
  assert.match(html, /本次执行结果未知，缓存中暂无运行记录；请先刷新历史查看服务端结果/);
  assert.doesNotMatch(html, /本项目尚无保存的 Lean 运行/);
  assert.match(html, /data-action="lean-run"[^>]*disabled/);
});

test("panel uses section heading h2 and error code is described as diagnostic", async () => {
  const value = entry("error", "p", "error");
  value.verification.error = { code: "test_diagnostic" };
  const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: [value] } : undefined);
  await controller.load(project("p"));
  assert.match(controller.render(null), /<h2>Lean 固定引理核验（试点）<\/h2>/);
  const html = controller.render(project("p"));
  assert.match(html, /<h2>Lean 固定引理核验（试点）<\/h2>/);
  assert.match(html, /诊断代码：test_diagnostic/);
  assert.doesNotMatch(html, /执行代码：/);
});

test("GET save order is reversed for latest-first display without sorting fixture timestamps", async () => {
  const earlierSaved = entry("first-saved");
  earlierSaved.verification.created_at = "2026-10-16T12:00:00Z";
  const laterSaved = entry("last-saved");
  laterSaved.verification.created_at = "2020-01-01T00:00:00Z";
  const serverRuns = [earlierSaved, laterSaved];
  const { controller } = setup(url => url.endsWith("/lean/runs") ? { runs: serverRuns } : undefined);
  const p = project("p");
  await controller.load(p);
  const html = controller.render(p);
  assert.ok(html.indexOf('data-lean-run-id="last-saved"') < html.indexOf('data-lean-run-id="first-saved"'));
  assert.deepEqual(serverRuns.map(item => item.id), ["first-saved", "last-saved"], "normalizing does not mutate the API response array");
});

test("POST newest entry remains newest after refreshing append-ordered server history", async () => {
  const serverRuns = [entry("first-saved"), entry("second-saved")];
  const { controller } = setup((url, request) => {
    if (!url.endsWith("/lean/runs")) return undefined;
    if (request.method === "POST") { const value = entry("third-saved"); serverRuns.push(value); return { entry: value }; }
    return { runs: serverRuns };
  });
  const p = project("p");
  await controller.load(p);
  assert.equal((await controller.run(p)).reused, false);
  for (const refresh of [false, true]) {
    if (refresh) await controller.load(p, { force: true });
    const ids = Array.from(controller.render(p).matchAll(/data-lean-run-id="([^"]+)"/g), match => match[1]);
    assert.deepEqual(ids, ["third-saved", "second-saved", "first-saved"]);
  }
});

test("reused POST record keeps its saved position and is clearly distinguished from a new run", async () => {
  const original = entry("deduplicated");
  const newer = entry("newer", "p", "incomplete", "direct_sorry");
  const { controller, calls } = setup((url, request) => {
    if (!url.endsWith("/lean/runs")) return undefined;
    return request.method === "POST" ? { entry: original } : { runs: [original, newer] };
  });
  const p = project("p");
  await controller.load(p);
  const result = await controller.run(p);
  assert.equal(result.ok, true);
  assert.equal(result.reused, true);
  assert.match(result.message, /已有保存记录，未新增运行历史/);
  const html = controller.render(p);
  const ids = Array.from(html.matchAll(/data-lean-run-id="([^"]+)"/g), match => match[1]);
  assert.deepEqual(ids, ["newer", "deduplicated"]);
  assert.match(html, /服务返回已有保存记录，未新增运行历史/);
  assert.equal(calls.filter(item => item.request.method === "POST").length, 1);
});

test("explicit save failure preserves the real result separately and blocks another POST until manual refresh", async () => {
  const temporary = unsavedEntry();
  const original = JSON.stringify(temporary);
  const { controller, calls } = setup((url, request) => request.method === "POST" ? Promise.reject(savingError(temporary)) : undefined);
  const p = project("p");
  const projectBefore = JSON.stringify(p);
  await controller.load(p);
  const result = await controller.run(p);
  assert.equal(result.reason, "save_failed");
  assert.equal(result.hasUnsavedResult, true);
  const html = controller.render(p);
  assert.match(html, /未保存的临时结果（1）/);
  assert.match(html, /已取得核验结果，但未持久化/);
  assert.match(html, /data-lean-unsaved-id="temporary-result"/);
  assert.match(html, /data-action="lean-download-unsaved" data-project-id="p" data-run-id="temporary-result"/);
  assert.doesNotMatch(html, /data-lean-run-id="temporary-result"/);
  assert.doesNotMatch(html, /temporary-result\/export/);
  assert.match(html, /data-action="lean-run"[^>]*disabled/);
  assert.equal((await controller.run(p)).reason, "refresh_required");
  await controller.load(p);
  assert.equal(calls.filter(item => item.request.method === "POST").length, 1);
  assert.equal(JSON.stringify(controller.exportUnsaved("p", "temporary-result")), original);
  assert.equal(JSON.stringify(p), projectBefore);
});

test("temporary output and export are isolated by project and defensively copied", async () => {
  const temporary = unsavedEntry();
  const expected = JSON.parse(JSON.stringify(temporary));
  const { controller } = setup((url, request) => request.method === "POST" ? Promise.reject(savingError(temporary)) : undefined);
  await controller.load(project("p"));
  await controller.load(project("q"));
  await controller.run(project("p"));
  temporary.verification.formal_statement.source = "outside mutation";
  assert.deepEqual(controller.exportUnsaved("p", expected.id), expected);
  const exported = controller.exportUnsaved("p", expected.id);
  exported.verification.formal_statement.source = "download consumer mutation";
  assert.deepEqual(controller.exportUnsaved("p", expected.id), expected);
  assert.equal(controller.exportUnsaved("q", expected.id), null);
  assert.equal(controller.exportUnsaved("unknown", expected.id), null);
  assert.equal(controller.exportUnsaved("p", "unknown"), null);
  assert.doesNotMatch(controller.render(project("q")), /temporary-result|未保存的临时结果/);
});

test("successful empty or unrelated history refresh does not pretend temporary results were persisted", async () => {
  const temporary = unsavedEntry();
  let history = [];
  const { controller, calls } = setup((url, request) => {
    if (request.method === "POST") return Promise.reject(savingError(temporary));
    if (url.endsWith("/lean/runs")) return { runs: history };
  });
  const p = project("p");
  await controller.load(p);
  await controller.run(p);
  for (const records of [[], [entry("unrelated")]]) {
    history = records;
    await controller.load(p, { force: true });
    assert.deepEqual(controller.exportUnsaved("p", temporary.id), temporary);
    const html = controller.render(p);
    assert.match(html, /data-lean-unsaved-id="temporary-result"/);
    assert.doesNotMatch(html, /data-lean-run-id="temporary-result"/);
    assert.doesNotMatch(html, /已确认对应临时结果持久化/);
  }
  assert.equal(calls.filter(item => item.request.method === "POST").length, 1, "refresh never re-executes the proof");
});

test("only a valid same-ID server record moves a temporary result into persisted history", async () => {
  const temporary = unsavedEntry();
  let history = [];
  const { controller } = setup((url, request) => {
    if (request.method === "POST") return Promise.reject(savingError(temporary));
    if (url.endsWith("/lean/runs")) return { runs: history };
  });
  const p = project("p");
  await controller.load(p);
  await controller.run(p);
  for (const malformed of [
    { id: temporary.id, context: { project_id: "p" } },
    { ...temporary, verification: { ...temporary.verification, case_id: "direct_sorry" } },
    { ...temporary, verification: { ...temporary.verification, id: "other-verification" } }
  ]) {
    history = [malformed];
    await controller.load(p, { force: true });
    assert.deepEqual(controller.exportUnsaved("p", temporary.id), temporary);
  }
  const saved = { ...temporary, context_matches_current: false };
  history = [saved];
  await controller.load(p, { force: true });
  assert.equal(controller.exportUnsaved("p", temporary.id), null);
  const html = controller.render(p);
  assert.match(html, /data-lean-run-id="temporary-result"/);
  assert.match(html, /已确认对应临时结果持久化/);
  assert.match(html, /历史上下文/);
  assert.doesNotMatch(html, /未保存的临时结果|data-lean-unsaved-id|data-action="lean-download-unsaved"/);
});

test("failed history refresh and card invalidation retain unsaved output without asserting current context", async () => {
  const temporary = unsavedEntry();
  let failHistory = false;
  const { controller } = setup((url, request) => {
    if (request.method === "POST") return Promise.reject(savingError(temporary));
    if (url.endsWith("/lean/runs") && failHistory) return Promise.reject(new Error("history unavailable"));
  });
  const p = project("p");
  await controller.load(p);
  await controller.run(p);
  controller.invalidate("p");
  failHistory = true;
  await controller.load(p, { force: true });
  assert.deepEqual(controller.exportUnsaved("p", temporary.id), temporary);
  const html = controller.render(p);
  assert.match(html, /未保存结果仅保留运行时命题快照/);
  assert.doesNotMatch(html, /已保存命题快照未变化/);
  assert.match(html, /运行历史读取失败/);
  assert.equal((await controller.run(p)).reason, "refresh_required");
});

test("save failure without a result differs from transport uncertainty and still requires explicit refresh", async () => {
  const { controller, calls } = setup((url, request) => request.method === "POST" ? Promise.reject(savingError(null)) : undefined);
  const p = project("p");
  await controller.load(p);
  const result = await controller.run(p);
  assert.equal(result.reason, "save_failed");
  assert.equal(result.hasUnsavedResult, false);
  const html = controller.render(p);
  assert.match(html, /服务报告保存失败，未取得可展示的完整核验结果/);
  assert.match(html, /检查项目预约记录/);
  assert.doesNotMatch(html, /执行结果未知，先刷新历史|未保存的临时结果|data-action="lean-download-unsaved"/);
  assert.equal((await controller.run(p)).reason, "refresh_required");
  assert.equal(calls.filter(item => item.request.method === "POST").length, 1);
});

test("untrusted save-failure payloads cannot introduce foreign or malformed temporary results", async () => {
  const valid = unsavedEntry();
  const malformed = [
    unsavedEntry("foreign", "q"),
    unsavedEntry("wrong-case", "p", "passed", "direct_sorry"),
    { ...valid, verification: { ...valid.verification, id: "mismatch" } },
    { ...valid, verification: { ...valid.verification, schema_version: "unknown" } },
    { ...valid, verification: { ...valid.verification, status: "constructor" } },
    { ...valid, verification: { ...valid.verification, duration_ms: -1 } },
    { ...valid, association_status: "proved_current_card" },
    { ...valid, context: { project_id: "p" } },
    {}
  ];
  for (const temporary of malformed) {
    const { controller } = setup((url, request) => request.method === "POST" ? Promise.reject(savingError(temporary)) : undefined);
    const p = project("p");
    await controller.load(p);
    const result = await controller.run(p);
    assert.equal(result.reason, "save_failed");
    assert.equal(result.hasUnsavedResult, false);
    const html = controller.render(p);
    assert.match(html, /未展示或缓存该结果/);
    assert.doesNotMatch(html, /data-lean-unsaved-id|data-action="lean-download-unsaved"/);
  }
});

test("save-failure handling requires the exact HTTP and persistence markers", async () => {
  for (const overrides of [{ status: 502 }, { body: { code: "other", persisted: false, unsaved_entry: unsavedEntry() } }, { body: { code: "lean_save_failed", persisted: true, unsaved_entry: unsavedEntry() } }]) {
    const error = Object.assign(savingError(unsavedEntry()), overrides);
    const { controller } = setup((url, request) => request.method === "POST" ? Promise.reject(error) : undefined);
    const p = project("p");
    await controller.load(p);
    assert.equal((await controller.run(p)).reason, "unknown_execution");
    assert.equal(controller.exportUnsaved("p", "temporary-result"), null);
  }
});

test("temporary result text and button identifiers are escaped without losing exact export data", async () => {
  const payload = '<img src=x onerror="alert(1)">';
  const id = 'run/" onclick="alert(1)';
  const temporary = unsavedEntry(id);
  temporary.verification.formal_statement.source = payload;
  temporary.verification.diagnostics = [{ severity: "error", message: payload }];
  const { controller } = setup((url, request) => request.method === "POST" ? Promise.reject(savingError(temporary)) : undefined);
  const p = project("p");
  await controller.load(p);
  await controller.run(p);
  const html = controller.render(p);
  assert.doesNotMatch(html, /<img|data-run-id="run\/" onclick=/);
  assert.match(html, /&lt;img|data-run-id="run\/&quot;/);
  assert.deepEqual(controller.exportUnsaved("p", id), temporary);
});
