"""Package only the reviewed public-case evidence and completed recording."""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/public-cases/20261023"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    human = json.loads((OUT / "human-confirmation.json").read_text(encoding="utf-8"))
    assert digest(OUT / "HUMAN-REVIEW.md") == human["review_document_sha256"]
    calls = json.loads((OUT / "model-calls.json").read_text(encoding="utf-8"))
    assert len(calls) == 9 and all(call["request_id"] for call in calls)
    video = json.loads((OUT / "video-validation.json").read_text(encoding="utf-8"))
    assert video["duration_seconds"] == 180 and digest(OUT / video["file"]) == video["sha256"]
    recording = json.loads((OUT / "video/recording.json").read_text(encoding="utf-8"))
    assert not recording["errors"] and not recording["fixture"]
    runner = OUT / "A/run_experiment.py"
    shutil.copyfile(ROOT / "scripts/public_case_experiment.py", runner)
    smoke = json.loads((OUT / "smoke/run.json").read_text(encoding="utf-8"))
    assert digest(runner) == smoke["runner_sha256"]
    for filename in ("public-case-all-frontend.log", "public-case-workflow-tests.log", "public-case-proposal-tests.log"):
        target = OUT / "verification" / filename
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "tmp" / filename, target)
    snapshot = OUT / "case-database.snapshot.db"
    # SQLite's backup API incorporates the WAL and produces a consistent snapshot.
    with sqlite3.connect((OUT / "cases.db").resolve().as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(snapshot) as destination:
            source.backup(destination)
            assert destination.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert destination.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2
            assert destination.execute("SELECT COUNT(*) FROM paper_document_originals").fetchone()[0] == 2
    names = ["index.html", "README.md", "HUMAN-REVIEW.md", "human-confirmation.json", "case-index.json",
             "model-calls.json", "proposal-reviews.json", "validation.json", "video-validation.json",
             "workbench-backup.json", "case-database.snapshot.db", video["file"], "video/recording.json",
             "video/operation-history.json"]
    for key in ("A", "B"):
        names += [f"{key}/{filename}" for filename in ("project.json", "direction-map.json", "literature.json",
                  "relevant-page.txt", "decision-package.md")]
        names += [path.relative_to(OUT).as_posix() for path in (OUT / key).glob("operator-review-*.json")]
    names += ["A/EXECUTION-PLAN.md", "A/model-plan.md", "A/run_experiment.py", "B/NARROWING-DECISION.md",
              "B/failed-metadata.json"]
    for directory in ("model-audit", "smoke", "verification"):
        names += [path.relative_to(OUT).as_posix() for path in (OUT / directory).glob("*") if path.is_file()]
    for version in ("0909.4061v2", "1504.05477v4"):
        names += [f"sources/{version}.pdf", f"sources/{version}.pdf.retrieval.json"]
    names = sorted(set(names))
    assert all((OUT / name).is_file() for name in names)
    # Inspect the exported text, without logging values, before packaging requests.
    for name in names:
        if Path(name).suffix in (".json", ".md", ".txt", ".log", ".py", ".html"):
            assert not re.search(r"sk-[A-Za-z0-9]{20,}", (OUT / name).read_text(encoding="utf-8")), f"Unexpected credential pattern: {name}"
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
        def handle_starttag(self, tag, attrs):
            self.links.extend(value for key, value in attrs if key == "href")
    links = Links()
    links.feed((OUT / "index.html").read_text(encoding="utf-8"))
    for href in links.links:
        parsed = urlsplit(href)
        if not parsed.scheme and parsed.path:
            assert unquote(parsed.path) in names, f"Unpackaged report link: {href}"
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "scope": "Two public-paper cases; real model calls and delegated human decisions; formal experiments not completed",
                "files": [{"path": name, "bytes": (OUT / name).stat().st_size, "sha256": digest(OUT / name)} for name in names]}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    names.append("MANIFEST.json")
    archive = OUT / "研证-真实案例与演示.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name in names:
            package.write(OUT / name, arcname=name)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        for entry in manifest["files"]:
            assert hashlib.sha256(package.read(entry["path"])).hexdigest() == entry["sha256"]
    result = {"file": archive.name, "bytes": archive.stat().st_size, "sha256": digest(archive),
              "files": len(names), "zip_crc_passed": True, "manifest_hashes_passed": True,
              "report_links_packaged": True, "human_memo_hash_passed": True, "model_calls": len(calls)}
    (OUT / "bundle-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
