"use strict";
// Executes the unmodified cloud adapter in a minimal DOM. All fetches are
// in-memory stubs: no browser, HTTP listener, model, database or local config.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "static", "cloud-demo.js");
const source = fs.readFileSync(sourcePath, "utf8");
const origin = "https://demo.example.test";
const tokenKey = "yanzheng.cloud.session.v1";
const headerName = "x-yanzheng-session";
const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json", ...headers },
  });
const tick = () => new Promise(resolve => setImmediate(resolve));

function storage(initial = {}) {
  const result = { ...initial };
  Object.defineProperties(result, {
    getItem: { value: key => Object.hasOwn(result, key) ? result[key] : null },
    setItem: { value: (key, value) => { result[key] = String(value); } },
    removeItem: { value: key => { delete result[key]; } },
  });
  return result;
}

function harness({ token = "A".repeat(43), initialSession = {}, onFetch } = {}) {
  const requests = [], downloads = [], blobs = [], revoked = [], timers = [];
  const byId = new Map(), created = [], documentListeners = new Map();
  const sessionStorage = storage(initialSession);
  const localStorage = storage();
  let reloads = 0;

  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.listeners = new Map();
      this.style = {};
      this.dataset = {};
      this.textContent = "";
      this.value = "";
      this.dispatched = [];
      created.push(this);
    }
    set id(value) { this._id = value; byId.set(value, this); }
    get id() { return this._id; }
    setAttribute(key, value) { this[key] = value; }
    append(...children) {
      for (const child of children) { child.parent = this; this.children.push(child); }
    }
    appendChild(child) { this.append(child); return child; }
    prepend(child) { child.parent = this; this.children.unshift(child); }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    querySelector() { return null; }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    dispatchEvent(event) { this.dispatched.push(event.type); return true; }
    closest(selector) {
      if (selector === "a[download]" && this.tagName === "A" && this.download !== undefined) return this;
      return this.parent?.closest(selector) || null;
    }
    click() {
      if (this.tagName === "A") downloads.push({ href: this.href, filename: this.download });
      return this.listeners.get("click")?.({ target: this });
    }
    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
    }
    focus() { this.focused = true; }
    scrollIntoView() {}
  }

  const body = new Element("body");
  const main = new Element("main");
  body.append(main);
  for (const id of ["initial-idea", "goal", "resources", "time-horizon", "context", "planning-mode"]) {
    const field = new Element(id === "initial-idea" ? "textarea" : "input");
    field.id = id;
    main.append(field);
  }
  const document = {
    readyState: "loading", body,
    getElementById: id => byId.get(id) || null,
    querySelector: selector => selector === "main" ? main : null,
    querySelectorAll: () => [],
    createElement: tag => new Element(tag),
    addEventListener(name, callback, options) { documentListeners.set(name, { callback, options }); },
  };
  const location = { href: `${origin}/#explore`, origin, hash: "#explore", reload() { reloads += 1; } };

  async function fetchStub(input, options = {}) {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(options.headers || {}).forEach((value, key) => headers.set(key, value));
    const request = {
      url: url.href, pathname: url.pathname, options,
      method: options.method || (input instanceof Request ? input.method : "GET"),
      headers, body: options.body,
    };
    requests.push(request);
    if (onFetch) {
      const custom = await onFetch(request);
      if (custom !== undefined) return custom;
    }
    if (url.pathname === "/demo/session") return json({ session: token, idle_seconds: 3600, max_projects: 3 });
    if (url.pathname === "/demo/info") return json({ session_call_limit: null, daily_call_limit: null, model_call_limit_policy: "unlimited", model_calls_enabled: false });
    if (url.pathname === "/api/status") return json({ model: { configured: false } });
    return json({ ok: true });
  }

  const window = { fetch: fetchStub, sessionStorage, localStorage, confirm: () => true };
  const sandbox = {
    window, document, location, sessionStorage, localStorage, Request, Response, Headers, Blob,
    Event: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    MutationObserver: class { constructor(callback) { this.callback = callback; } observe() {} },
    URL: class extends URL {
      static createObjectURL(blob) { blobs.push(blob); return `blob:cloud-test-${blobs.length}`; }
      static revokeObjectURL(value) { revoked.push(value); }
    },
    setTimeout(callback, delay) { timers.push({ callback, delay }); return timers.length; },
  };
  vm.runInNewContext(source, sandbox, { filename: sourcePath, timeout: 1000 });
  return {
    window, document, requests, downloads, blobs, revoked, timers, created, sessionStorage, localStorage,
    status: () => byId.get("cloud-demo-status")?.textContent || "",
    reloads: () => reloads,
    async mount() { documentListeners.get("DOMContentLoaded").callback(); await tick(); },
    button(label) { return created.find(element => element.tagName === "BUTTON" && element.textContent === label); },
    anchor(href, download = "") {
      const anchor = new Element("a");
      anchor.href = new URL(href, origin).href;
      if (download !== null) anchor.download = download;
      return anchor;
    },
    async click(target) {
      const event = {
        target, prevented: false, stopped: false,
        preventDefault() { this.prevented = true; },
        stopImmediatePropagation() { this.stopped = true; },
      };
      await documentListeners.get("click").callback(event);
      return event;
    },
    field: id => byId.get(id),
  };
}

