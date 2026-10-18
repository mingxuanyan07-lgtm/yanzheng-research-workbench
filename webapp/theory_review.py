"""Local, append-only checking materials. No files, model calls or proof execution."""
import copy
import json
import re
import uuid
from datetime import datetime

import engine
import theory

PROPOSITIONS = {"P0", "P1", "P2", "OTHER", "H14", "HISTORICAL", "REVIEW_STATUS"}
CREATE_FIELDS = {"request_id", "expected_revision", "context_token", "target_key", "claim_id",
    "proposition", "card_proposition", "claim_text", "assumptions", "support_scope", "derivation",
    "record_origin", "source_reference", "note_id", "locator", "artifact"}
OPTIONAL_CREATE_FIELDS = {"target_scope", "case_key", "artifact_refs"}
CASE_FIELDS = {"request_id", "expected_revision", "case_id", "title", "source_version", "source_sha256", "source_reference"}
REVIEW_FIELDS = {"request_id", "expected_revision", "kind", "scope", "opinion", "reviewer", "confirm_record_only"}


class Conflict(ValueError):
    pass


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} 必须为 1–{limit} 字符的非空文字。")
    return value  # preserve exact submitted material, including whitespace


def _basis(project):
    state = project.get("theory") or {}
    return {"project_id": project["id"], "card_version": state.get("card_version", 0),
        "card": state.get("card"), "routes": state.get("proof_routes", []),
        "notes": theory._evidence_catalog(project), "direction_map": project.get("direction_map"),
        "example_provenance": project.get("example_provenance")}


def targets(project):
    state = project.get("theory") or {}
    cards = list(state.get("card_versions", [])) + [{"version": state.get("card_version", 0),
        "card": state.get("card"), "proof_routes": state.get("proof_routes", []),
        "accepted_proposal_id": state.get("accepted_proposal_id")}]
    result, card_targets = [], []
    for card in cards:
        if not card.get("card"):
            continue
        card_binding = {"project_id": project["id"], "target_scope": "card",
            "card_id": project["id"] + ":theory-card", "card_version": card["version"],
            "card_sha256": theory._hash(card["card"]), "route_id": None, "obligation_id": None,
            "accepted_proposal_id": card.get("accepted_proposal_id")}
        card_targets.append({**card_binding, "target_key": theory._hash(card_binding),
            "card": copy.deepcopy(card["card"]), "route": None, "obligation": None,
            "historical": card["version"] != state.get("card_version")})
        for route in card.get("proof_routes", []):
            for obligation in route.get("obligations", []):
                binding = {"project_id": project["id"], "card_id": project["id"] + ":theory-card",
                    "card_version": card["version"], "card_sha256": theory._hash(card["card"]),
                    "route_id": route["id"], "obligation_id": obligation["id"],
                    "accepted_proposal_id": card.get("accepted_proposal_id")}
                result.append({**binding, "target_scope": "obligation", "target_key": theory._hash(binding),
                    "card": copy.deepcopy(card["card"]), "route": copy.deepcopy(route),
                    "obligation": copy.deepcopy(obligation),
                    "historical": card["version"] != state.get("card_version")})
    for case in cases(project):
        binding = {"project_id": project["id"], "target_scope": "case", "case_key": case["case_key"],
            "case_id": case["case_id"], "card_id": None, "card_version": None, "card_sha256": None,
            "route_id": None, "obligation_id": None, "accepted_proposal_id": None}
        card_targets.append({**binding, "target_key": theory._hash(binding), "card": None,
            "route": None, "obligation": None, "case": case, "historical": case["historical"]})
    return result + card_targets


def cases(project):
    items = copy.deepcopy((project.get("theory") or {}).get("review_cases", []))
    latest = {c["case_id"]: c["case_key"] for c in items}
    for item in items:
        item["historical"] = latest[item["case_id"]] != item["case_key"]
    return items


def _submission_token(project):
    # Keep v1 tokens identical until a case is explicitly registered.
    registered = (project.get("theory") or {}).get("review_cases", [])
    return theory._hash({"research": _basis(project), "cases": registered}) if registered else theory._hash(_basis(project))


