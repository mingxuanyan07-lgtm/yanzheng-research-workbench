"""Durable project envelopes for the already verified, fixed-case Lean adapter.

This module never changes theory cards, obligations, selections or model state.
Only short SQLite mutations surround computation; no compiler runs in a write
transaction. A bounded lease records interrupted attempts without inventing a
verification result or silently resuming work.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
import uuid

import lean_service
from storage import ProjectNotFound


ASSOCIATION_STATUS = "context_only_not_semantically_verified"
DEDUPE_SECONDS = 10
LEASE_SECONDS = 90
_EXECUTION_LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


class LeanBusy(Exception):
    pass


class LeanExecutionError(Exception):
    pass


class LeanSaveError(Exception):
    def __init__(self, message, *, entry=None, reservation_id=None):
        super().__init__(message)
        self.entry = copy.deepcopy(entry)
        self.reservation_id = reservation_id


def card_context(project):
    state = project.get("theory") or {}
    card = state.get("card")
    version = state.get("card_version")
    if card is None:
        return {"project_id": project["id"], "card_version": None, "card_sha256": None}
    digest = hashlib.sha256(json.dumps(card, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"project_id": project["id"], "card_version": version if type(version) is int else None,
            "card_sha256": digest}


def entry_view(project, entry):
    value = copy.deepcopy(entry)
    context = value["context"]
    value["context_matches_current"] = (card_context(project) == context
        if context["card_version"] is not None and context["card_sha256"] is not None else None)
    return value


def project_view(project):
    value = copy.deepcopy(project)
    if "lean_runs" in project:
        value["lean_runs"] = [entry_view(project, entry) for entry in project["lean_runs"]]
    return value


def _recent(project, request, context, stamp):
    for attempt in reversed(project.get("lean_attempts", [])):
        if (attempt.get("state") == "completed" and attempt.get("case_id") == request["case_id"]
                and attempt.get("context") == context
                and 0 <= stamp - attempt.get("finished_epoch", 0) <= DEDUPE_SECONDS):
            entry = next((item for item in project.get("lean_runs", []) if item["id"] == attempt.get("verification_id")), None)
            if entry is not None:
                return entry
    return None


class LeanRuns:
    def __init__(self, store, runner=None):
        self.store = store
        self.runner = runner
        self.owner = uuid.uuid4().hex

    def list(self, project_id):
        project = self.store.get(project_id)
        return [entry_view(project, item) for item in project.get("lean_runs", [])]

    def get(self, project_id, run_id):
        return next((item for item in self.list(project_id) if item["id"] == run_id), None)

    def run(self, project_id, request):
        request = lean_service.validate_request(request)
        project = self.store.get(project_id)
        recent = _recent(project, request, card_context(project), time.time())
        if recent is not None:
            return entry_view(project, recent)
        if not _EXECUTION_LOCK.acquire(blocking=False):
            raise LeanBusy("本机已有 Lean 核验正在运行；本次没有执行，请等待完成。")
        reservation_id = uuid.uuid4().hex
        context = None
        try:
            reused = []

            def reserve(current):
                nonlocal context
                context = card_context(current)
                stamp = time.time()
                previous = _recent(current, request, context, stamp)
                if previous is not None:
                    reused.append(entry_view(current, previous))
                    return
                pending = next((item for item in current.get("lean_attempts", []) if item.get("state") == "running"), None)
                if pending:
                    if pending["lease_expires_epoch"] > stamp:
                        raise LeanBusy("此项目已有核验预约尚未结束；本次没有执行，请稍后读取历史。")
                    # An expired lease is not evidence of compiler success or
                    # failure. Preserve the start context and never auto-resume.
                    pending.update(state="interrupted_result_unknown", finished_at=now())
                current.setdefault("lean_attempts", []).append({
                    "id": reservation_id, "owner": self.owner, "state": "running",
                    "case_id": request["case_id"], "context": copy.deepcopy(context),
                    "reserved_at": now(), "lease_expires_epoch": stamp + LEASE_SECONDS,
                    "verification_id": None})
                current["lean_runs_updated_at"] = now()
                # Deliberately leave updated_at/history intact: unrelated model
                # revisions use updated_at as their optimistic concurrency token.

            try:
                self.store.update(project_id, reserve)
            except (LeanBusy, ProjectNotFound):
                raise
            except Exception:
                raise LeanSaveError("核验预约保存失败；本次没有启动执行器。", reservation_id=reservation_id) from None
            if reused:
                return reused[0]
            try:
                verification = (self.runner or lean_service.run)(request)
                # The adapter owns validation and the complete verification
                # contract. This envelope only rejects broken transport shapes.
                if (not isinstance(verification, dict) or verification.get("record_type") != "research_lean_verification"
                        or not isinstance(verification.get("id"), str) or not verification["id"]
                        or verification.get("case_id") != request["case_id"]
                        or verification.get("status") not in lean_service.STATUSES):
                    raise ValueError("Invalid adapter result")
                json.dumps(verification, ensure_ascii=False, allow_nan=False)
            except Exception:
                def failed(current):
                    attempt = next(item for item in current["lean_attempts"] if item["id"] == reservation_id)
                    attempt.update(state="failed_result_unknown", finished_at=now())
                    current["lean_runs_updated_at"] = now()
                try:
                    self.store.update(project_id, failed)
                except Exception:
                    raise LeanSaveError("核验器异常且失败记账未完成；预约结果未知，请先检查项目记录。",
                                        reservation_id=reservation_id) from None
                raise LeanExecutionError("固定核验器未返回完整记录；预约和原问题卡已保留，未自动重试。") from None

            entry = {"id": verification["id"], "verification": copy.deepcopy(verification),
                     "context": copy.deepcopy(context), "association_status": ASSOCIATION_STATUS,
                     "context_matches_current": None}
            saved = []

            def finish(current):
                attempt = next(item for item in current["lean_attempts"] if item["id"] == reservation_id)
                if attempt["owner"] != self.owner or attempt["state"] != "running":
                    raise ValueError("Lean reservation ownership changed")
                if any(item["id"] == entry["id"] for item in current.get("lean_runs", [])):
                    raise ValueError("Duplicate verification identity")
                value = entry_view(current, entry)
                current.setdefault("lean_runs", []).append(value)
                attempt.update(state="completed", finished_at=now(), finished_epoch=time.time(), verification_id=entry["id"])
                current["lean_runs_updated_at"] = now()
                saved.append(value)

            try:
                self.store.update(project_id, finish)
            except Exception:
                # Do not return an entry claiming it is durable when commit
                # failed. Its reservation remains for operator inspection.
                raise LeanSaveError("核验已返回，但保存失败；响应保留未入库结果，请人工保存并检查预约，不要自动重跑。",
                                    entry=entry, reservation_id=reservation_id) from None
            return saved[0]
        finally:
            _EXECUTION_LOCK.release()