test("loading, mounting and filling the public sample never initialize a session or call a model", async () => {
  const h = harness();
  assert.equal(h.requests.length, 0);
  await h.mount();
  assert.deepEqual(h.requests.map(item => [item.method, item.pathname]), [["GET", "/demo/info"]]);
  h.button("填入公开样例").click();
  assert.match(h.field("initial-idea").value, /随机低秩逼近/);
  assert.equal(h.field("goal").value, "comparison");
  assert.match(h.field("context").value, /不宣称新定理/);
  assert.equal(h.field("planning-mode").value, "demo");
  assert.match(h.status(), /不调用模型/);
  assert.equal(h.requests.length, 1);
  assert.equal(h.downloads.length, 0);
});

test("configured model sample selection still performs no generation or acceptance request", async () => {
  const h = harness({
    onFetch: request => request.pathname === "/api/status" ? json({ model: { configured: true } }) : undefined,
  });
  await h.mount();
  const response = await h.window.fetch("/api/status");
  assert.equal((await response.json()).model.configured, true, "adapter status inspection does not consume the UI response");
  const before = h.requests.length;
  h.button("填入公开样例").click();
  assert.equal(h.field("planning-mode").value, "skill");
  assert.match(h.status(), /尚未调用模型/);
  assert.equal(h.requests.length, before);
  assert.equal(h.requests.filter(item => item.method === "POST").length, 1, "only empty session initialization was posted");
  assert.equal(h.requests.find(item => item.method === "POST").pathname, "/demo/session");
});

test("cloud banner describes unlimited model calls without displaying null as a count", async () => {
  const h = harness();
  await h.mount();
  const banner = h.created.map(element => element.textContent).join("\n");
  assert.match(banner, /访客模型试用次数无上限/);
  assert.match(banner, /全站每日模型请求次数无上限/);
  assert.doesNotMatch(banner, /最多 null|最多 3 次|最多 30 次|费用受试用次数限制/);
});

test("cloud banner remains accurate while an older backend still reports finite limits", async () => {
  const h = harness({ onFetch: request => request.pathname === "/demo/info"
    ? json({ session_call_limit: 3, daily_call_limit: 30, model_calls_enabled: true }) : undefined });
  await h.mount();
  const banner = h.created.map(element => element.textContent).join("\n");
  assert.match(banner, /访客最多 3 次模型请求，全站每天最多 30 次/);
});