def register_case(project, body):
    _request(body, CASE_FIELDS)
    entries = (project.get("theory") or {}).get("review_cases", [])
    if _duplicate(entries, body):
        return project
    if body["expected_revision"] != len(entries):
        raise Conflict("案例登记列表已更新；草稿保留，请核对后显式同步。")
    _text(body["case_id"], "case_id", 80)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", body["case_id"]):
        raise ValueError("case_id 必须是小写字母开头的小写字母、数字或连字符。")
    for field, maximum in (("title", 200), ("source_version", 300), ("source_reference", 2000)):
        _text(body[field], field, maximum)
    _digest(body["source_sha256"])
    source = {k: copy.deepcopy(body[k]) for k in CASE_FIELDS - {"request_id", "expected_revision", "source_sha256"}}
    source["declared_source_sha256"] = body["source_sha256"]
    version = sum(c["case_id"] == body["case_id"] for c in entries) + 1
    entry = {**source, "project_id": project["id"], "version": version,
        "case_source_metadata_sha256": theory._hash(source), "request_id": body["request_id"],
        "request_sha256": theory._hash(body), "created_at": engine.now(),
        "origin": "researcher_declared", "verification": "unknown", "access": "not_connected"}
    entry["case_key"] = theory._hash({"project_id": project["id"], "version": version, "source": source})
    project.setdefault("theory", {}).setdefault("review_cases", []).append(entry)
    engine.add_event(project, "theory_case_registered", "登记本项目案例来源版本；不核验原件或数学，不创建命题或义务。")
    return project


def _digest(value):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value)):
        raise ValueError("字节 SHA256 声明不合法；未知请填 null。")


def _artifact(artifact):
    if artifact is None:
        return None
    if not isinstance(artifact, dict) or set(artifact) != {"artifact_id", "original_bytes_sha256", "acquired_at"}:
        raise ValueError("原件元数据字段不符合合同。")
    _text(artifact["artifact_id"], "artifact_id", 200)
    _digest(artifact["original_bytes_sha256"])
    if artifact["acquired_at"] is not None:
        try:
            dt = datetime.fromisoformat(artifact["acquired_at"].replace("Z", "+00:00"))
            if not dt.tzinfo:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("取得时间必须包含时区；未知请填 null。") from None
    return {"artifact_id": artifact["artifact_id"], "declared_original_bytes_sha256": artifact["original_bytes_sha256"],
        "declared_acquired_at": artifact["acquired_at"], "verification": "unknown", "access": "not_connected"}


def record(project, record_id):
    return next((r for r in (project.get("theory") or {}).get("checking_records", [])
                 if r["id"] == record_id and r["project_id"] == project["id"]), None)


def artifact_declarations(item, artifact_id):
    material = item["material"]
    return copy.deepcopy([entry for entry in [material.get("artifact"), *material.get("artifact_refs", [])]
                          if entry and entry.get("artifact_id") == artifact_id])


def append_artifact_read(project, record_id, fact):
    item = record(project, record_id)
    if item is None or not artifact_declarations(item, fact["artifact_id"]):
        raise Conflict("核查材料或原件关联已经变化，读取事实未保存。")
    item.setdefault("artifact_reads", []).append(copy.deepcopy(fact))
    engine.add_event(project, "theory_artifact_read", "追加原件读取事实；不更改来源声明、审阅或数学状态。")
    return project


def record_view(project, item):
    result = copy.deepcopy(item)
    current = _basis(project)
    stale = []
    scope = item["target"].get("target_scope", "obligation")
    result["target"]["target_scope"] = scope
    if scope != "case" and (item["target"]["card_version"] != current["card_version"] or item["target"]["card_sha256"] != theory._hash(current["card"])):
        stale.append("card_changed")
    if theory._hash(current) != item["context_sha256"]:
        stale.append("evidence_or_research_context_changed")
    case = item.get("case_snapshot")
    if case:
        current_case = next((c for c in reversed(cases(project)) if c["case_id"] == case["case_id"]), None)
        if not current_case or current_case["case_key"] != case["case_key"]:
            stale.append("case_source_changed")
    result["stale_reasons"] = stale
    result["human_review_status"] = "recorded_unverified" if any(r["kind"] == "human_review" for r in item["reviews"]) else "pending"
    result["formal_status"] = "not_formally_verified"
    result["independent_mathematical_review"] = "pending"
    result["obligation_status"] = "open" if scope == "obligation" else "not_applicable"
    return result


