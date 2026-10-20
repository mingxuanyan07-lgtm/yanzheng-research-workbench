"""Gap A: direction supports/limits evidence must bind the exact confirmed reading note."""

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import engine
import workflow
from test_workflow import add_search, call, directions, project


def confirmed_note(note_id="note-ok", run_id="run1", source_id="S001", **extra):
    note = {"id": note_id, "run_id": run_id, "source_id": source_id, "level": "abstract_checked",
            "content": "摘要只在特定设置下报告差异，没有一般耗时结论。", "locator": "Abstract",
            "checked_at": "2026-10-16T03:00:00+00:00", "confirmed": True, "origin": "user_attested"}
    note.update(extra)
    return note


def supports(source_id, note_id="note-ok", claim="摘要给出的观测仅限于特定设置。"):
    return {"source_id": source_id, "relation": "supports", "claim": claim, "note_id": note_id}


def proposal_with(evidence):
    result = directions()
    result["topics"][0]["evidence"] = [evidence]
    return result


class DirectionNoteBindingTest(unittest.TestCase):
    def fixture(self):
        item = project()
        source_id = add_search(item)
        item["reading_notes"] = [confirmed_note()]
        return item, source_id

    def test_binding_persists_exact_snapshot_through_selection_and_export(self):
        item, source_id = self.fixture()
        normalized = workflow.validate_payload(proposal_with(supports(source_id)), item)
        evidence = normalized["topics"][0]["evidence"][0]
        snapshot = evidence["note_snapshot"]
        note = item["reading_notes"][0]
        self.assertEqual(evidence["note_id"], "note-ok")
        self.assertEqual(snapshot["note_id"], "note-ok")
        self.assertEqual(snapshot["run_id"], "run1")
        self.assertEqual(snapshot["source_id"], "S001")
        self.assertEqual(snapshot["level"], "abstract_checked")
        self.assertEqual(snapshot["locator"], "Abstract")
        self.assertEqual(snapshot["content_sha256"], hashlib.sha256(note["content"].encode("utf-8")).hexdigest())
        self.assertEqual(snapshot["note_sha256"], hashlib.sha256(
            json.dumps(note, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest())
        self.assertEqual(snapshot["note_sha256"], hashlib.sha256(json.dumps(
            note, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest())
        self.assertIn("不是程序核验", snapshot["caveat"])
        self.assertIn("note-ok", evidence["claim"])

        workflow.apply_result(item, proposal_with(supports(source_id)), call())
        stored = item["direction_map"]["topics"][0]["evidence"][0]
        self.assertEqual(stored["note_snapshot"], snapshot)
        self.assertEqual(engine.validate(item["direction_map"]), [])

        engine.select_topic(item, item["direction_map"]["topics"][0]["id"], "由我按已验证依据明确选择。")
        self.assertEqual(item["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"], snapshot)
        plan = engine.render_plan(item)
        self.assertIn("note-ok", plan)
        self.assertIn("Abstract", plan)
        self.assertIn("笔记 SHA256", plan)
        self.assertIn("不是程序核验", plan)
        exported = json.loads(json.dumps(item, ensure_ascii=False))
        exported_snapshot = exported["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(exported_snapshot, snapshot)

        engine.reopen_project(item)
        revised = proposal_with(supports(source_id, claim="依据同一已确认摘录进一步收窄。"))
        workflow.apply_result(item, revised, call("call-2"))
        archived = item["direction_versions"][-1]["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(archived, snapshot)

    def test_wrong_run_or_source_note_is_rejected(self):
        item, source_id = self.fixture()
        item["reading_notes"].append(confirmed_note("note-other-run", run_id="run2", source_id="S001",
                                                    locator="Abstract"))
        item["reading_notes"].append(confirmed_note("note-other-source", run_id="run1", source_id="S002",
                                                    locator="Abstract"))
        for note_id in ("note-other-run", "note-other-source", "note-missing"):
            with self.subTest(note_id=note_id):
                with self.assertRaises(ValueError):
                    workflow.validate_payload(proposal_with(supports(source_id, note_id=note_id)), item)
        # A note from a different source must not make the source's own reading claim pass.
        workflow.apply_result(item, proposal_with(supports(source_id)), call())

    def test_unconfirmed_wrong_level_missing_locator_or_untrusted_origin_note_is_rejected(self):
        item, _ = self.fixture()
        item["reading_notes"].append(confirmed_note("note-unconfirmed", confirmed=False))
        item["reading_notes"].append(confirmed_note("note-metadata-level", level="metadata_checked"))
        item["reading_notes"].append(confirmed_note("note-no-locator", locator=""))
        item["reading_notes"].append(confirmed_note("note-curated", origin="curated_example"))
        for note_id in ("note-unconfirmed", "note-metadata-level", "note-no-locator", "note-curated"):
            with self.subTest(note_id=note_id):
                with self.assertRaises(ValueError):
                    workflow.validate_payload(proposal_with(supports(source_evidence_id(item), note_id=note_id)), item)
        # The one valid confirmed note still binds.
        workflow.validate_payload(proposal_with(supports(source_evidence_id(item))), item)

    def test_missing_note_id_is_rejected_for_supports_but_not_for_background(self):
        item, source_id = self.fixture()
        legacy = {"source_id": source_id, "relation": "supports", "claim": "旧式输出没有 note_id。"}
        with self.assertRaises(ValueError):
            workflow.validate_payload(proposal_with(legacy), item)
        background = {"source_id": source_id, "relation": "background", "claim": "题录已经证明一切。", "note_id": None}
        normalized = workflow.validate_payload(proposal_with(background), item)
        entry = normalized["topics"][0]["evidence"][0]
        self.assertNotIn("note_snapshot", entry)
        self.assertIn("仅表示待核查的书目信息", entry["claim"])
        with self.assertRaises(ValueError):
            workflow.validate_payload(proposal_with({**background, "note_id": "note-ok"}), item)

    def test_unverified_source_only_allows_unverified_lead_without_note(self):
        item, _ = self.fixture()
        unverified_source = add_search(item, "run3", "S003", authors=[], year_or_version=None)
        normalized = workflow.validate_payload(
            proposal_with({"source_id": unverified_source, "relation": "unverified_lead", "claim": "待核查线索。", "note_id": None}), item)
        self.assertNotIn("note_snapshot", normalized["topics"][0]["evidence"][0])
        for relation in ("background", "supports"):
            with self.subTest(relation=relation):
                with self.assertRaises(ValueError):
                    workflow.validate_payload(
                        proposal_with({"source_id": unverified_source, "relation": relation,
                                       "claim": "未核实来源不能升级。", "note_id": None}), item)

    def test_limits_binding_uses_the_same_note_snapshot(self):
        item, source_id = self.fixture()
        evidence = {"source_id": source_id, "relation": "limits", "claim": "该摘录限定了结论的适用范围。", "note_id": "note-ok"}
        normalized = workflow.validate_payload(proposal_with(evidence), item)
        snapshot = normalized["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(snapshot["note_id"], "note-ok")
        self.assertEqual(snapshot["locator"], "Abstract")


def source_evidence_id(item):
    """Return the program-owned composite source id for the single run1/S001 source."""
    return workflow.context_sources(item)[0]["id"]


if __name__ == "__main__":
    unittest.main()