test("parallel API requests wait for one successful session initialization", async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const token = "S".repeat(43);
  const h = harness({ onFetch: request => request.pathname === "/demo/session" ? gate : undefined });
  const first = h.window.fetch("/api/projects");
  const second = h.window.fetch("/api/status");
  await tick();
  assert.deepEqual(h.requests.map(item => item.pathname), ["/demo/session"]);
  const init = h.requests[0];
  assert.equal(init.method, "POST");
  assert.equal(init.body, "{}");
  assert.equal(init.headers.get("content-type"), "application/json");
  assert.equal(init.options.credentials, "same-origin");
  assert.equal(init.options.redirect, "error");
  release(json({ session: token, idle_seconds: 3600, max_projects: 3 }));
  await Promise.all([first, second]);
  assert.equal(h.requests.filter(item => item.pathname === "/demo/session").length, 1);
  for (const request of h.requests.slice(1)) {
    assert.equal(request.headers.get(headerName), token);
    assert.equal(request.options.credentials, "same-origin");
    assert.equal(request.options.redirect, "error");
    assert.equal(request.options.cache, "no-store");
    assert.ok(!request.url.includes(token));
  }
  assert.equal(h.sessionStorage.getItem(tokenKey), token);
});

test("request bodies and existing headers survive while the current session replaces a caller token", async () => {
  const h = harness();
  const request = new Request(`${origin}/api/projects`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Existing": "retained" },
    body: '{"initial_idea":"public synthetic example"}',
  });
  await h.window.fetch(request, { headers: { "X-Added": "retained-too", "X-Yanzheng-Session": "wrong-session" } });
  const sent = h.requests.at(-1);
  assert.equal(sent.method, "POST");
  assert.equal(sent.headers.get("content-type"), "application/json");
  assert.equal(sent.headers.get("x-existing"), "retained");
  assert.equal(sent.headers.get("x-added"), "retained-too");
  assert.equal(sent.headers.get(headerName), "A".repeat(43));
  assert.equal(await request.text(), '{"initial_idea":"public synthetic example"}');
});

test("separate browser contexts retain separate bearer tokens and never reuse the other context", async () => {
  const a = harness({ token: "A".repeat(43) });
  const b = harness({ token: "B".repeat(43) });
  await Promise.all([a.window.fetch("/api/projects"), b.window.fetch("/api/projects")]);
  await Promise.all([a.window.fetch("/api/projects/a/export"), b.window.fetch("/api/projects/b/export")]);
  assert.equal(a.sessionStorage.getItem(tokenKey), "A".repeat(43));
  assert.equal(b.sessionStorage.getItem(tokenKey), "B".repeat(43));
  assert.ok(a.requests.filter(item => item.pathname.startsWith("/api/")).every(item => item.headers.get(headerName) === "A".repeat(43)));
  assert.ok(b.requests.filter(item => item.pathname.startsWith("/api/")).every(item => item.headers.get(headerName) === "B".repeat(43)));
});

test("a restored bearer is sent only in the initialization header, never URL or cookie", async () => {
  const restored = "R".repeat(43);
  const h = harness({ token: restored, initialSession: { [tokenKey]: restored } });
  await h.window.fetch("/api/projects");
  assert.equal(h.requests[0].headers.get(headerName), restored);
  assert.equal(h.requests[0].headers.get("cookie"), null);
  assert.equal(h.requests[0].url, `${origin}/demo/session`);
  assert.equal(h.requests[0].options.credentials, "same-origin");
});

test("cross-origin API-looking URLs and non-API resources receive no adapter bearer or session call", async () => {
  const h = harness({ initialSession: { [tokenKey]: "R".repeat(43) } });
  for (const url of ["https://other.example.test/api/projects", "/static/app.js", "/demo/public-evidence.zip"]) {
    await h.window.fetch(url);
  }
  assert.equal(h.requests.length, 3);
  assert.ok(h.requests.every(item => item.headers.get(headerName) === null));
  assert.ok(h.requests.every(item => item.pathname !== "/demo/session"));
  assert.equal(h.requests[0].options.credentials, undefined, "adapter never enables credentials on an external request");
});

