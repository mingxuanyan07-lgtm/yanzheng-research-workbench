"""Local draft rules and plan rendering. This module makes no network calls."""

import copy
import importlib.util
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_validator_path = ROOT / "skills/research-direction-guide/scripts/validate_direction_map.py"
_spec = importlib.util.spec_from_file_location("direction_map_validator", _validator_path)
_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validator)
validate = _validator.validate

GOALS = {"experimental": "实验与复现", "theory": "理论与条件分析", "comparison": "方法比较"}
RESOURCES = {"laptop": "普通笔记本", "gpu": "可使用 GPU"}
HORIZONS = {"week": "一周起步", "month": "一个月", "flexible": "时间可调整"}
QUESTION_FIELDS = {
    "research_object": "research_object",
    "desired_result": "desired_result",
    "available_material": "available_material",
}
QUESTIONS = [
    {"id": "research_object", "question": "你希望研究哪个具体对象或场景？请举一个你关心的问题。", "why_it_matters": "明确对象后才能确定候选方向与检索关键词。"},
    {"id": "desired_result", "question": "第一轮你最想得到什么：可复现的现象、方法对比，还是一个可推导的命题？", "why_it_matters": "预期结果会影响首轮验证与候选排序。"},
    {"id": "available_material", "question": "你已有哪些数据、论文、代码或观察？暂时没有也可以说明。", "why_it_matters": "现有材料决定起步成本与需要补充的依据。"},
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def add_event(project, action, summary):
    stamp = now()
    project["updated_at"] = stamp
    project["history"].append({"id": uuid.uuid4().hex, "action": action, "summary": summary, "created_at": stamp})
    project["stage"] = project["direction_map"]["stage"]


def assert_valid(direction_map):
    errors = validate(direction_map)
    if errors:
        raise RuntimeError("课题地图未通过契约校验：" + "; ".join(errors[:4]))


def is_low_rank(project):
    known = project["direction_map"]["intake"]["known"]
    text = " ".join([project["intake_form"]["initial_idea"], project["intake_form"].get("context", ""), *[str(value) for key, value in known.items() if key in QUESTION_FIELDS.values()]])
    return any(term in text.lower() for term in ("随机低秩", "低秩逼近", "低秩近似", "随机 svd", "随机svd", "randomized svd", "randomized low-rank"))


def sufficiently_specific(form):
    text = form["initial_idea"] + " " + form.get("context", "")
    return len(text) >= 90 and any(term in text for term in ("准确率", "误差", "收敛", "鲁棒", "成本", "性能", "因果", "机制", "基线", "定理", "假设", "证明", "测试集"))


def create_project(form, auto_generate=True):
    stamp = now()
    known = {"goal": GOALS[form["goal"]], "resources": RESOURCES[form["resources"]], "time_horizon": HORIZONS[form["time_horizon"]]}
    if form.get("context"):
        known["provided_context"] = form["context"]
    direction_map = {
        "schema_version": "1.0", "record_type": "research_direction_map", "created_at": stamp,
        "stage": "clarifying", "initial_idea": form["initial_idea"],
        "intake": {"known": known, "assumptions": [], "unknowns": [q["question"] for q in QUESTIONS]},
        "questions": copy.deepcopy(QUESTIONS),
        "provenance": {"generator": "local_demo_rules", "live_search_performed": False, "external_model_api_called": False, "new_experiments_executed": False},
        "search_log": [], "sources": [], "topics": [],
        "recommendation": {"topic_id": None, "reason": "等待补充信息后比较方向；本地规则未调用千问。", "would_change_if": ["具体研究对象、材料或目标改变时重新比较。"]},
        "selected_topic_id": None, "next_action": "回答澄清问题，再查看本地示例候选。",
    }
    project = {"id": uuid.uuid4().hex, "title": re.sub(r"\s+", " ", form["initial_idea"])[:32], "created_at": stamp, "updated_at": stamp, "stage": "clarifying", "intake_form": form, "direction_map": direction_map, "history": [], "literature_searches": []}
    add_event(project, "created", "创建研究记录；本地规则模式，未调用外部模型。")
    if auto_generate and (is_low_rank(project) or sufficiently_specific(form)):
        direction_map["questions"] = []
        direction_map["intake"]["unknowns"] = ["具体数据规模、背景与每日可投入时间仍需在计划阶段确认。"]
        generate_directions(project)
    assert_valid(direction_map)
    return project


def answer_questions(project, answers):
    record = project["direction_map"]
    if record["stage"] != "clarifying":
        raise ValueError("当前研究已经进入候选阶段，无待回答的澄清问题。")
    allowed = {question["id"] for question in record["questions"]}
    if not answers or set(answers) - allowed:
        raise ValueError("回答包含未知或已回答的问题，请刷新后重试。")
    for key, answer in answers.items():
        record["intake"]["known"][QUESTION_FIELDS[key]] = answer
    record["questions"] = [question for question in record["questions"] if question["id"] not in answers]
    record["intake"]["unknowns"] = [question["question"] for question in record["questions"]]
    add_event(project, "answered", "保存澄清回答：" + "、".join(answers))
    if not record["questions"]:
        generate_directions(project)
    assert_valid(record)


def _seed_topics(project):
    seed_path = ROOT / "output/math-ai-workbench/research-direction-map.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8-sig"))
    sources, source_lookup, topics = [], {}, []
    for old_topic in seed["topics"]:
        evidence = []
        for reference in old_topic.get("references", []):
            if reference["url"] not in source_lookup:
                source_id = "prior_" + str(len(sources) + 1)
                source_lookup[reference["url"]] = source_id
                sources.append({
                    "id": source_id, "title": reference["title"], "authors": reference["authors"],
                    "year_or_version": reference["year"], "url": reference["url"], "origin": "prior_project",
                    "verification": "metadata_checked", "checked_at": reference.get("reviewed_on"),
                    "locator": "旧项目种子参考资料；旧记录未保存正文核查位置，本轮未重新检索或阅读。",
                })
            evidence.append({"source_id": source_lookup[reference["url"]], "relation": "background", "claim": "此前项目保存的相关题录，作为后续正文核查入口；本轮未读取内容，不能据此确认算法或误差界。"})
        topics.append({
            "id": old_topic["id"], "title": old_topic["title"], "question": old_topic["question"],
            "known_work": "此前项目的概括（待正文复核）：" + old_topic["known"],
            "potential_difference": old_topic["entry"], "novelty_status": "needs_review", "evidence": evidence,
            "first_validation": {"steps": ["核对种子论文正文中的方法、假设与评价指标；本轮未核对正文。", *old_topic["first"]], "baseline": "同一矩阵与目标秩下的精确截断 SVD；效率比较另列同时间或同访问预算。", "metrics": [part.strip("。 ") for part in old_topic["metrics"].split("、")], "success_signal": "首先复现可信基线，再观察跨独立种子、可比预算下稳定的条件性现象。"},
            "resources": [RESOURCES[project["intake_form"]["resources"]] + "；先用小规模合成矩阵进行正确性检查。", "计划时间：" + HORIZONS[project["intake_form"]["time_horizon"]] + "；实际实验未启动。"],
            "stop_conditions": [old_topic["criterion"], "若文献已解释该现象，记录为复现或修订问题，先不宣称创新。"],
            "risks": ["预先整理的样例不能替代当前文献检索；需核实适用条件。", "有限数值观察不能推出一般数学结论。"],
            "open_questions": [old_topic["open"]], "readiness": "provisional",
        })
    return sources, topics


