"use strict";
// Record the owned real-case service. No route interception, fake model or fixture.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'output/public-cases/20261023');
const server=JSON.parse(fs.readFileSync(path.join(out,'SERVER.json'),'utf8'));
const index=JSON.parse(fs.readFileSync(path.join(out,'case-index.json'),'utf8'));
const preview=process.argv.includes('--preview');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const python=path.join(root,'webapp/.venv/Scripts/python.exe');
const actions=[],errors=[];let browser;
async function run(){
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true,
    ...(preview?{}:{recordVideo:{dir:path.join(out,'video/raw'),size:{width:1440,height:960}}})});
  await context.addInitScript(id=>{if(!localStorage.getItem('yanzheng.activeProject'))localStorage.setItem('yanzheng.activeProject',id);},index.A.project_id);
  context.setDefaultTimeout(15000);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const get=async key=>{const r=await page.request.get(server.base+'/api/projects/'+index[key].project_id);assert.equal(r.status(),200);return r.json();};
  const overlay=async(label,caption)=>page.evaluate(({label,caption})=>{
    let top=document.querySelector('#case-film-top');if(!top){top=document.createElement('div');top.id='case-film-top';top.style.cssText='position:fixed;left:0;right:0;top:0;z-index:2147483647;background:#153d30;color:#dbeee0;padding:8px 24px;font:14px Microsoft YaHei,sans-serif;pointer-events:none';document.body.append(top);}
    top.textContent='研证 · 真实公开案例 / '+label+' · 作者确认，助手代录 · 字幕演示';
    let bottom=document.querySelector('#case-film-caption');if(!bottom){bottom=document.createElement('div');bottom.id='case-film-caption';bottom.style.cssText='position:fixed;bottom:16px;left:70px;right:70px;z-index:2147483647;background:rgba(16,40,32,.95);border:1px solid #79ad90;border-radius:14px;color:white;padding:16px 24px;font:22px/1.6 Microsoft YaHei,sans-serif;text-align:center;box-shadow:0 8px 30px #0002;pointer-events:none';document.body.append(bottom);}
    bottom.textContent=caption;
  },{label,caption});
  const shot=async name=>page.screenshot({path:path.join(out,'video',name+'.png'),fullPage:false});
  const goto=async key=>{
    await page.goto(server.base);await page.locator('.research-journey').waitFor();
    await page.locator('[data-view="projects"]').click();
    await page.locator('[data-action="open"][data-project-id="'+index[key].project_id+'"]').click();
    await page.waitForFunction(id=>localStorage.getItem('yanzheng.activeProject')===id&&!document.querySelector('#project-content .loading-state'),index[key].project_id);
    await page.locator('.research-journey').waitFor();
  };
  const cite=async(key)=>{
    await page.locator('[data-view="literature"]').click();
    const button=page.locator('.reading-note [data-action="document-anchor-open"]').first();
    await button.scrollIntoViewIfNeeded();await button.click();await page.locator('mark.document-exact-highlight').first().waitFor();
    const draft=JSON.parse(fs.readFileSync(path.join(out,key,'reading-note-DRAFT.json'),'utf8'));
    assert.equal(await page.locator('mark.document-exact-highlight').first().textContent(),draft.document_anchor.quote,'The visible original belongs to this case');
    await page.locator('mark.document-exact-highlight').first().scrollIntoViewIfNeeded();
  };
  const accept=async key=>{
    const project=await get(key);if(project.direction_map.selected_topic_id)return project;
    if(project.workflow?.pending_proposal){
      const before=await get(key);assert.equal(before.workflow.pending_proposal.status,'pending');
      await page.locator('[data-action="direction-accept"]').scrollIntoViewIfNeeded();
      await page.locator('[data-action="direction-accept"]').click();
      await page.locator('[data-action="direction-accept"]').waitFor({state:'detached'});
      actions.push({case:key,action:'accept_via_UI',at:new Date().toISOString(),proposal_id:before.workflow.pending_proposal.id,operator:'Codex under recorded human authorization'});
    }
    return get(key);
  };
  const select=async(key,topicId,reason)=>{
    const current=await get(key);if(current.direction_map.selected_topic_id)return;
    await page.locator('.selection-reason').evaluate(e=>e.open=true);
    await page.locator('#selection-reason').fill(reason);
    const button=page.locator('[data-action="select"][data-topic-id="'+topicId+'"]');await button.scrollIntoViewIfNeeded();await button.click();
    await page.waitForFunction(()=>!!document.querySelector('[data-action="reopen"]'));
    assert.equal((await get(key)).direction_map.selected_topic_id,topicId);
    actions.push({case:key,action:'select_via_UI',topic_id:topicId,reason,at:new Date().toISOString(),operator:'Codex delegated data entry'});
  };
  fs.mkdirSync(path.join(out,'video'),{recursive:true});
  if(preview){
    await goto('A');await overlay('预览','真实论文与模型草案，尚未代录选择');await shot('preview-A');
    await cite('A');await overlay('预览','点击引用，返回真实导入正文的精确位置');await shot('preview-original');
    await goto('B');await cite('B');await shot('preview-B-original');
    await page.goto(server.base+'/showcase');await shot('preview-report');
    fs.writeFileSync(path.join(out,'video/preview.json'),JSON.stringify({errors,fixture:false},null,2));
    await context.close();await browser.close();return;
  }
  await page.goto(server.base+'/showcase');await overlay('开场','两份公开论文案例：形成可执行计划，也能依据证据及时收窄。');
  const started=Date.now();const at=async seconds=>{const remaining=started+seconds*1000-Date.now();if(remaining>0)await pause(remaining);};
  actions.push({action:'record_start',at:new Date().toISOString(),scope:'Saved real-call replay; actual delegated acceptance/selection where pending; no new model calls in recording'});
  await at(12);await goto('A');await overlay('案例 A','作者已确认一周 CPU 复现方向。模型提供候选；具体参数和结论边界仍须审阅。');
  await at(24);await accept('A');await overlay('案例 A','已拒绝过错误的残差与预算建议；本次采用边界明确的误差—代价对照。');
  await at(36);await cite('A');await overlay('案例 A · 原文','导入真实 PDF。点击摘录，定位到第 26 页，保留版本、文件哈希与字符位置。');await shot('A-original');
  await at(49);await page.locator('.reading-note').first().scrollIntoViewIfNeeded();await overlay('案例 A · 笔记','正文摘录与研究解释分别保存。论文支撑实现依据，本机收益和创新性仍待验证。');
  await at(62);await page.locator('[data-view="explore"]').click();
  const a=await get('A');const selectedA=a.direction_map.topics.find(t=>t.id==='q_variation')||a.direction_map.topics.find(t=>t.id.includes('variation'));
  assert.ok(selectedA,'Reviewed A comparison candidate exists');
  assert.ok((await page.locator('.sources-panel .source-excerpts').textContent()).includes('recommended implementation appears as Algorithm 4.4.'),'Saved excerpt is bound in direction sources');
  if(a.direction_map.selected_topic_id)await page.locator('.plan-panel').scrollIntoViewIfNeeded();
  if(!a.direction_map.selected_topic_id){const details=page.locator('.topic-card').filter({has:page.locator('[data-topic-id="'+selectedA.id+'"]')}).locator('.topic-details');if(await details.count())await details.evaluate(e=>e.open=true);await page.locator('[data-action="select"][data-topic-id="'+selectedA.id+'"]').scrollIntoViewIfNeeded();}
  await overlay('案例 A · 取舍','保持同一矩阵与秩，分别报告误差、耗时和块乘代价；不把不同迭代次数称为同预算。');await shot('A-candidate');
  await at(76);await select('A',selectedA.id,'依据本对话人工确认，Codex代录：选择一周CPU随机SVD误差—代价复现。论文仅支撑正交化实现，参数网格是自定协议；正式90组尚未执行，其他候选阈值未采用；详见A/EXECUTION-PLAN.md与human-confirmation.json。');
  await page.goto(server.base+'/showcase#reviewed-plan-A');await page.locator('#reviewed-plan-A').scrollIntoViewIfNeeded();
  await overlay('案例 A · 计划','计划给出数据、基线、指标、资源与停止标准。可运行脚本已过 6 组冒烟，正式实验待执行。');await shot('A-plan');
  await at(91);await goto('B');await overlay('案例 B','原问题：谱范数残差接近最优，是否足以验收 PCA 主成分质量？');
  await at(103);await cite('B');await overlay('案例 B · 证据边界','第 3 页 §2.2 指出指标覆盖的缺口，并另外定义逐分量误差；不能仅凭残差推断 PCA 质量。');await shot('B-original');
  await at(117);await page.locator('.reading-note').first().scrollIntoViewIfNeeded();await overlay('案例 B · 人工决定','作者确认停止单指标验收目标，收窄为补充分量方差与适用条件；不否定算法的全部用途。');
  await at(130);await page.locator('[data-view="explore"]').click();
  const b=await get('B');if(b.workflow?.pending_proposal){
    const reason='Codex依据作者已确认边界代审：停止单指标PCA验收目标。此草案仍把必须触发算法质量告警、并与原文图1一致设为成功条件；我们未复现图1真实数据实验，不能预设算法必然失败。暂不接受整份模型修订。收窄后的人工协议见B/NARROWING-DECISION.md；新模型候选未获选择，不伪称科研结论已验证。';
    await page.locator('#direction-review-reason').fill(reason).catch(async()=>{await page.locator('[data-direction-review-reason]').fill(reason);});
    await page.locator('[data-action="direction-reject"]').scrollIntoViewIfNeeded();await page.locator('[data-action="direction-reject"]').click();
    await page.locator('[data-action="direction-reject"]').waitFor({state:'detached'});
    const saved=await get('B');assert.equal(saved.workflow.proposals.find(p=>p.id===b.workflow.pending_proposal.id).researcher_reason,reason);
    actions.push({case:'B',action:'reject_via_UI',reason,proposal_id:b.workflow.pending_proposal.id,at:new Date().toISOString(),operator:'Codex delegated review'});
  } else {
    assert.equal(b.direction_map.selected_topic_id,null);
    assert.ok(b.workflow.proposals.some(p=>p.status==='rejected'&&p.researcher_reason?.includes('停止单指标PCA验收目标')));
    await page.goto(server.base+'/showcase#review');await page.locator('#review').scrollIntoViewIfNeeded();
  }
  await overlay('案例 B · 止步','收窄是有效结果。替代协议仍按人工边界完善，有误的模型草案保留并拒绝应用。');
  await at(143);await goto('B');await page.locator('[data-view="records"]').click();await page.locator('.model-call-panel').scrollIntoViewIfNeeded();await overlay('调用与审阅','真实模型请求保留 Token、时间与状态；结构校验通过，也不等于科研内容正确。');await shot('call-history');
  execFileSync(python,['-X','utf8','-B',path.join(root,'scripts/public_case_package.py')],{cwd:root,windowsHide:true,stdio:'pipe'});
  await at(155);await page.goto(server.base+'/showcase#human');await page.locator('#human').scrollIntoViewIfNeeded();await overlay('人工记录','原始答复、确认范围和助手代录身份一起保存；没有把自动建议冒充作者决定。');await shot('human-decision');
  await at(167);await page.locator('#audit').scrollIntoViewIfNeeded();await overlay('可复核交付','原始 PDF、精确定位、完整模型请求响应、人工决定与可执行脚本均可导出。');await shot('audit');
  await at(177);await page.locator('#overview').scrollIntoViewIfNeeded();await overlay('研证','从“给一个建议”，走向“有证据、有边界、有下一步”。');
  await at(182);actions.push({action:'record_end',at:new Date().toISOString()});
  const video=page.video();await context.close();const raw=await video.path();
  fs.writeFileSync(path.join(out,'video/recording.json'),JSON.stringify({raw,actions,errors,fixture:false,model_network_calls_during_recording:0,captioned:true,audio:false,elapsed_ms:Date.now()-started},null,2));
  await browser.close();assert.deepEqual(errors,[]);console.log(JSON.stringify({raw,actions:actions.length,errors}));
}
run().catch(async e=>{fs.mkdirSync(path.join(out,'video'),{recursive:true});fs.writeFileSync(path.join(out,'video/recording-error.json'),JSON.stringify({message:e.message,stack:e.stack,actions,errors},null,2));if(browser)await browser.close();console.error(e);process.exitCode=1;});
