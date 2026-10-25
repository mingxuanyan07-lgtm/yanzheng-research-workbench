"use strict";
// Actual Chrome and browser downloads, with two owned, offline, disposable services.
const fs=require("node:fs"),path=require("node:path"),assert=require("node:assert/strict");
const {spawn,spawnSync}=require("node:child_process");
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||"playwright");
const root=path.resolve(__dirname,".."), output=path.join(root,"output/product-acceptance",`${Date.now()}-${require("node:crypto").randomBytes(3).toString("hex")}`);
const python=process.env.YANZHENG_QA_PYTHON||path.join(root,process.platform==="win32"?"webapp/.venv/Scripts/python.exe":"webapp/.venv/bin/python");
fs.mkdirSync(output,{recursive:true});
const servers=[],checks=[],errors=[];let browser;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function start(name){
  const folder=path.join(output,name);fs.mkdirSync(folder,{recursive:true});
  const process=spawn(python,["-X","utf8","-B",path.join(__dirname,"research_journey_acceptance_server.py"),"--output",folder],{cwd:root,windowsHide:true,stdio:["ignore","pipe","pipe"]});
  const server={name,folder,process,logs:""};servers.push(server);
  process.stdout.on("data",data=>server.logs+=data);process.stderr.on("data",data=>server.logs+=data);
  const identity=path.join(folder,"SERVER.json");
  for(let i=0;i<150&&!fs.existsSync(identity);i++){if(process.exitCode!==null)throw Error(server.logs);await pause(100);}
  assert.ok(fs.existsSync(identity),"Owned fixture starts");
  Object.assign(server,JSON.parse(fs.readFileSync(identity,"utf8")));
  return server;
}
async function banner(page){await page.evaluate(()=>{
  if(document.querySelector("#qa-fixture-banner"))return;
  const element=document.createElement("div");element.id="qa-fixture-banner";
  element.textContent="离线界面验收 · 独立临时库 · 合成题录/模型 · 未调用真实模型";
  element.style.cssText="position:fixed;bottom:0;left:0;right:0;z-index:9999;background:#fff4d5;color:#58471b;padding:6px;text-align:center;font-size:11px;pointer-events:none";
  document.body.append(element);
});}
async function projects(page){await page.locator('[data-view="projects"]').first().click();await page.locator(".project-data-tools>summary").waitFor();}
async function openDataTools(page){await page.locator(".project-data-tools>summary").click();await page.locator("#backup-file").waitFor({state:"attached"});}
async function run(){
  const source=await start("source"),target=await start("restore-target");
  browser=await chromium.launch({channel:process.env.YANZHENG_QA_BROWSER||"chrome",headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
  const page=await context.newPage(),restored=await context.newPage();
  for(const [name,tab] of [["source",page],["target",restored]])tab.on("pageerror",error=>errors.push({name,message:error.message}));
  await page.goto(source.base);await page.waitForFunction(()=>!document.querySelector("#intake-model-notice").textContent.includes("正在读取"));
  assert.equal(await page.locator('#idea-form button[type="submit"]').count(),1);
  await banner(page);await page.screenshot({path:path.join(output,"desktop-start.png"),animations:"disabled"});checks.push("clear initial primary action");
  const startActionBox=await page.locator("#create-button").boundingBox();
  fs.writeFileSync(path.join(output,"start-action-layout.json"),JSON.stringify(startActionBox,null,2));
  assert.ok(startActionBox&&startActionBox.y+startActionBox.height<=1050,"Primary creation action fits the 1440x1050 desktop viewport");
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),"Empty mobile intake has no horizontal overflow");
  await page.screenshot({path:path.join(output,"mobile-start.png"),animations:"disabled"});checks.push("mobile intake layout");
  await page.setViewportSize({width:1440,height:1050});
  await page.locator("#initial-idea").fill("[QA 合成产品验收] 在个人电脑上比较随机低秩逼近的误差与预算。");
  await page.locator("#planning-mode").selectOption("demo");await page.locator("#create-button").click();
  await page.locator(".research-journey").waitFor();
  assert.equal(await page.locator(".journey-next .button").count(),1);
  await page.locator('.journey-next [data-action="journey-read"]').click();
  await page.locator("#reference-input").fill("10.1137/090771806");await page.locator('#reference-import-form button[type="submit"]').click();
  await page.locator(".imported-metadata").waitFor();await page.locator('[data-action="prepare-abstract-note"]').click();
  assert.equal(await page.locator('.reading-note-form [name="confirmed"]').isChecked(),false);
  await page.locator('.reading-note-form [name="confirmed"]').check();await page.locator('.reading-note-form button[type="submit"]').click();
  await page.locator(".reading-note").waitFor();checks.push("real UI metadata import and explicit reading confirmation");
  await page.locator('[data-action="return-research"]').first().click();
  await page.locator(".research-actions>summary").click();await page.locator('[data-action="research-plan-create"]').click();
  await page.locator('[data-action="research-action-run"][data-action-id="search-1"]').waitFor();
  const initial=await(await page.request.get(source.base+"/api/projects")).json();assert.equal(initial.projects.length,1);
  const id=initial.projects[0].id;
  await projects(page);await page.locator("#project-search").fill("definitely-no-such-project");
  assert.equal(await page.evaluate(()=>document.activeElement.id),"project-search");
  assert.equal(await page.locator('#project-results [data-action="open"]').count(),0);
  await page.locator('[data-action="clear-project-filters"]').click();
  assert.ok(await page.locator('#project-results [data-action="open"]').count()>0);checks.push("project search retains focus and clear filters restores results");
  await openDataTools(page);
  const pendingDownload=page.waitForEvent("download");await page.locator('a[href="/api/backups/export"]').click();
  const download=await pendingDownload;const backup=path.join(output,"downloaded-backup.json");await download.saveAs(backup);
  const document=JSON.parse(fs.readFileSync(backup,"utf8"));assert.equal(document.project_count,1);
  assert.equal(document.projects[0].reading_notes.length,1);checks.push("actual complete backup browser download");
  await restored.goto(target.base);await restored.waitForFunction(()=>!document.querySelector("#intake-model-notice").textContent.includes("正在读取"));
  await projects(restored);await openDataTools(restored);
  await restored.locator("#backup-file").setInputFiles(backup);
  await restored.waitForFunction(()=>document.querySelector("#backup-feedback")?.textContent.includes("恢复 1"));
  let current=await(await restored.request.get(target.base+"/api/projects/"+id)).json();
  assert.equal(current.reading_notes.length,1);assert.equal(current.research_plan.status,"paused");
  assert.equal(current.backup_restore.revalidation_status,"not_revalidated");checks.push("restore into independent empty database preserves evidence and pauses actions");
  const before=current.updated_at;
  await restored.locator("#backup-file").setInputFiles(backup);
  await restored.waitForFunction(()=>document.querySelector("#backup-feedback")?.textContent.includes("跳过 1"));
  current=await(await restored.request.get(target.base+"/api/projects/"+id)).json();assert.equal(current.updated_at,before);
  assert.equal((await(await restored.request.get(target.base+"/api/projects")).json()).projects.length,1);checks.push("duplicate restore does not overwrite or duplicate projects");
  let restorePosts=0;restored.on("request",request=>{if(request.url().endsWith("/api/backups/restore")&&request.method()==="POST")restorePosts++;});
  await restored.locator("#backup-file").setInputFiles({name:"invalid.json",mimeType:"application/json",buffer:Buffer.from("{ invalid JSON")});
  await restored.waitForFunction(()=>document.querySelector("#backup-feedback")?.textContent.includes("JSON"));
  assert.equal(restorePosts,0);checks.push("invalid JSON is explained without a restore request");
  const raw=fs.readFileSync(backup,"utf8").replace('"format": "yanzheng-local-backup"','"format": "unexpected", "format": "yanzheng-local-backup"');
  assert.notEqual(raw,fs.readFileSync(backup,"utf8"));
  await restored.locator("#backup-file").setInputFiles({name:"duplicate-keys.json",mimeType:"application/json",buffer:Buffer.from(raw)});
  await restored.waitForFunction(()=>document.querySelector("#backup-feedback")?.textContent.includes("重复"));
  assert.equal(restorePosts,1);checks.push("raw duplicate JSON fields reach server and are rejected");
  await restored.locator('#project-results [data-action="open"]').first().click();await restored.locator(".research-journey").waitFor();
  assert.ok((await restored.locator("#project-content").textContent()).includes("备份"));
  await restored.reload();await restored.locator(".research-journey").waitFor();checks.push("restored project reopens and survives refresh");
  const doctor=spawnSync(python,["-X","utf8","-B",path.join(__dirname,"check_workbench.py"),"--port",new URL(target.base).port,"--json"],{cwd:root,encoding:"utf8",windowsHide:true});
  assert.equal(doctor.status,0,doctor.stderr);
  const diagnosis=JSON.parse(doctor.stdout);assert.equal(diagnosis.ready,true);assert.equal(diagnosis.service.state,"current");
  fs.writeFileSync(path.join(output,"startup-check.json"),JSON.stringify(diagnosis,null,2));checks.push("read-only startup check recognizes the current running source");
  await banner(restored);await restored.screenshot({path:path.join(output,"desktop-restored.png"),animations:"disabled"});
  for(const width of [390,320]){
    await restored.setViewportSize({width,height:844});await restored.locator(".research-journey").scrollIntoViewIfNeeded();
    const layout=await restored.evaluate(()=>({viewport:innerWidth,width:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll("body *")].filter(el=>el.getClientRects().length&&el.getBoundingClientRect().right>innerWidth+1).map(el=>({tag:el.tagName,class:typeof el.className==="string"?el.className:"",text:el.textContent.slice(0,100)})).slice(0,20)}));
    fs.writeFileSync(path.join(output,`layout-${width}.json`),JSON.stringify(layout,null,2));assert.ok(layout.width<=width+1,`${width}px has no horizontal overflow`);
    await restored.screenshot({path:path.join(output,`mobile-${width}.png`),animations:"disabled"});checks.push(`${width}px responsive layout`);
  }
  const audit=await(await restored.request.get(target.base+"/__research_qa__")).json();
  assert.equal(audit.import_operations,0);assert.equal(audit.search_operations,0);assert.equal(audit.model_operations,0);assert.equal(audit.model_network_calls,0);
  checks.push("restoring never executes tools or model");
  assert.deepEqual(errors,[]);checks.push("no browser runtime errors");
  const result={status:"passed",fixture:"offline_synthetic_two_databases",checks,audit};
  fs.writeFileSync(path.join(output,"validation.json"),JSON.stringify(result,null,2));console.log(JSON.stringify({status:"passed",output,checks:checks.length}));
}
run().catch(async error=>{
  fs.writeFileSync(path.join(output,"failure.txt"),error.stack);console.error(error.stack);process.exitCode=1;
  if(browser)for(const [i,page] of browser.contexts().flatMap(context=>context.pages()).entries())await page.screenshot({path:path.join(output,`failure-${i}.png`)}).catch(()=>{});
}).finally(async()=>{if(browser)await browser.close();for(const server of servers){server.process.kill();fs.writeFileSync(path.join(server.folder,"server.log"),server.logs);}});
