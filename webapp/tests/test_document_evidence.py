"""Exact uploaded excerpts survive evidence and backups without leaking whole papers."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import literature_service as literature
import storage
import workflow
from test_direction_note_binding import confirmed_note, proposal_with, supports
from test_workflow import add_search, call, project


def fixture(text="导言😀。\n选中结论仅对这一设置成立。\n未选正文不应发送。", *, extra_source=False):
    item = project()
    source_id = add_search(item)
    if extra_source:
        add_search(item, "run2", "S001")
    document = literature.extract_paper_document(text.encode("utf-8"), "论文.txt", "run1", "S001")
    item["paper_documents"] = [document]
    quote = "选中结论仅对这一设置成立。" if "选中结论仅对这一设置成立。" in text else text[:20]
    start = text.index(quote)
    anchor = {"document_id": document["id"], "page": 1, "start": start, "end": start + len(quote),
              "quote": quote, "file_sha256": document["file_sha256"],
              "page_sha256": document["pages"][0]["text_sha256"]}
    item["reading_notes"] = [confirmed_note(level="fulltext_checked", content="用户解释：结论有明确适用范围。",
                                          locator=literature.document_anchor_locator(anchor), document_anchor=anchor)]
    return item, source_id, anchor


def package(items):
    return json.loads(storage.create_backup(items))


def encoded(document):
    document["project_count"] = len(document["projects"])
    document["projects_sha256"] = hashlib.sha256(storage._canonical(document["projects"])).hexdigest()
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


class DocumentEvidenceTest(unittest.TestCase):
    def test_anchor_is_program_bound_exact_unicode_excerpt_and_snapshot_is_independent(self):
        item, source_id, anchor = fixture()
        self.assertEqual(anchor["start"], 5)  # emoji is one Unicode codepoint, not two UTF-16 units.
        result = workflow.validate_payload(proposal_with(supports(source_id)), item)
        snapshot = result["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(snapshot["document_anchor"], anchor)
        self.assertIn("程序已核对", snapshot["caveat"])
        self.assertIn("不代表出版社认证", snapshot["caveat"])
        workflow.apply_result(item, proposal_with(supports(source_id)), call())
        self.assertEqual(engine.validate(item["direction_map"]), [])
        self.assertEqual(item["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"], snapshot)
        item["reading_notes"][0]["document_anchor"]["quote"] = "后来修改的笔记"
        self.assertEqual(snapshot["document_anchor"], anchor | {"quote": "选中结论仅对这一设置成立。"})

    def test_model_cannot_supply_an_anchor_or_server_snapshot(self):
        item, source_id, anchor = fixture()
        for extra in ({"document_anchor": anchor}, {"note_snapshot": {"document_anchor": anchor}}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    workflow.validate_payload(proposal_with(supports(source_id) | extra), item)

    def test_wrong_run_source_unconfirmed_or_forged_anchor_cannot_support(self):
        item, source_id, anchor = fixture(extra_source=True)
        for change in ({"run_id": "run2"}, {"source_id": "S999"}, {"confirmed": False},
                       {"document_anchor": anchor | {"quote": "不存在的结论"}},
                       {"document_anchor": anchor | {"start": True}},
                       {"document_anchor": anchor | {"file_sha256": "0" * 64}}):
            with self.subTest(change=change):
                altered = copy.deepcopy(item)
                altered["reading_notes"][0].update(change)
                with self.assertRaises(ValueError):
                    workflow.validate_payload(proposal_with(supports(source_id)), altered)
                self.assertEqual(workflow.context_sources(altered)[0]["verification"], "metadata_checked")

    def test_whole_paper_and_arbitrary_note_fields_never_enter_model_context(self):
        text = "选中结论仅对这一设置成立。" + "正文未选内容SECRET" * 20000
        item, _, anchor = fixture(text)
        item["reading_notes"][0]["paper_documents"] = [{"text": "note-extra-SECRET"}]
        messages = workflow.build_messages(item)
        context = json.loads(messages[1]["content"].split("\n", 1)[1])
        self.assertIn(anchor["quote"], messages[1]["content"])
        self.assertNotIn("正文未选内容SECRET", messages[1]["content"])
        self.assertNotIn("note-extra-SECRET", messages[1]["content"])
        self.assertNotIn("paper_documents", context)
        self.assertEqual(context["reading_notes"][0]["document_anchor"], anchor)
        self.assertLess(len(messages[1]["content"]), 10000)

    def test_context_over_budget_is_explicit_and_never_silently_truncated(self):
        item, _, anchor = fixture()
        for index in range(9):
            item["reading_notes"].append(confirmed_note("long-" + str(index), content="有依据的解释" * 900,
                level="fulltext_checked", document_anchor=copy.deepcopy(anchor)))
        with self.assertRaisesRegex(ValueError, "上下文超过.*尚未调用模型.*不会被静默截断"):
            workflow.build_messages(item)

    def test_saved_anchor_shape_is_strict_and_legacy_snapshot_remains_valid(self):
        item, source_id, _ = fixture()
        workflow.apply_result(item, proposal_with(supports(source_id)), call())
        original = item["direction_map"]
        changes = ({"page": True}, {"page": 0}, {"start": False}, {"end": -1}, {"quote": "错"},
                   {"quote": " "}, {"quote": "字" * 4001}, {"file_sha256": "fake"},
                   {"page_sha256": "X" * 64}, {"document_id": ".."}, {"extra": True})
        for change in changes:
            with self.subTest(change=change):
                record = copy.deepcopy(original)
                record["topics"][0]["evidence"][0]["note_snapshot"]["document_anchor"].update(change)
                self.assertTrue(engine.validate(record))
        legacy = copy.deepcopy(original)
        del legacy["topics"][0]["evidence"][0]["note_snapshot"]["document_anchor"]
        self.assertEqual(engine.validate(legacy), [])

    def test_referenced_page_change_stales_pending_but_unrelated_upload_does_not(self):
        item, source_id, _ = fixture()
        workflow.store_proposal(item, proposal_with(supports(source_id)), call())
        self.assertFalse(workflow.pending_proposal_is_stale(item))
        unrelated = literature.extract_paper_document(b"unrelated body", "extra.txt", "run1", "S001")
        item["paper_documents"].append(unrelated)
        self.assertFalse(workflow.pending_proposal_is_stale(item))
        item["paper_documents"][0]["pages"][0]["text"] += " changed"
        self.assertTrue(workflow.pending_proposal_is_stale(item))
        with self.assertRaises(workflow.ProposalConflict):
            workflow.accept_proposal(item, item["workflow"]["pending_proposal"]["id"])

    def test_legacy_note_requires_no_document_and_preserves_existing_binding(self):
        item = project()
        source_id = add_search(item)
        item["reading_notes"] = [confirmed_note()]
        workflow.apply_result(item, proposal_with(supports(source_id)), call())
        snapshot = item["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertNotIn("document_anchor", snapshot)
        self.assertIn("不是程序核验", snapshot["caveat"])
        storage.parse_backup(storage.create_backup([item]))

    def test_backup_roundtrip_keeps_text_and_anchors_but_never_original_or_availability(self):
        item, source_id, anchor = fixture()
        workflow.apply_result(item, proposal_with(supports(source_id)), call())
        item["paper_documents"][0]["original_available"] = True  # derived UI field, never portable.
        raw = storage.create_backup([item])
        self.assertNotIn(b'original_available', raw)
        parsed = storage.parse_backup(raw)
        with patch("literature_service._extract_pdf_in_worker", side_effect=AssertionError("no re-extract")):
            restored = storage.restored_projects(parsed)[0]
        restored_doc = copy.deepcopy(restored["paper_documents"][0])
        self.assertFalse(restored_doc.pop("backup_restore")["original_files_included"])
        expected_doc = copy.deepcopy(item["paper_documents"][0])
        expected_doc.pop("original_available")
        self.assertEqual(restored_doc, expected_doc)
        self.assertEqual(restored["reading_notes"][0]["document_anchor"], anchor)
        self.assertEqual(restored["direction_map"], item["direction_map"])
        root = Path(__file__).resolve().parent / ".tmp"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="document-backup-", dir=root) as folder:
            store = storage.Store(Path(folder) / "restored.db")
            store.restore_missing([restored])
            self.assertEqual(store.paper_document_original_ids(item["id"]), set())
            self.assertIsNone(store.get_paper_document_original(item["id"], restored_doc["id"]))
            storage.parse_backup(storage.create_backup(store.snapshot()))

    def test_restored_text_boundary_is_explicit_in_new_model_context_and_binding(self):
        item, source_id, _ = fixture()
        restored = storage.restored_projects(storage.parse_backup(storage.create_backup([item])))[0]
        context = json.loads(workflow.build_messages(restored)[1]["content"].split("\n", 1)[1])
        self.assertIn("从备份恢复", context["reading_notes"][0]["document_context_caveat"])
        self.assertIn("未重新提取或核对原始文件", context["reading_notes"][0]["document_context_caveat"])
        result = workflow.validate_payload(proposal_with(supports(source_id)), restored)
        snapshot = result["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertIn("从备份恢复", snapshot["caveat"])
        self.assertEqual(snapshot["origin"], "user_attested")

    def test_backup_rejects_tampered_page_doc_digest_and_exact_anchor(self):
        item, source_id, _ = fixture()
        workflow.apply_result(item, proposal_with(supports(source_id)), call())
        original = package([item])
        changes = (lambda p: p["paper_documents"][0]["pages"][0].update(text="changed"),
                   lambda p: p["paper_documents"][0].update(text_sha256="a" * 64),
                   lambda p: p["paper_documents"][0]["pages"][0].update(number=2),
                   lambda p: p["reading_notes"][0]["document_anchor"].update(quote="wrong"),
                   lambda p: p["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"]["document_anchor"].update(page=2))
        for change in changes:
            with self.subTest(change=change):
                document = copy.deepcopy(original)
                change(document["projects"][0])
                with self.assertRaisesRegex(ValueError, "备份未恢复"):
                    storage.parse_backup(encoded(document))

    def test_backup_rejects_cross_source_anchor_in_note_map_history_and_preview(self):
        item, source_id, _ = fixture(extra_source=True)
        workflow.apply_result(item, proposal_with(supports(source_id)), call())
        workflow.store_proposal(item, proposal_with(supports(source_id)), call("call-2"))
        original = package([item])
        for where in ("note", "map", "history", "preview"):
            with self.subTest(where=where):
                document = copy.deepcopy(original)
                current = document["projects"][0]
                if where == "note":
                    current["reading_notes"][0]["run_id"] = "run2"
                elif where == "map":
                    current["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"]["run_id"] = "run2"
                elif where == "history":
                    historic = copy.deepcopy(current["direction_map"])
                    historic["topics"][0]["evidence"][0]["note_snapshot"]["document_anchor"]["quote"] = "forged"
                    current["direction_versions"].append({"id": "fake-history", "direction_map": historic})
                else:
                    current["workflow"]["pending_proposal"]["payload"]["topics"][0]["evidence"][0]["note_snapshot"]["run_id"] = "run2"
                with self.assertRaisesRegex(ValueError, "备份未恢复"):
                    storage.parse_backup(encoded(document))

    def test_backup_rejects_duplicate_doc_ids_associations_path_and_embedded_bytes(self):
        item, _, _ = fixture()
        original = package([item])
        for field, value in (("id", ".."), ("filename", "../secret.txt"), ("filename", " paper.txt "),
                             ("byte_count", True), ("byte_count", literature.PAPER_MAX_BYTES + 1),
                             ("media_type", "text/html"), ("origin", "publisher_authenticated"),
                             ("body", "pretend original bytes")):
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(original)
                document["projects"][0]["paper_documents"][0][field] = value
                with self.assertRaises(ValueError):
                    storage.parse_backup(encoded(document))
        for duplicate_id in (True, False):
            document = copy.deepcopy(original)
            duplicate = copy.deepcopy(document["projects"][0]["paper_documents"][0])
            if not duplicate_id:
                duplicate["id"] = "different-id-same-file"
            document["projects"][0]["paper_documents"].append(duplicate)
            with self.assertRaises(ValueError):
                storage.parse_backup(encoded(document))

    def test_backup_checks_page_boundaries_and_project_document_budgets(self):
        item, _, _ = fixture()
        original = package([item])
        for variant in ("pages", "single_chars", "total_chars", "count", "control"):
            with self.subTest(variant=variant):
                document = copy.deepcopy(original)
                current = document["projects"][0]
                current["reading_notes"] = []
                if variant == "pages":
                    texts = ["a"] * (literature.PAPER_MAX_PAGES + 1)
                elif variant == "single_chars":
                    texts = ["a" * (literature.PAPER_MAX_CHARACTERS + 1)]
                elif variant == "control":
                    texts = ["text\x00hidden"]
                else:
                    texts = ["a" * (400000 if variant == "total_chars" else 1)]
                doc = current["paper_documents"][0]
                doc["pages"] = [{"number": n + 1, "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
                                for n, text in enumerate(texts)]
                doc["text_sha256"] = literature.paper_text_sha256(doc["pages"])
                if variant in ("total_chars", "count"):
                    count = 6 if variant == "total_chars" else literature.PROJECT_MAX_DOCUMENTS + 1
                    current["paper_documents"] = []
                    for n in range(count):
                        clone = copy.deepcopy(doc)
                        clone.update(id="doc-" + str(n), file_sha256=hashlib.sha256(str(n).encode()).hexdigest())
                        current["paper_documents"].append(clone)
                with self.assertRaisesRegex(ValueError, "备份未恢复"):
                    storage.parse_backup(encoded(document))

    def test_bad_last_project_fails_before_isolated_restore_writes_any_project(self):
        first, _, _ = fixture()
        second, _, _ = fixture()
        document = package([first, second])
        document["projects"][-1]["paper_documents"][0]["pages"][0]["text"] += "tampered"
        root = Path(__file__).resolve().parent / ".tmp"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="document-atomic-", dir=root) as folder:
            store = storage.Store(Path(folder) / "empty.db")
            with self.assertRaises(ValueError):
                parsed = storage.parse_backup(encoded(document))
                store.restore_missing(storage.restored_projects(parsed))
            self.assertEqual(store.snapshot(), [])


if __name__ == "__main__":
    unittest.main()
