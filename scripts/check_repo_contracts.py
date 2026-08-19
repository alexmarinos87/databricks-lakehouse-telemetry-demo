#!/usr/bin/env python3
"""Validate lightweight repository contracts without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTING_MANIFEST = Path("sql/reporting_assets/manifest.json")
MAX_TEXT_FILE_BYTES = 2_000_000
IGNORED_DIRECTORIES = {
    ".databricks",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".review",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "metastore_db",
    "node_modules",
    "output",
    "spark-warehouse",
    "tmp",
    "venv",
}
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("Databricks token", re.compile(r"\bdapi[a-f0-9]{32}\b", re.IGNORECASE)),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
)


class CandidateFileError(Exception):
    """Raised when candidate bytes cannot be inspected safely."""


def uses_git_index(root: Path) -> bool:
    git_metadata = root / ".git"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        if git_metadata.exists():
            raise CandidateFileError(
                "git executable is required when repository metadata is present"
            ) from exc
        return False
    if result.returncode == 0 and result.stdout.strip() == "true":
        return True
    if git_metadata.exists():
        raise CandidateFileError(
            f"cannot inspect repository metadata with git (exit {result.returncode})"
        )
    return False


def repository_files(root: Path) -> list[Path]:
    """Return tracked files when Git is available, otherwise scan the source tree."""
    if uses_git_index(root):
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]

    files: list[Path] = []
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root)
        if (candidate.is_symlink() or candidate.is_file()) and not any(
            part in IGNORED_DIRECTORIES for part in relative.parts
        ):
            files.append(relative)
    return sorted(files)


def git_changed_paths(root: Path, *diff_args: str) -> set[Path]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", *diff_args],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return {Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item}


def validate_candidate_worktree_consistency(
    root: Path, base_ref: str | None = None
) -> list[str]:
    """Ensure checks execute the same bytes as staged or committed candidate paths."""
    if not uses_git_index(root):
        return []
    try:
        staged = git_changed_paths(root, "--cached")
        candidate = set(staged)
        if base_ref:
            merge_base = subprocess.run(
                ["git", "merge-base", base_ref, "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            candidate.update(git_changed_paths(root, merge_base, "HEAD"))
        unstaged = git_changed_paths(root)
    except (subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        return [f"cannot compare candidate and worktree paths: {exc}"]
    return [
        f"candidate file also has unstaged changes; restage or revert it: {path}"
        for path in sorted(candidate & unstaged)
    ]


def read_candidate_bytes(root: Path, relative: Path, use_index: bool) -> bytes:
    """Read exact index bytes in Git, or a bounded regular file in exported trees."""
    if relative.is_absolute() or ".." in relative.parts:
        raise CandidateFileError(f"unsafe repository path: {relative}")

    if use_index:
        stage = subprocess.run(
            ["git", "ls-files", "--stage", "--", relative.as_posix()],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if stage.returncode != 0 or not stage.stdout:
            raise CandidateFileError(f"cannot inspect index metadata for {relative}")
        modes = {line.split(b" ", 1)[0] for line in stage.stdout.splitlines() if line}
        if modes == {b"120000"}:
            raise CandidateFileError(f"symbolic links are not allowed: {relative}")
        if modes != {b"100644"} and modes != {b"100755"}:
            raise CandidateFileError(f"unsupported Git file mode for {relative}")

        size_result = subprocess.run(
            ["git", "cat-file", "-s", f":{relative.as_posix()}"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if size_result.returncode != 0:
            raise CandidateFileError(f"cannot inspect staged size for {relative}")
        if int(size_result.stdout.strip()) > MAX_TEXT_FILE_BYTES:
            raise CandidateFileError(
                f"file exceeds the {MAX_TEXT_FILE_BYTES}-byte inspection limit: {relative}"
            )

        result = subprocess.run(
            ["git", "show", f":{relative.as_posix()}"],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise CandidateFileError(f"cannot read staged bytes for {relative}")
        content = result.stdout
    else:
        path = root / relative
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise CandidateFileError(f"cannot inspect {relative}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise CandidateFileError(f"symbolic links are not allowed: {relative}")
        if not stat.S_ISREG(metadata.st_mode):
            raise CandidateFileError(f"not a regular file: {relative}")
        if metadata.st_size > MAX_TEXT_FILE_BYTES:
            raise CandidateFileError(
                f"file exceeds the {MAX_TEXT_FILE_BYTES}-byte inspection limit: {relative}"
            )

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as handle:
                content = handle.read(MAX_TEXT_FILE_BYTES + 1)
        except OSError as exc:
            raise CandidateFileError(f"cannot read {relative}: {exc}") from exc

    if len(content) > MAX_TEXT_FILE_BYTES:
        raise CandidateFileError(
            f"file exceeds the {MAX_TEXT_FILE_BYTES}-byte inspection limit: {relative}"
        )
    return content


def read_candidate_text(root: Path, relative: Path, use_index: bool) -> str:
    try:
        return read_candidate_bytes(root, relative, use_index).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CandidateFileError(f"file is not UTF-8 text: {relative}") from exc


def validate_json_files(
    root: Path, files: list[Path], use_index: bool | None = None
) -> list[str]:
    use_index = uses_git_index(root) if use_index is None else use_index
    errors: list[str] = []
    for relative in files:
        if relative.suffix != ".json":
            continue
        try:
            json.loads(read_candidate_text(root, relative, use_index))
        except (CandidateFileError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSON in {relative}: {exc}")
    return errors


def validate_reporting_manifest(
    root: Path, use_index: bool | None = None
) -> tuple[list[str], int]:
    use_index = uses_git_index(root) if use_index is None else use_index
    errors: list[str] = []
    try:
        manifest = json.loads(read_candidate_text(root, REPORTING_MANIFEST, use_index))
    except (CandidateFileError, json.JSONDecodeError) as exc:
        return [f"cannot read reporting manifest: {exc}"], 0

    if not isinstance(manifest, list):
        return ["reporting manifest must contain a JSON list"], 0

    names: set[str] = set()
    asset_files: set[str] = set()
    asset_root = REPORTING_MANIFEST.parent

    for index, asset in enumerate(manifest):
        location = f"reporting manifest entry {index}"
        if not isinstance(asset, dict):
            errors.append(f"{location} must be an object")
            continue

        for field in ("display_name", "description", "file"):
            if not isinstance(asset.get(field), str) or not asset[field].strip():
                errors.append(f"{location} requires a non-empty {field}")

        display_name = asset.get("display_name")
        file_name = asset.get("file")
        if not isinstance(display_name, str) or not isinstance(file_name, str):
            continue

        if display_name in names:
            errors.append(f"duplicate reporting display_name: {display_name}")
        names.add(display_name)

        if file_name in asset_files:
            errors.append(f"duplicate reporting file: {file_name}")
        asset_files.add(file_name)

        relative_asset = PurePosixPath(file_name)
        if relative_asset.is_absolute() or ".." in relative_asset.parts or relative_asset.suffix != ".sql":
            errors.append(f"unsafe reporting file path: {file_name}")
            continue

        query_path = asset_root / Path(*relative_asset.parts)
        try:
            query_text = read_candidate_text(root, query_path, use_index).strip().upper()
        except CandidateFileError as exc:
            errors.append(f"cannot inspect reporting SQL asset {file_name}: {exc}")
            continue
        if not (query_text.startswith("SELECT") or query_text.startswith("WITH")):
            errors.append(f"reporting SQL asset must begin with SELECT or WITH: {file_name}")

    return errors, len(manifest)


def scan_for_secrets(
    root: Path, files: list[Path], use_index: bool | None = None
) -> list[str]:
    """Narrow known-signature scan that never echoes candidate secret values."""
    use_index = uses_git_index(root) if use_index is None else use_index
    errors: list[str] = []
    for relative in files:
        try:
            text = read_candidate_text(root, relative, use_index)
        except CandidateFileError as exc:
            errors.append(f"cannot secret-scan {relative}: {exc}")
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    errors.append(f"possible {label} in {relative}:{line_number}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        help="Optional base ref used to reject dirty bytes on committed candidate paths.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        use_index = uses_git_index(REPO_ROOT)
        files = repository_files(REPO_ROOT)
        errors = validate_candidate_worktree_consistency(REPO_ROOT, args.base)
        errors.extend(validate_json_files(REPO_ROOT, files, use_index))
        manifest_errors, asset_count = validate_reporting_manifest(REPO_ROOT, use_index)
        errors.extend(manifest_errors)
        errors.extend(scan_for_secrets(REPO_ROOT, files, use_index))
    except CandidateFileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    json_count = sum(path.suffix == ".json" for path in files)
    print(
        f"Repository contracts passed: {len(files)} files scanned, "
        f"{json_count} JSON files parsed, {asset_count} reporting assets validated; "
        f"source={'Git index' if use_index else 'exported worktree'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
