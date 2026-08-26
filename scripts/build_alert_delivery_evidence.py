#!/usr/bin/env python3
"""Build and verify one protected development alert-delivery evidence package."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = Path(__file__).with_name("verify_alert_delivery_evidence.py")
SPEC = importlib.util.spec_from_file_location("_alert_delivery_verifier", VERIFIER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("alert_delivery_verifier_unavailable")
_verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = _verifier
SPEC.loader.exec_module(_verifier)

DEFAULT_POLICY = _verifier.DEFAULT_POLICY
EXPECTED_REPOSITORY = _verifier.EXPECTED_REPOSITORY
OUTPUT_MANIFEST = "alert-delivery-evidence.json"
OUTPUT_SUMMARY = "alert-delivery-evidence-summary.md"
MAX_METADATA_BYTES = 500_000
MAX_ARTIFACT_BYTES = 5_000_000
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")

_MANIFEST_KEYS = {
    "schema_version",
    "target",
    "repository",
    "source_commit",
    "captured_at_utc",
    "workspace_fingerprint",
    "alert_event_id",
    "alert_id",
    "severity",
    "owner",
    "deployed_asset_fingerprint",
    "destination_fingerprint",
    "triggered_at_utc",
    "delivered_at_utc",
    "acknowledged_at_utc",
    "resolved_at_utc",
    "delivery_attempts",
    "notification_count",
    "delivery_status",
    "acknowledging_owner",
    "runbook",
    "test_alert",
    "evidence_sha256",
}
if _MANIFEST_KEYS != set(_verifier._EVIDENCE_KEYS):
    raise RuntimeError("alert_delivery_manifest_contract_mismatch")
_METADATA_KEYS = (_MANIFEST_KEYS - {"evidence_sha256"}) | {"protected_artifact"}


class PackageError(RuntimeError):
    """Stable invalid-input category safe to expose in logs."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _expect(condition: bool, category: str) -> None:
    if not condition:
        raise PackageError(category)


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _regular_bytes(path: Path, *, maximum: int, category: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError:
        raise PackageError(f"{category}_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PackageError(f"{category}_not_regular")
    if metadata.st_size < 1 or metadata.st_size > maximum:
        raise PackageError(f"{category}_size_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise PackageError(f"{category}_not_regular")
            payload = handle.read(maximum + 1)
            closed = os.fstat(handle.fileno())
    except PackageError:
        raise
    except OSError:
        raise PackageError(f"{category}_unreadable") from None
    if len(payload) < 1 or len(payload) > maximum:
        raise PackageError(f"{category}_size_invalid")
    identity_before = (metadata.st_dev, metadata.st_ino, metadata.st_size)
    identity_open = (opened.st_dev, opened.st_ino, opened.st_size)
    identity_after = (closed.st_dev, closed.st_ino, closed.st_size)
    if identity_before != identity_open or identity_open != identity_after:
        raise PackageError(f"{category}_changed_during_read")
    return payload


def _json_object(payload: bytes, *, category: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageError(f"{category}_invalid_json") from None
    if not isinstance(value, dict):
        raise PackageError(f"{category}_shape_invalid")
    return value


def _fingerprint(value: Any, *, category: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PackageError(category)
    return value


def _artifact_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        raise PackageError("protected_artifact_path_invalid")
    if "\\" in value or any(character in value for character in ("\x00", "\n", "\r")):
        raise PackageError("protected_artifact_path_invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise PackageError("protected_artifact_path_invalid")
    try:
        root_metadata = root.lstat()
    except OSError:
        raise PackageError("protected_artifact_root_unavailable") from None
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise PackageError("protected_artifact_root_invalid")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except OSError:
            raise PackageError("protected_artifact_unavailable") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise PackageError("protected_artifact_symlink_rejected")
    if not candidate.is_file():
        raise PackageError("protected_artifact_not_regular")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        raise PackageError("protected_artifact_outside_root") from None
    return candidate


def _load_metadata(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _regular_bytes(
        path,
        maximum=MAX_METADATA_BYTES,
        category="alert_package_metadata",
    )
    document = _json_object(raw, category="alert_package_metadata")
    if set(document) != _METADATA_KEYS:
        raise PackageError("alert_package_metadata_shape_invalid")
    artifact = document.get("protected_artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"path", "expected_sha256"}:
        raise PackageError("protected_artifact_descriptor_shape_invalid")
    _fingerprint(
        artifact.get("expected_sha256"),
        category="protected_artifact_expected_digest_invalid",
    )
    return document, raw


def _ensure_output_outside_protected_root(
    output_directory: Path, artifact_root: Path
) -> None:
    try:
        resolved_root = artifact_root.resolve(strict=True)
        resolved_output = output_directory.resolve(strict=False)
    except OSError:
        raise PackageError("alert_package_output_separation_unavailable") from None
    try:
        resolved_output.relative_to(resolved_root)
    except ValueError:
        return
    raise PackageError("alert_package_output_inside_protected_root")


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise PackageError("alert_package_output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise PackageError("alert_package_output_directory_unavailable") from None
    if path.is_symlink() or not path.is_dir():
        raise PackageError("alert_package_output_directory_invalid")
    return path


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise PackageError("alert_package_temporary_output_exists")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise PackageError("alert_package_output_path_invalid")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise PackageError("alert_package_output_write_failed") from None


def _publish_candidate(candidate: Path, final_path: Path) -> None:
    if final_path.exists() and (final_path.is_symlink() or not final_path.is_file()):
        raise PackageError("alert_package_output_path_invalid")
    try:
        candidate.replace(final_path)
    except OSError:
        raise PackageError("alert_package_output_write_failed") from None


def render_summary(
    manifest: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    metadata_sha256: str,
    artifact_byte_count: int,
) -> str:
    return "\n".join(
        [
            "# Alert delivery evidence package",
            "",
            f"- Verification status: **{verification['status']}**",
            f"- Alert: `{manifest['alert_id']}`",
            f"- Event: `{manifest['alert_event_id']}`",
            f"- Source commit: `{manifest['source_commit']}`",
            f"- Metadata: `{metadata_sha256}`",
            f"- Protected evidence: `{manifest['evidence_sha256']}`",
            f"- Protected evidence bytes: `{artifact_byte_count}`",
            f"- Delivery delay: `{verification['delivery_delay_minutes']}` minutes",
            f"- Notifications: `{verification['notification_count']}`",
            "",
            "The package contains no protected artifact path, destination URL, credential, "
            "provider response, notification body or raw telemetry value.",
            "",
        ]
    )


def build_package(
    metadata_path: Path,
    artifact_root: Path,
    output_directory: Path,
    *,
    policy_path: Path = DEFAULT_POLICY,
    repository_root: Path = REPO_ROOT,
    now: datetime | None = None,
    max_age_hours: float = _verifier.DEFAULT_MAX_AGE_HOURS,
) -> dict[str, Any]:
    metadata, metadata_bytes = _load_metadata(metadata_path)
    descriptor = metadata["protected_artifact"]
    artifact_path = _artifact_path(artifact_root, descriptor["path"])
    artifact_bytes = _regular_bytes(
        artifact_path,
        maximum=MAX_ARTIFACT_BYTES,
        category="protected_alert_evidence",
    )
    artifact_sha256 = _sha256(artifact_bytes)
    _expect(
        artifact_sha256 == descriptor["expected_sha256"],
        "protected_artifact_digest_mismatch",
    )

    manifest = {
        key: value for key, value in metadata.items() if key != "protected_artifact"
    }
    manifest["evidence_sha256"] = artifact_sha256
    _expect(set(manifest) == _MANIFEST_KEYS, "alert_package_manifest_shape_invalid")

    _ensure_output_outside_protected_root(output_directory, artifact_root)
    directory = _prepare_output_directory(output_directory)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    candidate_path = directory / f".{OUTPUT_MANIFEST}.candidate"
    if candidate_path.exists() or candidate_path.is_symlink():
        raise PackageError("alert_package_candidate_manifest_exists")
    _write_atomic(candidate_path, manifest_text)
    published = False
    try:
        try:
            verification = _verifier.verify_evidence(
                policy_path,
                candidate_path,
                repository_root=repository_root,
                now=now,
                max_age_hours=max_age_hours,
            )
            _verifier.write_outputs(directory, verification)
        except _verifier.AlertEvidenceError as error:
            raise PackageError(error.category) from None
        _publish_candidate(candidate_path, directory / OUTPUT_MANIFEST)
        published = True
    finally:
        if not published:
            try:
                candidate_path.unlink(missing_ok=True)
            except OSError:
                pass

    metadata_sha256 = _sha256(metadata_bytes)
    _write_atomic(
        directory / OUTPUT_SUMMARY,
        render_summary(
            manifest,
            verification,
            metadata_sha256=metadata_sha256,
            artifact_byte_count=len(artifact_bytes),
        ),
    )
    return {
        "manifest": manifest,
        "verification": verification,
        "metadata_sha256": metadata_sha256,
        "artifact_byte_count": len(artifact_bytes),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--max-age-hours",
        type=_verifier.positive_hours,
        default=_verifier.DEFAULT_MAX_AGE_HOURS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_package(
            args.metadata,
            args.artifact_root,
            args.output_dir,
            policy_path=args.policy,
            max_age_hours=args.max_age_hours,
        )
    except PackageError as error:
        print(f"Alert delivery evidence packaging failed: {error.category}", file=sys.stderr)
        return 2
    status = result["verification"]["status"]
    print(
        f"Alert delivery evidence package {status}: "
        f"alert={result['manifest']['alert_id']}"
    )
    return 0 if status == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