def workspace(project):
    state = project.get("theory") or {}
    return {"schema_version": "theory-checking-v2", "supported_contracts": ["theory-checking-v1", "theory-checking-v2"],
        "cases": cases(project), "case_revision": len(state.get("review_cases", [])), "context_token": _submission_token(project),
        "revision": len(state.get("checking_records", [])), "targets": targets(project),
        "notes": theory._evidence_catalog(project),
        "records": [record_view(project, r) for r in state.get("checking_records", [])],
        "model_scope": "核查材料、审阅与原件读取事实仅保存在本地，不进入下一轮模型消息。"}


def _request(body, fields, optional=frozenset()):
    if not isinstance(body, dict) or not fields <= set(body) <= fields | optional:
        raise ValueError("核查请求字段不符合合同。")
    _text(body["request_id"], "request_id", 80)
    if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
        raise ValueError("expected_revision 必须为非负整数。")


def _duplicate(items, body):
    prior = next((r for r in items if r["request_id"] == body["request_id"]), None)
    if prior:
        if prior["request_sha256"] != theory._hash(body):
            raise Conflict("同一提交标识不能替换已保存材料或意见。")
        return True
    return False


def save(project, body):
    _request(body, CREATE_FIELDS, OPTIONAL_CREATE_FIELDS)
    existing = (project.get("theory") or {}).get("checking_records", [])
    if _duplicate(existing, body):
        return project
    view = workspace(project)
    if body["expected_revision"] != view["revision"] or body["context_token"] != view["context_token"]:
        raise Conflict("核查上下文或材料列表已更新；请保留草稿，核对后显式同步版本。")
    target = next((t for t in view["targets"] if t["target_key"] == body["target_key"]), None)
    if target is None:
        raise ValueError("本项目不存在该案例/卡/路线/义务关联。")
    scope = target["target_scope"]
    if body.get("target_scope") is not None and body["target_scope"] != scope:
        raise ValueError("target_scope 与本项目实际目标不一致。")
    case_key = body.get("case_key") or target.get("case_key")
    if scope == "case" and case_key != target["case_key"]:
        raise ValueError("案例目标和来源登记版本不一致。")
    case = next((c for c in view["cases"] if c["case_key"] == case_key), None) if case_key else None
    if case_key and case is None:
        raise ValueError("本项目没有该案例来源登记版本，不能跨项目关联。")
    for key, limit in {"claim_id": 80, "claim_text": 8000, "assumptions": 4000,
            "support_scope": 4000, "derivation": 16000, "source_reference": 2000, "locator": 2000}.items():
        _text(body[key], key, limit)
    if (body["proposition"] not in PROPOSITIONS or
            (scope == "case" and body["card_proposition"] is not None) or
            (scope != "case" and body["card_proposition"] not in PROPOSITIONS)):
        raise ValueError("请选择明确的论证对象类别。")
    if body["record_origin"] not in ("researcher_draft", "assistant_check", "external_material"):
        raise ValueError("记录来源不支持。")
    note = None
    if body["note_id"]:
        note = next((n for n in view["notes"] if n["note_id"] == body["note_id"]), None)
        if note is None:
            raise ValueError("本项目不存在该有效确认笔记；不能借用其他项目笔记。")
    elif body["note_id"] is not None:
        raise ValueError("没有笔记时 note_id 应为 null。")
    artifact = _artifact(body["artifact"])
    refs = body.get("artifact_refs")
    cleaned_refs = []
    if refs is not None:
        if not isinstance(refs, list) or len(refs) > 12:
            raise ValueError("artifact_refs 最多12项。")
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"artifact_id", "original_bytes_sha256", "acquired_at", "label", "source_version", "locator", "support_scope", "origin"}:
                raise ValueError("来源引用字段不符合合同。")
            base = _artifact({k: ref[k] for k in ("artifact_id", "original_bytes_sha256", "acquired_at")})
            for key, limit in (("label", 300), ("source_version", 300), ("locator", 2000), ("support_scope", 4000), ("origin", 200)):
                base[key] = _text(ref[key], key, limit)
            cleaned_refs.append(base)
    material = {k: copy.deepcopy(body[k]) for k in CREATE_FIELDS - {"request_id", "expected_revision", "context_token", "target_key", "artifact"}}
    material["artifact"] = artifact
    if "artifact_refs" in body:
        material["artifact_refs"] = cleaned_refs
    item = {"id": uuid.uuid4().hex, "project_id": project["id"], "request_id": body["request_id"],
        "request_sha256": theory._hash(body), "created_at": engine.now(), "target": target,
        "material": material, "material_sha256": theory._hash(material),
        "note_snapshot": copy.deepcopy(note), "note_sha256": note["note_sha256"] if note else None,
        "note_content_sha256": note["content_sha256"] if note else None,
        "source_metadata_sha256": note["source_sha256"] if note else None,
        "context_basis": copy.deepcopy(_basis(project)), "context_sha256": theory._hash(_basis(project)),
        "association_warning": "论证对象与所属卡标签不同；此材料不证明所属卡，请核查关联范围。" if scope != "case" and body["proposition"] != body["card_proposition"] else None,
        "reviews": [], "review_revision": 0, "formal_status": "not_formally_verified", "obligation_status": "open" if scope == "obligation" else "not_applicable"}
    if case:
        item["case_snapshot"] = copy.deepcopy(case)
    project.setdefault("theory", {}).setdefault("checking_records", []).append(item)
    engine.add_event(project, "theory_material_saved", "保存核查材料；原义务保持开放，未请求模型。")
    return project


