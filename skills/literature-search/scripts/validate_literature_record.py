#!/usr/bin/env python3
"""Read-only consistency check for research_literature_search v1.

Usage: python validate_literature_record.py path/to/search.json
PASS checks the contract, not paper authenticity, reading quality or novelty.
Only the standard library is used; this script makes no network calls.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


def validate(record):
    errors = []

    def fail(path, message):
        errors.append(f"{path}: {message}")

    def obj(value, path, required):
        if not isinstance(value, dict):
            fail(path, "expected object")
            return {}
        for key in required.split():
            if key not in value:
                fail(f"{path}.{key}", "required")
        return value

    def string(value, path):
        if not isinstance(value, str) or not value.strip():
            fail(path, "expected non-empty string")
            return False
        return True

    def array(value, path):
        if not isinstance(value, list):
            fail(path, "expected array")
            return []
        return value

    def strings(value, path):
        values = array(value, path)
        for i, item in enumerate(values):
            string(item, f"{path}[{i}]")
        return values

    def choice(value, path, choices):
        if not isinstance(value, str) or value not in choices:
            fail(path, "expected " + " / ".join(choices))

    def integer(value, path, minimum=0, nullable=False):
        if nullable and value is None:
            return True
        if type(value) is not int or value < minimum:
            fail(path, f"expected integer >= {minimum}" + (" or null" if nullable else ""))
            return False
        return True

    def iso(value, path, timezone=False):
        if not string(value, path):
            return
        try:
            if "T" in value or "t" in value:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if timezone and parsed.utcoffset() is None:
                    raise ValueError()
            elif timezone:
                raise ValueError()
            else:
                date.fromisoformat(value)
        except ValueError:
            fail(path, "expected ISO 8601 date/time" + (" with timezone" if timezone else ""))

    def url(value, path):
        if not string(value, path):
            return
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            parsed.port
            sensitive_query = {"api_key", "apikey", "access_token", "token", "authorization", "password", "passwd", "cookie", "insttoken"}
            if (parsed.scheme not in ("http", "https") or not host
                    or any(c.isspace() for c in value)
                    or parsed.username or parsed.password
                    or any(key.lower() in sensitive_query for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
                    or host in ("localhost", "example.com", "example.org", "example.net")
                    or host.endswith((".invalid", ".example", ".test", ".localhost"))):
                raise ValueError()
        except ValueError:
            fail(path, "expected usable http(s) URL without credentials, not a placeholder")

    def identified(items, path):
        found = {}
        for i, item in enumerate(items):
            p = f"{path}[{i}]"
            item = obj(item, p, "id")
            item_id = item.get("id")
            if string(item_id, p + ".id"):
                if item_id in found:
                    fail(p + ".id", "duplicate id")
                found[item_id] = item
        return found

    r = obj(record, "$", "schema_version record_type created_at request provenance search_log sources evidence limitations next_action")
    choice(r.get("schema_version"), "$.schema_version", ("1.0",))
    choice(r.get("record_type"), "$.record_type", ("research_literature_search",))
    iso(r.get("created_at"), "$.created_at", timezone=True)
    string(r.get("next_action"), "$.next_action")
    strings(r.get("limitations"), "$.limitations")

    request = obj(r.get("request"), "$.request", "idea queries providers year_from year_to max_results_per_query")
    string(request.get("idea"), "$.request.idea")
    queries = strings(request.get("queries"), "$.request.queries")
    providers = strings(request.get("providers"), "$.request.providers")
    for key in ("year_from", "year_to"):
        integer(request.get(key), "$.request." + key, minimum=1, nullable=True)
    year_from, year_to = request.get("year_from"), request.get("year_to")
    if type(year_from) is int and type(year_to) is int and year_from > year_to:
        fail("$.request.year_to", "must be >= year_from")
    integer(request.get("max_results_per_query"), "$.request.max_results_per_query", minimum=1)

    provenance = obj(r.get("provenance"), "$.provenance", "generator live_search_performed external_model_api_called new_experiments_executed")
    string(provenance.get("generator"), "$.provenance.generator")
    for key in ("live_search_performed", "external_model_api_called", "new_experiments_executed"):
        if type(provenance.get(key)) is not bool:
            fail("$.provenance." + key, "expected boolean")
    for key in ("external_model_api_called", "new_experiments_executed"):
        if provenance.get(key) is not False:
            fail("$.provenance." + key, "must be false for this search contract")

    log = array(r.get("search_log"), "$.search_log")
    if log and not queries:
        fail("$.request.queries", "search_log requires at least one query")
    if log and not providers:
        fail("$.request.providers", "search_log requires at least one provider")
    searches = identified(log, "$.search_log")
    attempted = False
    for i, entry in enumerate(log):
        p = f"$.search_log[{i}]"
        entry = obj(entry, p, "id provider query status searched_at attempts result_count error")
        string(entry.get("provider"), p + ".provider")
        string(entry.get("query"), p + ".query")
        if entry.get("provider") not in providers:
            fail(p + ".provider", "must belong to request.providers")
        if entry.get("query") not in queries:
            fail(p + ".query", "must belong to request.queries")
        status = entry.get("status")
        choice(status, p + ".status", ("planned", "success", "empty", "failed"))
        attempts = entry.get("attempts")
        count = entry.get("result_count")
        attempts_valid = integer(attempts, p + ".attempts")
        count_valid = integer(count, p + ".result_count")
        if attempts_valid and attempts > 0:
            attempted = True
            iso(entry.get("searched_at"), p + ".searched_at", timezone=True)
        elif entry.get("searched_at") is not None:
            fail(p + ".searched_at", "must be null when no request was attempted")
        if status == "planned":
            if attempts != 0:
                fail(p + ".attempts", "planned search must have 0 attempts")
        elif status in ("success", "empty") and (not attempts_valid or attempts < 1):
            fail(p + ".attempts", "success/empty require an actual request attempt")
        if status == "success":
            if count_valid and count < 1:
                fail(p + ".result_count", "success requires at least one result")
        elif count != 0:
            fail(p + ".result_count", "planned/empty/failed must have 0 results")
        if status == "failed":
            string(entry.get("error"), p + ".error")
        elif entry.get("error") is not None:
            fail(p + ".error", "must be null unless failed")
    if type(provenance.get("live_search_performed")) is bool and provenance.get("live_search_performed") != attempted:
        fail("$.provenance.live_search_performed", "must equal whether any search entry has attempts > 0")

    sources = array(r.get("sources"), "$.sources")
    source_ids = identified(sources, "$.sources")
    references_per_search = {search_id: set() for search_id in searches}
    for i, source in enumerate(sources):
        p = f"$.sources[{i}]"
        source = obj(source, p, "id title authors year_or_version url origin verification locator checked_at identifiers abstract access retrievals")
        string(source.get("title"), p + ".title")
        authors = strings(source.get("authors"), p + ".authors")
        year = source.get("year_or_version")
        if year is not None:
            if type(year) is int:
                integer(year, p + ".year_or_version", minimum=1)
            else:
                string(year, p + ".year_or_version")
        origin = source.get("origin")
        verification = source.get("verification")
        choice(origin, p + ".origin", ("this_run", "provided_material", "prior_project"))
        choice(verification, p + ".verification", ("unverified", "metadata_checked", "abstract_checked", "fulltext_checked"))
        verified = verification in ("metadata_checked", "abstract_checked", "fulltext_checked")
        if verified and not authors:
            fail(p + ".authors", "verified source requires at least one author")
        if verified and year is None:
            fail(p + ".year_or_version", "verified source requires a year/version")
        if verified or source.get("url") is not None:
            url(source.get("url"), p + ".url")
        if verified or source.get("checked_at") is not None:
            iso(source.get("checked_at"), p + ".checked_at")
        if verification in ("abstract_checked", "fulltext_checked") or source.get("locator") is not None:
            string(source.get("locator"), p + ".locator")
        identifiers = obj(source.get("identifiers"), p + ".identifiers", "doi arxiv_id openalex_id")
        for key in ("doi", "arxiv_id", "openalex_id"):
            if identifiers.get(key) is not None:
                string(identifiers.get(key), p + ".identifiers." + key)
        if source.get("abstract") is not None:
            string(source.get("abstract"), p + ".abstract")
        access = obj(source.get("access"), p + ".access", "is_open_access landing_page_url pdf_url license")
        if access.get("is_open_access") is not None and type(access.get("is_open_access")) is not bool:
            fail(p + ".access.is_open_access", "expected boolean or null")
        for key in ("landing_page_url", "pdf_url"):
            if access.get(key) is not None:
                url(access.get(key), p + ".access." + key)
        if access.get("license") is not None:
            string(access.get("license"), p + ".access.license")
        retrievals = array(source.get("retrievals"), p + ".retrievals")
        if origin == "this_run" and not retrievals:
            fail(p + ".retrievals", "this_run requires a successful search retrieval")
        for j, retrieval in enumerate(retrievals):
            rp = f"{p}.retrievals[{j}]"
            retrieval = obj(retrieval, rp, "search_id provider url retrieved_at")
            search_id = retrieval.get("search_id")
            string(search_id, rp + ".search_id")
            search = searches.get(search_id) if isinstance(search_id, str) else None
            string(retrieval.get("provider"), rp + ".provider")
            url(retrieval.get("url"), rp + ".url")
            iso(retrieval.get("retrieved_at"), rp + ".retrieved_at", timezone=True)
            if search is None:
                fail(rp + ".search_id", "must reference an entry in search_log")
            else:
                if search.get("status") != "success":
                    fail(rp + ".search_id", "retrieval must reference a success entry")
                if retrieval.get("provider") != search.get("provider"):
                    fail(rp + ".provider", "must match the referenced search provider")
                source_id = source.get("id")
                if isinstance(source_id, str):
                    references_per_search[search_id].add(source_id)
    for search_id, source_refs in references_per_search.items():
        count = searches[search_id].get("result_count")
        if type(count) is int and len(source_refs) > count:
            fail("$.search_log", f"{search_id}: more distinct retrieved sources than result_count")

    for i, evidence in enumerate(array(r.get("evidence"), "$.evidence")):
        p = f"$.evidence[{i}]"
        evidence = obj(evidence, p, "source_id relation claim locator")
        source_id = evidence.get("source_id")
        string(source_id, p + ".source_id")
        source = source_ids.get(source_id) if isinstance(source_id, str) else None
        if source is None:
            fail(p + ".source_id", "must reference a source id")
        relation = evidence.get("relation")
        choice(relation, p + ".relation", ("background", "supports", "limits", "unverified_lead"))
        string(evidence.get("claim"), p + ".claim")
        if evidence.get("locator") is not None or relation in ("supports", "limits"):
            string(evidence.get("locator"), p + ".locator")
        if source:
            verification = source.get("verification")
            if verification == "unverified" and relation != "unverified_lead":
                fail(p + ".relation", "unverified sources only allow unverified_lead")
            if verification == "metadata_checked" and relation != "background":
                fail(p + ".relation", "metadata_checked sources only allow bibliographic background")
            if relation in ("supports", "limits") and verification not in ("abstract_checked", "fulltext_checked"):
                fail(p + ".relation", "supports/limits require an actually reviewed abstract or full text")
    return errors


def validate_record(record):
    """Public import/search-client API; returns errors without modifying input."""
    return validate(record)


def main():
    if len(sys.argv) != 2:
        print("Usage: validate_literature_record.py <JSON file>", file=sys.stderr)
        return 1
    try:
        record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"FAIL: cannot read JSON: {exc}", file=sys.stderr)
        return 1
    errors = validate(record)
    if errors:
        for error in errors[:16]:
            print("FAIL: " + error, file=sys.stderr)
        if len(errors) > 16:
            print(f"... {len(errors) - 16} more errors", file=sys.stderr)
        return 1
    print("PASS: structure/consistency only; authenticity, reading quality and novelty still require review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