def _generic_topics(project):
    record = project["direction_map"]
    known = record["intake"]["known"]
    subject = known.get("research_object", project["intake_form"]["initial_idea"])
    subject = re.sub(r"\s+", " ", subject)[:70]
    shared = [RESOURCES[project["intake_form"]["resources"]] + "；具体数据或理论材料需确认。", "计划时间：" + HORIZONS[project["intake_form"]["time_horizon"]] + "；先缩小为一个可核查实例。"]
    templates = [
        ("reproduce", "复现与诊断", f"围绕“{subject}”，可以先复现哪个已有结论，并定位结果对设置的敏感点？", ["先检索原始论文或官方方法资料，确认一个可复现对象。", "记录数据或例子的定义、版本、条件与结果检查方式。", "复现一个最小实例，核对结果与参考材料的差异。"], "所选资料中的原方法、结论或最小实例（尚待取得）。", ["复现一致性与失败原因", "时间及资源消耗"], "得到一个可追溯、可再次核查的最小复现记录。"),
        ("conditions", "边界与条件", f"关于“{subject}”，哪个关键条件改变时，已有结论可能失效或需要收窄？", ["从取得的原始资料写明定义域、量词、前提和结论。", "只改变一个前提，构造小例子或条件对照。", "区分数值现象、精确反例与证明目标，并检查已有解释。"], "原资料中满足前提的结论或实例（尚待核对）。", ["条件覆盖范围与反例可核查性", "未闭合的证明依赖"], "发现一个可重复的条件性差异或确认已有边界。"),
        ("compare", "公平方法对照", f"解决“{subject}”时，两条现有路线在相同目标和资源约束下有何取舍？", ["检索并选择解决同一问题的两条现有路线。", "统一输入、输出、评价指标和资源核算。", "先对照一个小规模实例，记录准确性、适用条件与成本。"], "两条已核对的现有方法；无法统一预算时分开报告。", ["统一目标下的结果质量", "耗时、资源与条件限制"], "形成明确的适用条件比较，而非未经验证的优劣排名。"),
    ]
    topics = []
    for topic_id, title, question, steps, baseline, metrics, success in templates:
        topics.append({
            "id": topic_id, "title": title + "：" + subject[:26], "question": question,
            "known_work": "当前没有为该想法执行文献检索；这是本地规则提供的暂定研究切口，尚未确定已有工作。",
            "potential_difference": "候选差异需在取得文献并验证后判断；暂不主张原创贡献。", "novelty_status": "needs_review", "evidence": [],
            "first_validation": {"steps": steps, "baseline": baseline, "metrics": metrics, "success_signal": success},
            "resources": shared[:], "stop_conditions": ["找不到可核查的资料、数据或明确定义时，暂停并收窄研究对象。", "差异来自不一致条件或额外资源时，撤回优势解释并重做对照。"],
            "risks": ["这是通用规划模板，须由千问和真实文献进一步具体化。", "当前未执行实验或数学证明。"],
            "open_questions": ["首轮可用的原始资料是什么？", "该切口是否已被已有工作覆盖？"], "readiness": "provisional",
        })
    return [], topics


