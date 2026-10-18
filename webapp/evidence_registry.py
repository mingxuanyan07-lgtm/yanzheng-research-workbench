"""Read-only evidence registry with byte-level integrity verification.

Independent deliverable for the 研证 local research workbench.
Standard library only; no network, database, model calls or import side effects.

Trust model
-----------
A trusted server-side manifest enumerates the only original files this module may
read. Callers pass an ``artifact_id`` only -- never paths, URLs, commands or roots.
Every read recomputes the SHA-256 of the exact bytes that are returned; verified
bytes therefore belong to the same single read and are never taken from a cache.

What verification means
-----------------------
A ``verified`` result means: the bytes read now are byte-identical (size and
SHA-256) to the registered copy declared in the trusted manifest. It does not
prove source authenticity, content correctness, that a model or researcher ever
read the file, or that any mathematical claim is true.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MODULE_VERSION = "evidence-registry-v1.1"
MANIFEST_SCHEMA = "evidence-registry-manifest-v1"
READ_RESULT_SCHEMA = "evidence-registry-result-v1"
DESCRIBE_SCHEMA = "evidence-registry-description-v1"
HASH_SCOPE = "exact_saved_file_bytes"

DEFAULT_READ_LIMIT = 1024 * 1024
MIN_READ_LIMIT = 1
MAX_READ_LIMIT = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_PATH_CHARS = 1024
MAX_REASON_CHARS = 300
_READ_CHUNK = 64 * 1024

_ARTIFACT_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,200}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_BAD_CHARS = set('<>"|?*')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# FILE_ATTRIBUTE_REPARSE_POINT from the Windows API.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400

_TOP_KEYS = {"schema_version", "created_at", "read_limit_bytes", "allowed_root",
             "source", "metadata", "artifacts"}
_ARTIFACT_KEYS = {"artifact_id", "expected_byte_sha256", "expected_bytes", "hash_scope",
                  "acquired_at", "acquired_at_status", "identity_kind", "provenance",
                  "locations"}
_LOCATION_KEYS = {"path", "expected_byte_sha256", "expected_bytes", "provenance"}
_ACQUIRED_UNKNOWN = "unknown_not_inferred_from_mtime"
_ACQUIRED_RECORDED = "recorded_historical_retrieval"

_READ_STATUSES = frozenset({
    "verified", "unregistered", "missing", "too_large", "size_mismatch",
    "hash_mismatch", "unreadable", "not_a_regular_file", "path_rejected",
    "location_conflict",
})
_LOCATION_STATUSES = frozenset({
    "matched", "missing", "too_large", "size_mismatch", "hash_mismatch",
    "unreadable", "not_a_regular_file", "path_rejected",
})
_BASE_CAVEATS = (
    "byte_match_only_means_bytes_equal_the_registered_copy",
    "does_not_prove_source_authenticity_or_content_correctness",
    "does_not_prove_historical_model_or_researcher_reading",
    "does_not_prove_mathematical_claim_or_formal_verification",
    "no_semantic_or_mathematical_review_performed_here",
)
_KNOWN_LIMITATIONS = (
    "no_file_lock_and_no_cache; a later read may observe a replaced file",
    "windows_reparse_check_and_open_are_not_atomic; race window is documented, not solved",
    "only size+sha256 compared; upstream/disk-level tampering is out of scope",
)


class RegistryError(ValueError):
    """Invalid manifest, root configuration, or registry contract violation."""


class RegistryConflict(RegistryError):
    """The same artifact_id is declared with mutually incompatible facts."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _check_object(value, name):
    if not isinstance(value, dict):
        raise RegistryError(f"{name} must be a JSON object.")
    return value


def _check_keys(value, allowed, name):
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RegistryError(f"{name} has unknown fields: {unknown}")


def _check_text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RegistryError(f"{name} must be a non-empty string of at most {limit} characters.")
    return value


def _check_sha256(value, name):
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise RegistryError(f"{name} must be 64 lowercase hexadecimal characters.")
    return value


def _check_non_negative_int(value, name):
    if type(value) is not int or value < 0:
        raise RegistryError(f"{name} must be a non-negative integer.")
    return value


