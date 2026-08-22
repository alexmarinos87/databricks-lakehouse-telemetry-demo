#!/usr/bin/env python3
"""Upload a validated ingestion plan without overwriting landing objects."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.ingestion_identity import (  # noqa: E402
    ValidatedUploadEntry,
    load_manifest,
)


DEFAULT_COMMAND_TIMEOUT_SECONDS = 60.0
_SAFE_TARGET = __import__("re").compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class CommandResult(NamedTuple):
    returncode: int
    stdout: bytes


def positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of seconds")
    return parsed


def _run_command(
    command: Sequence[str], *, timeout_seconds: float
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(
            f"Databricks CLI command exceeded {timeout_seconds:g} seconds"
        ) from None
    except OSError:
        raise RuntimeError("Databricks CLI command could not be started") from None
    return CommandResult(
        returncode=int(completed.returncode),
        stdout=bytes(completed.stdout or b""),
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _cat_remote(
    *, target: str, destination_path: str, timeout_seconds: float
) -> CommandResult:
    return _run_command(
        [
            "databricks",
            "fs",
            "cat",
            destination_path,
            "-t",
            target,
        ],
        timeout_seconds=timeout_seconds,
    )


def _remote_matches(entry: ValidatedUploadEntry, result: CommandResult) -> bool:
    return (
        result.returncode == 0
        and len(result.stdout) == entry.size_bytes
        and _sha256(result.stdout) == entry.sha256
    )


def _upload_entry(
    *,
    target: str,
    entry: ValidatedUploadEntry,
    timeout_seconds: float,
) -> str:
    existing = _cat_remote(
        target=target,
        destination_path=entry.destination_path,
        timeout_seconds=timeout_seconds,
    )
    if existing.returncode == 0:
        if not _remote_matches(entry, existing):
            raise RuntimeError(
                "Immutable landing destination exists with different content"
            )
        return "skipped"

    copied = _run_command(
        [
            "databricks",
            "fs",
            "cp",
            str(entry.source_path),
            entry.destination_path,
            "-t",
            target,
        ],
        timeout_seconds=timeout_seconds,
    )
    if copied.returncode != 0:
        # A concurrent uploader may have won the create race. Re-read and accept
        # only an exact byte-for-byte match; never add --overwrite.
        raced = _cat_remote(
            target=target,
            destination_path=entry.destination_path,
            timeout_seconds=timeout_seconds,
        )
        if _remote_matches(entry, raced):
            return "skipped"
        raise RuntimeError(
            f"Databricks immutable upload failed with exit code {copied.returncode}"
        )

    uploaded = _cat_remote(
        target=target,
        destination_path=entry.destination_path,
        timeout_seconds=timeout_seconds,
    )
    if not _remote_matches(entry, uploaded):
        raise RuntimeError("Uploaded landing object did not match its content identity")
    return "uploaded"


def upload_manifest(
    *,
    target: str,
    manifest_path: str | Path,
    repository_root: str | Path,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, int | str]:
    if not _SAFE_TARGET.fullmatch(target or ""):
        raise ValueError("Databricks target contains unsupported characters")
    if not math.isfinite(command_timeout_seconds) or command_timeout_seconds <= 0:
        raise ValueError("command timeout must be finite and positive")

    manifest = load_manifest(
        manifest_path,
        repository_root=repository_root,
    )
    mkdir_result = _run_command(
        [
            "databricks",
            "fs",
            "mkdirs",
            manifest.destination_root,
            "-t",
            target,
        ],
        timeout_seconds=command_timeout_seconds,
    )
    if mkdir_result.returncode != 0:
        raise RuntimeError(
            f"Databricks landing directory preparation failed with exit code {mkdir_result.returncode}"
        )

    uploaded = 0
    skipped = 0
    for entry in manifest.entries:
        outcome = _upload_entry(
            target=target,
            entry=entry,
            timeout_seconds=command_timeout_seconds,
        )
        if outcome == "uploaded":
            uploaded += 1
        else:
            skipped += 1

    return {
        "plan_id": manifest.plan_id,
        "uploaded": uploaded,
        "skipped": skipped,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--command-timeout-seconds",
        type=positive_seconds,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = upload_manifest(
        target=args.target,
        manifest_path=args.manifest,
        repository_root=args.repository_root,
        command_timeout_seconds=args.command_timeout_seconds,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