def generate_directions(project):
    record = project["direction_map"]
    low_rank = is_low_rank(project)
    sources, topics = _seed_topics(project) if low_rank else _generic_topics(project)
    desired = {"experimental": "budget" if low_rank else "reproduce", "theory": "spectrum" if low_rank else "conditions", "comparison": "krylov" if low_rank else "compare"}[project["intake_form"]["goal"]]
    topics.sort(key=lambda topic: topic["id"] != desired)
    record.update({"stage": "directions_ready", "questions": [], "sources": sources, "topics": topics, "selected_topic_id": None, "next_action": "比较候选并由你选择；选题只保存计划，不自动执行实验。"})
    # Research assumptions describe the scientific problem, never service state.
    # This demo has not established any new research assumption.
    record["intake"]["assumptions"] = []
    query = "randomized low-rank approximation oversampling power iteration" if low_rank else record["initial_idea"][:160] + " baseline conditions comparison"
    record["search_log"] = [{"query": query, "searched_at": None, "outcome": "planned"}]
    record["recommendation"] = {"topic_id": desired, "reason": "按你选择的“" + GOALS[project["intake_form"]["goal"]] + "”目标排序。本地规则给出起步建议，等待真实文献与千问进一步核查。", "would_change_if": ["可用材料、研究目标或资源发生变化。", "真实文献表明已有工作覆盖该问题，或首轮验证无法成立。"]}
    add_event(project, "directions_generated", "生成 3 个暂定候选；" + ("使用此前随机低秩种子材料。" if low_rank else "使用通用规划模板，没有附加虚构文献。"))
    assert_valid(record)


def select_topic(project, topic_id, reason=""):
    record = project["direction_map"]
    topic = next((topic for topic in record["topics"] if topic["id"] == topic_id), None)
    if topic is None:
        raise ValueError("课题不存在或尚未生成，请刷新候选列表。")
    stamp = now()
    record["selected_topic_id"] = topic_id
    record["stage"] = "topic_selected"
    record["intake"]["known"]["selection_basis"] = f"用户通过网页点击选择：{topic['title']}；记录时间 {stamp}。"
    if reason:
        record["intake"]["known"]["selection_reason"] = reason
        record["intake"]["known"]["selection_basis"] += "选择理由：" + reason
    else:
        record["intake"]["known"].pop("selection_reason", None)
    record["next_action"] = "查看并导出研究计划草案；资料核查与实验执行仍需后续完成。"
    add_event(project, "topic_selected", "用户选择：" + topic["title"] + ("；理由：" + reason if reason else "；未填写额外选择理由。"))
    assert_valid(record)


