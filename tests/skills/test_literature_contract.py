#!/usr/bin/env python3
"""Offline tests for execution provenance, retrieval links and review boundaries."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "literature-search" / "scripts" / "validate_literature_record.py"
spec = importlib.util.spec_from_file_location("literature_validator", SCRIPT)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

TIME = "2026-10-14T19:00:00+08:00"
QUERY = "randomized low rank approximation"


def base_record(status="success", attempts=1):
    result = {
        "schema_version": "1.0", "record_type": "research_literature_search", "created_at": TIME,
        "request": {"idea": "随机低秩逼近", "queries": [QUERY], "providers": ["crossref"],
                    "year_from": None, "year_to": None, "max_results_per_query": 10},
        "provenance": {"generator": "offline test fixture", "live_search_performed": attempts > 0,
                       "external_model_api_called": False, "new_experiments_executed": False},
        "search_log": [{"id": "q1", "provider": "crossref", "query": QUERY, "status": status,
                        "searched_at": TIME if attempts else None, "attempts": attempts,
                        "result_count": 1 if status == "success" else 0,
                        "error": "HTTP 503" if status == "failed" else None}],
        "sources": [], "evidence": [], "limitations": [], "next_action": "核对相关摘要及正文",
    }
    if status == "success":
        result["sources"] = [{
            "id": "s1", "title": "Finding Structure with Randomness: Probabilistic Algorithms for Constructing Approximate Matrix Decompositions",
            "authors": ["N. Halko", "P. G. Martinsson", "J. A. Tropp"], "year_or_version": 2011,
            "url": "https://doi.org/10.1137/090771806", "origin": "this_run", "verification": "metadata_checked",
            "locator": None, "checked_at": TIME,
            "identifiers": {"doi": "10.1137/090771806", "arxiv_id": None, "openalex_id": None},
            "abstract": None,
            "access": {"is_open_access": None, "landing_page_url": "https://doi.org/10.1137/090771806",
                       "pdf_url": None, "license": None},
            "retrievals": [{"search_id": "q1", "provider": "crossref",
                            "url": "https://api.crossref.org/works?query=randomized%20low%20rank%20approximation", "retrieved_at": TIME}],
        }]
    return result


class LiteratureContractTests(unittest.TestCase):
    def test_query_credentials_are_not_recorded_as_source_links(self):
        for key in ("api_key", "access_token", "password", "insttoken"):
            with self.subTest(key=key):
                record = base_record()
                record["sources"][0]["url"] += "?" + key + "=test-value"
                self.assertTrue(validator.validate(record))

    def assert_valid(self, record):
        self.assertEqual([], validator.validate(record))

    def assert_invalid(self, record, fragment):
        errors = validator.validate(record)
        self.assertTrue(errors, "incorrectly accepted inconsistent record")
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_success_and_real_attempted_failure_or_empty(self):
        for status in ("success", "failed", "empty"):
            with self.subTest(status=status):
                self.assert_valid(base_record(status))

    def test_plans_and_configuration_failures_are_not_live(self):
        self.assert_valid(base_record("planned", 0))
        self.assert_valid(base_record("failed", 0))

    def test_import_has_no_query_retrieval_or_live_claim(self):
        record = base_record()
        record["search_log"] = []
        record["request"].update(queries=[], providers=[])
        record["provenance"]["live_search_performed"] = False
        source = record["sources"][0]
        source.update(origin="provided_material", verification="unverified", retrievals=[], checked_at=None)
        source["prior_review"] = {"verification": "fulltext_checked", "locator": "§4", "checked_at": "2025-01-01"}
        self.assert_valid(record)
        source["origin"] = "prior_project"
        self.assert_valid(record)
        record["evidence"] = [{"source_id": "s1", "relation": "supports", "claim": "旧核对不代替本次阅读", "locator": "§4"}]
        self.assert_invalid(record, "unverified sources")

    def test_incomplete_import_metadata_can_be_retained_unverified(self):
        record = base_record("planned", 0)
        record["sources"] = [{
            "id": "import-1", "title": "用户导入的待核实文献", "authors": [], "year_or_version": None,
            "url": None, "origin": "provided_material", "verification": "unverified", "locator": None,
            "checked_at": None, "identifiers": {"doi": None, "arxiv_id": None, "openalex_id": None},
            "abstract": None, "access": {"is_open_access": None, "landing_page_url": None, "pdf_url": None, "license": None},
            "retrievals": [],
        }]
        record["evidence"] = [{"source_id": "import-1", "relation": "unverified_lead", "claim": "待核实线索", "locator": None}]
        self.assert_valid(record)
        record["sources"][0]["verification"] = "metadata_checked"
        self.assert_invalid(record, "verified source requires")

    def test_abstract_returned_by_api_does_not_imply_reading(self):
        record = base_record()
        record["sources"][0]["abstract"] = "This field is present in the API response; it has not been reviewed."
        self.assert_valid(record)
        record["evidence"] = [{"source_id": "s1", "relation": "supports", "claim": "摘要存在不能证明读过", "locator": "Abstract"}]
        self.assert_invalid(record, "require an actually reviewed")

    def test_metadata_background_is_valid(self):
        record = base_record()
        record["evidence"] = [{"source_id": "s1", "relation": "background", "claim": "2011年发表的候选文献", "locator": None}]
        self.assert_valid(record)

    def test_reviewed_content_requires_source_and_evidence_locator(self):
        for level in ("abstract_checked", "fulltext_checked"):
            with self.subTest(level=level):
                record = base_record()
                source = record["sources"][0]
                source.update(verification=level, locator="Abstract" if level == "abstract_checked" else "§4.5")
                record["evidence"] = [{"source_id": "s1", "relation": "limits", "claim": "人工阅读后记录的范围限制", "locator": source["locator"]}]
                self.assert_valid(record)
                source["locator"] = None
                self.assert_invalid(record, "sources[0].locator")
                source["locator"] = "§4.5"
                record["evidence"][0]["locator"] = None
                self.assert_invalid(record, "evidence[0].locator")

    def test_provenance_cannot_hide_attempts_or_invent_live_search(self):
        for status, attempts in (("success", 1), ("empty", 1), ("failed", 1), ("planned", 0), ("failed", 0)):
            with self.subTest(status=status, attempts=attempts):
                record = base_record(status, attempts)
                record["provenance"]["live_search_performed"] = not record["provenance"]["live_search_performed"]
                self.assert_invalid(record, "whether any search entry")

    def test_plan_cannot_have_executed_timestamp(self):
        record = base_record("planned", 0)
        record["search_log"][0]["searched_at"] = TIME
        self.assert_invalid(record, "must be null")

    def test_empty_cannot_report_results(self):
        record = base_record("empty")
        record["search_log"][0]["result_count"] = 1
        self.assert_invalid(record, "must have 0 results")

    def test_success_cannot_have_zero_attempts_or_results(self):
        record = base_record()
        record["search_log"][0]["attempts"] = 0
        self.assert_invalid(record, "require an actual request attempt")
        record = base_record()
        record["search_log"][0]["result_count"] = 0
        self.assert_invalid(record, "at least one result")

    def test_failure_cannot_lose_error_reason(self):
        record = base_record("failed")
        record["search_log"][0]["error"] = None
        self.assert_invalid(record, "error")
        record = base_record()
        record["search_log"][0]["error"] = "previous failure hidden here"
        self.assert_invalid(record, "unless failed")

    def test_search_must_belong_to_request(self):
        for key in ("provider", "query"):
            with self.subTest(key=key):
                record = base_record()
                record["search_log"][0][key] = "not-in-request"
                self.assert_invalid(record, "must belong to request")

    def test_this_run_cannot_be_an_unlinked_import(self):
        record = base_record()
        record["sources"][0]["retrievals"] = []
        self.assert_invalid(record, "requires a successful search retrieval")

    def test_retrieval_must_link_successful_search_and_matching_provider(self):
        for mutation, fragment in (({"search_id": "missing"}, "entry in search_log"), ({"provider": "arxiv"}, "referenced search provider")):
            with self.subTest(mutation=mutation):
                record = base_record()
                record["sources"][0]["retrievals"][0].update(mutation)
                self.assert_invalid(record, fragment)
        for status in ("planned", "empty", "failed"):
            with self.subTest(status=status):
                record = base_record()
                record["search_log"] = base_record(status, 0 if status == "planned" else 1)["search_log"]
                record["provenance"]["live_search_performed"] = status != "planned"
                self.assert_invalid(record, "retrieval must reference a success entry")

    def test_deduplicated_source_retains_two_query_links(self):
        record = base_record()
        record["request"]["queries"].append("randomized SVD")
        second = copy.deepcopy(record["search_log"][0])
        second.update(id="q2", query="randomized SVD")
        record["search_log"].append(second)
        second_retrieval = copy.deepcopy(record["sources"][0]["retrievals"][0])
        second_retrieval["search_id"] = "q2"
        record["sources"][0]["retrievals"].append(second_retrieval)
        record["sources"][0]["versions"] = ["journal version", "preprint"]
        record["sources"][0]["field_conflicts"] = []
        self.assert_valid(record)

    def test_result_count_can_exceed_deduplicated_sources_but_not_be_smaller(self):
        record = base_record()
        record["search_log"][0]["result_count"] = 5
        self.assert_valid(record)
        second = copy.deepcopy(record["sources"][0])
        second["id"] = "s2"
        record["sources"].append(second)
        record["search_log"][0]["result_count"] = 1
        self.assert_invalid(record, "more distinct retrieved sources")

    def test_duplicate_source_or_search_ids_rejected(self):
        for key in ("sources", "search_log"):
            with self.subTest(key=key):
                record = base_record()
                record[key].append(copy.deepcopy(record[key][0]))
                self.assert_invalid(record, "duplicate id")

    def test_evidence_cannot_reference_unknown_source(self):
        record = base_record()
        record["evidence"] = [{"source_id": "s999", "relation": "background", "claim": "unlinked claim", "locator": None}]
        self.assert_invalid(record, "reference a source id")

    def test_unverified_source_cannot_be_bibliographic_background(self):
        record = base_record()
        record["sources"][0]["verification"] = "unverified"
        record["evidence"] = [{"source_id": "s1", "relation": "background", "claim": "待核实来源", "locator": None}]
        self.assert_invalid(record, "only allow unverified_lead")

    def test_year_range_and_integer_bool_errors(self):
        record = base_record()
        record["request"].update(year_from=2026, year_to=2020)
        self.assert_invalid(record, "must be >= year_from")
        for section, key in (("request", "max_results_per_query"), ("request", "year_from"), ("search_log", "attempts"), ("search_log", "result_count")):
            with self.subTest(section=section, key=key):
                record = base_record()
                target = record[section][0] if section == "search_log" else record[section]
                target[key] = True
                self.assert_invalid(record, "expected integer")

    def test_timestamps_without_timezone_cannot_claim_actual_live_time(self):
        record = base_record()
        record["search_log"][0]["searched_at"] = "2026-10-14T19:00:00"
        self.assert_invalid(record, "with timezone")

    def test_placeholder_and_credential_urls_rejected(self):
        for value in ("https://example.com/paper", "https://user:secret@doi.org/10.1137/090771806", "file:///local.pdf"):
            with self.subTest(value=value):
                record = base_record()
                record["sources"][0]["url"] = value
                self.assert_invalid(record, "usable http(s) URL")

    def test_extra_model_and_experiment_flags_not_allowed(self):
        for key in ("external_model_api_called", "new_experiments_executed"):
            record = base_record()
            record["provenance"][key] = True
            self.assert_invalid(record, "must be false")

    def test_malformed_json_shapes_report_errors_without_crashing(self):
        for value in (None, [], "bad", {}, {"request": [], "provenance": [], "sources": [None], "search_log": [None], "evidence": [None]}):
            with self.subTest(value=value):
                self.assertTrue(validator.validate(value))

    def test_cli_success_and_failure_exit_codes(self):
        scratch = ROOT / "webapp" / "tests" / ".tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="literature-contract-", dir=scratch) as directory:
            target = Path(directory) / "record.json"
            target.write_text(json.dumps(base_record(), ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(target)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("structure/consistency only", result.stdout)
            target.write_text("invalid json", encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(target)], capture_output=True, text=True)
            self.assertEqual(1, result.returncode)
            self.assertIn("cannot read JSON", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
