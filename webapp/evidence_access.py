"""Thin, fixed-manifest adapter. No client paths, downloads or mathematical verdicts."""
import copy
import hashlib
import re
from pathlib import Path

MODULE_SHA256 = "57082cf181f1b5c26f5cf5578239f2d91b9e2beef840eaff0168ef524606585d"
MANIFEST_SHA256 = "680f0551ed3d01bed45447b8356268c457686fdb4bed12d66c027a83af2236b0"
PREVIEW_LIMIT = 12000
READ_STATUSES = {"verified", "unregistered", "missing", "too_large", "size_mismatch", "hash_mismatch",
    "unreadable", "not_a_regular_file", "path_rejected", "location_conflict"}
PUBLIC_FIELDS = {"schema_version", "artifact_id", "registered", "status", "read_status", "byte_match",
    "expected", "actual", "acquired_at", "acquired_at_status", "read_at", "identity_kind", "provenance",
    "locations", "shared_content_with", "registry_manifest_sha256", "bytes_returned", "caveats", "note"}


class Unavailable(Exception):
    pass


def _public(result):
    """Remove execution paths; retain separately labelled source declarations."""
    matched = result.get("matched_location")
    result = {key: copy.deepcopy(value) for key, value in result.items() if key in PUBLIC_FIELDS}
    locations = result.get("locations", [])
    # Match before removing execution paths, without exposing their spelling.
    original_locations = locations
    result["matched_location_index"] = next((i for i, loc in enumerate(locations)
                                               if loc.get("path") == matched), None) if matched else None
    result["locations"] = []
    for index, raw in enumerate(original_locations):
        location = {k: v for k, v in raw.items() if k in {"status", "expected_byte_sha256", "expected_bytes",
            "actual_byte_sha256", "actual_bytes", "provenance"}}
        location["location_index"] = index
        if raw.get("reason") is not None:
            location["reason"] = "registered_location_check_failed"
        result["locations"].append(location)
    if "caveats" in result:
        result["caveats"] = list(dict.fromkeys(c if isinstance(c, str) and
            re.fullmatch(r"[a-z0-9_]+(?:; [a-z0-9_]+)*", c) else "registry_diagnostic_details_omitted"
            for c in result["caveats"]))
    if "note" in result:
        result["note"] = "metadata only; original bytes were not read by this lookup"
    return result


def _verified_bytes_match(result, public):
    """Recheck the returned payload before it can be shown as verified text.

    The pinned registry is still responsible for authorizing and locking the
    filesystem objects. This boundary check only rejects inconsistent results;
    it cannot grant access or turn a failed registry read into a success.
    """
    payload = result.bytes
    if (not isinstance(payload, bytes) or public.get("byte_match") is not True or
            public.get("bytes_returned") is not True):
        return False
    expected, actual = public.get("expected"), public.get("actual")
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    digest = hashlib.sha256(payload).hexdigest()
    return all(type(facts.get("bytes")) is int and facts["bytes"] == len(payload) and
               facts.get("byte_sha256") == digest for facts in (expected, actual))


