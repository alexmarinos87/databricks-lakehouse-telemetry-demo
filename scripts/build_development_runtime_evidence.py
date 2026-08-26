#!/usr/bin/env python3
"""Build and verify one protected controlled-development runtime package."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
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


core = _load_sibling(
    "_development_runtime_package_core",
    "development_runtime_package_core.py",
)
integrity = _load_sibling(
    "_runtime_package_integrity",
    "runtime_package_integrity.py",
)
io = core.io
verifier = core.verifier
EXPECTED_REPOSITORY = verifier.EXPECTED_REPOSITORY
OUTPUT_MANIFEST = "development-runtime-evidence.json"
OUTPUT_SUMMARY = "development-runtime-evidence-summary.md"


class PackageError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _translate(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except (
        core.PackageError,
        io.EvidenceIOError,
        integrity.IntegrityError,
    ) as error:
        raise PackageError(error.category) from None


def render_summary(
    *,
    report: Mapping[str, Any],
    metadata_sha256: str,
    artifact_count: int,
    aggregate_bytes: int,
) -> str:
    return "\n".join(
        [
            "# Controlled development runtime evidence package",
            "",
            f"- Verification status: **{report['status']}**",
            f"- Source commit: `{report['source_commit']}`",
            f"- Metadata: `{metadata_sha256}`",
            f"- Protected artifacts: `{artifact_count}`",
            f"- Protected artifact bytes: `{aggregate_bytes}`",
            (
                "- Evidence families: "
                f"`{report['evidence_family_count']}/"
                f"{report['required_evidence_family_count']}`"
            ),
            (
                "- Assertions: "
                f"`{report['assertion_count']}/"
                f"{report['required_assertion_count']}`"
            ),
            "",
            "The package contains no protected paths or contents, raw principal IDs, "
            "resource names, table contents, provider responses, workspace URLs or "
            "credentials.",
            "",
        ]
    )


def build_package(
    metadata_path: Path,
    artifact_root: Path,
    output_directory: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = verifier.DEFAULT_MAX_AGE_HOURS,
    max_execution_hours: float = verifier.DEFAULT_MAX_EXECUTION_HOURS,
) -> dict[str, Any]:
    document, metadata_bytes = _translate(core.load_metadata, metadata_path)
    _translate(
        integrity.validate_artifact_roles,
        document,
        verifier=verifier,
        io=io,
    )
    digests, aggregate_bytes = _translate(
        core.hash_artifacts,
        document,
        artifact_root,
    )
    package_manifest = core.manifest(document, digests)

    transaction = integrity.PackageDirectoryTransaction(
        output_directory,
        artifact_root,
        io=io,
    )
    try:
        with transaction:
            directory = transaction.directory
            candidate = directory / f".{OUTPUT_MANIFEST}.candidate"
            _translate(
                io.write_atomic,
                candidate,
                json.dumps(package_manifest, indent=2, sort_keys=True) + "\n",
                "runtime_package",
            )
            try:
                report = verifier.verify_evidence(
                    candidate,
                    now=now,
                    max_age_hours=max_age_hours,
                    max_execution_hours=max_execution_hours,
                )
                verifier.write_outputs(directory, report)
            except verifier.VerificationError as error:
                raise PackageError(error.category) from None

            _translate(
                io.publish_candidate,
                candidate,
                directory / OUTPUT_MANIFEST,
                "runtime_package",
            )
            metadata_sha256 = io.sha256(metadata_bytes)
            _translate(
                io.write_atomic,
                directory / OUTPUT_SUMMARY,
                render_summary(
                    report=report,
                    metadata_sha256=metadata_sha256,
                    artifact_count=len(digests),
                    aggregate_bytes=aggregate_bytes,
                ),
                "runtime_package",
            )
            _translate(transaction.commit)
    except integrity.IntegrityError as error:
        raise PackageError(error.category) from None

    return {
        "manifest": package_manifest,
        "verification": report,
        "metadata_sha256": metadata_sha256,
        "artifact_count": len(digests),
        "aggregate_artifact_bytes": aggregate_bytes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--max-age-hours",
        type=verifier.positive_hours,
        default=verifier.DEFAULT_MAX_AGE_HOURS,
    )
    parser.add_argument(
        "--max-execution-hours",
        type=verifier.positive_hours,
        default=verifier.DEFAULT_MAX_EXECUTION_HOURS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_package(
            args.metadata,
            args.artifact_root,
            args.output_dir,
            max_age_hours=args.max_age_hours,
            max_execution_hours=args.max_execution_hours,
        )
    except PackageError as error:
        print(f"Runtime evidence packaging failed: {error.category}", file=sys.stderr)
        return 2
    status = result["verification"]["status"]
    print(f"Runtime evidence package {status}: artifacts={result['artifact_count']}")
    return 0 if status == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
