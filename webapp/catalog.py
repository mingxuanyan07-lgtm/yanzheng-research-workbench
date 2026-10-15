"""Read-only, allowlisted archive of pre-existing project artifacts."""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVED_LITERATURE = "output/literature-search-skill/cases/forward-trial/literature-search.json"
ARCHIVED_EXPERIMENT = "output/math-ai-workbench/experiment-data.json"
ARTIFACTS = {
    "experiment-data": ("微型随机 SVD 原始实验数据", ARCHIVED_EXPERIMENT),
    "literature-search": ("已保存的文献检索与内容核对记录", ARCHIVED_LITERATURE),
    "direction-handoff": ("文献向方向探索的交接记录", "output/literature-search-skill/cases/forward-trial/direction-handoff.json"),
    "research-direction-map": ("既有随机低秩课题地图", "output/math-ai-workbench/research-direction-map.json"),
    "case-architectures": ("优秀案例架构与技术复盘", "output/project-review/cases.json"),
}


class ArtifactNotFound(Exception):
    pass


def artifact_path(artifact_id):
    if artifact_id not in ARTIFACTS:
        raise ArtifactNotFound(artifact_id)
    path = (ROOT / ARTIFACTS[artifact_id][1]).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ArtifactNotFound(artifact_id)
    return path


def _read(artifact_id):
    return json.loads(artifact_path(artifact_id).read_text(encoding="utf-8-sig"))


def library():
    result = {
        "record_type": "existing_project_library",
        "provenance": {"new_search_performed": False, "new_experiments_executed": False},
        "literature": [], "experiment": None, "artifacts": [], "cases": [], "case_reviewed_on": None,
        "limitations": ["以下是此前项目保存的资料与实验，本轮只是读取档案；不代表本次项目已执行检索、阅读全文或实验。"],
    }
    for artifact_id, (title, relative_path) in ARTIFACTS.items():
        try:
            artifact_path(artifact_id)
        except ArtifactNotFound:
            result["limitations"].append("档案暂不可用：" + title)
            continue
        result["artifacts"].append({"id": artifact_id, "title": title,
                                    "relative_path": relative_path, "format": "json",
                                    "download_url": "/api/library/artifacts/" + artifact_id})
    try:
        cases = _read("case-architectures")
        result["cases"] = cases["cases"]
        result["case_reviewed_on"] = cases["reviewed_on"]
    except (ArtifactNotFound, ValueError, KeyError, TypeError):
        pass
    try:
        literature = _read("literature-search")
        for source in literature["sources"]:
            if source["verification"] not in ("abstract_checked", "fulltext_checked"):
                continue
            item = {key: copy.deepcopy(source.get(key)) for key in
                    ("id", "title", "authors", "year_or_version", "url", "verification", "checked_at", "locator")}
            item.update({"origin": "prior_project", "artifact_id": "literature-search", "relative_path": ARCHIVED_LITERATURE,
                         "evidence": [copy.deepcopy(evidence) for evidence in literature["evidence"] if evidence["source_id"] == source["id"]]})
            result["literature"].append(item)
    except (ArtifactNotFound, ValueError, KeyError, TypeError):
        result["limitations"].append("文献档案无法解析，未将缺失内容补成已读来源。")
    try:
        experiment = _read("experiment-data")
        metadata, datasets, groups = experiment["metadata"], experiment["datasets"], experiment["groups"]
        result["experiment"] = {
            "title": metadata["experiment_name"], "status": "archived",
            "created_at": metadata["created_at"], "matrix_count": len(datasets),
            "configuration_count": len(groups),
            "randomized_run_count": sum(len(group["individual_runs"]) for group in groups),
            "baseline_run_count": sum(len(dataset["baseline"]["individual_runs"]) for dataset in datasets),
            "shape": metadata["shape"], "target_rank": metadata["target_rank"],
            "matrices": [{key: dataset[key] for key in ("id", "name", "matrix_sha256")} for dataset in datasets],
            "artifact_id": "experiment-data", "relative_path": ARCHIVED_EXPERIMENT,
            "limitations": ["3 个固定合成矩阵，尺寸 120×80、目标秩 8；20 个采样种子不是 20 个独立数据集。",
                            "p、q 增加会增加计算量，误差下降不代表同预算优势。",
                            "计时为此前本机实测；有限实验不证明一般结论、普遍加速或新算法。"],
        }
    except (ArtifactNotFound, ValueError, KeyError, TypeError):
        result["limitations"].append("实验档案无法解析，未报告推测的运行次数。")
    return result
