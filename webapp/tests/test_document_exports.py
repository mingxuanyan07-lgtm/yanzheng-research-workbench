"""Portable exports retain exact excerpt provenance without whole-paper disclosure."""
import copy
import hashlib
import unittest

import engine


class DocumentExportTest(unittest.TestCase):
    def setUp(self):
        self.project = engine.create_project({"initial_idea": "随机低秩逼近的误差和计算预算",
            "goal": "comparison", "resources": "laptop", "time_horizon": "week"})
        self.text = "QA 合成正文😀。只有这一句被选中。其余正文未授权导出。"
        quote = "只有这一句被选中。"
        self.document = {"id": "doc-qa", "run_id": "run-qa", "source_id": "S-QA",
            "filename": "QA <paper>.txt", "file_sha256": hashlib.sha256(self.text.encode()).hexdigest(),
            "pages": [{"number": 1, "text": self.text,
                       "text_sha256": hashlib.sha256(self.text.encode()).hexdigest()}]}
        self.note = {"id": "note-qa", "run_id": "run-qa", "source_id": "S-QA",
            "confirmed": True, "origin": "user_attested", "level": "fulltext_checked",
            "checked_at": engine.now(), "locator": "QA 合成正文，第 1 页", "content": "个人判断：还需核查基线。",
            "document_anchor": {"document_id": "doc-qa", "page": 1,
                "start": self.text.index(quote), "end": self.text.index(quote) + len(quote), "quote": quote,
                "file_sha256": self.document["file_sha256"],
                "page_sha256": self.document["pages"][0]["text_sha256"]}}
        self.project["paper_documents"] = [self.document]
        self.project["reading_notes"] = [self.note]
        self.project["literature_searches"] = [{"id": "run-qa", "sources": [{"id": "S-QA", "title": "QA 合成题录"}], "search_log": []}]
        engine.select_topic(self.project, self.project["direction_map"]["topics"][0]["id"])

    def test_plan_and_decision_package_keep_quote_separate_from_commentary(self):
        for render in (engine.render_plan, engine.render_decision_package):
            with self.subTest(export=render.__name__):
                text = render(self.project)
                self.assertIn("个人判断：还需核查基线。", text)
                self.assertIn("原文摘录：只有这一句被选中。", text)
                self.assertIn("QA &lt;paper&gt;.txt", text)
                self.assertIn("第 1 页", text)
                self.assertIn("Unicode 字符", text)
                self.assertIn(self.document["file_sha256"], text)
                self.assertNotIn("其余正文未授权导出", text)
                self.assertNotIn("<paper>", text)

    def test_changed_page_quote_or_cross_source_is_never_exported_as_exact(self):
        for change in ({"quote": "伪造摘录"}, {"page": True}, {"start": 0}, {"file_sha256": "a" * 64}):
            note = copy.deepcopy(self.note)
            note["document_anchor"].update(change)
            with self.subTest(change=change):
                self.assertIn("不完整或与当前文字不一致", engine._document_anchor_text(self.project, note))
                self.assertNotIn("位置与导入文字一致", engine._document_anchor_text(self.project, note))
        cross_source = self.note | {"source_id": "OTHER"}
        self.assertIn("不完整或与当前文字不一致", engine._document_anchor_text(self.project, cross_source))

    def test_imported_anchor_does_not_claim_the_program_never_read_the_file(self):
        self.assertEqual(engine._declared_fingerprint_text(self.note), "")
        self.assertIn("程序未读取", engine._declared_fingerprint_text({"content": "手写笔记"}))

    def test_unsafe_excerpt_stays_plain_text(self):
        document = self.document
        quote = '<script>alert(1)</script> [click](javascript:alert(2))'
        document["pages"][0].update(text=quote, text_sha256=hashlib.sha256(quote.encode()).hexdigest())
        note = copy.deepcopy(self.note)
        note["document_anchor"].update(start=0, end=len(quote), quote=quote,
            page_sha256=document["pages"][0]["text_sha256"])
        text = engine._document_anchor_text(self.project, note)
        self.assertNotIn("<script>", text)
        self.assertNotIn("[click]", text)
        self.assertIn("&lt;script&gt;", text)


if __name__ == "__main__":
    unittest.main()