def review(project, record_id, body):
    _request(body, REVIEW_FIELDS)
    item = record(project, record_id)
    if item is None:
        raise ValueError("本项目没有该核查材料。")
    if _duplicate(item["reviews"], body):
        return project
    if body["expected_revision"] != item["review_revision"]:
        raise Conflict("审阅已更新；请保留草稿，核对后显式同步版本。")
    if body["kind"] not in ("assistant_check", "human_review") or body["confirm_record_only"] is not True:
        raise ValueError("须确认仅记录意见，不认证身份或数学正确性。")
    for key, limit in (("scope", 4000), ("opinion", 8000), ("reviewer", 200)):
        _text(body[key], key, limit)
    item["reviews"].append({**copy.deepcopy(body), "request_sha256": theory._hash(body),
        "recorded_at": engine.now(), "identity_verified": False, "correctness_verified": False,
        "formal_status": "not_formally_verified"})
    item["review_revision"] += 1
    engine.add_event(project, "theory_material_reviewed", "追加审阅记录；不认证身份或正确性，不关闭证明义务。")
    return project


def case_markdown(items):
    if not items:
        return ""
    raw = json.dumps(items, ensure_ascii=False, indent=2).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return "\n## 案例来源登记历史（声明，不授予原件访问）\n\n```json\n" + raw + "\n```\n"


def markdown(items):
    if not items:
        return ""
    lines = ["", "## 本地核查材料与审阅", "", "仅记录；义务 open；not_formally_verified。原件声明保留，显式读取事实单独追加；人工身份未认证。"]
    for item in items:
        m, target, md = item["material"], item["target"], engine._markdown_text
        scope = target.get("target_scope", "obligation")
        lines += ["", "### " + md(m["claim_id"]), "", f"- 对象：{md(m['proposition'])}；目标层级：{md(scope)}"]
        if scope != "case":
            lines += [f"- 所属卡标签：{md(m['card_proposition'])}；卡 v{target['card_version']}"]
        if scope == "obligation":
            lines += [f"- 路线 / 义务：{md(target['route_id'])} / {md(target['obligation_id'])}"]
        if item.get("case_snapshot"):
            c = item["case_snapshot"]
            lines += [f"- 案例：{md(c['case_id'])}；源版本：{md(c['source_version'])}；声明源文件字节SHA256：{md(c['declared_source_sha256'] or 'unknown')}"]
        for key, label in (("claim_text", "论断"), ("assumptions", "假设"), ("support_scope", "支持范围"),
                ("source_reference", "来源版本"), ("locator", "定位"), ("derivation", "推导材料")):
            lines += ["", f"**{label}**：{md(m[key])}"]
        for review in item["reviews"]:
            lines += ["", f"审阅类型：{md(review['kind'])}；声明提供者：{md(review['reviewer'])}；身份未认证。",
                f"范围：{md(review['scope'])}", f"意见：{md(review['opinion'])}"]
        # JSON strings escape newlines, so user text cannot start a fence line.
        # Unicode escapes keep HTML inert and still round-trip through json.loads.
        raw = json.dumps(item, ensure_ascii=False, indent=2).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        lines += ["", "完整记录（JSON 可回读；含历史快照、来源与全部审阅）：", "", "```json", raw, "```"]
    return "\n".join(lines) + "\n"
