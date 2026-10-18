"""Local research planning API. Start with uvicorn app:app --host 127.0.0.1 --port 8097."""

import copy
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

import engine
import catalog
import literature_service
import planner
import workflow
import experiments
import theory
import theory_review
import evidence_access
import theory_examples
import lean_service
import lean_api
from model_service import ModelService, ModelServiceError
from storage import ProjectNotFound, Store

BASE = Path(__file__).resolve().parent


class Intake(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_idea: str = Field(min_length=1, max_length=3000)
    goal: Literal["experimental", "theory", "comparison"] = "experimental"
    resources: Literal["laptop", "gpu"] = "laptop"
    time_horizon: Literal["week", "month", "flexible"] = "week"
    context: str = Field(default="", max_length=6000)
    planning_mode: Literal["demo", "skill"] = "demo"

    @field_validator("initial_idea", "context", mode="before")
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value


class Answers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str] = Field(min_length=1, max_length=3)

    @field_validator("answers")
    @classmethod
    def clean_answers(cls, values):
        result = {}
        for key, value in values.items():
            if len(key) > 80 or not value.strip() or len(value) > 2000:
                raise ValueError("每项回答应为 1—2000 字，问题标识不得超过 80 字。")
            result[key] = value.strip()
        return result


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="", max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def clean_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class LiteratureSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300)
    provider: Literal["crossref", "arxiv"] = "crossref"
    limit: int = Field(default=5, ge=1, le=5, strict=True)

    @field_validator("query", mode="before")
    @classmethod
    def clean_query(cls, value):
        return value.strip() if isinstance(value, str) else value


class Advance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str] = Field(default_factory=dict, max_length=3)
    material: str = Field(default="", max_length=6000)


class ReadingNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=100)
    level: Literal["abstract_checked", "fulltext_checked"]
    content: str = Field(min_length=1, max_length=6000)
    locator: str = Field(min_length=1, max_length=300)
    confirmed: Literal[True]

    @field_validator("content", "locator", mode="before")
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value


class TheoryCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card: dict
    expected_version: int = Field(ge=0, strict=True)


class TheoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TheoryReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=2000)


class TheoryPropose(TheoryRequest):
    context_token: str | None = Field(default=None, min_length=64, max_length=64)


class FailureReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(min_length=1, max_length=80)


class TheoryExample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["uniform-continuity"] = "uniform-continuity"


class CheckArtifact(TheoryRequest):
    artifact_id: str = Field(min_length=1, max_length=200)
    original_bytes_sha256: str | None = Field(max_length=64)
    acquired_at: str | None = Field(max_length=80)


class CheckMaterial(TheoryRequest):
    request_id: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=0, strict=True)
    context_token: str = Field(min_length=64, max_length=64)
    target_key: str = Field(min_length=64, max_length=64)
    claim_id: str = Field(min_length=1, max_length=80)
    proposition: Literal["P0", "P1", "P2", "OTHER", "H14", "HISTORICAL", "REVIEW_STATUS"]
    card_proposition: Literal["P0", "P1", "P2", "OTHER", "H14", "HISTORICAL", "REVIEW_STATUS"] | None
    claim_text: str = Field(min_length=1, max_length=8000)
    assumptions: str = Field(min_length=1, max_length=4000)
    support_scope: str = Field(min_length=1, max_length=4000)
    derivation: str = Field(min_length=1, max_length=16000)
    record_origin: Literal["researcher_draft", "assistant_check", "external_material"]
    source_reference: str = Field(min_length=1, max_length=2000)
    note_id: str | None = Field(max_length=128)
    locator: str = Field(min_length=1, max_length=2000)
    artifact: CheckArtifact | None
    target_scope: Literal["case", "card", "obligation"] | None = None
    case_key: str | None = Field(default=None, min_length=64, max_length=64)
    artifact_refs: list["CheckReference"] | None = Field(default=None, max_length=12)


