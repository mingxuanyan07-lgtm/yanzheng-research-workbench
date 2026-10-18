"use strict";

// Pure UI regression fixtures. These tests do not run Lean, a model, or HTTP.
const test = require("node:test");
const assert = require("node:assert/strict");
const { create } = require("../../static/lean-ui.js");

async function renderTrust(trust, limits, diagnostics = []) {
  const record = {
    id: "fixture-run", case_id: "ui-contract-fixture", status: diagnostics.length ? "error" : "passed",
    formal_statement: { source: "Fixture : Prop" }, duration_ms: 1,
    trust: { audit_completed: true, passed: true, ...trust },
    execution: { resource_limits: limits }, diagnostics,
  };
  const entry = {
    id: record.id, verification: record,
    context: { project_id: "fixture-project", card_version: null, card_sha256: null },
    association_status: "context_only_not_semantically_verified",
    context_matches_current: null,
  };
  const controller = create({
    storage: null,
    api: async path => path.endsWith("/status")
      ? { environment: { available: true } }
      : path.endsWith("/cases")
        ? { cases: [{ case_id: record.case_id, statement: record.formal_statement.source }] }
        : { runs: [entry] },
  });
  const project = { id: entry.context.project_id };
  await controller.load(project);
  return controller.render(project);
}

test("unrechecked imports and exact trust level remain explicit; trusted components are escaped", async () => {
  const html = await renderTrust({
    imported_modules_rechecked: false,
    independent_leanchecker_run: false,
    trusted_components: ["pinned imports", '<img src=x onerror="alert(1)">'],
  }, { kernel_trust_level: 1 });
  assert.match(html, /未重验预编译导入；这些导入属于本次信任基础/);
  assert.match(html, /<dt>Lean 内核信任级别<\/dt><dd>1<\/dd>/);
  assert.match(html, /<dt>独立 leanchecker 已运行<\/dt><dd>否<\/dd>/);
  assert.match(html, /pinned imports、&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /导入重验与信任基础见下方详情/);
  assert.match(html, /<details[^>]*data-lean-detail="fixture-run:trust"/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

test("a record reporting import recheck and another trust level is rendered without a hardcoded t1 claim", async () => {
  const html = await renderTrust({
    imported_modules_rechecked: true,
    independent_leanchecker_run: true,
    trusted_components: ["fixture audit"],
  }, { kernel_trust_level: 0 });
  assert.match(html, /按本次记录，预编译导入已重验/);
  assert.doesNotMatch(html, /未重验预编译导入|未运行独立 leanchecker/);
  assert.match(html, /<dt>Lean 内核信任级别<\/dt><dd>0<\/dd>/);
  assert.match(html, /<dt>独立 leanchecker 已运行<\/dt><dd>是<\/dd>/);
});

test("missing trust fields stay unknown instead of inventing a trust level or recheck result", async () => {
  const html = await renderTrust({}, undefined);
  assert.match(html, /未记录，不能确认预编译导入是否重验/);
  assert.match(html, /<dt>记录的信任组件<\/dt><dd>未记录<\/dd>/);
  assert.match(html, /<dt>Lean 内核信任级别<\/dt><dd>未记录<\/dd>/);
  assert.match(html, /<dt>独立 leanchecker 已运行<\/dt><dd>未记录<\/dd>/);
  assert.doesNotMatch(html, /按本次记录，预编译导入已重验|未重验预编译导入/);
});

test("compiler diagnostic positions use contract fields, retain legacy compatibility and escape locations", async () => {
  const html = await renderTrust({}, undefined, [
    { severity: "error", message: "contract diagnostic", position: { line: 2, column: 3 }, end_position: { line: 4, column: 5 }, pos: "ignored legacy value" },
    { severity: "warning", message: "legacy diagnostic", pos: '<svg onload="alert(1)">', endPos: "legacy end" },
  ]);
  assert.match(html, /定位：\{&quot;line&quot;:2,&quot;column&quot;:3\}；结束位置：\{&quot;line&quot;:4,&quot;column&quot;:5\}/);
  assert.match(html, /定位：&lt;svg onload=&quot;alert\(1\)&quot;&gt;；结束位置：legacy end/);
  assert.doesNotMatch(html, /<svg|ignored legacy value/);
});
