"""Bounded, fixed-code randomized SVD execution and independent run records.

The model never supplies Python, files, URLs, commands or execution facts.  The
only subprocess target is experiment_worker.py next to this module.
"""

import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(__file__).resolve().parent
WORKER = BASE / "experiment_worker.py"
MAX_RUNTIME_SECONDS = 20
MAX_OUTPUT_BYTES = 1_048_576
EXPERIMENT_VERSION = "randomized-svd-fixed-v1"
_RUN_LOCK = threading.Lock()
_DEFAULTS = {"rows": 120, "cols": 80, "rank": 8, "p_values": [4, 8, 16],
             "q_values": [0, 1], "repeats": 3, "data_seed": 20261016}
_THREAD_ENV = {key: "1" for key in (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "OMP_THREAD_LIMIT",
    "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")}
LIMITATIONS = [
    "仅执行固定的随机 SVD 与精确截断 SVD；不执行模型生成的代码，也不代表任意学科的实验平台。",
    "三类合成矩阵每类仅生成一个数据集；重复采样种子衡量固定矩阵上的随机性，不是独立数据集。",
    "小矩阵、单一数据种子和有限重复只能提供局部数值观察，不能据此宣称新算法、普遍优势或数学证明。",
    "耗时是真实本机测量，受系统调度影响；不同 p、q 的工作量不同，不是同时间或同访问预算的优势比较。",
    "矩阵访问预算只计逻辑上的稠密 A/A.T 乘法次数 2q+2；实际成本还取决于采样宽度 k+p。",
    "计时包含采样、分解及秩 k 重构，不包含矩阵构造、预热、误差计算和导出；精确基线先测量。",
]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def defaults():
    return copy.deepcopy(_DEFAULTS)


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low} 至 {high} 的整数。")
    return value


def validate_config(config):
    if not isinstance(config, dict) or set(config) - set(_DEFAULTS):
        raise ValueError("实验参数只允许 rows、cols、rank、p_values、q_values、repeats、data_seed；不接受代码或路径。")
    values = defaults()
    values.update(copy.deepcopy(config))
    _integer(values["rows"], "rows", 40, 240)
    _integer(values["cols"], "cols", 20, 160)
    _integer(values["rank"], "rank", 2, 32)
    dimension = min(values["rows"], values["cols"])
    if values["rank"] >= dimension:
        raise ValueError("目标秩 rank 必须小于矩阵的最小维数。")
    _integer(values["repeats"], "repeats", 1, 5)
    _integer(values["data_seed"], "data_seed", 0, 2**32 - 1)
    for key, maximum in (("p_values", min(64, dimension - values["rank"])), ("q_values", 2)):
        sequence = values[key]
        if not isinstance(sequence, list) or not 1 <= len(sequence) <= 3:
            raise ValueError(f"{key} 必须包含 1 至 3 个不重复整数。")
        for value in sequence:
            _integer(value, key, 0, maximum)
        if len(set(sequence)) != len(sequence):
            raise ValueError(f"{key} 中的参数不能重复。")
        values[key] = sorted(sequence)
    if 3 * len(values["p_values"]) * len(values["q_values"]) * values["repeats"] > 135:
        raise ValueError("单次实验最多执行 135 次随机 SVD。")
    return values


def _selected(project):
    record = project.get("direction_map", {})
    selected_id = record.get("selected_topic_id")
    if record.get("stage") != "topic_selected" or not selected_id:
        return None
    return next((topic for topic in record.get("topics", []) if topic.get("id") == selected_id), None)


def supported(project):
    topic = _selected(project)
    if topic is None:
        return {"supported": False, "reason": "请先由你明确选择一个候选课题；模型推荐不会自动成为选题。"}
    record = project.get("direction_map", {})
    form = project.get("intake_form", {})
    # Exclude all other model candidates: an unrelated recommendation must not
    # accidentally make a generic project eligible for a low-rank experiment.
    text = json.dumps({"initial_idea": form.get("initial_idea", record.get("initial_idea", "")),
                       "context": form.get("context", ""),
                       "answers": record.get("intake", {}).get("known", {}),
                       "selected_topic": topic}, ensure_ascii=False).lower()
    relevant = any(term in text for term in ("低秩", "low-rank", "low rank", "奇异值分解")) or bool(re.search(r"\bsvd\b", text))
    if not relevant:
        return {"supported": False, "reason": "首个执行器仅支持低秩／随机 SVD 课题；当前方向可继续查阅文献与修订计划。"}
    return {"supported": True, "reason": "可运行固定随机 SVD 对照实验；结果只检验这里的有限数值实例。"}


