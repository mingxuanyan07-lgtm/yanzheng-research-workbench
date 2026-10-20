/** Runs only against the uniquely identified acceptance server; no real user profile. */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const [out, playwrightModule, chrome] = process.argv.slice(2);
const { chromium } = createRequire(import.meta.url)(playwrightModule);
const info = JSON.parse(fs.readFileSync(path.join(out, "SERVER.json"), "utf8"));
const checks = [], errors = [], blocked = [], screenshots = [];
const check = (name, pass) => {
  checks.push({ name, pass: Boolean(pass) });
  if (!pass) throw new Error(name);
};
const browser = await chromium.launch({
  executablePath: chrome, headless: true,
  args: ["--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--disable-background-networking", "--disable-component-update"],
});
const result = { browser: browser.version(), run_id: info.run_id, checks, errors, blocked, screenshots };
const deadline = setTimeout(() => {
  result.failure = "Overall browser deadline exceeded";
  void browser.close().catch(() => {});
}, 110000);
async function fetchProject(page, id) {
  return page.evaluate(async id => {
    const response = await fetch(`/api/projects/${id}`);
    if (!response.ok) throw new Error(`GET project ${response.status}`);
    return response.json();
  }, id);
}
try {
  for (const action of ["accept", "reject", "stale", "legacy"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN" });
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.origin === info.base) return route.continue();
      blocked.push(url.origin); return route.abort();
    });
    await context.addInitScript(id => localStorage.setItem("yanzheng.activeProject", id), info.projects[action]);
    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    page.on("pageerror", error => errors.push(String(error)));
    await page.goto(info.base + "/#explore", { waitUntil: "networkidle" });
    const identity = await page.evaluate(async () => (await fetch("/__acceptance__/identity")).json());
    check(`${action}: exact server and isolated database`, identity.run_id === info.run_id && identity.database === info.database);
    check(`${action}: fixture model and no Lean`, identity.model === "offline-fixture" && identity.lean_enabled === false);
    const id = info.projects[action];
    const before = await fetchProject(page, id);
    if (action === "legacy") {
      const topics = before.direction_map.topics;
      const exported = await page.evaluate(async id => {
        const response = await fetch(`/api/projects/${id}/export`);
        return { status: response.status, body: response.ok ? await response.json() : null };
      }, id);
      check("legacy: JSON export succeeds and preserves map", exported.status === 200 &&
        JSON.stringify(exported.body.direction_map) === JSON.stringify(before.direction_map));
      await page.locator(`button[data-action="select"][data-topic-id="${topics[0].id}"]`).click();
      await page.waitForSelector('[data-action="reopen"]');
      const selected = await fetchProject(page, id);
      check("legacy: topic selection succeeds", selected.direction_map.selected_topic_id === topics[0].id);
      check("legacy: selection does not fabricate note binding",
        JSON.stringify(selected.direction_map.topics) === JSON.stringify(topics));
      const planText = await page.locator(".plan-panel").innerText();
      check("legacy: UI discloses unbound historical evidence", /历史/.test(planText) && /未绑定/.test(planText));
      const plan = await page.evaluate(async id => {
        const response = await fetch(`/api/projects/${id}/plan`);
        return { status: response.status, text: await response.text() };
      }, id);
      check("legacy: Markdown retains unbound warning", plan.status === 200 && /历史/.test(plan.text) && /未绑定/.test(plan.text));
      await page.locator('[data-action="reopen"]').click();
      await page.waitForSelector('[data-action="select"]');
      const reopened = await fetchProject(page, id);
      check("legacy: reopening clears selection", reopened.direction_map.selected_topic_id === null);
      check("legacy: reopening retains evidence unchanged", JSON.stringify(reopened.direction_map.topics) === JSON.stringify(topics));
    } else {
      const panel = page.locator("[data-direction-proposal]");
      await panel.waitFor({ state: "visible" });
      const pending = before.workflow.pending_proposal;
      check(`${action}: pending proposal visible`, /待确认的方向修订/.test(await panel.innerText()));
      check(`${action}: pending stored`, pending?.status === "pending");
      if (action === "accept") {
        check("accept: candidates not applied before acceptance", before.direction_map.topics.length === 0);
      }
      if (action === "reject") {
        check("reject: test begins with a nonempty existing map", before.direction_map.topics.length === 3);
      }
      if (action === "stale") {
        check("stale: GET marks proposal stale", before.workflow.pending_proposal_stale === true);
        check("stale: warning is visible", /上下文已变化/.test(await panel.innerText()));
        check("stale: accept disabled", await page.locator('[data-action="direction-accept"]').isDisabled());
        check("stale: reject enabled", await page.locator('[data-action="direction-reject"]').isEnabled());
        const status = await page.evaluate(async ({ id, proposal }) => (await fetch(
          `/api/projects/${id}/direction/proposals/${proposal}/accept`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).status,
          { id, proposal: pending.id });
        check("stale: real API rejects accept with 409", status === 409);
      }
      const shotBefore = action + "-before.png";
      await page.screenshot({ path: path.join(out, shotBefore), fullPage: true });
      screenshots.push(shotBefore);
      await page.locator(`[data-action="direction-${action === "stale" ? "reject" : action}"]`).click();
      await page.waitForSelector("[data-direction-proposal]", { state: "detached" });
      const after = await fetchProject(page, id);
      check(`${action}: pending cleared`, after.workflow.pending_proposal == null);
      const call = after.model_calls.find(item => item.id === pending.model_call_id);
      if (action === "accept") {
        check("accept: three candidates applied", after.direction_map.topics.length === 3);
        check("accept: recommendation remains unselected", after.direction_map.selected_topic_id === null);
        const archive = after.direction_versions.find(item => item.proposal_id === pending.id);
        check("accept: exact previous map archived", JSON.stringify(archive?.direction_map) === JSON.stringify(before.direction_map));
        check("accept: model call applied", call?.application_status === "applied");
      } else {
        check(`${action}: map and selection preserved`, JSON.stringify(after.direction_map) === JSON.stringify(before.direction_map));
        check(`${action}: archives unchanged`, JSON.stringify(after.direction_versions) === JSON.stringify(before.direction_versions));
        check(`${action}: model call rejected`, call?.application_status === "rejected_by_user");
      }
    }
    const shot = action + "-after.png";
    await page.screenshot({ path: path.join(out, shot), fullPage: true });
    screenshots.push(shot);
    await context.close();
  }
  check("no browser page errors", errors.length === 0);
  check("no external page requests", blocked.length === 0);
} catch (error) {
  result.failure = String(error.stack || error);
  throw error;
} finally {
  clearTimeout(deadline);
  result.passed = checks.filter(item => item.pass).length;
  result.failed = checks.filter(item => !item.pass).length + (result.failure && checks.every(item => item.pass) ? 1 : 0);
  fs.writeFileSync(path.join(out, "browser-checks.json"), JSON.stringify(result, null, 2) + "\n");
  await browser.close();
}
console.log(JSON.stringify({ passed: result.passed, failed: result.failed }));
