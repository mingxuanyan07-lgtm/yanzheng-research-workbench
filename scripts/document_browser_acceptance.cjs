"use strict";
// Real Chrome, owned disposable databases, synthetic files/metadata/model only.
const fs=require("node:fs"),path=require("node:path"),assert=require("node:assert/strict");
const {spawn}=require("node:child_process"),{createHash,randomBytes}=require("node:crypto");
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||"playwright");
const root=path.resolve(__dirname,".."),output=path.join(root,"output/document-acceptance",`${Date.now()}-${randomBytes(3).toString("hex")}`);
const python=process.env.YANZHENG_QA_PYTHON||path.join(root,process.platform==="win32"?"webapp/.venv/Scripts/python.exe":"webapp/.venv/bin/python");
fs.mkdirSync(output,{recursive:true});
const servers=[],checks=[],errors=[];let browser;
checks.push=(...items)=>{Array.prototype.push.apply(checks,items);fs.writeFileSync(path.join(output,"progress.json"),JSON.stringify(checks,null,2));return checks.length;};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const sha=value=>createHash("sha256").update(value).digest("hex");
async function start(name){
  const folder=path.join(output,name);fs.mkdirSync(folder,{recursive:true});
  const child=spawn(python,["-X","utf8","-B",path.join(__dirname,"research_journey_acceptance_server.py"),"--output",folder],{cwd:root,windowsHide:true,stdio:["ignore","pipe","pipe"]});
  const server={folder,child,logs:""};servers.push(server);
  child.stdout.on("data",data=>server.logs+=data);child.stderr.on("data",data=>server.logs+=data);
  const identity=path.join(folder,"SERVER.json");
  for(let i=0;i<150&&!fs.existsSync(identity);i++){if(child.exitCode!==null)throw Error(server.logs);await pause(100);}
  assert.ok(fs.existsSync(identity),"isolated fixture starts");return Object.assign(server,JSON.parse(fs.readFileSync(identity,"utf8")));
}
function syntheticPDF(){
  const stream=text=>`BT /F1 12 Tf 50 700 Td (${text}) Tj ET\n`;
  const first=stream("QA synthetic first page. Not a scientific publication."),second=stream("QA second-page evidence with a verified locator.");
  const objects=["<< /Type /Catalog /Pages 2 0 R >>","<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${Buffer.byteLength(first)} >>\nstream\n${first}endstream`,
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
    `<< /Length ${Buffer.byteLength(second)} >>\nstream\n${second}endstream`,"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"];
  let pdf="%PDF-1.4\n",offsets=[0];
  for(const [i,value] of objects.entries()){offsets.push(Buffer.byteLength(pdf));pdf+=`${i+1} 0 obj\n${value}\nendobj\n`;}
  const xref=Buffer.byteLength(pdf);pdf+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;
  for(const offset of offsets.slice(1))pdf+=`${String(offset).padStart(10,"0")} 00000 n \n`;
  return Buffer.from(pdf+`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
}
async function annotate(page){await page.evaluate(()=>{
  if(document.querySelector("#qa-fixture-banner"))return;
  const element=document.createElement("div");element.id="qa-fixture-banner";element.textContent="原文联动离线验收 · 独立数据库 · 合成文件/题录/模型 · 未调用真实模型";
  element.style.cssText="position:fixed;bottom:0;left:0;right:0;z-index:9999;background:#fff4d5;color:#58471b;padding:6px;text-align:center;font-size:11px;pointer-events:none";document.body.append(element);
});}
async function upload(page,name,mimeType,buffer){
  const panel=page.locator(".paper-documents").first();
  await panel.locator(".paper-document-import").evaluate(element=>{element.open=true;});
  const form=panel.locator("form.paper-document-upload");
  await form.locator('[name="document_file"]').setInputFiles({name,mimeType,buffer});
  await form.locator('[name="binding_confirmed"]').check();
  const responsePromise=page.waitForResponse(response=>response.request().method()==="POST"&&/\/documents\?/.test(response.url()));
  await form.locator('button[type="submit"]').click();const response=await responsePromise;
  assert.equal(response.status(),200,await response.text());
  await panel.locator('[data-action="document-open"]').filter({hasText:name}).waitFor();
  return response.json();
}
async function selectQuote(page,quote){
  await page.locator(".paper-document-reader .document-page-text").first().evaluate((element,selected)=>{
    const start=element.textContent.indexOf(selected);if(start<0)throw Error("QA quote absent from actual DOM text");
    const locate=offset=>{const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode())){if(offset<=node.length)return {node,offset};offset-=node.length;}throw Error("Selection boundary missing");};
    const left=locate(start),right=locate(start+selected.length),range=document.createRange();range.setStart(left.node,left.offset);range.setEnd(right.node,right.offset);
    const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
  },quote);
  await page.locator('[data-action="document-excerpt"]').first().click();
  await page.locator(".document-anchor-draft").waitFor();
}
async function run(){
  const source=await start("source"),target=await start("restored");
  browser=await chromium.launch({channel:process.env.YANZHENG_QA_BROWSER||"chrome",headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
  context.setDefaultTimeout(20000);
  const page=await context.newPage(),restored=await context.newPage();
  for(const tab of [page,restored])tab.on("pageerror",error=>errors.push(error.message));
  await page.goto(source.base);await page.waitForFunction(()=>!document.querySelector("#intake-model-notice").textContent.includes("正在读取"));
  await page.locator("#initial-idea").fill("[QA 合成原文联动] 随机低秩近似，核对公平预算下的误差。");
  await page.locator("#planning-mode").selectOption("demo");await page.locator("#create-button").click();
  await page.locator(".research-journey").waitFor();await page.locator('[data-action="journey-read"]').first().click();
  await page.locator("#reference-input").fill("10.1137/090771806");await page.locator('#reference-import-form button[type="submit"]').click();
  await page.locator(".imported-metadata").waitFor();
  const quote="只在相同预算与适用条件下比较误差。",text="[QA 合成论文正文]\n前缀包含😀，用于验证 Unicode 定位。\n"+quote+"\n正文后段未选入笔记，不发送给模型。\n<script>window.qaInjected=true</script>";
  await upload(page,"QA-正文.txt","text/plain",Buffer.from(text));
  const id=(await(await page.request.get(source.base+"/api/projects")).json()).projects[0].id;
  const base=source.base+"/api/projects/"+id;
  let project=await(await page.request.get(base)).json();const txt=project.paper_documents[0];
  assert.equal(project.reading_notes?.length||0,0);assert.equal(txt.file_sha256,sha(Buffer.from(text)));
  assert.equal(await page.evaluate(()=>window.qaInjected),undefined);checks.push("UTF-8 import with SHA256, inert text, no automatic reading confirmation");
  await selectQuote(page,quote);
  const form=page.locator(".reading-note-form").first();
  assert.equal(await form.locator('[name="confirmed"]').isChecked(),false);
  await form.locator('[name="content"]').fill("QA 个人判断：该句仅说明比较前提，不证明方法优越。");
  assert.ok((await page.locator(".document-anchor-draft").textContent()).includes(quote));
  await form.locator('[name="confirmed"]').check();await form.locator('button[type="submit"]').click();await page.locator(".reading-note").waitFor();
  project=await(await page.request.get(base)).json();const note=project.reading_notes[0];
  assert.equal(note.document_anchor.quote,quote);assert.equal(note.document_anchor.start,Array.from(text.slice(0,text.indexOf(quote))).length);
  assert.equal(note.content,"QA 个人判断：该句仅说明比较前提，不证明方法优越。");checks.push("actual DOM selection creates an unconfirmed draft, Unicode offsets and separate commentary survive save");
  const before=project.updated_at;
  const forged=await page.request.post(base+"/reading-notes",{data:{run_id:note.run_id,source_id:note.source_id,level:note.level,content:"QA 伪造定位",locator:"fake",confirmed:true,document_anchor:{...note.document_anchor,quote:"伪造摘录"}}});
  assert.equal(forged.status(),400);assert.equal((await(await page.request.get(base)).json()).updated_at,before);checks.push("forged quote rejected without project writes");
  const pdf=syntheticPDF();fs.writeFileSync(path.join(output,"QA-two-pages.pdf"),pdf);
  await upload(page,"QA-two-pages.pdf","application/pdf",pdf);
  assert.equal(await page.locator(".document-page-select option").count(),2);
  await page.locator(".document-page-select").selectOption("2");
  assert.ok((await page.locator(".document-page-text").textContent()).includes("second-page evidence"));
  await page.locator('.document-search-form [name="query"]').fill("first page");await page.locator('.document-search-form button[type="submit"]').click();
  assert.equal(await page.locator(".document-page-select").inputValue(),"1");assert.equal(await page.locator("mark.document-search-highlight").textContent(),"first page");
  await page.locator('[data-action="document-select-match"]').focus();await page.keyboard.press("Enter");
  await page.locator('[data-action="document-excerpt"]').focus();await page.keyboard.press("Enter");
  assert.ok((await page.locator(".document-anchor-draft").textContent()).includes("first page"));
  assert.equal(await page.locator('.reading-note-form [name="confirmed"]').isChecked(),false);checks.push("keyboard search selection creates a separate unconfirmed PDF excerpt");
  const originalDownload=page.waitForEvent("download");await page.locator('.paper-document-reader a[href$="/original"]').click();
  const original=await originalDownload;const originalPath=path.join(output,"downloaded-original.pdf");await original.saveAs(originalPath);assert.equal(sha(fs.readFileSync(originalPath)),sha(pdf));
  checks.push("two-page text PDF extraction, cross-page search, original browser download with matching bytes");
  await upload(page,"QA-补充.md","text/markdown",Buffer.from("# QA 合成补充\n仅为文件格式验收，不是用户阅读确认。"));
  assert.equal((await(await page.request.get(base)).json()).paper_documents.length,3);checks.push("Markdown import stays inert and separate from reading evidence");
  await upload(page,"QA-正文.txt","text/plain",Buffer.from(text));
  await page.locator(`.paper-document-reader[data-document-id="${txt.id}"]`).waitFor();
  assert.equal(await page.locator(".paper-document-reader").getAttribute("data-document-id"),txt.id);
  assert.equal((await(await page.request.get(base)).json()).paper_documents.length,3);checks.push("duplicate upload reopens its original document without changing identity or opening the last file");
  let audit=await(await page.request.get(source.base+"/__research_qa__")).json();assert.equal(audit.model_operations,0);assert.equal(audit.model_network_calls,0);checks.push("upload, reading, selecting, search and save never call a model");
  await page.locator('[data-action="revise-from-literature"]').click();await page.locator('[data-action="direction-accept"]').waitFor();
  await page.locator('[data-action="direction-accept"]').click();await page.waitForFunction(()=>!document.querySelector('[data-action="direction-accept"]'));
  await page.locator('[data-action="journey-compare"]').first().click();
  await page.locator('.topic-card .topic-details>summary').first().click();
  await page.locator('.topic-card .evidence-note [data-action="document-anchor-open"]').first().click();
  assert.equal(await page.locator("mark.document-exact-highlight").textContent(),quote);assert.equal(await page.locator(".document-page-select").inputValue(),"1");
  assert.equal(await page.locator('.paper-document-reader a[href$="/original"]').count(),1);checks.push("original availability remains accurate after accepting a candidate revision");
  assert.ok((await page.locator(".reading-note .note-candidate-links").textContent()).includes("QA 合成摘录"));
  checks.push("accepted candidate evidence links to the exact original excerpt; note shows linked candidate claim");
  await annotate(page);await page.locator(".paper-document-reader").scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,"desktop-original-evidence.png"),animations:"disabled"});
  await page.reload();await page.locator('.reading-note [data-action="document-anchor-open"]').first().waitFor({state:"attached"});await page.locator('[data-view="literature"]').first().click();
  await page.locator('.reading-note [data-action="document-anchor-open"]').first().click();assert.equal(await page.locator("mark.document-exact-highlight").textContent(),quote);checks.push("saved citation survives browser refresh");
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});await annotate(page);await page.locator(".paper-document-reader").scrollIntoViewIfNeeded();
    const layout=await page.evaluate(()=>({viewport:innerWidth,width:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll("body *")].filter(el=>el.getClientRects().length&&el.getBoundingClientRect().right>innerWidth+1).map(el=>({tag:el.tagName,class:typeof el.className==="string"?el.className:"",text:el.textContent.slice(0,80)})).slice(0,20)}));
    fs.writeFileSync(path.join(output,`layout-${width}.json`),JSON.stringify(layout,null,2));assert.ok(layout.width<=width+1,`${width}px has no horizontal overflow`);
    await page.screenshot({path:path.join(output,`mobile-original-${width}.png`),animations:"disabled"});checks.push(`${width}px reader layout`);
  }
  await page.setViewportSize({width:1440,height:1050});
  await page.locator('[data-view="projects"]').first().click();await page.locator(".project-data-tools>summary").click();
  const backupDownload=page.waitForEvent("download");await page.locator('a[href="/api/backups/export"]').click();const backup=await backupDownload;
  const backupPath=path.join(output,"document-backup.json");await backup.saveAs(backupPath);
  const packageData=JSON.parse(fs.readFileSync(backupPath,"utf8"));assert.equal(packageData.projects[0].paper_documents.length,3);
  await restored.goto(target.base);await restored.waitForFunction(()=>!document.querySelector("#intake-model-notice").textContent.includes("正在读取"));
  await restored.locator('[data-view="projects"]').first().click();await restored.locator(".project-data-tools>summary").click();await restored.locator("#backup-file").setInputFiles(backupPath);
  await restored.waitForFunction(()=>document.querySelector("#backup-feedback")?.textContent.includes("恢复 1"));
  const recovered=await(await restored.request.get(target.base+"/api/projects/"+id)).json();assert.equal(recovered.paper_documents[0].original_available,false);
  assert.deepEqual(recovered.reading_notes[0].document_anchor,note.document_anchor);assert.equal((await restored.request.get(target.base+`/api/projects/${id}/documents/${txt.id}/original`)).status(),404);
  await restored.locator('#project-results [data-action="open"]').first().click();await restored.locator(".research-journey").waitFor();await restored.locator('[data-view="literature"]').first().click();
  await restored.locator('.reading-note [data-action="document-anchor-open"]').first().click();assert.equal(await restored.locator("mark.document-exact-highlight").textContent(),quote);
  assert.ok((await restored.locator(".document-reader-caution").textContent()).includes("快照"));checks.push("restored text keeps exact excerpt navigation while original is explicitly unavailable");
  audit=await(await page.request.get(source.base+"/__research_qa__")).json();assert.equal(audit.model_operations,1);assert.equal(audit.model_network_calls,0);
  const restoredAudit=await(await restored.request.get(target.base+"/__research_qa__")).json();assert.equal(restoredAudit.model_operations,0);assert.deepEqual(errors,[]);checks.push("one explicitly requested synthetic revision; restore never executes tools; no browser errors");
  const result={status:"passed",fixture:"synthetic_offline_two_databases",checks,audit,restoredAudit};fs.writeFileSync(path.join(output,"validation.json"),JSON.stringify(result,null,2));
  console.log(JSON.stringify({status:"passed",output,checks:checks.length}));
}
run().catch(async error=>{fs.writeFileSync(path.join(output,"failure.txt"),error.stack);console.error(error.stack);process.exitCode=1;if(browser)for(const [i,page] of browser.contexts().flatMap(context=>context.pages()).entries())await page.screenshot({path:path.join(output,`failure-${i}.png`)}).catch(()=>{});})
  .finally(async()=>{if(browser)await browser.close();for(const server of servers){server.child.kill();fs.writeFileSync(path.join(server.folder,"server.log"),server.logs);}});
