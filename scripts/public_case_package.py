"""Export public cases and a reviewable report; never invent missing decisions."""
import hashlib
import html
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/public-cases/20261023"
os.environ["YANZHENG_DB_PATH"] = str(OUT / "cases.db")
sys.path.insert(0, str(ROOT / "webapp"))
import engine
import literature_service
from storage import Store

def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    index = json.loads((OUT / "case-index.json").read_text(encoding="utf-8"))
    human = json.loads((OUT / "human-confirmation.json").read_text(encoding="utf-8"))
    assert human["review_document_sha256"] == hashlib.sha256((OUT / "HUMAN-REVIEW.md").read_bytes()).hexdigest()
    store = Store(OUT / "cases.db")
    rows, calls, cards, checks = [], [], [], []
    esc = lambda value: html.escape(str(value))
    for key in ("A", "B"):
        project = store.get(index[key]["project_id"])
        assert engine.validate(project["direction_map"]) == []
        for note in project.get("reading_notes", []):
            literature_service.validate_document_anchor(project, note["run_id"], note["source_id"], note["document_anchor"])
        for run in project.get("literature_searches", []):
            errors = literature_service.validate(run)
            assert not errors, errors
        write(key + "/project.json", project)
        write(key + "/direction-map.json", project["direction_map"])
        selected = next((t for t in project["direction_map"]["topics"] if t["id"] == project["direction_map"]["selected_topic_id"]), None)
        if selected:
            notice = "> 导出说明：下文保留工作台原模型草案与选择记录。具体执行细节已由助手按作者确认边界细化并修正，正式90组协议以同目录 EXECUTION-PLAN.md 为准；草案中的45组计数等不作为最终协议。未完成正式实验。\n\n"
            (OUT / key / "decision-package.md").write_text(notice + engine.render_decision_package(project), encoding="utf-8")
            (OUT / key / "model-plan.md").write_text(notice + engine.render_plan(project), encoding="utf-8")
        elif key == "B":
            (OUT / key / "decision-package.md").write_text((OUT / key / "NARROWING-DECISION.md").read_text(encoding="utf-8") +
                "\n\n## 工作台状态说明\n\n原判断已由作者确认停止，新的模型候选未获选择。项目中保留初始本地模板及全部被拒模型草案作为操作历史；这些候选不能当作已通过的研究结论。收窄后的人工协议以本文件为准。笔记和实际调用可在 project.json 中复核。\n", encoding="utf-8")
        pending = project.get("workflow", {}).get("pending_proposal")
        shown = selected or (pending and pending["payload"]["topics"][0])
        note = project["reading_notes"][0]
        anchor = note["document_anchor"]
        doc = project["paper_documents"][0]
        file = OUT / "sources" / doc["filename"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == anchor["file_sha256"]
        checks.append({"case": key, "project_id": project["id"], "source_anchor_valid": True,
            "official_pdf_hash_matches_import": True, "reading_scope": human["reading_scope"],
            "selected_topic_id": selected and selected["id"], "stage": project["stage"]})
        for call in project.get("model_calls", []):
            audit_path = OUT / "model-audit" / (call["id"] + ".json")
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            assert audit["call"]["request_id"] == call["request_id"]
            assert audit["call"]["usage"] == call["usage"]
            # Keep provider success and application validation/review distinct.
            audit["application_outcome"] = call
            audit["human_review"] = "delegated within recorded human authorization"
            write("model-audit/" + call["id"] + ".json", audit)
            calls.append({"case": key, **call})
        result = "形成可执行计划" if key == "A" else "停止原推断，收窄目标"
        detail = "一周 · CPU · 三类谱 · 90组正式协议 · 6组冒烟已过" if key == "A" else "谱残差 ≠ PCA分量质量 · 补充分量方差与适用条件"
        target = "A/EXECUTION-PLAN.md" if key == "A" else "B/NARROWING-DECISION.md"
        status_label = '网页已代录选择' if selected else ('作者已确认收窄 · 未选择新模型候选' if key == 'B' and not pending else '人工已确认边界 · 网页草案待应用')
        shown_title = shown['title'] if shown else ('人工协议：补充分量方差与适用条件；有误模型草案不采用' if key == 'B' else '等待真实模型输出')
        protocol = ""
        if key == "A":
            plan_text = (OUT / target).read_text(encoding="utf-8")
            plan_rows = [line.strip().strip("|").split("|") for line in plan_text.splitlines() if line.startswith("|") and not line.startswith("|---")]
            table = "".join("<tr>" + "".join(f"<td>{esc(cell.strip())}</td>" for cell in row) + "</tr>" for row in plan_rows)
            protocol = f'''<div id="reviewed-plan-A"><h3>一周执行协议 · 作者确认边界内，由助手细化</h3><p>CPU / float64 / 单线程 · k=10/20，p=10，q=0/1/2，3类谱，5种子：正式90组，尚未执行。已完成6组冒烟。</p><p>工作台保留原模型草案；其中45组计数等细节按完整执行计划修正。以此协议为准，不能将草案阈值当成论文结论。</p><table>{table}</table><p>按同误差目标比较耗时，另报块乘代价。停止标准和限制见完整计划；不预设加速或创新。</p></div>'''
        cards.append(f'''<section id="case-{key}" class="case"><div class="label">PUBLIC CASE {key}</div><h2>{esc(result)}</h2><p class="lead">{esc(detail)}</p><div class="pill">{status_label}</div><h3>研究问题</h3><p>{esc(project['direction_map']['initial_idea'])}</p><h3>当前{'选择' if selected else '决定'}</h3><p>{esc(shown_title)}</p><h3>原文与解释</h3><p><a href="sources/{esc(doc['filename'])}#page={anchor['page']}">{esc(doc['filename'])} · PDF第{anchor['page']}页</a> · 字符 {anchor['start']}–{anchor['end']}</p><blockquote>{esc(anchor['quote'])}</blockquote><p>{esc(note['content'])}</p><details><summary>文件指纹与笔记绑定</summary><code>{esc(anchor['file_sha256'])}</code><p>笔记 {esc(note['id'])}；只确认相关摘录，不表示通读。</p></details>{protocol}<div class="links"><a href="{target}">完整{'执行计划' if key == 'A' else '收窄决定'}</a><a href="{key}/project.json">真实项目 JSON</a><a href="{key}/decision-package.md">导出决策包</a></div></section>''')
    write("validation.json", {"validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixture": False, "human_confirmation_present": True, "checks": checks,
        "scientific_scope": "公开论文驱动的实际操作案例；未完成正式实验、用户效果研究或创新性证明"})
    write("model-calls.json", calls)
    total_tokens = sum(c.get("usage", {}).get("total_tokens") or 0 for c in calls)
    success = sum(c["status"] == "success" for c in calls)
    call_rows = "".join(f'''<tr><td>{esc(c['case'])}</td><td>{esc(c['model'])}</td><td>{esc(c.get('application_status') or c.get('error_code') or c['status'])}</td><td>{esc(c['usage'].get('total_tokens'))}</td><td><a href="model-audit/{c['id']}.json"><code>{esc(c['request_id'])}</code></a></td></tr>''' for c in calls)
    proposals = [{"case": key, "id": p["id"], "status": p["status"], "reason": p.get("review_reason"),
                  "researcher_reason": p.get("researcher_reason"), "reviewed_at": p.get("reviewed_at")}
                 for key in ("A", "B") for p in store.get(index[key]["project_id"]).get("workflow", {}).get("proposals", [])]
    write("proposal-reviews.json", proposals)
    reviews_html = "".join(f"<li>{esc(p['case'])} · {esc(p['status'])} · {esc(p.get('researcher_reason') or p.get('reason') or '待确认')}</li>" for p in proposals)
    report = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>研证｜两份真实公开案例</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f5f0;color:#25372f;font:17px/1.75 "Microsoft YaHei",sans-serif}}main{{max-width:1180px;margin:auto;padding:60px 48px}}header{{padding:48px;background:#153d30;color:#fff;border-radius:24px;margin-bottom:28px}}.label{{font-size:13px;letter-spacing:2px;color:#68af91;font-weight:700}}header .label{{color:#b7dcc7}}h1{{font-size:44px;line-height:1.3;margin:14px 0 22px}}h2{{font-size:30px;margin:6px 0}}h3{{font-size:18px;margin:24px 0 6px}}p{{margin:8px 0}}.hero-summary,.lead{{font-size:20px}}.small{{font-size:14px;opacity:.8}}.case,.panel{{background:#fff;border:1px solid #dce4d9;border-radius:20px;padding:34px;margin:24px 0}}.pill{{display:inline-block;padding:5px 14px;background:#e7f2e7;border-radius:20px;font-size:14px}}a{{color:#276548;text-decoration:underline;text-underline-offset:4px}}header a{{color:#d2eed9}}blockquote{{margin:15px 0;padding:16px 22px;background:#f4f6f0;border-left:4px solid #70a97e;font:17px/1.6 Georgia,serif}}.links{{display:flex;gap:24px;flex-wrap:wrap;margin-top:22px}}code{{overflow-wrap:anywhere;font-size:12px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;border-bottom:1px solid #e3e8dd;padding:10px 8px;vertical-align:top}}nav{{display:flex;gap:24px}}li{{margin:12px 0}}@media(max-width:700px){{main{{padding:20px}}header,.case,.panel{{padding:24px}}h1{{font-size:32px}}table{{font-size:12px}}}}
</style><main><header id="overview"><div class="label">YANZHENG · REAL PUBLIC CASES</div><h1>让研究有据可查，也能及时止步</h1><p class="hero-summary">一份走向可执行计划，一份因证据边界而收窄。</p><p>真实公开论文 · 真实 qwen-plus 调用 · 项目作者明确决定</p><p class="small">基于公开论文制作的实际操作案例。正式科研实验、创新性与产品效果研究尚未完成。</p><nav><a href="#case-A">案例 A</a><a href="#case-B">案例 B</a><a href="#human">人工决定</a><a href="#audit">完整调用</a></nav></header>{''.join(cards)}
<section id="human" class="panel"><div class="label">HUMAN DECISION</div><h2>决定来自作者，操作由助手代录</h2><blockquote>{esc(human['answer'])}</blockquote><p>答复来源：本次 Codex 对话的确认请求。保存于 {esc(human['recorded_at'])}。</p><p>{esc(human['reading_scope'])}。模型草案与制作助手细化均单独标注。</p><div class="links"><a href="human-confirmation.json">原答复及授权范围</a><a href="HUMAN-REVIEW.md">当时审阅的完整内容</a></div></section>
<section id="review" class="panel"><div class="label">REVIEW BEFORE ACCEPTANCE</div><h2>结构通过，仍要核对科研内容</h2><p>保留了来源编号错误与内容审阅后的拒绝记录；不会把错误建议包装成有效结论。</p><ul>{reviews_html}</ul></section>
<section id="audit" class="panel"><div class="label">ACTUAL MODEL CALLS</div><h2>{len(calls)} 次真实调用，完整保留</h2><p>应用结构校验通过 {success} 次；其余请求亦有真实返回或失败记录。总 Token：{total_tokens:,}。下列状态表示该请求如何被应用，不把接口成功等同于科学正确。</p><table><thead><tr><th>案例</th><th>模型</th><th>应用状态</th><th>Token</th><th>实际 Request ID / 原请求响应</th></tr></thead><tbody>{call_rows}</tbody></table><div class="links"><a href="model-calls.json">完整调用清单</a><a href="proposal-reviews.json">草案审阅轨迹</a><a href="validation.json">证据绑定校验</a></div></section>
<section id="execution" class="panel"><div class="label">EXECUTABLE NEXT STEP</div><h2>下一步可以真实运行</h2><p>A：现有 NumPy 环境，6组冒烟已通过。正式协议为90组，尚未执行。</p><p>B：3×3辅助诊断已计算，证明指标可能遗漏方差信息，不声称具体算法会产生该输出。</p><div class="links"><a href="smoke/run.json">真实冒烟记录</a><a href="smoke/results.csv">6行原始结果</a><a href="smoke/B-diagnostic.json">辅助诊断</a></div></section><footer class="small">制作于2026-10-23 · 相关正文阅读范围随案例保留 · 没有发布或修改魔搭入口</footer></main></html>'''
    (OUT / "index.html").write_text(report, encoding="utf-8")
    print(json.dumps({"projects": checks, "calls": len(calls), "tokens": total_tokens}, ensure_ascii=False))

if __name__ == "__main__":
    main()