class CheckReference(CheckArtifact):
    label: str = Field(min_length=1, max_length=300)
    source_version: str = Field(min_length=1, max_length=300)
    locator: str = Field(min_length=1, max_length=2000)
    support_scope: str = Field(min_length=1, max_length=4000)
    origin: str = Field(min_length=1, max_length=200)


class CheckCase(TheoryRequest):
    request_id: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=0, strict=True)
    case_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=300)
    source_sha256: str | None = Field(max_length=64)
    source_reference: str = Field(min_length=1, max_length=2000)


class CheckReview(TheoryRequest):
    request_id: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=0, strict=True)
    kind: Literal["assistant_check", "human_review"]
    scope: str = Field(min_length=1, max_length=4000)
    opinion: str = Field(min_length=1, max_length=8000)
    reviewer: str = Field(min_length=1, max_length=200)
    confirm_record_only: bool = Field(strict=True)


class CheckArtifactAccess(TheoryRequest):
    artifact_id: str = Field(min_length=1, max_length=200, strict=True)


class LeanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=80, strict=True)

    @field_validator("case_id")
    @classmethod
    def reviewed_case(cls, value):
        return lean_service.validate_request({"case_id": value})["case_id"]


def project_summary(project):
    record = project["direction_map"]
    selected = next((topic["title"] for topic in record["topics"] if topic["id"] == record["selected_topic_id"]), None)
    return {key: project[key] for key in ("id", "title", "stage", "created_at", "updated_at")} | {"initial_idea": record["initial_idea"], "topic_count": len(record["topics"]), "selected_topic_title": selected, "literature_search_count": len(project.get("literature_searches", []))}


