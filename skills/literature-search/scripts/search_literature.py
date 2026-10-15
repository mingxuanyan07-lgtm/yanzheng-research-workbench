#!/usr/bin/env python3
"""Search public scholarly metadata and write research_literature_search v1.

Python 3.9+, standard library only. No model API or subscribed full text is used.
API abstracts are retrieved text, not evidence that a reader reviewed them.
Exit: 0 all queries completed; 3 partial/failed retrieval (JSON still saved);
2 invalid arguments or file error. Importable parsers/helpers make no requests.
"""

import argparse
import copy
import html
import json
import os
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROVIDERS = ("crossref", "arxiv", "openalex")
BASE_FIELDS = ("id", "title", "authors", "year_or_version", "url", "origin",
               "verification", "locator", "checked_at")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value):
    if not isinstance(value, str):
        return None
    value = html.unescape(re.sub(r"<[^>]*>", " ", value))
    return " ".join(value.split()) or None


def http_url(value):
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    if parts.scheme.lower() in ("http", "https") and parts.netloc and not parts.username:
        return value.strip()
    return None


def normalize_doi(value):
    if not isinstance(value, str):
        return None
    value = unquote(value.strip())
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/|^doi:\s*", "", value, flags=re.I)
    return value.lower() if re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I) else None


def normalize_arxiv_id(value, keep_version=False):
    if not isinstance(value, str):
        return None
    value = unquote(value.strip())
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/|^arxiv:\s*", "", value, flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I).lower()
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7})(?:v\d+)?", value):
        return None
    return value if keep_version else re.sub(r"v\d+$", "", value)


def new_source(title, authors, year, url, identifiers, abstract, access,
               search_id, provider, request_url, retrieved_at, versions=None):
    # A complete bibliographic record can be metadata_checked. Content review
    # is performed separately; even a populated abstract never raises the level.
    complete = bool(title and authors and year and http_url(url))
    return {
        "id": "", "title": title, "authors": authors, "year_or_version": year,
        "url": http_url(url), "origin": "this_run",
        "verification": "metadata_checked" if complete else "unverified",
        "locator": "API metadata" if complete else None,
        "checked_at": retrieved_at, "identifiers": identifiers,
        "abstract": abstract, "access": access,
        "retrievals": [{"search_id": search_id, "provider": provider,
                        "url": request_url, "retrieved_at": retrieved_at}],
        "versions": versions or [],
    }


def empty_access():
    return {"is_open_access": None, "landing_page_url": None,
            "pdf_url": None, "license": None}


def crossref_year(item):
    for field in ("published", "published-print", "published-online", "issued"):
        parts = item.get(field, {}).get("date-parts", [])
        if parts and isinstance(parts[0], list) and parts[0] and isinstance(parts[0][0], int):
            return parts[0][0]
    return None


def parse_crossref(payload, search_id, request_url, retrieved_at):
    data = json.loads(payload)
    items = data.get("message", {}).get("items")
    if not isinstance(items, list):
        raise ValueError("Crossref response has no message.items array")
    sources, warnings = [], []
    for index, item in enumerate(items):
        titles = item.get("title") or []
        title = clean_text(titles[0]) if isinstance(titles, list) and titles else None
        if not title:
            warnings.append(f"Crossref item {index + 1}: missing title; record omitted.")
            continue
        authors = []
        for author in item.get("author") or []:
            name = clean_text(author.get("name")) or clean_text(" ".join(
                str(author.get(part) or "") for part in ("given", "family")))
            if name:
                authors.append(name)
        doi = normalize_doi(item.get("DOI"))
        url = http_url(item.get("URL")) or ("https://doi.org/" + doi if doi else None)
        access = empty_access()
        access["landing_page_url"] = url
        # A publisher PDF URL or a text-mining license is not proof of open access.
        sources.append(new_source(title, authors, crossref_year(item), url,
            {"doi": doi, "arxiv_id": None, "openalex_id": None},
            clean_text(item.get("abstract")), access, search_id, "crossref",
            request_url, retrieved_at))
    return sources, warnings


