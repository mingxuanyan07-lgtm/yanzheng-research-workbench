"""Bounded public metadata searches; no model, credential or database access."""

import importlib.util
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills/literature-search/scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SKILL / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_client = _load("workbench_literature_client", "search_literature.py")
_validator = _load("workbench_literature_validator", "validate_literature_record.py")
run_search = _client.run_search
validate = _validator.validate
_arxiv_lock = threading.Lock()
_last_arxiv_finished = 0.0


def _failed(idea, query, provider, limit, message):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": "1.0", "record_type": "research_literature_search",
        "created_at": stamp,
        "request": {"idea": idea, "queries": [query], "providers": [provider],
                    "year_from": None, "year_to": None, "max_results_per_query": limit},
        "provenance": {"generator": "workbench/literature_service.py",
                       "live_search_performed": False, "external_model_api_called": False,
                       "new_experiments_executed": False},
        "search_log": [{"id": "Q001", "provider": provider, "query": query,
                        "status": "failed", "searched_at": None, "attempts": 0,
                        "result_count": 0, "error": message}],
        "sources": [], "evidence": [], "limitations": [message],
        "next_action": "查看失败说明后稍后重试；失败和未命中不能证明不存在相关研究。",
    }


def search(idea, query, provider="crossref", limit=5):
    """One provider/query, no retries. Runs outside SQLite write transactions."""
    global _last_arxiv_finished
    if provider not in ("crossref", "arxiv") or not 1 <= limit <= 5 or not 1 <= len(query.strip()) <= 300:
        raise ValueError("检索参数不正确，请检查关键词、提供方和条数。")
    acquired = provider != "arxiv" or _arxiv_lock.acquire(timeout=1)
    try:
        if not acquired:
            record = _failed(idea, query, provider, limit, "arXiv 检索正在进行，本次没有发出请求；请稍后重试。")
        else:
            if provider == "arxiv":
                # Shared across requests, stricter than three seconds between starts.
                time.sleep(max(0, 3 - (time.monotonic() - _last_arxiv_finished)))
            try:
                record = run_search(idea, [query], [provider], limit=limit,
                                    timeout=12, retries=0, mailto=None, api_key=None)
                if validate(record):
                    raise ValueError("invalid client record")
            except Exception:
                # Never expose traceback, environment values or raw provider errors.
                # Normal network errors are already tracked by the client's log.
                record = _failed(idea, query, provider, limit,
                                 "检索客户端未生成有效记录，无法确认请求执行状态；请稍后重试。")
                # The contract's zero attempts means no confirmed request log;
                # it cannot establish that the interrupted client sent nothing.
                record["execution_state_unknown"] = True
    finally:
        if provider == "arxiv" and acquired:
            _last_arxiv_finished = time.monotonic()
            _arxiv_lock.release()
    record["id"] = uuid.uuid4().hex
    if validate(record):
        raise ValueError("检索记录无法保存，请稍后重试。")
    return record
