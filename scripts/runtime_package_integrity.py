"""Integrity boundaries for protected controlled-runtime packages."""
from __future__ import annotations

import shutil
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


class IntegrityError(RuntimeError):
    """Stable package-integrity category safe to expose in logs."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _artifact_id(io: ModuleType, value: Any, category: str) -> str:
    try:
        return io.artifact_id(value, category)
    except io.EvidenceIOError as error:
        raise IntegrityError(error.category) from None


def validate_artifact_roles(
    document: Mapping[str, Any],
    *,
    verifier: ModuleType,
    io: ModuleType,
) -> None:
    """Prevent one protected record from satisfying unrelated evidence roles."""

    apply = document["apply"]
    execution = document["execution"]
    rollback = document["rollback"]

    anchor_values = {
        "apply_approval": apply["approval_artifact_id"],
        "accepted_plan": apply["accepted_plan_artifact_id"],
        "accepted_plan_review": apply["accepted_plan_review_artifact_id"],
        "execution_record": execution["evidence_artifact_id"],
        "rollback_record": rollback["evidence_artifact_id"],
        "recovery_point": rollback["recovery_point_artifact_id"],
    }
    anchors: dict[str, str] = {}
    artifact_to_anchor: dict[str, str] = {}
    for role, value in anchor_values.items():
        artifact = _artifact_id(
            io,
            value,
            "runtime_package_artifact_reference_invalid",
        )
        if artifact in artifact_to_anchor:
            raise IntegrityError("runtime_package_anchor_artifact_overlap")
        anchors[role] = artifact
        artifact_to_anchor[artifact] = role

    family_by_artifact: dict[str, str] = {}
    for item in document["evidence_families"]:
        family = item["family"]
        artifact = _artifact_id(
            io,
            item["evidence_artifact_id"],
            "runtime_package_artifact_reference_invalid",
        )
        if artifact in artifact_to_anchor:
            raise IntegrityError("runtime_package_anchor_artifact_reused")
        previous_family = family_by_artifact.get(artifact)
        if previous_family is not None and previous_family != family:
            raise IntegrityError("runtime_package_family_artifact_overlap")
        family_by_artifact[artifact] = family

    assertion_family_by_artifact: dict[str, str] = {}
    for item in document["assertions"]:
        assertion_id = item["assertion_id"]
        required_family = verifier.ASSERTION_FAMILIES[assertion_id]
        artifact = _artifact_id(
            io,
            item["evidence_artifact_id"],
            "runtime_package_artifact_reference_invalid",
        )
        if artifact in artifact_to_anchor:
            raise IntegrityError("runtime_package_anchor_artifact_reused")
        family_owner = family_by_artifact.get(artifact)
        if family_owner is not None and family_owner != required_family:
            raise IntegrityError("runtime_package_cross_family_artifact_reuse")
        assertion_owner = assertion_family_by_artifact.get(artifact)
        if assertion_owner is not None and assertion_owner != required_family:
            raise IntegrityError("runtime_package_cross_family_artifact_reuse")
        assertion_family_by_artifact[artifact] = required_family


class PackageDirectoryTransaction:
    """Build a complete package privately, then publish its directory atomically."""

    def __init__(
        self,
        final_directory: Path,
        protected_root: Path,
        *,
        io: ModuleType,
    ) -> None:
        self.final_directory = final_directory
        self.protected_root = protected_root
        self.io = io
        self.staging_directory: Path | None = None
        self.committed = False

    def __enter__(self) -> "PackageDirectoryTransaction":
        try:
            self.io.ensure_output_outside_root(
                self.final_directory,
                self.protected_root,
                "runtime_package_output",
            )
        except self.io.EvidenceIOError as error:
            raise IntegrityError(error.category) from None

        if (
            not self.final_directory.name
            or self.final_directory.name in {".", ".."}
        ):
            raise IntegrityError("runtime_package_output_directory_name_invalid")
        if self.final_directory.exists() or self.final_directory.is_symlink():
            raise IntegrityError("runtime_package_output_directory_exists")

        parent = self.final_directory.parent
        if parent.exists() and parent.is_symlink():
            raise IntegrityError("runtime_package_output_parent_is_symlink")
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise IntegrityError("runtime_package_output_parent_unavailable") from None
        if parent.is_symlink() or not parent.is_dir():
            raise IntegrityError("runtime_package_output_parent_invalid")

        staging = parent / f".{self.final_directory.name}.staging"
        if staging.exists() or staging.is_symlink():
            raise IntegrityError("runtime_package_staging_directory_exists")
        try:
            staging.mkdir(mode=0o700)
        except OSError:
            raise IntegrityError("runtime_package_staging_directory_unavailable") from None
        if staging.is_symlink() or not staging.is_dir():
            raise IntegrityError("runtime_package_staging_directory_invalid")
        self.staging_directory = staging
        return self

    @property
    def directory(self) -> Path:
        if self.staging_directory is None:
            raise IntegrityError("runtime_package_transaction_not_started")
        return self.staging_directory

    def commit(self) -> None:
        if self.staging_directory is None:
            raise IntegrityError("runtime_package_transaction_not_started")
        if self.final_directory.exists() or self.final_directory.is_symlink():
            raise IntegrityError("runtime_package_output_directory_exists")
        try:
            self.staging_directory.replace(self.final_directory)
        except OSError:
            raise IntegrityError("runtime_package_output_publish_failed") from None
        self.committed = True

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.committed or self.staging_directory is None:
            return
        staging = self.staging_directory
        try:
            if staging.is_symlink():
                staging.unlink()
            elif staging.exists():
                shutil.rmtree(staging)
        except OSError:
            if exc is None:
                raise IntegrityError("runtime_package_staging_cleanup_failed") from None