test("private-platform authenticated same-origin requests establish a session and read status", async () => {
  const h = harness({ onFetch: request => {
    if (request.options.credentials !== "same-origin") return json({ detail: "platform login required" }, 403);
    if (request.pathname === "/api/status") return json({ model: { configured: true } });
  }});
  await h.mount();
  const response = await h.window.fetch("/api/status");
  assert.equal(response.status, 200);
  assert.equal((await response.json()).model.configured, true);
  assert.ok(h.requests.every(request => request.options.credentials === "same-origin"));
  assert.ok(h.requests.every(request => request.options.redirect === "error"));
  assert.ok(h.requests.every(request => request.headers.get("cookie") === null), "JavaScript never reads or constructs platform cookies");
});

test("failed initialization blocks all dependent APIs without retrying or inventing a session", async () => {
  const h = harness({
    onFetch: request => request.pathname === "/demo/session" ? json({ detail: "会话容量已满" }, 429) : undefined,
  });
  await h.mount();
  const results = await Promise.allSettled([h.window.fetch("/api/projects"), h.window.fetch("/api/status")]);
  assert.ok(results.every(result => result.status === "rejected" && /会话容量已满/.test(result.reason.message)));
  assert.equal(h.requests.filter(item => item.pathname === "/demo/session").length, 1);
  assert.equal(h.requests.filter(item => item.pathname.startsWith("/api/")).length, 0);
  assert.equal(h.sessionStorage.getItem(tokenKey), null);
  assert.match(h.status(), /会话容量已满/);
});

test("expired API sessions are reported without silently creating a replacement", async () => {
  const h = harness({
    onFetch: request => request.pathname === "/api/projects" ? json({ detail: "expired" }, 401) : undefined,
  });
  await h.mount();
  const response = await h.window.fetch("/api/projects");
  assert.equal(response.status, 401);
  assert.match(h.status(), /会话已过期/);
  assert.equal(h.requests.filter(item => item.pathname === "/demo/session").length, 1);
  assert.equal(h.downloads.length, 0);
});

test("an explicit action retries session initialization after a transient failure", async () => {
  let attempts = 0;
  const h = harness({ onFetch: request => {
    if (request.pathname === "/demo/session" && ++attempts === 1) return json({ detail: "暂时繁忙" }, 503);
  }});
  await h.mount();
  await assert.rejects(h.window.fetch("/api/projects"), /暂时繁忙/);
  const response = await h.window.fetch("/api/projects");
  assert.equal(response.status, 200);
  assert.equal(attempts, 2);
  assert.equal(h.requests.filter(item => item.pathname === "/api/projects").length, 1);
  assert.equal(h.sessionStorage.getItem(tokenKey), "A".repeat(43));
});

test("a rejected network request can recover without reloading or losing the existing token", async () => {
  const oldToken = "B".repeat(43);
  let attempts = 0;
  const h = harness({ token: oldToken, initialSession: { [tokenKey]: oldToken }, onFetch: request => {
    if (request.pathname === "/demo/session" && ++attempts === 1) throw new Error("temporary network outage");
  }});
  await h.mount();
  await assert.rejects(h.window.fetch("/api/status"));
  assert.equal(h.sessionStorage.getItem(tokenKey), oldToken);
  assert.equal((await h.window.fetch("/api/status")).status, 200);
  assert.equal(h.requests.filter(item => item.pathname === "/demo/session").length, 2);
  assert.ok(h.requests.filter(item => item.pathname === "/demo/session").every(item => item.headers.get(headerName) === oldToken));
  assert.equal(h.reloads(), 0);
});

test("public sample notice is cleared once a write is actually submitted", async () => {
  const h = harness({ onFetch: request => request.pathname === "/api/status" ? json({ model: { configured: true } }) : undefined });
  await h.mount();
  await h.window.fetch("/api/status");
  h.button("填入公开样例").click();
  assert.match(h.status(), /尚未调用模型/);
  await h.window.fetch("/api/projects", { method: "POST", body: "{}" });
  assert.doesNotMatch(h.status(), /尚未调用模型|点击创建才请求/);
  assert.match(h.status(), /独立临时数据/);
  assert.equal(h.requests.filter(item => item.pathname === "/api/projects").length, 1);
});

