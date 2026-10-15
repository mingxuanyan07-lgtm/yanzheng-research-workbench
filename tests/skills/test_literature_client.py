import importlib.util
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("client", Path(__file__).resolve().parents[2] / "skills/literature-search/scripts/search_literature.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
DATE = "2026-10-14T00:00:00+00:00"
URL = "https://example.org/query"

class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, *_): return self.value

class Opener:
    def __init__(self, *values): self.values = list(values); self.calls = []
    def open(self, request, **kwargs):
        self.calls.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception): raise value
        return Response(value)

def source(doi=None, arxiv=None, title="Example paper", version=None):
    return client.new_source(title, ["A. Author"], 2024, "https://example.org/paper",
        {"doi": doi, "arxiv_id": arxiv, "openalex_id": None}, None,
        client.empty_access(), "Q001", "crossref", URL, DATE,
        [version] if version else [])

class ClientTests(unittest.TestCase):
    def test_doi_normalization(self):
        self.assertEqual(client.normalize_doi("https://doi.org/10.1234/ABC"), "10.1234/abc")
        self.assertIsNone(client.normalize_doi("not doi"))

    def test_arxiv_old_new_versions(self):
        self.assertEqual(client.normalize_arxiv_id("https://arxiv.org/pdf/math/0605598v3.pdf"), "math/0605598")
        self.assertEqual(client.normalize_arxiv_id("0909.4061v2", True), "0909.4061v2")

    def test_transitive_dedup_keeps_versions_and_conflicts(self):
        inputs = [source("10.1234/a", title="Formal paper"),
                  source(arxiv="0909.4061", version="0909.4061v1"),
                  source("10.1234/a", "0909.4061", version="0909.4061v2")]
        original = json.dumps(inputs)
        result = client.deduplicate_sources(inputs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["versions"], ["0909.4061v1", "0909.4061v2"])
        self.assertTrue(result[0]["field_conflicts"])
        self.assertEqual(json.dumps(inputs), original)

    def test_titles_do_not_prove_identity(self):
        self.assertEqual(len(client.deduplicate_sources([source(), source()])), 2)

    def test_adapter_skips_incomplete_sources(self):
        inputs = client.deduplicate_sources([source(), source("10.1234/b")])
        inputs[1]["authors"] = []
        accepted, skipped = client.export_direction_sources(inputs)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(skipped[0]["id"], "S002")
        self.assertEqual(set(accepted[0]), set(client.BASE_FIELDS))

    def test_crossref_abstract_not_reviewed(self):
        body = {"message": {"items": [{"title": ["Paper"], "author": [{"given": "A", "family": "B"}],
            "issued": {"date-parts": [[2020]]}, "DOI": "10.1234/a", "abstract": "<jats:p>A &amp; B</jats:p>"}]}}
        records, warnings = client.parse_crossref(json.dumps(body), "Q001", URL, DATE)
        self.assertEqual(records[0]["abstract"], "A & B")
        self.assertEqual(records[0]["verification"], "metadata_checked")
        self.assertIsNone(records[0]["access"]["is_open_access"])
        self.assertFalse(warnings)

    def test_crossref_incomplete_and_empty(self):
        records, warnings = client.parse_crossref('{"message":{"items":[{"title":["Paper"]}]}}', "Q001", URL, DATE)
        self.assertEqual(records[0]["authors"], [])
        self.assertIsNone(records[0]["year_or_version"])
        self.assertEqual(records[0]["verification"], "unverified")
        self.assertEqual(client.parse_crossref('{"message":{"items":[]}}', "Q001", URL, DATE), ([], []))
        with self.assertRaises(ValueError): client.parse_crossref('{}', "Q001", URL, DATE)

    def test_arxiv_atom_and_error(self):
        body = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry><id>http://arxiv.org/abs/0909.4061v2</id><title> Paper\n title </title>
          <published>2009-09-22T00:00:00Z</published><author><name>A. Author</name></author>
          <summary>Summary</summary><arxiv:doi>10.1234/A</arxiv:doi>
          <link title="pdf" href="http://arxiv.org/pdf/0909.4061v2"/></entry></feed>'''
        records, warnings = client.parse_arxiv(body, "Q001", URL, DATE)
        self.assertEqual(records[0]["identifiers"]["doi"], "10.1234/a")
        self.assertEqual(records[0]["versions"], ["0909.4061v2"])
        self.assertEqual(records[0]["verification"], "metadata_checked")
        self.assertEqual(records[0]["access"]["is_open_access"], True)
        self.assertFalse(warnings)
        with self.assertRaises(ValueError):
            client.parse_arxiv('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#x</id><summary>Error</summary></entry></feed>', "Q001", URL, DATE)

    def test_openalex_inverted_abstract_and_oa(self):
        body = {"results": [{"id": "https://openalex.org/W123", "title": "Paper", "doi": "https://doi.org/10.1234/a",
            "authorships": [{"author": {"display_name": "A. Author"}}], "publication_year": 2024,
            "abstract_inverted_index": {"B": [1], "A": [0, 2]},
            "primary_location": {"landing_page_url": "https://arxiv.org/abs/0909.4061v2"},
            "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/0909.4061v2", "license": "cc-by"},
            "open_access": {"is_oa": True}}]}
        records, _ = client.parse_openalex(json.dumps(body), "Q001", URL, DATE)
        self.assertEqual(records[0]["abstract"], "A B A")
        self.assertEqual(records[0]["identifiers"]["arxiv_id"], "0909.4061")
        self.assertEqual(records[0]["verification"], "metadata_checked")

    def test_bearer_key_not_url_or_error(self):
        http = client.HttpClient(retries=0, api_key="SENTINEL_SECRET")
        http.opener = Opener(b'{"results":[]}')
        url, _ = client.build_url("openalex", "query", 2)
        http.get(url, "openalex")
        self.assertEqual(http.opener.calls[0].get_header("Authorization"), "Bearer SENTINEL_SECRET")
        self.assertNotIn("SENTINEL_SECRET", http.opener.calls[0].full_url)
        self.assertNotIn("SENTINEL_SECRET", http.safe_error(URLError("SENTINEL_SECRET api_key=abc")))

    def test_retry_429_and_5xx_bounded(self):
        http = client.HttpClient(retries=2)
        http.opener = Opener(HTTPError(URL, 429, "rate", {"Retry-After": "0"}, None),
                             HTTPError(URL, 503, "busy", {}, None), b'{}')
        with patch.object(client.time, "sleep"):
            payload, attempts, _, error = http.get(URL, "crossref")
        self.assertEqual((payload, attempts, error), (b'{}', 3, None))

    def test_long_retry_after_does_not_retry(self):
        http = client.HttpClient(retries=2)
        http.opener = Opener(HTTPError(URL, 429, "rate", {"Retry-After": "120"}, None))
        payload, attempts, _, error = http.get(URL, "crossref")
        self.assertEqual((payload, attempts, error), (None, 1, "HTTP 429"))

    def test_arxiv_minimum_interval(self):
        http = client.HttpClient(retries=0)
        http.opener = Opener(b'a', b'b')
        with patch.object(client.time, "monotonic", side_effect=[10, 11, 13]), patch.object(client.time, "sleep") as sleep:
            http.get(URL, "arxiv")
            http.get(URL, "arxiv")
            sleep.assert_called_once_with(2)

    def test_failed_empty_success_truthful(self):
        with patch.object(client.HttpClient, "get", side_effect=[
            (None, 2, DATE, "HTTP 503"),
            (b'{"results":[]}', 1, DATE, None)]):
            record = client.run_search("idea", ["query"], ["crossref", "openalex"])
        self.assertEqual([log["status"] for log in record["search_log"]], ["failed", "empty"])
        self.assertTrue(record["provenance"]["live_search_performed"])
        self.assertEqual(record["sources"], [])
        self.assertEqual(record["evidence"], [])

if __name__ == "__main__": unittest.main(verbosity=2)