class EvidenceAccess:
    def __init__(self, registry=None, *, registry_factory=None):
        # Injection is a trusted in-process test seam, never an HTTP configuration.
        self.registry = registry
        self.error = None
        self.injected = registry is not None or registry_factory is not None
        if registry is None:
            try:
                if registry_factory is not None:
                    self.registry = registry_factory()
                else:
                    base = Path(__file__).resolve().parent
                    if hashlib.sha256((base / "evidence_registry.py").read_bytes()).hexdigest() != MODULE_SHA256:
                        self.error = "accepted_module_hash_mismatch"
                        return
                    from evidence_registry import Registry
                    self.registry = Registry.from_manifest(base / "evidence_registry_manifest.json",
                        allowed_root=str(base.parent), expected_manifest_sha256=MANIFEST_SHA256)
                if self.registry is None:
                    self.error = "registry_initialization_failed"
            except Exception:
                # Loading must fail closed, including a missing/unavailable platform helper.
                # Never publish the exception text, import path or traceback.
                self.registry = None
                self.error = "registry_initialization_failed"

    def metadata(self):
        return {"contract": "theory-evidence-access-v1", "available": self.registry is not None,
            "module_version": "evidence-registry-v1.2", "accepted_module_sha256": MODULE_SHA256,
            "accepted_manifest_sha256": MANIFEST_SHA256,
            "registry_manifest_sha256": self.registry.manifest_sha256 if self.registry else None,
            "registry_source": "trusted_in_process_injection" if self.injected else "fixed_accepted_manifest",
            "production_module_accepted": self.registry is not None and not self.injected,
            "availability_reason": self.error,
            "scope": "explicit_associated_artifact_only", "automatic_read": False,
            "preview_limit_characters": PREVIEW_LIMIT, "download_endpoint": False,
            "limitation": "secure reads are limited to local Windows NTFS ordinary paths without reparse components or multiple links; four real symlink substitution cases could not be executed in the validating environment (WinError 1314) and remain unverified; byte match is not source or mathematical certification"}

    def describe(self, artifact_id):
        if self.registry is None:
            raise Unavailable()
        try:
            return _public(self.registry.describe(artifact_id))
        except Exception:
            raise Unavailable() from None

    def _failure(self, artifact_id, status, diagnostic_code):
        return {"schema_version": "evidence-access-failure-v1", "artifact_id": artifact_id,
            "status": status, "byte_match": None, "bytes_returned": False,
            "actual": {"byte_sha256": None, "bytes": None}, "read_at": None,
            "acquired_at": None, "acquired_at_status": "unknown_after_registry_failure",
            "registry_manifest_sha256": self.registry.manifest_sha256,
            "result_origin": "adapter_exception_mapping", "diagnostic_code": diagnostic_code}

    def read(self, artifact_id, declarations):
        if self.registry is None:
            raise Unavailable()
        from evidence_registry import RegistryError
        result = None
        try:
            result = self.registry.read_verified(artifact_id)
            public = _public(result.as_dict())
            if (public.get("status") not in READ_STATUSES or public.get("artifact_id") != artifact_id or
                    public.get("status") != result.status or
                    (result.status == "verified" and not _verified_bytes_match(result, public))):
                public = self._failure(artifact_id, "unreadable", "inconsistent_registry_result")
        except RegistryError:
            public = self._failure(artifact_id, "path_rejected", "registry_read_policy_error")
        except OSError:
            public = self._failure(artifact_id, "unreadable", "registry_read_io_error")
        except Exception:
            raise Unavailable() from None
        actual = public.get("actual", {}).get("byte_sha256")
        from evidence_registry import compare_declared_sha256
        public["declaration_comparisons"] = [{"declaration_index": i,
            "declared_original_bytes_sha256": declaration.get("declared_original_bytes_sha256"),
            "declared_acquired_at": declaration.get("declared_acquired_at"),
            "byte_sha256_comparison": compare_declared_sha256(declaration.get("declared_original_bytes_sha256"), actual)}
            for i, declaration in enumerate(declarations)]
        public["preview"] = {"status": "unavailable", "text": None, "truncated": False}
        if public["status"] != "verified":
            public["bytes_returned"] = False
            public["byte_match"] = False if public.get("byte_match") is not None else None
        if public["status"] == "verified":
            try:
                text = result.bytes.decode("utf-8", errors="strict")
                # NUL indicates binary even if its bytes happen to decode.
                if "\x00" in text:
                    raise UnicodeError()
                public["preview"] = {"status": "utf8_plain_text", "text": text[:PREVIEW_LIMIT],
                    "truncated": len(text) > PREVIEW_LIMIT, "original_characters": len(text)}
            except UnicodeError:
                public["preview"]["status"] = "binary_or_non_utf8"
        return public