def create_app(db_path=None, model_client=None, lean_client=None, evidence_client=None):
    application = FastAPI(title="研证本地工作台", version="0.5.0", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    store = Store(db_path or os.environ.get("YANZHENG_DB_PATH", BASE / "data/research.db"))
    application.state.store = store
    evidence = evidence_client if evidence_client is not None else evidence_access.EvidenceAccess()
    application.state.evidence_access = evidence
    model = model_client if model_client is not None else ModelService()
    application.state.model_service = model
    lean_runs = lean_api.LeanRuns(store, runner=lean_client)
    application.state.lean_runs = lean_runs
    operation_locks = {}
    lock_guard = threading.Lock()

    def operation_lock(project_id, kind):
        with lock_guard:
            return operation_locks.setdefault((project_id, kind), threading.Lock())

    def advance_skill(project_id, answers=None, material=""):
        gate = operation_lock(project_id, "workflow")
        if not gate.acquire(blocking=False):
            return JSONResponse({"detail": "此研究已有一步正在处理，输入尚未改动，请等待完成。"}, status_code=409)
        try:
            def prepare(current):
                if current["stage"] == "topic_selected":
                    raise ValueError("请先返回候选比较，再使用资料修订已选课题。")
                if current.get("workflow", {}).get("mode") != "skill":
                    current["workflow"] = workflow.initialize_project(current["intake_form"])["workflow"]
                    engine.add_event(current, "skill_workflow_enabled", "启用 Skill 科研流程；保留已有简报与候选。")
                workflow.prepare_input(current, answers=answers or {}, material=material)
            project = store.update(project_id, prepare)
            revision = project["updated_at"]
            messages = workflow.build_messages(project)
            input_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            try:
                payload, call = model.complete(messages)
            except ModelServiceError as error:
                call = copy.deepcopy(error.record)
                call.update({"input_revision": revision, "input_sha256": input_hash, "prompt_version": workflow.PROMPT_VERSION})
                model.record_result(call)
                # Pending input has already been saved. A zero-request failure
                # should still expose the retry state, without claiming a call.
                store.update(project_id, lambda current: workflow.apply_failure(current, call))
                return JSONResponse({"detail": error.public_message}, status_code=error.http_status)
            call.update({"input_revision": revision, "input_sha256": input_hash, "prompt_version": workflow.PROMPT_VERSION})
            try:
                workflow.validate_payload(payload, project)
            except (ValueError, TypeError):
                call.update({"status": "failed", "application_status": "rejected", "error_code": "invalid_workflow",
                             "summary": "模型输出未通过科研流程校验；已有方向和本次输入已保留，可重试。"})
                model.record_result(call)
                store.update(project_id, lambda current: workflow.apply_failure(current, call))
                return JSONResponse({"detail": call["summary"]}, status_code=502)
            conflict = []

            def apply(current):
                if current["updated_at"] != revision or current["stage"] == "topic_selected":
                    call.update({"application_status": "discarded_conflict", "summary": "请求期间研究已更新，本次结果未覆盖当前内容。"})
                    workflow.apply_failure(current, call)
                    conflict.append(True)
                else:
                    call.update({"application_status": "applied", "summary": "科研流程结果已校验并保存，推荐仍等待用户选择。"})
                    workflow.apply_result(current, payload, call)

            result = store.update(project_id, apply)
            model.record_result(call)
            if conflict:
                return JSONResponse({"detail": call["summary"]}, status_code=409)
            return result
        finally:
            gate.release()

    @application.middleware("http")
    async def local_request_policy(request: Request, call_next):
        # No external CORS; reject cross-site mutations before parsing a body.
        origin = request.headers.get("origin")
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin is not None and origin != str(request.base_url).rstrip("/")):
                return JSONResponse({"detail": "请从本地工作台页面发起操作。"}, status_code=403)
            content_type = request.headers.get("content-type", "").lower()
            if request.url.path.startswith("/api/") and request.url.path.rsplit("/", 1)[-1] != "reopen" and not content_type.startswith("application/json"):
                return JSONResponse({"detail": "请求需使用 JSON 格式。"}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    @application.exception_handler(ProjectNotFound)
    async def missing_project(request, error):
        return JSONResponse({"detail": "研究记录不存在，请返回项目列表。"}, status_code=404)

    @application.exception_handler(catalog.ArtifactNotFound)
    async def missing_artifact(request, error):
        return JSONResponse({"detail": "档案不存在或暂不可用。"}, status_code=404)

    @application.exception_handler(ValueError)
    async def invalid_action(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"detail": "输入格式不正确：请检查必填内容、选项及字数限制。"}, status_code=422)

    @application.get("/api/status")
    def status():
        model_status = model.status()
        configured = model_status["configured"]
        return {"mode": "qwen" if configured else "demo", "model_connected": model_status["state"] == "verified",
                "model": model_status, "storage": "sqlite", "version": "0.5.0",
                "capabilities": {"direction_generation": "qwen_available" if configured else "demo", "literature_search": "available",
                                 "model": model_status["state"], "experiments": "randomized_svd",
                                 "skill_workflow": True, "reading_notes": True,
                                 "theory_workspace": True, "theory_revision_review": True, "formal_verification": False,
                                 "lean_fixed_cases": True, "lean_run_storage": True}}

    @application.get("/api/lean/status")
    def lean_status():
        return {"environment": lean_service.environment()}

    @application.get("/api/lean/cases")
    def lean_cases():
        return {"cases": lean_service.list_cases()}

    @application.get("/api/projects/{project_id}/lean/runs")
    def list_lean_runs(project_id: str):
        return {"runs": lean_runs.list(project_id)}

    @application.post("/api/projects/{project_id}/lean/runs")
    def run_lean_case(project_id: str, body: LeanRequest):
        try:
            return {"entry": lean_runs.run(project_id, body.model_dump())}
        except lean_api.LeanBusy as error:
            return JSONResponse({"detail": str(error), "code": "lean_busy"}, status_code=409)
        except lean_api.LeanExecutionError as error:
            return JSONResponse({"detail": str(error), "code": "lean_result_unavailable"}, status_code=502)
        except lean_api.LeanSaveError as error:
            return JSONResponse({"detail": str(error), "code": "lean_save_failed", "persisted": False,
                                 "reservation_id": error.reservation_id, "unsaved_entry": error.entry}, status_code=503)

    @application.get("/api/projects/{project_id}/lean/runs/{run_id}/export")
    def export_lean_run(project_id: str, run_id: str, format: Literal["json"] = "json"):
        entry = lean_runs.get(project_id, run_id)
        if entry is None:
            return JSONResponse({"detail": "Lean 核验记录不存在。"}, status_code=404)
        return Response(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="yanzheng-lean-{entry["id"]}.json"'})

    @application.get("/api/library")
    def library():
        return catalog.library()

    @application.get("/api/library/artifacts/{artifact_id}")
    def download_artifact(artifact_id: str):
        path = catalog.artifact_path(artifact_id)
        return FileResponse(path, media_type="application/json", filename=path.name)

    @application.get("/api/projects")
    def projects():
        return {"projects": [project_summary(project) for project in store.list()]}

    @application.post("/api/projects", status_code=201)
    def create_project(body: Intake):
        form = body.model_dump(exclude={"planning_mode"})
        if body.planning_mode == "skill":
            project = store.create(workflow.initialize_project(form))
            advance_skill(project["id"])
            # Even a failed first call returns the saved input and retry state.
            return store.get(project["id"])
        return store.create(engine.create_project(form))

    @application.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return lean_api.project_view(store.get(project_id))

    @application.post("/api/projects/{project_id}/answers")
    def answer_project(project_id: str, body: Answers):
        if store.get(project_id).get("workflow", {}).get("mode") == "skill":
            return advance_skill(project_id, answers=body.answers)
        return store.update(project_id, lambda project: engine.answer_questions(project, body.answers))

    @application.post("/api/projects/{project_id}/advance")
    def advance_project(project_id: str, body: Advance):
        return advance_skill(project_id, body.answers, body.material)

    @application.post("/api/projects/{project_id}/select")
    def select_project(project_id: str, body: Selection):
        return store.update(project_id, lambda project: engine.select_topic(project, body.topic_id, body.reason))

    @application.post("/api/projects/{project_id}/generate")
    def generate_project_directions(project_id: str):
        if store.get(project_id).get("workflow", {}).get("mode") == "skill":
            return advance_skill(project_id)
        gate = operation_lock(project_id, "workflow")
        if not gate.acquire(blocking=False):
            return JSONResponse({"detail": "此研究已有模型步骤正在进行，请等待完成。"}, status_code=409)
        try:
            return generate_legacy_directions(project_id)
        finally:
            gate.release()

    def generate_legacy_directions(project_id):
        project = store.get(project_id)
        if project["stage"] != "directions_ready":
            return JSONResponse({"detail": "请先完成澄清；已选题的研究请先返回候选比较。"}, status_code=409)
        revision = project["updated_at"]
        messages = planner.build_messages(project)
        input_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        try:
            payload, call = model.complete(messages)
        except ModelServiceError as error:
            call = copy.deepcopy(error.record)
            call.update({"input_revision": revision, "input_sha256": input_hash, "prompt_version": planner.PROMPT_VERSION})
            model.record_result(call)
            # A rejected duplicate did not call the model and must not invalidate
            # the real in-flight generation by changing the project revision.
            if call.get("attempts", 0):
                store.update(project_id, lambda current: planner.apply_failure(current, call))
            return JSONResponse({"detail": error.public_message}, status_code=error.http_status)
        call.update({"input_revision": revision, "input_sha256": input_hash, "prompt_version": planner.PROMPT_VERSION})
        try:
            planner.validate_payload(payload)
        except (ValueError, TypeError):
            call.update({"status": "failed", "application_status": "rejected", "error_code": "invalid_plan",
                         "summary": "模型已返回，但候选结构或业务边界不合要求；原有候选已保留。"})
            model.record_result(call)
            store.update(project_id, lambda current: planner.apply_failure(current, call))
            return JSONResponse({"detail": call["summary"]}, status_code=502)
        conflict = []

        def apply_if_current(current):
            if current["updated_at"] != revision or current["stage"] != "directions_ready":
                call.update({"application_status": "discarded_conflict", "summary": "模型已返回，但期间项目发生变化；保留当前候选，请确认后再试。"})
                planner.apply_failure(current, call)
                conflict.append(True)
            else:
                call["application_status"] = "applied"
                planner.apply_result(current, payload, call)

        result = store.update(project_id, apply_if_current)
        model.record_result(call)
        if conflict:
            return JSONResponse({"detail": call["summary"]}, status_code=409)
        return result

    @application.post("/api/projects/{project_id}/literature-search")
    def search_project_literature(project_id: str, body: LiteratureSearch):
        # Resolve before making requests; network I/O never holds SQLite's write lock.
        project = store.get(project_id)
        record = literature_service.search(project["direction_map"]["initial_idea"], body.query, body.provider, body.limit)
        return store.update(project_id, lambda current: engine.add_literature_search(current, record))

    @application.post("/api/projects/{project_id}/reading-notes")
    def save_reading_note(project_id: str, body: ReadingNote):
        def save(current):
            run = next((item for item in current.get("literature_searches", []) if item["id"] == body.run_id), None)
            source = next((item for item in run["sources"] if item["id"] == body.source_id), None) if run else None
            if source is None:
                raise ValueError("来源不属于该项目的这轮检索，请刷新后重试。")
            note = body.model_dump() | {"id": uuid.uuid4().hex, "checked_at": engine.now(), "origin": "user_attested"}
            current.setdefault("reading_notes", []).append(note)
            engine.add_event(current, "reading_note_added", "用户确认阅读并保存摘录：" + source["title"] + "；位置：" + body.locator)
        return store.update(project_id, save)

    @application.post("/api/theory/examples", status_code=201)
    def create_theory_example(body: TheoryExample):
        return store.create(theory_examples.create_example(body.name))

    def workspace_with_access(project):
        result = theory.workspace(project)
        result["checking"]["evidence_access"] = evidence.metadata()
        return result

    @application.get("/api/projects/{project_id}/theory")
    def theory_workspace(project_id: str):
        return workspace_with_access(store.get(project_id))

    @application.post("/api/projects/{project_id}/theory/card")
    def save_theory_card(project_id: str, body: TheoryCard):
        return lean_api.project_view(store.update(project_id, lambda current: theory.save_card(current, body.card, body.expected_version)))

    @application.post("/api/projects/{project_id}/theory/propose")
    def propose_theory_revision(project_id: str, body: TheoryPropose):
        # Both model paths share the same project gate, so a skipped request
        # cannot invalidate the other path's genuine in-flight result.
        gate = operation_lock(project_id, "workflow")
        if not gate.acquire(blocking=False):
            return JSONResponse({"detail": "此命题已有修订请求正在进行，请等待完成。"}, status_code=409)
        try:
            project = store.get(project_id)
            messages = theory.build_messages(project)
            if body.context_token is not None and body.context_token != theory._hash(messages):
                return JSONResponse({"detail": "下一轮材料已变化，请重新检查上下文；本次未调用模型。"}, status_code=409)
            revision = project["updated_at"]
            input_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

            def record_failure(call, captured, diagnostic):
                if call.get("attempts", 0) or call.get("error_code") != "busy":
                    store.update(project_id, lambda current: theory.archive_failure(
                        current, call, captured, project, messages, diagnostic))
                model.record_result(call)

            try:
                payload, call = getattr(model, "complete_for_theory", model.complete)(messages)
            except ModelServiceError as error:
                call = copy.deepcopy(error.record)
                captured = call.pop("_response_capture", None)
                call.update(input_revision=revision, input_sha256=input_hash, prompt_version=theory.PROMPT_VERSION)
                record_failure(call, captured, error.public_message)
                return JSONResponse({"detail": error.public_message}, status_code=error.http_status)
            captured = call.pop("_response_capture", None)
            call.update(input_revision=revision, input_sha256=input_hash, prompt_version=theory.PROMPT_VERSION)
            try:
                theory.validate_payload(payload, project)
            except (ValueError, TypeError) as error:
                call.update(status="failed", application_status="rejected", error_code="invalid_theory",
                            summary="修订输出未通过命题、引理或引用校验，当前命题与已有提议均已保留，可重试。")
                record_failure(call, captured, str(error))
                return JSONResponse({"detail": call["summary"]}, status_code=502)
            conflict = []

            def save_proposal(current):
                # Clock resolution is not a revision guarantee (including on Windows).
                # Preserve the real response even when two edits share a timestamp.
                if (current["updated_at"] != revision or
                        theory.context_fingerprint(current) != theory.context_fingerprint(project)):
                    call.update(application_status="discarded_conflict", summary="请求期间研究发生变化，本次提议未覆盖当前内容。")
                    theory.archive_failure(current, call, captured, project, messages, call["summary"])
                    conflict.append(True)
                else:
                    call.update(application_status="pending_review", summary="修订提议已保存，等待研究者审阅；当前命题尚未改变。")
                    theory.store_proposal(current, payload, call, input_project=project)

            result = store.update(project_id, save_proposal)
            model.record_result(call)
            if conflict:
                return JSONResponse({"detail": call["summary"]}, status_code=409)
            return result
        finally:
            gate.release()

    @application.get("/api/projects/{project_id}/theory/next-context")
    def theory_next_context(project_id: str):
        return theory.next_context(store.get(project_id))

    @application.post("/api/projects/{project_id}/theory/checks")
    def save_theory_check(project_id: str, body: CheckMaterial):
        try:
            return store.update(project_id, lambda current: theory_review.save(current, body.model_dump(exclude_unset=True)))
        except theory_review.Conflict as error:
            return JSONResponse({"detail": str(error)}, status_code=409)

    @application.post("/api/projects/{project_id}/theory/cases")
    def register_theory_case(project_id: str, body: CheckCase):
        try:
            return store.update(project_id, lambda current: theory_review.register_case(current, body.model_dump()))
        except theory_review.Conflict as error:
            return JSONResponse({"detail": str(error)}, status_code=409)

    @application.post("/api/projects/{project_id}/theory/checks/{record_id}/reviews")
    def review_theory_check(project_id: str, record_id: str, body: CheckReview):
        if not theory_review.record(store.get(project_id), record_id):
            return JSONResponse({"detail": "本项目没有该核查材料。"}, status_code=404)
        try:
            return store.update(project_id, lambda current: theory_review.review(current, record_id, body.model_dump()))
        except theory_review.Conflict as error:
            return JSONResponse({"detail": str(error)}, status_code=409)

    def associated_artifact(project_id, record_id, artifact_id):
        project = store.get(project_id)
        item = theory_review.record(project, record_id)
        if item is None:
            return None, None, JSONResponse({"detail": "本项目没有该核查材料。"}, status_code=404)
        declarations = theory_review.artifact_declarations(item, artifact_id)
        if not declarations:
            return None, None, JSONResponse({"detail": "该原件未明确关联此条材料。", "status": "unassociated"}, status_code=403)
        return project, declarations, None

    @application.post("/api/projects/{project_id}/theory/checks/{record_id}/artifacts/describe")
    def describe_check_artifact(project_id: str, record_id: str, body: CheckArtifactAccess, request: Request):
        if request.query_params:
            return JSONResponse({"detail": "不接受查询参数；只提交 artifact_id。"}, status_code=422)
        _, _, error = associated_artifact(project_id, record_id, body.artifact_id)
        if error is not None:
            return error
        try:
            return evidence.describe(body.artifact_id)
        except evidence_access.Unavailable:
            return JSONResponse({"detail": "固定原件注册器不可用。", "status": "registry_unavailable"}, status_code=503)

    @application.post("/api/projects/{project_id}/theory/checks/{record_id}/artifacts/read")
    def read_check_artifact(project_id: str, record_id: str, body: CheckArtifactAccess, request: Request):
        if request.query_params:
            return JSONResponse({"detail": "不接受查询参数；只提交 artifact_id。"}, status_code=422)
        project, declarations, error = associated_artifact(project_id, record_id, body.artifact_id)
        if error is not None:
            return error
        item = theory_review.record_view(project, theory_review.record(project, record_id))
        unavailable = False
        try:
            result = evidence.read(body.artifact_id, declarations)
        except evidence_access.Unavailable:
            unavailable = True
            result = {"artifact_id": body.artifact_id, "status": "registry_unavailable"}
        fact = {"id": str(uuid.uuid4()), "artifact_id": body.artifact_id, "recorded_at": engine.now(),
            "material_sha256_at_read": item["material_sha256"], "target": copy.deepcopy(item["target"]),
            "stale_reasons_at_read": item["stale_reasons"], "service": evidence.metadata(), "result": result}
        try:
            # File IO happened above, outside SQLite's write transaction. Recheck ownership/association.
            store.update(project_id, lambda current: theory_review.append_artifact_read(current, record_id, fact))
        except (OSError, sqlite3.Error, theory_review.Conflict, ProjectNotFound):
            return JSONResponse({"detail": "读取已结束，事实未保存；保留此结果并核对本地存储。", "saved": False, "fact": fact}, status_code=503)
        return JSONResponse({"saved": True, "fact": fact, "detail": "注册器不可用；失败事实已保存。" if unavailable else "本次读取事实已保存。"}, status_code=503 if unavailable else 200)

    @application.get("/api/projects/{project_id}/theory/checks/{record_id}/export")
    def export_theory_check(project_id: str, record_id: str, format: Literal["json", "md"] = "json"):
        project = store.get(project_id)
        item = theory_review.record(project, record_id)
        if not item:
            return JSONResponse({"detail": "本项目没有该核查材料。"}, status_code=404)
        entry = theory_review.record_view(project, item)
        content = theory_review.markdown([entry]) if format == "md" else json.dumps(entry, ensure_ascii=False, indent=2) + "\n"
        return Response(content, media_type="text/markdown; charset=utf-8" if format == "md" else "application/json",
            headers={"Content-Disposition": f'attachment; filename="theory-check-{record_id}.{format}"'})

    @application.get("/api/projects/{project_id}/theory/failures/{failure_id}")
    def theory_failure(project_id: str, failure_id: str):
        entry = theory.failure_record(store.get(project_id), failure_id)
        return entry if entry else JSONResponse({"detail": "本项目没有该失败记录。"}, status_code=404)

    @application.post("/api/projects/{project_id}/theory/failures/{failure_id}/reviews")
    def review_theory_failure(project_id: str, failure_id: str, body: FailureReview):
        if not theory.failure_record(store.get(project_id), failure_id):
            return JSONResponse({"detail": "本项目没有该失败记录。"}, status_code=404)
        try:
            return store.update(project_id, lambda current: theory.review_failure(
                current, failure_id, body.reason, body.expected_revision, body.request_id))
        except theory.ReviewConflict as error:
            return JSONResponse({"detail": str(error)}, status_code=409)

    @application.get("/api/projects/{project_id}/theory/failures/{failure_id}/export")
    def export_theory_failure(project_id: str, failure_id: str, format: Literal["json", "md"] = "json"):
        entry = theory.failure_record(store.get(project_id), failure_id)
        if not entry:
            return JSONResponse({"detail": "本项目没有该失败记录。"}, status_code=404)
        content = json.dumps(entry, ensure_ascii=False, indent=2) + "\n"
        media_type = "application/json"
        if format == "md":
            content = theory.render_failure_markdown(entry)
            media_type = "text/markdown; charset=utf-8"
        return Response(content, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="theory-failure-{entry["id"]}.{format}"'})

    @application.post("/api/projects/{project_id}/theory/proposals/{proposal_id}/accept")
    def accept_theory_revision(project_id: str, proposal_id: str, body: TheoryRequest):
        return lean_api.project_view(store.update(project_id, lambda current: theory.apply_proposal(current, proposal_id)))

    @application.post("/api/projects/{project_id}/theory/proposals/{proposal_id}/reject")
    def reject_theory_revision(project_id: str, proposal_id: str, body: TheoryReview):
        return lean_api.project_view(store.update(project_id, lambda current: theory.reject_proposal(current, proposal_id, body.reason)))

    @application.get("/api/projects/{project_id}/theory/export")
    def export_theory(project_id: str, format: Literal["md", "json"] = "md"):
        project = store.get(project_id)
        if format == "md":
            content, media_type = theory.render_markdown(project), "text/markdown; charset=utf-8"
        else:
            content = json.dumps(workspace_with_access(project), ensure_ascii=False, indent=2) + "\n"
            media_type = "application/json"
        return Response(content, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="yanzheng-theory-{project_id}.{format}"'})

    @application.get("/api/projects/{project_id}/experiments")
    def experiment_workspace(project_id: str):
        project = store.get(project_id)
        return experiments.supported(project) | {"defaults": experiments.defaults(), "runs": project.get("experiments", [])}

    @application.post("/api/projects/{project_id}/experiments")
    def run_experiment(project_id: str, body: dict):
        project = store.get(project_id)
        support = experiments.supported(project)
        if not support["supported"]:
            return JSONResponse({"detail": support["reason"]}, status_code=409)
        config = experiments.validate_config(body)
        gate = operation_lock(project_id, "experiment")
        if not gate.acquire(blocking=False):
            return JSONResponse({"detail": "此项目的实验正在运行，请等待完成。"}, status_code=409)
        try:
            record = experiments.run(project, config)
            return store.update(project_id, lambda current: experiments.attach_result(current, record))
        finally:
            gate.release()

    @application.get("/api/projects/{project_id}/experiments/{run_id}/export")
    def export_experiment(project_id: str, run_id: str, format: Literal["json", "csv"] = "json"):
        project = store.get(project_id)
        run = next((item for item in project.get("experiments", []) if item["id"] == run_id), None)
        if run is None:
            raise ProjectNotFound(run_id)
        content = experiments.export_csv(run) if format == "csv" else json.dumps(run, ensure_ascii=False, indent=2) + "\n"
        return Response(content, media_type="text/csv; charset=utf-8" if format == "csv" else "application/json",
                        headers={"Content-Disposition": f'attachment; filename="yanzheng-experiment-{run_id}.{format}"'})

    @application.get("/api/projects/{project_id}/literature/{search_id}/export")
    def export_literature(project_id: str, search_id: str):
        project = store.get(project_id)
        record = next((run for run in project.get("literature_searches", []) if run["id"] == search_id), None)
        if record is None:
            raise ProjectNotFound(search_id)
        return Response(json.dumps(record, ensure_ascii=False, indent=2) + "\n", media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="yanzheng-literature-{record["id"]}.json"'})

    @application.post("/api/projects/{project_id}/reopen")
    def reopen_project(project_id: str):
        return store.update(project_id, engine.reopen_project)

    @application.get("/api/projects/{project_id}/export")
    def export_project(project_id: str):
        project = lean_api.project_view(store.get(project_id))
        engine.assert_valid(project["direction_map"])
        return Response(json.dumps(project, ensure_ascii=False, indent=2) + "\n", media_type="application/json", headers={"Content-Disposition": f'attachment; filename="yanzheng-{project["id"]}.json"'})

    @application.get("/api/projects/{project_id}/plan")
    def export_plan(project_id: str):
        project = store.get(project_id)
        return Response(engine.render_plan(project), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="yanzheng-plan-{project["id"]}.md"'})

    @application.get("/")
    def home():
        return FileResponse(BASE / "static/index.html")

    application.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    return application


app = create_app()