test("only same-origin download anchors under /api/ are intercepted", async () => {
  const h = harness();
  for (const anchor of [
    h.anchor("https://other.example.test/api/projects/x/export"),
    h.anchor("/demo/public-evidence.zip"),
    h.anchor("/api/projects/x/export", null),
  ]) {
    const event = await h.click(anchor);
    assert.equal(event.prevented, false);
    assert.equal(event.stopped, false);
  }
  assert.equal(h.requests.length, 0);
  assert.equal(h.blobs.length, 0);
});

test("JSON and Markdown downloads preserve returned bytes, filenames and the current session header", async () => {
  for (const format of ["json", "md"]) {
    const content = format === "json" ? '{"public":true,"title":"公开样例"}\n' : "# 公开样例\n\n未独立审阅。\n";
    const filename = `theory-check-public.${format}`;
    const h = harness({
      onFetch: request => request.pathname.endsWith("/export")
        ? new Response(content, { headers: {
          "content-type": format === "json" ? "application/json" : "text/markdown; charset=utf-8",
          "content-disposition": `attachment; filename="${filename}"`,
        } }) : undefined,
    });
    await h.mount();
    const anchor = h.anchor(`/api/projects/public/theory/checks/public/export?format=${format}`);
    const nested = { closest: selector => anchor.closest(selector) };
    const event = await h.click(nested);
    assert.equal(event.prevented, true);
    assert.equal(event.stopped, true);
    assert.equal(h.requests.at(-1).headers.get(headerName), "A".repeat(43));
    assert.equal(await h.blobs[0].text(), content);
    assert.deepEqual(h.downloads, [{ href: "blob:cloud-test-1", filename }]);
    assert.match(h.status(), /交给浏览器下载/);
    assert.equal(h.timers[0].delay, 30000);
    h.timers[0].callback();
    assert.deepEqual(h.revoked, ["blob:cloud-test-1"]);
  }
});

test("failed exports never create a Blob or successful download notification", async () => {
  for (const failure of ["http", "network", "invalid-json"]) {
    const h = harness({
      onFetch: request => {
        if (!request.pathname.endsWith("/export")) return undefined;
        if (failure === "network") throw new Error("synthetic network failure");
        if (failure === "invalid-json") return new Response("bad gateway", { status: 502 });
        return json({ detail: "该会话无权访问此项目" }, 404);
      },
    });
    await h.mount();
    const event = await h.click(h.anchor("/api/projects/other/export"));
    assert.equal(event.prevented, true);
    assert.equal(h.downloads.length, 0);
    assert.equal(h.blobs.length, 0);
    assert.equal(h.timers.length, 0);
    assert.doesNotMatch(h.status(), /交给浏览器下载/);
    assert.equal(h.document.getElementById("cloud-demo-status").style.color, "#b42318");
    assert.equal(h.requests.filter(item => item.pathname.endsWith("/export")).length, 1);
  }
});

test("explicit new-session action clears app storage but keeps unrelated origin storage", async () => {
  const h = harness({ initialSession: { [tokenKey]: "R".repeat(43), unrelated: "keep" } });
  h.localStorage.setItem("yanzheng.intakeDraft", "old draft");
  h.localStorage.setItem("unrelated.preference", "keep");
  await h.mount();
  h.window.confirm = () => false;
  h.button("开始新会话").click();
  assert.equal(h.sessionStorage.getItem(tokenKey), "R".repeat(43));
  assert.equal(h.reloads(), 0);
  h.window.confirm = () => true;
  h.button("开始新会话").click();
  assert.equal(h.sessionStorage.getItem(tokenKey), null);
  assert.equal(h.sessionStorage.getItem("unrelated"), "keep");
  assert.equal(h.localStorage.getItem("yanzheng.intakeDraft"), null);
  assert.equal(h.localStorage.getItem("unrelated.preference"), "keep");
  assert.equal(h.reloads(), 1);
  assert.equal(h.requests.filter(item => item.method !== "GET").length, 0);
});
