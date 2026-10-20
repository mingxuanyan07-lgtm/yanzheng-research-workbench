"use strict";

(() => {
  const state = { projects: [], project: null, view: "explore", status: null, statusLoading: false, opening: 0, mutating: false, generating: false, literatureBusy: false, experimentBusy: false, experimentLoading: false, experimentWorkspaces: {}, experimentDrafts: {}, readingDrafts: {}, answerDrafts: {}, materialDrafts: {}, literatureDrafts: {}, reasonDrafts: {}, library: null, libraryLoading: false, validationMode: "numeric", theoryWorkspaces: {}, theoryWorkspaceRevisions: {}, theoryDrafts: {}, theoryLoading: {}, theoryErrors: {}, theoryBusy: false };
  const activeKey = "yanzheng.activeProject";
  const draftKey = "yanzheng.intakeDraft";
  const theoryReviewDrafts = {};
  const leanUI = window.YanzhengLean?.create({ api, date, onChange: patchLeanWorkspace });
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const list = value => Array.isArray(value) ? value : value ? [value] : [];
  const text = value => list(value).join("；");
  const line = value => escape(text(value));
  const safeUrl = value => {
    try { const url = new URL(String(value)); return ["https:", "http:"].includes(url.protocol) ? escape(url.href) : null; } catch { return null; }
  };
  const localIconPaths = {
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".5"/>',
    cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3m6-3v3m-6 14v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    shield: '<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z"/><path d="m8 12 3 3 5-6"/>',
    orbit: '<circle cx="12" cy="12" r="2"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(-35 12 12)"/><path d="M8 3c2-1 6 3 9 8s3 9 1 10"/>',
    quote: '<path d="M5 8h5v6H6l-1 3H3l2-3V8Zm9 0h5v6h-4l-1 3h-2l2-3V8Z"/>',
    book: '<path d="M12 5c-3-2-7-2-10-1v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Zm0 0v15"/>',
    circle: '<circle cx="12" cy="12" r="8"/>'
  };
  const spriteNames = { folder: "folder-open", info: "circle-help", chevron: "chevron-down", compass: "compass", history: "history", sparkles: "sparkles", plus: "plus", check: "check", "arrow-right": "arrow-right", download: "download", menu: "menu", x: "x", "arrow-left": "arrow-left" };
  function icon(name) {
    const shape = spriteNames[name] ? `<use href="/static/assets/icons.svg#${spriteNames[name]}"></use>` : (localIconPaths[name] || localIconPaths.circle);
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shape}</svg>`;
  }
  function paintIcons(scope = document) { $$('[data-icon]', scope).forEach(element => { element.innerHTML = icon(element.dataset.icon); }); }
  function remember(id) { try { id ? localStorage.setItem(activeKey, id) : localStorage.removeItem(activeKey); } catch { /* Browser storage is optional; project content is stored on the server. */ } }
  function remembered() { try { return localStorage.getItem(activeKey); } catch { return null; } }
  function persistIntakeDraft() { try { localStorage.setItem(draftKey, JSON.stringify(Object.fromEntries(new FormData($("#idea-form"))))); } catch { /* Form drafts are optional; saved projects remain on the server. */ } }
  function restoreIntakeDraft() { try { const draft = JSON.parse(localStorage.getItem(draftKey) || "null"); if (draft && typeof draft === "object") for (const [name, value] of Object.entries(draft)) { const field = $("#idea-form").elements.namedItem(name); if (field && typeof value === "string") field.value = value; } } catch { /* Ignore obsolete or unavailable draft storage. */ } $("#idea-counter").textContent = `${$("#initial-idea").value.length} / 3000`; }
  function date(value, withTime = false) {
    if (!value) return "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value).replaceAll("-", "/");
    const dateValue = new Date(value);
    if (Number.isNaN(dateValue.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}) }).format(dateValue);
  }
  function stageInfo(stage) {
    return { clarifying: ["待澄清", ""], directions_ready: ["待选择方向", "mint"], topic_selected: ["首轮计划草案", "purple"] }[stage] || ["研究进行中", ""];
  }
  function stagePill(stage) { const [label, color] = stageInfo(stage); return `<span class="stage-pill ${color}">${label}</span>`; }
  let toastTimer;
  function toast(message, isError = false) { clearTimeout(toastTimer); $("#toast").textContent = message; $("#toast").classList.toggle("is-error", isError); $("#toast").hidden = false; toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4200); }
  function message(element, content, isError = true) { element.textContent = content; element.classList.toggle("is-error", isError); element.hidden = !content; }
  function leanBusy() { return leanUI?.isBusy() || false; }
  function patchLeanWorkspace(projectId) {
    if (state.project?.id !== projectId || state.view !== "experiments" || state.validationMode !== "theory") return;
    const previous = $("#lean-workspace");
    if (!previous || !leanUI) return;
    const focused = previous.contains(document.activeElement) ? document.activeElement : null;
    const focusId = focused?.id;
    const focusAction = focused?.dataset?.action;
    const expanded = Array.from(previous.querySelectorAll("details[data-lean-detail][open]"), item => item.dataset.leanDetail);
    previous.outerHTML = leanUI.render(state.project);
    const next = $("#lean-workspace");
    if (next) {
      next.querySelectorAll("details[data-lean-detail]").forEach(item => { item.open = expanded.includes(item.dataset.leanDetail); });
      const replacement = focusId ? $("#" + focusId, next) : focusAction ? Array.from(next.querySelectorAll("[data-action]")).find(item => item.dataset.action === focusAction) : null;
      if (replacement && !replacement.disabled) replacement.focus({ preventScroll: true });
    }
    syncMutationControls();
  }
  async function performLean(action, button) {
    const project = state.project;
    if (!project || !leanUI || button.dataset.projectId !== project.id) return;
    if (state.mutating || state.literatureBusy || state.experimentBusy || leanBusy()) return;
    // Lean updates only its own panel; preserve the independent mathematical draft.
    captureTheoryDraft();
    try {
      const operation = action === "lean-run" ? leanUI.run(project) : leanUI.load(project, { force: true });
      syncMutationControls();
      const result = await operation;
      if (state.project?.id === project.id && result?.message) toast(result.message, result.ok === false);
    } catch {
      // The controller presents request errors per project; never auto-retry.
      if (state.project?.id === project.id) toast("本次核验请求未完成，请先刷新运行历史查看执行状态。", true);
    } finally { syncMutationControls(); }
  }
  async function api(path, options = {}) {
    let response;
    try { response = await fetch(path, { ...options, headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } }); }
    catch { throw new Error("暂时连接不到本地服务。请确认工作台已经启动，再重试。"); }
    let body;
    try { body = await response.json(); } catch { throw new Error("服务没有返回可读取的结果，请稍后重试。"); }
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : typeof body.error === "string" ? body.error : null;
      const error = new Error(detail || `这一步没有完成（${response.status}），你的已有记录仍然保留。`);
      // Preserve structured failure results (for example an executed Lean result
      // that the server could not persist) without treating them as success.
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  }
  function busy(button, label) {
    const previous = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner" aria-hidden="true"></span>${escape(label)}`;
    return () => { if (button.isConnected) { button.disabled = false; button.innerHTML = previous; } };
  }
  function activateValidationProject(project) {
    if (!project) { state.validationProjectId = null; return; }
    if (state.validationProjectId === project.id) return;
    let preferred = null;
    try { preferred = localStorage.getItem(`yanzheng.validationMode.${project.id}`); } catch { /* Use the project's research preference if browser storage is unavailable. */ }
    state.validationMode = ["numeric", "theory"].includes(preferred) ? preferred : project.intake_form?.goal === "theory" || project.theory?.card ? "theory" : "numeric";
    state.validationProjectId = project.id;
  }
  function chooseValidationMode(mode) {
    state.validationMode = mode;
    state.validationProjectId = state.project?.id || null;
    if (state.project) {
      try { localStorage.setItem(`yanzheng.validationMode.${state.project.id}`, mode); } catch { /* Keep the active tab for this browser session. */ }
    }
  }
  function setView(view, updateHash = true) {
    if (!["explore", "projects", "records", "literature", "experiments", "library", "about"].includes(view)) view = "explore";
    state.view = view;
    $$(".screen").forEach(screen => { screen.hidden = screen.id !== `screen-${view}`; });
    $$(".nav-item").forEach(button => { const active = button.dataset.view === view; button.classList.toggle("is-active", active); active ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current"); });
    $("#view-label").textContent = { explore: "方向探索", projects: "研究项目", records: "研究记录", literature: "文献工作台", experiments: "实验工作台", library: "案例与证据", about: "关于研证" }[view];
    $(".sidebar").classList.remove("is-open");
    $("#mobile-menu").setAttribute("aria-expanded", "false");
    $("#mobile-menu").setAttribute("aria-label", "展开导航");
    $("#mobile-menu").innerHTML = icon("menu");
    if (updateHash && location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
    if (view === "projects") renderProjects();
    if (view === "records") renderRecords();
    if (view === "literature") renderLiterature();
    if (view === "experiments") loadExperiments();
    if (view === "library") loadLibrary();
  }
  function refreshStats() {
    $("#nav-project-count").textContent = state.projects.length;
    $("#top-project-count").textContent = `${state.projects.length} 个研究项目`;
    $("#stat-projects").textContent = state.projects.length;
    $("#stat-selected").textContent = state.projects.filter(project => project.stage === "topic_selected" || project.selected_topic_title).length;
    $("#stat-sources").textContent = state.project?.direction_map?.sources?.length || 0;
  }
  async function refreshProjects(renderLiteratureView = true) {
    const result = await api("/api/projects");
    state.projects = Array.isArray(result.projects) ? result.projects : [];
    refreshStats();
    if (state.view === "projects") renderProjects();
    if (state.view === "records") renderRecords();
    if (state.view === "literature" && renderLiteratureView) renderLiterature();
    if (state.view === "experiments") renderExperiments();
  }
  function mapOf(project) { return project?.direction_map || {}; }
  function isQwenMap(map) { return ["qwen", "qwen_skill_workflow"].includes(map.provenance?.generator); }
  function modelStateLabel() {
    const model = state.status?.model;
    if (!model?.configured) return "千问未配置";
    if (model.state === "verified") return "千问最近调用成功";
    if (model.state === "error") return "千问最近调用失败";
    return "千问已配置 · 待验证";
  }
  function syncMutationControls() {
    const locked = state.mutating || state.literatureBusy || state.experimentBusy || leanBusy();
    $$('[data-action="select"], [data-action="reopen"], #answers-form button[type="submit"], .reading-note-form button[type="submit"], [data-sample]').forEach(button => { button.disabled = locked; });
    $$('[data-action="generate"], [data-action="advance"], [data-action="revise-from-literature"], #material-form button[type="submit"]').forEach(button => {
      button.disabled = locked || !state.status?.model?.configured;
      if (button.dataset.action === "revise-from-literature" && state.project?.stage === "topic_selected") button.disabled = true;
      if (button.dataset.action === "generate" || button.dataset.action === "advance") button.innerHTML = state.generating ? '<span class="spinner" aria-hidden="true"></span>千问正在处理' : `${icon("sparkles")}${state.project?.workflow?.state === "failed" ? "重试这一步" : state.project?.workflow?.mode === "skill" ? "请千问判断下一步" : "启用 Skill 科研流程"}`;
    });
    const literatureButton = $('#literature-form button[type="submit"]');
    if (literatureButton) literatureButton.disabled = locked;
    $$('[data-action="search-idea"]').forEach(button => { button.disabled = locked; });
    $$('[data-action="revise-from-experiments"]').forEach(button => { button.disabled = locked || !state.status?.model?.configured; });
    const experimentButton = $('#experiment-form button[type="submit"]');
    if (experimentButton) experimentButton.disabled = locked;
    $$('.checking-form input, .checking-form textarea, .checking-form select, .checking-form button, [data-action="artifact-describe"], [data-action="artifact-read"], #theory-card-form input, #theory-card-form textarea, #theory-card-form button, #theory-review-reason, [data-failure-review], [data-action="failure-review"], [data-action="failure-rebase"], [data-action="theory-preview"], [data-action="theory-accept"], [data-action="theory-reject"], [data-action="theory-example"], [data-action="theory-discard-draft"]').forEach(field => { field.disabled = locked; });
    const theoryPropose = $('[data-action="theory-propose"]');
    if (theoryPropose) theoryPropose.disabled = locked || !state.status?.model?.configured || !theoryContextPreviews[state.project?.id] || theoryContextPreviews[state.project?.id]?.blocked || !!theoryDraft(state.project?.id);
    const theoryAccept = $('[data-action="theory-accept"]');
    if (theoryAccept) theoryAccept.disabled = locked || !!(state.project && theoryDraft(state.project.id));
    const ideaButton = $("#create-button");
    if (ideaButton) ideaButton.disabled = locked;
    $$('#lean-workspace [data-action], #lean-case-picker').forEach(field => {
      // Restore only this app's lock; keep the panel's environment restrictions.
      if (locked) {
        if (!field.dataset.appLocked) field.dataset.appWasDisabled = String(field.disabled);
        field.dataset.appLocked = "true"; field.disabled = true;
      } else if (field.dataset.appLocked) {
        field.disabled = field.dataset.appWasDisabled === "true";
        delete field.dataset.appLocked; delete field.dataset.appWasDisabled;
      }
    });
    $$('#experiment-project, #record-project, #literature-project').forEach(field => { field.disabled = locked; });
    $$('#idea-form input, #idea-form textarea, #idea-form select, #answers-form textarea, #material-form textarea, .reading-note-form input, .reading-note-form textarea, .reading-note-form select, #experiment-form input').forEach(field => { field.disabled = locked; });
  }
  async function refreshModelStatus(button) {
    if (state.statusLoading) return;
    state.statusLoading = true;
    const done = button ? busy(button, "读取状态") : () => {};
    try {
      state.status = await api("/api/status");
      renderCapabilities();
      const panel = $("#model-generation-panel");
      if (panel) panel.outerHTML = renderGenerationPanel(mapOf(state.project));
      syncMutationControls();
      if (button) toast("模型配置状态已刷新，未调用模型。");
    } catch (error) { if (button) { message($("#global-message"), error.message); toast(error.message, true); } }
    finally { state.statusLoading = false; done(); }
  }
  function empty(symbol, title, description, button = true) { return `<div class="empty-state"><span class="empty-symbol">${icon(symbol)}</span><h2>${escape(title)}</h2><p>${escape(description)}</p>${button ? `<button type="button" class="button button-primary" data-action="new">开始一个研究 ${icon("arrow-right")}</button>` : ""}</div>`; }
  function projectHeader(project) {
    const origin = list(mapOf(project).topics).length ? `<span class="project-origin">${isQwenMap(mapOf(project)) ? "千问生成候选" : "本地模板候选"}</span>` : "";
    return `<div class="project-context"><div><span class="section-kicker">YOUR CURRENT RESEARCH</span><h2>${escape(project.title || project.initial_idea || mapOf(project).initial_idea)}</h2><div class="project-meta">${stagePill(project.stage || mapOf(project).stage)}${origin}<span>${escape(date(project.updated_at, true))} 更新</span></div></div><button class="text-button" data-action="new" type="button">新建研究 ${icon("plus")}</button></div>`;
  }
  function updateSteps(stage) {
    const current = stage === "topic_selected" ? 3 : stage === "directions_ready" ? 2 : 1;
    $$("#stepper li").forEach(item => { const number = Number(item.dataset.step); item.classList.toggle("is-current", number === current); item.classList.toggle("is-done", number < current); number === current ? item.setAttribute("aria-current", "step") : item.removeAttribute("aria-current"); });
    document.body.dataset.researchStage = state.project ? stage : "empty";
    document.dispatchEvent(new CustomEvent("yanzheng:stage", { detail: { stage: state.project ? stage : "empty", projectId: state.project?.id || null } }));
  }
  function renderProject() {
    const project = state.project;
    activateValidationProject(project);
    if (project && state.theoryWorkspaces[project.id] && state.theoryWorkspaceRevisions[project.id] !== project.updated_at) delete state.theoryWorkspaces[project.id];
    $("#intake-panel").hidden = !!project;
    $("#project-content").hidden = !project;
    if (!project) { $("#project-content").innerHTML = ""; updateSteps("clarifying"); refreshStats(); return; }
    const map = mapOf(project);
    const stage = project.stage || map.stage;
    const hasPending = !!project.workflow?.pending_proposal;
    let content = projectHeader(project) + renderBrief(project);
    if (hasPending) {
      content += renderDirectionProposal(project);
    } else {
      if (stage !== "topic_selected") content += renderGenerationPanel(map);
      if (stage === "clarifying") content += renderQuestions(map);
      else if (stage === "topic_selected") content += renderPlan(map);
      else content += renderDirections(map);
    }
    $("#project-content").innerHTML = content;
    syncMutationControls();
    updateSteps(stage);
    refreshStats();
  }
  function renderBrief(project) {
    const map = mapOf(project), form = project.intake_form || {}, intake = map.intake || {}, known = intake.known || {};
    const labels = { goal: { experimental: "动手做实验", theory: "理论与证明", comparison: "比较与评估" }, resources: { laptop: "个人电脑", gpu: "GPU / 云算力" }, time_horizon: { week: "一周以内", month: "一个月左右", flexible: "暂不设限" } };
    const pair = (label, value) => `<div><dt>${label}</dt><dd>${escape(value || "尚未提供")}</dd></div>`;
    const answers = list(known.clarification_answers);
    const materials = list(project.workflow?.provided_materials);
    const additions = `${answers.length ? `<section class="brief-saved-input"><h3>已保存的澄清问答</h3><dl>${answers.map(answer => pair(answer.question || answer.id, answer.answer)).join("")}</dl></section>` : ""}${materials.length ? `<section class="brief-saved-input"><h3>你补充的资料</h3>${materials.map(material => `<div><time>${escape(date(material.created_at, true))}</time><p>${escape(material.content)}</p></div>`).join("")}</section>` : ""}`;
    return `<details class="panel research-brief"><summary><span>${icon("folder")}<strong>研究简报</strong><small>输入、假设与待确认条件</small></span>${icon("chevron")}</summary><div class="brief-content"><h3>你提供的信息</h3><dl class="brief-grid">${pair("原始想法", map.initial_idea || form.initial_idea)}${pair("研究偏好", labels.goal[form.goal] || known.goal)}${pair("可用资源", labels.resources[form.resources] || text(known.resources))}${pair("首轮时间", labels.time_horizon[form.time_horizon] || known.time_horizon)}${pair("背景与约束", form.context || known.provided_context)}${pair("具体研究对象", known.research_object)}${pair("希望得到的结果", known.desired_result)}${pair("已有材料", known.available_material)}</dl>${additions}<div class="brief-uncertainties"><section><h3>原记录中的假设</h3>${list(intake.assumptions).length ? `${stringList(intake.assumptions)}<p class="brief-origin-note">如含执行状态说明，仅对应生成候选时点；后续查询以文献工作台日志为准。</p>` : '<p>当前记录没有额外科研假设。</p>'}</section><section><h3>仍待确认</h3>${list(intake.unknowns).length ? stringList(intake.unknowns) : '<p>当前没有记录额外待确认项；这不代表课题已经验证。</p>'}</section></div></div></details>`;
  }
  function renderQuestions(map) {
    const questions = list(map.questions);
    const skillMode = state.project?.workflow?.mode === "skill";
    const answers = { ...(state.project?.workflow?.pending_answers || {}), ...(state.answerDrafts[state.project?.id] || {}) };
    if (!questions.length) return '<section class="panel clarification-panel"><h2>你的想法已保存。</h2><p class="panel-subtitle">请重试上方流程，或补充资料后再请千问判断下一步。</p></section>';
    return `<section class="panel clarification-panel" aria-labelledby="clarify-title"><div class="panel-head"><div><span class="section-kicker">LET'S MAKE IT CLEAR</span><h2 id="clarify-title">先把问题问清楚。</h2></div><span class="panel-corner">?</span></div><p class="panel-subtitle">补充这 ${questions.length} 个问题，我们会判断是否已能形成候选。${skillMode ? "提交后会把简报与回答发送至百炼，消耗 API 额度。" : "本次采用本地演示规则，不调用模型。"}</p><form id="answers-form">${questions.map((question, index) => `<div class="question-field"><label for="answer-${index}"><span>${String(index + 1).padStart(2, "0")}</span>${escape(question.question)}</label><p>${escape(question.why_it_matters)}</p><textarea id="answer-${index}" name="${escape(question.id)}" required maxlength="2000" placeholder="说说你的情况；暂不确定也可以直接说明。">${escape(answers[question.id] || "")}</textarea></div>`).join("")}<div class="message" id="answers-message" role="alert" hidden></div><div class="form-actions"><span class="save-note">${icon("shield")}回答先保存，失败后可重试</span><button type="submit" class="button button-primary">${skillMode ? "提交回答并继续" : "整理演示候选"} ${icon("arrow-right")}</button></div></form></section>${map.next_action ? `<div class="next-action">${icon("compass")}<span>${escape(map.next_action)}</span></div>` : ""}`;
  }
  function renderDirectionProposal(project) {
    const pending = project?.workflow?.pending_proposal;
    if (!pending || typeof pending !== "object") return "";
    const diff = pending.diff || {};
    const stale = project?.workflow?.pending_proposal_stale === true;
    const stageLabel = value => ({ clarifying: "待澄清", directions_ready: "待选择方向", topic_selected: "已选题" }[value] || value || "未知");
    const titles = items => list(items).map(item => `${item.title || item.id || "未命名"}`).join("、") || "无";
    const questions = list(diff.questions?.after);
    const bound = list(diff.bound_notes);
    return `<section class="panel direction-proposal${stale ? " is-stale" : ""}" data-direction-proposal="${escape(pending.id)}">
      <div class="theory-section-head"><div><span class="section-kicker">REVIEW BEFORE APPLYING</span><h2>待确认的方向修订</h2></div><span class="stage-pill ${stale ? "purple" : "purple"}">${stale ? "上下文已变化 · 需重新生成" : (pending.source === "model" ? "模型建议" : "建议") + " · 尚未应用"}</span></div>
      ${stale ? '<div class="message is-error" role="alert"><strong>上下文已变化，需重新生成。</strong><p>本修订基于旧输入或旧证据；接受已禁用，可放弃本次修订后重新请求。旧地图和你的选择保持不变。</p></div>' : ""}
      <p class="theory-notice">接受前，当前方向地图、候选和你的选择都不会改变。若期间修改简报、补充资料、阅读笔记、方向选择或已有方向，本修订会过期，需要重新请求。</p>
      <div class="proposal-diff">
        <section><h3>阶段</h3><p>${escape(stageLabel(diff.stage?.before))} → <strong>${escape(stageLabel(diff.stage?.after))}</strong></p></section>
        <section><h3>候选（建议 ${list(diff.topics?.after).length}）</h3><p>当前：${escape(titles(diff.topics?.before))}</p><p>建议：${escape(titles(diff.topics?.after))}</p></section>
        ${questions.length ? `<section><h3>澄清问题</h3><ol>${questions.map(question => `<li>${escape(question.question || "")}</li>`).join("")}</ol></section>` : ""}
        ${diff.recommendation?.after?.reason ? `<section><h3>推荐（仅建议，不会自动选择）</h3><p>${escape(diff.recommendation.after.reason)}</p></section>` : ""}
        ${list(diff.search_queries).length ? `<section><h3>检索建议 · 尚未执行</h3>${stringList(diff.search_queries)}</section>` : ""}
        ${diff.revision_note ? `<section><h3>修订说明</h3><p>${escape(diff.revision_note)}</p></section>` : ""}
        ${bound.length ? `<section><h3>绑定阅读笔记（${bound.length}）</h3>${bound.map(note => `<p><strong>${escape(note.note_id)}</strong> · ${escape(note.relation)} · ${sourceVerification(note.level)} · 定位：${escape(note.locator || "未记录")} · 笔记 SHA256：${escape(String(note.note_sha256 || "").slice(0, 20))}…<br>${escape(declaredFingerprintText(note))}</p>`).join("")}</section>` : ""}
      </div>
      <div class="theory-actions"><button type="button" class="button button-primary" data-action="direction-accept" data-proposal-id="${escape(pending.id)}" ${stale ? "disabled" : ""}>${icon("check")}接受并应用修订</button><button type="button" class="button button-secondary" data-action="direction-reject" data-proposal-id="${escape(pending.id)}">放弃本次修订</button></div>
      <details class="theory-history"><summary>查看本次修订的输入与旧地图身份 ${icon("chevron")}</summary><p>上下文 SHA256：${escape(pending.context_sha256 || "未记录")}</p><p>旧方向地图 SHA256：${escape(pending.base_map_sha256 || "未记录")}</p><p>模型输入 SHA256：${escape(pending.input_sha256 || "未记录")}</p><p>提醒：接受会归档旧方向地图；推荐仍不是你的选择，字节/哈希一致也不构成科学核验。</p></details>
    </section>`;
  }
  async function performDirectionProposal(action, button) {
    if (!state.project || state.mutating || state.literatureBusy || state.experimentBusy || leanBusy()) return;
    const projectId = state.project.id, proposalId = button.dataset.proposalId;
    const pending = state.project.workflow?.pending_proposal;
    if (!pending || pending.id !== proposalId) { toast("待确认修订已变化，请刷新后重试。", true); return; }
    if (action === "accept" && state.project.workflow?.pending_proposal_stale === true) { toast("上下文已变化，需重新生成；接受已禁用，可放弃本次修订。", true); return; }
    state.mutating = true;
    const done = busy(button, action === "accept" ? "正在应用修订" : "正在放弃修订");
    syncMutationControls();
    try {
      const endpoint = action === "accept"
        ? `/api/projects/${encodeURIComponent(projectId)}/direction/proposals/${encodeURIComponent(proposalId)}/accept`
        : `/api/projects/${encodeURIComponent(projectId)}/direction/proposals/${encodeURIComponent(proposalId)}/reject`;
      const body = action === "accept" ? {} : { reason: "" };
      const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
      if (state.project?.id !== projectId) return;
      state.project = result.project || result;
      delete state.answerDrafts[projectId];
      delete state.materialDrafts[projectId];
      delete state.reasonDrafts[projectId];
      renderProject();
      await refreshProjects(false);
      message($("#global-message"), "");
      toast(action === "accept"
        ? "已接受并应用修订；旧方向已归档，推荐仍不是你的选择。"
        : "已放弃本次修订；当前方向地图和你的选择保持不变。", false);
    } catch (error) {
      try {
        const latest = await api(`/api/projects/${encodeURIComponent(projectId)}`);
        if (state.project?.id === projectId) { state.project = latest.project || latest; renderProject(); }
      } catch { /* Keep the last saved project. */ }
      message($("#global-message"), error.message);
      toast(error.message, true);
    } finally {
      state.mutating = false;
      done();
      syncMutationControls();
      await refreshModelStatus();
    }
  }
  function stringList(values, ordered = false) {
    const items = list(values).filter(value => value !== null && value !== "");
    if (!items.length) return '<p>暂未确定，需进一步补充。</p>';
    const tag = ordered ? "ol" : "ul";
    return `<${tag}>${items.map(value => `<li>${escape(value)}</li>`).join("")}</${tag}>`;
  }
  function sourceVerification(value) { return { unverified: "待核实", metadata_checked: "已核对书目信息", abstract_checked: "已核对摘要", fulltext_checked: "已核对相关正文" }[value] || "待核实"; }
  function declaredScopeLabel(value) { return { user_declared_fulltext_file: "用户声明为全文文件", user_declared_metadata: "用户声明为元数据" }[value] || "未声明范围"; }
  function declaredFingerprint(item) {
    if (!item || typeof item !== "object") return null;
    const nested = item.source_fingerprint && typeof item.source_fingerprint === "object" ? item.source_fingerprint : {};
    const version = item.source_version || nested.source_version;
    const sha256 = item.source_sha256 || nested.source_sha256;
    const scope = item.source_sha256_scope || nested.source_sha256_scope;
    const caveat = item.source_fingerprint_caveat || nested.caveat;
    if (!(typeof version === "string" && version.trim() && typeof sha256 === "string" && /^[0-9a-f]{64}$/.test(sha256))) return null;
    if (!["user_declared_fulltext_file", "user_declared_metadata"].includes(scope)) return null;
    return { version: version.trim(), sha256, scope, caveat: typeof caveat === "string" && caveat.trim() ? caveat : "用户声明的版本/文件哈希；程序没有读取/认证原文，不证明内容真实性、版本正确性或科学结论。" };
  }
  function declaredFingerprintText(item) {
    const fingerprint = declaredFingerprint(item);
    if (!fingerprint) return "未登记原文版本/指纹（程序未读取/认证原文）";
    return `用户声明原文版本：${fingerprint.version}；声明文件 SHA256（${declaredScopeLabel(fingerprint.scope)}）：${fingerprint.sha256}；${fingerprint.caveat}`;
  }
  function renderSource(source) {
    const url = safeUrl(source.url);
    const title = url ? `<a href="${url}" class="source-title" target="_blank" rel="noopener noreferrer">${escape(source.title)}</a>` : `<span class="source-title">${escape(source.title)}</span>`;
    const authors = text(source.authors);
    return `<div class="source-item">${title}<div class="source-meta"><span>${escape(authors || "作者信息未提供")}${source.year_or_version ? " · " : ""}${escape(source.year_or_version)}</span><span class="source-verification ${source.verification === "unverified" ? "unverified" : ""}">${sourceVerification(source.verification)}</span></div><div class="source-scope"><span>核对位置：${escape(source.locator || "尚未记录")}</span><span>核对日期：${source.checked_at ? `<time datetime="${escape(source.checked_at)}">${escape(date(source.checked_at, true))}</time>` : "尚未核对"}</span></div>${source.abstract ? `<details class="returned-abstract"><summary>接口返回的摘要 · 不自动标记为已读 ${icon("chevron")}</summary><p>${escape(source.abstract)}</p></details>` : ""}</div>`;
  }
  function renderSources(map) {
    const sources = list(map.sources);
    return `<section class="panel sources-panel"><span class="section-kicker">SOURCES & EVIDENCE</span><h3>来源记录与阅读范围</h3><p class="sources-description">${isQwenMap(map) ? "千问可依据简报、补充材料、真实检索题录与用户确认的阅读摘录修订候选。模型不会自动读取全文；题录核对、阅读记录和论断支持分别留存，仍需人工审查。" : map.provenance?.live_search_performed ? "按方向地图展示实际取得的来源与核对范围；内容仍需人工审查。" : "生成这些候选时未执行检索。下列来源来自此前材料；本项目后续的独立检索见文献工作台，阅读范围按来源记录展示。"}</p>${sources.length ? sources.map(renderSource).join("") : '<p class="sources-description">当前方向没有可引用的来源。请先检索、阅读并核实文献依据。</p>'}</section>`;
  }
  function renderEvidence(topic, map) {
    const evidence = list(topic.evidence);
    if (!evidence.length) return '<p>尚未建立可核查的文献支持。</p>';
    return evidence.map(entry => {
      const source = list(map.sources).find(item => item.id === entry.source_id);
      const relation = { supports: "支持", limits: "限制", background: "背景", unverified_lead: "待核实线索" }[entry.relation] || "待审查";
      const url = source && safeUrl(source.url);
      const snapshot = entry.note_snapshot && typeof entry.note_snapshot === "object" ? entry.note_snapshot : null;
      const liveNote = snapshot ? list(state.project?.reading_notes).find(note => note && note.id === snapshot.note_id) : null;
      const excerpt = liveNote && typeof liveNote.content === "string" ? liveNote.content : "";
      const noteBlock = snapshot ? `<div class="evidence-note"><p><strong>已绑定用户确认阅读笔记</strong> · ${sourceVerification(snapshot.level)} · 阅读定位：${escape(snapshot.locator || "未记录")}</p>${excerpt ? `<p class="evidence-excerpt">${escape(excerpt.slice(0, 600))}${excerpt.length > 600 ? "…" : ""}</p>` : '<p class="evidence-excerpt">当前项目阅读记录中未找到该笔记原文；快照仍保留哈希与定位。</p>'}<small>note_id：${escape(snapshot.note_id || "未记录")} · 笔记 SHA256：${escape(snapshot.note_sha256 || "未记录")} · 内容 SHA256：${escape(snapshot.content_sha256 || "未记录")}${snapshot.checked_at ? ` · 核对时间：${escape(date(snapshot.checked_at, true))}` : ""}</small><p class="evidence-fingerprint">${escape(declaredFingerprintText(snapshot))}</p><p class="evidence-caution">${escape(snapshot.caveat || "笔记快照不是程序核验，也不证明论断与摘录相符。")}</p></div>` : "";
      const scope = snapshot ? `阅读定位：${escape(snapshot.locator || "未记录")}` : `位置：${escape(entry.locator || source.locator || "尚未记录")}`;
      return `<div class="evidence-entry"><p class="evidence-line"><strong>${escape(relation)}</strong> · ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${escape(source.title)} ↗</a>` : escape(source?.title || entry.source_id)}<br>${escape(entry.claim)}</p>${source ? `<p class="evidence-scope">${sourceVerification(source.verification)} · ${scope} · 核对：${escape(source.checked_at ? date(source.checked_at, true) : "尚未核对")}</p>` : '<p class="evidence-scope">关联来源暂不可用，请核查原始记录。</p>'}${noteBlock}</div>`;
    }).join("");
  }
  function renderDirections(map) {
    const topics = list(map.topics);
    const recommendation = map.recommendation || {};
    const recommended = topics.find(topic => topic.id === recommendation.topic_id);
    const reason = state.reasonDrafts[state.project?.id] || "";
    return `<div class="workflow-link"><div><strong>先核查文献，再决定方向。</strong><p>${isQwenMap(map) ? "当前候选由千问生成，内容与潜在切入点仍需人工审查。" : "当前候选由本地模板整理，可选择使用千问重新生成。"}公开题录检索可帮助你找到阅读入口。</p></div><button type="button" class="button button-secondary" data-action="literature">${icon("book")}先核查文献</button></div>${recommended ? `<div class="recommendation"><span>${icon("sparkles")}</span><div><strong>优先参考：${escape(recommended.title)}</strong><p>${escape(recommendation.reason)}</p><small>推荐供你比较，最终方向由你选择。</small></div></div>` : ""}<details class="panel selection-reason"><summary>记录你为什么选择一个方向 <span>选填</span>${icon("chevron")}</summary><label for="selection-reason">选择理由</label><textarea id="selection-reason" maxlength="1000" placeholder="例如：已有相关数据，首轮验证成本可控；仍需要核对……">${escape(reason)}</textarea><p>点击任一“选择这个方向”后，这段理由会和你的选择一起保存。最多 1000 字。</p></details><div class="direction-head"><h3>${topics.length} 个可以比较的方向</h3><span>${isQwenMap(map) ? "千问生成 · 待审查" : "本地模板 · 待审查"}</span></div>${topics.map((topic, index) => renderTopic(topic, index, map)).join("")}${map.next_action ? `<div class="next-action">${icon("compass")}<span>${escape(map.next_action)}</span></div>` : ""}${renderSources(map)}`;
  }
  function renderGenerationPanel(map) {
    const model = state.status?.model || {};
    const disabled = !model.configured || state.mutating || state.literatureBusy || state.experimentBusy || leanBusy();
    const workflow = state.project?.workflow || {};
    const failure = workflow.state === "failed" ? `<div class="message is-error workflow-failure" role="alert"><strong>这一步尚未完成，当前输入与已有候选已保留。</strong><p>${escape(typeof workflow.last_error === "string" ? workflow.last_error : workflow.last_error?.message || workflow.last_error?.summary || "模型调用或输出校验未完成，请重试。")}</p></div>` : "";
    const material = state.materialDrafts[state.project?.id] ?? (workflow.state === "failed" ? list(workflow.provided_materials).at(-1)?.content : "") ?? "";
    const configError = model.configuration_error ? `<p class="model-config-note">${escape(model.configuration_error)}</p>` : "";
    return `<section class="panel model-generation-panel" id="model-generation-panel" aria-labelledby="model-generation-title"><div class="model-generation-head"><div><span class="section-kicker">SKILL GUIDED WORKFLOW</span><h3 id="model-generation-title">按研究进展，判断下一步。</h3></div><span class="stage-pill ${model.state === "verified" ? "mint" : ""}">${escape(modelStateLabel())}</span></div>${failure}<p class="generation-origin">本项目当前候选：<strong>${list(map.topics).length ? (isQwenMap(map) ? "千问生成" : "本地模板") : "尚未生成"}</strong>。</p><p>点击后会把当前简报、澄清回答、补充材料、真实检索题录和用户确认的阅读摘录发送至百炼，发起一次模型请求并消耗 API 额度。Skill 规则决定继续澄清或返回候选，选题始终由你决定。</p>${configError}${!model.configured ? '<p class="model-config-note">先在本机运行 <code>Configure-Qwen.ps1</code> 配置 API Key，再刷新状态。密钥不填写在网页中。</p>' : `<p class="model-config-note">当前模型：${escape(model.model || "千问")}。${model.state === "error" ? "最近调用未成功，可先核对本机配置；已有候选仍保留。" : model.state === "verified" ? "配置已通过最近一次实际调用；每次生成均需手动发起。" : "已发现配置，尚未通过实际调用验证。"}</p>`}<div class="model-generation-actions"><button type="button" class="button button-primary" data-action="advance" ${disabled ? "disabled" : ""}>${state.generating ? '<span class="spinner" aria-hidden="true"></span>千问正在生成' : `${icon("sparkles")}用千问生成方向`}</button><button type="button" class="button button-secondary" data-action="refresh-model-status">刷新配置状态</button><span>刷新状态不会调用模型</span></div><details class="material-revision"><summary>补充资料后修订 ${icon("chevron")}</summary><form id="material-form"><label for="revision-material">补充背景、约束或阅读发现</label><textarea id="revision-material" name="material" required maxlength="6000" placeholder="例如：我已读到某篇论文的某节，发现原方案已有类似工作；希望把首轮验证改为……">${escape(material)}</textarea><p>资料会保存到项目。提交时连同当前简报、题录和已确认的阅读摘录发给百炼，消耗 API 额度。</p><button type="submit" class="button button-secondary" ${disabled ? "disabled" : ""}>保存资料并请千问修订</button></form></details></section>`;
  }
  function renderTopic(topic, index, map) {
    const recommended = topic.id === map.recommendation?.topic_id;
    const selected = topic.id === map.selected_topic_id;
    const validation = topic.first_validation || {};
    return `<article class="panel topic-card ${recommended ? "is-recommended" : ""} ${selected ? "is-selected" : ""}"><div class="topic-top"><span class="topic-index">DIRECTION ${String(index + 1).padStart(2, "0")}</span><div class="topic-badges">${recommended ? '<span class="badge-recommended">优先参考</span>' : ""}${selected ? '<span class="badge-selected">你已选择</span>' : ""}</div></div><h3>${escape(topic.title)}</h3><p class="topic-question">${escape(topic.question)}</p><div class="topic-difference"><strong>潜在切入点 · 待审查</strong><p>${escape(topic.potential_difference)}</p></div><div class="topic-info"><div><strong>首轮验证</strong><p>${escape(list(validation.steps)[0] || "需进一步明确验证步骤")}</p></div><div><strong>资源与约束</strong><p>${line(topic.resources) || "需进一步明确"}</p></div></div><details class="topic-details"><summary>展开依据、验证与风险 ${icon("chevron")}</summary><div class="topic-detail-grid"><div class="detail-block"><h4>已有工作</h4>${stringList(topic.known_work)}</div><div class="detail-block"><h4>文献关联</h4>${renderEvidence(topic, map)}</div><div class="detail-block"><h4>验证步骤</h4>${stringList(validation.steps, true)}</div><div class="detail-block"><h4>基线与判断方式</h4><p>${escape(validation.baseline)}</p>${stringList(validation.metrics)}<p>${escape(validation.success_signal)}</p></div><div class="detail-block"><h4>停止条件</h4>${stringList(topic.stop_conditions)}</div><div class="detail-block"><h4>风险与开放问题</h4>${stringList([...list(topic.risks), ...list(topic.open_questions)])}</div>${recommended && list(map.recommendation?.would_change_if).length ? `<div class="detail-block"><h4>何时改变推荐</h4>${stringList(map.recommendation.would_change_if)}</div>` : ""}</div></details><div class="topic-actions"><span>${topic.readiness === "ready_to_plan" ? "可形成首轮计划，依据仍需审查" : "暂定方向，需要继续澄清"}</span><button type="button" class="button ${recommended ? "button-primary" : "button-secondary"}" data-action="select" data-topic-id="${escape(topic.id)}">选择这个方向 ${icon("arrow-right")}</button></div></article>`;
  }
  function renderPlan(map) {
    const topic = list(map.topics).find(item => item.id === map.selected_topic_id);
    if (!topic) return '<div class="message is-error">选题记录暂时无法读取，请重新打开此项目。</div>';
    const validation = topic.first_validation || {};
    return `<section class="panel plan-panel"><div class="plan-selected-label">${icon("check")}你已选择方向 · 首轮计划草案</div><h2>${escape(topic.title)}</h2><p class="plan-question">${escape(topic.question)}</p><div class="plan-section"><h3>你的选择理由</h3><p>${escape(map.intake?.known?.selection_reason || "你未填写额外选择理由。")}</p><p class="selection-basis">选择依据已留在研究记录。</p></div><div class="plan-section"><h3>此方向的文献关联</h3>${renderEvidence(topic, map)}</div><div class="plan-section"><h3>首轮验证步骤</h3>${stringList(validation.steps, true)}</div><div class="plan-grid"><div class="plan-section"><h3>对照基线</h3><p>${escape(validation.baseline)}</p></div><div class="plan-section"><h3>判断方式</h3>${stringList(validation.metrics)}<p>${escape(validation.success_signal)}</p></div><div class="plan-section"><h3>需要准备</h3>${stringList(topic.resources)}</div><div class="plan-section"><h3>何时停下来</h3>${stringList(topic.stop_conditions)}</div></div><div class="plan-section"><h3>保留的风险与问题</h3>${stringList([...list(topic.risks), ...list(topic.open_questions)])}</div><div class="plan-warning">这是首轮研究计划草案。潜在贡献与文献支持仍需核实；新实验是否实际执行，以实验工作台中的独立运行记录为准。</div><div class="plan-actions"><button class="button button-primary" type="button" data-action="experiments">${icon("cpu")}进入实验工作台</button><button class="button button-secondary" type="button" data-action="download-plan">${icon("download")}导出计划草案</button><button class="button button-secondary" type="button" data-action="download-json">导出完整研究记录</button><button class="button button-secondary" type="button" data-action="literature">继续核查文献</button></div><div class="plan-review"><span>你的选择与依据已加入研究记录</span><button class="text-button" type="button" data-action="reopen">重新比较方向</button></div></section>${renderSources(map)}`;
  }
  function renderLiterature() {
    const target = $("#literature-content");
    if (!state.projects.length) { target.innerHTML = empty("book", "先建立一个研究项目。", "文献查询与阅读笔记会独立保存到项目中。"); return; }
    const project = state.project;
    const picker = `<label class="project-picker" for="literature-project">当前研究项目<select id="literature-project"><option value="">请选择研究项目</option>${state.projects.map(item => `<option value="${escape(item.id)}" ${item.id === project?.id ? "selected" : ""}>${escape(item.title)}</option>`).join("")}</select></label>`;
    if (!project) { target.innerHTML = `${picker}${empty("book", "选择要核查文献的研究。", "只有你提交的公开检索词会发送给题录服务。", false)}`; return; }
    const queries = list(project.workflow?.suggested_queries).map(item => typeof item === "string" ? item : item?.query).filter(Boolean);
    const draft = state.literatureDrafts[project.id] || { query: queries[0] || "", provider: "crossref", limit: 5 };
    const searches = list(project.literature_searches), notes = list(project.reading_notes);
    const revisable = project.stage !== "topic_selected" && state.status?.model?.configured;
    target.innerHTML = `<div class="literature-toolbar">${picker}<button type="button" class="button button-secondary" data-action="return-research">${icon("arrow-left")}${project.stage === "topic_selected" ? "查看计划草案" : project.stage === "directions_ready" ? "返回方向比较" : "返回当前研究"}</button></div><section class="panel literature-form-panel"><span class="section-kicker">PUBLIC METADATA SEARCH</span><h2>让研究想法找到文献入口。</h2><p class="literature-disclosure">检索词发送到所选公开题录服务。点击“按研究想法查文献”会使用建议查询（没有建议时使用想法文本），实际执行一次查询；请先确认查询框适合公开。返回题录不代表已阅读。</p><form id="literature-form"><label class="field-label" for="literature-query">公开检索词 <span>最多 300 字</span></label><input id="literature-query" name="query" type="text" required maxlength="300" value="${escape(draft.query)}" placeholder="例如：randomized singular value decomposition" autocomplete="off">${queries.length ? `<div class="literature-samples query-suggestions"><span>研究查询建议 · 点击填入</span>${queries.map(query => `<button type="button" class="sample-chip" data-literature-sample="${escape(query.slice(0, 300))}">${escape(query)}</button>`).join("")}</div>` : ""}<div class="literature-samples"><span>公开示例</span><button type="button" class="sample-chip" data-literature-sample="randomized singular value decomposition">随机低秩逼近</button><button type="button" class="sample-chip" data-literature-sample="retrieval augmented generation citation accuracy">AI 引用准确性</button></div><div class="literature-form-bottom"><label for="literature-provider">题录服务<select id="literature-provider" name="provider"><option value="crossref" ${draft.provider === "crossref" ? "selected" : ""}>Crossref</option><option value="arxiv" ${draft.provider === "arxiv" ? "selected" : ""}>arXiv</option></select></label><label for="literature-limit">最多返回<select id="literature-limit" name="limit">${[1, 2, 3, 4, 5].map(number => `<option value="${number}" ${Number(draft.limit) === number ? "selected" : ""}>${number} 条</option>`).join("")}</select></label><button type="submit" class="button button-primary" ${state.literatureBusy ? "disabled" : ""}>${state.literatureBusy ? '<span class="spinner" aria-hidden="true"></span>正在检索' : `${icon("book")}检索题录`}</button></div><div class="idea-search-action"><button type="button" class="button button-secondary" data-action="search-idea" ${state.literatureBusy ? "disabled" : ""}>按研究想法查文献</button><span>每次点击只查询一个服务，不调用模型</span></div><div id="literature-message" class="message" role="alert" hidden></div></form></section>${searches.length ? `<div class="workflow-link literature-revise"><div><strong>把阅读发现带回候选方向。</strong><p>当前保存 ${notes.filter(note => note.origin !== "curated_example").length} 条用户确认笔记${notes.some(note => note.origin === "curated_example") ? `，另有 ${notes.filter(note => note.origin === "curated_example").length} 条公开案例预置笔记（不作为你的阅读声明）` : ""}。修订时会将简报、补充资料、公开题录与已确认的阅读摘录发送至百炼，消耗 API 额度。${project.stage === "topic_selected" ? "当前方向已由你选择；请先返回研究页重新比较，再修订。" : "模型会提出候选或澄清问题，不会替你选题。"}</p></div><button type="button" class="button button-secondary" data-action="revise-from-literature" ${revisable ? "" : "disabled"}>依据资料修订方向</button></div>` : ""}<div class="direction-head"><h3>${searches.length} 轮独立检索记录</h3><span>${notes.length} 条阅读记录</span></div><p class="literature-record-note">题录状态保持原样。摘要或正文是否读过，由你确认并填写阅读位置；保存笔记不自动证明候选论断成立。</p>${searches.length ? [...searches].reverse().map(record => renderSearchRun(record, project.id)).join("") : '<div class="empty-state small-empty"><h2>还没有发起查询。</h2><p>实际返回、无结果和失败状态都会保存。</p></div>'}`;
    syncMutationControls();
  }
  function renderReadingNotes(source, record, projectId) {
    const key = `${projectId}:${record.id}:${source.id}`;
    const notes = list(state.project?.reading_notes).filter(note => note.run_id === record.id && note.source_id === source.id);
    const draft = state.readingDrafts[key] || {};
    return `<div class="reading-records">${notes.map(note => `<article class="reading-note"><div><span class="source-verification">${sourceVerification(note.level)}</span><span>${note.origin === "curated_example" ? "公开案例整理 · 非你的阅读确认" : "用户确认"} · ${escape(date(note.checked_at, true))}</span></div><p class="reading-locator">阅读位置：${escape(note.locator)}</p><p class="reading-content">${escape(note.content)}</p><p class="reading-fingerprint">${escape(declaredFingerprintText(note))}</p></article>`).join("")}<details class="reading-note-editor"><summary>记录我读过的内容 ${icon("chevron")}</summary><form class="reading-note-form" data-run-id="${escape(record.id)}" data-source-id="${escape(source.id)}" data-draft-key="${escape(key)}"><label>实际阅读范围<select name="level"><option value="abstract_checked" ${draft.level === "abstract_checked" ? "selected" : ""}>已阅读并核对摘要</option><option value="fulltext_checked" ${draft.level === "fulltext_checked" ? "selected" : ""}>已阅读相关正文</option></select></label><label>阅读位置<input name="locator" required maxlength="300" value="${escape(draft.locator || "")}" placeholder="例如：摘要；v2 第 3.1 节 / 第 6 页"></label><label>原文摘录或阅读笔记<textarea name="content" required maxlength="6000" placeholder="记录实际读到的内容，以及它能支持或限制什么。">${escape(draft.content || "")}</textarea></label><fieldset class="reading-fingerprint-fields"><legend>原文版本与文件指纹（可选 · 用户声明，程序不认证）</legend><label>原文版本<input name="source_version" maxlength="300" value="${escape(draft.source_version || "")}" placeholder="例如：v2 / arXiv:2001.00001v3"></label><label>声明文件 SHA256<input name="source_sha256" maxlength="64" value="${escape(draft.source_sha256 || "")}" placeholder="64 位小写十六进制"></label><label>哈希范围<select name="source_sha256_scope"><option value="" ${!draft.source_sha256_scope ? "selected" : ""}>不声明</option><option value="user_declared_fulltext_file" ${draft.source_sha256_scope === "user_declared_fulltext_file" ? "selected" : ""}>用户声明：全文文件</option><option value="user_declared_metadata" ${draft.source_sha256_scope === "user_declared_metadata" ? "selected" : ""}>用户声明：元数据</option></select></label><p>三项须同时填写或同时留空；程序只保存你的声明，不读取/认证原文，也不证明内容真实性或版本正确性。</p></fieldset><label class="reading-confirmation"><input name="confirmed" type="checkbox" required ${draft.confirmed ? "checked" : ""}><span>我确认已阅读上述范围，位置与笔记由我填写。</span></label><p>只保存你的阅读声明与笔记；程序不会自动下载全文或把题录升级为已读。</p><button type="submit" class="button button-secondary">保存阅读记录</button></form></details></div>`;
  }
  function captureLiteratureDraft() {
    const query = $("#literature-query");
    if (!state.project || !query) return;
    state.literatureDrafts[state.project.id] = { query: query.value, provider: $("#literature-provider")?.value || "crossref", limit: Number($("#literature-limit")?.value || 5) };
  }
  function renderSearchRun(record, projectId) {
    const logs = list(record.search_log), sources = list(record.sources);
    const unknown = record.execution_state_unknown || record.provenance?.execution_state_unknown;
    const statusLabels = { success: "已返回题录", empty: "本次未命中", failed: "检索失败", planned: "尚未执行" };
    const nextAction = record.next_action === "Review relevance and bibliographic metadata, then read selected abstracts or permitted full text and record precise locators before adding claim evidence." ? "先核对题录与相关性，再阅读选中的摘要或合法全文；记录版本、位置和日期后再建立证据关联。" : record.next_action;
    return `<article class="panel search-run"><div class="search-run-head"><div><span class="section-kicker">SAVED SEARCH RECORD</span><h3>${escape(text(record.request?.queries) || "文献检索记录")}</h3></div>${record.id ? `<a class="button button-secondary button-small" href="/api/projects/${encodeURIComponent(projectId)}/literature/${encodeURIComponent(record.id)}/export" download>${icon("download")}导出 JSON</a>` : ""}</div><p class="search-run-provenance">${unknown ? "客户端异常，无法确认请求执行状态。" : record.provenance?.live_search_performed ? "已实际尝试实时查询；返回不代表已阅读。" : "本记录未发出实时查询请求。"}保存 ${sources.length} 条题录。</p>${logs.map(log => `<div class="search-log"><div><span class="search-status ${escape(["success", "empty", "failed", "planned"].includes(log.status) ? log.status : "failed")}">${statusLabels[log.status] || "状态待核查"}</span><strong>${escape(log.provider)}</strong><span>${escape(log.query)}</span></div><p>时间：${escape(log.searched_at ? date(log.searched_at, true) : "未记录")} · ${unknown ? "请求执行次数未确认" : `实际请求 ${Number(log.attempts) || 0} 次`} · ${unknown ? "返回状态未确认" : `返回 ${Number(log.result_count) || 0} 条`}</p>${log.error ? `<p class="search-error">${escape(log.error)}</p>` : ""}${log.status === "empty" ? '<p>本次没有返回可用题录，不能据此推断相关研究不存在。</p>' : ""}</div>`).join("")}<div class="search-source-list">${sources.map(source => `<div class="search-reading-source">${renderSource(source)}${renderReadingNotes(source, record, projectId)}</div>`).join("")}</div>${list(record.limitations).length ? `<details class="record-limitations"><summary>检索范围与限制 ${icon("chevron")}</summary>${stringList(record.limitations)}</details>` : ""}${nextAction ? `<p class="search-next-action">下一步：${escape(nextAction)}</p>` : ""}</article>`;
  }
  async function searchLiterature(form) {
    if (!state.project || state.literatureBusy || state.mutating || leanBusy()) return;
    const project = state.project, token = state.opening;
    const body = { query: $("#literature-query").value.trim(), provider: $("#literature-provider").value, limit: Number($("#literature-limit").value) };
    if (!body.query || body.query.length > 300) { message($("#literature-message"), "请填写 1—300 字的公开检索词。"); return; }
    state.literatureDrafts[project.id] = body;
    state.literatureBusy = true;
    syncMutationControls();
    const done = busy($("button[type=submit]", form), "正在检索");
    let failure = "";
    message($("#literature-message"), "");
    try {
      const result = await api(`/api/projects/${encodeURIComponent(project.id)}/literature-search`, { method: "POST", body: JSON.stringify(body) });
      if (token !== state.opening || state.project?.id !== project.id) { await refreshProjects(false); return; }
      state.project = result.project || result;
      renderProject();
      await refreshProjects();
      toast("本次查询记录已保存，结果与阅读等级分别留存。");
    } catch (error) {
      if (token === state.opening && state.project?.id === project.id) { failure = error.message; message($("#literature-message") || $("#global-message"), failure); toast(failure, true); }
    } finally {
      state.literatureBusy = false;
      done();
      syncMutationControls();
      if (state.view === "literature") {
        if (token === state.opening && state.project?.id === project.id) renderLiterature();
        else {
          const currentButton = $("#literature-form button[type=submit]");
          if (currentButton) { currentButton.disabled = false; currentButton.innerHTML = `${icon("book")}检索题录`; }
        }
      }
      if (failure && state.view === "literature" && token === state.opening && state.project?.id === project.id) message($("#literature-message"), failure);
    }
  }
  async function loadExperiments(force = false) {
    if (state.validationMode === "theory") { await loadTheory(force); return; }
    if (!state.projects.length || !state.project) { renderExperiments(); return; }
    const projectId = state.project.id, token = state.opening;
    if (state.experimentWorkspaces[projectId] && !force) { renderExperiments(); return; }
    if (state.experimentLoading) return;
    state.experimentLoading = true;
    renderExperiments();
    try {
      const result = await api(`/api/projects/${encodeURIComponent(projectId)}/experiments`);
      state.experimentWorkspaces[projectId] = result;
      if (token === state.opening && state.project?.id === projectId && state.view === "experiments") renderExperiments();
    } catch (error) {
      if (token === state.opening && state.project?.id === projectId) $("#experiments-content").innerHTML = workbenchTabs() + `<div class="message is-error">${escape(error.message)}</div><button type="button" class="button button-secondary" data-action="reload-experiments">重新读取实验工作台</button>`;
    } finally { state.experimentLoading = false; if (state.view === "experiments" && state.project && state.project.id !== projectId) loadExperiments(); }
  }
  function workbenchTabs() {
    return `<div class="validation-tabs" role="group" aria-label="研究验证方式"><button type="button" data-action="validation-numeric" aria-pressed="${state.validationMode === "numeric"}">${icon("cpu")}数值实验<span>参数与结果</span></button><button type="button" data-action="validation-theory" aria-pressed="${state.validationMode === "theory"}">${icon("book")}理论论证<span>命题、证据与证明路线</span></button></div>`;
  }
  function renderExperiments() {
    activateValidationProject(state.project);
    if (state.validationMode === "theory") renderTheory();
    else { renderNumericalExperiments(); $("#experiments-content").insertAdjacentHTML("afterbegin", workbenchTabs()); }
  }
  function renderNumericalExperiments() {
    const target = $("#experiments-content"), project = state.project;
    if (!state.projects.length) { target.innerHTML = empty("cpu", "先从研究想法开始。", "由你选择课题后，再查看适合的首轮实验。"); return; }
    const picker = `<label class="project-picker" for="experiment-project">当前研究项目<select id="experiment-project"><option value="">请选择研究项目</option>${state.projects.map(item => `<option value="${escape(item.id)}" ${item.id === project?.id ? "selected" : ""}>${escape(item.title)}</option>`).join("")}</select></label>`;
    const toolbar = `<div class="literature-toolbar">${picker}<button class="button button-secondary" type="button" data-action="return-research">${icon("arrow-left")}返回当前研究</button></div>`;
    if (!project) { target.innerHTML = toolbar + empty("cpu", "选择要继续验证的研究。", "实验运行与结果将归入对应项目。", false); return; }
    const workspace = state.experimentWorkspaces[project.id];
    if (!workspace) { target.innerHTML = toolbar + '<div class="loading-state"><span class="spinner" aria-hidden="true"></span>正在核对可运行的实验……</div>'; return; }
    const config = state.experimentDrafts[project.id] || workspace.defaults || {};
    const runs = list(project.experiments).length ? list(project.experiments) : list(workspace.runs);
    const topic = list(mapOf(project).topics).find(item => item.id === mapOf(project).selected_topic_id);
    const input = (name, label, min, max) => `<label for="experiment-${name}">${label}<input type="number" id="experiment-${name}" name="${name}" required min="${min}" max="${max}" step="1" value="${escape(config[name])}"></label>`;
    const supportedForm = `<section class="panel experiment-config"><span class="section-kicker">A SMALL, REPRODUCIBLE EXPERIMENT</span><h2>随机低秩逼近 · 首轮验证</h2><p class="experiment-topic">你已选择：${escape(topic?.title || "研究方向")}</p><p class="experiment-disclosure">在本机运行固定 NumPy 程序，对 3 类合成矩阵比较过采样 p 与幂迭代 q，并与截断 SVD 基线对照。不会调用模型、上传数据或执行用户代码。单次运行上限约 20 秒。</p><form id="experiment-form"><div class="experiment-fields">${input("rows", "矩阵行数（40—240）", 40, 240)}${input("cols", "矩阵列数（20—160）", 20, 160)}${input("rank", "目标秩（2—32）", 2, 32)}${input("repeats", "每配置随机重复（1—5）", 1, 5)}<label for="experiment-p">过采样 p（最多 3 个）<input id="experiment-p" name="p_values" required value="${escape(Array.isArray(config.p_values) ? config.p_values.join(", ") : config.p_values || "")}" placeholder="4, 8, 16"></label><label for="experiment-q">幂迭代 q（0—2，最多 3 个）<input id="experiment-q" name="q_values" required value="${escape(Array.isArray(config.q_values) ? config.q_values.join(", ") : config.q_values || "")}" placeholder="0, 1"></label>${input("data_seed", "数据种子", 0, 2147483647)}</div><p class="experiment-config-note">目标秩必须小于行列数的较小值；p 不超过 min(64, 较小维度 − 目标秩)。固定数据种子用于复现，多个采样种子不算多个独立数据集。</p><div id="experiment-message" class="message" role="alert" hidden></div><button type="submit" class="button button-primary" ${state.experimentBusy ? "disabled" : ""}>${state.experimentBusy ? '<span class="spinner" aria-hidden="true"></span>本地实验正在运行' : `${icon("cpu")}运行首轮实验`}</button></form></section>`;
    const unsupported = `<section class="panel experiment-pending"><span class="section-kicker">EXPERIMENT PLAN</span><h2>${project.stage === "topic_selected" ? "这个课题的实验仍待实现。" : "先由你选择研究方向。"}</h2><p>${escape(workspace.reason || "当前方向不在固定实验支持范围内。")}</p>${topic ? `<h3>已选课题的首轮步骤</h3>${stringList(topic.first_validation?.steps, true)}` : ""}<p class="experiment-disclosure">当前可执行范围为随机低秩逼近的合成矩阵实验。其他课题保留各自计划，不会套用 SVD 或把计划显示为已运行。</p><button type="button" class="button button-secondary" data-action="return-research">返回研究方向</button></section>`;
    target.innerHTML = toolbar + (workspace.supported ? supportedForm : unsupported) + `<div class="direction-head"><h3>${runs.length} 轮实际运行记录</h3><span>与历史示例档案分别保存</span></div>${runs.length ? [...runs].reverse().map(run => renderExperimentRun(run, project.id)).join("") : '<div class="empty-state small-empty"><h2>这个项目还没有运行实验。</h2><p>参数、真实结果、检查与失败状态会在运行后保存。</p></div>'}${runs.length ? `<div class="workflow-link experiment-revise"><div><strong>让实际结果影响下一轮问题。</strong><p>点击后将重新打开方向比较，把实验结果摘要、简报与研究资料发送至百炼，发起一次模型请求并消耗额度。先前选题与运行快照保留，修订后仍由你重新选择。</p></div><button type="button" class="button button-secondary" data-action="revise-from-experiments" ${state.status?.model?.configured ? "" : "disabled"}>依据结果修订方向</button></div>` : ""}`;
    syncMutationControls();
  }
  const theoryFields = { title: "命题标题", domain: "领域与定义域", statement: "当前命题", definitions: "定义与符号", assumptions: "假设与量词", target: "研究与审阅目标" };
  function blankTheoryCard() { return { title: "", domain: "", statement: "", definitions: [], assumptions: [], target: "" }; }
  function theoryDraft(projectId) {
    if (Object.hasOwn(state.theoryDrafts, projectId)) return state.theoryDrafts[projectId];
    try {
      const saved = JSON.parse(localStorage.getItem(`yanzheng.theoryDraft.${projectId}`) || "null");
      state.theoryDrafts[projectId] = saved && typeof saved.card === "object" && Number.isInteger(saved.baseVersion) ? saved : null;
    } catch { state.theoryDrafts[projectId] = null; }
    return state.theoryDrafts[projectId];
  }
  function clearTheoryDraft(projectId) {
    state.theoryDrafts[projectId] = null;
    try { localStorage.removeItem(`yanzheng.theoryDraft.${projectId}`); } catch { /* Server records remain available. */ }
  }
  function captureTheoryDraft() {
    const form = $("#theory-card-form"), project = state.project;
    if (!form || !project) return null;
    if (state.mutating || state.theoryBusy || state.literatureBusy || state.experimentBusy || leanBusy()) return theoryDraft(project.id);
    const raw = Object.fromEntries(new FormData(form));
    const card = Object.fromEntries(Object.keys(theoryFields).map(key => [key, ["definitions", "assumptions"].includes(key) ? String(raw[key] || "").split(/\r?\n/).map(item => item.trim()).filter(Boolean) : String(raw[key] || "").trim()]));
    const workspace = state.theoryWorkspaces[project.id] || project.theory || {};
    const previous = theoryDraft(project.id);
    const draft = { card, baseVersion: previous?.baseVersion ?? workspace.card_version ?? 0 };
    if (!previous && JSON.stringify(card) === JSON.stringify(Object.fromEntries(Object.keys(theoryFields).map(key => [key, (workspace.card || blankTheoryCard())[key]])))) return draft;
    state.theoryDrafts[project.id] = draft;
    try { localStorage.setItem(`yanzheng.theoryDraft.${project.id}`, JSON.stringify(draft)); } catch { /* Keep the in-memory draft if storage is unavailable. */ }
    const status = $("#theory-draft-status"); if (status) status.textContent = "本机草稿已保留 · 尚未保存到项目";
    const accept = $('[data-action="theory-accept"]'); if (accept) accept.disabled = true;
    return draft;
  }
  async function loadTheory(force = false) {
    renderExperiments();
    const project = state.project;
    if (project && leanUI) void leanUI.load(project, { force }).catch(() => {});
    if (!project || (state.theoryWorkspaces[project.id] && !force) || state.theoryLoading[project.id]) return;
    const projectId = project.id, token = state.opening;
    state.theoryLoading[projectId] = true;
    try {
      const result = await api(`/api/projects/${encodeURIComponent(projectId)}/theory`);
      state.theoryWorkspaces[projectId] = result;
      state.theoryWorkspaceRevisions[projectId] = project.updated_at;
    } catch (error) { state.theoryErrors[projectId] = error.message; }
    finally {
      state.theoryLoading[projectId] = false;
      if (token === state.opening && state.project?.id === projectId && state.view === "experiments" && state.validationMode === "theory") renderExperiments();
    }
  }
  function renderTheoryEvidence(note, binding = null) {
    const url = safeUrl(note.url), origin = note.origin === "curated_example" ? "公开案例预置摘录 · 非你的阅读声明" : "用户确认的阅读笔记";
    const relation = { supports: "支持线索", limits: "适用限制", contradicts: "反驳线索", background: "背景参考" }[binding?.relation] || "关联线索";
    return `<details class="theory-evidence"><summary><span>${escape(note.title || "阅读笔记")}<small>${escape(note.year_or_version || "版本未记录")} · ${escape(note.locator || "位置未记录")}</small></span>${icon("chevron")}</summary><div><span class="theory-origin">${origin}</span>${binding ? `<p><strong>${relation}：</strong>${escape(binding.claim)}</p>` : ""}<p class="theory-quote">${escape(note.content)}</p>${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">查看原始出处 ↗</a>` : '<p>当前笔记未记录可用的原文链接。</p>'}<small>笔记 ID：${escape(note.note_id || note.id)}${note.content_sha256 ? ` · 内容指纹：${escape(note.content_sha256)}` : ""}</small></div></details>`;
  }
  function renderProofRoutes(routes, pending = false, archived = false) {
    if (!list(routes).length) return `<div class="theory-empty"><strong>${pending ? "本次未提供证明路线。" : "尚未确认可继续推进的证明路线。"}</strong><p>保存命题、核对阅读资料后，可请千问提出路线；接受修订后会保留在这里。</p></div>`;
    return list(routes).map((route, index) => `<article class="theory-route"><div class="theory-route-head"><span class="section-kicker">ROUTE ${String(index + 1).padStart(2, "0")}</span><span class="stage-pill">${archived ? "历史建议" : pending ? "待审阅建议" : "已采用的规划"} · 未形式核验</span></div><h3>${escape(route.title)}</h3><h4>证明步骤</h4>${stringList(route.steps, true)}<h4>待核查的证明义务</h4><p class="theory-notice">义务是审查清单；尚未核查不表示已发现数学错误。</p>${list(route.obligations).length ? `<ol class="theory-obligations">${list(route.obligations).map(lemma => `<li><span class="theory-lemma-id">${escape(lemma.id)}</span><p>${escape(lemma.statement)}</p><small>依赖：${list(lemma.depends_on).length ? list(lemma.depends_on).map(escape).join("、") : "无前置引理记录"} · 待核查 · 未形式核验</small></li>`).join("")}</ol>` : '<p>未列出独立引理。</p>'}<div class="theory-gaps"><h4>已发现的缺口与待补证据</h4>${list(route.gaps).length ? stringList(route.gaps) : '<p>未识别额外数学缺口，仍待审阅。空列表不表示完成证明或形式核验。</p>'}</div>${list(route.evidence).length ? `<h4>路线关联的阅读依据</h4>${list(route.evidence).map(binding => renderTheoryEvidence(binding.snapshot || { note_id: binding.note_id }, binding)).join("")}` : '<p class="theory-notice">此路线尚未绑定阅读笔记，需补充核对。</p>'}</article>`).join("");
  }
  function renderTheoryVersions(workspace) {
    const versions = list(workspace.card_versions);
    if (!versions.length) return "";
    return `<details class="panel theory-history"><summary>历史命题版本 <span>${versions.length} 个历史版本 · 当前 v${Number(workspace.card_version) || 0}</span>${icon("chevron")}</summary>${[...versions].reverse().map(version => `<article><strong>命题 v${Number(version.version) || 0}</strong><span>${escape(date(version.replaced_at || version.saved_at, true))}</span><details class="theory-revised-card"><summary>${escape(version.card?.title || "查看此版本完整命题")}${icon("chevron")}</summary><dl>${Object.entries(theoryFields).map(([key, label]) => `<div><dt>${label}</dt><dd>${escape(Array.isArray(version.card?.[key]) ? version.card[key].join("\n") : version.card?.[key] || "（空）")}</dd></div>`).join("")}</dl></details></article>`).join("")}</details>`;
  }
  function theoryProposalSource(item) {
    return item.source === "curated_example" ? "人工整理的公开案例" : item.source === "model" ? "千问模型提议" : "来源未记录";
  }
  function theoryProposalVersion(item, project) {
    if (item.source === "curated_example") return "不适用（人工整理）";
    const call = list(project?.model_calls).find(entry => entry.id === item.model_call_id);
    // Never relabel an old proposal with the workspace's latest prompt version.
    return item.prompt_version || call?.prompt_version || "未记录";
  }
  function renderTheoryProposalMetadata(item, project) {
    return `<dl class="theory-proposal-meta"><div><dt>内容来源</dt><dd>${escape(theoryProposalSource(item))}</dd></div><div><dt>本条提议的提示版本</dt><dd>${escape(theoryProposalVersion(item, project))}</dd></div></dl>`;
  }
  function renderTheoryFeedback(workspace) {
    const feedback = list(workspace.proposals).filter(item => item.status === "rejected" && item.researcher_reason).slice(-2);
    if (!feedback.length) return '<p class="theory-notice">暂无明确填写的拒绝反馈；接受或拒绝均不会自动发起下一轮请求。</p>';
    return `<details class="theory-feedback"><summary>提议审阅历史 <span>${feedback.length} 条明确意见</span>${icon("chevron")}</summary><p>这些是已保存的历史意见；下一轮实际带入范围以材料预览为准。意见仍待独立核对，不是程序认定的数学事实。</p>${[...feedback].reverse().map(item => `<article><strong>${escape(item.revised_card?.title || "历史建议")} · 基于命题 v${Number(item.base_card_version) || 0}</strong><p>${escape(item.researcher_reason)}</p><small>提议 ID：${escape(item.id)}</small></article>`).join("")}</details>`;
  }
  function renderTheoryProposalHistory(workspace, project) {
    const proposals = list(workspace.proposals);
    if (!proposals.length) return "";
    return `<details class="panel theory-history"><summary>建议历史 <span>${proposals.length} 次提议 · 原输出与审阅分别留存</span>${icon("chevron")}</summary>${[...proposals].reverse().map(item => `<article><div class="theory-history-title"><span class="stage-pill">${{ accepted: "已接受规划", rejected: "已拒绝", pending: "待确认", superseded: "已被后续版本替代", stale: "上下文已变化" }[item.status] || "已留档"}</span><time>${escape(date(item.created_at, true))}</time></div>${renderTheoryProposalMetadata(item, project)}<p>${escape(item.revision_reason)}</p>${item.review_reason ? `<div class="theory-review-record"><strong>本条提议的审阅记录</strong><p>${escape(item.review_reason)}</p><small>记录审阅决定；不等于程序完成数学核验。</small></div>` : ""}<details class="theory-archived-proposal"><summary>查看当时的命题与原建议路线 ${icon("chevron")}</summary><p class="theory-notice">这是历史输出，保留已拒绝或未解决的内容；展开不表示采纳。</p><dl class="theory-archived-card">${Object.entries(theoryFields).map(([key, label]) => `<div><dt>${escape(label)}</dt><dd>${escape(Array.isArray(item.revised_card?.[key]) ? item.revised_card[key].join("\n") : item.revised_card?.[key] || "（空）")}</dd></div>`).join("")}</dl>${renderProofRoutes(item.proof_routes, true, true)}</details></article>`).join("")}</details>`;
  }
  const theoryContextPreviews = {};
  const checkDrafts = {};
  const checkMessages = {};
  function checkingWorkspace() { return (state.theoryWorkspaces[state.project?.id] || state.project?.theory || {}).checking || {}; }
  function checkDraft(projectId, id, checking) {
    const key = `${projectId}:${id}`;
    if (!Object.hasOwn(checkDrafts, key)) {
      try { checkDrafts[key] = JSON.parse(localStorage.getItem(`yanzheng.checkDraft.${key}`) || "null"); } catch {}
    }
    if (!checkDrafts[key]) {
      const item = list(checking.records).find(r => r.id === id);
      checkDrafts[key] = {request_id: crypto.randomUUID(), expected_revision: id === "new" ? checking.revision : id === "case-source" ? checking.case_revision || 0 : item?.review_revision,
        ...(id === "new" ? {context_token: checking.context_token, target_key: checking.targets?.[0]?.target_key || "", proposition:"OTHER",card_proposition:"OTHER",record_origin:"researcher_draft"} : id === "case-source" ? {} : {kind:"assistant_check"})};
    }
    return checkDrafts[key];
  }
  function persistCheckDraft(projectId, id, draft) {
    checkDrafts[`${projectId}:${id}`] = draft;
    try { localStorage.setItem(`yanzheng.checkDraft.${projectId}:${id}`, JSON.stringify(draft)); } catch {}
  }
  function captureCheckDraft(form) {
    if (!state.project || form.dataset.projectId !== state.project.id) return;
    const id = form.dataset.checkId, draft = checkDraft(state.project.id, id, checkingWorkspace());
    const fields = Object.fromEntries(new FormData(form));
    Object.assign(draft, fields);
    if (id !== "new" && id !== "case-source") draft.confirm_record_only = fields.confirm_record_only === "on" || fields.confirm_record_only === true;
    persistCheckDraft(state.project.id, id, draft);
  }
  function checkingTarget(target) {
    if (!target) return '<p class="message is-error">目标已不可用，请选择本项目已保存的案例、卡或义务。</p>';
    if (target.target_scope === "case") return `<p>案例级材料 · ${escape(target.case?.title)}${target.historical ? " · 旧源版本关联" : ""}</p>${checkingCaseSource(target.case)}<p>本条未绑定应用卡、路线或义务。</p>`;
    if (target.target_scope === "card") return `<p>${target.historical ? "旧卡关联 · " : ""}卡级材料 v${Number(target.card_version)}：${escape(target.card?.statement)}</p><p>未绑定具体路线或义务。</p><small>应用卡 SHA256：${escape(target.card_sha256)} · ${escape(target.card_id)}</small>`;
    return `<p>${target.historical ? "旧卡关联 · " : ""}卡 v${Number(target.card_version)}：${escape(target.card?.statement)}</p><p>路线：${escape(target.route?.title)}；义务 ${escape(target.obligation_id)}：${escape(target.obligation?.statement)}</p><small>卡 SHA256：${escape(target.card_sha256)} · ${escape(target.card_id)}</small>`;
  }
  function checkingCaseSource(item) {
    if (!item) return "";
    return `<p>案例 ID：${escape(item.case_id)} · 登记版本 ${Number(item.version)} · 源版本：${escape(item.source_version)}</p><p>声明的源文件字节 SHA256：${escape(item.declared_source_sha256 || "unknown")}</p><details><summary>案例来源身份与声明</summary><p>${escape(item.source_reference)}</p><small>来源元数据 SHA256（不是文件字节）：${escape(item.case_source_metadata_sha256)}<br>本项目案例身份：${escape(item.case_key)}；历史声明 unknown / not_connected</small></details>`;
  }
  function checkingTargetLabel(t) {
    if (t.target_scope === "case") return `案例 · ${t.case.title} · ${t.case.source_version}${t.historical ? "（旧源版本）" : ""}`;
    if (t.target_scope === "card") return `问题卡 v${t.card_version} · ${t.card.title}${t.historical ? "（旧卡）" : ""}`;
    return `义务 v${t.card_version} · ${t.route.title} · ${t.obligation_id}${t.historical ? "（旧卡）" : ""}`;
  }
  function renderReferenceEditor(draft) {
    return Array.from({length:Math.min(Number(draft.ref_count)||0,12)},(_,i)=>`<fieldset><legend>来源 ${i+1}</legend>${[["artifact_id","artifact_id",200],["label","材料名称",300],["source_version","来源版本",300],["locator","出处定位",2000],["support_scope","支持范围",4000],["origin","来源身份（如历史模型/助手推导/网页快照）",200],["original_bytes_sha256","声明的原件字节 SHA256（可空）",64],["acquired_at","取得时间（含时区，可空）",80]].map(([key,label,max])=>`<label>${label}<textarea name="ref_${i}_${key}" rows="2" maxlength="${max}" ${["original_bytes_sha256","acquired_at"].includes(key)?"":"required"}>${escape(draft[`ref_${i}_${key}`]||"")}</textarea></label>`).join("")}</fieldset>`).join("");
  }
  function changeCheckReferences(form, delta) {
    captureCheckDraft(form);
    const d=checkDraft(state.project.id,"new",checkingWorkspace()), count=Number(d.ref_count)||0;
    if (delta>0 && count>=12) { toast("最多12项来源引用。",true); return; }
    if (delta<0 && count===0) return;
    if (delta<0) for(const key of Object.keys(d)) if(key.startsWith(`ref_${count-1}_`)) delete d[key];
    d.ref_count=count+delta;persistCheckDraft(state.project.id,"new",d);
    form.querySelector(".checking-ref-fields").innerHTML=renderReferenceEditor(d);
  }
  function renderArtifactReferences(material) {
    return list(material.artifact_refs).length ? `<details><summary>逐项材料引用（外部声明） · ${material.artifact_refs.length} 项</summary>${material.artifact_refs.map(ref=>`<article><h4>${escape(ref.label)}</h4><p>来源身份：${escape(ref.origin)}；版本：${escape(ref.source_version)}</p><p>定位：${escape(ref.locator)}</p><p>支持范围：${escape(ref.support_scope)}</p><small>artifact_id：${escape(ref.artifact_id)}<br>声明字节 SHA256：${escape(ref.declared_original_bytes_sha256 || "unknown")}<br>取得时间：${escape(ref.declared_acquired_at || "unknown")}；历史声明 unknown / not_connected（不随新核查改写）</small></article>`).join("")}</details>` : "";
  }
  const artifactViews = {};
  function artifactKey(projectId, recordId, artifactId) { return `${projectId}:${recordId}:${artifactId}`; }
  function unsavedArtifactFacts(projectId, recordId) {
    try { return JSON.parse(localStorage.getItem(`yanzheng.artifactUnsaved.${projectId}:${recordId}`) || "[]"); } catch { return []; }
  }
  function renderArtifactFact(fact, saved = true) {
    const r = fact.result || {}, p = r.preview;
    const verified = r.status === "verified" && r.byte_match === true;
    const displayFact = verified ? fact : {...fact,result:{...r,preview:{status:"unavailable",text:null,truncated:false}}};
    return `<article class="artifact-fact"><strong>${saved ? "已保存的历史读取事实" : "未保存到项目 · 本机保留结果"}：${escape(r.status)} · ${verified ? "字节核对通过" : "核对未通过"}</strong><p>artifact_id：${escape(fact.artifact_id)}<br>本次记录：${escape(fact.recorded_at)}；核查时间：${escape(r.read_at || "unknown")}</p><p>实际字节 SHA256：${escape(r.actual?.byte_sha256 || "unknown")}；大小：${escape(r.actual?.bytes ?? "unknown")}<br>登记副本取得时间：${escape(r.acquired_at || "unknown")} · ${escape(r.acquired_at_status || "unknown")}</p>${list(fact.stale_reasons_at_read).length ? `<p class="message is-error">读取时旧关联：${escape(fact.stale_reasons_at_read.join("、"))}</p>` : ""}${list(r.declaration_comparisons).map(c=>`<p>声明 ${Number(c.declaration_index)+1} 与本次字节对照：${escape(c.byte_sha256_comparison)}；原声明取得时间：${escape(c.declared_acquired_at || "unknown")}</p>`).join("")}${verified && p?.text != null ? `<details><summary>查看本次 UTF-8 纯文本预览${p.truncated ? "（已截断至 12000 字符）" : ""}</summary><pre class="theory-raw artifact-preview">${escape(p.text)}</pre></details>` : `<p>纯文本预览：${escape(verified ? (p?.status || "unavailable") : "unavailable")}</p>`}<details><summary>本次完整核查事实</summary><pre class="theory-raw">${escape(JSON.stringify(displayFact,null,2))}</pre></details></article>`;
  }
  function renderArtifactAccess(item, project, service) {
    const ids = [...new Set([item.material.artifact, ...list(item.material.artifact_refs)].filter(Boolean).map(r=>r.artifact_id))];
    const unsaved = unsavedArtifactFacts(project.id,item.id);
    return `<section class="artifact-access"><h3>关联原件 · 显式核对</h3><p>当前注册器：${service?.available ? "可手动核对固定登记副本" : "不可用"}。页面加载不读取原件。字节一致不证明来源真实、支持范围、历史模型已读或数学正确；历史事实不代表当前文件状态。</p>${ids.map(id=>{
      const view = artifactViews[artifactKey(project.id,item.id,id)];
      return `<div class="artifact-controls"><p>${escape(id)}</p><button type="button" class="button button-secondary" data-action="artifact-describe" data-project-id="${escape(project.id)}" data-check-id="${escape(item.id)}" data-artifact-id="${escape(id)}">查看登记元数据</button><button type="button" class="button button-secondary" data-action="artifact-read" data-project-id="${escape(project.id)}" data-check-id="${escape(item.id)}" data-artifact-id="${escape(id)}">重新读取并保存核查事实</button>${view ? `<p role="status">${escape(view.message)}</p>${view.unsaved ? renderArtifactFact(view.unsaved,false) : ""}${view.metadata ? `<details open><summary>登记元数据 · 未读取字节</summary><pre class="theory-raw">${escape(JSON.stringify(view.metadata,null,2))}</pre></details>` : ""}` : ""}</div>`;
    }).join("") || "<p>本条尚无明确 artifact_id；来源文字或案例源哈希不授予原件读取关联。</p>"}${unsaved.map(f=>renderArtifactFact(f,false)).join("")}<details ${list(item.artifact_reads).length ? "open" : ""}><summary>已保存的原件核查历史 · ${list(item.artifact_reads).length} 次</summary>${list(item.artifact_reads).slice().reverse().map(f=>renderArtifactFact(f)).join("")}</details></section>`;
  }
  async function inspectArtifact(button) {
    if (!state.project || button.dataset.projectId !== state.project.id || state.mutating || leanBusy()) return;
    const projectId=state.project.id, recordId=button.dataset.checkId, artifactId=button.dataset.artifactId;
    const action=button.dataset.action === "artifact-read" ? "read" : "describe";
    const key=artifactKey(projectId,recordId,artifactId);
    $$(".checking-form").forEach(captureCheckDraft); captureTheoryDraft();
    state.mutating=true;syncMutationControls();
    try {
      const result=await api(`/api/projects/${encodeURIComponent(projectId)}/theory/checks/${encodeURIComponent(recordId)}/artifacts/${action}`,{method:"POST",body:JSON.stringify({artifact_id:artifactId})});
      artifactViews[key]={message:action === "read" ? (result.fact.result.status === "verified" && result.fact.result.byte_match === true ? "本次字节核对通过，事实已保存。" : `核对未通过（${result.fact.result.status}）；失败事实已保存。`) : `登记状态：${result.status}；本次未读取字节。`,metadata:action === "describe" ? result : null};
      if(action === "read") state.theoryWorkspaces[projectId]=await api(`/api/projects/${encodeURIComponent(projectId)}/theory`);
    } catch(error) {
      artifactViews[key]={message:(action === "describe" ? "元数据查询未完成；本次未读取字节。" : "本次读取未完成。") + error.message};
      if(error.body?.fact && error.body.saved === false) {
        const facts=unsavedArtifactFacts(projectId,recordId);facts.push(error.body.fact);
        try { localStorage.setItem(`yanzheng.artifactUnsaved.${projectId}:${recordId}`,JSON.stringify(facts)); }
        catch { artifactViews[key].unsaved=error.body.fact;artifactViews[key].message+=" 本机存储也不可用，请从下方复制结果。"; }
      }
      if(error.body?.saved === true) {
        try { state.theoryWorkspaces[projectId]=await api(`/api/projects/${encodeURIComponent(projectId)}/theory`); } catch {}
      }
    } finally {
      state.mutating=false;
      if(state.project?.id === projectId) renderTheory();
      syncMutationControls();
    }
  }
  function renderCheckingDerivation(material) {
    let parsed;
    try { parsed = JSON.parse(material.derivation); } catch {}
    if (parsed?.format !== "c-preserved-claim-fields-v1") return `<pre class="theory-raw">${escape(material.derivation)}</pre>`;
    return `<h3>原推导与核查边界</h3><p>${escape(parsed.derivation_verbatim)}</p><p>反例核查：${escape(parsed.counterexample_check)}</p><p>依赖论断：${escape(list(parsed.depends_on_claim_ids).join("、") || "无")}</p><p>待审阅项：${escape(list(parsed.unresolved_questions).join("；"))}</p><p>原核查时间：${escape(parsed.historical_reviewed_at)}；本次保存不增加真实数学审阅次数。</p><details><summary>逐项来源、定位与支持范围（外部声明）</summary>${list(parsed.evidence_refs).filter(e => e && typeof e === "object").map(e => `<article><p>${escape(e.original_path)}</p><p>位置：${escape(e.locator?.text)}；${escape(e.support_boundary)}</p><p>artifact_id：${escape(e.artifact_id)}<br>声明的原件字节 SHA256：${escape(e.byte_sha256 || "unknown")}<br>定位文本 artifact_id：${escape(e.locator?.locator_artifact_id || "unknown")}</p><p>历史模型输入归属：${escape(e.historical_model_input_membership || "unknown")}</p></article>`).join("")}</details>`;
  }
  function renderChecking(workspace, project) {
    if (!workspace.checking) return '<section class="panel theory-checking"><h2>论断与义务核查材料</h2><p>正在读取本项目的卡、路线与义务关联。</p></section>';
    const checking = workspace.checking, records = list(checking.records), targets = list(checking.targets);
    const draft = checkDraft(project.id, "new", checking);
    const caseDraft = checkDraft(project.id, "case-source", checking);
    const input = (d, name, label, max, required = true) => `<label>${label}<textarea name="${name}" rows="2" maxlength="${max}" ${required ? "required" : ""}>${escape(d[name] || "")}</textarea></label>`;
    const select = (d, name, label, values) => `<label>${label}<select name="${name}">${values.map(([value, text]) => `<option value="${escape(value)}" ${d[name] === value ? "selected" : ""}>${escape(text)}</option>`).join("")}</select></label>`;
    const propositions = [["P0","P0 · 原命题/反例"],["P1","P1 · 加强条件充分性"],["P2","P2 · 必要性边界"],["OTHER","其他明确对象"],["H14","H14 · 历史模型论断"],["HISTORICAL","历史事实"],["REVIEW_STATUS","审阅状态"]];
    return `<section class="panel theory-checking"><h2>论断与义务核查材料</h2><p>本地保存 ${records.length} 条材料，${new Set(records.map(r=>r.material.claim_id)).size} 个不同论断。核查意见不认证数学正确性；原义务保持开放、未形式核验。${escape(checking.model_scope || "这些材料不进入模型消息。")}</p><p role="status">${escape(checkMessages[project.id] || "")}</p>
      <details ${caseDraft.title ? "open" : ""}><summary>登记本项目案例来源版本</summary><p>已有 ${list(checking.cases).length} 个来源登记版本。明确登记后可选择案例级目标；不会创建问题卡或证明义务。来源哈希仅为声明；原件读取只针对保存材料中明确关联的固定登记 artifact_id。</p><form class="checking-form" data-check-id="case-source" data-project-id="${escape(project.id)}">${input(caseDraft,"case_id","稳定案例 ID（小写字母、数字、连字符）",80)}${input(caseDraft,"title","案例标题",200)}${input(caseDraft,"source_version","案例来源版本",300)}${input(caseDraft,"source_sha256","声明的源文件字节 SHA256（未知可留空）",64,false)}${input(caseDraft,"source_reference","案例来源说明与位置",2000)}<button type="submit" class="button button-primary">保存案例来源版本</button><button type="button" class="button button-secondary" data-action="check-rebase" data-check-id="case-source">核对后保留草稿并同步版本</button></form></details>
      ${targets.length ? `<details ${draft.claim_text ? "open" : ""}><summary>添加核查材料（选择案例/卡/义务）</summary><form class="checking-form" data-check-id="new" data-project-id="${escape(project.id)}">
      ${select(draft,"target_key","归属层级与实际目标：所属卡、路线与义务 / 案例",targets.map(t => [t.target_key,checkingTargetLabel(t)]))}
      <div class="checking-target">${checkingTarget(targets.find(t => t.target_key === draft.target_key))}</div>
      <div class="theory-fields">${select(draft,"case_key","关联的本项目案例来源版本（明确选择，可空）",[["","未关联案例"],...list(checking.cases).map(c=>[c.case_key,`${c.title} · ${c.source_version}${c.historical ? "（旧源版本）" : ""}`])])}${select(draft,"card_proposition","所属卡标签（案例级选择不归属卡）",[["","不归属卡 · 案例级"],...propositions])}${select(draft,"proposition","实际论证对象",propositions)}${input(draft,"claim_id","稳定论断 ID，如 C01",80)}${input(draft,"claim_text","论断原文",8000)}${input(draft,"assumptions","使用的假设与定义域",4000)}${input(draft,"support_scope","支持范围与不能推出的结论",4000)}${input(draft,"derivation","具体推导或反例核查材料",16000)}${select(draft,"record_origin","记录来源",[["researcher_draft","研究者草稿"],["assistant_check","助手核查（非人工审阅）"],["external_material","外部材料转录"]])}${input(draft,"source_reference","材料来源/版本（未知请明确填写 unknown）",2000)}${select(draft,"note_id","本项目笔记（可不关联）",[["","无笔记 · 来源未核实"],...list(checking.notes).map(n => [n.note_id,`${n.title} · ${n.locator}`])])}${input(draft,"locator","出处定位与阅读范围",2000)}</div>
      <details><summary>外部原件元数据（仅声明，不触发读取）</summary><p>只记录外部声明，不认证字节哈希。不把后补快照当历史模型已读；未知可留空。</p>${input(draft,"artifact_id","artifact_id",200,false)}${input(draft,"original_bytes_sha256","声明的原网页/文件字节 SHA256（非笔记哈希）",64,false)}${input(draft,"acquired_at","声明的取得时间（ISO 含时区）",80,false)}</details>
      <details class="checking-ref-editor"><summary>添加逐项材料引用</summary><p>逐项填写来源身份、版本、定位与支持范围；只保存声明，不注册或读取文件。</p><div class="checking-ref-fields">${renderReferenceEditor(draft)}</div><button type="button" class="button button-secondary" data-action="check-add-reference">增加一项来源</button><button type="button" class="button button-secondary" data-action="check-remove-reference">移除最后一项草稿来源</button></details><p>不同对象可关联：例如 P1 卡内保存 P0 反例，但不能据此证明 P1。保存后原材料不可覆盖，更正请另存一条。</p><button class="button button-primary" type="submit">保存核查材料</button><button class="button button-secondary" type="button" data-action="check-rebase" data-check-id="new">核对后保留草稿并同步版本</button></form></details>` : '<p>先登记本项目案例来源，或保存真实问题卡，即可选择其相应层级；义务级需要已有规划。</p>'}
      ${records.slice().reverse().map(item => {
        const m = item.material, d = checkDraft(project.id,item.id,checking), base = `/api/projects/${encodeURIComponent(project.id)}/theory/checks/${encodeURIComponent(item.id)}/export`;
        return `<details class="checking-record" data-check-record="${escape(item.id)}" ${Object.keys(artifactViews).some(k=>k.startsWith(`${project.id}:${item.id}:`)) || unsavedArtifactFacts(project.id,item.id).length ? "open" : ""}><summary>${escape(m.claim_id)} · ${escape(m.proposition)} · ${escape(item.target.obligation_id || ((item.target.target_scope === "case") ? "案例级" : "卡级"))} · ${item.human_review_status === "recorded_unverified" ? "人工意见已记录，未认证" : "人工审阅 pending"}</summary><p>${escape(m.claim_text)}</p>${checkingTarget(item.target)}${item.case_snapshot && item.target.target_scope !== "case" ? `<details><summary>关联案例来源版本</summary>${checkingCaseSource(item.case_snapshot)}</details>` : ""}${item.association_warning ? `<p class="message is-error">${escape(item.association_warning)}</p>` : ""}${list(item.stale_reasons).length ? '<p class="message is-error">旧关联：卡、证据、研究背景或案例源版本已变化。保留原材料与历史审阅，不自动迁移。</p>' : ""}<p>假设：${escape(m.assumptions)}</p><p>支持范围：${escape(m.support_scope)}</p>${renderCheckingDerivation(m)}${renderArtifactReferences(m)}<p>记录来源：${escape(m.record_origin)}；原件历史声明保持原样，当前核查见下方。</p>${renderArtifactAccess(item,project,checking.evidence_access)}<details><summary>来源版本与原始定位声明</summary><p>材料来源/版本：</p><pre class="theory-raw">${escape(m.source_reference)}</pre><p>原始定位：</p><pre class="theory-raw">${escape(m.locator)}</pre></details><details><summary>完整依据、哈希和历史快照</summary><pre class="theory-raw">${escape(JSON.stringify(item,null,2))}</pre></details>
        <h3>审阅记录 · 不认证身份或正确性</h3>${list(item.reviews).map(r => `<article><p>${escape(r.kind)} · ${escape(r.reviewer)} · ${escape(r.recorded_at)}</p><p>范围：${escape(r.scope)}</p><p>${escape(r.opinion)}</p></article>`).join("") || "<p>pending · 尚无审阅事件。</p>"}
        <form class="checking-form" data-check-id="${escape(item.id)}" data-project-id="${escape(project.id)}">${select(d,"kind","意见类型",[["assistant_check","助手核查"],["human_review","记录外部人工意见（身份未认证）"]])}${input(d,"reviewer","意见提供者（声明，非认证）",200)}${input(d,"scope","实际审阅范围",4000)}${input(d,"opinion","具体意见与待解决问题",8000)}<label><input type="checkbox" name="confirm_record_only" ${d.confirm_record_only ? "checked" : ""} required>确认仅记录意见，不关闭义务或认证证明。</label><button type="submit" class="button button-primary">保存审阅记录</button><button type="button" class="button button-secondary" data-action="check-rebase" data-check-id="${escape(item.id)}">核对后保留草稿并同步版本</button></form><p>保存时间：${escape(item.created_at)}；独立数学审阅 pending。</p><p>${item.target.obligation_id ? "义务 open" : "无绑定义务 · not_applicable"} · not_formally_verified</p><div class="theory-actions"><a class="button button-secondary" download href="${base}?format=json">本条 JSON</a><a class="button button-secondary" download href="${base}?format=md">本条 Markdown</a></div></details>`;
      }).join("")}</section>`;
  }
  async function rebaseCheck(id) {
    if (!state.project || state.mutating || leanBusy()) return;
    const projectId = state.project.id;
    const fresh = await api(`/api/projects/${encodeURIComponent(projectId)}/theory`);
    if (state.project?.id !== projectId) return;
    state.theoryWorkspaces[projectId] = fresh;
    const d = checkDraft(projectId,id,fresh.checking);
    d.expected_revision = id === "new" ? fresh.checking.revision : id === "case-source" ? fresh.checking.case_revision : fresh.checking.records.find(r => r.id === id)?.review_revision;
    if (id === "new") d.context_token = fresh.checking.context_token;
    d.request_id = crypto.randomUUID(); persistCheckDraft(projectId,id,d);
    checkMessages[projectId] = "草稿保留，已同步版本；请再次核对目标、笔记与审阅后保存。";
    captureTheoryDraft(); renderTheory();
  }
  async function saveCheck(form) {
    if (!state.project || form.dataset.projectId !== state.project.id || state.mutating || leanBusy()) return;
    captureCheckDraft(form); captureTheoryDraft();
    const projectId = state.project.id, id = form.dataset.checkId, d = checkDraft(projectId,id,checkingWorkspace());
    let body;
    if (id === "case-source") {
      body = Object.fromEntries(["request_id","expected_revision","case_id","title","source_version","source_reference"].map(k=>[k,d[k]]));
      body.source_sha256 = d.source_sha256 || null;
    } else if (id === "new") {
      body = Object.fromEntries(["request_id","expected_revision","context_token","target_key","claim_id","proposition","card_proposition","claim_text","assumptions","support_scope","derivation","record_origin","source_reference","locator"].map(k => [k,d[k]]));
      body.note_id = d.note_id || null;
      const target = list(checkingWorkspace().targets).find(t=>t.target_key === d.target_key);
      body.target_scope = target?.target_scope || "obligation";
      body.case_key = d.case_key || null;
      body.card_proposition = d.card_proposition || null;
      if (Number(d.ref_count)>0) body.artifact_refs = Array.from({length:Number(d.ref_count)},(_,i)=>Object.fromEntries(["artifact_id","label","source_version","locator","support_scope","origin","original_bytes_sha256","acquired_at"].map(k=>[k,d[`ref_${i}_${k}`] || (["original_bytes_sha256","acquired_at"].includes(k)?null:"")])));
      if (!d.artifact_id && (d.original_bytes_sha256 || d.acquired_at)) { toast("填写原件声明时需要 artifact_id；未知请留空。",true); return; }
      body.artifact = d.artifact_id ? {artifact_id:d.artifact_id, original_bytes_sha256:d.original_bytes_sha256 || null, acquired_at:d.acquired_at || null} : null;
    } else body = Object.fromEntries(["request_id","expected_revision","kind","scope","opinion","reviewer","confirm_record_only"].map(k => [k,d[k]]));
    state.mutating = true; syncMutationControls();
    try {
      const endpoint = id === "case-source" ? "cases" : `checks${id === "new" ? "" : `/${encodeURIComponent(id)}/reviews`}`;
      const result = await api(`/api/projects/${encodeURIComponent(projectId)}/theory/${endpoint}`, {method:"POST",body:JSON.stringify(body)});
      delete checkDrafts[`${projectId}:${id}`]; try { localStorage.removeItem(`yanzheng.checkDraft.${projectId}:${id}`); } catch {}
      if (state.project?.id === projectId) state.project = result;
      checkMessages[projectId] = "已保存；未请求模型，义务仍开放、未形式核验。";
    } catch (error) { checkMessages[projectId] = `${error.message} 本机草稿保留；核对后可显式同步版本。`; toast(checkMessages[projectId],true); }
    finally {
      state.mutating = false; delete state.theoryWorkspaces[projectId];
      if (state.project?.id === projectId) { await loadTheory(true); renderTheory(); }
      syncMutationControls();
    }
  }
  const failureDrafts = {};
  function failureDraft(projectId, item) {
    const key = `${projectId}:${item.id}`;
    if (!Object.hasOwn(failureDrafts, key)) {
      try { failureDrafts[key] = JSON.parse(localStorage.getItem(`yanzheng.failureDraft.${key}`) || "null"); } catch { /* optional local storage */ }
    }
    return failureDrafts[key] || { reason: "", expected_revision: item.review_revision, request_id: crypto.randomUUID() };
  }
  function renderTheoryFailures(workspace, project) {
    const failures = list(workspace.failure_records);
    if (!failures.length) return "";
    const categories = { structural_failure: "结构校验失败", service_failure: "模型服务失败", context_conflict: "上下文冲突" };
    return `<section class="panel theory-failures"><h2>失败材料与具体审阅</h2><p>共 ${failures.length} 条。原错误与人工数学意见分别保存；失败材料不能接受为已证明。</p>${[...failures].reverse().map(item => {
      const draft = failureDraft(project.id, item), capture = item.capture || {};
      return `<details class="theory-failure" data-failure-id="${escape(item.id)}"><summary>${escape(categories[item.category] || item.category)} · ${escape(item.source)} · 命题 v${Number(item.base_card_version)}</summary><p class="message is-error">${escape(item.diagnostic)}</p><p>原调用：${escape(item.source_call_id)} · 提示：${escape(item.prompt_version)} · ${item.source === "historical_replay" ? "历史调用 Token（本轮无新计费）" : "实际总 Token"}：${escape(item.call?.usage?.total_tokens ?? "未知")}</p><p>保存范围：${escape(capture.kind)}；${capture.truncated ? "已截断，不能完整重放" : capture.complete ? "已完整取得" : "未取得正文"}${capture.redacted ? "；凭据回显已脱敏" : ""}。上限 ${Number(capture.limit_characters) || 50000} 字符。</p><details><summary>原文与来源详情</summary><pre class="theory-raw">${escape(capture.text || "（没有可保存的正文）")}</pre><pre class="theory-raw">${escape(JSON.stringify({ call: item.call, historical_origin: item.historical_origin, card_sha256: item.card_sha256, input_sha256: item.input_sha256, capture: { ...capture, text: undefined } }, null, 2))}</pre><details><summary>当时的完整输入上下文</summary><pre class="theory-raw">${escape(JSON.stringify(item.input_messages, null, 2))}</pre></details></details><h3>人工审阅意见 · 未独立核验</h3>${list(item.reviews).map(r => `<p class="theory-review-record">${escape(r.reason)}<small>${escape(date(r.reviewed_at, true))}</small></p>`).join("") || "<p>尚无人工意见；结构失败不能推出命题为假。</p>"}<label>具体反馈<textarea rows="4" maxlength="2000" data-failure-review="${escape(item.id)}" data-project-id="${escape(project.id)}" placeholder="指出原文中的具体步骤、错误原因及需要核查的依据">${escape(draft.reason)}</textarea></label>${draft.expected_revision !== item.review_revision ? '<p class="message is-error">审阅已更新，本机草稿仍保留。请核对历史意见，点击同步版本后提交。</p><button type="button" class="button button-secondary" data-action="failure-rebase" data-failure-id="'+escape(item.id)+'">保留草稿并同步审阅版本</button>' : ""}<div class="theory-actions"><button type="button" class="button button-secondary" data-action="failure-review" data-failure-id="${escape(item.id)}">保存具体反馈</button><a class="button button-secondary" download href="/api/projects/${encodeURIComponent(project.id)}/theory/failures/${encodeURIComponent(item.id)}/export">导出本条原始材料</a><a class="button button-secondary" download href="/api/projects/${encodeURIComponent(project.id)}/theory/failures/${encodeURIComponent(item.id)}/export?format=md">导出本条 Markdown</a></div></details>`;
    }).join("")}</section>`;
  }
  function renderNextContext(projectId) {
    const preview = theoryContextPreviews[projectId];
    if (!preview) return '<p class="theory-notice">先保存命题卡，再检查下一轮材料；检查不会请求模型。</p>';
    const labels = { stale_context: "原卡、研究背景或证据上下文已变化", missing_context_basis: "缺少生成时上下文依据，原件保留", invalid_context_basis: "原输入依据完整性校验失败", no_original_content: "缺少原提交内容", no_review: "尚无具体反馈", no_model_content: "没有模型正文", partial_or_redacted_content: "原文不完整或已脱敏", older_than_latest_two: "不在最近两条适用反馈内", cross_project: "跨项目" };
    return `<details class="theory-next-context" open><summary>下一轮材料预览 · 尚未发送</summary><p>核查材料与其审阅仅本地保存，不进入本次模型消息。</p><p>${preview.blocked ? escape(preview.detail) : `${preview.context_characters} / ${preview.limit_characters} 字符；最近两条适用反馈`}</p>${list(preview.feedback_dispositions || preview.failures).map(item => `<p>${escape(({proposal_rejection:"提议审阅",legacy_rejected_output:"旧结构拒绝",failure_record:"失败材料"})[item.record_kind] || "失败材料")} · ${escape(item.source_call_id || item.record_id || item.failure_id)}：${item.included ? (preview.blocked ? "适用，但总预算阻止发送" : "将带入原文及具体意见") : "不带入 · " + escape(labels[item.exclusion] || item.exclusion)}</p>`).join("")}<details><summary>检查实际将发送的完整消息</summary><pre class="theory-raw">${escape(JSON.stringify(preview.messages, null, 2))}</pre></details></details>`;
  }
  async function inspectNextContext(button) {
    if (!state.project || state.mutating || leanBusy()) return;
    captureTheoryDraft();
    const projectId = state.project.id;
    if (theoryDraft(projectId)) { toast("请先保存命题草稿，再检查下一轮材料。", true); return; }
    const done = busy(button, "检查材料");
    try {
      const preview = await api(`/api/projects/${encodeURIComponent(projectId)}/theory/next-context`);
      theoryContextPreviews[projectId] = preview;
      if (state.project?.id === projectId) renderTheory();
    } catch (error) { toast(error.message, true); }
    finally { done(); syncMutationControls(); }
  }
  async function saveFailureReview(button, rebase = false) {
    if (!state.project || state.mutating || leanBusy()) return;
    const projectId = state.project.id, failureId = button.dataset.failureId;
    const workspace = state.theoryWorkspaces[projectId] || state.project.theory || {};
    const item = list(workspace.failure_records).find(r => r.id === failureId);
    if (!item) return;
    const key = `${projectId}:${failureId}`, draft = failureDraft(projectId, item);
    if (rebase) {
      draft.expected_revision = item.review_revision; draft.request_id = crypto.randomUUID();
      failureDrafts[key] = draft;
      try { localStorage.setItem(`yanzheng.failureDraft.${key}`, JSON.stringify(draft)); } catch {}
      captureTheoryDraft(); renderTheory(); return;
    }
    if (!draft.reason.trim()) { toast("请填写具体审阅意见。", true); return; }
    captureTheoryDraft();
    state.mutating = true; syncMutationControls();
    try {
      const result = await api(`/api/projects/${encodeURIComponent(projectId)}/theory/failures/${encodeURIComponent(failureId)}/reviews`, { method: "POST", body: JSON.stringify(draft) });
      delete failureDrafts[key]; delete theoryContextPreviews[projectId];
      try { localStorage.removeItem(`yanzheng.failureDraft.${key}`); } catch {}
      if (state.project?.id === projectId) state.project = result;
      toast("具体反馈已保存；未请求模型，数学结论仍待核查。");
    } catch (error) { toast(error.message, true); }
    finally {
      state.mutating = false;
      delete state.theoryWorkspaces[projectId];
      if (state.project?.id === projectId) { await loadTheory(true); renderTheory(); }
      syncMutationControls();
    }
  }
  function renderTheory() {
    const target = $("#experiments-content"), project = state.project;
    const tabs = workbenchTabs();
    const exampleButton = `<button type="button" class="button button-secondary" data-action="theory-example">${icon("plus")}创建独立公开案例</button>`;
    const exampleNote = '<p class="theory-notice">公开案例：逐点收敛与一致收敛。用于检查流程可靠性，包含预置资料和待确认修订；不调用模型、不检索、不代表新定理。</p>';
    if (!project) {
      target.innerHTML = tabs + `<section class="panel theory-intro"><span class="section-kicker">FROM CONJECTURE TO ARGUMENT</span><h2>把一个数学想法，拆成能审阅的论证。</h2><p>从研究项目开始，保存定义、假设和目标；让每次修订都能回到具体依据。</p><div class="theory-actions"><button type="button" class="button button-primary" data-action="new">新建研究 ${icon("arrow-right")}</button>${exampleButton}</div>${exampleNote}</section>`;
      syncMutationControls(); return;
    }
    const workspace = state.theoryWorkspaces[project.id] || project.theory || {}, draft = theoryDraft(project.id), card = draft?.card || workspace.card || blankTheoryCard();
    const version = Number(workspace.card_version) || 0, pending = workspace.pending_proposal, notes = list(workspace.evidence_notes), proposals = list(workspace.proposals);
    const stale = draft && draft.baseVersion !== version;
    const picker = `<div class="literature-toolbar"><label class="project-picker" for="experiment-project">当前研究项目<select id="experiment-project"><option value="">请选择研究项目</option>${state.projects.map(item => `<option value="${escape(item.id)}" ${item.id === project.id ? "selected" : ""}>${escape(item.title)}</option>`).join("")}</select></label><button class="button button-secondary" type="button" data-action="return-research">${icon("arrow-left")}返回当前研究</button></div>`;
    const input = (name, rows = 3) => `<label for="theory-${name}" class="${["statement", "target"].includes(name) ? "theory-wide" : ""}"><span>${theoryFields[name]}${["definitions", "assumptions"].includes(name) ? '<small>每行一项</small>' : ""}</span>${["title", "domain"].includes(name) ? `<input id="theory-${name}" name="${name}" maxlength="${name === "title" ? 160 : 300}" required value="${escape(card[name] || "")}" placeholder="${name === "title" ? "例如：连续函数列的极限何时连续？" : "例如：实分析；区间 [0, 1] 上的实值函数"}">` : `<textarea id="theory-${name}" name="${name}" rows="${rows}" maxlength="${{ statement: 4000, target: 2000, definitions: 12000, assumptions: 12000 }[name]}" ${["statement", "target"].includes(name) ? "required" : ""} placeholder="${{ statement: "准确写出待研究命题，包含量词与定义域。", definitions: "例如：逐点收敛是对每个固定 x 分别取极限。", assumptions: "例如：每个 f_n 在定义域上连续。", target: "写出希望证明、解释、修订或寻找反例的具体目标。" }[name]}">${escape(Array.isArray(card[name]) ? card[name].join("\n") : card[name] || "")}</textarea>`}</label>`;
    const exportBase = `/api/projects/${encodeURIComponent(project.id)}/theory/export`;
    const diffValue = value => Array.isArray(value) ? value.join("\n") : value || "（空）";
    const reviewEditor = pending ? `<label class="theory-review-editor" for="theory-review-reason">审阅备注（可选）<textarea id="theory-review-reason" maxlength="2000" rows="3" data-project-id="${escape(project.id)}" data-proposal-id="${escape(pending.id)}" placeholder="例如：第 2 个引理遗漏了定义域条件；请记录你拒绝本次建议的具体理由。">${escape(theoryReviewDrafts[`${project.id}:${pending.id}`] || "")}</textarea><small>拒绝时会随本次建议归档，最多 2000 字。</small></label>` : "";
    const proposal = pending ? `<section class="panel theory-proposal"><div class="theory-section-head"><div><span class="section-kicker">REVIEW BEFORE APPLYING</span><h2>待你确认的修订</h2></div><span class="stage-pill purple">${pending.source === "curated_example" ? "公开案例预置" : "千问建议"} · 尚未应用</span></div><p>${escape(pending.revision_reason)}</p>${renderTheoryProposalMetadata(pending, project)}<p class="theory-notice">基于命题 v${Number(pending.base_card_version) || 0}。${pending.source === "curated_example" ? "这是预置示例，并非本次调用千问生成。" : "模型建议需要审阅；接受后才更新项目命题。"}</p><div class="theory-diff">${list(pending.diff).map(change => `<section><h3>${escape(theoryFields[change.field] || change.field)}</h3><div><article><span>当前版本</span><p>${escape(diffValue(change.before))}</p></article><article><span>建议修订</span><p>${escape(diffValue(change.after))}</p></article></div></section>`).join("") || '<p>本次没有命题字段差异，可审阅证明路线与依据。</p>'}</div><details class="theory-revised-card"><summary>查看完整建议命题 ${icon("chevron")}</summary><dl>${Object.entries(theoryFields).map(([key, label]) => `<div><dt>${label}</dt><dd>${escape(diffValue(pending.revised_card?.[key]))}</dd></div>`).join("")}</dl></details><h3 class="theory-subtitle">建议的证明路线</h3>${renderProofRoutes(pending.proof_routes, true)}${list(pending.search_leads).length ? `<div class="theory-search-leads"><h3>后续检索线索 · 尚未执行查询</h3>${stringList(pending.search_leads)}</div>` : ""}${reviewEditor}<div class="theory-actions"><button type="button" class="button button-primary" data-action="theory-accept" data-proposal-id="${escape(pending.id)}" ${draft ? "disabled" : ""}>${icon("check")}接受修订与路线</button><button type="button" class="button button-secondary" data-action="theory-reject" data-proposal-id="${escape(pending.id)}">拒绝本次建议</button></div><p class="theory-notice">接受表示采用研究规划，不能标记定理已证明。${draft ? "本机还有未保存草稿；接受修订前请先保存或放弃草稿。" : ""}</p></section>` : "";
    target.innerHTML = tabs + picker + `<section class="panel theory-card"><div class="theory-section-head"><div><span class="section-kicker">MATHEMATICAL PROBLEM CARD</span><h2>命题与假设卡</h2></div><span class="stage-pill">${version ? `项目已保存 v${version}` : "尚未保存命题"}</span></div><p class="theory-notice">先写清对象、定义域、量词和目标。命题草稿不会改变已选研究方向；模型修订始终等待你确认。</p>${stale ? `<div class="message is-error">项目命题已更新至 v${version}，本机草稿基于 v${Number(draft.baseVersion)}。请先复制需要保留的内容，再放弃旧草稿以载入最新命题；程序会拒绝旧版本覆盖。</div>` : ""}<form id="theory-card-form"><div class="theory-fields">${input("title")}${input("domain")}${input("statement", 4)}${input("definitions")}${input("assumptions")}${input("target")}</div><div class="theory-actions"><button type="submit" class="button button-primary">${icon("check")}保存命题卡</button>${draft ? '<button type="button" class="button button-secondary" data-action="theory-discard-draft">放弃本机草稿</button>' : ""}<span id="theory-draft-status">${draft ? "本机草稿已恢复 · 尚未保存到项目" : "修改会按项目保留在本机；点击保存后进入项目版本记录"}</span></div></form></section><section class="panel theory-research"><span class="section-kicker">EVIDENCE BEFORE REVISION</span><h2>依据阅读资料，提出下一步。</h2><p>先保存命题卡并检查下一轮材料。再点击请求，才将预览的命题、项目背景、适用反馈与阅读笔记发送至百炼，消耗 API 额度。不会自动检索新文献或接受建议。</p><details class="theory-reading-list"><summary>可用于本次建议的阅读笔记 <span>${notes.length} 条</span>${icon("chevron")}</summary><p>${escape(workspace.evidence_scope || "仅使用可定位的有效确认笔记；关联判断仍需审阅。")}${Number(workspace.omitted_note_count) > 0 ? ` 另有 ${Number(workspace.omitted_note_count)} 条较早笔记未纳入本次请求。` : ""}</p>${notes.length ? notes.map(note => renderTheoryEvidence(note)).join("") : '<p>尚无可引用的确认笔记。可先去文献工作台阅读、核对并记录位置；缺少依据时，建议应保留证据缺口。</p>'}</details>${renderTheoryFeedback(workspace)}${renderNextContext(project.id)}<div class="theory-actions"><button type="button" class="button button-secondary" data-action="theory-preview">检查下一轮材料</button><button type="button" class="button button-primary" data-action="theory-propose">${icon("sparkles")}请千问提出修订</button><button type="button" class="button button-secondary" data-action="literature">${icon("book")}查阅与记录文献</button></div><p class="theory-notice">${state.status?.model?.configured ? `当前模型：${escape(state.status.model.model || "千问")}。` : "千问未配置，可编辑保存并体验独立公开案例。"}证明路线是待核查规划；下方可单独运行固定 Lean 案例；它不核验这些自然语言证明路线。</p><div id="theory-message" class="message" role="alert" ${state.theoryErrors[project.id] || workspace.last_error ? "" : "hidden"}>${escape(state.theoryErrors[project.id] || workspace.last_error?.message || (typeof workspace.last_error === "string" ? workspace.last_error : ""))}</div></section>${renderTheoryFailures(workspace, project)}${renderChecking(workspace, project)}${proposal}<section class="panel theory-confirmed"><div class="theory-section-head"><div><span class="section-kicker">CONTINUE THE ARGUMENT</span><h2>已确认的证明规划</h2></div><span class="stage-pill">未形式核验</span></div>${renderProofRoutes(workspace.proof_routes)}<div class="theory-actions"><a class="button button-secondary" href="${exportBase}?format=md" download>${icon("download")}导出 Markdown</a><a class="button button-secondary" href="${exportBase}?format=json" download>导出完整 JSON</a></div><p class="theory-notice">导出项目已保存的命题、建议与状态；本机未保存草稿不在导出中。</p></section>${leanUI ? leanUI.render(project) : '<section class="panel lean-panel" id="lean-workspace"><h2>Lean 界面未加载</h2><p>请刷新页面；当前命题和理论记录不受影响。</p></section>'}${renderTheoryProposalHistory(workspace, project)}${renderTheoryVersions(workspace)}<section class="theory-example-block">${exampleButton}${exampleNote}</section>`;
    syncMutationControls();
    const accept = $('[data-action="theory-accept"]'); if (accept && draft) accept.disabled = true;
  }
  async function performTheory(action, button, proposalId = "") {
    if (!state.project || state.mutating || state.literatureBusy || state.experimentBusy || leanBusy()) return;
    const form = $("#theory-card-form");
    const draft = action === "save" ? captureTheoryDraft() : theoryDraft(state.project.id);
    if (action === "save" && !form?.reportValidity()) return;
    if (action === "save" && ["definitions", "assumptions"].some(key => draft.card[key].length > 12 || draft.card[key].some(item => item.length > 1000))) { const error = "定义与假设各最多 12 项，每项最多 1000 字；请分行填写并精简内容。"; message($("#theory-message"), error); toast(error, true); return; }
    if (action === "accept" && draft) { toast("请先保存或放弃本机草稿，再接受修订。", true); return; }
    if (action === "propose" && (!state.status?.model?.configured || theoryDraft(state.project.id) || !theoryContextPreviews[state.project.id] || theoryContextPreviews[state.project.id].blocked)) return;
    const projectId = state.project.id, token = state.opening, base = `/api/projects/${encodeURIComponent(projectId)}/theory`;
    state.mutating = true; state.theoryBusy = true;
    const done = busy(button, action === "propose" ? "千问正在提出建议" : "正在保存"); syncMutationControls();
    delete state.theoryErrors[projectId];
    try {
      if (action === "save") {
        const saved = await api(`${base}/card`, { method: "POST", body: JSON.stringify({ card: draft.card, expected_version: draft.baseVersion }) });
        if (token !== state.opening || state.project?.id !== projectId) return;
        state.project = saved.project || saved; clearTheoryDraft(projectId); delete theoryContextPreviews[projectId]; delete state.theoryWorkspaces[projectId]; leanUI?.invalidate(projectId);
      }
      if (action !== "save") {
        const path = action === "propose" ? `${base}/propose` : `${base}/proposals/${encodeURIComponent(proposalId)}/${action}`;
        const requestBody = action === "reject" ? { reason: String($("#theory-review-reason")?.value ?? theoryReviewDrafts[`${projectId}:${proposalId}`] ?? "").trim() } : action === "propose" ? { context_token: theoryContextPreviews[projectId].context_token } : {};
        const result = await api(path, { method: "POST", body: JSON.stringify(requestBody) });
        if (token !== state.opening || state.project?.id !== projectId) return;
        state.project = result.project || result; delete state.theoryWorkspaces[projectId]; leanUI?.invalidate(projectId);
        if (action === "reject") delete theoryReviewDrafts[`${projectId}:${proposalId}`];
      }
      toast({ save: "命题已保存，版本记录可恢复。", propose: "修订建议已保存，等待你审阅确认。", accept: "已采用修订与证明规划，仍需数学核查。", reject: "已拒绝本次建议，原命题保留。" }[action]);
    } catch (error) {
      state.theoryErrors[projectId] = error.message;
      try { const latest = await api(`/api/projects/${encodeURIComponent(projectId)}`); if (token === state.opening && state.project?.id === projectId) { state.project = latest.project || latest; delete state.theoryWorkspaces[projectId]; } } catch { /* Preserve the last saved record and local draft. */ }
      if (token === state.opening && state.project?.id === projectId) toast(error.message, true);
    } finally {
      delete theoryContextPreviews[projectId];
      state.mutating = false; state.theoryBusy = false; done(); syncMutationControls();
      if (token === state.opening && state.project?.id === projectId) { renderProject(); await refreshProjects(false); if (state.view === "experiments") { renderExperiments(); await loadTheory(true); } }
    }
  }
  async function createTheoryExample(button) {
    if (state.mutating || state.literatureBusy || state.experimentBusy || leanBusy()) return;
    captureTheoryDraft();
    const token = state.opening;
    state.mutating = true; const done = busy(button, "正在创建独立案例"); syncMutationControls();
    try {
      const result = await api("/api/theory/examples", { method: "POST", body: JSON.stringify({ name: "uniform-continuity" }) });
      if (token !== state.opening) return;
      state.project = result.project || result; ++state.opening; remember(state.project.id); chooseValidationMode("theory");
      renderProject(); await refreshProjects(false); setView("experiments");
      toast("已创建独立公开案例；原研究项目与本机草稿均保留。");
    } catch (error) { if (token === state.opening) { toast(error.message, true); message($("#theory-message") || $("#global-message"), error.message); } }
    finally { state.mutating = false; done(); syncMutationControls(); }
  }
  function renderExperimentRun(run, projectId) {
    const number = value => Number.isFinite(value) ? Number(value).toPrecision(4) : "未记录";
    const range = value => value ? `${number(value.median)} [${number(value.min)}, ${number(value.max)}]` : "未记录";
    const succeeded = run.status === "success";
    const base = `/api/projects/${encodeURIComponent(projectId)}/experiments/${encodeURIComponent(run.id)}/export`;
    const metrics = run.metrics || {}, checks = run.checks || {};
    return `<article class="panel experiment-run"><div class="search-run-head"><div><span class="section-kicker">FIXED LOCAL WORKER · ${escape(date(run.finished_at || run.created_at, true))}</span><h2>${escape(run.selected_topic_snapshot?.title || run.selected_topic_snapshot?.topic?.title || "随机低秩逼近实验")}</h2></div><span class="search-status ${succeeded ? "success" : "failed"}">${succeeded ? "实际运行完成" : "本轮运行失败"}</span></div>${run.attachment?.selected_topic_matches_current === false ? '<p class="experiment-association">此记录属于运行时的选题快照；当前课题可能已经修订。</p>' : ""}${!succeeded ? `<div class="message is-error"><strong>${escape(run.error?.message || "本轮未完成，请调整参数或稍后重试。")}</strong><p>失败记录已保存，不能据此形成实验结论。</p></div>` : ""}<div class="archive-stats">${[[metrics.matrix_count, "固定数据集"], [metrics.configuration_count, "参数配置"], [metrics.randomized_run_count, "随机运行"], [metrics.baseline_run_count, "基线运行"]].map(([value, label]) => `<div><strong>${Number(value) || 0}</strong><span>${label}</span></div>`).join("")}</div><p class="experiment-check ${checks.passed ? "is-passed" : ""}">${succeeded ? checks.passed ? "程序数值检查通过" : "程序检查未全部通过，请先核查原始数据" : "运行未完成"}${checks.all_numeric_data_finite === true ? " · 数值均有限" : ""}${Number.isInteger(checks.significantly_below_optimum_count) ? ` · 显著低于最优秩误差的异常数量：${checks.significantly_below_optimum_count}` : ""}</p>${list(run.datasets).map(dataset => `<section class="experiment-dataset"><h3>${escape(dataset.name || dataset.id)}</h3><p>尺寸 ${escape(list(dataset.shape).join(" × "))} · 目标秩 ${escape(dataset.target_rank)} · 基线相对 Frobenius 误差 ${number(dataset.baseline?.relative_error)} · 基线耗时中位数 ${number(dataset.baseline?.timing_ms?.median)} ms</p><div class="experiment-table-scroll" tabindex="0" role="region" aria-label="${escape(dataset.name || dataset.id)}配置结果"><table class="experiment-table"><thead><tr><th>p / q</th><th>采样宽度 / 矩阵遍历</th><th>相对误差</th><th>误差 / 基线</th><th>耗时 ms</th></tr></thead><tbody>${list(run.groups).filter(group => group.dataset_id === dataset.id).map(group => `<tr><td>${escape(group.p)} / ${escape(group.q)}</td><td>${escape(group.sample_width)} / ${escape(group.matrix_passes)}</td><td>${range(group.relative_error)}</td><td>${range(group.baseline_error_ratio)}</td><td>${range(group.timing_ms)}</td></tr>`).join("")}</tbody></table></div><p class="experiment-table-note">表内为采样重复的中位数 [最小值, 最大值]。耗时受环境和矩阵规模影响。</p><details class="record-limitations"><summary>数据指纹与构造 ${icon("chevron")}</summary><p class="matrix-fingerprint">SHA-256：${escape(dataset.matrix_sha256)}</p><pre>${escape(typeof dataset.construction === "string" ? dataset.construction : JSON.stringify(dataset.construction || {}, null, 2))}</pre></details></section>`).join("")}<details class="record-limitations"><summary>参数、检查与运行环境 ${icon("chevron")}</summary><pre>${escape(JSON.stringify({ config: run.config, checks: run.checks, environment: run.environment, provenance: run.provenance }, null, 2))}</pre></details><div class="archive-limitations"><h3>结论边界</h3>${stringList(run.limitations || ["仅限当前合成矩阵、参数和运行环境；不代表新算法、一般理论保证或普遍提速。"] )}</div><div class="experiment-downloads"><a class="button button-secondary button-small" href="${base}?format=json" download>${icon("download")}完整 JSON</a><a class="button button-secondary button-small" href="${base}?format=csv" download>逐次结果 CSV</a></div></article>`;
  }
  function captureExperimentDraft() {
    const form = $("#experiment-form");
    if (form && state.project) state.experimentDrafts[state.project.id] = Object.fromEntries(new FormData(form));
  }
  async function runExperiment(form) {
    if (!state.project || state.mutating || state.literatureBusy || state.experimentBusy || leanBusy()) return;
    captureExperimentDraft();
    const raw = Object.fromEntries(new FormData(form)), body = {};
    const parseValues = value => String(value).split(/[,，\s]+/).filter(Boolean).map(Number);
    for (const key of ["rows", "cols", "rank", "repeats", "data_seed"]) body[key] = Number(raw[key]);
    body.p_values = parseValues(raw.p_values); body.q_values = parseValues(raw.q_values);
    if (Object.values(body).flat().some(value => !Number.isInteger(value)) || !body.p_values.length || !body.q_values.length || body.p_values.length > 3 || body.q_values.length > 3) { message($("#experiment-message"), "请填写整数参数，p 和 q 各填写 1—3 个整数，用逗号分隔。"); return; }
    const projectId = state.project.id, token = state.opening;
    state.experimentBusy = true; syncMutationControls();
    const done = busy($("button[type=submit]", form), "本地实验正在运行");
    let failure = "";
    try {
      const result = await api(`/api/projects/${encodeURIComponent(projectId)}/experiments`, { method: "POST", body: JSON.stringify(body) });
      if (token !== state.opening || state.project?.id !== projectId) { await refreshProjects(false); return; }
      state.project = result.project || result;
      delete state.experimentWorkspaces[projectId];
      renderProject(); await refreshProjects(false);
      const run = list(state.project.experiments).at(-1);
      toast(run?.status === "success" ? "本轮实际结果与参数已保存。" : "实验未完成，失败状态已保存。", run?.status !== "success");
    } catch (error) { failure = error.message; toast(failure, true); }
    finally {
      state.experimentBusy = false; done(); syncMutationControls();
      if (state.view === "experiments" && token === state.opening && state.project?.id === projectId) { await loadExperiments(true); if (failure) message($("#experiment-message") || $("#global-message"), failure); }
    }
  }
  async function reviseFromExperiments(button) {
    if (!state.project || state.mutating || state.literatureBusy || state.experimentBusy || leanBusy() || !state.status?.model?.configured) return;
    const projectId = state.project.id, token = state.opening;
    state.mutating = true; state.generating = true; syncMutationControls();
    const done = busy(button, "千问正在依据结果修订");
    try {
      if (state.project.stage === "topic_selected") {
        const reopened = await api(`/api/projects/${encodeURIComponent(projectId)}/reopen`, { method: "POST" });
        state.project = reopened.project || reopened;
        delete state.experimentWorkspaces[projectId];
      }
      const result = await api(`/api/projects/${encodeURIComponent(projectId)}/advance`, { method: "POST", body: "{}" });
      if (token !== state.opening || state.project?.id !== projectId) return;
      state.project = result.project || result; delete state.reasonDrafts[projectId]; delete state.experimentWorkspaces[projectId];
      renderProject(); setView("explore"); await refreshProjects();
      toast("结果摘要已用于修订；请审查后重新选择研究方向。");
    } catch (error) {
      try { const latest = await api(`/api/projects/${encodeURIComponent(projectId)}`); if (token === state.opening && state.project?.id === projectId) state.project = latest.project || latest; } catch { /* Keep the last saved project. */ }
      renderProject(); setView("explore"); message($("#global-message"), error.message); toast(error.message, true);
    } finally { state.mutating = false; state.generating = false; done(); syncMutationControls(); await refreshModelStatus(); }
  }
  async function loadLibrary(force = false) {
    if (state.library && !force) { renderLibrary(); return; }
    if (state.libraryLoading) return;
    state.libraryLoading = true;
    $("#library-content").innerHTML = '<div class="loading-state"><span class="spinner" aria-hidden="true"></span>正在读取已有案例与证据……</div>';
    try { state.library = await api("/api/library"); renderLibrary(); }
    catch (error) { $("#library-content").innerHTML = `<div class="message is-error">${escape(error.message)}</div><button class="button button-secondary" type="button" data-action="reload-library">重试读取</button>`; }
    finally { state.libraryLoading = false; }
  }
  function artifactLink(id, label = "下载原始 JSON") { return `<a class="button button-secondary button-small" href="/api/library/artifacts/${encodeURIComponent(id)}" download>${icon("download")}${escape(label)}</a>`; }
  function renderLibrary() {
    const library = state.library;
    if (!library) return;
    const experiment = library.experiment, sources = list(library.literature), cases = list(library.cases);
    const category = { award: "获奖案例", research: "科研系统", product: "产品案例" };
    $("#library-content").innerHTML = `<div class="workflow-link"><div><strong>从已有证据学习，把问题带回研究。</strong><p>此页只读取此前保存的档案，不重新检索或执行实验。</p></div><button class="button button-primary" type="button" data-action="new">从一个问题开始 ${icon("arrow-right")}</button></div>${list(library.limitations).length ? `<div class="archive-notice">${stringList(library.limitations)}</div>` : ""}${experiment ? `<section class="panel archived-experiment"><div class="search-run-head"><div><span class="section-kicker">ARCHIVED EXPERIMENT · ${escape(date(experiment.created_at, true))}</span><h2>${escape(experiment.title)}</h2></div><span class="stage-pill">已保存实验</span></div><div class="archive-stats">${[[experiment.matrix_count, "固定矩阵"], [experiment.configuration_count, "配置组合"], [experiment.randomized_run_count, "随机运行"], [experiment.baseline_run_count, "基线运行"]].map(([count, label]) => `<div><strong>${Number(count) || 0}</strong><span>${label}</span></div>`).join("")}</div><p>矩阵尺寸 ${escape(list(experiment.shape).join(" × "))} · 目标秩 ${escape(experiment.target_rank)} · 本轮没有新执行实验。</p><details class="record-limitations"><summary>矩阵与可追溯信息 ${icon("chevron")}</summary>${list(experiment.matrices).map(matrix => `<div class="matrix-record"><strong>${escape(matrix.name || matrix.id)}</strong><p>ID：${escape(matrix.id)}</p><code>SHA-256：${escape(matrix.matrix_sha256)}</code></div>`).join("")}</details><div class="archive-limitations"><h3>这些数据能说明什么</h3>${stringList(experiment.limitations)}</div>${experiment.artifact_id ? artifactLink(experiment.artifact_id, "下载实验原始数据") : ""}</section>` : '<div class="archive-notice">实验档案暂不可用，未显示推测的运行数据。</div>'}<section class="panel archived-reading"><span class="section-kicker">PREVIOUS CONTENT REVIEW</span><h2>已有阅读记录</h2><p>阅读等级和位置沿用此前记录，不代表本项目刚刚读取或验证了论文。</p>${sources.length ? sources.map(source => `<details class="archived-source"><summary><span>${escape(source.title)}<small>${sourceVerification(source.verification)}</small></span>${icon("chevron")}</summary>${renderSource(source)}<div class="archive-evidence"><h3>此前记录的内容关联</h3>${renderEvidence({ evidence: source.evidence || [] }, { sources: [source] })}</div>${source.artifact_id ? artifactLink(source.artifact_id, "下载阅读与检索记录") : ""}</details>`).join("") : '<p>当前没有可展示的已读资料。</p>'}</section><div class="direction-head library-cases-head"><h3>真实案例与架构启发</h3><span>核查日期：${escape(library.case_reviewed_on || "未记录")}</span></div><p class="literature-record-note">分别标明获奖、研究与产品案例。来源的实际阅读范围决定本页的说明边界。</p><div class="case-grid">${cases.map(item => `<article class="panel case-card"><div class="case-card-top"><span class="stage-pill">${category[item.category] || "案例"}</span><span>${escape(item.status_label)}</span></div><h2>${escape(item.name)}</h2><p>${escape(item.problem)}</p><h3>工作流程</h3>${stringList(item.pipeline, true)}<h3>可以带回研证的做法</h3><p>${escape(item.takeaway)}</p><details class="record-limitations"><summary>边界与来源 ${icon("chevron")}</summary>${stringList(item.limits)}${list(item.sources).map(source => { const url = safeUrl(source.url); return `<div class="case-source">${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${escape(source.title)} ↗</a>` : `<span>${escape(source.title)}</span>`}<p>实际阅读范围：${escape(source.read_scope || "未记录")}</p></div>`; }).join("")}</details></article>`).join("")}</div><section class="panel archive-downloads"><span class="section-kicker">ORIGINAL ARTIFACTS</span><h2>完整档案</h2><p>下载原始记录，查看字段、来源与此前执行事实。</p><div>${list(library.artifacts).map(artifact => artifactLink(artifact.id, artifact.title)).join("")}</div></section>`;
  }
  function renderCapabilities() {
    const status = state.status;
    if (!status) return;
    const capabilities = status.capabilities || {}, model = status.model || {};
    const available = [status.storage === "sqlite" ? "真实本地保存：简报、回答、选择、题录、阅读笔记与实验可恢复导出" : null, capabilities.literature_search === "available" ? "公开题录检索：Crossref / arXiv，记录成功、无结果与失败" : null, capabilities.reading_notes ? "用户确认阅读：分开保存摘要或相关正文的笔记、位置和日期" : null, capabilities.theory_workspace ? "理论论证：命题版本、阅读依据、待确认修订、引理依赖与证明缺口" : null, capabilities.experiments === "randomized_svd" ? "本地首轮实验：已选的随机低秩方向可运行固定 SVD 对照，记录误差、耗时与检查" : "浏览已有阅读、实验与案例档案"].filter(Boolean);
    $("#capability-content").innerHTML = `<span class="section-kicker">CURRENT VERSION · ${escape(status.version)}</span><h2>能力与实际完成程度</h2><h3>已经可用</h3><ul>${available.map(item => `<li>${icon("check")}${escape(item)}</li>`).join("")}</ul><h3>Skill 驱动千问流程</h3><p class="about-model-state">${escape(modelStateLabel())}${model.configured ? ` · ${escape(model.model || "千问")}` : ""}</p><p class="about-disclosure">${model.configured ? "千问按 Skill 规则判断：信息不足时提出少量澄清问题，信息足够时生成候选，并依据补充资料、题录、用户阅读笔记或实验摘要修订。返回结果经后端校验，推荐不会自动成为已选课题。" : "先在本机运行 Configure-Qwen.ps1 配置密钥，再刷新状态。未配置时可体验本地演示；网页不接收或显示 API Key。"}</p><p class="about-disclosure">新建时可选择千问流程或本地演示。每次带千问的提交会把简报、回答及本项目相关资料发送至百炼并消耗额度；查询、保存笔记和本地实验各有独立日志。</p><button type="button" class="button button-secondary button-small" data-action="refresh-model-status">刷新配置状态</button><h3>当前边界</h3><ul class="planned">${["模型不自动读取论文全文，阅读范围由用户确认", "数值实验支持固定随机低秩逼近；理论工作台另提供固定 Lean 案例入口，可用性由其环境状态确认", "数值检查不等于独立科学复核，新算法与一般性结论仍需验证", "理论路线仍待形式核验；固定 Lean 案例单独运行，通过只针对记录中的形式陈述", "魔搭创空间在线发布仍待完成"].map(item => `<li>${icon("circle")}${item}</li>`).join("")}</ul><p class="about-disclosure">题录返回、内容阅读、科学论断支持和实验验证是不同状态。历史档案与本项目新执行结果分别保存。</p>`;
    $("#mode-label").textContent = modelStateLabel();
    $("#mode-title").textContent = "Skill 驱动的研究流程";
    $("#mode-description").textContent = "新建可用千问判断需要澄清还是形成候选；之后连接真实题录、阅读笔记与固定本地实验。选择研究方向始终由你完成。";
    $(".version").textContent = `v${status.version}`;
    renderIntakeMode();
  }
  function renderIntakeMode() {
    const field = $("#planning-mode"), configured = !!state.status?.model?.configured;
    if (!field) return;
    $("option[value=skill]", field).disabled = !configured;
    if (!field.dataset.chosen) field.value = configured ? "skill" : "demo";
    if (!configured && field.value === "skill") field.value = "demo";
    $("#intake-model-notice").textContent = field.value === "skill" ? "开始探索会把研究简报发送至百炼，调用一次千问并消耗 API 额度。输入先保存；失败后可重试。" : "本地演示采用固定规则，不调用模型。千问配置后可切换为 Skill 流程。";
  }
  function renderProjects() {
    $("#project-list").innerHTML = state.projects.length ? `<div class="projects-grid">${state.projects.map(project => `<article class="panel project-card"><div class="project-card-top">${stagePill(project.stage)}<time datetime="${escape(project.updated_at)}">${escape(date(project.updated_at))}</time></div><h2>${escape(project.title)}</h2><p>${escape(project.initial_idea)}</p>${project.selected_topic_title ? `<div class="project-selected">已选：${escape(project.selected_topic_title)}</div>` : ""}<div class="project-card-bottom"><span>${Number(project.topic_count) || 0} 个候选方向</span><button class="text-button" type="button" data-action="open" data-project-id="${escape(project.id)}">继续研究 ${icon("arrow-right")}</button></div></article>`).join("")}</div>` : empty("folder", "从第一个问题开始。", "新建研究后，你的想法与计划会保留在这里。");
  }
  function renderRecords() {
    if (!state.projects.length) { $("#records-content").innerHTML = empty("history", "还没有研究记录。", "创建一个研究，记录会随你的每一步自动保存。"); return; }
    const project = state.project;
    if (!project) {
      $("#records-content").innerHTML = `<div class="records-toolbar"><label>选择一个研究项目<select id="record-project"><option value="">请选择项目</option>${state.projects.map(item => `<option value="${escape(item.id)}">${escape(item.title)}</option>`).join("")}</select></label></div>${empty("history", "先选择一个研究项目。", "查看问答、候选方向和选题的完整来路。", false)}`;
      return;
    }
    const selected = project.stage === "topic_selected";
    const actions = { theory_card_saved: "保存数学命题卡", theory_proposal_created: "生成待确认的命题修订", theory_proposal_accepted: "接受命题修订与证明规划", theory_proposal_rejected: "拒绝本次命题建议", theory_proposal_failed: "命题修订本次未完成", theory_example_created: "创建独立公开论证案例", created: "开始研究", project_created: "开始研究", answered: "补充澄清回答", answers_submitted: "补充澄清回答", clarified: "澄清完成", directions_generated: "整理候选方向", topic_selected: "选择研究方向", selected: "选择研究方向", reopened: "重新比较方向", directions_reopened: "重新比较方向", literature_searched: "保存独立文献查询", model_generated: "千问生成候选方向", model_call_failed: "千问调用未成功", qwen_generated: "千问生成候选方向", qwen_generation_failed: "千问生成未完成", qwen_directions_generated: "千问生成候选方向", skill_workflow_applied: "保存校验后的研究进展", skill_workflow_failed: "Skill 流程本次未完成", workflow_advanced: "更新 Skill 研究流程", workflow_failed: "Skill 流程未完成", workflow_input_saved: "保存研究补充输入", skill_input_saved: "保存当前科研输入", reading_note_added: "保存用户确认的阅读笔记", experiment_completed: "完成本地实验", experiment_failed: "本地实验未完成", qwen_generation_not_applied: "千问结果未应用" };
    const historyItems = list(project.history);
    $("#records-content").innerHTML = `<div class="records-toolbar"><label for="record-project">当前研究<select id="record-project">${state.projects.map(item => `<option value="${escape(item.id)}" ${item.id === project.id ? "selected" : ""}>${escape(item.title)}</option>`).join("")}</select></label><div class="record-actions"><button type="button" class="button button-secondary button-small" data-action="download-json">${icon("download")}导出 JSON</button><button type="button" class="button button-secondary button-small" data-action="download-plan" ${selected ? "" : 'disabled aria-describedby="record-note"'}>导出计划</button></div></div><section class="panel history-panel"><h2>${escape(project.title)}</h2><ol class="history-list">${historyItems.map(item => `<li><time datetime="${escape(item.created_at)}">${escape(date(item.created_at, true))}</time><strong>${escape(actions[item.action] || "研究记录更新")}</strong><p>${escape(item.summary)}</p></li>`).join("")}</ol>${historyItems.length ? "" : '<p class="panel-subtitle">暂时没有事件记录。</p>'}</section>${renderModelCalls(project)}<p class="record-note" id="record-note">${selected ? "完整记录包含原始想法、澄清问答、候选方向与选择依据。" : "选择方向后，就可以导出首轮计划。"}</p>`;
  }
  function renderModelCalls(project) {
    const calls = list(project.model_calls);
    const tokenCount = value => Number.isInteger(value) && value >= 0 ? String(value) : "未返回";
    return `<section class="panel model-call-panel"><span class="section-kicker">MODEL CALL HISTORY</span><h2>模型调用记录</h2>${calls.length ? [...calls].reverse().map(call => `<article class="model-call-record"><div><strong>${escape(call.model || "千问")}</strong><span class="search-status ${call.status === "success" ? "success" : "failed"}">${call.status === "success" ? "调用成功" : "调用未成功"}</span><time>${escape(date(call.finished_at || call.started_at, true) || "时间未记录")}</time></div>${call.summary ? `<p>${escape(call.summary)}</p>` : ""}${call.application_status === "discarded_conflict" ? '<p>调用期间项目发生变化，本次结果未应用到候选。</p>' : call.application_status === "rejected" ? '<p>返回内容未通过校验，已有候选保留。</p>' : ""}<p>输入 Token：${tokenCount(call.usage?.prompt_tokens)} · 输出 Token：${tokenCount(call.usage?.completion_tokens)} · 合计：${tokenCount(call.usage?.total_tokens)}</p></article>`).join("") : '<p class="about-disclosure">本项目尚无模型调用记录。模板生成和刷新配置状态不产生模型请求。</p>'}</section>`;
  }
  async function openProject(id, view = "explore") {
    if (state.mutating || state.experimentBusy || state.literatureBusy || leanBusy()) { toast("当前操作仍在进行，保存完成后再切换研究项目。"); return; }
    const token = ++state.opening;
    const target = view === "records" ? $("#records-content") : view === "literature" ? $("#literature-content") : view === "experiments" ? $("#experiments-content") : $("#project-content");
    setView(view);
    if (view === "explore") { $("#intake-panel").hidden = true; target.hidden = false; }
    target.innerHTML = '<div class="loading-state"><span class="spinner" aria-hidden="true"></span>正在打开研究记录……</div>';
    try {
      const project = await api(`/api/projects/${encodeURIComponent(id)}`);
      if (token !== state.opening) return;
      state.project = project.project || project;
      remember(state.project.id);
      renderProject();
      if (view === "records") renderRecords();
      if (view === "literature") renderLiterature();
      if (view === "experiments") await loadExperiments(true);
      message($("#global-message"), "");
    } catch (error) {
      if (token !== state.opening) return;
      message($("#global-message"), error.message);
      renderProject();
      if (view === "records") renderRecords();
      if (view === "literature") renderLiterature();
      if (view === "experiments") await loadExperiments(true);
    }
  }
  function newProject() {
    if (state.mutating || state.experimentBusy || state.literatureBusy || leanBusy()) { toast("当前操作仍在进行，保存完成后再新建研究。"); return; }
    ++state.opening;
    state.project = null;
    remember(null);
    renderProject();
    setView("explore");
    $("#idea-form").reset();
    delete $("#planning-mode").dataset.chosen; renderIntakeMode(); persistIntakeDraft();
    $("#idea-counter").textContent = "0 / 3000";
    message($("#form-message"), "");
    message($("#global-message"), "");
    $("#initial-idea").focus();
    $("#intake-panel").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
  }
  async function mutateProject(action, body, button, loadingText) {
    if (state.literatureBusy || state.experimentBusy || leanBusy()) { toast("查询或实验仍在进行，保存完成后再修改研究方向。"); return; }
    if (!state.project || state.mutating) return;
    state.mutating = true;
    state.generating = action === "generate" || action === "advance" || (action === "answers" && state.project?.workflow?.mode === "skill");
    const token = state.opening;
    const done = busy(button, loadingText);
    syncMutationControls();
    const previous = state.project;
    try {
      const result = await api(`/api/projects/${encodeURIComponent(previous.id)}/${action}`, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      if (token !== state.opening || state.project?.id !== previous.id) { await refreshProjects(false); return; }
      state.project = result.project || result;
      if (state.project.workflow?.pending_proposal) {
        try {
          const latest = await api(`/api/projects/${encodeURIComponent(previous.id)}`);
          if (token === state.opening && state.project?.id === previous.id) state.project = latest.project || latest;
        } catch { /* Keep the just-saved project if the read-only staleness refresh fails. */ }
      }
      if (["reopen", "generate", "advance"].includes(action)) delete state.reasonDrafts[previous.id];
      delete state.experimentWorkspaces[previous.id];
      if (state.project.workflow?.state !== "failed") { delete state.answerDrafts[previous.id]; if (body?.material) delete state.materialDrafts[previous.id]; if (action === "reading-notes") delete state.readingDrafts[`${previous.id}:${body.run_id}:${body.source_id}`]; }
      renderProject();
      await refreshProjects();
      message($("#global-message"), "");
      if (state.project.workflow?.state === "failed") toast("输入已保存，这一步尚未完成，可重试。", true);
      else if (["generate", "advance"].includes(action) && state.project.workflow?.pending_proposal) toast("模型结果已保存为待确认修订，尚未应用；请在预览中接受或放弃。");
      else toast(action === "reading-notes" ? "你的阅读范围、位置与笔记已保存。" : ["generate", "advance"].includes(action) ? (state.project.stage === "clarifying" ? "需要再确认少量问题，已保存当前进展。" : "候选已保存，推荐供比较，请由你选择方向。") : action === "select" ? "已保存你的选择与理由，首轮计划草案准备好了。" : action === "reopen" ? "已回到方向比较，先前选择保留在研究记录中。" : "回答与当前进展已保存。");
    } catch (error) {
      if (token !== state.opening || state.project?.id !== previous.id) return;
      if (["generate", "advance", "answers"].includes(action)) {
        try {
          const latest = await api(`/api/projects/${encodeURIComponent(previous.id)}`);
          if (token === state.opening && state.project?.id === previous.id) {
            state.project = latest.project || latest;
            renderProject();
            if (state.view === "records") renderRecords();
            if (state.view === "literature") renderLiterature();
          }
        } catch { /* Preserve the current project if even the read-only refresh is unavailable. */ }
      }
      const errorBox = action === "answers" ? $("#answers-message") : $("#global-message");
      message(errorBox || $("#global-message"), error.message);
      if (action !== "answers") toast(error.message, true);
    } finally {
      state.mutating = false;
      state.generating = false;
      done();
      syncMutationControls();
      if (["generate", "advance", "answers"].includes(action)) await refreshModelStatus();
      if (state.view === "literature") renderLiterature();
    }
  }
  async function download(kind, button) {
    if (!state.project) return;
    const project = state.project;
    const done = busy(button, "准备导出");
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(project.id)}/${kind === "plan" ? "plan" : "export"}`);
      if (!response.ok) { let body; try { body = await response.json(); } catch { body = {}; } throw new Error(typeof body.detail === "string" ? body.detail : "暂时无法导出，请稍后重试。"); }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `研证-${project.title || "研究记录"}.${kind === "plan" ? "md" : "json"}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast(kind === "plan" ? "首轮计划已导出。" : "完整研究记录已导出。");
    } catch (error) { message($("#global-message"), error.message || "连接失败，请稍后重试。"); toast(error.message || "连接失败，请稍后重试。", true); }
    finally { done(); }
  }
  function downloadLeanUnsaved(button) {
    const projectId = state.project?.id;
    if (!projectId || button.dataset.projectId !== projectId) return;
    const entry = leanUI?.exportUnsaved?.(projectId, button.dataset.runId);
    if (!entry || entry.context?.project_id !== projectId) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(entry, null, 2)], { type: "application/json;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `lean-${String(entry.id).replace(/[^a-zA-Z0-9_-]/g, "_")}-unsaved.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("已下载未保存结果；此次下载不代表结果已存入项目历史。");
  }
  const samples = {
    svd: { idea: "我想研究随机低秩逼近：在个人电脑上比较过采样和幂迭代，看看怎样用较少的计算让近似误差更稳定。", goal: "experimental", context: "我有线性代数基础，会用 Python 和 NumPy。希望首轮先在可复现的合成矩阵上验证。" },
    reading: { idea: "我想研究 AI 辅助论文阅读：如何帮助学生准确找到论文关键结论的原文依据，并判断回答是否超出了资料支持范围？", goal: "comparison", context: "希望先用公开论文和人工标注的问题做小规模比较，关注引用准确性与可核查性。" },
    vague: { idea: "我对数学、AI 和科学计算都感兴趣，但还不知道该研究哪个问题。想找到适合自己开始的方向。", goal: "experimental", context: "" }
  };
  document.addEventListener("click", async event => {
    const nav = event.target.closest("[data-view]");
    if (nav) { setView(nav.dataset.view); return; }
    const sample = event.target.closest("[data-sample]");
    if (sample) {
      const value = samples[sample.dataset.sample];
      $("#initial-idea").value = value.idea;
      $("#goal").value = value.goal;
      $("#context").value = value.context;
      $("#idea-counter").textContent = `${value.idea.length} / 3000`;
      $("#initial-idea").focus();
      persistIntakeDraft();
      return;
    }
    const literatureSample = event.target.closest("[data-literature-sample]");
    if (literatureSample) { $("#literature-query").value = literatureSample.dataset.literatureSample; captureLiteratureDraft(); $("#literature-query").focus(); return; }
    const button = event.target.closest("[data-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (action === "new") newProject();
    if (action === "about") { setView("about"); $$(".mode-info").forEach(element => { element.open = false; }); }
    if (action === "literature") setView("literature");
    if (action === "experiments") setView("experiments");
    if (action === "validation-numeric" || action === "validation-theory") {
      if (!state.mutating && !state.literatureBusy && !state.experimentBusy && !leanBusy()) { captureTheoryDraft(); captureExperimentDraft(); }
      chooseValidationMode(action === "validation-theory" ? "theory" : "numeric");
      await loadExperiments();
    }
    if (action === "lean-run" || action === "lean-refresh") await performLean(action, button);
    if (action === "lean-download-unsaved") downloadLeanUnsaved(button);
    if (action === "theory-preview") await inspectNextContext(button);
    if (["check-add-reference","check-remove-reference"].includes(action) && state.project && !state.mutating && !leanBusy()) {
      changeCheckReferences(button.closest(".checking-form"),action==="check-add-reference"?1:-1);
    }
    if (action === "artifact-describe" || action === "artifact-read") await inspectArtifact(button);
    if (action === "check-rebase") { try { await rebaseCheck(button.dataset.checkId); } catch (error) { toast(error.message, true); } }
    if (action === "failure-review") await saveFailureReview(button);
    if (action === "failure-rebase") await saveFailureReview(button, true);
    if (action === "theory-propose") await performTheory("propose", button);
    if (action === "theory-accept" || action === "theory-reject") await performTheory(action === "theory-accept" ? "accept" : "reject", button, button.dataset.proposalId);
    if (action === "direction-accept" || action === "direction-reject") { await performDirectionProposal(action === "direction-accept" ? "accept" : "reject", button); return; }
    if (action === "theory-example") await createTheoryExample(button);
    if (action === "theory-discard-draft" && state.project) { clearTheoryDraft(state.project.id); renderExperiments(); }
    if (action === "reload-experiments") await loadExperiments(true);
    if (action === "revise-from-experiments") await reviseFromExperiments(button);
    if (action === "search-idea") {
      const suggested = list(state.project?.workflow?.suggested_queries)[0];
      const query = $("#literature-query");
      if (!query.value.trim()) query.value = String(typeof suggested === "string" ? suggested : suggested?.query || state.project?.initial_idea || mapOf(state.project).initial_idea || "").slice(0, 300);
      captureLiteratureDraft();
      if ($("#literature-form").reportValidity()) await searchLiterature($("#literature-form"));
    }
    if (["advance", "revise-from-literature"].includes(action)) {
      if (!state.project || state.project.stage === "topic_selected" || !state.status?.model?.configured) return;
      if (action === "revise-from-literature") { renderProject(); setView("explore"); }
      await mutateProject("advance", {}, button, "千问正在判断下一步");
    }
    if (action === "return-research") { renderProject(); setView("explore"); }
    if (action === "reload-library") await loadLibrary(true);
    if (action === "refresh-model-status") await refreshModelStatus(button);
    if (action === "generate") {
      if (state.project?.stage !== "directions_ready" || !state.status?.model?.configured) return;
      await mutateProject("generate", {}, button, "千问正在生成");
    }
    if (action === "open") await openProject(button.dataset.projectId);
    if (action === "select") await mutateProject("select", { topic_id: button.dataset.topicId, reason: $("#selection-reason")?.value.trim() || "" }, button, "保存选择");
    if (action === "reopen") await mutateProject("reopen", null, button, "返回比较");
    if (action === "download-plan") await download("plan", button);
    if (action === "download-json") await download("json", button);
  });
  document.addEventListener("submit", async event => {
    if (event.target.classList.contains("checking-form")) { event.preventDefault(); if (event.target.reportValidity()) await saveCheck(event.target); return; }
    if (event.target.id === "theory-card-form") { event.preventDefault(); if (event.target.reportValidity()) await performTheory("save", $('button[type="submit"]', event.target)); return; }
    if (event.target.id === "literature-form") { event.preventDefault(); if (event.target.reportValidity()) await searchLiterature(event.target); return; }
    if (event.target.id === "experiment-form") { event.preventDefault(); if (event.target.reportValidity()) await runExperiment(event.target); return; }
    if (event.target.id === "material-form") {
      event.preventDefault();
      if (!event.target.reportValidity()) return;
      await mutateProject("advance", { material: $("#revision-material").value.trim() }, $("button[type=submit]", event.target), "千问正在修订"); return;
    }
    if (event.target.classList.contains("reading-note-form")) {
      event.preventDefault();
      if (!event.target.reportValidity()) return;
      const fields = Object.fromEntries(new FormData(event.target));
      const declared = { source_version: String(fields.source_version || "").trim(), source_sha256: String(fields.source_sha256 || "").trim(), source_sha256_scope: fields.source_sha256_scope || "" };
      const declaredCount = [declared.source_version, declared.source_sha256, declared.source_sha256_scope].filter(Boolean).length;
      if (declaredCount !== 0 && declaredCount !== 3) { toast("原文版本、声明文件 SHA256 与哈希范围须同时填写或同时留空。", true); return; }
      const body = { run_id: event.target.dataset.runId, source_id: event.target.dataset.sourceId, level: fields.level, locator: fields.locator.trim(), content: fields.content.trim(), confirmed: fields.confirmed === "on" };
      if (declaredCount === 3) Object.assign(body, { source_version: declared.source_version, source_sha256: declared.source_sha256, source_sha256_scope: declared.source_sha256_scope });
      await mutateProject("reading-notes", body, $("button[type=submit]", event.target), "保存阅读记录"); return;
    }
    if (event.target.id === "idea-form") {
      event.preventDefault();
      const form = event.target;
      if (state.mutating || state.literatureBusy || state.experimentBusy || leanBusy() || !form.reportValidity()) return;
      const body = Object.fromEntries(new FormData(form));
      body.initial_idea = body.initial_idea.trim();
      if (body.initial_idea.length < 5) { message($("#form-message"), "再补充一点想法，至少写 5 个字。"); $("#initial-idea").focus(); return; }
      body.planning_mode = state.status?.model?.configured && $("#planning-mode").value === "skill" ? "skill" : "demo";
      persistIntakeDraft();
      const token = ++state.opening;
      state.mutating = true; syncMutationControls();
      const done = busy($("#create-button"), body.planning_mode === "skill" ? "千问正在判断研究阶段" : "正在整理演示");
      message($("#form-message"), "");
      try {
        const result = await api("/api/projects", { method: "POST", body: JSON.stringify(body) });
        if (token !== state.opening) { await refreshProjects(false); return; }
        state.project = result.project || result;
        remember(state.project.id);
        renderProject();
        await refreshProjects();
        message($("#global-message"), "");
        try { localStorage.removeItem(draftKey); } catch { /* Saved on the server. */ }
        toast(state.project.workflow?.state === "failed" ? "研究和输入已保存，模型这一步未完成，可重试。" : state.project.workflow?.pending_proposal ? "研究已创建；模型结果已保存为待确认修订，尚未应用，请接受或放弃。" : "研究已创建，当前进展已保存。", state.project.workflow?.state === "failed");
        $("#project-content").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
      } catch (error) { if (token === state.opening) message($("#form-message"), error.message); }
      finally { state.mutating = false; done(); syncMutationControls(); await refreshModelStatus(); }
    }
    if (event.target.id === "answers-form") {
      event.preventDefault();
      if (!event.target.reportValidity()) return;
      const answers = Object.fromEntries(Array.from(new FormData(event.target), ([key, value]) => [key, String(value).trim()]));
      if (Object.values(answers).some(value => !value)) { message($("#answers-message"), "请回答每个问题；暂时不知道也可以说明。"); return; }
      state.answerDrafts[state.project.id] = answers;
      await mutateProject(state.project.workflow?.mode === "skill" ? "advance" : "answers", { answers }, $("button[type=submit]", event.target), "正在比较方向");
    }
  });
  document.addEventListener("change", event => {
    const checkForm = event.target.closest(".checking-form");
    if (checkForm) {
      captureCheckDraft(checkForm);
      if (event.target.name === "target_key") {
        const target = list(checkingWorkspace().targets).find(t => t.target_key === event.target.value);
        const d = checkDraft(state.project.id,"new",checkingWorkspace());
        if (target?.target_scope === "case") { d.case_key = target.case_key; d.card_proposition = ""; }
        else if (!d.card_proposition) d.card_proposition = "OTHER";
        checkForm.querySelector('[name="case_key"]').value = d.case_key || "";
        checkForm.querySelector('[name="card_proposition"]').value = d.card_proposition;
        persistCheckDraft(state.project.id,"new",d);
        checkForm.querySelector(".checking-target").innerHTML = checkingTarget(target);
      }
    }
    if (["record-project", "literature-project", "experiment-project"].includes(event.target.id) && (state.mutating || state.literatureBusy || state.experimentBusy || leanBusy())) {
      event.target.value = state.project?.id || "";
      toast("当前操作仍在进行，保存完成后再切换研究项目。");
      return;
    }
    if (event.target.id === "lean-case-picker" && state.project && event.target.dataset.projectId === state.project.id && !leanBusy()) { leanUI?.chooseCase(state.project.id, event.target.value); return; }
    if (event.target.id === "planning-mode") { event.target.dataset.chosen = "true"; renderIntakeMode(); }
    if (event.target.closest("#idea-form")) persistIntakeDraft();
    if (event.target.closest("#experiment-form")) captureExperimentDraft();
    if (event.target.closest("#theory-card-form")) captureTheoryDraft();
    captureReadingDraft(event.target);
    if (["literature-provider", "literature-limit"].includes(event.target.id)) captureLiteratureDraft();
    if (event.target.id === "record-project" && event.target.value) openProject(event.target.value, "records");
    if (event.target.id === "experiment-project") {
      if (event.target.value) openProject(event.target.value, "experiments");
      else { ++state.opening; state.project = null; remember(null); renderProject(); renderExperiments(); }
    }
    if (event.target.id === "literature-project") {
      if (event.target.value) openProject(event.target.value, "literature");
      else { ++state.opening; state.project = null; remember(null); renderProject(); renderLiterature(); }
    }
  });
  function captureReadingDraft(element) {
    const form = element.closest(".reading-note-form");
    if (form) { const draft = Object.fromEntries(new FormData(form)); draft.confirmed = draft.confirmed === "on"; state.readingDrafts[form.dataset.draftKey] = draft; }
  }
  document.addEventListener("input", event => {
    const checkForm = event.target.closest(".checking-form"); if (checkForm) captureCheckDraft(checkForm);
    if (event.target.dataset.failureReview && event.target.dataset.projectId === state.project?.id) {
      const projectId = state.project.id, id = event.target.dataset.failureReview;
      const item = list((state.theoryWorkspaces[projectId] || state.project.theory || {}).failure_records).find(r => r.id === id);
      if (item) {
        const key = `${projectId}:${id}`, draft = failureDraft(projectId, item);
        draft.reason = event.target.value;
        failureDrafts[key] = draft;
        try { localStorage.setItem(`yanzheng.failureDraft.${key}`, JSON.stringify(draft)); } catch {}
      }
    }
    if (event.target.id === "theory-review-reason" && event.target.dataset.projectId === state.project?.id) theoryReviewDrafts[`${event.target.dataset.projectId}:${event.target.dataset.proposalId}`] = event.target.value;
    if (event.target.closest("#idea-form")) persistIntakeDraft();
    if (event.target.closest("#experiment-form")) captureExperimentDraft();
    if (event.target.closest("#theory-card-form")) captureTheoryDraft();
    if (event.target.closest("#answers-form") && state.project) state.answerDrafts[state.project.id] = Object.fromEntries(new FormData($("#answers-form")));
    if (event.target.id === "revision-material" && state.project) state.materialDrafts[state.project.id] = event.target.value;
    captureReadingDraft(event.target);
    if (event.target.id === "selection-reason" && state.project) state.reasonDrafts[state.project.id] = event.target.value;
    if (event.target.id === "literature-query") captureLiteratureDraft();
  });
  $("#initial-idea").addEventListener("input", event => { $("#idea-counter").textContent = `${event.target.value.length} / 3000`; });
  $("#mobile-menu").addEventListener("click", () => { const open = !$(".sidebar").classList.contains("is-open"); $(".sidebar").classList.toggle("is-open", open); $("#mobile-menu").setAttribute("aria-expanded", String(open)); $("#mobile-menu").setAttribute("aria-label", open ? "收起导航" : "展开导航"); $("#mobile-menu").innerHTML = icon(open ? "x" : "menu"); });
  document.addEventListener("keydown", event => { if (event.key === "Escape") { $(".sidebar").classList.remove("is-open"); $("#mobile-menu").setAttribute("aria-expanded", "false"); $("#mobile-menu").setAttribute("aria-label", "展开导航"); $("#mobile-menu").innerHTML = icon("menu"); $$(".mode-info").forEach(element => { element.open = false; }); } });
  window.addEventListener("hashchange", () => setView(location.hash.slice(1), false));
  paintIcons();
  restoreIntakeDraft();
  if ($("#initial-idea").value) $("#planning-mode").dataset.chosen = "true";
  setView(location.hash.slice(1) || "explore");
  async function start() {
    const token = state.opening;
    const results = await Promise.allSettled([api("/api/status"), refreshProjects()]);
    if (results[0].status === "fulfilled") {
      state.status = results[0].value;
      renderCapabilities();
    }
    const failed = results.find(result => result.status === "rejected");
    if (failed) { message($("#global-message"), failed.reason.message); return; }
    if (token !== state.opening) return;
    const active = remembered();
    if (active && state.projects.some(project => project.id === active)) {
      const initialView = state.view;
      await openProject(active, ["records", "literature", "experiments"].includes(initialView) ? initialView : "explore");
      if (!["explore", "records", "literature", "experiments"].includes(initialView)) setView(initialView);
    }
    else remember(null);
  }
  start();
})();
