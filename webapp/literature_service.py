"""Bounded public metadata searches; no model, credential or database access."""

import importlib.util
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx

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
PAPER_MAX_BYTES = 8 * 1024 * 1024
PAPER_MAX_PAGES = 120
PAPER_MAX_CHARACTERS = 400000
PROJECT_MAX_DOCUMENTS = 12
PROJECT_MAX_CHARACTERS = 2000000
PAPER_PDF_TIMEOUT = 15
PAPER_PDF_MAX_OUTPUT = 3 * 1024 * 1024
PAPER_PDF_PARSER_VERSION = "6.21.2"


class PaperExtractionUnavailable(Exception):
    pass


class PaperExtractionTimeout(Exception):
    pass


def paper_filename(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 180
            or value != value.strip() or any(char in value for char in "/\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("请提供不含路径或控制字符、长度不超过 180 字的文件名。")
    extension = Path(value).suffix.lower()
    if extension not in (".pdf", ".txt", ".md"):
        raise ValueError("正文仅支持有文字层的 PDF、UTF-8 TXT 或 Markdown 文件。")
    return value


def document_source(project, run_id, source_id):
    run = next((item for item in project.get("literature_searches", []) if item["id"] == run_id), None)
    source = next((item for item in run.get("sources", []) if item["id"] == source_id), None) if run else None
    if source is None:
        raise ValueError("请在本研究已有的文献题录下导入正文；检索轮次或来源不匹配。")
    return source


def paper_text_sha256(pages):
    """Hash an unambiguous UTF-8 JSON array of exact page texts, in page order."""
    content = json.dumps([page["text"] for page in pages], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _paper_pages(texts):
    if not isinstance(texts, list) or not 1 <= len(texts) <= PAPER_MAX_PAGES:
        raise ValueError("单份正文最多支持 120 页，未保存文件。")
    if any(not isinstance(text, str) for text in texts):
        raise ValueError("正文提取结果无效，未保存文件。")
    # HTML text nodes and selection APIs use LF line breaks. Normalize before
    # hashing, while retaining the uploaded bytes and their separate digest.
    texts = [text.replace("\r\n", "\n").replace("\r", "\n") for text in texts]
    if sum(len(text) for text in texts) > PAPER_MAX_CHARACTERS:
        raise ValueError("单份正文最多提取 40 万字符，未保存文件。")
    if not any(text.strip() for text in texts):
        raise ValueError("文件没有可提取文字；扫描版 PDF 暂不支持 OCR，请提供文字版 PDF 或 UTF-8 文本。")
    if any(any((ord(char) < 32 and char not in "\n\r\t\f") or ord(char) == 127
               or 0xD800 <= ord(char) <= 0xDFFF for char in text) for text in texts):
        raise ValueError("提取文本含无效控制字符或 Unicode 内容，未保存文件。")
    return [{"number": index + 1, "text": text,
             "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()} for index, text in enumerate(texts)]


def _extract_pdf_in_worker(payload):
    """A fixed subprocess target, bounded wall time and output, no user paths."""
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    # TemporaryFile is not a user-controlled filesystem target. It prevents a
    # malformed parser response from growing the parent process's capture RAM.
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--extract-paper-pdf"],
                input=payload, stdout=output, stderr=subprocess.DEVNULL, timeout=PAPER_PDF_TIMEOUT,
                env=environment, cwd=str(Path(__file__).resolve().parent), shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise PaperExtractionTimeout("PDF 提取超过 15 秒，已停止；未保存文件，请改用 UTF-8 文本或较小的文字版 PDF。") from None
        except OSError:
            raise PaperExtractionUnavailable("PDF 提取进程暂时无法启动；可先使用 UTF-8 TXT 或 Markdown。") from None
        if output.tell() > PAPER_PDF_MAX_OUTPUT:
            raise ValueError("PDF 提取输出超过上限，未保存文件。")
        output.seek(0)
        try:
            result = json.loads(output.read(PAPER_PDF_MAX_OUTPUT + 1).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ValueError("PDF 提取未返回有效内容，未保存文件。") from None
    if not isinstance(result, dict):
        raise ValueError("PDF 提取结果无效，未保存文件。")
    if result.get("status") == "dependency_unavailable":
        raise PaperExtractionUnavailable("PDF 解析依赖尚未就绪；请更新本地环境，或先使用 UTF-8 TXT 或 Markdown。")
    errors = {"encrypted": "暂不支持加密 PDF，请提供未加密的文字版或 UTF-8 文本。",
              "page_limit": "单份 PDF 最多支持 120 页，未保存文件。",
              "content_limit": "PDF 内容流或提取文字超过安全上限，未保存文件。",
              "invalid": "PDF 无法解析，未保存文件；请提供有效的文字版 PDF 或 UTF-8 文本。"}
    if completed.returncode != 0 or result.get("status") != "success":
        raise ValueError(errors.get(result.get("status"), errors["invalid"]))
    if result.get("version") != PAPER_PDF_PARSER_VERSION:
        raise PaperExtractionUnavailable("PDF 解析器版本与当前应用不一致，请更新本地环境后重试。")
    return _paper_pages(result.get("texts"))


def extract_paper_document(payload, filename, run_id, source_id):
    """Parse user-assigned local content; it never marks a source as read."""
    filename = paper_filename(filename)
    if not isinstance(payload, bytes) or not 0 < len(payload) <= PAPER_MAX_BYTES:
        raise ValueError("正文文件应为非空文件且不超过 8 MiB。")
    extension = Path(filename).suffix.lower()
    if extension == ".pdf":
        if not payload.lstrip()[:1024].startswith(b"%PDF-"):
            raise ValueError("文件没有有效 PDF 标识，未保存；请检查文件格式。")
        pages = _extract_pdf_in_worker(payload)
        media_type, extraction = "application/pdf", {"kind": "pypdf_text", "version": PAPER_PDF_PARSER_VERSION}
    else:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError("文本文件不是有效 UTF-8，请以 UTF-8 编码另存后重试。") from None
        pages = _paper_pages([text])
        media_type = "text/markdown" if extension == ".md" else "text/plain"
        extraction = {"kind": "utf8_text", "version": "1"}
    return {"id": uuid.uuid4().hex, "run_id": run_id, "source_id": source_id,
            "filename": filename, "media_type": media_type, "byte_count": len(payload),
            "file_sha256": hashlib.sha256(payload).hexdigest(), "text_sha256": paper_text_sha256(pages),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "pages": pages, "extraction": extraction, "origin": "user_imported_local_file"}


def validate_document_anchor(project, run_id, source_id, anchor):
    """Validate exact saved text offsets, never a claim of publisher authenticity."""
    fields = {"document_id", "page", "start", "end", "quote", "file_sha256", "page_sha256"}
    if not isinstance(anchor, dict) or set(anchor) != fields:
        raise ValueError("正文摘录定位字段不完整或包含未知字段，请重新选取原文。")
    if (not isinstance(anchor["document_id"], str) or type(anchor["page"]) is not int
            or type(anchor["start"]) is not int or type(anchor["end"]) is not int
            or not isinstance(anchor["quote"], str) or not anchor["quote"].strip()
            or len(anchor["quote"]) > 4000):
        raise ValueError("正文摘录应为 1—4000 字符，并包含有效的页码与字符位置。")
    document = next((item for item in project.get("paper_documents", []) if item.get("id") == anchor["document_id"]), None)
    if document is None or document.get("run_id") != run_id or document.get("source_id") != source_id:
        raise ValueError("所选正文不属于本研究的这轮文献来源，未保存阅读笔记。")
    document_source(project, run_id, source_id)
    pages = document.get("pages")
    if not isinstance(pages, list) or not 1 <= anchor["page"] <= len(pages):
        raise ValueError("正文摘录页码无效，请重新选择。")
    page = pages[anchor["page"] - 1]
    text = page.get("text") if isinstance(page, dict) else None
    if (not isinstance(text, str) or page.get("number") != anchor["page"]
            or not isinstance(anchor["file_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", anchor["file_sha256"])
            or not isinstance(anchor["page_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", anchor["page_sha256"])
            or document.get("file_sha256") != anchor["file_sha256"]
            or page.get("text_sha256") != anchor["page_sha256"]
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != anchor["page_sha256"]):
        raise ValueError("正文文件或页文本的指纹已变化，请重新打开正文后选取摘录。")
    if not 0 <= anchor["start"] < anchor["end"] <= len(text) or text[anchor["start"]:anchor["end"]] != anchor["quote"]:
        raise ValueError("正文摘录与保存页的字符位置不一致，请重新选取；未保存阅读笔记。")
    return dict(anchor)


def document_anchor_locator(anchor, filename=None):
    label = "《" + paper_filename(filename) + "》" if filename is not None else anchor["document_id"]
    return (f"导入正文{label} · 第 {anchor['page']} 页 · "
            f"字符 {anchor['start']}—{anchor['end']}（从0计，右端不含）")


def _paper_pdf_worker():
    """The only child entry point. No model, network, shell or user file paths."""
    import logging
    logging.disable(logging.CRITICAL)
    try:
        import pypdf
    except ImportError:
        return {"status": "dependency_unavailable"}
    if pypdf.__version__ != PAPER_PDF_PARSER_VERSION:
        return {"status": "dependency_unavailable"}
    try:
        payload = sys.stdin.buffer.read(PAPER_MAX_BYTES + 1)
        if not payload or len(payload) > PAPER_MAX_BYTES:
            return {"status": "invalid"}
        # Restrict stream decompression in the child; a parser failure cannot
        # return partial page text as a successful document.
        pypdf.overwrite_configuration(
            maximum_declared_stream_length=PAPER_MAX_BYTES,
            array_based_stream_maximum_output_length=4 * 1024 * 1024,
            zlib_maximum_output_length=4 * 1024 * 1024,
            lzw_maximum_output_length=4 * 1024 * 1024,
            run_length_maximum_output_length=4 * 1024 * 1024,
            brotli_maximum_output_length=4 * 1024 * 1024,
            image_maximum_buffer_size=4 * 1024 * 1024,
            stream_decoding_work_maximum_length=16 * 1024 * 1024,
            stream_parsing_work_maximum_count=500000,
            page_tree_maximum_entries=1000,
            xform_maximum_invocations_per_extraction=100,
            jbig2dec_binary=None,
        )
        # PDF actions and hyperlinks are data, never instructions. The parser
        # has no reason to access a network or launch an external decoder.
        def deny_external_execution(event, arguments):
            if event in ("subprocess.Popen", "os.system", "os.exec", "socket.connect", "socket.getaddrinfo"):
                raise RuntimeError("external work disabled in PDF extraction")
        sys.addaudithook(deny_external_execution)
        reader = pypdf.PdfReader(io.BytesIO(payload), strict=False)
        if reader.is_encrypted:
            return {"status": "encrypted"}
        if not 1 <= len(reader.pages) <= PAPER_MAX_PAGES:
            return {"status": "page_limit"}
        texts, characters, stream_bytes = [], 0, 0
        for page in reader.pages:
            contents = page.get_contents()
            content = contents.get_data() if contents is not None else b""
            stream_bytes += len(content)
            if len(content) > 4 * 1024 * 1024 or stream_bytes > 16 * 1024 * 1024:
                return {"status": "content_limit"}
            text = page.extract_text() or ""
            characters += len(text)
            if characters > PAPER_MAX_CHARACTERS:
                return {"status": "content_limit"}
            texts.append(text)
        return {"status": "success", "version": pypdf.__version__, "texts": texts}
    except Exception:
        return {"status": "invalid"}


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


def parse_reference(value):
    """Accept identifiers, never a user-chosen fetch target."""
    value = value.strip()
    if re.match(r"^(?:(?:www\.)?arxiv\.org/|(?:dx\.)?doi\.org/)", value, flags=re.I):
        value = "https://" + value
    if not value or len(value) > 300 or any(c.isspace() for c in value):
        raise ValueError("请粘贴一条 DOI、arXiv 编号或对应的官方链接。")
    if value.lower().startswith(("http://", "https://")):
        parts = urlsplit(value)
        if parts.username or parts.password or parts.port or parts.query or parts.fragment:
            raise ValueError("文献链接不能包含账号、端口、查询参数或片段。")
        if parts.hostname in ("doi.org", "dx.doi.org"):
            value = unquote(parts.path.lstrip("/"))
        elif parts.hostname in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
            match = re.fullmatch(r"/(?:abs|pdf)/(.+?)(?:\.pdf)?/?", parts.path)
            if not match:
                raise ValueError("请使用 arXiv 的摘要或 PDF 链接。")
            value = "arxiv:" + unquote(match[1])
        else:
            raise ValueError("仅支持 doi.org 和 arxiv.org 官方文献链接。")
    doi = _client.normalize_doi(value)
    if doi and re.fullmatch(r"10\.\d{4,9}/[^\s?#\\]+", doi):
        return {"provider": "crossref", "identifier": doi, "key": "doi:" + doi}
    arxiv = _client.normalize_arxiv_id(value, keep_version=True)
    if arxiv:
        return {"provider": "arxiv", "identifier": arxiv, "key": "arxiv:" + arxiv}
    raise ValueError("未识别到 DOI 或 arXiv 编号，请检查后重试。")


def import_reference(idea, reference):
    """Resolve one exact ID, preserving metadata bytes/version, with no retries."""
    global _last_arxiv_finished
    parsed = parse_reference(reference)
    provider, identifier = parsed["provider"], parsed["identifier"]
    url = ("https://api.crossref.org/works/" + quote(identifier, safe="") if provider == "crossref"
           else "https://export.arxiv.org/api/query?" + urlencode({"id_list": identifier, "max_results": 1}))
    record = _failed(idea, parsed["key"], provider, 1, "导入尚未完成。")
    record["id"] = uuid.uuid4().hex
    record["import_reference"] = dict(parsed)
    log = record["search_log"][0]
    acquired = provider != "arxiv" or _arxiv_lock.acquire(timeout=1)
    try:
        if not acquired:
            raise ValueError("arXiv 正在处理其他请求，本次没有请求；请稍后重试。")
        if provider == "arxiv":
            time.sleep(max(0, 3 - (time.monotonic() - _last_arxiv_finished)))
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log.update(attempts=1, searched_at=stamp)
        record["provenance"]["live_search_performed"] = True
        chunks, size = [], 0
        # The target is constructed above; redirects and inherited credentials/proxies are disabled.
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url, headers={"User-Agent": "YanzhengLiteratureImport/1.0",
                               "Accept": "application/atom+xml" if provider == "arxiv" else "application/json"}) as response:
                if response.status_code == 404:
                    log.update(status="empty", error=None)
                    record["limitations"] = ["官方服务未找到此编号；不代表论文不存在，也可能不由该服务登记。"]
                    record["next_action"] = "核对编号、版本和登记机构后再导入。"
                    return record
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ValueError("元数据响应超过 2 MiB 上限，已停止读取。")
                    chunks.append(chunk)
        payload = b"".join(chunks)
        if provider == "crossref":
            item = json.loads(payload).get("message")
            if not isinstance(item, dict) or _client.normalize_doi(item.get("DOI")) != identifier:
                raise ValueError("返回的 DOI 与请求不一致。")
            sources, warnings = _client.parse_crossref(json.dumps({"message": {"items": [item]}}), "Q001", url, stamp)
        else:
            sources, warnings = _client.parse_arxiv(payload, "Q001", url, stamp)
            for source in sources:
                versions = source.get("versions", [])
                resolved = versions[0] if versions else None
                if (source.get("identifiers", {}).get("arxiv_id") != _client.normalize_arxiv_id(identifier)
                        or (re.search(r"v\d+$", identifier) and resolved != identifier)):
                    raise ValueError("返回的 arXiv 编号或版本与请求不一致。")
        if len(sources) > 1:
            raise ValueError("精确编号返回了多条记录。")
        for source in sources:
            source["id"] = "S001"
            source["import_metadata"] = {"requested_reference": parsed["key"],
                "response_sha256": hashlib.sha256(payload).hexdigest(), "retrieved_at": stamp,
                "scope": "official_metadata_response_only", "versions": source.get("versions", []),
                "caveat": "仅核对官方题录；此哈希不是论文全文指纹，不表示已经阅读或认证原文。"}
        record.update(sources=sources, limitations=["精确导入仅获取官方元数据；摘要不会自动成为已读证据。", *warnings],
                      next_action="打开原文核对版本，记录实际读到的位置与摘录，再用于修订方向。")
        log.update(status="success" if sources else "empty", result_count=len(sources), error=None)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError) as error:
        # Provider content and network errors are untrusted; expose only bounded classifications.
        message = ("官方元数据服务请求失败，请核对网络或编号后手动重试。" if isinstance(error, httpx.HTTPError)
                   else "官方元数据未通过编号、版本或格式检查；未保存为有效来源。")
        if not acquired:
            message = "arXiv 正在处理其他请求，本次没有请求；请稍后重试。"
        log.update(status="failed", error=message, result_count=0)
        record.update(sources=[], limitations=[message], next_action="已有资料保留，请核对后手动重试。")
    finally:
        if provider == "arxiv" and acquired:
            _last_arxiv_finished = time.monotonic()
            _arxiv_lock.release()
    if validate(record):
        raise ValueError("导入记录未通过校验。")
    return record


if __name__ == "__main__":
    if sys.argv[1:] != ["--extract-paper-pdf"]:
        raise SystemExit(2)
    worker_result = _paper_pdf_worker()
    output = json.dumps(worker_result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(output) > PAPER_PDF_MAX_OUTPUT:
        output = b'{"status":"content_limit"}'
    sys.stdout.buffer.write(output)
    raise SystemExit(0 if worker_result.get("status") == "success" else 1)