def reopen_project(project):
    record = project["direction_map"]
    if not record["topics"]:
        raise ValueError("请先完成澄清，生成候选后再返回比较。")
    record["stage"] = "directions_ready"
    record["selected_topic_id"] = None
    record["intake"]["known"].pop("selection_basis", None)
    record["intake"]["known"].pop("selection_reason", None)
    record["next_action"] = "重新比较候选并选择方向；此前选择记录保存在历史中。"
    add_event(project, "reopened", "返回候选比较，清除当前选择并保留历史。")
    assert_valid(record)


def _markdown_text(value):
    # Export user text as plain Markdown text, never raw HTML or executable links.
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|", "!"):
        text = text.replace(character, "\\" + character)
    return text


def add_literature_search(project, record):
    # Keep retrieval IDs, failures and reading scope in their own contract.
    # Metadata never silently becomes support for any candidate direction.
    project.setdefault("literature_searches", []).append(copy.deepcopy(record))
    counts = {status: sum(log["status"] == status for log in record["search_log"])
              for status in ("success", "empty", "failed")}
    query = record["request"]["queries"][0]
    unknown = "执行状态未知，无法确认实际请求次数；" if record.get("execution_state_unknown") else ""
    add_event(project, "literature_searched", f"文献检索：{query}；成功 {counts['success']}、未命中 {counts['empty']}、失败 {counts['failed']}；{unknown}保存 {len(record['sources'])} 条题录，未自动提升阅读等级或改写候选依据。")


def literature_status(project):
    searches = project.get("literature_searches", [])
    if not searches:
        return "本项目尚未从网页发起文献检索。地图中的历史来源与查询计划不等于本次检索。"
    logs = [log for search in searches for log in search["search_log"]]
    attempts = sum(log["attempts"] for log in logs)
    unknown_count = sum(bool(search.get("execution_state_unknown")) for search in searches)
    counts = {status: sum(log["status"] == status for log in logs) for status in ("success", "empty", "failed")}
    execution = f"可确认请求 {attempts} 次，另有 {unknown_count} 轮执行状态未知" if unknown_count else f"实际请求 {attempts} 次"
    return f"本项目保存 {len(searches)} 轮检索记录，{execution}；成功查询 {counts['success']}、未命中 {counts['empty']}、失败 {counts['failed']}。检索题录独立保存，尚未自动成为课题科学论断的证据。"


