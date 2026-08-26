"""Strict metadata and digest binding for controlled runtime evidence packages."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent


def _load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{name}_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


io = _load_sibling("_protected_evidence_io", "protected_evidence_io.py")
verifier = _load_sibling("_development_runtime_verifier", "verify_development_runtime_evidence.py")

MAX_METADATA_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 5_000_000
MAX_AGGREGATE_ARTIFACT_BYTES = 50_000_000
MAX_ARTIFACTS = 64
TOP_LEVEL_KEYS = {
    "schema_version", "target", "repository", "source_commit", "captured_at_utc",
    "apply", "execution", "evidence_families", "assertions", "rollback",
    "protected_artifacts",
}
APPLY_KEYS = {
    "authorized", "approved_at_utc", "approval_artifact_id",
    "accepted_plan_artifact_id", "accepted_plan_review_artifact_id",
    "workflow_run_fingerprint",
}
EXECUTION_KEYS = {
    "execution_fingerprint", "evidence_artifact_id", "started_at_utc",
    "completed_at_utc", "production_contact", "deployment_principal_fingerprint",
    "runtime_principal_fingerprint",
}
FAMILY_KEYS = {
    "family", "execution_fingerprint", "observed_at_utc",
    "evidence_artifact_id", "record_count",
}
ASSERTION_KEYS = {
    "assertion_id", "family", "execution_fingerprint", "status",
    "observed_at_utc", "evidence_artifact_id",
}
ROLLBACK_KEYS = {
    "tested", "completed_at_utc", "evidence_artifact_id",
    "recovery_point_artifact_id",
}
ARTIFACT_KEYS = {"artifact_id", "path", "expected_sha256"}


class PackageError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _raise_io(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except io.EvidenceIOError as error:
        raise PackageError(error.category) from None


def _exact(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PackageError(category)
    return value


def load_metadata(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _raise_io(
        io.regular_bytes,
        path,
        maximum=MAX_METADATA_BYTES,
        category="runtime_package_metadata",
    )
    document = _raise_io(io.json_object, raw, "runtime_package_metadata")
    if set(document) != TOP_LEVEL_KEYS:
        raise PackageError("runtime_package_metadata_shape_invalid")
    _exact(document.get("apply"), APPLY_KEYS, "runtime_package_apply_shape_invalid")
    _exact(
        document.get("execution"),
        EXECUTION_KEYS,
        "runtime_package_execution_shape_invalid",
    )
    _exact(
        document.get("rollback"),
        ROLLBACK_KEYS,
        "runtime_package_rollback_shape_invalid",
    )
    families = document.get("evidence_families")
    assertions = document.get("assertions")
    artifacts = document.get("protected_artifacts")
    if not isinstance(families, list) or len(families) != len(verifier.REQUIRED_FAMILIES):
        raise PackageError("runtime_package_families_shape_invalid")
    if not isinstance(assertions, list) or len(assertions) != len(verifier.ASSERTION_FAMILIES):
        raise PackageError("runtime_package_assertions_shape_invalid")
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > MAX_ARTIFACTS:
        raise PackageError("runtime_package_artifacts_shape_invalid")
    for item in families:
        _exact(item, FAMILY_KEYS, "runtime_package_family_shape_invalid")
    for item in assertions:
        _exact(item, ASSERTION_KEYS, "runtime_package_assertion_shape_invalid")
    for item in artifacts:
        _exact(item, ARTIFACT_KEYS, "runtime_package_artifact_shape_invalid")
    family_names = [item.get("family") for item in families]
    if len(set(family_names)) != len(family_names) or set(family_names) != set(verifier.REQUIRED_FAMILIES):
        raise PackageError("runtime_package_family_set_invalid")
    assertion_ids = [item.get("assertion_id") for item in assertions]
    if len(set(assertion_ids)) != len(assertion_ids) or set(assertion_ids) != set(verifier.ASSERTION_FAMILIES):
        raise PackageError("runtime_package_assertion_set_invalid")
    return document, raw


def artifact_references(document: Mapping[str, Any]) -> list[str]:
    apply = document["apply"]
    execution = document["execution"]
    rollback = document["rollback"]
    references = [
        apply["approval_artifact_id"],
        apply["accepted_plan_artifact_id"],
        apply["accepted_plan_review_artifact_id"],
        execution["evidence_artifact_id"],
        rollback["evidence_artifact_id"],
        rollback["recovery_point_artifact_id"],
    ]
    references.extend(item["evidence_artifact_id"] for item in document["evidence_families"])
    references.extend(item["evidence_artifact_id"] for item in document["assertions"])
    return [
        _raise_io(io.artifact_id, value, "runtime_package_artifact_reference_invalid")
        for value in references
    ]


def hash_artifacts(
    document: Mapping[str, Any], root: Path
) -> tuple[dict[str, str], int]:
    descriptors: dict[str, Mapping[str, Any]] = {}
    paths: set[str] = set()
    for descriptor in document["protected_artifacts"]:
        artifact_id = _raise_io(
            io.artifact_id,
            descriptor.get("artifact_id"),
            "runtime_package_artifact_id_invalid",
        )
        if artifact_id in descriptors:
            raise PackageError("runtime_package_artifact_id_duplicate")
        path = _raise_io(
            io.canonical_relative,
            descriptor.get("path"),
            invalid="protected_runtime_evidence_path_invalid",
            noncanonical="runtime_package_artifact_path_not_canonical",
        )
        if path in paths:
            raise PackageError("runtime_package_artifact_path_duplicate")
        paths.add(path)
        _raise_io(
            io.fingerprint,
            descriptor.get("expected_sha256"),
            "runtime_package_expected_digest_invalid",
        )
        descriptors[artifact_id] = descriptor
    references = set(artifact_references(document))
    if references != set(descriptors):
        if references - set(descriptors):
            raise PackageError("runtime_package_artifact_reference_unknown")
        raise PackageError("runtime_package_artifact_unused")
    digests: dict[str, str] = {}
    aggregate = 0
    for artifact_id in sorted(descriptors):
        descriptor = descriptors[artifact_id]
        path = _raise_io(
            io.protected_path,
            root,
            descriptor["path"],
            prefix="protected_runtime_evidence",
        )
        payload = _raise_io(
            io.regular_bytes,
            path,
            maximum=MAX_ARTIFACT_BYTES,
            category="protected_runtime_evidence",
        )
        aggregate += len(payload)
        if aggregate > MAX_AGGREGATE_ARTIFACT_BYTES:
            raise PackageError("runtime_package_aggregate_artifact_size_invalid")
        calculated = io.sha256(payload)
        if calculated != descriptor["expected_sha256"]:
            raise PackageError("runtime_package_artifact_digest_mismatch")
        digests[artifact_id] = calculated
    return digests, aggregate


def manifest(document: Mapping[str, Any], digests: Mapping[str, str]) -> dict[str, Any]:
    apply = document["apply"]
    execution = document["execution"]
    rollback = document["rollback"]
    return {
        "schema_version": document["schema_version"],
        "target": document["target"],
        "repository": document["repository"],
        "source_commit": document["source_commit"],
        "captured_at_utc": document["captured_at_utc"],
        "apply": {
            "authorized": apply["authorized"],
            "approved_at_utc": apply["approved_at_utc"],
            "approval_sha256": digests[apply["approval_artifact_id"]],
            "accepted_plan_sha256": digests[apply["accepted_plan_artifact_id"]],
            "accepted_plan_review_sha256": digests[apply["accepted_plan_review_artifact_id"]],
            "workflow_run_fingerprint": apply["workflow_run_fingerprint"],
        },
        "execution": {
            "execution_fingerprint": execution["execution_fingerprint"],
            "evidence_sha256": digests[execution["evidence_artifact_id"]],
            "started_at_utc": execution["started_at_utc"],
            "completed_at_utc": execution["completed_at_utc"],
            "production_contact": execution["production_contact"],
            "deployment_principal_fingerprint": execution["deployment_principal_fingerprint"],
            "runtime_principal_fingerprint": execution["runtime_principal_fingerprint"],
        },
        "evidence_families": [
            {
                "family": item["family"],
                "execution_fingerprint": item["execution_fingerprint"],
                "observed_at_utc": item["observed_at_utc"],
                "evidence_sha256": digests[item["evidence_artifact_id"]],
                "record_count": item["record_count"],
            }
            for item in document["evidence_families"]
        ],
        "assertions": [
            {
                "assertion_id": item["assertion_id"],
                "family": item["family"],
                "execution_fingerprint": item["execution_fingerprint"],
                "status": item["status"],
                "observed_at_utc": item["observed_at_utc"],
                "evidence_sha256": digests[item["evidence_artifact_id"]],
            }
            for item in document["assertions"]
        ],
        "rollback": {
            "tested": rollback["tested"],
            "completed_at_utc": rollback["completed_at_utc"],
            "evidence_sha256": digests[rollback["evidence_artifact_id"]],
            "recovery_point_sha256": digests[rollback["recovery_point_artifact_id"]],
        },
    }
