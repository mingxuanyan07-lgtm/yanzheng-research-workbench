"""Thin, fixed-manifest adapter. No client paths, downloads or mathematical verdicts."""
import copy
import hashlib
from pathlib import Path

MODULE_SHA256 = "ae8042031efb08864992a2dea69780a69da9a6340ec544543a0716645a1405ee"
MANIFEST_SHA256 = "680f0551ed3d01bed45447b8356268c457686fdb4bed12d66c027a83af2236b0"
PREVIEW_LIMIT = 12000


class Unavailable(Exception):
    pass


def _public(result):
    """Remove execution paths; retain separately labelled source declarations."""
    result = copy.deepcopy(result)
    locations = result.get("locations", [])
    matched = result.pop("matched_location", None)
    result["matched_location_index"] = next((i for i, loc in enumerate(locations)
                                               if loc.get("path") == matched), None) if matched else None
    for index, location in enumerate(locations):
        location.pop("path", None)
        location["location_index"] = index
        # Module errors can mention resolved paths. Keep the status, not execution details.
        if location.get("status") == "path_rejected":
            location["reason"] = "registered location rejected by path policy"
    return result


class EvidenceAccess:
    def __init__(self, registry=None):
        # Injection is a trusted in-process test seam, never an HTTP configuration.
        self.registry = registry
        self.error = None
        if registry is None:
            try:
                base = Path(__file__).resolve().parent
                if hashlib.sha256((base / "evidence_registry.py").read_bytes()).hexdigest() != MODULE_SHA256:
                    raise Unavailable("accepted module hash mismatch")
                from evidence_registry import Registry
                self.registry = Registry.from_manifest(base / "evidence_registry_manifest.json",
                    allowed_root=str(base.parent), expected_manifest_sha256=MANIFEST_SHA256)
            except (OSError, ValueError, Unavailable):
                self.error = "fixed_registry_unavailable"

    def metadata(self):
        return {"contract": "theory-evidence-access-v1", "available": self.registry is not None,
            "module_version": "evidence-registry-v1.1", "accepted_module_sha256": MODULE_SHA256,
            "accepted_manifest_sha256": MANIFEST_SHA256,
            "registry_manifest_sha256": self.registry.manifest_sha256 if self.registry else None,
            "scope": "explicit_associated_artifact_only", "automatic_read": False,
            "preview_limit_characters": PREVIEW_LIMIT, "download_endpoint": False,
            "limitation": "check/open concurrent replacement race remains; byte match is not source or mathematical certification"}

    def describe(self, artifact_id):
        if self.registry is None:
            raise Unavailable()
        return _public(self.registry.describe(artifact_id))

    def read(self, artifact_id, declarations):
        if self.registry is None:
            raise Unavailable()
        result = self.registry.read_verified(artifact_id)
        public = _public(result.as_dict())
        actual = public.get("actual", {}).get("byte_sha256")
        from evidence_registry import compare_declared_sha256
        public["declaration_comparisons"] = [{"declaration_index": i,
            "declared_original_bytes_sha256": declaration.get("declared_original_bytes_sha256"),
            "declared_acquired_at": declaration.get("declared_acquired_at"),
            "byte_sha256_comparison": compare_declared_sha256(declaration.get("declared_original_bytes_sha256"), actual)}
            for i, declaration in enumerate(declarations)]
        public["preview"] = {"status": "unavailable", "text": None, "truncated": False}
        if result.status == "verified" and result.bytes is not None:
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
