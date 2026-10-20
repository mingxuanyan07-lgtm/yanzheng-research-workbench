"""Saved-map compatibility must not relax new model evidence or rewrite old claims."""
import copy
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import engine
import planner
import workflow
from test_workflow import add_search, call, directions, project


def legacy_project():
    item = project()
    source_id = add_search(item)
    item["reading_notes"] = [{
        "id": "synthetic-old-note", "run_id": "run1", "source_id": "S001",
        "level": "abstract_checked", "content": "合成旧记录：结论只适用于所列条件。",
        "locator": "Abstract", "checked_at": "2026-10-16T03:00:00+00:00",
        "confirmed": True, "origin": "user_attested",
    }]
    payload = directions()
    payload["topics"][0]["evidence"] = [{
        "source_id": source_id, "relation": "supports",
        "claim": "旧版保存的关联声明。", "note_id": "synthetic-old-note",
    }]
    workflow.apply_result(item, payload, call())
    entry = item["direction_map"]["topics"][0]["evidence"][0]
    entry.pop("note_id")
    entry.pop("note_snapshot")
    entry["claim"] = "旧版保存的关联声明。"
    item["reading_notes"] = []
    return item


class SavedDirectionCompatibilityTest(unittest.TestCase):
    def test_saved_compatibility_is_explicit_and_does_not_mutate(self):
        item = legacy_project()
        record = item["direction_map"]
        before = copy.deepcopy(item)
        self.assertTrue(engine.validate(record))
        with self.assertRaises(RuntimeError):
            engine.assert_valid(record)
        engine.assert_saved_direction_map(record)
        self.assertEqual(item, before)
        # An unsigned data flag cannot change the default strict validator.
        record["provenance"]["allow_saved_unbound_evidence"] = True
        self.assertTrue(engine.validate(record))

    def test_incomplete_null_or_malformed_bindings_are_not_legacy_shapes(self):
        for update in (
            {"note_id": None}, {"note_snapshot": None},
            {"note_id": None, "note_snapshot": None},
            {"note_id": "note-only"}, {"note_snapshot": {}},
            {"note_id": "bad", "note_snapshot": {"note_id": "bad", "note_sha256": "z" * 64}},
            {"legacy": True},
        ):
            with self.subTest(update=update):
                item = legacy_project()
                item["direction_map"]["topics"][0]["evidence"][0].update(update)
                before = copy.deepcopy(item)
                with self.assertRaises(RuntimeError):
                    engine.assert_saved_direction_map(item["direction_map"])
                self.assertEqual(item, before)

    def test_compatibility_still_checks_source_verification_and_claim(self):
        for verification in ("metadata_checked", "unverified"):
            with self.subTest(verification=verification):
                item = legacy_project()
                item["direction_map"]["sources"][0]["verification"] = verification
                with self.assertRaises(RuntimeError):
                    engine.assert_saved_direction_map(item["direction_map"])
        for change in ({"source_id": "missing"}, {"claim": ""}):
            with self.subTest(change=change):
                item = legacy_project()
                item["direction_map"]["topics"][0]["evidence"][0].update(change)
                with self.assertRaises(RuntimeError):
                    engine.assert_saved_direction_map(item["direction_map"])

    def test_selection_reopen_and_markdown_preserve_unbound_evidence_and_archives(self):
        item = legacy_project()
        record = item["direction_map"]
        evidence = copy.deepcopy(record["topics"][0]["evidence"])
        item["direction_versions"].append({"direction_map": copy.deepcopy(record), "reason": "synthetic-old-version"})
        archives = copy.deepcopy(item["direction_versions"])
        topic_id = record["topics"][0]["id"]
        engine.select_topic(item, topic_id, "保留旧声明，继续人工核查。")
        plan = engine.render_plan(item)
        self.assertIn("历史关联：未绑定阅读笔记", plan)
        self.assertIn("不等于已核验证据", plan)
        self.assertNotIn("绑定阅读笔记 synthetic-old-note", plan)
        self.assertEqual(record["topics"][0]["evidence"], evidence)
        engine.reopen_project(item)
        self.assertEqual(record["topics"][0]["evidence"], evidence)
        self.assertEqual(item["direction_versions"], archives)
        self.assertEqual(record["stage"], "directions_ready")
        self.assertIsNone(record["selected_topic_id"])

    def test_invalid_saved_map_is_rejected_before_selection_or_reopen_mutates(self):
        for action in ("select", "reopen"):
            with self.subTest(action=action):
                item = legacy_project()
                item["direction_map"]["topics"][0]["evidence"][0]["note_snapshot"] = None
                before = copy.deepcopy(item)
                with self.assertRaises(RuntimeError):
                    if action == "select":
                        engine.select_topic(item, item["direction_map"]["topics"][0]["id"])
                    else:
                        engine.reopen_project(item)
                self.assertEqual(item, before)

    def test_new_workflow_and_planner_payloads_still_reject_unbound_support(self):
        item = legacy_project()
        # Even a genuine newly confirmed note cannot be borrowed implicitly.
        item["reading_notes"] = [{
            "id": "fresh-note", "run_id": "run1", "source_id": "S001",
            "level": "abstract_checked", "content": "新确认摘录", "locator": "Abstract",
            "checked_at": "2026-10-20T03:00:00+00:00", "confirmed": True, "origin": "user_attested",
        }]
        payload = directions()
        payload["topics"][0]["evidence"] = copy.deepcopy(item["direction_map"]["topics"][0]["evidence"])
        before = copy.deepcopy(item)
        with self.assertRaises(ValueError):
            workflow.store_proposal(item, payload, call("new-unbound"))
        with self.assertRaises(ValueError):
            planner.validate_payload({key: payload[key] for key in ("topics", "recommendation")})
        self.assertEqual(item, before)
        # Explicitly binding the new note produces a strict, current-format map.
        payload["topics"][0]["evidence"][0]["note_id"] = "fresh-note"
        workflow.store_proposal(item, payload, call("new-bound"))
        workflow.accept_proposal(item, item["workflow"]["pending_proposal"]["id"])
        engine.assert_valid(item["direction_map"])
        self.assertEqual(item["direction_map"]["topics"][0]["evidence"][0]["note_id"], "fresh-note")


if __name__ == "__main__":
    unittest.main()
