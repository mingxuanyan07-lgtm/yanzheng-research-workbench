/* Fixed-case Lean interface. Rendering and requests only; no DOM, model calls, or theory mutations. */
"use strict";

(function (root, factory) {
  const exported = factory();
  if (typeof module === "object" && module.exports) module.exports = exported;
  else root.YanzhengLean = exported;
})(typeof window !== "undefined" ? window : globalThis, function () {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const array = value => Array.isArray(value) ? value : [];
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key) ? value[key] : null;
  const valueText = value => value === null || value === undefined ? "未记录" : typeof value === "boolean" ? (value ? "是" : "否") : typeof value === "object" ? JSON.stringify(value) : String(value);
  const line = (title, value) => `<div class="lean-detail-row"><dt>${escape(title)}</dt><dd>${escape(valueText(value))}</dd></div>`;
  const nameList = value => Array.isArray(value) ? value.length ? value.map(valueText).join("、") : "无" : "未记录";
  const STATUS = {
    passed: ["形式陈述通过", "passed"],
    error: ["核验错误或信任检查未通过", "error"],
    incomplete: ["证明未完成", "incomplete"],
    timeout: ["核验超时", "timeout"],
    unavailable: ["执行环境不可用或忙碌", "unavailable"]
  };
  const FIXTURES = {
    valid_proof: "教学例题 · 固定形式证明",
    invalid_proof: "测试示例 · 错误证明",
    unfinished_proof: "测试示例 · 含直接或间接 sorry",
    unapproved_axiom: "测试示例 · 未认可公理",
    extra_trust: "测试示例 · 额外信任依赖",
    timeout: "测试示例 · 超时夹具"
  };

  function create(options) {
    if (!options || typeof options.api !== "function") throw new TypeError("Lean UI requires api(path, options).");
    const api = options.api;
    const notify = typeof options.onChange === "function" ? options.onChange : () => {};
    const formatDate = typeof options.date === "function" ? options.date : value => String(value ?? "");
    let storage = options.storage;
    if (storage === undefined) { try { storage = globalThis.localStorage; } catch { storage = null; } }
    const projects = new Map();
    let environment = null, environmentError = "", cases = [], caseError = "", catalogLoaded = false;
    let metadataPending = null, metadataGeneration = 0, metadataAttempted = false, activeRun = null;
    const idFor = project => typeof project?.id === "string" && project.id ? project.id : null;
    function get(id) {
      if (!projects.has(id)) {
        let chosen = "";
        try { chosen = storage?.getItem(`yanzheng.lean.case.${id}`) || ""; } catch { /* Optional local preference. */ }
        projects.set(id, { chosen, runs: [], unsaved: [], loaded: false, loading: false, historyError: "", runError: "", runNotice: "", saveFailure: "", refreshRequired: false, unknownOutcome: false, pending: null, generation: 0, contextStale: false });
      }
      return projects.get(id);
    }
    const path = id => `/api/projects/${encodeURIComponent(id)}/lean/runs`;
    const errorText = error => typeof error?.message === "string" ? error.message : "服务请求没有完成。";
    function changed(id) { notify(id); }
    function saveChoice(id, state) { try { storage?.setItem(`yanzheng.lean.case.${id}`, state.chosen); } catch { /* Preference failure never blocks verification. */ } }
    function repairChoice(id, state) {
      if (!state.chosen && cases.length) { state.chosen = cases[0].case_id; saveChoice(id, state); }
      // A previous user choice is preserved even when a refreshed catalog no longer contains it.
    }

    function loadMetadata(force) {
      if (metadataPending) return metadataPending;
      if (!force && metadataAttempted) return Promise.resolve();
      const generation = ++metadataGeneration;
      metadataAttempted = true;
      const task = Promise.allSettled([Promise.resolve().then(() => api("/api/lean/status")), Promise.resolve().then(() => api("/api/lean/cases"))]).then(results => {
        if (generation !== metadataGeneration) return;
        const [envResult, casesResult] = results;
        if (envResult.status === "fulfilled" && object(envResult.value?.environment) && typeof envResult.value.environment.available === "boolean") {
          environment = envResult.value.environment;
          environmentError = "";
        } else {
          environment = null;
          environmentError = envResult.status === "rejected" ? errorText(envResult.reason) : "环境接口返回格式不完整。";
        }
        if (casesResult.status === "fulfilled" && Array.isArray(casesResult.value?.cases)
            && casesResult.value.cases.every(item => object(item) && typeof item.case_id === "string" && item.case_id && typeof item.statement === "string")
            && new Set(casesResult.value.cases.map(item => item.case_id)).size === casesResult.value.cases.length) {
          cases = casesResult.value.cases;
          catalogLoaded = true;
          caseError = "";
        } else {
          caseError = casesResult.status === "rejected" ? errorText(casesResult.reason) : "案例目录接口返回格式不完整。";
          catalogLoaded = false;
        }
        // Shared metadata can change another visible project's availability; update every cached panel.
        for (const [id, state] of projects) { repairChoice(id, state); changed(id); }
      }).finally(() => { if (metadataPending === task) metadataPending = null; });
      metadataPending = task;
      return task;
    }

    function load(project, { force = false } = {}) {
      const id = idFor(project);
      if (!id) return Promise.resolve({ ok: false, message: "请先选择已保存的项目。" });
      const state = get(id);
      if (state.pending) return state.pending;
      if (state.loaded && !force) return Promise.resolve({ ok: !state.historyError && !caseError && !environmentError });
      const generation = ++state.generation;
      state.loading = true;
      changed(id);
      const task = Promise.allSettled([loadMetadata(force), Promise.resolve().then(() => api(path(id)))]).then(results => {
        if (generation !== state.generation) return { ok: false, reason: "superseded" };
        const historyResult = results[1];
        if (historyResult.status === "fulfilled" && Array.isArray(historyResult.value?.runs)
            && historyResult.value.runs.every(entry => object(entry) && entry.context?.project_id === id)) {
          // The API returns append/save order (newest last), not chronological fixture timestamps.
          state.runs = historyResult.value.runs.slice().reverse();
          const savedIds = new Set(state.unsaved.filter(temporary => state.runs.some(saved =>
            saved.id === temporary.id && isUnsavedEntry(saved, id, temporary.verification.case_id)
          )).map(temporary => temporary.id));
          const recovered = state.unsaved.filter(entry => savedIds.has(entry.id));
          // A readable history response alone does not establish that a temporary result was saved.
          state.unsaved = state.unsaved.filter(entry => !savedIds.has(entry.id));
          if (!state.unsaved.length && recovered.length) state.saveFailure = "";
          state.historyError = "";
          state.contextStale = false;
          // A successful explicit refresh resolves transport uncertainty; users can inspect the saved history.
          if (force) {
            state.unknownOutcome = false;
            state.refreshRequired = false;
            state.runError = "";
            state.runNotice = recovered.length ? "服务端历史已包含相同 ID，已确认对应临时结果持久化。" : "";
          }
        } else {
          state.historyError = historyResult.status === "rejected" ? errorText(historyResult.reason) : "运行历史接口返回格式不完整。";
        }
        state.loaded = true;
        repairChoice(id, state);
        return { ok: !state.historyError && !caseError && !environmentError };
      }).finally(() => {
        if (generation !== state.generation) return;
        state.loading = false;
        if (state.pending === task) state.pending = null;
        changed(id);
      });
      state.pending = task;
      return task;
    }

    function chooseCase(projectId, caseId) {
      if (typeof projectId !== "string" || typeof caseId !== "string" || activeRun?.projectId === projectId) return false;
      if (!catalogLoaded || caseError || !cases.some(item => item.case_id === caseId)) return false;
      const state = get(projectId);
      state.chosen = caseId;
      saveChoice(projectId, state);
      changed(projectId);
      return true;
    }

    function run(project) {
      const id = idFor(project);
      if (!id) return Promise.resolve({ ok: false, message: "请先选择已保存的项目。" });
      if (activeRun) return Promise.resolve({ ok: false, reason: "busy", message: "已有固定案例正在核验，请等待实际结果。" });
      const state = get(id);
      if (state.loading || !state.loaded || !catalogLoaded || caseError || environmentError || environment?.available !== true
          || !cases.some(item => item.case_id === state.chosen)) {
        return Promise.resolve({ ok: false, reason: "unavailable", message: "请先刷新环境、案例目录和运行历史，再选择可用的固定案例。" });
      }
      if (state.historyError || state.unknownOutcome || state.refreshRequired) return Promise.resolve({ ok: false, reason: "refresh_required", message: state.saveFailure ? "本次结果保存失败，请先下载临时结果并刷新历史，勿直接重复运行。" : "执行结果未知，先刷新历史，勿直接重复运行。" });
      const caseId = state.chosen;
      const marker = { projectId: id, caseId };
      activeRun = marker;
      state.runError = "";
      state.runNotice = "";
      for (const knownId of projects.keys()) changed(knownId);
      const task = Promise.resolve().then(() => api(path(id), { method: "POST", body: JSON.stringify({ case_id: caseId }) })).then(response => {
        const entry = response?.entry;
        if (!object(entry) || typeof entry.id !== "string" || !entry.id || !object(entry.verification)
            || entry.verification.case_id !== caseId || entry.context?.project_id !== id) {
          throw new Error("返回记录与本次固定案例或项目不一致，无法确认执行结果。");
        }
        // No project/theory object is touched. Only the server-returned verification entry is appended.
        const existingIndex = state.runs.findIndex(item => item?.id === entry.id);
        const reused = existingIndex >= 0;
        if (reused) {
          state.runs = state.runs.map((saved, index) => index === existingIndex ? entry : saved);
          state.runNotice = "服务返回已有保存记录，未新增运行历史；请以该记录的实际时间与结果为准。";
        } else state.runs = [entry, ...state.runs];
        state.unsaved = state.unsaved.filter(saved => saved.id !== entry.id);
        if (!state.unsaved.length) state.saveFailure = "";
        state.unknownOutcome = false;
        state.refreshRequired = false;
        state.runError = "";
        return { ok: true, entry, reused, ...(reused ? { message: state.runNotice } : {}) };
      }).catch(error => {
        const body = error?.body;
        if (error?.status === 503 && object(body) && body.code === "lean_save_failed" && body.persisted === false) {
          const temporary = body.unsaved_entry;
          const valid = isUnsavedEntry(temporary, id, caseId);
          if (valid) state.unsaved = [JSON.parse(JSON.stringify(temporary)), ...state.unsaved.filter(saved => saved.id !== temporary.id)];
          state.unknownOutcome = false;
          state.refreshRequired = true;
          state.runError = "";
          state.saveFailure = valid
            ? "服务报告保存失败：已取得核验结果，但未持久化。结果仅在本页面内存暂存，请立即下载 JSON；页面关闭或重新加载可能丢失。请先刷新历史，勿直接重复运行。"
            : temporary === null || temporary === undefined
              ? "服务报告保存失败，未取得可展示的完整核验结果。请先刷新历史并检查项目预约记录，勿直接重复运行。"
              : "服务报告保存失败，但附带结果与本项目、案例或核验合同不一致，未展示或缓存该结果。请先刷新历史并检查项目预约记录，勿直接重复运行。";
          return { ok: false, reason: "save_failed", message: state.saveFailure, hasUnsavedResult: valid };
        }
        state.unknownOutcome = true;
        state.refreshRequired = true;
        state.runError = `执行结果未知，先刷新历史，勿直接重复运行。${errorText(error)}`;
        return { ok: false, reason: "unknown_execution", message: state.runError };
      }).finally(() => {
        if (activeRun === marker) activeRun = null;
        for (const knownId of projects.keys()) changed(knownId);
      });
      return task;
    }

    function isUnsavedEntry(entry, projectId, caseId) {
      if (!object(entry) || typeof entry.id !== "string" || !entry.id || !object(entry.verification) || !object(entry.context)) return false;
      const record = entry.verification, context = entry.context;
      return record.id === entry.id && record.case_id === caseId && context.project_id === projectId
        && record.record_type === "research_lean_verification" && record.schema_version === "1.0"
        && typeof record.status === "string" && !!own(STATUS, record.status)
        && object(record.formal_statement) && typeof record.formal_statement.source === "string" && !!record.formal_statement.source.trim()
        && object(record.source) && object(record.environment) && object(record.trust) && Array.isArray(record.diagnostics)
        && object(record.execution) && object(record.provenance)
        && typeof record.duration_ms === "number" && Number.isFinite(record.duration_ms) && record.duration_ms >= 0
        && (context.card_version === null || Number.isInteger(context.card_version) && context.card_version >= 0)
        && (context.card_sha256 === null || typeof context.card_sha256 === "string")
        && entry.association_status === "context_only_not_semantically_verified";
    }

    function exportUnsaved(projectId, runId) {
      const entry = projects.get(projectId)?.unsaved.find(entry => entry.id === runId);
      return entry ? JSON.parse(JSON.stringify(entry)) : null;
    }

    function invalidate(projectId) {
      if (!projects.has(projectId)) return;
      const state = get(projectId);
      state.generation++;
      state.loaded = false;
      state.loading = false;
      state.pending = null;
      state.contextStale = true;
      changed(projectId);
    }

    function details(key, title, content, extraClass = "") {
      return `<details class="lean-details ${extraClass}" data-lean-detail="${escape(key)}"><summary>${escape(title)}</summary>${content}</details>`;
    }
    function fixtureKind(record) {
      const item = cases.find(item => item.case_id === record?.case_id);
      return item?.fixture_kind || record?.fixture_kind || "unknown";
    }
    function fixtureLabel(kind) { return own(FIXTURES, kind) || "固定案例 · 类型待确认，暂不作为研究结论"; }
    function diagnosticLocation(item) {
      const start = item.position ?? item.pos;
      const end = item.end_position ?? item.endPos;
      if (start === null || start === undefined) return end === null || end === undefined ? "" : `<span class="lean-muted">结束位置：${escape(valueText(end))}</span>`;
      return `<span class="lean-muted">定位：${escape(valueText(start))}${end === null || end === undefined ? "" : `；结束位置：${escape(valueText(end))}`}</span>`;
    }
    function diagnosticView(record) {
      const diagnostics = array(record.diagnostics);
      if (!diagnostics.length) return `<p class="lean-muted">${record.status === "passed" ? "本次未记录编译诊断。" : "本次没有可用诊断；请查看执行及信任检查详情。"}</p>`;
      return `<ul class="lean-diagnostics">${diagnostics.map(item => object(item)
        ? `<li><span class="lean-diagnostic-level">${escape(item.severity || "未分类")}</span><pre class="lean-code">${escape(item.message || "未记录诊断正文")}</pre>${diagnosticLocation(item)}</li>`
        : `<li>${escape(valueText(item))}</li>`).join("")}</ul>`;
    }
    function trustView(record) {
      const trust = object(record.trust) ? record.trust : {};
      const status = trust.audit_completed === true ? (trust.passed === true ? "审计完成并通过" : "审计完成，未通过") : "审计未完成";
      const imports = trust.imported_modules_rechecked === false
        ? "未重验预编译导入；这些导入属于本次信任基础。"
        : trust.imported_modules_rechecked === true
          ? "按本次记录，预编译导入已重验。"
          : "未记录，不能确认预编译导入是否重验。";
      const fields = [
        ["实际使用公理", "axioms"], ["认可公理", "approved_axioms"], ["sorry 依赖", "sorry_axioms"],
        ["未认可公理", "unapproved_axioms"], ["额外信任依赖", "extra_dependencies"],
        ["unsafe 依赖", "unsafe_dependencies"], ["partial 依赖", "partial_dependencies"],
        ["缺失依赖", "missing_dependencies"], ["完整传递依赖", "dependencies"]
      ];
      return `<dl class="lean-detail-list">${line("信任检查", status)}${line("审计策略", trust.policy_id)}${line("预编译导入重验", imports)}${line("记录的信任组件", nameList(trust.trusted_components))}${line("Lean 内核信任级别", record.execution?.resource_limits?.kernel_trust_level)}${line("独立 leanchecker 已运行", trust.independent_leanchecker_run)}${fields.map(([label, key]) => line(label, nameList(trust[key]))).join("")}</dl><p class="lean-muted">请结合本次记录的工具链、导入重验与信任组件审阅；审计通过不消除这些信任边界。</p>`;
    }
    function technicalView(record, entry) {
      const env = object(record.environment) ? record.environment : {};
      const source = object(record.source) ? record.source : {};
      const execution = object(record.execution) ? record.execution : {};
      const formal = object(record.formal_statement) ? record.formal_statement : {};
      const context = object(entry.context) ? entry.context : {};
      const files = array(source.files);
      const dependencies = array(env.dependencies).map(valueText).join("；") || "未记录";
      return `<dl class="lean-detail-list">${line("记录 ID", entry.id)}${line("核验 ID", record.id)}${line("工具链", env.toolchain)}${line("Lean 构建", env.lean_commit)}${line("工具链二进制 SHA-256", env.binary_sha256)}${line("依赖", dependencies)}${line("mathlib 参与", env.mathlib_used)}${line("外部依赖", nameList(env.external_packages))}${line("准确展开类型 SHA-256", formal.elaborated_sha256)}${line("完整编译源码 SHA-256", source.sha256)}${line("已审查源码匹配", source.reviewed_sources_match)}${line("编译已启动", execution.compiler_started)}${line("退出码", execution.exit_code)}${line("进程组终止确认", execution.tree_termination_confirmed)}${line("资源限制", execution.resource_limits)}${line("执行状态", record.provenance?.execution_state)}${line("项目已保存卡版本", context.card_version)}${line("项目已保存卡 SHA-256", context.card_sha256)}${line("关联状态", entry.association_status)}</dl>
        ${formal.elaborated ? `<p class="lean-muted">Lean 实际展开的目标类型</p><pre class="lean-code">${escape(formal.elaborated)}</pre>` : ""}
        ${files.length ? `<p class="lean-muted">输入文件指纹</p><ul class="lean-file-list">${files.map(file => `<li>${escape(file?.path)}<code class="lean-hash">${escape(file?.sha256)}</code></li>`).join("")}</ul>` : ""}
        <p class="lean-muted">资源限制和凭据剥离不构成运行任意代码的通用安全沙箱。</p>`;
    }
    function renderRecord(entry, projectId, index, state, unsaved = false) {
      if (!object(entry) || !object(entry.verification)) return `<article class="lean-run-record lean-record-invalid"><p class="lean-error">第 ${index + 1} 条历史记录格式不完整，无法展示核验结论，请刷新或联系维护者。</p></article>`;
      const record = entry.verification;
      const kind = fixtureKind(record);
      const formal = object(record.formal_statement) ? record.formal_statement : {};
      const caseInfo = cases.find(item => item.case_id === record.case_id);
      const runId = typeof entry.id === "string" && entry.id ? entry.id : null;
      const detailKey = `${unsaved ? "unsaved:" : ""}${runId || `${projectId}-invalid-${index}`}`;
      const millis = typeof record.duration_ms === "number" && Number.isFinite(record.duration_ms) && record.duration_ms >= 0 ? `${record.duration_ms.toLocaleString("zh-CN", { maximumFractionDigits: 3 })} ms` : "未记录";
      let contextLabel = "未关联命题快照；自然语言与形式陈述的对应关系仍未确认。";
      if (unsaved) contextLabel = "未保存结果仅保留运行时命题快照；与当前已保存命题的关系尚未重新确认，也未确认数学语义对应。";
      else if (state.contextStale) contextLabel = "上下文待重新读取；请刷新后确认该运行与已保存命题的快照关系。";
      else if (entry.context_matches_current === false) contextLabel = "历史上下文：当前已保存命题与本次运行快照不一致。";
      else if (entry.context_matches_current === true) contextLabel = "已保存命题快照未变化；这不表示已确认数学语义对应。";
      const trustPassed = record.trust?.audit_completed === true && record.trust?.passed === true;
      const provenanceWarning = record.status === "passed" && (!trustPassed || typeof formal.source !== "string" || !formal.source.trim() || entry.context?.project_id !== projectId);
      const [statusLabel, statusClass] = provenanceWarning ? ["记录待核查 · 不认定通过", "unknown"] : own(STATUS, record.status) || ["未知状态 · 未确认核验", "unknown"];
      return `<article class="lean-run-record${unsaved ? " lean-unsaved-record" : ""}" ${unsaved ? "data-lean-unsaved-id" : "data-lean-run-id"}="${escape(runId || "")}">
        ${unsaved ? '<p class="lean-warning"><strong>未保存 · 本页面临时结果</strong>。服务明确报告未持久化；请下载 JSON 留存，此记录不属于已保存运行历史。</p>' : ""}
        <div class="lean-run-heading"><div><p class="lean-eyebrow">${escape(fixtureLabel(kind))}</p><h4>${escape(caseInfo?.label || record.case_id || "未记录案例")}</h4></div><span class="lean-status lean-status-${statusClass}">${escape(statusLabel)}</span></div>
        <p class="lean-muted">${escape(formatDate(record.created_at, true))} · 实际耗时 ${escape(millis)}</p>
        <p class="lean-formal-label">本次准确形式陈述</p><pre class="lean-code lean-statement">${escape(formal.source || "未记录准确形式陈述，不能据此认定核验通过。")}</pre>
        <p class="lean-context${entry.context_matches_current === false ? " lean-context-stale" : ""}">${escape(contextLabel)}</p>
        <p class="lean-trust-summary">${trustPassed ? "公理与信任审计：本次记录为通过。导入重验与信任基础见下方详情。" : record.trust?.audit_completed === true ? "公理与信任审计：本次记录未通过。" : "公理与信任审计：未完成，不能视为完整核验。"}</p>
        ${record.error?.code ? `<p class="lean-error">诊断代码：${escape(record.error.code)}</p>` : ""}
        ${provenanceWarning ? '<p class="lean-error">状态字段与记录内容不完整或不一致，请维护者核查；此处不认定通过。</p>' : ""}
        ${details(`${detailKey}:diagnostics`, `编译诊断（${array(record.diagnostics).length}）`, diagnosticView(record))}
        ${details(`${detailKey}:trust`, "公理与信任检查详情", trustView(record))}
        ${details(`${detailKey}:technical`, "工具链、哈希与执行详情", technicalView(record, entry))}
        ${runId ? unsaved ? `<button type="button" class="lean-button lean-secondary" data-action="lean-download-unsaved" data-project-id="${escape(projectId)}" data-run-id="${escape(runId)}">下载未保存结果 JSON</button>` : `<a class="lean-download" data-project-id="${escape(projectId)}" href="${path(projectId)}/${encodeURIComponent(runId)}/export?format=json" download>下载本次 JSON</a>` : ""}
      </article>`;
    }

    function render(project) {
      const id = idFor(project);
      if (!id) return '<section id="lean-workspace" class="panel lean-panel"><h2>Lean 固定引理核验（试点）</h2><p class="lean-empty">先保存并选择一个项目，再查看固定案例核验。</p></section>';
      const state = get(id);
      const busy = !!activeRun;
      const localBusy = activeRun?.projectId === id;
      const selected = cases.find(item => item.case_id === state.chosen);
      const runDisabled = busy || state.loading || !state.loaded || !catalogLoaded || !!caseError || !!environmentError || environment?.available !== true || !selected || state.unknownOutcome || state.refreshRequired || !!state.historyError;
      let environmentLabel = "环境尚未读取";
      if (environmentError) environmentLabel = "环境状态读取失败";
      else if (environment) environmentLabel = environment.available === true ? "固定工具链可用" : "当前执行环境不可用";
      const optionsHtml = cases.map(item => `<option value="${escape(item.case_id)}"${item.case_id === state.chosen ? " selected" : ""}>${escape(item.label || item.case_id)} · ${escape(fixtureLabel(item.fixture_kind))}</option>`).join("");
      const unavailableChoice = state.chosen && !selected ? `<option value="${escape(state.chosen)}" selected>原选择 ${escape(state.chosen)}（当前目录不可用，选择已保留）</option>` : "";
      const runs = state.runs;
      const historyHeading = state.unknownOutcome ? "已有历史记录（不代表本次请求结果）" : "最近保存的运行";
      return `<section id="lean-workspace" class="panel lean-panel" data-project-id="${escape(id)}" aria-busy="${state.loading || localBusy ? "true" : "false"}">
        <div class="lean-panel-heading"><div><p class="lean-eyebrow">可选形式核验</p><h2>Lean 固定引理核验（试点）</h2></div><span class="lean-status lean-status-${environment?.available === true && !environmentError ? "passed" : "unavailable"}">${escape(environmentLabel)}</span></div>
        <p class="lean-intro">手动运行已注册的固定案例。通过仅针对本次准确形式陈述，不会关闭命题卡的待核查义务，也不会将整张卡标为已证明。</p>
        <p class="lean-warning" role="note"><strong>受限交付提示：</strong>当前 Lean 超时可靠性未通过；历史记录出现过间歇超时，单次通过不等于稳定。演示或试用时请如实说明，不要把本试点作为正式稳定版能力展示。</p>
        <p class="lean-muted">关联依据项目已保存的命题，本机草稿不参与判断。固定案例与自然语言研究命题是否对应，仍需数学审阅。</p>
        <div class="lean-controls"><label class="lean-picker-label" for="lean-case-picker">注册案例<select class="lean-case-picker" id="lean-case-picker" data-project-id="${escape(id)}"${busy || state.loading || !catalogLoaded || caseError ? " disabled" : ""}>${unavailableChoice}${optionsHtml || '<option value="">案例目录尚不可用</option>'}</select></label>
          <div class="lean-action-row"><button type="button" class="lean-button lean-secondary" data-action="lean-refresh" data-project-id="${escape(id)}"${state.loading || busy ? " disabled" : ""}>${state.loading ? "正在读取…" : "刷新环境与历史"}</button><button type="button" class="lean-button lean-primary" data-action="lean-run" data-project-id="${escape(id)}"${runDisabled ? " disabled" : ""}>${localBusy ? "正在核验…" : "运行固定案例"}</button></div></div>
        ${selected ? `<div class="lean-selected-case"><p class="lean-eyebrow">${escape(fixtureLabel(selected.fixture_kind))}</p><pre class="lean-code">${escape(selected.statement)}</pre></div>` : ""}
        <div class="lean-notices" aria-live="polite">${state.loading ? '<p class="lean-muted">正在读取环境、案例目录和本项目运行历史；未启动核验。</p>' : ""}${localBusy ? '<p class="lean-progress">正在执行固定 Lean 案例，请等待实际记录；不会自动重试。</p>' : busy ? '<p class="lean-muted">另一项目正在执行核验，请等待其结束。</p>' : ""}
          ${environmentError ? `<p class="lean-error">${escape(environmentError)} 请手动刷新，已有运行历史仍可查看。</p>` : environment?.available === false ? `<p class="lean-warning">当前环境无法执行：${escape(environment.error_code || "工具链或受限执行条件未就绪")}。已有历史不受影响。</p>` : ""}
          ${caseError ? `<p class="lean-error">案例目录读取失败：${escape(caseError)}。选择已保留，请手动刷新。</p>` : ""}
          ${state.historyError ? `<p class="lean-error">运行历史读取失败：${escape(state.historyError)}。已缓存记录保留，刷新前不启动新核验。</p>` : ""}
          ${state.runError ? `<p class="lean-error">${escape(state.runError)}</p>` : ""}
          ${state.saveFailure ? `<p class="lean-error">${escape(state.saveFailure)}</p>` : ""}
          ${state.runNotice ? `<p class="lean-muted">${escape(state.runNotice)}</p>` : ""}
        </div>
        ${environment ? details(`${id}:environment`, "执行环境详情", `<dl class="lean-detail-list">${line("固定工具链", environment.toolchain)}${line("Lean 版本", environment.lean_version)}${line("Lean 构建", environment.lean_commit)}${line("资源限制已启用", environment.limiter_applied)}${line("资源限制方式", environment.limiter)}${line("mathlib 参与", environment.mathlib_used)}${line("自动下载", environment.automatic_downloads)}</dl>`) : ""}
        ${runs.length ? `<h4 class="lean-history-heading">${historyHeading}</h4>${renderRecord(runs[0], id, 0, state)}${runs.length > 1 ? details(`${id}:history`, `查看更早运行（${runs.length - 1}）`, runs.slice(1).map((entry, index) => renderRecord(entry, id, index + 1, state)).join(""), "lean-history") : ""}` : `<p class="lean-empty">${state.unknownOutcome ? "本次执行结果未知，缓存中暂无运行记录；请先刷新历史查看服务端结果。" : state.saveFailure ? "本次保存失败；已保存历史仍以服务端刷新结果为准。" : state.loaded && !state.historyError ? "本项目尚无保存的 Lean 运行。请选择固定案例，再手动核验。" : "本项目运行历史尚未读取完成。"}</p>`}
        ${state.unsaved.length ? `<div class="lean-unsaved-zone"><h3 class="lean-history-heading">未保存的临时结果（${state.unsaved.length}）</h3>${state.unsaved.map((entry, index) => renderRecord(entry, id, index, state, true)).join("")}</div>` : ""}
        <p class="lean-boundary">错误、未完成或超时不能说明命题为假；本试点不接受任意自然语言命题或用户代码，不调用千问。</p>
      </section>`;
    }
    return { render, load, run, chooseCase, invalidate, exportUnsaved, isBusy: projectId => projectId ? activeRun?.projectId === projectId : !!activeRun, hasLoaded: projectId => !!projects.get(projectId)?.loaded };
  }
  return { create };
});
