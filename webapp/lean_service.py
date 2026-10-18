"""Optional fixed-case Lean adapter, intentionally independent of API and storage.

Public contract: list_cases(), environment(), validate_request(), run({case_id}).
No source, statement, import, executable, file path, or command is accepted.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import threading
import time
import uuid

import lean_worker


_RUN_LOCK = threading.Lock()
_worker_environment = lean_worker.clean_environment
STATUSES = {"passed", "error", "incomplete", "timeout", "unavailable"}
LIMITATIONS = [
    "Only reviewed fixed case IDs are accepted; no model-generated or user-supplied Lean is executed.",
    "Passed applies only to the recorded exact formal statement, not its equivalence to a natural-language problem.",
    "An error, incomplete proof, or timeout does not establish that the mathematical proposition is false.",
    "The pinned Lean kernel, bundled imported modules, local toolchain, Python adapter and audit code remain trusted; no independent leanchecker was run.",
    "Resource limits and credential removal are not a general security sandbox and do not isolate filesystem or network access.",
    "This adapter does not change theory cards, discharge obligations, select topics, or write the application database.",
]


def list_cases():
    return [{key: copy.deepcopy(case[key]) for key in ("case_id", "label", "target", "statement", "fixture_kind")}
            for case in lean_worker.load_cases().values()]


def validate_request(value):
    if not isinstance(value, dict) or set(value) != {"case_id"}:
        raise ValueError("Only a reviewed case_id is accepted; code, statements, paths and commands are forbidden.")
    case_id = value["case_id"]
    if not isinstance(case_id, str) or len(case_id) > 80 or case_id not in lean_worker.load_cases():
        raise ValueError("Unknown reviewed Lean case_id.")
    return {"case_id": case_id}


def environment():
    try:
        for case in lean_worker.load_cases().values():
            lean_worker.reviewed_sources(case)
        return lean_worker.probe_environment()
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False, "toolchain": lean_worker.TOOLCHAIN, "error_code": "reviewed_project_unavailable",
                "automatic_downloads": False, "mathlib_used": False}


def _invoke_worker(request, timeout_seconds):
    worker = Path(lean_worker.__file__).resolve()
    env = _worker_environment(lean_worker._lean_candidate())
    binary = lean_worker._lean_candidate()
    if binary:
        env["YANZHENG_LEAN_BINARY"] = str(binary)
    response = lean_worker.bounded_process(
        [sys.executable, "-I", "-B", str(worker)],
        input_bytes=json.dumps(dict(request, timeout_seconds=timeout_seconds), separators=(",", ":")).encode("utf-8"),
        cwd=worker.parent, env=env, timeout_seconds=timeout_seconds + 10,
        max_bytes=1_048_576)
    if response["timed_out"]:
        raise _TransportError("timeout", "worker_timeout", response)
    if response["start_error"] or not response["limiter_applied"]:
        raise _TransportError("unavailable", "worker_or_resource_limiter_unavailable", response)
    if response["output_limited"] or response["exit_code"] != 0:
        raise _TransportError("error", "worker_transport_failed", response)
    try:
        payload = json.loads(response["stdout"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _TransportError("error", "invalid_worker_result", response) from None
    payload["supervisor"] = {key: response.get(key) for key in
                             ("limiter", "limiter_applied", "duration_ms", "tree_termination_confirmed")}
    payload["supervisor"]["process"] = lean_worker.process_evidence(response)
    return payload


class _TransportError(RuntimeError):
    def __init__(self, status, code, execution):
        super().__init__(code)
        self.status, self.code, self.execution = status, code, execution


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_worker_result(payload, case):
    if (not isinstance(payload, dict) or payload.get("worker_protocol") != "lean-fixed-v1"
            or payload.get("case_id") != case["case_id"] or payload.get("status") not in STATUSES):
        raise ValueError("Invalid worker identity or status")
    for key in ("formal_statement", "source", "environment", "trust", "execution"):
        if not isinstance(payload.get(key), dict):
            raise ValueError("Missing worker fields")
    formal, trust, execution, source = (payload[key] for key in ("formal_statement", "trust", "execution", "source"))
    if formal.get("target") != case["target"] or formal.get("source") != case["statement"]:
        raise ValueError("Formal statement is not the requested fixed case")
    if (type(payload.get("duration_ms")) not in (float, int) or not math.isfinite(payload["duration_ms"])
            or payload["duration_ms"] < 0 or not isinstance(payload.get("diagnostics"), list)):
        raise ValueError("Invalid diagnostics or duration")
    if payload["status"] == "passed":
        if (type(execution.get("exit_code")) is not int or execution.get("exit_code") != 0 or execution.get("compiler_started") is not True
                or execution.get("timed_out") is not False or execution.get("output_limited") is not False
                or execution.get("limiter_applied") is not True or trust.get("audit_completed") is not True
                or trust.get("passed") is not True or trust.get("policy_id") != lean_worker.POLICY_ID
                or not _strings(trust.get("axioms"))
                or set(trust["axioms"]) - set(lean_worker.APPROVED_AXIOMS)
                or any(trust.get(key) != [] for key in ("sorry_axioms", "unapproved_axioms", "unsafe_dependencies",
                                                       "partial_dependencies", "extra_dependencies", "missing_dependencies"))):
            raise ValueError("Successful exit is insufficient for a completed proof")
        if (not isinstance(formal.get("elaborated"), str) or not formal["elaborated"].strip()
                or formal.get("elaborated_sha256") != hashlib.sha256(formal["elaborated"].encode("utf-8")).hexdigest()
                or source.get("reviewed_sources_match") is not True
                or source.get("files") != lean_worker.reviewed_sources(case)):
            raise ValueError("Missing exact statement or source provenance")
        expected_source = ((lean_worker.PROJECT / "Audit.lean").read_text(encoding="utf-8").rstrip() + "\n\n"
                           + (lean_worker.PROJECT / case["file"]).read_text(encoding="utf-8").rstrip()
                           + "\n\n#yanzheng_audit " + case["target"] + "\n")
        if source.get("sha256") != hashlib.sha256(expected_source.encode("utf-8")).hexdigest():
            raise ValueError("Compiled source provenance mismatch")
        env = payload["environment"]
        if (env.get("available") is not True or env.get("toolchain") != lean_worker.TOOLCHAIN
                or env.get("lean_commit") != lean_worker.LEAN_COMMIT):
            raise ValueError("Unpinned toolchain")
        if any(not isinstance(d, dict) or d.get("severity") == "error" for d in payload["diagnostics"]):
            raise ValueError("Error diagnostics cannot pass")
    elif trust.get("passed") is True:
        raise ValueError("Non-passed run cannot claim trusted completion")
    return payload


def run(request, *, timeout_seconds=10.0):
    request = validate_request(request)
    timeout_seconds = lean_worker.validate_timeout(timeout_seconds)
    case = lean_worker.load_cases()[request["case_id"]]
    started = time.perf_counter()
    record = {"schema_version": "1.0", "record_type": "research_lean_verification", "adapter_version": "lean-fixed-v1",
              "id": uuid.uuid4().hex, "case_id": request["case_id"], "status": "unavailable",
              "created_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
              "formal_statement": {"target": case["target"], "source": case["statement"], "elaborated": None,
                                   "elaborated_sha256": None},
              "source": {"sha256": None, "files": [], "reviewed_sources_match": False},
              "environment": {}, "diagnostics": [], "duration_ms": 0,
              "trust": lean_worker._blank_trust(),
              "execution": {"compiler_started": False, "exit_code": None, "resource_limits": {"wall_seconds": timeout_seconds}},
              "provenance": {"external_model_api_called": False, "arbitrary_code_accepted": False,
                             "natural_language_equivalence_verified": False, "application_state_modified": False,
                             "source_kind": "reviewed_fixed_case", "execution_state": "confirmed_not_started"},
              "limitations": copy.deepcopy(LIMITATIONS)}
    acquired = _RUN_LOCK.acquire(blocking=False)
    try:
        if not acquired:
            record["error"] = {"code": "busy"}
        else:
            payload = _validate_worker_result(_invoke_worker(request, timeout_seconds), case)
            for key in ("status", "formal_statement", "source", "environment", "diagnostics", "trust", "execution", "supervisor", "error"):
                if key in payload:
                    record[key] = copy.deepcopy(payload[key])
            record["execution"]["worker_duration_ms"] = payload["duration_ms"]
            record["provenance"]["worker_sha256"] = hashlib.sha256(Path(lean_worker.__file__).read_bytes()).hexdigest()
            if record["execution"].get("compiler_started"):
                record["provenance"]["execution_state"] = "started_result_unknown" if record["status"] == "timeout" else "confirmed_started"
    except _TransportError as error:
        record.update(status=error.status, error={"code": error.code})
        record["provenance"]["execution_state"] = "unknown" if error.execution.get("started") else "confirmed_not_started"
        record["execution"]["supervisor"] = {key: error.execution.get(key) for key in
                                               ("exit_code", "timed_out", "output_limited", "limiter", "limiter_applied", "tree_termination_confirmed")}
        record["execution"]["supervisor"]["process"] = lean_worker.process_evidence(error.execution)
    except (ValueError, KeyError, TypeError, OSError):
        record.update(status="error", error={"code": "invalid_worker_result"})
        record["provenance"]["execution_state"] = "unknown"
    finally:
        if acquired:
            _RUN_LOCK.release()
    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    return record
