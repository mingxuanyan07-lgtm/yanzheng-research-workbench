"""Bounded local paper imports and exact Unicode anchors on disposable databases."""

import concurrent.futures
import copy
import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import engine
import literature_service as literature
import storage
from app import create_app
from test_model_api import FakeModel
from test_research_journey import search_record


def pdf_bytes(texts=("A real text layer.",), *, encrypted=False):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    for text in texts:
        page = writer.add_blank_page(width=200, height=200)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font)})})
        if text:
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(("BT /F1 12 Tf 10 100 Td (" + escaped + ") Tj ET").encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("not-for-import")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class PaperDocumentsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="paper-documents-")
        self.database = Path(self.directory.name) / "research.db"
        self.model = FakeModel()
        self.app = create_app(self.database, model_client=self.model,
                              lean_client=lambda *_: self.fail("No compiler is permitted"))
        self.client = TestClient(self.app)
        self.project = self.client.post("/api/projects", json={"initial_idea": "本地正文定位"}).json()
        self.base = "/api/projects/" + self.project["id"]
        self.run = search_record("本地正文定位", "paper text")
        self.app.state.store.update(self.project["id"], lambda current: engine.add_literature_search(current, self.run))
        self.text = "第一行😀引用。\nSecond line with evidence.\n末行。"

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def upload(self, content=None, filename="文献正文.txt", *, base=None, run_id=None, source_id="S001", confirmed="true", headers=None):
        return self.client.post((base or self.base) + "/documents", params={
            "run_id": run_id or self.run["id"], "source_id": source_id,
            "filename": filename, "binding_confirmed": confirmed},
            content=content if content is not None else self.text.encode("utf-8"),
            headers=headers or {"Content-Type": "application/octet-stream"})

    def saved(self):
        return self.app.state.store.get(self.project["id"])

    def blob_count(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT COUNT(*) FROM paper_document_originals").fetchone()[0]

    def anchor(self, document, quote="😀引用"):
        text = document["pages"][0]["text"]
        start = text.index(quote)
        return {"document_id": document["id"], "page": 1, "start": start, "end": start + len(quote),
                "quote": quote, "file_sha256": document["file_sha256"],
                "page_sha256": document["pages"][0]["text_sha256"]}

    def note(self, anchor, **changes):
        return self.client.post(self.base + "/reading-notes", json={
            "run_id": self.run["id"], "source_id": "S001", "level": "fulltext_checked",
            "content": "独立注释：仅支持所选段落范围。", "confirmed": True,
            "document_anchor": anchor, **changes})

    def test_utf8_bom_markdown_and_hashes_are_exact_without_reading_upgrade(self):
        payload = b"\xef\xbb\xbf" + self.text.encode("utf-8")
        response = self.upload(payload, "论文.md")
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()
        document = project["paper_documents"][0]
        self.assertEqual(document["pages"][0]["text"], self.text)
        self.assertEqual(document["file_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(document["text_sha256"], hashlib.sha256(json.dumps([self.text], ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest())
        self.assertEqual(document["pages"][0]["text_sha256"], hashlib.sha256(self.text.encode("utf-8")).hexdigest())
        self.assertEqual(document["extraction"], {"kind": "utf8_text", "version": "1"})
        self.assertEqual(document["media_type"], "text/markdown")
        self.assertEqual(document["origin"], "user_imported_local_file")
        self.assertTrue(document["original_available"])
        self.assertNotIn("original_available", self.saved()["paper_documents"][0])
        self.assertFalse(project.get("reading_notes"))
        self.assertEqual(project["literature_searches"][0]["sources"][0]["verification"], "metadata_checked")
        self.assertEqual(self.model.calls, 0)
        self.assertEqual(self.blob_count(), 1)

    def test_original_download_is_exact_attachment_and_scoped(self):
        document = self.upload().json()["paper_documents"][0]
        url = self.base + "/documents/" + document["id"] + "/original"
        response = self.client.get(url)
        self.assertEqual(response.content, self.text.encode("utf-8"))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])
        other = self.client.post("/api/projects", json={"initial_idea": "另一个研究"}).json()
        response = self.client.get("/api/projects/" + other["id"] + "/documents/" + document["id"] + "/original")
        self.assertEqual(response.status_code, 404)
        with TestClient(create_app(self.database, model_client=FakeModel())) as restarted:
            self.assertEqual(restarted.get(url).content, self.text.encode("utf-8"))

    def test_duplicate_upload_preserves_document_id_history_and_original(self):
        first = self.upload().json()
        second = self.upload(filename="别名.txt").json()
        self.assertEqual(second["paper_documents"][0]["id"], first["paper_documents"][0]["id"])
        self.assertEqual(second["history"], first["history"])
        self.assertEqual(second["updated_at"], first["updated_at"])
        self.assertEqual(len(second["paper_documents"]), 1)
        self.assertEqual(self.blob_count(), 1)

    def test_same_file_on_different_sources_has_distinct_bound_identity(self):
        first = self.upload().json()["paper_documents"][0]
        other = search_record("本地正文定位", "another paper")
        self.app.state.store.update(self.project["id"], lambda current: engine.add_literature_search(current, other))
        second = self.upload(run_id=other["id"]).json()["paper_documents"][1]
        self.assertEqual(first["file_sha256"], second["file_sha256"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.blob_count(), 2)

    def test_binding_confirmation_required_and_unknown_source_never_writes(self):
        for changes in ({"confirmed": "false"}, {"confirmed": ""}, {"source_id": "S999"}, {"run_id": "unknown"}):
            response = self.upload(**changes)
            self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.saved().get("paper_documents"))
        self.assertEqual(self.blob_count(), 0)

    def test_paths_extensions_invalid_utf8_empty_and_controls_rejected(self):
        for filename in ("../paper.txt", "C:\\paper.pdf", "paper\r\n.txt", "paper.html", " paper.txt", "a" * 181 + ".txt"):
            response = self.upload(filename=filename)
            self.assertIn(response.status_code, (400, 422), response.text)
        for payload in (b"\xff\xfeinvalid", b"", b" \n\t", b"some\x00binary"):
            response = self.upload(content=payload)
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.blob_count(), 0)
        self.assertFalse(self.saved().get("paper_documents"))

    def test_byte_limit_content_length_and_chunked_request_have_no_partial_writes(self):
        with patch.object(literature, "PAPER_MAX_BYTES", 8):
            self.assertEqual(self.upload(b"a" * 9).status_code, 413)
            self.assertEqual(self.upload(iter([b"aaaaa", b"bbbbb"])).status_code, 413)
        self.assertEqual(self.blob_count(), 0)

    def test_per_document_and_project_character_and_document_budgets(self):
        with patch.object(literature, "PAPER_MAX_CHARACTERS", 4):
            self.assertEqual(self.upload(b"12345").status_code, 400)
        with patch.object(literature, "PROJECT_MAX_DOCUMENTS", 2), patch.object(literature, "PROJECT_MAX_CHARACTERS", 8):
            self.assertEqual(self.upload(b"abcd").status_code, 200)
            self.assertEqual(self.upload(b"efghi").status_code, 400)
            self.assertEqual(self.upload(b"efgh").status_code, 200)
            self.assertEqual(self.upload(b"ijkl").status_code, 400)
        self.assertEqual(len(self.saved()["paper_documents"]), 2)
        self.assertEqual(self.blob_count(), 2)

    def test_metadata_write_failure_rolls_back_original_bytes_too(self):
        before = self.saved()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TRIGGER reject_document BEFORE UPDATE ON projects "
                               "BEGIN SELECT RAISE(ABORT, 'fixture'); END")
            connection.commit()
        response = self.upload()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.saved(), before)
        self.assertEqual(self.blob_count(), 0)

    def test_parallel_duplicate_store_attachment_inserts_only_once(self):
        payload = self.text.encode("utf-8")
        document = literature.extract_paper_document(payload, "same.txt", self.run["id"], "S001")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.app.state.store.attach_paper_document(self.project["id"], document, payload), range(2)))
        self.assertEqual(len(self.saved()["paper_documents"]), 1)
        self.assertEqual(self.blob_count(), 1)
        self.assertEqual(sum(item["action"] == "paper_document_imported" for item in self.saved()["history"]), 1)

    def test_real_text_pdf_has_pages_hashes_and_original_without_model_or_network(self):
        payload = pdf_bytes(("Page one text.", "Page two text."))
        with patch("literature_service.search", side_effect=AssertionError("No network")):
            response = self.upload(payload, "paper.pdf")
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()["paper_documents"][0]
        self.assertEqual(len(document["pages"]), 2)
        self.assertIn("Page one text.", document["pages"][0]["text"])
        self.assertIn("Page two text.", document["pages"][1]["text"])
        self.assertEqual(document["pages"][1]["number"], 2)
        self.assertEqual(document["extraction"], {"kind": "pypdf_text", "version": "6.21.2"})
        self.assertEqual(document["media_type"], "application/pdf")
        self.assertEqual(document["file_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(self.model.calls, 0)

    def test_pdf_blank_encrypted_malformed_and_over_page_limit_are_rejected(self):
        cases = (pdf_bytes(("",)), pdf_bytes(encrypted=True), b"%PDF-invalid", pdf_bytes([""] * 121), b"not pdf")
        for payload in cases:
            response = self.upload(payload, "paper.pdf")
            self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.saved().get("paper_documents"))
        self.assertEqual(self.blob_count(), 0)

    def test_pdf_timeout_missing_dependency_and_bad_worker_output_never_write(self):
        with patch("literature_service.subprocess.run", side_effect=subprocess.TimeoutExpired("fixed", 15)):
            response = self.upload(b"%PDF-fixture", "paper.pdf")
            self.assertEqual(response.status_code, 504, response.text)
        def worker(result):
            def execute(*args, **kwargs):
                kwargs["stdout"].write(json.dumps(result).encode("utf-8"))
                return SimpleNamespace(returncode=0)
            return execute
        for result, expected in (({"status": "dependency_unavailable"}, 503),
                                 ({"status": "success", "version": "wrong", "texts": ["x"]}, 503),
                                 ({"status": "success", "version": "6.21.2", "texts": [42]}, 400)):
            with patch("literature_service.subprocess.run", side_effect=worker(result)):
                response = self.upload(b"%PDF-fixture", "paper.pdf")
                self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(self.blob_count(), 0)

    def test_pdf_high_expansion_content_stream_is_rejected_before_saving(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data(b" " * (5 * 1024 * 1024))
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
        output = io.BytesIO()
        writer.write(output)
        self.assertLess(len(output.getvalue()), literature.PAPER_MAX_BYTES)
        response = self.upload(output.getvalue(), "compressed.pdf")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.saved().get("paper_documents"))
        self.assertEqual(self.blob_count(), 0)

    def test_pdf_javascript_stays_in_original_and_never_becomes_extracted_commands(self):
        writer = PdfWriter(io.BytesIO(pdf_bytes(("Text, without running actions.",))))
        writer.add_js("app.launchURL('https://example.invalid/should-not-be-requested');")
        output = io.BytesIO()
        writer.write(output)
        response = self.upload(output.getvalue(), "with-actions.pdf")
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()["paper_documents"][0]["pages"][0]
        self.assertIn("Text, without running actions.", page["text"])
        self.assertNotIn("launchURL", page["text"])
        self.assertEqual(self.model.calls, 0)

    def test_anchor_matches_unicode_codepoints_and_program_locator(self):
        document = self.upload().json()["paper_documents"][0]
        anchor = self.anchor(document)
        response = self.note(anchor, locator="客户端提供的错误位置")
        self.assertEqual(response.status_code, 200, response.text)
        note = response.json()["reading_notes"][0]
        self.assertEqual(note["document_anchor"], anchor)
        self.assertEqual(note["content"], "独立注释：仅支持所选段落范围。")
        self.assertEqual(note["locator"], literature.document_anchor_locator(anchor, document["filename"]))
        self.assertNotEqual(note["locator"], "客户端提供的错误位置")
        self.assertEqual(anchor["end"] - anchor["start"], 3)
        self.assertTrue(note["confirmed"])
        self.assertEqual(note["origin"], "user_attested")
        self.assertEqual(self.saved()["literature_searches"][0]["sources"][0]["verification"], "metadata_checked")

    def test_forged_anchor_bad_hash_range_quote_types_and_unknown_fields_never_write(self):
        document = self.upload().json()["paper_documents"][0]
        original = self.anchor(document)
        cases = [{**original, key: value} for key, value in (
            ("page", 0), ("page", True), ("page", 2), ("start", -1), ("start", True), ("end", 10000),
            ("quote", "假的摘录"), ("quote", ""), ("quote", " " * 3), ("quote", "x" * 4001),
            ("file_sha256", "0" * 64), ("page_sha256", "1" * 64), ("document_id", "not-owned"))]
        cases.append({**original, "untrusted": True})
        cases.append({key: value for key, value in original.items() if key != "end"})
        before = self.saved()
        for anchor in cases:
            response = self.note(anchor)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.saved(), before)
        response = self.note(original, confirmed=False)
        self.assertEqual(response.status_code, 422)

    def test_anchor_rejects_another_run_same_source_and_another_project(self):
        document = self.upload().json()["paper_documents"][0]
        anchor = self.anchor(document)
        other = search_record("本地正文定位", "other")
        self.app.state.store.update(self.project["id"], lambda current: engine.add_literature_search(current, other))
        self.assertEqual(self.note(anchor, run_id=other["id"]).status_code, 400)
        other_project = self.client.post("/api/projects", json={"initial_idea": "另一研究"}).json()
        self.app.state.store.update(other_project["id"], lambda current: engine.add_literature_search(current, self.run))
        response = self.client.post("/api/projects/" + other_project["id"] + "/reading-notes", json={
            "run_id": self.run["id"], "source_id": "S001", "level": "fulltext_checked", "content": "注释",
            "confirmed": True, "document_anchor": anchor})
        self.assertEqual(response.status_code, 400)

    def test_tampered_saved_page_is_rejected_even_when_client_echoes_old_hash(self):
        document = self.upload().json()["paper_documents"][0]
        anchor = self.anchor(document)
        self.app.state.store.update(self.project["id"], lambda current: current["paper_documents"][0]["pages"][0].update(text="改过的正文"))
        before = self.saved()
        self.assertEqual(self.note(anchor).status_code, 400)
        self.assertEqual(self.saved(), before)

    def test_restored_text_can_anchor_without_original_and_exact_reupload_refills_bytes(self):
        document = self.upload().json()["paper_documents"][0]
        before = self.saved()
        restored_path = Path(self.directory.name) / "restored.db"
        restored_app = create_app(restored_path, model_client=FakeModel())
        restored_app.state.store.create(copy.deepcopy(before))
        with TestClient(restored_app) as restored:
            project = restored.get(self.base).json()
            self.assertFalse(project["paper_documents"][0]["original_available"])
            url = self.base + "/documents/" + document["id"] + "/original"
            self.assertEqual(restored.get(url).status_code, 404)
            anchor = self.anchor(document)
            response = restored.post(self.base + "/reading-notes", json={"run_id": self.run["id"], "source_id": "S001",
                "level": "fulltext_checked", "confirmed": True, "content": "从备份文字核对摘录，未声称已认证原件。", "document_anchor": anchor})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["paper_documents"][0]["original_available"])
            response = restored.post(self.base + "/documents", params={"run_id": self.run["id"], "source_id": "S001",
                "filename": "same.txt", "binding_confirmed": "true"}, content=self.text.encode("utf-8"),
                headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["paper_documents"]), 1)
            self.assertEqual(response.json()["paper_documents"][0]["id"], document["id"])
            self.assertEqual(response.json()["paper_documents"][0]["pages"], document["pages"])
            self.assertTrue(response.json()["paper_documents"][0]["original_available"])
            self.assertEqual(restored.get(url).content, self.text.encode("utf-8"))

    def test_original_corruption_is_not_downloaded_as_matching_file(self):
        document = self.upload().json()["paper_documents"][0]
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE paper_document_originals SET body = ?", (sqlite3.Binary(b"tampered"),))
            connection.commit()
        response = self.client.get(self.base + "/documents/" + document["id"] + "/original")
        self.assertEqual(response.status_code, 400)
        self.assertIn("完整性", response.json()["detail"])

    def test_accept_and_reject_preserve_derived_original_availability(self):
        from test_direction_proposal import ready
        self.upload()
        self.model.result = ready()
        for decision, available in (("accept", True), ("reject", False)):
            if not available:
                with closing(sqlite3.connect(self.database)) as connection:
                    connection.execute("DELETE FROM paper_document_originals")
                    connection.commit()
            response = self.client.post(self.base + "/advance", json={})
            self.assertEqual(response.status_code, 200, response.text)
            pending = response.json()["workflow"]["pending_proposal"]
            self.assertIsNotNone(pending)
            result = self.client.post(self.base + "/direction/proposals/" + pending["id"] + "/" + decision, json={})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertIs(result.json()["paper_documents"][0]["original_available"], available)
            self.assertNotIn("original_available", self.saved()["paper_documents"][0])

    def test_cross_site_upload_rejected_even_for_binary_endpoint(self):
        response = self.upload(headers={"Content-Type": "application/pdf", "Origin": "https://evil.test"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.blob_count(), 0)

    def test_line_breaks_normalize_before_text_hashes_but_original_hash_is_exact(self):
        raw = "第一行😀\r\n第二行\r第三行\n".encode("utf-8")
        response = self.upload(raw)
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()["paper_documents"][0]
        self.assertEqual(document["pages"][0]["text"], "第一行😀\n第二行\n第三行\n")
        self.assertEqual(document["file_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(document["pages"][0]["text_sha256"], hashlib.sha256(document["pages"][0]["text"].encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