def parse_arxiv(payload, search_id, request_url, retrieved_at):
    root = ET.fromstring(payload)
    if root.tag != ATOM + "feed":
        raise ValueError("arXiv response is not an Atom feed")
    sources, warnings = [], []
    for index, entry in enumerate(root.findall(ATOM + "entry")):
        raw_id = entry.findtext(ATOM + "id") or ""
        if "api/errors" in raw_id:
            raise ValueError("arXiv query error: " + (clean_text(entry.findtext(ATOM + "summary")) or "invalid query"))
        title = clean_text(entry.findtext(ATOM + "title"))
        arxiv_version = normalize_arxiv_id(raw_id, keep_version=True)
        if not title or not arxiv_version:
            warnings.append(f"arXiv entry {index + 1}: missing title or invalid id; record omitted.")
            continue
        authors = [name for author in entry.findall(ATOM + "author")
                   if (name := clean_text(author.findtext(ATOM + "name")))]
        published = entry.findtext(ATOM + "published") or ""
        year = int(published[:4]) if re.match(r"^\d{4}-", published) else None
        url = "https://arxiv.org/abs/" + arxiv_version
        access = empty_access()
        access.update({"is_open_access": True, "landing_page_url": url})
        for link in entry.findall(ATOM + "link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                access["pdf_url"] = http_url(link.get("href"))
        sources.append(new_source(title, authors, year, url,
            {"doi": normalize_doi(entry.findtext(ARXIV + "doi")),
             "arxiv_id": normalize_arxiv_id(arxiv_version), "openalex_id": None},
            clean_text(entry.findtext(ATOM + "summary")), access,
            search_id, "arxiv", request_url, retrieved_at, [arxiv_version]))
    return sources, warnings


def reconstruct_abstract(index):
    if not isinstance(index, dict) or not index:
        return None
    positions = {}
    for word, places in index.items():
        if not isinstance(word, str) or not isinstance(places, list):
            continue
        for position in places:
            if isinstance(position, int) and not isinstance(position, bool) and 0 <= position < 100000:
                positions[position] = word
    return " ".join(positions[key] for key in sorted(positions)) or None


def parse_openalex(payload, search_id, request_url, retrieved_at):
    data = json.loads(payload)
    items = data.get("results")
    if not isinstance(items, list):
        raise ValueError("OpenAlex response has no results array")
    sources, warnings = [], []
    for index, item in enumerate(items):
        title = clean_text(item.get("title") or item.get("display_name"))
        if not title:
            warnings.append(f"OpenAlex item {index + 1}: missing title; record omitted.")
            continue
        authors = [name for author in item.get("authorships") or []
                   if (name := clean_text((author.get("author") or {}).get("display_name")))]
        locations = item.get("locations") or []
        primary = item.get("primary_location") or {}
        best_oa = item.get("best_oa_location") or {}
        doi = normalize_doi(item.get("doi"))
        arxiv_version = None
        for location in [primary, best_oa] + locations:
            for key in ("landing_page_url", "pdf_url"):
                arxiv_version = normalize_arxiv_id(location.get(key), keep_version=True)
                if arxiv_version:
                    break
            if arxiv_version:
                break
        openalex_id = http_url(item.get("id"))
        oa = item.get("open_access") or {}
        oa_status = oa.get("is_oa") if isinstance(oa.get("is_oa"), bool) else None
        landing = http_url(primary.get("landing_page_url")) or ("https://doi.org/" + doi if doi else None)
        url = landing or openalex_id
        access = {"is_open_access": oa_status, "landing_page_url": landing,
                  "pdf_url": http_url(best_oa.get("pdf_url")),
                  "license": clean_text(best_oa.get("license"))}
        year = item.get("publication_year")
        year = year if isinstance(year, int) and not isinstance(year, bool) else None
        sources.append(new_source(title, authors, year, url,
            {"doi": doi, "arxiv_id": normalize_arxiv_id(arxiv_version), "openalex_id": openalex_id},
            reconstruct_abstract(item.get("abstract_inverted_index")), access,
            search_id, "openalex", request_url, retrieved_at,
            [arxiv_version] if arxiv_version else []))
    return sources, warnings


def _identifier_keys(source):
    ids = source.get("identifiers") or {}
    keys = []
    for field, normalize in (("doi", normalize_doi), ("arxiv_id", normalize_arxiv_id),
                             ("openalex_id", http_url)):
        value = normalize(ids.get(field))
        if value:
            keys.append((field, value))
    return keys


def _merge_source(target, incoming):
    conflicts = target.setdefault("field_conflicts", [])
    for field in ("title", "authors", "year_or_version", "url", "abstract"):
        old, new = target.get(field), incoming.get(field)
        if not old and new:
            target[field] = copy.deepcopy(new)
        elif old and new and old != new:
            conflict = {"field": field, "values": [old, new]}
            if conflict not in conflicts:
                conflicts.append(copy.deepcopy(conflict))
    for field in ("retrievals", "versions", "prior_review"):
        if field in incoming:
            target.setdefault(field, [])
            for item in incoming[field]:
                if item not in target[field]:
                    target[field].append(copy.deepcopy(item))
    for field in ("identifiers", "access"):
        target.setdefault(field, {})
        for key, value in (incoming.get(field) or {}).items():
            old = target[field].get(key)
            if old is None:
                target[field][key] = value
            elif value is not None and old != value:
                conflict = {"field": field + "." + key, "values": [old, value]}
                if conflict not in conflicts:
                    conflicts.append(conflict)
    # API metadata may complete an unverified record, but merging cannot borrow
    # an abstract/full-text review from a different record or version.
    if target.get("verification") == "unverified" and incoming.get("verification") == "metadata_checked":
        target["verification"] = "metadata_checked"
        target["locator"] = incoming.get("locator")
        target["checked_at"] = incoming.get("checked_at")


def deduplicate_sources(sources):
    """Pure helper: merge only shared stable IDs; keep title matches separate.

    Returns copies with deterministic source IDs. DOI/arXiv bridges are handled
    transitively. Version identifiers and conflicting metadata remain available.
    """
    parent = list(range(len(sources)))
    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    lookup = {}
    for index, source in enumerate(sources):
        for key in _identifier_keys(source):
            if key in lookup:
                first, second = find(index), find(lookup[key])
                parent[max(first, second)] = min(first, second)
            else:
                lookup[key] = index
    groups = {}
    for index, source in enumerate(sources):
        groups.setdefault(find(index), []).append(source)
    result = []
    for members in groups.values():
        merged = copy.deepcopy(members[0])
        for member in members[1:]:
            _merge_source(merged, member)
        merged["id"] = f"S{len(result) + 1:03d}"
        result.append(merged)
    return result


def export_direction_sources(sources):
    """Return (compatible base-field sources, [{id, reason}, ...]).

    This is a sources-array adapter, never a complete research_direction_map.
    Incomplete records are kept in the original search record and reported here.
    """
    accepted, skipped = [], []
    for source in sources:
        missing = []
        for field in ("id", "title", "year_or_version"):
            if not source.get(field):
                missing.append(field)
        authors = source.get("authors")
        if not (isinstance(authors, list) and authors and all(isinstance(x, str) and x.strip() for x in authors)):
            missing.append("authors")
        if source.get("verification") != "unverified":
            if not http_url(source.get("url")):
                missing.append("url")
            for field in ("checked_at", "locator"):
                if not source.get(field):
                    missing.append(field)
        if missing:
            skipped.append({"id": source.get("id"), "reason": "missing/invalid " + ", ".join(missing)})
        else:
            accepted.append({field: copy.deepcopy(source.get(field)) for field in BASE_FIELDS})
    return accepted, skipped


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise URLError("refused non-HTTPS redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(newurl).netloc != urlsplit(req.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


class HttpClient:
    def __init__(self, timeout=20, retries=2, mailto=None, api_key=None):
        self.timeout, self.retries = timeout, retries
        self.api_key = api_key
        self.user_agent = "YanzhengLiteratureSearch/1.0" + (f" (mailto:{mailto})" if mailto else "")
        self.opener = build_opener(SafeRedirect())
        self.last_arxiv_request = None

    def safe_error(self, exc):
        if isinstance(exc, HTTPError):
            return f"HTTP {exc.code}"
        message = str(getattr(exc, "reason", exc))
        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")
        message = re.sub(r"(?i)(api_key|token|authorization)=([^\s&]+)", r"\1=[REDACTED]", message)
        return type(exc).__name__ + ": " + message[:300]

    @staticmethod
    def retry_delay(headers, attempt):
        value = headers.get("Retry-After") if headers else None
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = 0
            # Never shorten a long server-requested wait and retry prematurely.
            if delay > 30:
                return None
            return max(delay, 0)
        return min(2 ** attempt, 8)

    def get(self, url, provider):
        attempts, searched_at = 0, None
        headers = {"User-Agent": self.user_agent,
                   "Accept": "application/atom+xml" if provider == "arxiv" else "application/json"}
        if provider == "openalex" and self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        while attempts <= self.retries:
            if provider == "arxiv" and self.last_arxiv_request is not None:
                time.sleep(max(0, 3 - (time.monotonic() - self.last_arxiv_request)))
            attempts += 1
            searched_at = searched_at or iso_now()
            if provider == "arxiv":
                self.last_arxiv_request = time.monotonic()
            try:
                with self.opener.open(Request(url, headers=headers), timeout=self.timeout) as response:
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ValueError("response exceeds 8 MiB safety limit")
                return payload, attempts, searched_at, None
            except HTTPError as exc:
                error = self.safe_error(exc)
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                delay = self.retry_delay(exc.headers, attempts - 1)
            except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError, HTTPException) as exc:
                error, retryable, delay = self.safe_error(exc), True, 2 ** (attempts - 1)
            except ValueError as exc:
                return None, attempts, searched_at, self.safe_error(exc)
            if not retryable or attempts > self.retries or delay is None:
                return None, attempts, searched_at, error
            time.sleep(delay)
        return None, attempts, searched_at, "retry limit reached"


def arxiv_query(query):
    if re.search(r"\b(?:all|ti|au|abs|cat|jr|id|submittedDate|lastUpdatedDate):", query):
        return query
    terms = re.findall(r'"[^"]+"|\S+', query)
    return " AND ".join('all:"' + term.strip('"').replace('\\', '\\\\').replace('"', '\\"') + '"' for term in terms)


def build_url(provider, query, limit, year_from=None, year_to=None):
    if provider == "crossref":
        params = {"query.bibliographic": query, "rows": limit}
        filters = []
        if year_from:
            filters.append(f"from-pub-date:{year_from}-01-01")
        if year_to:
            filters.append(f"until-pub-date:{year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        return "https://api.crossref.org/works?" + urlencode(params), query
    if provider == "arxiv":
        effective = arxiv_query(query)
        if year_from or year_to:
            # This filters first submission date, not journal publication date.
            effective = f"({effective}) AND submittedDate:[{year_from or 1:04d}01010000 TO {year_to or 9999:04d}12312359]"
        params = {"search_query": effective, "start": 0, "max_results": limit,
                  "sortBy": "relevance", "sortOrder": "descending"}
        return "https://export.arxiv.org/api/query?" + urlencode(params), effective
    if provider == "openalex":
        params = {"search": query, "per_page": limit}
        if year_from or year_to:
            params["filter"] = f"publication_year:{year_from or 1}-{year_to or 9999}"
        return "https://api.openalex.org/works?" + urlencode(params), query
    raise ValueError("unknown provider")


def run_search(idea, queries, providers, limit=10, year_from=None, year_to=None,
               timeout=20, retries=2, mailto=None, api_key=None):
    client = HttpClient(timeout, retries, mailto, api_key)
    record = {
        "schema_version": "1.0", "record_type": "research_literature_search",
        "created_at": iso_now(), "request": {"idea": idea, "queries": queries,
            "providers": providers, "year_from": year_from, "year_to": year_to,
            "max_results_per_query": limit},
        "provenance": {"generator": "literature-search/search_literature.py",
            "live_search_performed": False, "external_model_api_called": False,
            "new_experiments_executed": False},
        "search_log": [], "sources": [], "evidence": [],
        "limitations": [
            "API metadata and abstract text have not been read for research claims; verification is at most metadata_checked.",
            "Only the first bounded result page of each provider/query is fetched; this is not an exhaustive review or proof of novelty.",
            "Title similarity alone is not used to merge papers; preprint and published versions may remain separate without shared identifiers.",
            "Full text is not downloaded; provider access/license metadata does not authorize redistributing subscribed content.",
        ],
        "next_action": "Review relevance and bibliographic metadata, then read selected abstracts or permitted full text and record precise locators before adding claim evidence.",
    }
    if "arxiv" in providers and (year_from or year_to):
        record["limitations"].append("arXiv year bounds use first submission date; other providers use publication dates.")
    parsers = {"crossref": parse_crossref, "arxiv": parse_arxiv, "openalex": parse_openalex}
    for query in queries:
        for provider in providers:
            search_id = f"Q{len(record['search_log']) + 1:03d}"
            url, effective = build_url(provider, query, limit, year_from, year_to)
            payload, attempts, searched_at, error = client.get(url, provider)
            sources, warnings = [], []
            if payload is not None:
                try:
                    sources, warnings = parsers[provider](payload, search_id, url, iso_now())
                except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError) as exc:
                    error = "invalid provider response: " + client.safe_error(exc)
            # A page that contains only unusable records is a failed parse, not
            # an empty successful query. Retain the omitted-record explanation.
            if not sources and warnings:
                error = "all returned records were unusable; " + "; ".join(warnings)[:500]
            status = "failed" if error else ("success" if sources else "empty")
            record["search_log"].append({"id": search_id, "provider": provider,
                "query": query, "status": status, "searched_at": searched_at,
                "attempts": attempts, "result_count": len(sources) if not error else 0,
                "error": error, "request_url": url, "effective_query": effective})
            if attempts:
                record["provenance"]["live_search_performed"] = True
            if not error:
                record["sources"].extend(sources)
            else:
                record["limitations"].append(f"{search_id} {provider} query failed: {error}")
            record["limitations"].extend(warnings)
    record["sources"] = deduplicate_sources(record["sources"])
    for source in record["sources"]:
        if source["verification"] == "unverified":
            record["limitations"].append(f"{source['id']}: bibliographic fields incomplete; retained as unverified.")
        if source.get("field_conflicts"):
            record["limitations"].append(f"{source['id']}: shared identifiers have conflicting metadata; review field_conflicts and versions.")
    return record


def write_json(path, value, overwrite=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", required=True, help="Repeat for alternative keyword queries; use English synonyms where useful.")
    parser.add_argument("--idea", help="Original research question; defaults to joined queries.")
    parser.add_argument("--providers", default="crossref,arxiv", help="Comma-separated crossref,arxiv,openalex. OpenAlex key is optional via OPENALEX_API_KEY.")
    parser.add_argument("--limit", type=int, default=10, help="Results per provider/query, 1..50 (no pagination).")
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--timeout", type=float, default=20, help="Per-request socket timeout in seconds, 1..60.")
    parser.add_argument("--retries", type=int, default=2, help="Retries after initial attempt, 0..2.")
    parser.add_argument("--mailto", help="Optional real contact email in User-Agent; not a campus login.")
    parser.add_argument("--output", required=True, help="Output JSON path; existing files are refused unless --overwrite.")
    parser.add_argument("--direction-sources", help="Optional separate sources-array JSON for the direction-map adapter.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    queries = list(dict.fromkeys(query.strip() for query in args.query if query.strip()))
    providers = list(dict.fromkeys(name.strip().lower() for name in args.providers.split(",") if name.strip()))
    if not queries or not providers or any(provider not in PROVIDERS for provider in providers):
        parser.error("non-empty queries and valid providers are required")
    if not 1 <= args.limit <= 50 or not 1 <= args.timeout <= 60 or not 0 <= args.retries <= 2:
        parser.error("require limit 1..50, timeout 1..60, retries 0..2")
    if any(year is not None and not 1 <= year <= 9999 for year in (args.year_from, args.year_to)):
        parser.error("year bounds must be 1..9999")
    if args.year_from and args.year_to and args.year_from > args.year_to:
        parser.error("--year-from exceeds --year-to")
    if args.mailto and not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", args.mailto):
        parser.error("--mailto must be a contact email without whitespace")
    paths = [Path(args.output).resolve()]
    if args.direction_sources:
        paths.append(Path(args.direction_sources).resolve())
    if len(set(paths)) != len(paths):
        parser.error("output and direction-sources must be different paths")
    for path in paths:
        if path.exists() and (not args.overwrite or not path.is_file()):
            parser.error(f"output path exists: {path}; use --overwrite for an existing file")
    key = os.environ.get("OPENALEX_API_KEY", "").strip() or None
    record = run_search(args.idea.strip() if args.idea and args.idea.strip() else " | ".join(queries),
        queries, providers, args.limit, args.year_from, args.year_to,
        args.timeout, args.retries, args.mailto, key)
    accepted, skipped = export_direction_sources(record["sources"])
    if args.direction_sources and skipped:
        record["limitations"].append("Direction-source adapter skipped incomplete records: " + json.dumps(skipped, ensure_ascii=False))
    try:
        write_json(paths[0], record, args.overwrite)
        if args.direction_sources:
            write_json(paths[1], accepted, args.overwrite)
    except OSError as exc:
        print("Unable to save output: " + str(exc), file=sys.stderr)
        return 2
    failed = sum(log["status"] == "failed" for log in record["search_log"])
    print(f"Saved {len(record['sources'])} unique sources; {failed}/{len(record['search_log'])} queries failed: {paths[0]}")
    if args.direction_sources:
        print(f"Direction sources: {len(accepted)} exported, {len(skipped)} skipped (sources array only).")
    return 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
