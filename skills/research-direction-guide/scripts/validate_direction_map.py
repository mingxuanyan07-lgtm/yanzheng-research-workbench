#!/usr/bin/env python3
"""Read-only structure/consistency check for research_direction_map v1.

Usage: python validate_direction_map.py path/to/map.json
PASS checks the contract, not source authenticity or scientific quality.
Only the standard library is used; this script makes no network calls.
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit


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

    def text_or_texts(value, path):
        if isinstance(value, str):
            string(value, path)
        elif isinstance(value, list):
            if not strings(value, path):
                fail(path, "expected non-empty string or non-empty string array")
        else:
            fail(path, "expected string or string array")

    def choice(value, path, choices):
        if not isinstance(value, str) or value not in choices:
            fail(path, "expected " + " / ".join(choices))

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
            parsed.port  # Detect malformed ports too.
            if (parsed.scheme not in ("http", "https") or not host
                    or any(c.isspace() for c in value)
                    or parsed.username or parsed.password
                    or host in ("localhost", "example.com", "example.org", "example.net")
                    or host.endswith((".invalid", ".example", ".test", ".localhost"))):
                raise ValueError()
        except ValueError:
            fail(path, "expected usable http(s) URL, not a placeholder")

    def hex64(value, path):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            fail(path, "expected 64 lowercase hexadecimal characters")
            return False
        return True

    def known(value, path):
        if value is None or (isinstance(value, (str, list, dict)) and not value):
            fail(path, "empty/null value is not a known fact")
        elif isinstance(value, str) and not value.strip():
            fail(path, "blank value is not a known fact")
        elif isinstance(value, dict):
            for key, item in value.items():
                known(item, f"{path}.{key}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                known(item, f"{path}[{i}]")

    def identified(items, path):
        found = {}
        for i, item in enumerate(items):
            item_path = f"{path}[{i}]"
            item = obj(item, item_path, "id")
            item_id = item.get("id")
            if string(item_id, item_path + ".id"):
                if item_id in found:
                    fail(item_path + ".id", "duplicate id")
                found[item_id] = item
        return found

    r = obj(record, "$", "schema_version record_type created_at stage initial_idea intake questions provenance search_log sources topics recommendation selected_topic_id next_action")
    choice(r.get("schema_version"), "$.schema_version", ("1.0",))
    choice(r.get("record_type"), "$.record_type", ("research_direction_map",))
    iso(r.get("created_at"), "$.created_at", timezone=True)
    stage = r.get("stage")
    choice(stage, "$.stage", ("clarifying", "directions_ready", "topic_selected"))
    string(r.get("initial_idea"), "$.initial_idea")
    string(r.get("next_action"), "$.next_action")

    intake = obj(r.get("intake"), "$.intake", "known assumptions unknowns")
    facts = obj(intake.get("known"), "$.intake.known", "")
    for key, value in facts.items():
        known(value, f"$.intake.known.{key}")
    strings(intake.get("assumptions"), "$.intake.assumptions")
    strings(intake.get("unknowns"), "$.intake.unknowns")
    questions = array(r.get("questions"), "$.questions")
    identified(questions, "$.questions")
    for i, question in enumerate(questions):
        p = f"$.questions[{i}]"
        question = obj(question, p, "id question why_it_matters")
        for key in ("question", "why_it_matters"):
            string(question.get(key), p + "." + key)

    provenance = obj(r.get("provenance"), "$.provenance", "generator live_search_performed external_model_api_called new_experiments_executed")
    string(provenance.get("generator"), "$.provenance.generator")
    for key in ("live_search_performed", "external_model_api_called", "new_experiments_executed"):
        if type(provenance.get(key)) is not bool:
            fail("$.provenance." + key, "expected boolean")
    if provenance.get("new_experiments_executed") is not False:
        fail("$.provenance.new_experiments_executed", "must be false for this skill")
    search_log = array(r.get("search_log"), "$.search_log")
    for i, entry in enumerate(search_log):
        p = f"$.search_log[{i}]"
        entry = obj(entry, p, "query searched_at outcome")
        string(entry.get("query"), p + ".query")
        choice(entry.get("outcome"), p + ".outcome", ("executed", "planned"))
        if entry.get("outcome") == "executed" or entry.get("searched_at") is not None:
            iso(entry.get("searched_at"), p + ".searched_at")
    executed = any(isinstance(e, dict) and e.get("outcome") == "executed" for e in search_log)
    if provenance.get("live_search_performed") is True and not executed:
        fail("$.search_log", "live search requires an executed entry")
    if provenance.get("live_search_performed") is False and executed:
        fail("$.search_log", "executed search contradicts live_search_performed=false")

    sources = array(r.get("sources"), "$.sources")
    source_ids = identified(sources, "$.sources")
    for i, source in enumerate(sources):
        p = f"$.sources[{i}]"
        source = obj(source, p, "id title authors year_or_version url origin verification locator checked_at")
        string(source.get("title"), p + ".title")
        text_or_texts(source.get("authors"), p + ".authors")
        year = source.get("year_or_version")
        if type(year) is not int or year <= 0:
            string(year, p + ".year_or_version")
        choice(source.get("origin"), p + ".origin", ("this_run", "provided_material", "prior_project"))
        verified = source.get("verification")
        choice(verified, p + ".verification", ("unverified", "metadata_checked", "abstract_checked", "fulltext_checked"))
        if verified != "unverified" or source.get("url") is not None:
            url(source.get("url"), p + ".url")
        if verified != "unverified" or source.get("checked_at") is not None:
            iso(source.get("checked_at"), p + ".checked_at")
        if verified in ("abstract_checked", "fulltext_checked") or source.get("locator") is not None:
            string(source.get("locator"), p + ".locator")

    topics = array(r.get("topics"), "$.topics")
    topic_ids = identified(topics, "$.topics")
    for i, topic in enumerate(topics):
        p = f"$.topics[{i}]"
        topic = obj(topic, p, "id title question known_work potential_difference novelty_status evidence first_validation resources stop_conditions risks open_questions readiness")
        for key in ("title", "question", "potential_difference"):
            string(topic.get(key), p + "." + key)
        text_or_texts(topic.get("known_work"), p + ".known_work")
        choice(topic.get("novelty_status"), p + ".novelty_status", ("needs_review",))
        choice(topic.get("readiness"), p + ".readiness", ("provisional", "ready_to_plan"))
        if stage == "clarifying" and topic.get("readiness") != "provisional":
            fail(p + ".readiness", "clarifying candidates must be provisional")
        for j, evidence in enumerate(array(topic.get("evidence"), p + ".evidence")):
            ep = f"{p}.evidence[{j}]"
            evidence = obj(evidence, ep, "source_id relation claim")
            evidence_fields = {"source_id", "relation", "claim", "note_id", "note_snapshot"}
            if isinstance(evidence, dict):
                unknown = set(evidence) - evidence_fields
                if unknown:
                    fail(ep, "unexpected evidence fields: " + ", ".join(sorted(unknown)))
            source_id = evidence.get("source_id")
            string(source_id, ep + ".source_id")
            source = source_ids.get(source_id) if isinstance(source_id, str) else None
            if source is None:
                fail(ep + ".source_id", "must reference a source id")
            relation = evidence.get("relation")
            choice(relation, ep + ".relation", ("supports", "limits", "background", "unverified_lead"))
            if source and source.get("verification") == "unverified" and relation != "unverified_lead":
                fail(ep + ".relation", "unverified sources only allow unverified_lead")
            if source and source.get("verification") == "metadata_checked" and relation in ("supports", "limits"):
                fail(ep + ".relation", "metadata alone cannot support/limit a scientific claim")
            string(evidence.get("claim"), ep + ".claim")
            note_id = evidence.get("note_id")
            snapshot = evidence.get("note_snapshot")
            if relation in ("supports", "limits"):
                note_id_valid = string(note_id, ep + ".note_id")
                if snapshot is None:
                    fail(ep + ".note_snapshot", "supports/limits require a bound reading-note snapshot")
                else:
                    snapshot = obj(snapshot, ep + ".note_snapshot",
                                   "note_id note_sha256 content_sha256 run_id source_id level locator checked_at origin confirmed caveat")
                    snapshot_fields = {"note_id", "note_sha256", "content_sha256", "run_id", "source_id",
                                       "level", "locator", "checked_at", "origin", "confirmed", "caveat",
                                       "source_version", "source_sha256", "source_sha256_scope",
                                       "source_fingerprint_caveat"}
                    if isinstance(snapshot, dict):
                        unknown = set(snapshot) - snapshot_fields
                        if unknown:
                            fail(ep + ".note_snapshot", "unexpected snapshot fields: " + ", ".join(sorted(unknown)))
                    snapshot_note_id = snapshot.get("note_id")
                    string(snapshot_note_id, ep + ".note_snapshot.note_id")
                    if note_id_valid and isinstance(snapshot_note_id, str) and snapshot_note_id != note_id:
                        fail(ep + ".note_snapshot.note_id", "must equal evidence.note_id")
                    hex64(snapshot.get("note_sha256"), ep + ".note_snapshot.note_sha256")
                    hex64(snapshot.get("content_sha256"), ep + ".note_snapshot.content_sha256")
                    for key in ("run_id", "source_id", "locator", "caveat"):
                        string(snapshot.get(key), ep + ".note_snapshot." + key)
                    choice(snapshot.get("level"), ep + ".note_snapshot.level",
                           ("abstract_checked", "fulltext_checked"))
                    iso(snapshot.get("checked_at"), ep + ".note_snapshot.checked_at")
                    if snapshot.get("confirmed") is not True:
                        fail(ep + ".note_snapshot.confirmed", "must be true for a bound reading note")
                    choice(snapshot.get("origin"), ep + ".note_snapshot.origin", ("user_attested",))
                    declared = [snapshot.get("source_version"), snapshot.get("source_sha256"),
                                snapshot.get("source_sha256_scope")]
                    declared_present = [value is not None for value in declared]
                    if any(declared_present):
                        if not all(declared_present):
                            fail(ep + ".note_snapshot",
                                 "declared source version/hash/scope must be all present or all absent")
                        string(snapshot.get("source_version"), ep + ".note_snapshot.source_version")
                        hex64(snapshot.get("source_sha256"), ep + ".note_snapshot.source_sha256")
                        choice(snapshot.get("source_sha256_scope"), ep + ".note_snapshot.source_sha256_scope",
                               ("user_declared_fulltext_file", "user_declared_metadata"))
                        string(snapshot.get("source_fingerprint_caveat"),
                               ep + ".note_snapshot.source_fingerprint_caveat")
                    elif snapshot.get("source_fingerprint_caveat") is not None:
                        fail(ep + ".note_snapshot.source_fingerprint_caveat",
                             "caveat requires the declared version/hash/scope triple")
            else:
                if note_id is not None:
                    fail(ep + ".note_id", "background/unverified_lead must not bind a reading note")
                if snapshot is not None:
                    fail(ep + ".note_snapshot", "background/unverified_lead must not include a note snapshot")
        first = obj(topic.get("first_validation"), p + ".first_validation", "steps baseline metrics success_signal")
        strings(first.get("steps"), p + ".first_validation.steps")
        strings(first.get("metrics"), p + ".first_validation.metrics")
        string(first.get("baseline"), p + ".first_validation.baseline")
        string(first.get("success_signal"), p + ".first_validation.success_signal")
        for key in ("resources", "stop_conditions", "risks", "open_questions"):
            strings(topic.get(key), p + "." + key)
        if topic.get("readiness") == "ready_to_plan":
            for key in ("steps", "metrics"):
                if not first.get(key):
                    fail(p + ".first_validation." + key, "ready_to_plan requires at least one entry")
            for key in ("resources", "stop_conditions"):
                if not topic.get(key):
                    fail(p + "." + key, "ready_to_plan requires at least one entry")

    recommendation = obj(r.get("recommendation"), "$.recommendation", "topic_id reason would_change_if")
    recommended = recommendation.get("topic_id")
    if recommended is not None and (not isinstance(recommended, str) or recommended not in topic_ids):
        fail("$.recommendation.topic_id", "must be null or a topic id")
    string(recommendation.get("reason"), "$.recommendation.reason")
    strings(recommendation.get("would_change_if"), "$.recommendation.would_change_if")
    selected = r.get("selected_topic_id")
    if stage == "topic_selected":
        if not isinstance(selected, str) or selected not in topic_ids:
            fail("$.selected_topic_id", "topic_selected requires a topic id")
        string(facts.get("selection_basis"), "$.intake.known.selection_basis")
    elif selected is not None:
        fail("$.selected_topic_id", "must be null before user selection")
    if stage == "clarifying" and not questions:
        fail("$.questions", "clarifying requires unanswered questions")
    if stage in ("directions_ready", "topic_selected") and not topics:
        fail("$.topics", "stage requires at least one topic")
    return errors


def main():
    if len(sys.argv) != 2:
        print("Usage: validate_direction_map.py <JSON file>", file=sys.stderr)
        return 1
    try:
        record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"FAIL: cannot read JSON: {exc}", file=sys.stderr)
        return 1
    errors = validate(record)
    if errors:
        for error in errors[:12]:
            print("FAIL: " + error, file=sys.stderr)
        if len(errors) > 12:
            print(f"... {len(errors) - 12} more errors", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
