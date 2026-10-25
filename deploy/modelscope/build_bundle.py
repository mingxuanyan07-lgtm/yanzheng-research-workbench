"""Build an explicit public runtime snapshot; never copy the working directory recursively."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "webapp/evidence_registry_manifest.json"
REGISTRY_SHA256 = "680f0551ed3d01bed45447b8356268c457686fdb4bed12d66c027a83af2236b0"
PYTHON_MODULES = (
    "app", "catalog", "engine", "evidence_access", "evidence_registry",
    "experiment_worker", "experiments", "lean_api", "lean_service", "lean_worker",
    "literature_service", "model_service", "planner", "storage", "theory",
    "theory_examples", "theory_review", "workflow",
)
STATIC_FILES = (
    "index.html", "app.js", "styles.css", "workbench-polish.css",
    "lean-ui.js", "companion.js", "assets/favicon.svg", "assets/icons.svg",
    "assets/research-dragon.png", "assets/lucide/LICENSE",
)
LUCIDE_ICONS = (
    "activity", "arrow-left", "arrow-right", "arrow-up-right", "brain-circuit",
    "check", "chevron-down", "chevron-right", "circle-question-mark", "compass",
    "download", "external-link", "flask-conical", "folder-open", "git-branch",
    "menu", "plus", "rotate-ccw", "search", "sparkles", "x",
)
SKILL_FILES = (
    "skills/research-direction-guide/SKILL.md",
    "skills/research-direction-guide/scripts/validate_direction_map.py",
    "skills/research-direction-guide/references/output-contract.md",
    "skills/literature-search/SKILL.md",
    "skills/literature-search/scripts/search_literature.py",
    "skills/literature-search/scripts/validate_literature_record.py",
    "skills/literature-search/scripts/import_literature.py",
    "skills/literature-search/references/output-contract.md",
    "skills/literature-search/references/providers.md",
)
LEAN_FILES = (
    ".gitattributes", "Audit.lean", "cases.json", "lake-manifest.json",
    "lakefile.toml", "lean-toolchain", "reviewed-sources.json",
    "cases/DirectAxiom.lean", "cases/DirectSorry.lean",
    "cases/ImplicationIdentity.lean", "cases/IndirectAxiom.lean",
    "cases/IndirectSorry.lean", "cases/InvalidEquality.lean",
    "cases/NatAddZero.lean", "cases/NativeTrust.lean", "cases/TimeoutFixture.lean",
)
PUBLIC_JSON = (
    "output/math-ai-workbench/experiment-data.json",
    "output/math-ai-workbench/research-direction-map.json",
    "output/literature-search-skill/cases/forward-trial/literature-search.json",
    "output/literature-search-skill/cases/forward-trial/direction-handoff.json",
    "output/project-review/cases.json",
)
CLOUD_FILES = (
    "deploy/modelscope/cloud_app.py",
    "deploy/modelscope/static/cloud-demo.js",
)
DEPLOY_FILES = {
    "deploy/modelscope/Dockerfile": "Dockerfile",
    "deploy/modelscope/.dockerignore": ".dockerignore",
    "deploy/modelscope/README.md": "README.md",
}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".html", ".css", ".js", ".svg", ".toml", ".yaml", ".yml"}
CREDENTIAL_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{12,}"),
    re.compile(rb"""(?i)["']?(?:DASHSCOPE_API_KEY|API_KEY|ACCESS_TOKEN|SECRET_KEY)["']?\s*[:=]\s*["'][^"'\r\n]{12,}["']"""),
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def safe_relative(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or "\\" in name or ":" in name or
            any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("Unsafe relative path")
    parts = [part.lower() for part in path.parts]
    if (any(part.startswith(".env") or part in {
        ".git", ".venv", "venv", "private-backup", "__pycache__", ".lake", "node_modules",
    } for part in parts) or re.search(r"\.(?:db|sqlite|sqlite3|pem|key)(?:$|-)", name, re.I)):
        raise ValueError("Excluded file name: " + name)
    return path


def read_public(name):
    relative = safe_relative(name)
    current = ROOT
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Refuse linked/reparse source: " + name)
    if not current.is_file() or not current.resolve().is_relative_to(ROOT):
        raise ValueError("Public source is not a regular in-repository file: " + name)
    return current.read_bytes()


def check_text(name, data):
    path = PurePosixPath(name)
    if path.suffix.lower() in TEXT_SUFFIXES or path.name in ("Dockerfile", ".dockerignore", "LICENSE"):
        data.decode("utf-8-sig")
        if any(pattern.search(data) for pattern in CREDENTIAL_PATTERNS):
            # Never display the matching credential-like value.
            raise ValueError("Credential-like pattern requires review: " + name)
    if name.endswith(".py"):
        ast.parse(data, filename=name)


def public_registry():
    raw = read_public(REGISTRY)
    if sha(raw) != REGISTRY_SHA256:
        raise ValueError("The accepted public registry manifest changed; review it first.")
    manifest = json.loads(raw)
    locations = {}
    if len(manifest["artifacts"]) != 35:
        raise ValueError("Expected exactly the 35 accepted public identities")
    for artifact in manifest["artifacts"]:
        for location in artifact["locations"]:
            name = location["path"]
            safe_relative(name)
            if name in locations and locations[name] != location:
                raise ValueError("Conflicting registered location: " + name)
            locations[name] = location
    if len(locations) != 35:
        raise ValueError("Expected the 35 reviewed public file locations")
    return manifest, locations


def runtime_allowlist(extra_css):
    extras = []
    for name in extra_css:
        path = safe_relative(name)
        if path.parent != PurePosixPath("webapp/static") or path.suffix != ".css":
            raise ValueError("--extra-static-css must name one explicit webapp/static/*.css file")
        extras.append(name)
    paths = (
        [f"webapp/{name}.py" for name in PYTHON_MODULES]
        + ["webapp/requirements.txt", "webapp/ASSETS.md", REGISTRY]
        + [f"webapp/static/{name}" for name in STATIC_FILES]
        + [f"webapp/static/assets/lucide/{name}.svg" for name in LUCIDE_ICONS]
        + list(SKILL_FILES)
        + [f"lean/short-lemmas/{name}" for name in LEAN_FILES]
        + list(PUBLIC_JSON) + list(CLOUD_FILES) + extras
    )
    return sorted(set(paths))


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ("src", "href") and value and value.startswith("/static/"):
                self.paths.add("webapp" + value.split("?", 1)[0].split("#", 1)[0])


def check_nested_archive(name, data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 200 or sum(entry.file_size for entry in entries) > 16 * 1024 * 1024:
            raise ValueError("Unexpected nested public archive size: " + name)
        seen = set()
        for entry in entries:
            member = entry.filename.rstrip("/")
            safe_relative(member)
            if member in seen or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Unexpected duplicate/link archive member: " + name)
            seen.add(member)
            if not entry.is_dir():
                check_text(member, archive.read(entry))


def zip_entry(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=(2026, 10, 21, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.flag_bits |= 0x800
    archive.writestr(info, data)


def make_evidence_zip(locations):
    originals = {}
    for name, expected in sorted(locations.items()):
        data = read_public(name)
        if len(data) != expected["expected_bytes"] or sha(data) != expected["expected_byte_sha256"]:
            raise ValueError("Registered public bytes changed: " + name)
        check_text(name, data)
        if name.endswith(".zip"):
            check_nested_archive(name, data)
        originals[name] = data
    manifest = {
        "schema": "yanzheng-public-evidence-download-v1",
        "registry_manifest_sha256": REGISTRY_SHA256,
        "file_count": len(originals),
        "scope": "35 explicitly registered public historical originals; byte match is not source authentication or mathematical certification.",
        "historical_metadata": "Some originals retain developer-local path declarations. They are historical metadata, not server access permissions.",
        "files": [{"path": name, "sha256": sha(data), "bytes": len(data)} for name, data in originals.items()],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        zip_entry(archive, "PUBLIC-EVIDENCE.json", encoded(manifest))
        for name, data in originals.items():
            zip_entry(archive, name, data)
    return buffer.getvalue(), manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra-static-css", action="append", default=[],
                        help="An additional CSS path explicitly approved for the frozen UI; may be repeated.")
    parser.add_argument("--plan", action="store_true", help="Print the exact allowlist without staging, packaging or importing the app.")
    args = parser.parse_args()
    paths = runtime_allowlist(args.extra_static_css)
    _, locations = public_registry()
    if args.plan:
        print(json.dumps({"runtime_source_files": len(paths), "runtime_allowlist": paths,
                          "deployment_files": DEPLOY_FILES, "registered_public_download_files": sorted(locations),
                          "writes": False}, ensure_ascii=False, indent=2))
        return 0

    # Capture and inspect exactly the named files before creating any staging tree.
    captured = {name: read_public(name) for name in paths + list(DEPLOY_FILES)}
    for name, data in captured.items():
        check_text(name, data)
    for name in PUBLIC_JSON:
        json.loads(captured[name])
    references = AssetReferences()
    references.feed(captured["webapp/static/index.html"].decode("utf-8-sig"))
    missing = references.paths - set(paths)
    if missing:
        raise ValueError("Index references assets outside the explicit allowlist: " + ", ".join(sorted(missing)))
    evidence_zip, evidence_manifest = make_evidence_zip(locations)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # Refuse a source change during the read phase; package the captured bytes only.
    if any(read_public(name) != data for name, data in captured.items()):
        raise RuntimeError("Sources changed during bundle capture; freeze and rebuild once.")
    stamp = datetime.now(timezone.utc)
    round_dir = ROOT / "output" / "modelscope-demo" / (stamp.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    stage = round_dir / "stage"
    stage.mkdir(parents=True, exist_ok=False)
    staged = {f"runtime/{name}": captured[name] for name in paths}
    staged.update({destination: captured[name] for name, destination in DEPLOY_FILES.items()})
    zip_name = "runtime/deploy/modelscope/public-evidence.zip"
    staged[zip_name] = evidence_zip
    files = []
    for name, data in sorted(staged.items()):
        safe_relative(name)
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        source = name[len("runtime/"):] if name.startswith("runtime/") and name != zip_name else None
        files.append({"path": name, "source_path": source, "sha256": sha(data), "bytes": len(data)})
    ui_files = {name: sha(captured[name]) for name in paths if name.startswith("webapp/static/")}
    manifest = {
        "schema": "yanzheng-modelscope-public-bundle-v1", "created_at": stamp.isoformat(),
        "base_commit": head, "revision_kind": "working-tree-snapshot",
        "base_commit_is_complete_source_identity": False,
        "scope": "Explicit public runtime files, five historical JSON archives and a 35-original download. No local database, configuration or private archives.",
        "runtime_source_file_count": len(paths), "deployment_file_count": len(DEPLOY_FILES),
        "public_evidence": evidence_manifest,
        "public_evidence_zip_sha256": sha(evidence_zip),
        "ui_files": ui_files, "files": files,
        "excludes_self": True,
        "validation_limits": "Syntax, byte hashes, JSON parsing, asset allowlist and limited credential-pattern checks only. No Docker build, installation, network call or deployment performed.",
    }
    manifest_bytes = encoded(manifest)
    with (stage / "SOURCE-MANIFEST.json").open("xb") as stream:
        stream.write(manifest_bytes)
    result = {"stage_directory": str(stage), "source_manifest_sha256": sha(manifest_bytes),
              "runtime_source_files": len(paths), "deployment_files": len(DEPLOY_FILES),
              "registered_public_originals": 35, "public_evidence_zip_sha256": sha(evidence_zip),
              "docker_run": False, "uploaded": False, "local_database_accessed": False}
    with (round_dir / "BUILD-RESULT.json").open("xb") as stream:
        stream.write(encoded(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