def _topic_hash(topic):
    return hashlib.sha256(json.dumps(topic, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _worker_environment():
    environment = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(part in upper for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
            continue
        if upper.startswith(("QWEN_", "DASHSCOPE_", "OPENAI_", "YANZHENG_MODEL_", "PYTHON")):
            continue
        environment[key] = value
    environment.update(_THREAD_ENV)
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _failure(record, code, message, *, unknown=False):
    record["status"] = "failed"
    record["finished_at"] = _now()
    record["error"] = {"code": code, "message": message}
    record["checks"]["passed"] = False
    record["provenance"]["execution_state"] = "unknown" if unknown else "confirmed_not_started"
    record["execution_state_unknown"] = unknown
    return record


def _finite(value):
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


def run(project, config):
    """Execute outside the SQLite transaction, returning a record on failure."""
    eligibility = supported(project)
    if not eligibility["supported"]:
        raise ValueError(eligibility["reason"])
    config = validate_config(config)
    topic = copy.deepcopy(_selected(project))
    stamp = _now()
    record = {
        "schema_version": "1.0", "record_type": "research_experiment_run",
        "id": uuid.uuid4().hex, "experiment_type": "randomized_svd", "version": EXPERIMENT_VERSION,
        "status": "failed", "created_at": stamp, "started_at": None, "finished_at": None,
        "config": config, "selected_topic_id": topic["id"], "selected_topic_snapshot": topic,
        "selected_topic_sha256": _topic_hash(topic),
        "provenance": {"new_experiments_executed": False, "execution_state": "confirmed_not_started",
                       "code_source": "fixed_local_worker", "external_model_api_called": False},
        "metrics": {"matrix_count": 0, "configuration_count": 0, "randomized_run_count": 0,
                    "baseline_run_count": 0},
        "checks": {"passed": False}, "datasets": [], "rows": [], "groups": [],
        "limitations": copy.deepcopy(LIMITATIONS), "environment": {}, "definitions": {},
    }
    if not _RUN_LOCK.acquire(blocking=False):
        return _failure(record, "busy", "另一个固定实验正在运行；本次未启动，请稍后重试。")
    try:
        record["started_at"] = _now()
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(WORKER)],
            input=json.dumps(config).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(BASE), env=_worker_environment(), shell=False, timeout=MAX_RUNTIME_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if len(completed.stdout) > MAX_OUTPUT_BYTES or len(completed.stderr) > MAX_OUTPUT_BYTES:
            return _failure(record, "output_limit", "实验输出超过上限；保留失败记录，无法确认完整执行结果。", unknown=True)
        if completed.returncode != 0:
            return _failure(record, "worker_failed", "固定实验进程未正常完成；未将部分输出认定为实验结果。", unknown=True)
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
            fields = {"status", "execution_started", "metrics", "checks", "datasets", "rows", "groups", "environment", "definitions"}
            if not isinstance(payload, dict) or not fields.issubset(payload) or payload["status"] not in ("success", "failed") or type(payload["execution_started"]) is not bool or not _finite(payload):
                raise ValueError()
            if len(payload["rows"]) > 135 or len(payload["datasets"]) > 3 or len(payload["groups"]) > 27:
                raise ValueError()
            if payload["status"] == "success" and (payload["checks"].get("passed") is not True or payload["execution_started"] is not True):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
            return _failure(record, "invalid_result", "实验输出未通过结构检查；保留失败记录，可手动重试。", unknown=True)
        for key in ("status", "metrics", "checks", "datasets", "rows", "groups", "environment", "definitions"):
            record[key] = payload[key]
        executed = payload["execution_started"]
        record["provenance"].update({"new_experiments_executed": executed,
                                        "execution_state": "confirmed_started" if executed else "confirmed_not_started"})
        record["provenance"]["worker_sha256"] = hashlib.sha256(WORKER.read_bytes()).hexdigest()
        record["finished_at"] = _now()
        if payload["status"] == "failed":
            code = payload.get("error", {}).get("code")
            messages = {"dependency_missing": "当前 Python 缺少 NumPy，实验未启动。", "numerical_check_failed": "数值正确性检查未通过；请检查原始记录，暂不解释为科学结论。", "invalid_config": "固定实验进程拒绝了参数。"}
            record["error"] = {"code": code if code in messages else "computation_failed",
                               "message": messages.get(code, "固定实验计算失败；保留运行记录，可手动重试。")}
        return record
    except subprocess.TimeoutExpired:
        return _failure(record, "timeout", "实验超过 20 秒已停止；执行状态未知，未将部分结果认定为成功。", unknown=True)
    except OSError:
        return _failure(record, "start_failed", "无法启动固定实验进程；本次没有确认执行。")
    finally:
        _RUN_LOCK.release()


def attach_result(project, record):
    """Keep the original topic context even if a user changed it while running."""
    import engine
    if any(previous.get("id") == record["id"] for previous in project.get("experiments", [])):
        raise ValueError("该实验记录已经保存。")
    saved = copy.deepcopy(record)
    topic = _selected(project)
    saved["attachment"] = {"selected_topic_matches_current": bool(topic and topic.get("id") == saved["selected_topic_id"] and _topic_hash(topic) == saved["selected_topic_sha256"])}
    project.setdefault("experiments", []).append(saved)
    metrics = saved.get("metrics", {})
    summary = (f"固定随机 SVD 实验完成：{metrics.get('matrix_count', 0)} 个合成矩阵、{metrics.get('randomized_run_count', 0)} 次随机运行；保留数值检查与限制。"
               if saved["status"] == "success" else "固定随机 SVD 实验失败：" + saved.get("error", {}).get("message", "未取得有效结果。"))
    if not saved["attachment"]["selected_topic_matches_current"]:
        summary += "运行期间选题已变化，结果仍关联启动时的课题快照。"
    engine.add_event(project, "experiment_completed" if saved["status"] == "success" else "experiment_failed", summary)
    # direction_map has its own Skill provenance.  Never rewrite its execution
    # fields: true execution facts live only in this independent run contract.


def export_csv(record):
    buffer = io.StringIO(newline="")
    fields = ["experiment_id", "dataset_id", "rows", "cols", "target_rank", "p", "q", "sampling_seed", "relative_error", "baseline_error", "baseline_error_ratio", "time_ms", "matrix_passes", "sample_width", "matrix_sha256"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    config = record.get("config", {})
    for row in record.get("rows", []):
        item = {key: row.get(key, "") for key in fields}
        item.update({"experiment_id": record["id"], "rows": config.get("rows", ""), "cols": config.get("cols", ""), "target_rank": config.get("rank", "")})
        writer.writerow(item)
    return buffer.getvalue()
