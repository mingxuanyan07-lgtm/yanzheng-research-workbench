"use strict";

(() => {
  const state = { projects: [], project: null, view: "explore", status: null, statusLoading: false, opening: 0, mutating: false, generating: false, literatureBusy: false, experimentBusy: false, experimentLoading: false, experimentWorkspaces: {}, experimentDrafts: {}, readingDrafts: {}, answerDrafts: {}, materialDrafts: {}, literatureDrafts: {}, reasonDrafts: {}, library: null, libraryLoading: false };
  const activeKey = "yanzheng.activeProject";
  const draftKey = "yanzheng.intakeDraft";
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
  async function api(path, options = {}) {
    let response;
    try { response = await fetch(path, { ...options, headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } }); }
    catch { throw new Error("暂时连接不到本地服务。请确认工作台已经启动，再重试。"); }
    let body;
    try { body = await response.json(); } catch { throw new Error("服务没有返回可读取的结果，请稍后重试。"); }
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : typeof body.error === "string" ? body.error : null;
      throw new Error(detail || `这一步没有完成（${response.status}），你的已有记录仍然保留。`);
    }
    return body;
  }
  function busy(button, label) {
    const previous = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner" aria-hidden="true"></span>${escape(label)}`;
    return () => { if (button.isConnected) { button.disabled = false; button.innerHTML = previous; } };
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
    const locked = state.mutating || state.literatureBusy || state.experimentBusy;
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
    const ideaButton = $("#create-button");
    if (ideaButton) ideaButton.disabled = locked;
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
    $("#intake-panel").hidden = !!project;
    $("#project-content").hidden = !project;
    if (!project) { $("#project-content").innerHTML = ""; updateSteps("clarifying"); refreshStats(); return; }
    const map = mapOf(project);
    const stage = project.stage || map.stage;
    let content = projectHeader(project) + renderBrief(project);
    if (stage !== "topic_selected") content += renderGenerationPanel(map);
    if (stage === "clarifying") content += renderQuestions(map);
    else if (stage === "topic_selected") content += renderPlan(map);
    else content += renderDirections(map);
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
  function stringList(values, ordered = false) {
    const items = list(values).filter(value => value !== null && value !== "");
    if (!items.length) return '<p>暂未确定，需进一步补充。</p>';
    const tag = ordered ? "ol" : "ul";
    return `<${tag}>${items.map(value => `<li>${escape(value)}</li>`).join("")}</${tag}>`;
  }
  function sourceVerification(value) { return { unverified: "待核实", metadata_checked: "已核对书目信息", abstract_checked: "已核对摘要", fulltext_checked: "已核对相关正文" }[value] || "待核实"; }
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
      return `<div class="evidence-entry"><p class="evidence-line"><strong>${escape(relation)}</strong> · ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${escape(source.title)} ↗</a>` : escape(source?.title || entry.source_id)}<br>${escape(entry.claim)}</p>${source ? `<p class="evidence-scope">${sourceVerification(source.verification)} · 位置：${escape(entry.locator || source.locator || "尚未记录")} · 核对：${escape(source.checked_at ? date(source.checked_at, true) : "尚未核对")}</p>` : '<p class="evidence-scope">关联来源暂不可用，请核查原始记录。</p>'}</div>`;
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
    const disabled = !model.configured || state.mutating || state.literatureBusy || state.experimentBusy;
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
    target.innerHTML = `<div class="literature-toolbar">${picker}<button type="button" class="button button-secondary" data-action="return-research">${icon("arrow-left")}${project.stage === "topic_selected" ? "查看计划草案" : project.stage === "directions_ready" ? "返回方向比较" : "返回当前研究"}</button></div><section class="panel literature-form-panel"><span class="section-kicker">PUBLIC METADATA SEARCH</span><h2>让研究想法找到文献入口。</h2><p class="literature-disclosure">检索词发送到所选公开题录服务。点击“按研究想法查文献”会使用建议查询（没有建议时使用想法文本），实际执行一次查询；请先确认查询框适合公开。返回题录不代表已阅读。</p><form id="literature-form"><label class="field-label" for="literature-query">公开检索词 <span>最多 300 字</span></label><input id="literature-query" name="query" type="text" required maxlength="300" value="${escape(draft.query)}" placeholder="例如：randomized singular value decomposition" autocomplete="off">${queries.length ? `<div class="literature-samples query-suggestions"><span>研究查询建议 · 点击填入</span>${queries.map(query => `<button type="button" class="sample-chip" data-literature-sample="${escape(query.slice(0, 300))}">${escape(query)}</button>`).join("")}</div>` : ""}<div class="literature-samples"><span>公开示例</span><button type="button" class="sample-chip" data-literature-sample="randomized singular value decomposition">随机低秩逼近</button><button type="button" class="sample-chip" data-literature-sample="retrieval augmented generation citation accuracy">AI 引用准确性</button></div><div class="literature-form-bottom"><label for="literature-provider">题录服务<select id="literature-provider" name="provider"><option value="crossref" ${draft.provider === "crossref" ? "selected" : ""}>Crossref</option><option value="arxiv" ${draft.provider === "arxiv" ? "selected" : ""}>arXiv</option></select></label><label for="literature-limit">最多返回<select id="literature-limit" name="limit">${[1, 2, 3, 4, 5].map(number => `<option value="${number}" ${Number(draft.limit) === number ? "selected" : ""}>${number} 条</option>`).join("")}</select></label><button type="submit" class="button button-primary" ${state.literatureBusy ? "disabled" : ""}>${state.literatureBusy ? '<span class="spinner" aria-hidden="true"></span>正在检索' : `${icon("book")}检索题录`}</button></div><div class="idea-search-action"><button type="button" class="button button-secondary" data-action="search-idea" ${state.literatureBusy ? "disabled" : ""}>按研究想法查文献</button><span>每次点击只查询一个服务，不调用模型</span></div><div id="literature-message" class="message" role="alert" hidden></div></form></section>${searches.length ? `<div class="workflow-link literature-revise"><div><strong>把阅读发现带回候选方向。</strong><p>当前保存 ${notes.length} 条用户确认的阅读记录。修订时会将简报、补充资料、公开题录与已确认的阅读摘录发送至百炼，消耗 API 额度。${project.stage === "topic_selected" ? "当前方向已由你选择；请先返回研究页重新比较，再修订。" : "模型会提出候选或澄清问题，不会替你选题。"}</p></div><button type="button" class="button button-secondary" data-action="revise-from-literature" ${revisable ? "" : "disabled"}>依据资料修订方向</button></div>` : ""}<div class="direction-head"><h3>${searches.length} 轮独立检索记录</h3><span>${notes.length} 条阅读记录</span></div><p class="literature-record-note">题录状态保持原样。摘要或正文是否读过，由你确认并填写阅读位置；保存笔记不自动证明候选论断成立。</p>${searches.length ? [...searches].reverse().map(record => renderSearchRun(record, project.id)).join("") : '<div class="empty-state small-empty"><h2>还没有发起查询。</h2><p>实际返回、无结果和失败状态都会保存。</p></div>'}`;
    syncMutationControls();
  }
  function renderReadingNotes(source, record, projectId) {
    const key = `${projectId}:${record.id}:${source.id}`;
    const notes = list(state.project?.reading_notes).filter(note => note.run_id === record.id && note.source_id === source.id);
    const draft = state.readingDrafts[key] || {};
    return `<div class="reading-records">${notes.map(note => `<article class="reading-note"><div><span class="source-verification">${sourceVerification(note.level)}</span><span>用户确认 · ${escape(date(note.checked_at, true))}</span></div><p class="reading-locator">阅读位置：${escape(note.locator)}</p><p class="reading-content">${escape(note.content)}</p></article>`).join("")}<details class="reading-note-editor"><summary>记录我读过的内容 ${icon("chevron")}</summary><form class="reading-note-form" data-run-id="${escape(record.id)}" data-source-id="${escape(source.id)}" data-draft-key="${escape(key)}"><label>实际阅读范围<select name="level"><option value="abstract_checked" ${draft.level === "abstract_checked" ? "selected" : ""}>已阅读并核对摘要</option><option value="fulltext_checked" ${draft.level === "fulltext_checked" ? "selected" : ""}>已阅读相关正文</option></select></label><label>阅读位置<input name="locator" required maxlength="300" value="${escape(draft.locator || "")}" placeholder="例如：摘要；v2 第 3.1 节 / 第 6 页"></label><label>原文摘录或阅读笔记<textarea name="content" required maxlength="6000" placeholder="记录实际读到的内容，以及它能支持或限制什么。">${escape(draft.content || "")}</textarea></label><label class="reading-confirmation"><input name="confirmed" type="checkbox" required ${draft.confirmed ? "checked" : ""}><span>我确认已阅读上述范围，位置与笔记由我填写。</span></label><p>只保存你的阅读声明与笔记；程序不会自动下载全文或把题录升级为已读。</p><button type="submit" class="button button-secondary">保存阅读记录</button></form></details></div>`;
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
    if (!state.project || state.literatureBusy || state.mutating) return;
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
      if (token === state.opening && state.project?.id === projectId) $("#experiments-content").innerHTML = `<div class="message is-error">${escape(error.message)}</div><button type="button" class="button button-secondary" data-action="reload-experiments">重新读取实验工作台</button>`;
    } finally { state.experimentLoading = false; if (state.view === "experiments" && state.project && state.project.id !== projectId) loadExperiments(); }
  }
  function renderExperiments() {
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
    if (!state.project || state.mutating || state.literatureBusy || state.experimentBusy) return;
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
    if (!state.project || state.mutating || state.literatureBusy || state.experimentBusy || !state.status?.model?.configured) return;
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
    const available = [status.storage === "sqlite" ? "真实本地保存：简报、回答、选择、题录、阅读笔记与实验可恢复导出" : null, capabilities.literature_search === "available" ? "公开题录检索：Crossref / arXiv，记录成功、无结果与失败" : null, capabilities.reading_notes ? "用户确认阅读：分开保存摘要或相关正文的笔记、位置和日期" : null, capabilities.experiments === "randomized_svd" ? "本地首轮实验：已选的随机低秩方向可运行固定 SVD 对照，记录误差、耗时与检查" : "浏览已有阅读、实验与案例档案"].filter(Boolean);
    $("#capability-content").innerHTML = `<span class="section-kicker">CURRENT VERSION · ${escape(status.version)}</span><h2>能力与实际完成程度</h2><h3>已经可用</h3><ul>${available.map(item => `<li>${icon("check")}${escape(item)}</li>`).join("")}</ul><h3>Skill 驱动千问流程</h3><p class="about-model-state">${escape(modelStateLabel())}${model.configured ? ` · ${escape(model.model || "千问")}` : ""}</p><p class="about-disclosure">${model.configured ? "千问按 Skill 规则判断：信息不足时提出少量澄清问题，信息足够时生成候选，并依据补充资料、题录、用户阅读笔记或实验摘要修订。返回结果经后端校验，推荐不会自动成为已选课题。" : "先在本机运行 Configure-Qwen.ps1 配置密钥，再刷新状态。未配置时可体验本地演示；网页不接收或显示 API Key。"}</p><p class="about-disclosure">新建时可选择千问流程或本地演示。每次带千问的提交会把简报、回答及本项目相关资料发送至百炼并消耗额度；查询、保存笔记和本地实验各有独立日志。</p><button type="button" class="button button-secondary button-small" data-action="refresh-model-status">刷新配置状态</button><h3>当前边界</h3><ul class="planned">${["模型不自动读取论文全文，阅读范围由用户确认", "实验仅支持固定的随机低秩逼近验证；通用课题保留各自计划", "数值检查不等于独立科学复核，新算法与一般性结论仍需验证", "魔搭创空间在线发布仍待完成"].map(item => `<li>${icon("circle")}${item}</li>`).join("")}</ul><p class="about-disclosure">题录返回、内容阅读、科学论断支持和实验验证是不同状态。历史档案与本项目新执行结果分别保存。</p>`;
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
    const actions = { created: "开始研究", project_created: "开始研究", answered: "补充澄清回答", answers_submitted: "补充澄清回答", clarified: "澄清完成", directions_generated: "整理候选方向", topic_selected: "选择研究方向", selected: "选择研究方向", reopened: "重新比较方向", directions_reopened: "重新比较方向", literature_searched: "保存独立文献查询", model_generated: "千问生成候选方向", model_call_failed: "千问调用未成功", qwen_generated: "千问生成候选方向", qwen_generation_failed: "千问生成未完成", qwen_directions_generated: "千问生成候选方向", skill_workflow_applied: "保存校验后的研究进展", skill_workflow_failed: "Skill 流程本次未完成", workflow_advanced: "更新 Skill 研究流程", workflow_failed: "Skill 流程未完成", workflow_input_saved: "保存研究补充输入", skill_input_saved: "保存当前科研输入", reading_note_added: "保存用户确认的阅读笔记", experiment_completed: "完成本地实验", experiment_failed: "本地实验未完成", qwen_generation_not_applied: "千问结果未应用" };
    const historyItems = list(project.history);
    $("#records-content").innerHTML = `<div class="records-toolbar"><label for="record-project">当前研究<select id="record-project">${state.projects.map(item => `<option value="${escape(item.id)}" ${item.id === project.id ? "selected" : ""}>${escape(item.title)}</option>`).join("")}</select></label><div class="record-actions"><button type="button" class="button button-secondary button-small" data-action="download-json">${icon("download")}导出 JSON</button><button type="button" class="button button-secondary button-small" data-action="download-plan" ${selected ? "" : 'disabled aria-describedby="record-note"'}>导出计划</button></div></div><section class="panel history-panel"><h2>${escape(project.title)}</h2><ol class="history-list">${historyItems.map(item => `<li><time datetime="${escape(item.created_at)}">${escape(date(item.created_at, true))}</time><strong>${escape(actions[item.action] || "研究记录更新")}</strong><p>${escape(item.summary)}</p></li>`).join("")}</ol>${historyItems.length ? "" : '<p class="panel-subtitle">暂时没有事件记录。</p>'}</section>${renderModelCalls(project)}<p class="record-note" id="record-note">${selected ? "完整记录包含原始想法、澄清问答、候选方向与选择依据。" : "选择方向后，就可以导出首轮计划。"}</p>`;
  }
  function renderModelCalls(project) {
    const calls = list(project.model_calls);
    const tokenCount = value => Number.isInteger(value) && value >= 0 ? String(value) : "未返回";
    return `<section class="panel model-call-panel"><span class="section-kicker">MODEL CALL HISTORY</span><h2>模型调用记录</h2>${calls.length ? [...calls].reverse().map(call => `<article class="model-call-record"><div><strong>${escape(call.model || "千问")}</strong><span class="search-status ${call.status === "success" ? "success" : "failed"}">${call.status === "success" ? "调用成功" : "调用未成功"}</span><time>${escape(date(call.finished_at || call.started_at, true) || "时间未记录")}</time></div>${call.summary ? `<p>${escape(call.summary)}</p>` : ""}${call.application_status === "discarded_conflict" ? '<p>调用期间项目发生变化，本次结果未应用到候选。</p>' : call.application_status === "rejected" ? '<p>返回内容未通过校验，已有候选保留。</p>' : ""}<p>输入 Token：${tokenCount(call.usage?.prompt_tokens)} · 输出 Token：${tokenCount(call.usage?.completion_tokens)} · 合计：${tokenCount(call.usage?.total_tokens)}</p></article>`).join("") : '<p class="about-disclosure">本项目尚无模型调用记录。模板生成和刷新配置状态不产生模型请求。</p>'}</section>`;
  }
  async function openProject(id, view = "explore") {
    if (state.mutating || state.experimentBusy || state.literatureBusy) { toast("当前操作仍在进行，保存完成后再切换研究项目。"); return; }
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
    if (state.mutating || state.experimentBusy || state.literatureBusy) { toast("当前操作仍在进行，保存完成后再新建研究。"); return; }
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
    if (state.literatureBusy || state.experimentBusy) { toast("查询或实验仍在进行，保存完成后再修改研究方向。"); return; }
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
      if (["reopen", "generate", "advance"].includes(action)) delete state.reasonDrafts[previous.id];
      delete state.experimentWorkspaces[previous.id];
      if (state.project.workflow?.state !== "failed") { delete state.answerDrafts[previous.id]; if (body?.material) delete state.materialDrafts[previous.id]; if (action === "reading-notes") delete state.readingDrafts[`${previous.id}:${body.run_id}:${body.source_id}`]; }
      renderProject();
      await refreshProjects();
      message($("#global-message"), "");
      if (state.project.workflow?.state === "failed") toast("输入已保存，这一步尚未完成，可重试。", true);
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
      await mutateProject("reading-notes", { run_id: event.target.dataset.runId, source_id: event.target.dataset.sourceId, level: fields.level, locator: fields.locator.trim(), content: fields.content.trim(), confirmed: fields.confirmed === "on" }, $("button[type=submit]", event.target), "保存阅读记录"); return;
    }
    if (event.target.id === "idea-form") {
      event.preventDefault();
      const form = event.target;
      if (state.mutating || state.literatureBusy || state.experimentBusy || !form.reportValidity()) return;
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
        toast(state.project.workflow?.state === "failed" ? "研究和输入已保存，模型这一步未完成，可重试。" : "研究已创建，当前进展已保存。", state.project.workflow?.state === "failed");
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
    if (["record-project", "literature-project", "experiment-project"].includes(event.target.id) && (state.mutating || state.literatureBusy || state.experimentBusy)) {
      event.target.value = state.project?.id || "";
      toast("当前操作仍在进行，保存完成后再切换研究项目。");
      return;
    }
    if (event.target.id === "planning-mode") { event.target.dataset.chosen = "true"; renderIntakeMode(); }
    if (event.target.closest("#idea-form")) persistIntakeDraft();
    if (event.target.closest("#experiment-form")) captureExperimentDraft();
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
    if (event.target.closest("#idea-form")) persistIntakeDraft();
    if (event.target.closest("#experiment-form")) captureExperimentDraft();
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
