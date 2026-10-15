import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "literature-search" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import import_literature as importer
from search_literature import deduplicate_sources, export_direction_sources
from validate_literature_record import validate_record


class ImportChecks(unittest.TestCase):
    def test_import_retains_exact_versions_when_identifier_is_unversioned(self):
        source, error = importer.normalize({"title": "Versioned paper", "authors": ["Author"], "year_or_version": 2020,
            "url": "https://arxiv.org/abs/2008.04447v1", "identifiers": {"arxiv_id": "2008.04447"},
            "versions": ["2008.04447v1"]}, "provided_material", 1)
        self.assertIsNone(error)
        self.assertEqual(source["identifiers"]["arxiv_id"], "2008.04447")
        self.assertIn("2008.04447v1", source["versions"])

    def source(self, **extra):
        value = {"title": "Finding structure with randomness", "authors": ["Halko", "Martinsson", "Tropp"],
                 "year_or_version": 2011, "url": "https://arxiv.org/abs/0909.4061v2", **extra}
        return importer.normalize(value, "prior_project", 1)[0]

    def test_old_review_is_preserved_but_not_claimed(self):
        source = self.source(verification="abstract_checked", checked_at="2026-10-13", locator="Abstract")
        self.assertEqual(source["verification"], "unverified")
        self.assertIsNone(source["checked_at"])
        self.assertEqual(source["prior_review"][0]["verification"], "abstract_checked")
        self.assertEqual(source["retrievals"], [])

    def test_existing_project_source_shapes(self):
        entries = [{"title": "A"}]
        for data in [entries, {"sources": entries}, {"references": entries},
                     {"topics": [{"references": entries}]}, {"selected_topic": {"references": entries}}]:
            self.assertEqual(importer.json_entries(data), entries)

    def test_ris_authors_and_multiline_abstract(self):
        content = "TY  - JOUR\nTI  - A real title\nAU  - Alice\nAU  - Bob\nPY  - 2020\nDO  - 10.1000/AbC\nAB  - First line\n    next line\nER  - \n"
        entry = importer.parse_ris(content)[0]
        source, error = importer.normalize(entry, "provided_material", 1)
        self.assertIsNone(error)
        self.assertEqual(source["authors"], ["Alice", "Bob"])
        self.assertEqual(source["abstract"], "First line next line")
        self.assertEqual(source["identifiers"]["doi"], "10.1000/abc")
        self.assertEqual(source["verification"], "unverified")

    def test_ris_unterminated_record_rejected(self):
        with self.assertRaises(ValueError):
            importer.parse_ris("TY  - JOUR\nTI  - Missing end")

    def test_missing_authors_and_year_do_not_get_invented(self):
        source, error = importer.normalize({"title": "Incomplete"}, "provided_material", 1)
        self.assertIsNone(error)
        self.assertEqual(source["authors"], [])
        self.assertIsNone(source["year_or_version"])
        self.assertEqual(source["verification"], "unverified")
        accepted, skipped = export_direction_sources([source])
        self.assertEqual(accepted, [])
        self.assertEqual(len(skipped), 1)

    def test_same_identifier_merges_versions_and_prior_reviews(self):
        a = self.source(verification="abstract_checked", locator="Abstract")
        b = self.source(url="https://arxiv.org/abs/0909.4061v1", verification="metadata_checked", locator="metadata")
        merged = deduplicate_sources([a, b])
        self.assertEqual(len(merged), 1)
        self.assertCountEqual(merged[0]["versions"], ["0909.4061v2", "0909.4061v1"])
        self.assertEqual(len(merged[0]["prior_review"]), 2)
        self.assertEqual(merged[0]["verification"], "unverified")

    def test_same_title_without_identifier_does_not_merge(self):
        a, _ = importer.normalize({"title": "Same"}, "provided_material", 1)
        self.assertEqual(len(deduplicate_sources([a, copy.deepcopy(a)])), 2)

    def test_import_cli_no_live_query_and_input_unchanged(self):
        source_path = ROOT / "tests" / "fixtures" / "literature-sources.json"
        before = source_path.read_bytes()
        with tempfile.TemporaryDirectory(dir=ROOT / "webapp" / "tests" / ".tmp", prefix="lit-import-") as folder:
            output = Path(folder) / "record.json"
            self.assertEqual(importer.main(["--input", str(source_path), "--origin", "prior_project", "--output", str(output)]), 0)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(validate_record(record), [])
            self.assertFalse(record["provenance"]["live_search_performed"])
            self.assertEqual(record["search_log"], [])
            self.assertTrue(all(s["verification"] == "unverified" for s in record["sources"]))
        self.assertEqual(source_path.read_bytes(), before)

    def test_output_cannot_overwrite_input_even_with_flag(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "webapp" / "tests" / ".tmp", prefix="lit-import-") as folder:
            file = Path(folder) / "sources.json"
            file.write_text('[{"title":"Keep"}]', encoding="utf-8")
            self.assertEqual(importer.main(["--input", str(file), "--output", str(file), "--overwrite"]), 1)
            self.assertEqual(file.read_text(encoding="utf-8"), '[{"title":"Keep"}]')

    def test_existing_output_requires_explicit_overwrite(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "webapp" / "tests" / ".tmp", prefix="lit-import-") as folder:
            source, output = Path(folder) / "sources.json", Path(folder) / "out.json"
            source.write_text('[{"title":"Source"}]', encoding="utf-8")
            output.write_text("KEEP", encoding="utf-8")
            self.assertEqual(importer.main(["--input", str(source), "--output", str(output)]), 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "KEEP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
