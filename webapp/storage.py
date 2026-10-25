"""SQLite project persistence; every mutation runs in one write transaction."""

import copy
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import engine


class ProjectNotFound(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects ("
                "id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS paper_document_originals ("
                "project_id TEXT NOT NULL, document_id TEXT NOT NULL, "
                "run_id TEXT NOT NULL, source_id TEXT NOT NULL, file_sha256 TEXT NOT NULL, "
                "body BLOB NOT NULL, PRIMARY KEY (project_id, document_id), "
                "UNIQUE (project_id, run_id, source_id, file_sha256))"
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(self, project):
        with self._session() as connection:
            connection.execute(
                "INSERT INTO projects (id, body) VALUES (?, ?)",
                (project["id"], json.dumps(project, ensure_ascii=False)),
            )
        return project

    def get(self, project_id):
        with self._session() as connection:
            row = connection.execute(
                "SELECT body FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return json.loads(row[0])

    def list(self):
        with self._session() as connection:
            rows = connection.execute("SELECT body FROM projects").fetchall()
        projects = [json.loads(row[0]) for row in rows]
        return sorted(projects, key=lambda project: project["updated_at"], reverse=True)

    def snapshot(self):
        """Read one consistent database snapshot without adding derived UI fields."""
        with self._session() as connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT body FROM projects ORDER BY id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def restore_missing(self, projects):
        """Atomically add validated snapshots, preserving every existing identity.

        Conflict checks and inserts share a write transaction, so simultaneous
        restores cannot replace a record or create a second copy under a new ID.
        Validation belongs to backup_service and must finish before this call.
        """
        encoded = [(project["id"], json.dumps(project, ensure_ascii=False, allow_nan=False))
                   for project in projects]
        imported, skipped = [], []
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for project_id, body in encoded:
                if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                    skipped.append(project_id)
                    continue
                connection.execute("INSERT INTO projects (id, body) VALUES (?, ?)", (project_id, body))
                imported.append(project_id)
            connection.commit()
            return imported, skipped
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update(self, project_id, mutate):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT body FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)
            project = json.loads(row[0])
            mutate(project)
            connection.execute(
                "UPDATE projects SET body = ? WHERE id = ?",
                (json.dumps(project, ensure_ascii=False), project_id),
            )
            connection.commit()
            return project
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def attach_paper_document(self, project_id, document, original):
        """Attach extracted metadata and original bytes in one write transaction.

        Parsing finishes before this short transaction. The association, limits
        and duplicate check are repeated under the write lock to cover races.
        Re-uploading an exact restored file can refill its missing original
        without replacing its page text, identity or existing quote anchors.
        """
        import literature_service
        if (not isinstance(original, bytes) or len(original) != document.get("byte_count")
                or hashlib.sha256(original).hexdigest() != document.get("file_sha256")):
            raise ValueError("正文原始字节与提取记录不一致，未保存。")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT body FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)
            project = json.loads(row[0])
            literature_service.document_source(project, document["run_id"], document["source_id"])
            documents = project.setdefault("paper_documents", [])
            existing = next((item for item in documents if item.get("run_id") == document["run_id"]
                             and item.get("source_id") == document["source_id"]
                             and item.get("file_sha256") == document["file_sha256"]), None)
            selected = existing or document
            original_exists = connection.execute(
                "SELECT 1 FROM paper_document_originals WHERE project_id = ? AND document_id = ?",
                (project_id, selected["id"])).fetchone()
            if existing is None:
                if len(documents) >= literature_service.PROJECT_MAX_DOCUMENTS:
                    raise ValueError("每个研究最多保存 12 份正文；本次文件未保存。")
                total = sum(len(page["text"]) for item in documents for page in item["pages"])
                total += sum(len(page["text"]) for page in document["pages"])
                if total > literature_service.PROJECT_MAX_CHARACTERS:
                    raise ValueError("本研究提取正文已达到 200 万字符上限，本次文件未保存。")
                documents.append(copy.deepcopy(document))
                engine.add_event(project, "paper_document_imported",
                                 "导入用户提供的正文：" + document["filename"] + "；仅保存文件与提取文本，尚未确认阅读。")
            elif not original_exists:
                engine.add_event(project, "paper_document_original_restored",
                                 "重新提供相同哈希的正文原件；保留已有页文本、文档标识和阅读定位。")
            if not original_exists:
                connection.execute(
                    "INSERT INTO paper_document_originals (project_id, document_id, run_id, source_id, file_sha256, body) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (project_id, selected["id"], selected["run_id"], selected["source_id"],
                     selected["file_sha256"], sqlite3.Binary(original)))
            if existing is None or not original_exists:
                connection.execute("UPDATE projects SET body = ? WHERE id = ?",
                                   (json.dumps(project, ensure_ascii=False, allow_nan=False), project_id))
            connection.commit()
            return project
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def paper_document_original_ids(self, project_id):
        with self._session() as connection:
            rows = connection.execute(
                "SELECT document_id FROM paper_document_originals WHERE project_id = ?", (project_id,)).fetchall()
        return {row[0] for row in rows}

    def get_paper_document_original(self, project_id, document_id):
        """Only return originals associated with this exact project metadata."""
        with self._session() as connection:
            row = connection.execute("SELECT body FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)
            project = json.loads(row[0])
            document = next((item for item in project.get("paper_documents", []) if item["id"] == document_id), None)
            if document is None:
                return None
            row = connection.execute(
                "SELECT body, file_sha256 FROM paper_document_originals WHERE project_id = ? AND document_id = ?",
                (project_id, document_id)).fetchone()
        if row is None:
            return None
        payload = bytes(row[0])
        if (row[1] != document["file_sha256"] or len(payload) != document["byte_count"]
                or hashlib.sha256(payload).hexdigest() != document["file_sha256"]):
            raise ValueError("保存的正文原件未通过字节完整性检查，暂不能下载；提取文本仍保留。")
        return copy.deepcopy(document), payload


# Versioned local backup contract; kept in this bundled runtime module.
FORMAT = "yanzheng-local-backup"
VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
MAX_PROJECTS = 1000
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
WARNINGS = [
    "恢复只新增本机缺失的研究；同 ID 的已有研究已跳过，未覆盖或复制为新 ID。",
    "备份保留当时的阅读、工具和核验声明；导入未重新访问来源、调用模型、运行实验或核验数学结论。",
    "备份不包含证据原件、附件文件、应用配置中的模型密钥或机器配置；原件可用性须在本机重新检查。",
    "恢复的行动计划已暂停，在途结果记为未知；待审阅提议保留，但需重新生成后才能接受。",
]


def _fail(message):
    raise ValueError("备份未恢复：" + message)


def _text(value, name, maximum=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail(name + "应为有效文本。")
    return value


def _identifier(value, name):
    if not isinstance(value, str) or value in (".", "..") or not _ID.fullmatch(value):
        _fail(name + "的标识无效。")
    return value


def _timestamp(value, name):
    _text(value, name, 80)
    try:
        if datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError()
    except ValueError:
        _fail(name + "应为包含时区的日期时间。")


def _objects(value, name, *, identified=False):
    if not isinstance(value, list):
        _fail(name + "应为记录数组。")
    identifiers = set()
    for item in value:
        if not isinstance(item, dict):
            _fail(name + "包含无效记录。")
        if identified:
            identifier = _identifier(item.get("id"), name)
            if identifier in identifiers:
                _fail(name + "包含重复标识。")
            identifiers.add(identifier)
    return value


def _shape(value):
    """Bound recursion and node count even for syntactically valid hostile JSON."""
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 64 or count > 500000:
            _fail("结构过深或记录过多，请拆分为较小备份。")
        if isinstance(item, dict):
            for key, child in item.items():
                if (not isinstance(key, str) or len(key) > 256
                        or key in ("__proto__", "prototype")
                        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in key)):
                    _fail("包含无效字段名。")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            _fail("JSON 含无效数值。")
        elif isinstance(item, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in item):
            _fail("JSON 含无法保存的 Unicode 字符。")


def _plan(plan, name):
    if not isinstance(plan, dict):
        _fail(name + "应为对象。")
    _identifier(plan.get("id"), name)
    if plan.get("status") not in ("active", "paused"):
        _fail(name + "状态无效。")
    actions = _objects(plan.get("actions"), name + "行动", identified=True)
    if not 1 <= len(actions) <= 4:
        _fail(name + "行动数量无效。")
    for action in actions:
        if action.get("kind") not in ("literature_search", "direction_revision"):
            _fail(name + "含不支持的工具类型。")
        _text(action.get("title"), name + "行动名称", 300)
        if action.get("status") not in ("planned", "running", "succeeded", "empty", "failed",
                                         "awaiting_review", "interrupted_result_unknown"):
            _fail(name + "行动状态无效。")
        if action["kind"] == "literature_search":
            _text(action.get("query"), name + "检索词", 300)
            if action.get("provider") not in ("crossref", "arxiv") or type(action.get("limit")) is not int or not 1 <= action["limit"] <= 5:
                _fail(name + "检索设置无效。")
    for field in ("limits", "used"):
        values = plan.get(field)
        if not isinstance(values, dict):
            _fail(name + "预算缺失。")
        for key, maximum in (("search_operations", 3), ("model_operations", 1)):
            if type(values.get(key)) is not int or not 0 <= values[key] <= maximum:
                _fail(name + "预算超出本产品限制。")
    if any(plan["used"][key] > plan["limits"][key] for key in ("search_operations", "model_operations")):
        _fail(name + "已用预算超过上限。")


def _owned(value, project_id, name):
    if not isinstance(value, dict) or value.get("project_id") != project_id:
        _fail(name + "关联到其他研究或缺少所属研究标识。")


def _paper_documents(project, source_pairs):
    """Validate extracted text only; backup never contains or authenticates bytes."""
    import literature_service as literature
    documents = _objects(project.get("paper_documents", []), "正文文档", identified=True)
    if len(documents) > literature.PROJECT_MAX_DOCUMENTS:
        _fail("每个研究的正文文档超过数量上限。")
    required = {"id", "run_id", "source_id", "filename", "media_type", "byte_count", "file_sha256",
                "text_sha256", "created_at", "pages", "extraction", "origin"}
    media = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown"}
    total, associations = 0, set()
    for document in documents:
        if not required <= set(document) or set(document) - required - {"backup_restore", "original_available"}:
            _fail("正文文档字段缺失或不受支持；原始字节不能包含在备份中。")
        pair = document.get("run_id"), document.get("source_id")
        if pair not in source_pairs:
            _fail("正文文档未关联到本研究的文献来源。")
        try:
            filename = literature.paper_filename(document.get("filename"))
        except ValueError:
            _fail("正文文件名无效或包含路径。")
        suffix = Path(filename).suffix.lower()
        if suffix not in media or document.get("media_type") != media[suffix]:
            _fail("正文文件类型与文件名不一致或不受支持。")
        if (type(document.get("byte_count")) is not int
                or not 1 <= document["byte_count"] <= literature.PAPER_MAX_BYTES):
            _fail("正文原始文件大小超出限制。")
        for field in ("file_sha256", "text_sha256"):
            if not isinstance(document.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", document[field]):
                _fail("正文哈希格式无效。")
        association = pair + (document["file_sha256"],)
        if association in associations:
            _fail("正文文档包含重复的来源与文件关联。")
        associations.add(association)
        if document.get("origin") != "user_imported_local_file":
            _fail("正文文档来源标记无效。")
        if "original_available" in document and type(document["original_available"]) is not bool:
            _fail("正文原件可用性字段无效。")
        _timestamp(document.get("created_at"), "正文导入时间")
        extraction = document.get("extraction")
        expected_kind = "pypdf_text" if suffix == ".pdf" else "utf8_text"
        if (not isinstance(extraction, dict) or set(extraction) != {"kind", "version"}
                or extraction.get("kind") != expected_kind):
            _fail("正文提取方式与文件类型不一致。")
        _text(extraction.get("version"), "正文提取器版本", 80)
        pages = _objects(document.get("pages"), "正文页")
        if not 1 <= len(pages) <= literature.PAPER_MAX_PAGES:
            _fail("正文页数超出限制。")
        texts = []
        for number, page in enumerate(pages, 1):
            if set(page) != {"number", "text", "text_sha256"} or type(page.get("number")) is not int or page["number"] != number:
                _fail("正文页码应从 1 开始且连续、唯一。")
            text = page.get("text")
            if not isinstance(text, str) or len(text) > literature.PAPER_MAX_CHARACTERS:
                _fail("正文页文本无效或超过字符上限。")
            if any((ord(char) < 32 and char not in "\n\r\t\f") or ord(char) == 127 for char in text):
                _fail("正文页文本包含无效控制字符。")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != page.get("text_sha256"):
                _fail("正文页文本完整性校验失败。")
            texts.append(text)
        characters = sum(map(len, texts))
        total += characters
        if not any(text.strip() for text in texts) or characters > literature.PAPER_MAX_CHARACTERS:
            _fail("正文未含可读文字或超过单份字符上限。")
        digest = literature.paper_text_sha256(pages)
        if digest != document["text_sha256"]:
            _fail("正文整篇提取文本完整性校验失败。")
    if total > literature.PROJECT_MAX_CHARACTERS:
        _fail("本研究的正文提取文本超过总字符上限。")


def _document_anchor(project, run_id, source_id, anchor):
    import literature_service
    try:
        literature_service.validate_document_anchor(project, run_id, source_id, anchor)
    except (ValueError, TypeError, KeyError, AttributeError):
        _fail("阅读摘录未精确关联到所属来源的正文页文本。")


def _map_document_anchors(project, value):
    """Check saved current/history/proposal snapshots without re-running a model."""
    if not isinstance(value, dict):
        return
    for topic in value.get("topics", []):
        for evidence in topic.get("evidence", []):
            snapshot = evidence.get("note_snapshot")
            if isinstance(snapshot, dict) and "document_anchor" in snapshot:
                import workflow
                expected_source = "lit_" + workflow._component(snapshot.get("run_id")) + "_" + workflow._component(snapshot.get("source_id"))
                if evidence.get("source_id") != expected_source:
                    _fail("候选正文摘录绑定到了其他文献来源。")
                _document_anchor(project, snapshot.get("run_id"), snapshot.get("source_id"), snapshot["document_anchor"])


def _validate_backup_project(project):
    """Validate the saved-record contract while retaining legacy optional fields."""
    if not isinstance(project, dict):
        _fail("研究记录应为对象。")
    _identifier(project.get("id"), "研究")
    _text(project.get("title"), "研究标题", 1000)
    for field in ("created_at", "updated_at"):
        _timestamp(project.get(field), field)
    record = project.get("direction_map")
    try:
        errors = engine.validate(record, allow_saved_unbound_evidence=True)
    except (TypeError, ValueError, KeyError, AttributeError):
        _fail("课题地图结构不完整。")
    if errors:
        _fail("课题地图未通过保存契约校验（" + str(errors[0])[:220] + "）。")
    if project.get("stage") != record["stage"]:
        _fail("研究阶段与课题地图不一致。")
    form = project.get("intake_form")
    if not isinstance(form, dict):
        _fail("研究简报缺失。")
    _text(form.get("initial_idea"), "初始想法", 3000)
    if (form.get("goal") not in engine.GOALS or form.get("resources") not in engine.RESOURCES
            or form.get("time_horizon") not in engine.HORIZONS
            or not isinstance(form.get("context", ""), str) or len(form.get("context", "")) > 6000):
        _fail("研究简报选项或内容无效。")
    for event in _objects(project.get("history"), "研究历史", identified=True):
        _text(event.get("action"), "历史操作", 200)
        _text(event.get("summary"), "历史说明", 20000)
        _timestamp(event.get("created_at"), "历史时间")
    searches = _objects(project.get("literature_searches", []), "文献记录", identified=True)
    source_pairs = set()
    for run in searches:
        if "schema_version" in run:
            import literature_service
            if literature_service.validate(run):
                _fail("文献检索记录未通过保存契约校验。")
        for source in _objects(run.get("sources"), "文献来源", identified=True):
            _text(source.get("title"), "文献标题", 3000)
            source_pairs.add((run["id"], source["id"]))
        for entry in _objects(run.get("search_log"), "文献请求记录", identified=True):
            _text(entry.get("query"), "检索词", 300)
            _text(entry.get("provider"), "检索服务", 100)
    _paper_documents(project, source_pairs)
    _map_document_anchors(project, record)
    for note in _objects(project.get("reading_notes", []), "阅读笔记", identified=True):
        if (note.get("run_id"), note.get("source_id")) not in source_pairs:
            _fail("阅读笔记未关联到本研究的文献来源。")
        _text(note.get("content"), "阅读摘录", 6000)
        _text(note.get("locator"), "阅读定位", 2000)
        if note.get("level") not in ("metadata_checked", "abstract_checked", "fulltext_checked") or type(note.get("confirmed")) is not bool:
            _fail("阅读笔记的范围或确认状态无效。")
        if "document_anchor" in note:
            _document_anchor(project, note["run_id"], note["source_id"], note["document_anchor"])
    for key in ("model_calls", "experiments", "direction_versions", "lean_attempts", "lean_runs"):
        entries = _objects(project.get(key, []), key, identified=True)
        if key == "lean_runs":
            for entry in entries:
                context = entry.get("context")
                if not isinstance(context, dict) or context.get("project_id") != project["id"]:
                    _fail("形式核验记录的研究关联不一致。")
                if not {"card_version", "card_sha256"} <= set(context) or not isinstance(entry.get("verification"), dict):
                    _fail("形式核验记录不完整。")
        if key == "lean_attempts":
            for entry in entries:
                if "context" in entry:
                    _owned(entry["context"], project["id"], "核验预约上下文")
        if key == "direction_versions":
            for entry in entries:
                try:
                    if engine.validate(entry.get("direction_map"), allow_saved_unbound_evidence=True):
                        _fail("历史课题地图未通过保存契约校验。")
                    _map_document_anchors(project, entry.get("direction_map"))
                except (TypeError, KeyError, AttributeError):
                    _fail("历史课题地图结构不完整。")
    for key in ("workflow", "theory"):
        if key not in project:
            continue
        state = project[key]
        if not isinstance(state, dict):
            _fail(key + "应为对象。")
        proposals = _objects(state.get("proposals", []), key + "提议历史", identified=True)
        pending = state.get("pending_proposal")
        if pending is not None:
            if not isinstance(pending, dict) or pending.get("id") not in {item["id"] for item in proposals}:
                _fail(key + "待审阅提议与历史关联不一致。")
            required = ({"payload", "base_map", "diff", "context_sha256"} if key == "workflow"
                        else {"revised_card", "proof_routes", "revision_reason", "diff", "context_fingerprint"})
            if not required <= set(pending):
                _fail(key + "待审阅提议内容不完整。")
        if key == "workflow":
            for proposal in proposals:
                for field in ("base_map", "payload"):
                    _map_document_anchors(project, proposal.get(field))
            if pending:
                for field in ("base_map", "payload"):
                    _map_document_anchors(project, pending.get(field))
            for field in ("pending_answers", "pending_questions"):
                if not isinstance(state.get(field, {}), dict):
                    _fail("科研流程输入结构无效。")
            for field in ("suggested_queries", "provided_materials", "skill_versions"):
                if not isinstance(state.get(field, []), list):
                    _fail("科研流程资料结构无效。")
        else:
            # Reuse only pure saved-data readers; these never contact a model,
            # registry, original file or compiler. Do not call apply/validate
            # generated-payload functions, which would change legacy meaning.
            import theory
            if type(state.get("card_version", 0)) is not int or state.get("card_version", 0) < 0:
                _fail("数学问题卡版本无效。")
            if state.get("card") is not None:
                try:
                    theory._card(state["card"])
                except (TypeError, ValueError, KeyError, AttributeError):
                    _fail("数学问题卡结构无效。")
            for field in ("card_versions", "proof_routes", "search_leads", "checking_records", "review_cases",
                          "failure_records", "rejected_outputs"):
                if not isinstance(state.get(field, []), list):
                    _fail("理论工作区历史结构无效。")
            for item in _objects(state.get("checking_records", []), "理论核查记录", identified=True):
                _owned(item, project["id"], "理论核查记录")
                _owned(item.get("target"), project["id"], "核查目标")
                _owned(item.get("context_basis"), project["id"], "核查原始上下文")
                if item.get("case_snapshot") is not None:
                    _owned(item["case_snapshot"], project["id"], "核查案例快照")
            for field in ("review_cases", "failure_records"):
                for item in _objects(state.get(field, []), field):
                    _owned(item, project["id"], field)
            for field in ("proposals", "failure_records", "rejected_outputs"):
                for item in _objects(state.get(field, []), field):
                    feedback = item.get("feedback_context")
                    if feedback is not None:
                        if not isinstance(feedback, dict):
                            _fail("历史反馈上下文结构无效。")
                        _owned(feedback.get("snapshot"), project["id"], "历史反馈快照")
            try:
                theory.workspace(project)
                theory.render_markdown(project)
            except (TypeError, ValueError, KeyError, AttributeError, IndexError):
                _fail("理论工作区或证据关联不完整。")
    if project.get("research_plan") is not None:
        _plan(project["research_plan"], "当前行动计划")
    for plan in _objects(project.get("research_plan_history", []), "行动计划历史", identified=True):
        _plan(plan, "历史行动计划")


def validate_project(project):
    """Turn any malformed saved-data shape into a Chinese, non-partial failure."""
    try:
        return _validate_backup_project(project)
    except (TypeError, KeyError, AttributeError, IndexError, OverflowError):
        _fail("研究记录的字段或关联结构无效，请使用未修改的备份文件。")


def _canonical(projects):
    return json.dumps(projects, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def create_backup(projects):
    if len(projects) > MAX_PROJECTS:
        _fail("单份备份最多包含 1000 个研究。")
    # UI availability is derived from the independent BLOB table. It must never
    # become a portable claim that an original exists on the destination.
    _shape(projects)
    projects = copy.deepcopy(projects)
    for project in projects:
        if not isinstance(project, dict) or not isinstance(project.get("paper_documents", []), list):
            continue
        for document in project.get("paper_documents", []):
            if isinstance(document, dict):
                document.pop("original_available", None)
    _shape(projects)
    for project in projects:
        validate_project(project)
    document = {"format": FORMAT, "version": VERSION, "created_at": engine.now(),
                "project_count": len(projects), "projects_sha256": hashlib.sha256(_canonical(projects)).hexdigest(),
                "scope": "project_records_only", "projects": projects}
    content = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(content) > MAX_BYTES:
        _fail("整库 JSON 超过 20 MiB，暂不能通过此入口导出。")
    return content


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("JSON 含重复字段，无法确定真实记录。")
        value[key] = item
    return value


def parse_backup(content):
    if len(content) > MAX_BYTES:
        _fail("文件超过 20 MiB。")
    try:
        document = json.loads(content.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: _fail("JSON 含无效数值。"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("文件不是有效的 UTF-8 JSON 备份。")
    _shape(document)
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        _fail("请使用“下载工作空间备份”生成的文件，单个研究导出不能作为整库备份。")
    if type(document.get("version")) is not int or document["version"] != VERSION:
        _fail("备份版本暂不受支持，请在兼容版本的工作台恢复。")
    if set(document) != {"format", "version", "created_at", "project_count", "projects_sha256", "scope", "projects"}:
        _fail("备份字段缺失或不受支持。")
    _timestamp(document.get("created_at"), "备份创建时间")
    projects = _objects(document.get("projects"), "研究清单", identified=True)
    if len(projects) > MAX_PROJECTS or type(document.get("project_count")) is not int or document["project_count"] != len(projects):
        _fail("研究数量不一致或超过 1000 个。")
    if document.get("scope") != "project_records_only":
        _fail("备份范围暂不受支持。")
    digest = hashlib.sha256(_canonical(projects)).hexdigest()
    if document.get("projects_sha256") != digest:
        _fail("记录完整性校验失败，文件可能损坏或被修改。")
    for project in projects:
        validate_project(project)
    return document


def restored_projects(document):
    """Retain identities and declarations, never resume requests from a snapshot."""
    stamp, restored = engine.now(), copy.deepcopy(document["projects"])
    reason = "从备份恢复；外部快照未重新核验，旧提议需重新生成后审阅。"
    for project in restored:
        project["backup_restore"] = {"restored_at": stamp, "backup_created_at": document["created_at"],
            "projects_sha256": document["projects_sha256"], "origin": "imported_snapshot_not_reverified",
            "status": "imported_external_snapshot", "revalidation_status": "not_revalidated",
            "original_files_included": False, "caveat": WARNINGS[1] + WARNINGS[2]}
        # These records can be exported individually, outside their project
        # envelope. Carry the import boundary with each existing record while
        # leaving original result/status fields and identifier links intact.
        provenance = project["backup_restore"]
        for field in ("literature_searches", "model_calls", "experiments", "lean_runs", "paper_documents"):
            for item in project.get(field, []):
                item["backup_restore"] = copy.deepcopy(provenance)
                if field == "paper_documents":
                    item.pop("original_available", None)
        for field in ("checking_records", "failure_records"):
            for item in project.get("theory", {}).get(field, []):
                item["backup_restore"] = copy.deepcopy(provenance)
                for fact in item.get("artifact_reads", []):
                    fact["backup_restore"] = copy.deepcopy(provenance)
        for key, fingerprint in (("workflow", "context_sha256"), ("theory", "context_fingerprint")):
            state = project.get(key, {})
            pending = state.get("pending_proposal")
            if pending:
                pending["backup_original_context"] = pending.get(fingerprint)
                pending[fingerprint] = None
                pending["backup_restore_reason"] = reason
        plan = project.get("research_plan")
        if plan:
            plan.update(status="paused", paused_at=stamp, backup_restore_reason=reason)
            for action in plan["actions"]:
                if action["status"] == "running":
                    action.update(status="interrupted_result_unknown", finished_at=stamp,
                                  summary="备份中的运行未在本机续跑；本次结果未知。")
        for attempt in project.get("lean_attempts", []):
            if attempt.get("state") == "running":
                attempt.update(state="interrupted_result_unknown", finished_at=stamp)
        engine.add_event(project, "backup_restored", reason)
    return restored
