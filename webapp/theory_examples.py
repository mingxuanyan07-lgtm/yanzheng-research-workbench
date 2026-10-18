"""A curated public reliability example, never a live model/search result."""

import uuid

import engine
import theory


SOURCE_URL = "https://people.math.wisc.edu/~angenent/521.2017s/UniformConvergence.html"
SOURCE_TITLE = "Math 521 — Uniform Convergence"
SOURCE_VERSION = "2017 spring course notes; webpage has no numbered revision; checked 2026-10-16"


def create_example(name="uniform-continuity"):
    if name != "uniform-continuity":
        raise ValueError("没有这个公开案例。")
    project = engine.create_project({
        "initial_idea": "公开可靠性案例：连续函数列逐点收敛时，极限函数是否一定连续？",
        "goal": "theory", "resources": "laptop", "time_horizon": "week",
        "context": "使用公开已知结果检查缺失条件与修订流程；不是新定理、自动证明或本次实时文献检索。",
    }, auto_generate=False)
    project["title"] = "公开案例：逐点收敛与连续性"
    run_id = "provided-uniform-" + uuid.uuid4().hex
    source_id = "S001"
    stamp = engine.now()
    notes = [
        {"id": uuid.uuid4().hex, "run_id": run_id, "source_id": source_id,
         "level": "fulltext_checked", "content": "连续函数列在同一度量空间上一致收敛时，其极限函数连续；一致收敛中的指标上界需对所有自变量同时成立。",
         "locator": "Three consequences of uniform convergence — first theorem; Uniform convergence — definition",
         "confirmed": True, "origin": "curated_example", "checked_at": stamp,
         "scope_label": "开发者整理的公开案例阅读摘要，不代表用户阅读确认；未形式核验。"},
        {"id": uuid.uuid4().hex, "run_id": run_id, "source_id": source_id,
         "level": "fulltext_checked", "content": "在闭区间 [0,1] 上，连续函数 x^n 逐点趋向分段函数：x<1 时为零，x=1 时为一；极限在端点不连续，故逐点收敛条件不足。",
         "locator": "The problems with pointwise convergence — x^n example",
         "confirmed": True, "origin": "curated_example", "checked_at": stamp,
         "scope_label": "开发者整理的公开案例阅读摘要，不代表用户阅读确认；不是新发现的反例。"},
    ]
    project["example_provenance"] = {
        "id": name, "label": "公开已知结果的流程可靠性示例",
        "evidence_note_ids": [note["id"] for note in notes],
        "source_url": SOURCE_URL, "source_version": SOURCE_VERSION,
        "reviewed_on": "2026-10-16", "reviewed_scope": [note["locator"] for note in notes],
        "live_search_performed": False, "external_model_api_called": False,
        "formal_verification_performed": False,
        "note": "来源为预先核对的公开讲义；仅展示研究组织流程，不代表用户已阅读、模型实时生成或数学创新。",
    }
    project["literature_searches"] = [{
        "schema_version": "1.0", "record_type": "research_literature_search", "id": run_id,
        "created_at": stamp,
        "request": {"idea": project["intake_form"]["initial_idea"], "queries": [], "providers": [],
                    "year_from": None, "year_to": None, "max_results_per_query": 1},
        "provenance": {"generator": "curated_public_example", "live_search_performed": False,
                       "external_model_api_called": False, "new_experiments_executed": False},
        "search_log": [],
        "sources": [{"id": source_id, "title": SOURCE_TITLE,
                     "authors": ["UW–Madison Math 521 course notes"],
                     "year_or_version": SOURCE_VERSION, "url": SOURCE_URL,
                     "origin": "provided_material", "verification": "metadata_checked",
                     "locator": None, "checked_at": stamp,
                     "identifiers": {"doi": None, "arxiv_id": None, "openalex_id": None},
                     "abstract": None,
                     "access": {"is_open_access": None, "landing_page_url": SOURCE_URL, "pdf_url": None, "license": None},
                     "retrievals": []}],
        "evidence": [], "limitations": ["提供的公开案例资料，不是本次网页检索；正文阅读摘要另以 curated_example 记录。"],
        "next_action": "查看原始讲义与阅读范围，审阅示例提议，或另行发起真实检索。",
    }]
    project["reading_notes"] = notes
    card = {
        "title": "逐点收敛能否保持连续性？", "statement": "若连续函数列 f_n:[0,1]→ℝ 逐点收敛到 f，则 f 在 [0,1] 上连续。",
        "domain": "[0,1] 带通常距离；函数值属于实数；n 为正整数。",
        "definitions": ["逐点收敛：对每个 x 和 ε>0，可选依赖于 x、ε 的 N，使 n≥N 时 |f_n(x)−f(x)|<ε。"],
        "assumptions": ["每个 f_n 在 [0,1] 上连续。", "f_n 在 [0,1] 上逐点收敛到 f。"],
        "target": "核查原命题是否成立；若条件不足，记录反例并提出可审阅的修订。",
    }
    theory.save_card(project, card, expected_version=0)
    revised = card | {
        "title": "一致收敛条件下的连续性", "statement": "若连续函数列 f_n:[0,1]→ℝ 一致收敛到 f，则 f 在 [0,1] 上连续。",
        "definitions": card["definitions"] + ["一致收敛：对每个 ε>0，可选不依赖于 x 的 N，使所有 n≥N、x∈[0,1] 均满足 |f_n(x)−f(x)|<ε。"],
        "assumptions": ["每个 f_n 在 [0,1] 上连续。", "f_n 在 [0,1] 上一致收敛到 f。"],
        "target": "审阅 ε/3 证明路线，并解释原逐点收敛条件为什么不足。",
    }
    proposal = {
        "revised_card": revised,
        "revision_reason": "公开已知反例 x^n 排除了原来的普遍断言；预置提议加强为一致收敛，并给出可审阅的证明提纲。此处没有实时模型调用。",
        "proof_routes": [
            {"id": "epsilon_thirds", "title": "证明修订命题：ε/3 分解", "steps": [
                "任取 x₀∈[0,1] 和 ε>0。由一致收敛选择同一个 N，对所有 x 控制 |f_N(x)−f(x)|<ε/3。",
                "由固定函数 f_N 在 x₀ 的连续性，选 δ>0 控制 |f_N(x)−f_N(x₀)|<ε/3。",
                "将 |f(x)−f(x₀)| 分为两项近似误差和一项 f_N 的连续性误差，用三角不等式合并。"],
             "obligations": [
                 {"id": "uniform_index", "statement": "确认 N 只依赖 ε，不依赖 x。", "depends_on": []},
                 {"id": "continuous_index", "statement": "对选定的 f_N 在任意 x₀ 建立连续性控制。", "depends_on": ["uniform_index"]},
                 {"id": "triangle", "statement": "逐项检查三角不等式与任意 ε、x₀ 的量词。", "depends_on": ["uniform_index", "continuous_index"]}],
             "gaps": ["以上是自然语言证明提纲，各义务尚待研究者逐步审阅；本工作台未执行 Lean 核验。"],
             "evidence": [{"note_id": notes[0]["id"], "relation": "supports", "claim": "讲义给出一致极限保持连续性的已知定理及一致收敛定义；不构成本项目独立证明。"}]},
            {"id": "pointwise_counterexample", "title": "审查原命题：x^n 反例", "steps": [
                "对正整数 n，f_n(x)=x^n 在 [0,1] 上连续。",
                "固定 x<1，x^n→0；在 x=1，所有函数值均为 1，故点态极限在端点不连续。",
                "据此排除原命题，并区分反例检查与对修订命题的证明。"],
             "obligations": [
                 {"id": "pointwise_limit", "statement": "对每个 x∈[0,1] 分情况核对点态极限。", "depends_on": []},
                 {"id": "endpoint", "statement": "核对极限在 1 处相对于 [0,1] 的不连续性。", "depends_on": ["pointwise_limit"]}],
             "gaps": ["本例来源已定位，但极限与端点论证尚未在本工作台形式化；有限采样曲线不能替代这些论证。"],
             "evidence": [{"note_id": notes[1]["id"], "relation": "limits", "claim": "讲义中的 x^n 例子限制了原命题的逐点收敛假设。"}]},
        ],
        "search_leads": ["uniform limit theorem continuity epsilon thirds", "pointwise uniform convergence x^n counterexample"],
    }
    theory.store_proposal(project, proposal, None, source="curated_example")
    engine.add_event(project, "theory_example_created", "创建独立公开案例：资料与提议为预置示例；未检索、调用模型、替用户选题或执行形式核验。")
    return project
