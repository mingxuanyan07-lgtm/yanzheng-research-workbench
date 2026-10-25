"""Build auditable public-paper cases in an isolated DB, with real providers only.

No human reading/selection is asserted by prepare. Human confirmation is a separate
explicit step; network requests and model responses are retained, never substituted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/public-cases/20261023"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["YANZHENG_DB_PATH"] = str(OUT / "cases.db")
sys.path.insert(0, str(ROOT / "webapp"))

import httpx
from fastapi.testclient import TestClient
from app import create_app
from model_service import ModelService, ModelServiceError


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


class AuditedRealModel:
    """Capture the actual public-case messages; no transport/config override."""
    def __init__(self):
        self.service = ModelService()

    def status(self):
        return self.service.status()

    def record_result(self, record):
        return self.service.record_result(record)

    def complete(self, messages):
        try:
            result, record = self.service.complete(messages, capture_response=True)
        except ModelServiceError as error:
            capture = error.record.pop("_response_capture", None)
            save("model-audit/" + error.record["id"] + ".json", {
                "recorded_at": stamp(), "messages": messages,
                "call": error.record, "response_capture": capture,
                "kind": "actual_provider_call_failure", "human_review": "pending"})
            raise
        capture = record.pop("_response_capture", None)
        save("model-audit/" + record["id"] + ".json", {
            "recorded_at": stamp(), "messages": messages, "call": record,
            "response_capture": capture, "parsed_response": result,
            "kind": "actual_provider_call", "human_review": "pending"})
        return result, record


CASES = [
    {"key": "A", "reference": "arxiv:0909.4061v2", "file": "0909.4061v2.pdf",
     "url": "https://arxiv.org/pdf/0909.4061v2", "page": 26,
     "quote_start": "recommended implementation appears as Algorithm 4.4.", "quote_end": "recommended implementation appears as Algorithm 4.4.",
     "interpretation": "只核对相关正文：§4.5 Remark 4.3 指出，在交替乘 A 和转置时进行正交化有助于避免舍入误差；Algorithm 4.4（PDF 第27页）提供可复现步骤。这支撑实现与稳定性检查，不证明在我的硬件上更快，也不证明课题创新。",
     "idea": "公开案例 A：一周内在普通笔记本 CPU 上复现随机 SVD，比较 q=0/1/2 时的误差、耗时与矩阵访问代价，形成可执行计划。",
     "context": "这是基于真实公开论文的教学复现案例，不宣称方法创新。预算：普通 Windows 笔记本，无 GPU，无外部私有数据，一周；使用 Python/NumPy；矩阵由固定随机种子生成。先用 256x192 冒烟，再用 512x384，目标秩 k=10/20，过采样 p=10，q=0/1/2，指数、缓慢衰减、平台尾部三类谱，每组种子0-4。以 float64 精确 SVD 截断为误差参考，计时含 QR 和压缩 SVD，矩阵生成和参考 SVD 单独报告。按相同误差阈值比较耗时，并另外报告相同矩阵访问预算的结果，不能把不同 q 说成同预算。首日先检查 Q 正交性和残差计算；对重复奇异值只比较子空间/重构，不逐向量对齐。输出应是可执行计划，不能声称已跑完正式实验。请给2个边界清楚的候选，推荐首先完成的复现基线；计划须含指标、成功与失败条件。"},
    {"key": "B", "reference": "arxiv:1504.05477v4", "file": "1504.05477v4.pdf",
     "url": "https://arxiv.org/pdf/1504.05477v4", "page": 3,
     "quote_start": "norm low-rank approximation error does not guarantee", "quote_end": "many matrices",
     "interpretation": "只核对 §2.2（PDF 第3页及第4页图1）：谱范数低秩近似误差不能单独保证每个主成分捕获的方差。原文给出谱平台情形与真实数据示例，并另定义 per-vector error。因此放弃“仅凭谱范数残差通过就验收 PCA”的推断，收窄为检查逐分量方差/重构/谱间隙的指标协议；不宣称随机 SVD 不可用，也不把重复特征值下方向不唯一判为算法错误。",
     "idea": "公开案例 B：能否只凭随机 SVD 的谱范数重构误差接近最优，就确认 PCA 特征提取质量可靠？若证据不够，应收窄或放弃原判断。",
     "context": "这是公开论文驱动的反向案例，不预设算法失败，也不宣称新发现。资源：CPU 笔记本，一周，无私有数据。希望研究的是 PCA 的质量验收；现有初始判断只看谱范数残差，没有逐分量方差或谱间隙条件，证据缺口必须显式保留。建议暂停“残差近优推出 PCA 质量”的原目标，候选一改成指标/反例复现协议，候选二在补充分量方差和谱间隙前仅保留低秩重构质量验收。不要说论文没有 PCA 保证，作者恰好另外给出 per-vector 保证；我们的原判断缺少这些条件。不得声称已验证所有数据集或否定该算法；新颖性待核对。"}
]


def api(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:700]}")
    return response.json()


def prepare(call_models=False):
    model = AuditedRealModel()
    if not model.status()["configured"]:
        raise RuntimeError("A real model must be configured; no fallback is permitted")
    app = create_app(OUT / "cases.db", model_client=model)
    index = read("case-index.json") if (OUT / "case-index.json").exists() else {}
    with TestClient(app) as client:
        for case in CASES:
            key = case["key"]
            entry = index.setdefault(key, {})
            if not entry.get("project_id"):
                project = api(client, "POST", "/api/projects", json={
                    "initial_idea": case["idea"], "context": case["context"],
                    "goal": "comparison", "resources": "laptop", "time_horizon": "week",
                    "planning_mode": "demo"})
                entry["project_id"] = project["id"]
                save("case-index.json", index)
            base = "/api/projects/" + entry["project_id"]
            if not entry.get("run_id"):
                imported = api(client, "POST", base + "/literature-import", json={"reference": case["reference"]})
                if imported["import_status"] != "success":
                    save(key + "/failed-metadata.json", imported)
                    raise RuntimeError("Official metadata import failed; retained without fake replacement")
                run = next(r for r in imported["project"]["literature_searches"] if r["id"] == imported["run_id"])
                entry.update(run_id=run["id"], source_id=run["sources"][0]["id"])
                save(key + "/literature.json", run)
                save("case-index.json", index)
            source_path = OUT / "sources" / case["file"]
            source_path.parent.mkdir(parents=True, exist_ok=True)
            if not source_path.exists():
                with httpx.Client(timeout=40, follow_redirects=True) as net:
                    response = net.get(case["url"], headers={"User-Agent": "YanzhengPublicCases/1.0"})
                    response.raise_for_status()
                if not response.content.startswith(b"%PDF-"):
                    raise RuntimeError("Official download is not PDF")
                source_path.write_bytes(response.content)
                save("sources/" + case["file"] + ".retrieval.json", {"requested_url": case["url"],
                     "resolved_url": str(response.url), "retrieved_at": stamp(),
                     "sha256": hashlib.sha256(response.content).hexdigest(), "bytes": len(response.content)})
            if not entry.get("document_id"):
                project = api(client, "POST", base + "/documents", params={
                    "run_id": entry["run_id"], "source_id": entry["source_id"],
                    "filename": case["file"], "binding_confirmed": "true"},
                    content=source_path.read_bytes(), headers={"content-type": "application/pdf"})
                entry["document_id"] = project["paper_documents"][0]["id"]
                save("case-index.json", index)
            project = api(client, "GET", base)
            doc = next(d for d in project["paper_documents"] if d["id"] == entry["document_id"])
            page = doc["pages"][case["page"] - 1]
            start = page["text"].index(case["quote_start"])
            end = page["text"].index(case["quote_end"], start) + len(case["quote_end"])
            quote = page["text"][start:end]
            if len(quote.split()) > 25:
                # Keep an exact short excerpt; surrounding section is paraphrased.
                end = start + len(case["quote_start"])
                quote = page["text"][start:end]
            draft = {"run_id": entry["run_id"], "source_id": entry["source_id"],
                "level": "fulltext_checked", "content": case["interpretation"],
                "document_anchor": {"document_id": doc["id"], "page": case["page"],
                    "start": start, "end": end, "quote": quote,
                    "file_sha256": doc["file_sha256"], "page_sha256": page["text_sha256"]}}
            save(key + "/reading-note-DRAFT.json", draft)
            (OUT / key / "relevant-page.txt").write_text(page["text"], encoding="utf-8")
            if call_models and not entry.get("initial_call_done"):
                response = client.post(base + "/advance", json={"material": case["context"] +
                    "\n制作助手已查阅公开论文相关位置，但当前尚无用户确认阅读笔记。题录不可标 supports/limits，只作为背景；这些解释待人工审阅：" + case["interpretation"]})
                project = api(client, "GET", base)
                save(key + "/initial-project.json", project)
                entry["initial_call_done"] = True
                entry["initial_call_http_status"] = response.status_code
                save("case-index.json", index)
                if response.status_code >= 400:
                    print(json.dumps({"case": key, "model_failure": response.json()}, ensure_ascii=False), flush=True)
            proposal = project.get("workflow", {}).get("pending_proposal")
            print(json.dumps({"case": key, "project_id": project["id"], "quote": quote,
                "page": case["page"], "proposal_id": proposal and proposal["id"],
                "topics": proposal and [{"id": t["id"], "title": t["title"], "question": t["question"]} for t in proposal["payload"]["topics"]],
                "calls": [{k: c.get(k) for k in ("id", "status", "model", "request_id", "usage", "error_code")} for c in project.get("model_calls", [])]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "generate", "retry", "correct"])
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        confirmation = read("human-confirmation.json")
        if confirmation["answer"] != "确认 A 计划、B 收窄，按记录边界代录与录屏":
            raise RuntimeError("No matching human authorization; leaving proposals pending")
        model = AuditedRealModel()
        app = create_app(OUT / "cases.db", model_client=model)
        index = read("case-index.json")
        with TestClient(app) as client:
            for case in CASES:
                key = case["key"]
                entry = index[key]
                base = "/api/projects/" + entry["project_id"]
                if not entry.get("confirmed_note_id"):
                    draft = read(key + "/reading-note-DRAFT.json")
                    project = api(client, "POST", base + "/reading-notes", json={**draft, "confirmed": True})
                    entry["confirmed_note_id"] = project["reading_notes"][-1]["id"]
                    save("case-index.json", index)
                if entry.get("confirmed_call_done") and args.action not in ("retry", "correct"):
                    continue
                project = api(client, "GET", base)
                pending = project.get("workflow", {}).get("pending_proposal")
                corrections = ("前次模型草案虽通过结构校验，科研内容有错误，不能采用：一是不能要求真实近似残差接近零，须归一化对照精确截断SVD的非零最优残差；二是固定k/p且q不同时矩阵访问次数不同，不能称相同访问预算；三是不能凭未读内容断言论文未量化某硬件收益。请明确三个候选为可执行的q=0正确性基线、q=0/1/2误差-代价记录、按误差目标筛选配置。三者共用已确认正交化实现依据；请将引用claim严格限于此，而将参数网格和验收阈值明确写成我们预先设定的协议。误差和耗时如实记录即可交付，不设必须加速15%或必须失败的成功条件。Q正交性<1e-10；实测残差不得显著小于精确参考；残差比有限。矩阵访问代价用块乘2q+2次及块宽报告，不说三种q同预算。" if key == "A" else
                    "前次模型草案的per-vector公式错误、且预设必须发现算法失败，不能采用。制作助手已核对原文式(3)：逐分量误差比较的是每个向量捕获的方差，即 abs(u_i^T A A^T u_i - z_i^T A A^T z_i)，不是 Z^T u_i 的范数，不是夹角。这个公式补充来自制作助手对原文的核对；用户确认仅限已展示摘录及解释，不扩大成通读或已证明。不要生成向量内积或轻量谱估计的伪公式。请给3个候选：优先推荐有限范围的PCA验收指标协议，另给论文启发的小矩阵诊断、仅验收低秩重构的保守路径。原单指标PCA推断已由用户明确停止。成功是协议正确记录方差/残差及适用条件、能识别已给构造，不是必须发现某算法失败或编造阈值。重复特征值下不以逐向量夹角判错；参考数值完整SVD。不要在未计算时给出显著性/固定秒数/通用阈值结论。支持或限制claim严格限于已确认笔记的指标局限，不外推保证。")
                if pending:
                    if args.action != "correct":
                        continue
                    api(client, "POST", base + "/direction/proposals/" + pending["id"] + "/reject", json={"reason": "Codex 按用户确认的边界代审拒绝，非用户新增科学结论。" + corrections})
                    save(key + "/operator-review-" + pending["id"] + ".json", {
                        "reviewed_at": stamp(), "operator": "Codex delegated review", "proposal_id": pending["id"],
                        "human_authorization": "../human-confirmation.json", "decision": "reject_and_request_correction",
                        "reason": corrections})
                source_ref = "lit_" + entry["run_id"] + "_" + entry["source_id"]
                response = client.post(base + "/advance", json={"material": case["context"] +
                    "\n用户已在本对话明确确认：A计划、B收窄。制作助手代录，确认范围仅限所展示相关摘录及解释，不表示通读。请基于已确认笔记给3个不同候选，适当关联 supports/limits 的真实 note_id。所有自由文本绝不复写 DOI/arXiv编号/网址/方括号引用，引用只通过 evidence.source_id / note_id；本轮完整计划和选择仍待人工已确认边界复核。" +
                    "\n本项目唯一允许的 evidence.source_id 是 " + source_ref + "，不是 S001；唯一允许的 note_id 是 " + entry["confirmed_note_id"] + "。只能逐字复制。证据claim仅描述已确认笔记覆盖的稳定性实现或指标局限，不扩展至未读定理/实验。" +
                    ("\n制作助手的具体审阅反馈：" + corrections if args.action == "correct" else "")})
                project = api(client, "GET", base)
                entry["confirmed_call_done"] = True
                entry["confirmed_call_http_status"] = response.status_code
                save("case-index.json", index)
                save(key + "/proposed-project.json", project)
                proposal = project.get("workflow", {}).get("pending_proposal")
                print(json.dumps({"case": key, "status": response.status_code,
                    "error": response.json().get("detail"),
                    "proposal_id": proposal and proposal["id"],
                    "payload": proposal and proposal["payload"],
                    "calls": project.get("model_calls", [])}, ensure_ascii=False), flush=True)