def _parse_acquired(value, status, name):
    """Return (normalized_iso_or_None, status) and never infer from mtime."""
    if value is None:
        if status != _ACQUIRED_UNKNOWN:
            raise RegistryError(
                f"{name}: null acquired_at requires acquired_at_status={_ACQUIRED_UNKNOWN!r}.")
        return None, _ACQUIRED_UNKNOWN
    if not isinstance(value, str):
        raise RegistryError(f"{name}: acquired_at must be an ISO-8601 string or null.")
    if status != _ACQUIRED_RECORDED:
        raise RegistryError(
            f"{name}: recorded acquired_at requires acquired_at_status={_ACQUIRED_RECORDED!r}.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RegistryError(f"{name}: acquired_at is not valid ISO-8601.") from None
    if parsed.tzinfo is None:
        raise RegistryError(f"{name}: acquired_at must include a timezone.")
    return parsed.isoformat(), _ACQUIRED_RECORDED


def _check_provenance(value, name):
    _check_object(value, name)
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise RegistryError(f"{name} must contain only JSON-safe values.") from None
    return value


def _validate_relative_path(value, name):
    """Validate a registry-declared POSIX-style path without touching the disk."""
    _check_text(value, name, MAX_PATH_CHARS)
    if value.startswith("/") or value.startswith("\\"):
        raise RegistryError(f"{name} must be relative, not absolute.")
    if any(character in value for character in "\\:\x00"):
        raise RegistryError(f"{name} must use '/' separators and must not contain '\\', ':' or NUL.")
    if any(ord(character) < 32 for character in value):
        raise RegistryError(f"{name} must not contain control characters.")
    if any(character in _WINDOWS_BAD_CHARS for character in value):
        raise RegistryError(f"{name} contains characters invalid on Windows.")
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise RegistryError(f"{name} must not contain empty, '.' or '..' segments.")
        if part != part.rstrip(" ."):
            raise RegistryError(f"{name} segments must not end with '.' or ' '.")
        if part.split(".")[0].upper() in _WINDOWS_RESERVED:
            raise RegistryError(f"{name} uses a reserved Windows device name.")
    return "/".join(parts)


def _is_reparse(stat_result):
    if stat.S_ISLNK(stat_result.st_mode):
        return True
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _bounded_reason(text, limit=MAX_REASON_CHARS):
    """Collapse and bound diagnostics so an OS message cannot blow up a record."""
    if not isinstance(text, str):
        text = str(text)
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _path_prefixes(path):
    """Yield every existing-path prefix from the filesystem anchor to ``path``."""
    parts = Path(path).parts
    if not parts:
        return
    current = Path(parts[0])
    yield current
    for part in parts[1:]:
        current = current / part
        yield current


def _reparse_components(path):
    """Return normalized prefixes of ``path`` that are symlinks/junctions/reparse points.

    This inspects the *declared* root path itself and all its ancestors, so a root
    replaced by (or reparented under) a link is detectable before any target open.
    """
    found = set()
    for prefix in _path_prefixes(path):
        try:
            stat_result = os.lstat(prefix)
        except FileNotFoundError:
            break
        except OSError as error:
            raise RegistryError(
                f"ROOT_UNINSPECTABLE: cannot inspect {prefix!s}: {_bounded_reason(str(error))}") from None
        if _is_reparse(stat_result):
            found.add(os.path.normcase(str(prefix)))
    return frozenset(found)


def _assert_no_reparse_ancestors(root, parts, *, include_final=True):
    """Reject registered paths whose existing components include links/junctions.

    Raises RegistryError carrying a stable reason code in the message prefix.
    """
    current = Path(root)
    checked = parts if include_final else parts[:-1]
    for index, part in enumerate(checked):
        current = current / part
        try:
            stat_result = os.lstat(current)
        except FileNotFoundError:
            # A missing suffix is fine: read_verified reports "missing". Any
            # already-existing link/junction above it was still checked.
            return
        except OSError as error:
            raise RegistryError(f"PATH_UNREADABLE: cannot inspect {current!s}: {error.strerror}") from None
        if _is_reparse(stat_result):
            raise RegistryError(f"PATH_REPARSE_POINT: {current!s} is a symlink/junction/reparse point.")


def _resolved_containment(root, parts, name):
    """Return the absolute target path and prove it stays inside the resolved root."""
    root_path = Path(root)
    target = root_path.joinpath(*parts)
    resolved_root = Path(os.path.realpath(root_path))
    resolved_target = Path(os.path.realpath(target))
    try:
        common = Path(os.path.commonpath([str(resolved_root), str(resolved_target)]))
    except ValueError:
        raise RegistryError(f"{name} escapes the allowed root.") from None
    if common != resolved_root:
        raise RegistryError(f"{name} escapes the allowed root.")
    if resolved_target == resolved_root:
        raise RegistryError(f"{name} resolves to the allowed root directory, not a file.")
    return str(target)


@dataclass(frozen=True)
class _Location:
    rel_path: str
    abs_path: str
    expected_sha256: str
    expected_bytes: int
    provenance: dict


@dataclass(frozen=True)
class _Artifact:
    artifact_id: str
    expected_sha256: str
    expected_bytes: int
    hash_scope: str
    acquired_at: str | None
    acquired_at_status: str
    identity_kind: str
    provenance: dict
    locations: tuple


@dataclass
class ReadResult:
    """Outcome of one verified read. ``bytes`` is present only when verified."""

    artifact_id: str
    status: str
    byte_match: bool | None
    expected: dict
    actual: dict
    acquired_at: str | None
    acquired_at_status: str
    read_at: str
    identity_kind: str | None
    matched_location: str | None
    provenance: dict | None = None
    locations: list = field(default_factory=list)
    bytes: bytes | None = None
    shared_content_with: list = field(default_factory=list)
    registry_manifest_sha256: str = ""
    caveats: list = field(default_factory=list)

    def __post_init__(self):
        if self.status not in _READ_STATUSES:
            raise AssertionError(f"unexpected read status: {self.status}")

    def as_dict(self):
        """JSON-serializable view; never includes the bytes themselves."""
        return {
            "schema_version": READ_RESULT_SCHEMA,
            "artifact_id": self.artifact_id,
            "status": self.status,
            "byte_match": self.byte_match,
            "expected": dict(self.expected),
            "actual": dict(self.actual),
            "acquired_at": self.acquired_at,
            "acquired_at_status": self.acquired_at_status,
            "read_at": self.read_at,
            "identity_kind": self.identity_kind,
            "matched_location": self.matched_location,
            "provenance": copy.deepcopy(self.provenance) if self.provenance is not None else None,
            "locations": [dict(item) for item in self.locations],
            "shared_content_with": list(self.shared_content_with),
            "registry_manifest_sha256": self.registry_manifest_sha256,
            "caveats": list(self.caveats),
            "bytes_returned": self.bytes is not None,
        }


class Registry:
    """Immutable allowlist plus read-only byte verification. Safe to share."""

    def __init__(self, *, manifest_path, manifest_sha256, allowed_root, read_limit_bytes,
                 artifacts, manifest_metadata, declared_root, canonical_root,
                 root_identity, root_reparse_components):
        self._manifest_path = manifest_path
        self._manifest_sha256 = manifest_sha256
        self._allowed_root = allowed_root
        self._read_limit_bytes = read_limit_bytes
        self._artifacts = artifacts
        self._manifest_metadata = manifest_metadata
        # Authorization boundary captured at load time. Read-time checks compare the
        # currently resolved declared root, its directory identity and introduced
        # reparse points against these load-time facts; a root that now points at a
        # different directory never becomes authorized just because it resolves.
        self._declared_root = declared_root
        self._canonical_root = canonical_root
        self._root_identity = root_identity
        self._root_reparse_components = root_reparse_components
        self._shared_content = {}
        for artifact in artifacts.values():
            self._shared_content.setdefault(artifact.expected_sha256, []).append(artifact.artifact_id)
        for content, ids in self._shared_content.items():
            if len(ids) > 1:
                self._shared_content[content] = sorted(ids)

    # ---- trusted construction -------------------------------------------------
    @classmethod
    def from_manifest(cls, manifest_path, *, allowed_root=None, expected_manifest_sha256=None):
        path = Path(manifest_path)
        try:
            with open(path, "rb") as handle:
                raw = handle.read(MAX_MANIFEST_BYTES + 1)
        except OSError as error:
            raise RegistryError(f"MANIFEST_UNREADABLE: {error.strerror}") from None
        if len(raw) > MAX_MANIFEST_BYTES:
            raise RegistryError("MANIFEST_TOO_LARGE: registry manifest exceeds the size limit.")
        manifest_sha256 = hashlib.sha256(raw).hexdigest()
        if expected_manifest_sha256 is not None:
            _check_sha256(expected_manifest_sha256, "expected_manifest_sha256")
            if manifest_sha256 != expected_manifest_sha256:
                raise RegistryError("MANIFEST_HASH_MISMATCH: trusted manifest bytes changed.")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise RegistryError("MANIFEST_NOT_JSON: registry manifest is not valid UTF-8 JSON.") from None
        return cls._build(manifest, path, manifest_sha256, allowed_root)

    @classmethod
    def _build(cls, manifest, manifest_path, manifest_sha256, allowed_root):
        _check_object(manifest, "manifest")
        _check_keys(manifest, _TOP_KEYS, "manifest")
        if manifest.get("schema_version") != MANIFEST_SCHEMA:
            raise RegistryError(f"manifest.schema_version must be {MANIFEST_SCHEMA!r}.")
        created_at = manifest.get("created_at")
        if not isinstance(created_at, str):
            raise RegistryError("manifest.created_at must be an ISO-8601 string with timezone.")
        try:
            parsed_created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            raise RegistryError("manifest.created_at is not valid ISO-8601.") from None
        if parsed_created.tzinfo is None:
            raise RegistryError("manifest.created_at must include a timezone.")
        limit = manifest.get("read_limit_bytes", DEFAULT_READ_LIMIT)
        if type(limit) is not int or not MIN_READ_LIMIT <= limit <= MAX_READ_LIMIT:
            raise RegistryError(
                f"manifest.read_limit_bytes must be an integer between {MIN_READ_LIMIT} and {MAX_READ_LIMIT}.")

        # allowed root: explicit override > manifest field > manifest directory.
        # A manifest field that is present is validated even when overridden.
        manifest_root = None
        if "allowed_root" in manifest:
            manifest_root = Path(_check_text(manifest["allowed_root"], "manifest.allowed_root", 4096))
            if not manifest_root.is_absolute():
                raise RegistryError("manifest.allowed_root must be an absolute path.")
        if allowed_root is not None:
            root = Path(_check_text(allowed_root, "allowed_root", 4096))
        elif manifest_root is not None:
            root = manifest_root
        else:
            root = Path(manifest_path).resolve().parent
        if not root.is_absolute():
            raise RegistryError("allowed_root must be an absolute path.")
        declared_root = Path(os.path.abspath(root))
        try:
            declared_stat = os.stat(declared_root)
        except OSError as error:
            raise RegistryError(
                f"allowed_root must be an existing directory: {_bounded_reason(str(error))}") from None
        if not stat.S_ISDIR(declared_stat.st_mode):
            raise RegistryError("allowed_root must be an existing directory.")
        canonical_root = Path(os.path.realpath(declared_root))
        try:
            canonical_stat = os.stat(canonical_root)
        except OSError as error:
            raise RegistryError(
                f"allowed_root could not be authorized: {_bounded_reason(str(error))}") from None
        if not stat.S_ISDIR(canonical_stat.st_mode):
            raise RegistryError("allowed_root must resolve to a directory.")
        root_identity = (canonical_stat.st_dev, canonical_stat.st_ino)
        root_reparse_components = _reparse_components(declared_root)

        source = manifest.get("source", {})
        metadata = manifest.get("metadata", {})
        _check_provenance(source, "manifest.source")
        _check_provenance(metadata, "manifest.metadata")

        raw_artifacts = manifest.get("artifacts")
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            raise RegistryError("manifest.artifacts must be a non-empty list.")

        artifacts = {}
        for index, item in enumerate(raw_artifacts):
            artifact = cls._parse_artifact(item, index, canonical_root)
            previous = artifacts.get(artifact.artifact_id)
            if previous is None:
                artifacts[artifact.artifact_id] = artifact
                continue
            if (previous.expected_sha256, previous.expected_bytes) != (
                    artifact.expected_sha256, artifact.expected_bytes):
                raise RegistryConflict(
                    f"ARTIFACT_ID_CONFLICT: {artifact.artifact_id!r} is declared with different "
                    "expected bytes/hash; refusing to pick one.")
            if (previous.acquired_at, previous.acquired_at_status) != (
                    artifact.acquired_at, artifact.acquired_at_status):
                raise RegistryConflict(
                    f"ARTIFACT_ID_CONFLICT: {artifact.artifact_id!r} is declared with different "
                    "acquisition facts.")
            if previous.identity_kind != artifact.identity_kind:
                raise RegistryConflict(
                    f"ARTIFACT_ID_CONFLICT: {artifact.artifact_id!r} is declared with different "
                    "identity kinds.")
            if previous.provenance != artifact.provenance:
                raise RegistryConflict(
                    f"ARTIFACT_PROVENANCE_CONFLICT: {artifact.artifact_id!r} is declared with different "
                    "artifact-level provenance; refusing to keep one and silently drop the other.")
            merged = _merge_locations(previous.locations, artifact.locations, artifact.artifact_id)
            artifacts[artifact.artifact_id] = _Artifact(
                artifact_id=previous.artifact_id,
                expected_sha256=previous.expected_sha256,
                expected_bytes=previous.expected_bytes,
                hash_scope=previous.hash_scope,
                acquired_at=previous.acquired_at,
                acquired_at_status=previous.acquired_at_status,
                identity_kind=previous.identity_kind,
                provenance=previous.provenance,
                locations=merged,
            )
        return cls(
            manifest_path=str(manifest_path),
            manifest_sha256=manifest_sha256,
            allowed_root=str(canonical_root),
            read_limit_bytes=limit,
            artifacts=artifacts,
            manifest_metadata={"source": source, "metadata": metadata},
            declared_root=str(declared_root),
            canonical_root=str(canonical_root),
            root_identity=root_identity,
            root_reparse_components=root_reparse_components,
        )

    @classmethod
    def _parse_artifact(cls, item, index, root):
        name = f"manifest.artifacts[{index}]"
        _check_object(item, name)
        _check_keys(item, _ARTIFACT_KEYS, name)
        artifact_id = _check_text(item.get("artifact_id"), f"{name}.artifact_id", 200)
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise RegistryError(f"{name}.artifact_id has invalid characters.")
        expected_sha = _check_sha256(item.get("expected_byte_sha256"), f"{name}.expected_byte_sha256")
        if artifact_id.startswith("sha256:") and artifact_id[7:] != expected_sha:
            raise RegistryError(
                f"{name}.artifact_id does not match expected_byte_sha256; refusing an inconsistent identity.")
        expected_bytes = _check_non_negative_int(item.get("expected_bytes"), f"{name}.expected_bytes")
        if item.get("hash_scope") != HASH_SCOPE:
            raise RegistryError(f"{name}.hash_scope must be {HASH_SCOPE!r}.")
        acquired_at, acquired_at_status = _parse_acquired(
            item.get("acquired_at"), item.get("acquired_at_status"), f"{name}.acquired_at")
        identity_kind = _check_text(item.get("identity_kind"), f"{name}.identity_kind", 200)
        provenance = _check_provenance(item.get("provenance", {}), f"{name}.provenance")
        raw_locations = item.get("locations")
        if not isinstance(raw_locations, list) or not raw_locations:
            raise RegistryError(f"{name}.locations must be a non-empty list.")
        locations = []
        seen_paths = set()
        for location_index, location in enumerate(raw_locations):
            location_name = f"{name}.locations[{location_index}]"
            _check_object(location, location_name)
            _check_keys(location, _LOCATION_KEYS, location_name)
            rel_path = _validate_relative_path(location.get("path"), f"{location_name}.path")
            if rel_path in seen_paths:
                raise RegistryConflict(f"PATH_CONFLICT: duplicate location path {rel_path!r}.")
            seen_paths.add(rel_path)
            location_sha = _check_sha256(
                location.get("expected_byte_sha256"), f"{location_name}.expected_byte_sha256")
            location_bytes = _check_non_negative_int(
                location.get("expected_bytes"), f"{location_name}.expected_bytes")
            if location_sha != expected_sha or location_bytes != expected_bytes:
                raise RegistryConflict(
                    f"LOCATION_CONTENT_CONFLICT: {location_name} declares content different from its "
                    "artifact identity; distinct bytes belong to a distinct artifact_id.")
            parts = rel_path.split("/")
            _assert_no_reparse_ancestors(root, parts)
            abs_path = _resolved_containment(root, parts, f"{location_name}.path")
            locations.append(_Location(
                rel_path=rel_path,
                abs_path=abs_path,
                expected_sha256=location_sha,
                expected_bytes=location_bytes,
                provenance=_check_provenance(location.get("provenance", {}), f"{location_name}.provenance"),
            ))
        return _Artifact(
            artifact_id=artifact_id,
            expected_sha256=expected_sha,
            expected_bytes=expected_bytes,
            hash_scope=HASH_SCOPE,
            acquired_at=acquired_at,
            acquired_at_status=acquired_at_status,
            identity_kind=identity_kind,
            provenance=provenance,
            locations=tuple(locations),
        )

    # ---- public read-only surface --------------------------------------------
    @property
    def manifest_sha256(self):
        return self._manifest_sha256

    @property
    def allowed_root(self):
        return self._allowed_root

    @property
    def read_limit_bytes(self):
        return self._read_limit_bytes

    @property
    def artifact_ids(self):
        return tuple(sorted(self._artifacts))

    # ---- read-time authorization boundary ------------------------------------
    def _authorized_root(self):
        """Return the load-time authorized root, or reject if it was replaced.

        The registered root is trusted as it existed when the manifest was loaded.
        A later junction/symlink replacement, re-parenting, or delete-and-recreate
        of that directory is a new filesystem object and must not inherit the old
        authorization. This check runs before any target file is opened.
        """
        declared = self._declared_root
        try:
            current_real = os.path.realpath(declared)
        except OSError as error:
            raise RegistryError(
                f"ROOT_UNRESOLVABLE: {_bounded_reason(str(error))}") from None
        if os.path.normcase(str(current_real)) != os.path.normcase(self._canonical_root):
            raise RegistryError(
                "ROOT_REPLACED_OR_REPARENTED: the registered root no longer resolves to the "
                "load-time authorized directory; refusing to treat the new target as authorized.")
        try:
            current_stat = os.stat(self._canonical_root)
        except OSError as error:
            raise RegistryError(
                f"ROOT_UNAVAILABLE: {_bounded_reason(str(error))}") from None
        if not stat.S_ISDIR(current_stat.st_mode):
            raise RegistryError("ROOT_NOT_A_DIRECTORY: the registered root is no longer a directory.")
        if (current_stat.st_dev, current_stat.st_ino) != self._root_identity:
            raise RegistryError(
                "ROOT_IDENTITY_CHANGED: the registered root path was replaced by a different "
                "directory object after load.")
        introduced = _reparse_components(declared) - self._root_reparse_components
        if introduced:
            raise RegistryError(
                "ROOT_ANCESTOR_REPARSE: a new symlink/junction appeared on the registered root path.")
        try:
            canonical_stat = os.lstat(self._canonical_root)
        except OSError as error:
            raise RegistryError(
                f"ROOT_UNAVAILABLE: {_bounded_reason(str(error))}") from None
        if _is_reparse(canonical_stat):
            raise RegistryError(
                "ROOT_CANONICAL_REPARSE: the authorized root itself is now a reparse point.")
        return Path(self._canonical_root)

    def describe(self, artifact_id):
        """Return registered metadata only. Files are not opened here."""
        if not isinstance(artifact_id, str):
            return self._unregistered_description(artifact_id)
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            return self._unregistered_description(artifact_id)
        return {
            "schema_version": DESCRIBE_SCHEMA,
            "artifact_id": artifact.artifact_id,
            "registered": True,
            "status": "registered_metadata_only",
            "read_status": "not_checked",
            "expected": {"byte_sha256": artifact.expected_sha256, "bytes": artifact.expected_bytes},
            "acquired_at": artifact.acquired_at,
            "acquired_at_status": artifact.acquired_at_status,
            "identity_kind": artifact.identity_kind,
            "provenance": copy.deepcopy(artifact.provenance),
            "locations": [
                {"path": location.rel_path,
                 "expected_byte_sha256": location.expected_sha256,
                 "expected_bytes": location.expected_bytes,
                 "provenance": copy.deepcopy(location.provenance)}
                for location in artifact.locations
            ],
            "shared_content_with": self._siblings(artifact),
            "registry_manifest_sha256": self._manifest_sha256,
            "note": "metadata only; bytes were not read or verified by describe()",
        }

    def _unregistered_description(self, artifact_id):
        return {
            "schema_version": DESCRIBE_SCHEMA,
            "artifact_id": artifact_id if isinstance(artifact_id, str) else None,
            "registered": False,
            "status": "unregistered",
            "read_status": "not_checked",
            "expected": None,
            "acquired_at": None,
            "acquired_at_status": None,
            "identity_kind": None,
            "provenance": None,
            "locations": [],
            "shared_content_with": [],
            "registry_manifest_sha256": self._manifest_sha256,
            "note": "artifact_id is not in the trusted registry; no path or bytes were accessed",
        }

    def read_verified(self, artifact_id):
        """Read every declared location once, verify bytes, and return the same bytes."""
        if not isinstance(artifact_id, str) or artifact_id not in self._artifacts:
            return ReadResult(
                artifact_id=artifact_id if isinstance(artifact_id, str) else str(artifact_id),
                status="unregistered",
                byte_match=None,
                expected={"byte_sha256": None, "bytes": None},
                actual={"byte_sha256": None, "bytes": None},
                acquired_at=None,
                acquired_at_status=None,
                read_at=_now(),
                identity_kind=None,
                matched_location=None,
                locations=[],
                registry_manifest_sha256=self._manifest_sha256,
                caveats=list(_BASE_CAVEATS) + ["artifact_id_was_not_in_the_trusted_registry"],
            )
        artifact = self._artifacts[artifact_id]
        outcomes = [self._read_location(artifact, location) for location in artifact.locations]
        status, matched_location = _overall_status(outcomes)
        matched = next((item for item in outcomes if item["status"] == "matched"), None)
        content_mismatch = [item for item in outcomes
                            if item["status"] in ("size_mismatch", "hash_mismatch")]
        if status == "verified":
            actual = {"byte_sha256": matched["actual_byte_sha256"], "bytes": matched["actual_bytes"]}
            byte_match = True
            payload = matched.pop("payload")
            matched_location = matched["path"]
        else:
            first_actual = next((item for item in outcomes if item.get("actual_byte_sha256")), None)
            actual = ({"byte_sha256": first_actual["actual_byte_sha256"],
                       "bytes": first_actual["actual_bytes"]}
                      if first_actual else {"byte_sha256": None, "bytes": None})
            if content_mismatch:
                byte_match = False
            elif status == "location_conflict" and any(
                    item["status"] in ("size_mismatch", "hash_mismatch") for item in outcomes):
                byte_match = False
            else:
                byte_match = None
            payload = None
            matched_location = None
        caveats = list(_BASE_CAVEATS) + list(_KNOWN_LIMITATIONS)
        if artifact.acquired_at is None:
            caveats.append("acquired_at_unknown_and_not_inferred_from_mtime")
        if len(artifact.locations) > 1:
            caveats.append("multiple_registered_locations; identity_is_one_artifact_but_sources_stay_separate")
            if any(item["status"] == "missing" for item in outcomes):
                caveats.append("some_registered_locations_were_missing_but_not_treated_as_content_match")
        if status == "location_conflict":
            caveats.append("at_least_one_existing_location_mismatched; no_verified_bytes_returned")
        if status == "missing":
            caveats.append("no_registered_location_exists_on_disk")
        if any(item["status"] == "unreadable" for item in outcomes):
            caveats.append("some_registered_locations_were_unreadable_not_treated_as_match")
        return ReadResult(
            artifact_id=artifact.artifact_id,
            status=status,
            byte_match=byte_match,
            expected={"byte_sha256": artifact.expected_sha256, "bytes": artifact.expected_bytes},
            actual=actual,
            acquired_at=artifact.acquired_at,
            acquired_at_status=artifact.acquired_at_status,
            read_at=_now(),
            identity_kind=artifact.identity_kind,
            matched_location=matched_location,
            provenance=copy.deepcopy(artifact.provenance),
            locations=[{key: value for key, value in item.items() if key != "payload"}
                       for item in outcomes],
            bytes=payload,
            shared_content_with=self._siblings(artifact),
            registry_manifest_sha256=self._manifest_sha256,
            caveats=caveats,
        )

    def _siblings(self, artifact):
        return [item for item in self._shared_content.get(artifact.expected_sha256, [])
                if item != artifact.artifact_id]

    def _read_location(self, artifact, location):
        """Read one location with a single open->fstat->read sequence."""
        outcome = {"path": location.rel_path, "status": "unreadable",
                   "actual_byte_sha256": None, "actual_bytes": None, "reason": None,
                   "provenance": copy.deepcopy(location.provenance)}
        if artifact.expected_bytes > self._read_limit_bytes:
            outcome.update(status="too_large",
                           reason=_bounded_reason(
                               f"registered_size {artifact.expected_bytes} exceeds read limit "
                               f"{self._read_limit_bytes}"))
            return outcome
        try:
            # Re-authorize the load-time root first: a replaced root (including a
            # junction swap with no race) must be rejected before any open.
            authorized_root = self._authorized_root()
            _resolved_containment(authorized_root, location.rel_path.split("/"),
                                  f"read:{location.rel_path}")
            _assert_no_reparse_ancestors(authorized_root, location.rel_path.split("/"))
        except RegistryError as error:
            outcome.update(status="path_rejected", reason=_bounded_reason(str(error)))
            return outcome
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(location.abs_path, flags)
        except FileNotFoundError:
            outcome.update(status="missing", reason="no such file")
            return outcome
        except OSError as error:
            if error.errno == errno.ELOOP:
                outcome.update(status="path_rejected",
                               reason="final path component is a symbolic link (O_NOFOLLOW)")
                return outcome
            outcome.update(status="unreadable",
                           reason=_bounded_reason(f"open failed: {error.strerror}"))
            return outcome
        try:
            try:
                file_stat = os.fstat(descriptor)
            except OSError as error:
                outcome.update(status="unreadable", reason=f"fstat failed: {error.strerror}")
                return outcome
            if not stat.S_ISREG(file_stat.st_mode):
                outcome.update(status="not_a_regular_file", reason="target is not a regular file")
                return outcome
            if _is_reparse(file_stat):
                outcome.update(status="path_rejected", reason="target is a symlink/junction/reparse point")
                return outcome
            size = file_stat.st_size
            if size > self._read_limit_bytes:
                outcome.update(status="too_large",
                               reason=f"actual_size {size} exceeds read limit {self._read_limit_bytes}")
                return outcome
            chunks = []
            hasher = hashlib.sha256()
            total = 0
            while True:
                remaining = self._read_limit_bytes + 1 - total
                if remaining <= 0:
                    break
                try:
                    chunk = os.read(descriptor, min(_READ_CHUNK, remaining))
                except OSError as error:
                    # Only expected OS read failures become structured results;
                    # programming errors (TypeError etc.) still surface.
                    outcome.update(status="unreadable",
                                   reason=_bounded_reason(
                                       f"read failed at offset {total}: "
                                       f"{error.strerror} (errno={error.errno})"))
                    return outcome
                if not chunk:
                    break
                chunks.append(chunk)
                hasher.update(chunk)
                total += len(chunk)
            if total > self._read_limit_bytes:
                outcome.update(status="too_large",
                               reason=f"read bytes exceed read limit {self._read_limit_bytes}")
                return outcome
            data = b"".join(chunks)
            if len(data) != size:
                outcome.update(status="unreadable",
                               reason="file size changed between fstat and read")
                return outcome
            actual_sha256 = hasher.hexdigest()
            outcome.update(actual_byte_sha256=actual_sha256, actual_bytes=len(data))
            if len(data) != artifact.expected_bytes:
                outcome.update(status="size_mismatch",
                               reason=f"actual {len(data)} bytes, expected {artifact.expected_bytes}")
                return outcome
            if actual_sha256 != artifact.expected_sha256:
                outcome.update(status="hash_mismatch",
                               reason="actual SHA-256 differs from the registered expected value")
                return outcome
            outcome.update(status="matched", reason=None, payload=data)
            return outcome
        finally:
            try:
                os.close(descriptor)
            except OSError:
                # A close failure cannot turn an unread/verified result into a
                # success; descriptor may already be closed by the OS.
                pass


def _merge_locations(existing, incoming, artifact_id):
    merged = list(existing)
    seen = {location.rel_path: location for location in merged}
    for location in incoming:
        previous = seen.get(location.rel_path)
        if previous is None:
            merged.append(location)
            seen[location.rel_path] = location
            continue
        if (previous.expected_sha256, previous.expected_bytes) != (
                location.expected_sha256, location.expected_bytes):
            raise RegistryConflict(
                f"LOCATION_CONTENT_CONFLICT: {artifact_id!r} lists {location.rel_path!r} twice "
                "with contradictory expectations.")
        if previous.provenance != location.provenance:
            raise RegistryConflict(
                f"LOCATION_PROVENANCE_CONFLICT: {artifact_id!r} lists {location.rel_path!r} twice "
                "with different provenance; refusing to keep one source and silently drop the other.")
    return tuple(merged)


def _overall_status(outcomes):
    """Decide the artifact-level status; never let a mismatching copy count as success."""
    mismatch = [item for item in outcomes if item["status"] in
                ("size_mismatch", "hash_mismatch", "too_large", "not_a_regular_file", "path_rejected")]
    matched = [item for item in outcomes if item["status"] == "matched"]
    if mismatch:
        if len(outcomes) == 1:
            return mismatch[0]["status"], None
        return "location_conflict", None
    if matched:
        return "verified", matched[0]["path"]
    if any(item["status"] == "unreadable" for item in outcomes):
        return "unreadable", None
    return "missing", None


def compare_declared_sha256(declared, actual):
    """Compare an external declaration against a verified actual hash.

    Returns "match", "mismatch" or "unknown" (null/malformed input or missing actual).
    Declared data is never upgraded into a verified fact by this helper.
    """
    if not isinstance(actual, str) or not _SHA256_RE.fullmatch(actual.lower()):
        return "unknown"
    if not isinstance(declared, str) or not _SHA256_RE.fullmatch(declared.lower()):
        return "unknown"
    return "match" if declared.lower() == actual.lower() else "mismatch"
