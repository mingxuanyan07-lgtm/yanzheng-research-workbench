/* Cloud-only adapter; loaded before the unchanged workbench scripts. */
"use strict";
(() => {
  const originalFetch = window.fetch.bind(window);
  const tokenKey = "yanzheng.cloud.session.v1";
  const headerName = "X-Yanzheng-Session";
  let token = "";
  let sessionPromise = null;
  let boundaryMessage = "";
  let modelStatus = null;
  let sessionMessage = "正在建立独立临时会话；请稍后重试。";
  let sampleNotice = false;
  try { token = sessionStorage.getItem(tokenKey) || ""; } catch { /* In-memory session still works. */ }

  function notice(message, error = false) {
    boundaryMessage = message;
    const target = document.getElementById("cloud-demo-status");
    if (target) {
      target.textContent = message;
      target.style.color = error ? "#b42318" : "";
    }
  }

  async function ensureSession() {
    if (!sessionPromise) {
      sessionPromise = (async () => {
        const response = await originalFetch("/demo/session", {
          method: "POST", headers: { "Content-Type": "application/json", ...(token ? { [headerName]: token } : {}) },
          body: "{}", credentials: "same-origin", redirect: "error", cache: "no-store"
        });
        const data = await response.json();
        if (!response.ok) {
          const message = data.detail || "在线试用会话未能建立，请刷新后重试。";
          notice(message, true);
          throw new Error(message);
        }
        token = data.session;
        try { sessionStorage.setItem(tokenKey, token); } catch { /* No disk storage required. */ }
        sessionMessage = `本窗口使用独立临时数据；闲置约 ${Math.round(data.idle_seconds / 60)} 分钟后清理，请及时导出。`;
        notice(sessionMessage);
        return token;
      })().catch(error => {
        // Let the next explicit action recover from a temporary failure. Keep
        // the existing token: replacing an expired session would hide its data.
        sessionPromise = null;
        const message = error instanceof Error && error.message
          ? error.message : "在线试用会话连接失败，请稍后重试。";
        notice(message, true);
        throw error;
      });
    }
    return sessionPromise;
  }

  window.fetch = async function (input, options = {}) {
    const address = input instanceof Request ? input.url : String(input);
    const url = new URL(address, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) {
      return originalFetch(input, options);
    }
    const session = await ensureSession();
    const method = String(options.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (sampleNotice && method !== "GET" && method !== "HEAD") {
      sampleNotice = false;
      notice(sessionMessage);
    }
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(options.headers || {}).forEach((value, key) => headers.set(key, value));
    headers.set(headerName, session);
    // Do not place the bearer session in a URL, cookie, cross-origin call or redirect.
    const response = await originalFetch(input, { ...options, headers, credentials: "same-origin",
      redirect: "error", cache: "no-store" });
    if (response.status === 401) notice("在线试用会话已过期；点击“开始新会话”后重新开始。", true);
    if (url.pathname === "/api/status" && response.ok) {
      try { modelStatus = (await response.clone().json()).model; } catch { /* Main UI handles invalid JSON. */ }
    }
    return response;
  };

  function fillPublicExample() {
    if (document.getElementById("intake-panel")?.hidden) {
      document.querySelector('.topbar [data-action="new"]')?.click();
      if (document.getElementById("intake-panel")?.hidden) {
        notice("当前操作仍在进行，请等待完成后再填入公开样例。", true);
        return;
      }
    }
    location.hash = "explore";
    const values = {
      "initial-idea": "我想研究随机低秩逼近：在相同计算预算下，过采样与幂迭代怎样影响随机 SVD 的误差和耗时？",
      goal: "comparison", resources: "laptop", "time-horizon": "week",
      context: "公开试用样例：仅使用合成矩阵，一周内复现基线并比较误差与耗时。已有随机低秩逼近领域；不宣称新定理，不使用私有研究数据。请先给出可审阅的研究方向，保留待查文献和验证条件。",
      "planning-mode": modelStatus?.configured ? "skill" : "demo"
    };
    for (const [id, value] of Object.entries(values)) {
      const element = document.getElementById(id);
      if (element) {
        element.value = value;
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
    const idea = document.getElementById("initial-idea");
    idea?.focus();
    idea?.scrollIntoView({ behavior: "smooth", block: "center" });
    sampleNotice = true;
    notice(modelStatus?.configured
      ? "公开样例已填入，尚未调用模型。已选择“千问 + Skill 规则”，点击创建才请求千问。"
      : "公开样例已填入，当前采用模板试用，不调用模型。作者开放千问后，可手动请求待确认建议。");
  }

  function resetSession() {
    if (!window.confirm("开始新会话后，本窗口将无法继续访问当前临时项目。请先导出需要保留的记录。")) return;
    for (const storageName of ["localStorage", "sessionStorage"]) {
      try {
        const storage = window[storageName];
        for (const key of Object.keys(storage)) if (key.startsWith("yanzheng.")) storage.removeItem(key);
      } catch { /* Storage may be unavailable. */ }
    }
    token = "";
    sessionPromise = null;
    location.reload();
  }

  function mountBoundary() {
    const host = document.querySelector("main") || document.body;
    const box = document.createElement("section");
    box.id = "cloud-demo-boundary";
    box.className = "panel cloud-demo-boundary";
    box.setAttribute("aria-label", "在线试用说明");
    box.style.cssText = "padding:18px 22px;margin:0 0 24px;border:1px solid #cbd5e1;border-radius:18px;background:rgba(248,250,252,.96)";
    const title = document.createElement("strong");
    title.textContent = "研证 · 独立在线试用";
    const explanation = document.createElement("p");
    explanation.textContent = "本页与本地研究库完全独立。可体验公开样例 → 千问建议 → 人工接受或拒绝 → 导出；模型不会自动调用，建议不会自动采纳。云端暂不执行 Lean、数值实验或外部文献检索。";
    explanation.style.cssText = "font-size:13px;line-height:1.7;margin:8px 0";
    const controls = document.createElement("div");
    controls.style.cssText = "display:flex;gap:10px;flex-wrap:wrap";
    for (const [label, handler] of [["填入公开样例", fillPublicExample], ["开始新会话", resetSession]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-secondary";
      button.textContent = label;
      button.addEventListener("click", handler);
      controls.appendChild(button);
    }
    const artifacts = document.createElement("a");
    artifacts.className = "button button-secondary";
    artifacts.href = "/demo/public-evidence.zip";
    artifacts.download = "yanzheng-public-evidence.zip";
    artifacts.textContent = "下载公共材料包";
    controls.appendChild(artifacts);
    const status = document.createElement("p");
    status.id = "cloud-demo-status";
    status.setAttribute("role", "status");
    status.style.cssText = "font-size:12px;line-height:1.6;margin:10px 0 0";
    status.textContent = boundaryMessage || "正在建立独立临时会话；请勿填入隐私或敏感研究材料。";
    box.append(title, explanation, controls, status);
    host.prepend(box);
    originalFetch("/demo/info", { credentials: "same-origin", redirect: "error" })
      .then(response => response.ok ? response.json() : null)
      .then(info => {
        if (info) {
          const quota = document.createElement("p");
          quota.style.cssText = "font-size:12px;line-height:1.6;margin:8px 0 0";
          const visitorLimit = info.session_call_limit == null ? "访客模型试用次数无上限" : `访客最多 ${info.session_call_limit} 次模型请求`;
          const dailyLimit = info.daily_call_limit == null ? "全站每日模型请求次数无上限" : `全站每天最多 ${info.daily_call_limit} 次`;
          quota.textContent = `${visitorLimit}，${dailyLimit}；失败不自动重试。公开样例和核查材料不代表新成果或独立专家认证。`;
          if (!info.model_calls_enabled) quota.textContent += " 作者尚未开放真实模型调用。";
          box.appendChild(quota);
        }
      }).catch(() => {});
    adaptCloudContent();
    new MutationObserver(adaptCloudContent).observe(document.body, { childList: true, subtree: true });
  }

  function adaptCloudContent() {
    const capability = document.getElementById("capability-content");
    if (capability && !capability.querySelector("[data-cloud-capabilities]")) {
      const content = document.createElement("div");
      content.dataset.cloudCapabilities = "true";
      content.innerHTML = '<span class="section-kicker">ONLINE DEMO</span><h2>在线试用能力</h2>' +
        '<h3>已开放的流程</h3><ul><li>访客独立临时保存；本页不连接本地研究库</li>' +
        '<li>研究想法与约束 → 待确认方向 → 明确接受或拒绝 → 选择与导出</li>' +
        '<li>阅读已有公开材料、案例和历史实验档案</li></ul>' +
        '<p class="about-disclosure" data-cloud-model-summary></p><h3>当前边界</h3>' +
        '<ul class="planned"><li>云端不执行 Lean、数值子进程或外部文献检索</li>' +
        '<li>临时会话会过期、服务重启会失去会话；请及时导出</li>' +
        '<li>模型建议不等于证明或专家认证；已有案例不宣称新定理</li>' +
        '<li>API Secret 由作者在平台配置，访客无需也不应提交密钥</li></ul>';
      capability.replaceChildren(content);
    }
    const summary = document.querySelector("[data-cloud-model-summary]");
    if (summary) {
      const text = modelStatus?.configured
        ? "千问已配置，模型试用次数无上限。每次请求由你明确点击；生成结果先保存为待确认建议。"
        : "当前未开放可用的千问调用；可先体验模板与公开材料。由作者配置平台 Secret 并开放额度后启用。";
      if (summary.textContent !== text) summary.textContent = text;
    }
    const replacements = [
      ["#mode-description", "在线试用以独立临时会话整理研究方向；千问建议经确认才采纳，记录可导出。"],
      [".sidebar-bottom > span:nth-child(2)", "云端临时工作空间"],
      [".save-note", "研究记录保存在本窗口的云端临时会话，请及时导出。"],
      ["#screen-literature .page-heading > p", "在线试用可浏览已有公开资料；外部文献检索未开放。"],
      [".literature-disclosure", "在线试用暂不连接 Crossref / arXiv。可在案例与证据页阅读已公开材料；此处不会发起外部检索。"],
      [".experiment-disclosure", "在线试用不运行数值子进程。可在案例与证据页查看已有固定随机低秩逼近实验记录；历史结果不代表本会话新执行。"]
    ];
    for (const [selector, text] of replacements) {
      document.querySelectorAll(selector).forEach(element => {
        if (element.textContent !== text) element.textContent = text;
      });
    }
    const globalMessage = document.getElementById("global-message");
    if (globalMessage?.textContent.includes("暂时连接不到本地服务")) {
      globalMessage.textContent = "在线工作台暂时连接失败。可点击“刷新配置状态”重试；若提示会话过期，请使用“开始新会话”。";
    }
    document.querySelectorAll(".model-config-note").forEach(element => {
      if (element.textContent.includes("Configure-Qwen.ps1") || element.textContent.includes("本机配置")) {
        element.textContent = "模型配置由作者在创空间的 Secret 中维护；访客无需配置或提交 API Key。";
      }
    });
    document.querySelectorAll('#literature-form button[type="submit"], #experiment-form button[type="submit"], [data-action="search-idea"], [data-action="lean-run"]')
      .forEach(button => {
        button.disabled = true;
        button.title = "本项在线执行暂未开放；可查看公开记录";
      });
  }

  // Native anchor downloads do not carry custom headers; use the same protected
  // fetch path and create a local Blob URL only after a successful response.
  document.addEventListener("click", async event => {
    const anchor = event.target.closest?.("a[download]");
    if (!anchor) return;
    const url = new URL(anchor.href, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    try {
      const response = await window.fetch(url.href);
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "导出没有完成。");
      }
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="([A-Za-z0-9._-]+)"/);
      const objectUrl = URL.createObjectURL(await response.blob());
      const download = document.createElement("a");
      download.href = objectUrl;
      download.download = match ? match[1] : "yanzheng-export.json";
      download.hidden = true;
      document.body.appendChild(download);
      download.click();
      download.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
      notice("导出文件已交给浏览器下载；请检查浏览器下载列表。");
    } catch (error) {
      notice(error.message || "导出未完成，请检查会话状态。", true);
    }
  }, true);

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountBoundary, { once: true });
  else mountBoundary();
})();
