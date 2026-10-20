"""Direction-map contract (skills validator loaded directly): note binding fields fail closed."""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parent
sys.path.insert(0, str(BASE))
import workflow
from test_workflow import add_search, call, directions, project

SCRIPT = ROOT / "skills/research-direction-guide/scripts/validate_direction_map.py"
SPEC = importlib.util.spec_from_file_location("direction_map_validator_contract_test", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def bound_project(fingerprint=False):
    item = project()
    source_id = add_search(item)
    note = {"id": "note-contract", "run_id": "run1", "source_id": "S001", "level": "abstract_checked",
            "content": "摘要只支持特定条件。", "locator": "Abstract",
            "checked_at": "2026-10-16T03:00:00+00:00", "confirmed": True, "origin": "user_attested"}
    if fingerprint:
        note.update({"source_version": "v2", "source_sha256": "c" * 64,
                     "source_sha256_scope": "user_declared_metadata"})
    item["reading_notes"] = [note]
    proposal = directions()
    proposal["topics"][0]["evidence"] = [{"source_id": source_id, "relation": "supports",
                                          "claim": "摘要仅支持特定条件。", "note_id": "note-contract"}]
    workflow.apply_result(item, proposal, call())
    return item["direction_map"], source_id


class DirectionContractTest(unittest.TestCase):
    def setUp(self):
        self.record, self.source_id = bound_project()
        self.assertEqual(VALIDATOR.validate(self.record), [])

    def errors(self, record):
        return VALIDATOR.validate(copy.deepcopy(record))

    def test_bound_snapshot_with_and_without_declared_fingerprint_passes(self):
        declared, _ = bound_project(fingerprint=True)
        self.assertEqual(VALIDATOR.validate(declared), [])
        snapshot = declared["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertEqual(snapshot["source_sha256"], "c" * 64)
        self.assertEqual(snapshot["source_sha256_scope"], "user_declared_metadata")
        # Missing fingerprint remains structurally valid ("未登记" case).
        snapshot = self.record["topics"][0]["evidence"][0]["note_snapshot"]
        self.assertNotIn("source_sha256", snapshot)
        self.assertEqual(VALIDATOR.validate(self.record), [])

    def test_supports_without_note_id_or_snapshot_fails_closed(self):
        no_note = copy.deepcopy(self.record)
        no_note["topics"][0]["evidence"][0].pop("note_id")
        self.assertTrue(self.errors(no_note))
        no_snapshot = copy.deepcopy(self.record)
        no_snapshot["topics"][0]["evidence"][0].pop("note_snapshot")
        self.assertTrue(self.errors(no_snapshot))
        empty_snapshot = copy.deepcopy(self.record)
        empty_snapshot["topics"][0]["evidence"][0]["note_snapshot"] = {}
        self.assertTrue(self.errors(empty_snapshot))

    def test_malformed_hashes_and_missing_fields_fail_closed(self):
        for key, value in (("note_sha256", "z" * 64), ("note_sha256", "A" * 64),
                           ("note_sha256", "a" * 63), ("content_sha256", "not-a-hash")):
            with self.subTest(key=key, value=value):
                record = copy.deepcopy(self.record)
                record["topics"][0]["evidence"][0]["note_snapshot"][key] = value
                self.assertTrue(self.errors(record))
        for key in ("caveat", "locator", "checked_at", "origin", "confirmed"):
            with self.subTest(missing=key):
                record = copy.deepcopy(self.record)
                record["topics"][0]["evidence"][0]["note_snapshot"].pop(key)
                self.assertTrue(self.errors(record))
        record = copy.deepcopy(self.record)
        record["topics"][0]["evidence"][0]["note_snapshot"]["origin"] = "curated_example"
        self.assertTrue(self.errors(record))
        record = copy.deepcopy(self.record)
        record["topics"][0]["evidence"][0]["note_snapshot"]["confirmed"] = False
        self.assertTrue(self.errors(record))

    def test_background_and_unverified_lead_must_not_bind_notes(self):
        background = copy.deepcopy(self.record)
        background["topics"][0]["evidence"] = [{"source_id": self.source_id, "relation": "background",
                                                "claim": "题录线索。", "note_id": None, "note_snapshot": None}]
        self.assertEqual(self.errors(background), [])
        bound_background = copy.deepcopy(background)
        bound_background["topics"][0]["evidence"][0]["note_id"] = "note-contract"
        self.assertTrue(self.errors(bound_background))
        with_snapshot = copy.deepcopy(background)
        with_snapshot["topics"][0]["evidence"][0]["note_snapshot"] = copy.deepcopy(
            self.record["topics"][0]["evidence"][0]["note_snapshot"])
        self.assertTrue(self.errors(with_snapshot))

    def test_unknown_fields_and_incoherent_declared_triple_fail_closed(self):
        unknown = copy.deepcopy(self.record)
        unknown["topics"][0]["evidence"][0]["note_snapshot"]["unexpected"] = "x"
        self.assertTrue(self.errors(unknown))
        unknown_evidence = copy.deepcopy(self.record)
        unknown_evidence["topics"][0]["evidence"][0]["extra"] = "x"
        self.assertTrue(self.errors(unknown_evidence))
        declared, _ = bound_project(fingerprint=True)
        partial = copy.deepcopy(declared)
        snapshot = partial["topics"][0]["evidence"][0]["note_snapshot"]
        snapshot.pop("source_sha256_scope")
        self.assertTrue(self.errors(partial))


if __name__ == "__main__":
    unittest.main()