def render_plan(project):
    record = project["direction_map"]
    topic = next((topic for topic in record["topics"] if topic["id"] == record["selected_topic_id"]), None)
    if topic is None:
        raise ValueError("请先选择课题，再导出研究计划。")
    provenance = record["provenance"]
    if provenance.get("generator") == "qwen":
        model_status = "课题候选由千问生成；实际模型：" + _markdown_text(provenance.get("model", "调用记录中待确认")) + "。生成仅提供暂定研究规划。"
    else:
        model_status = "课题地图记录实际调用过外部模型接口。" if provenance["external_model_api_called"] else "课题候选来自本地规则模板，未调用千问。"
    ran_experiments = any(run.get("provenance", {}).get("new_experiments_executed") for run in project.get("experiments", []))
    experiment_status = "项目已有独立实验运行记录，结果对应运行时的选题快照，并不验证所有候选；" if ran_experiments else "未执行新实验；"
    lines = ["# 研证首轮研究计划草案", "", "## " + _markdown_text(topic["title"]), "", model_status + experiment_status + "潜在贡献、适用条件与资料内容仍待研究者核查。", "", literature_status(project), "", "原始想法：" + _markdown_text(record["initial_idea"]), "", "研究问题：" + _markdown_text(topic["question"]), "", "选择依据：" + _markdown_text(record["intake"]["known"]["selection_basis"]), ""]
    first = topic["first_validation"]
    for heading, entries in [
        ("已有工作", [topic["known_work"]]), ("潜在切口", [topic["potential_difference"]]),
        ("首轮步骤", first["steps"]), ("基线", [first["baseline"]]), ("指标", first["metrics"]),
        ("成功信号", [first["success_signal"]]), ("资源与时间", topic["resources"]),
        ("停止条件", topic["stop_conditions"]), ("风险", topic["risks"]), ("待确认问题", topic["open_questions"]),
    ]:
        lines.extend(["## " + heading, ""])
        lines.extend("- " + _markdown_text(entry) for entry in entries)
        lines.append("")
    lines.extend(["## 来源状态", ""])
    source_ids = {evidence["source_id"] for evidence in topic["evidence"]}
    for source in record["sources"]:
        if source["id"] in source_ids:
            lines.append("- " + _markdown_text(source["title"]) + "；核查等级：" + _markdown_text(source["verification"]) + "；核查日期：" + _markdown_text(source.get("checked_at") or "未核实") + "；核查范围：" + _markdown_text(source.get("locator") or "尚未记录") + "；" + _markdown_text(source.get("url") or "尚无核实链接"))
    if not source_ids:
        lines.append("该方向尚未关联核实来源；题录或检索计划不能替代文献支持。")
    lines.extend(["", "## 课题与来源的关联", ""])
    lines.extend("- " + _markdown_text(evidence["source_id"]) + " · " + _markdown_text(evidence["relation"]) + "：" + _markdown_text(evidence["claim"]) for evidence in topic["evidence"])
    if not topic["evidence"]:
        lines.append("尚未建立该方向的文献支持。")
    lines.extend(["", "## 网页检索记录", ""])
    for search in project.get("literature_searches", []):
        for log in search["search_log"]:
            request_detail = "；可确认请求次数：" + str(log["attempts"]) + "；本轮执行状态未知" if search.get("execution_state_unknown") else "；请求次数：" + str(log["attempts"])
            lines.append("- " + _markdown_text(log["provider"]) + " · " + _markdown_text(log["query"]) + " · 状态：" + _markdown_text(log["status"]) + "；结果数：" + str(log["result_count"]) + request_detail + ("；原因：" + _markdown_text(log["error"]) if log["error"] else ""))
    if not project.get("literature_searches"):
        lines.append("暂无本项目网页检索记录。")
    if project.get("model_calls"):
        lines.extend(["", "## 模型调用事实", "", "以下为程序保存的调用事实；调用成功不等于研究结论成立。", ""])
        for call in project["model_calls"]:
            applied = call.get("application_status") or ("applied" if call.get("status") == "success" else "not_applied")
            lines.append("- 调用 " + _markdown_text(call.get("id", "未知")) + "；请求模型：" + _markdown_text(call.get("requested_model", "未知")) + "；记录模型：" + _markdown_text(call.get("model") or "未返回") + "；状态：" + _markdown_text(call.get("status", "未知")) + "；应用状态：" + _markdown_text(applied) + "；耗时：" + str(call.get("duration_ms", "未知")) + " ms；请求标识：" + _markdown_text(call.get("request_id") or "未返回") + "。")
            lines.append("  调用时间：" + _markdown_text(call.get("started_at", "未知")) + " 至 " + _markdown_text(call.get("finished_at", "未知")) + "；提示版本：" + _markdown_text(call.get("prompt_version", "未知")) + "。")
            usage = call.get("usage")
            if isinstance(usage, dict) and usage:
                token = lambda key: str(usage[key]) if usage.get(key) is not None else "未知"
                lines.append("  服务返回用量：输入 " + token("prompt_tokens") + "、输出 " + token("completion_tokens") + "、总计 " + token("total_tokens") + " tokens；不据此估算费用。")
            else:
                lines.append("  服务未返回可记录的 token 用量；不估算用量或费用。")
    if project.get("reading_notes"):
        lines.extend(["", "## 用户确认的阅读记录", ""])
        for note in project["reading_notes"]:
            lines.append("- 来源 " + _markdown_text(note["run_id"] + "/" + note["source_id"]) + "；等级：" + _markdown_text(note["level"]) + "；位置：" + _markdown_text(note["locator"]) + "；摘录/笔记：" + _markdown_text(note["content"]))
    if project.get("experiments"):
        lines.extend(["", "## 独立实验运行记录", "", "固定执行器的局部数值结果，不是数学证明；参数与选题以每轮快照为准。", ""])
        for run in project["experiments"]:
            metrics = run.get("metrics", {})
            lines.append("- 运行 " + _markdown_text(run["id"]) + "；状态：" + _markdown_text(run["status"]) + "；原选题：" + _markdown_text(run.get("selected_topic_snapshot", {}).get("title", "未记录")) + "；固定矩阵 " + str(metrics.get("matrix_count", 0)) + "；随机运行 " + str(metrics.get("randomized_run_count", 0)) + "；基线运行 " + str(metrics.get("baseline_run_count", 0)) + "。")
            lines.extend("  - " + _markdown_text(item) for item in run.get("limitations", []))
    lines.extend(["", "## 操作历史", ""])
    lines.extend("- " + event["created_at"] + " · " + _markdown_text(event["summary"]) for event in project["history"])
    return "\n".join(lines) + "\n"
