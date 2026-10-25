#!/usr/bin/env python3
"""Import JSON/RIS bibliography without claiming a live search or new review."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from search_literature import deduplicate_sources, export_direction_sources, iso_now


def text(value):
    if value is None:
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result or None


def web_url(value):
    value = text(value)
    return value if value and urlparse(value).scheme in ("https", "http") and urlparse(value).netloc else None


def doi_id(value):
    value = text(value)
    if not value:
        return None
    value = unquote(re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I))
    return value.lower() if re.fullmatch(r"10\.\d{4,9}/\S+", value) else None


def arxiv_id(value):
    value = text(value)
    if not value:
        return None
    value = re.sub(r"^(?:https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/|arxiv:\s*)", "", value, flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    return value if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})(?:v\d+)?", value) else None


def json_entries(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        raise ValueError("JSON must be a source list or a research record object")
    if isinstance(data.get("sources"), list):
        return data["sources"]
    if isinstance(data.get("references"), list):
        return data["references"]
    result = []
    for topic in data.get("topics", []):
        if isinstance(topic, dict) and isinstance(topic.get("references"), list):
            result.extend(topic["references"])
    selected = data.get("selected_topic")
    if isinstance(selected, dict):
        result.extend(selected.get("references", []))
    if result:
        return result
    # A single bibliographic object is also useful for DOI/title import.
    if "title" in data:
        return [data]
    raise ValueError("JSON contains no supported sources/references list")


def parse_ris(content):
    entries, current, last_tag = [], None, None
    for number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        match = re.match(r"^([A-Z0-9]{2})\s{2}-\s?(.*)$", line)
        if match:
            tag, value = match.groups()
            if tag == "TY":
                if current is not None:
                    raise ValueError(f"RIS line {number}: previous record has no ER terminator")
                current = {"TY": [value]}
            elif current is None:
                raise ValueError(f"RIS line {number}: record must start with TY")
            elif tag == "ER":
                entries.append(current)
                current, last_tag = None, None
                continue
            else:
                current.setdefault(tag, []).append(value)
            last_tag = tag
        elif current is not None and last_tag:
            current[last_tag][-1] += " " + line.strip()
        else:
            raise ValueError(f"RIS line {number}: invalid record syntax")
    if current is not None:
        raise ValueError("RIS final record has no ER terminator")

    def first(entry, *tags):
        return next((entry[tag][0] for tag in tags if entry.get(tag)), None)

    return [{"title": first(entry, "TI", "T1", "CT"),
             "authors": entry.get("AU", entry.get("A1", [])),
             "year_or_version": first(entry, "PY", "Y1", "DA"),
             "url": first(entry, "UR"), "doi": first(entry, "DO"),
             "abstract": first(entry, "AB", "N2"), "imported_type": first(entry, "TY")}
            for entry in entries]


def normalize(entry, origin, position):
    if not isinstance(entry, dict) or not text(entry.get("title")):
        return None, f"Skipped input item {position}: missing nonempty title/object"
    raw_authors = entry.get("authors", [])
    if isinstance(raw_authors, str):
        authors = [text(raw_authors)] if text(raw_authors) else []
    elif isinstance(raw_authors, list):
        authors = [text(author) for author in raw_authors if isinstance(author, str) and text(author)]
    else:
        authors = []
    identifiers = entry.get("identifiers") if isinstance(entry.get("identifiers"), dict) else {}
    url = web_url(entry.get("url"))
    doi = doi_id(identifiers.get("doi") or entry.get("doi") or entry.get("DOI"))
    doi = doi or doi_id(url)
    identifier_arxiv = arxiv_id(identifiers.get("arxiv_id") or entry.get("arxiv_id"))
    url_arxiv = arxiv_id(url)
    arxiv = identifier_arxiv or url_arxiv
    if arxiv:
        arxiv = re.sub(r"v\d+$", "", arxiv)
    raw_versions = entry.get("versions") if isinstance(entry.get("versions"), list) else []
    versions = list(dict.fromkeys(value for raw_version in raw_versions + [url_arxiv, identifier_arxiv]
                                  if (value := arxiv_id(raw_version))))
    title = text(entry["title"])
    year = entry.get("year_or_version", entry.get("year"))
    year = year if isinstance(year, int) and not isinstance(year, bool) else text(year)
    key = doi or arxiv or json.dumps([title, authors, year], ensure_ascii=False)
    source = {
        "id": "s-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
        "title": title, "authors": authors, "year_or_version": year,
        "url": url or ("https://doi.org/" + doi if doi else "https://arxiv.org/abs/" + arxiv if arxiv else None),
        "origin": origin, "verification": "unverified", "locator": None, "checked_at": None,
        "identifiers": {"doi": doi, "arxiv_id": arxiv, "openalex_id": identifiers.get("openalex_id")},
        "abstract": text(entry.get("abstract")),
        "access": {"is_open_access": None, "landing_page_url": url, "pdf_url": None, "license": None},
        "retrievals": [],
        "versions": versions,
    }
    prior = {field: entry[field] for field in ("verification", "checked_at", "locator", "reviewed_on", "status") if field in entry}
    if prior:
        source["prior_review"] = [prior]
    return source, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--format", choices=("auto", "json", "ris"), default="auto")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--origin", choices=("provided_material", "prior_project"), default="provided_material")
    parser.add_argument("--idea", default="导入已有文献题录，后续按研究问题重新核对")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--direction-sources", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        outputs = [args.output] + ([args.direction_sources] if args.direction_sources else [])
        resolved = [path.resolve() for path in outputs]
        if len(set(resolved)) != len(resolved) or args.input.resolve() in resolved:
            raise ValueError("Input and output paths must be distinct")
        for path in outputs:
            if path.exists() and not args.overwrite:
                raise ValueError(f"Output exists; choose a new name or --overwrite: {path}")
        raw = args.input.read_bytes()
        content = raw.decode(args.encoding)
        fmt = args.format if args.format != "auto" else args.input.suffix.lower().lstrip(".")
        if fmt not in ("json", "ris"):
            raise ValueError("Supported formats are JSON and RIS; use --format explicitly if needed")
        entries = json_entries(json.loads(content)) if fmt == "json" else parse_ris(content)
        sources, skipped = [], []
        for position, entry in enumerate(entries, 1):
            source, error = normalize(entry, args.origin, position)
            if source:
                sources.append(source)
            elif error:
                skipped.append(error)
        sources = deduplicate_sources(sources)
        if isinstance(sources, tuple):
            sources, dedup_notes = sources
            skipped.extend(dedup_notes)
        record = {
            "schema_version": "1.0", "record_type": "research_literature_search", "created_at": iso_now(),
            "request": {"idea": args.idea, "queries": [], "providers": [], "year_from": None, "year_to": None, "max_results_per_query": 50},
            "provenance": {"generator": "literature-search/import_literature.py", "live_search_performed": False,
                           "external_model_api_called": False, "new_experiments_executed": False},
            "search_log": [], "sources": sources, "evidence": [],
            "limitations": ["Imported bibliography was not searched online or re-reviewed in this run.",
                            "Original verification is retained only as prior_review; imported sources start unverified."] + skipped,
            "next_action": "根据研究问题核对原始出处，阅读相关摘要或正文后更新核查等级与支撑位置。",
            "import_details": {"filename": args.input.name, "sha256": hashlib.sha256(raw).hexdigest(),
                               "format": fmt, "input_count": len(entries), "output_count": len(sources)},
        }
        adapted = None
        if args.direction_sources:
            adapted, exclusions = export_direction_sources(sources)
            record["direction_export"] = {"exported": len(adapted), "skipped": exclusions}
        from validate_literature_record import validate_record
        result = validate_record(record)
        if result:
            # Validator returns a list of errors, not scientific judgments.
            raise ValueError("Record validation failed: " + "; ".join(result))
        for path, data in [(args.output, record)] + ([(args.direction_sources, adapted)] if adapted is not None else []):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "sources": len(sources), "live_search_performed": False}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
