"""Bounded local I/O helpers for protected evidence package builders."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Any

SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
ARTIFACT_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{2,127}\Z")
_EXTERNAL_CONTROL_OUTPUT_PREFIX = "external_control_index_output"
_EXTERNAL_CONTROL_WRITE_PREFIX = "external_control_index"
_EXTERNAL_CONTROL_JSON = "external-control-evidence-index.json"
_EXTERNAL_CONTROL_MARKDOWN = "external-control-evidence-index.md"
_external_control_report_root: Path | None = None
_transactional_directories: dict[Path, Path] = {}


class EvidenceIOError(RuntimeError):
    """Stable local-evidence error category safe to expose."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def artifact_id(value: Any, category: str) -> str:
    if not isinstance(value, str) or not ARTIFACT_ID_PATTERN.fullmatch(value):
        raise EvidenceIOError(category)
    return value


def fingerprint(value: Any, category: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise EvidenceIOError(category)
    return value


def regular_bytes(path: Path, *, maximum: int, category: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError:
        raise EvidenceIOError(f"{category}_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise EvidenceIOError(f"{category}_not_regular")
    if metadata.st_size < 1 or metadata.st_size > maximum:
        raise EvidenceIOError(f"{category}_size_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise EvidenceIOError(f"{category}_not_regular")
            payload = handle.read(maximum + 1)
            closed = os.fstat(handle.fileno())
    except EvidenceIOError:
        raise
    except OSError:
        raise EvidenceIOError(f"{category}_unreadable") from None
    if len(payload) < 1 or len(payload) > maximum:
        raise EvidenceIOError(f"{category}_size_invalid")
    before = (metadata.st_dev, metadata.st_ino, metadata.st_size)
    during = (opened.st_dev, opened.st_ino, opened.st_size)
    after = (closed.st_dev, closed.st_ino, closed.st_size)
    if before != during or during != after:
        raise EvidenceIOError(f"{category}_changed_during_read")
    return payload


def json_object(payload: bytes, category: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceIOError(f"{category}_invalid_json") from None
    if not isinstance(value, dict):
        raise EvidenceIOError(f"{category}_shape_invalid")
    return value


def canonical_relative(value: Any, *, invalid: str, noncanonical: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        raise EvidenceIOError(invalid)
    if "\\" in value or any(char in value for char in ("\x00", "\n", "\r")):
        raise EvidenceIOError(invalid)
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise EvidenceIOError(invalid)
    canonical = relative.as_posix()
    if value != canonical:
        raise EvidenceIOError(noncanonical)
    return canonical


def protected_path(root: Path, relative_value: Any, *, prefix: str) -> Path:
    global _external_control_report_root

    relative = PurePosixPath(
        canonical_relative(
            relative_value,
            invalid=f"{prefix}_path_invalid",
            noncanonical=f"{prefix}_path_not_canonical",
        )
    )
    try:
        root_metadata = root.lstat()
    except OSError:
        raise EvidenceIOError(f"{prefix}_root_unavailable") from None
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise EvidenceIOError(f"{prefix}_root_invalid")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except OSError:
            raise EvidenceIOError(f"{prefix}_unavailable") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise EvidenceIOError(f"{prefix}_symlink_rejected")
    if not candidate.is_file():
        raise EvidenceIOError(f"{prefix}_not_regular")
    try:
        resolved_root = root.resolve(strict=True)
        candidate.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError):
        raise EvidenceIOError(f"{prefix}_outside_root") from None
    if prefix.startswith("external_control_") and prefix.endswith("_report"):
        _external_control_report_root = resolved_root
    return candidate


def ensure_output_outside_root(output: Path, root: Path, category: str) -> None:
    try:
        resolved_root = root.resolve(strict=True)
        resolved_output = output.resolve(strict=False)
    except OSError:
        raise EvidenceIOError(f"{category}_separation_unavailable") from None
    try:
        resolved_output.relative_to(resolved_root)
    except ValueError:
        return
    raise EvidenceIOError(f"{category}_inside_protected_root")


def _transaction_key(path: Path) -> Path:
    return path.absolute()


def _cleanup_transaction(staging: Path) -> None:
    _transactional_directories.pop(_transaction_key(staging), None)
    try:
        if staging.is_symlink():
            staging.unlink()
        elif staging.exists():
            shutil.rmtree(staging)
    except OSError:
        pass


def _prepare_transactional_directory(path: Path, prefix: str) -> Path:
    if _external_control_report_root is not None:
        ensure_output_outside_root(path, _external_control_report_root, prefix)
    if not path.name or path.name in {".", ".."}:
        raise EvidenceIOError(f"{prefix}_directory_name_invalid")
    if path.is_symlink():
        raise EvidenceIOError(f"{prefix}_directory_is_symlink")
    if path.exists():
        raise EvidenceIOError(f"{prefix}_directory_exists")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise EvidenceIOError(f"{prefix}_parent_is_symlink")
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise EvidenceIOError(f"{prefix}_parent_unavailable") from None
    if parent.is_symlink() or not parent.is_dir():
        raise EvidenceIOError(f"{prefix}_parent_invalid")
    staging = parent / f".{path.name}.staging"
    if staging.is_symlink():
        raise EvidenceIOError(f"{prefix}_staging_is_symlink")
    if staging.exists():
        raise EvidenceIOError(f"{prefix}_staging_exists")
    try:
        staging.mkdir(mode=0o700)
    except OSError:
        raise EvidenceIOError(f"{prefix}_staging_unavailable") from None
    if staging.is_symlink() or not staging.is_dir():
        raise EvidenceIOError(f"{prefix}_staging_invalid")
    _transactional_directories[_transaction_key(staging)] = path
    return staging


def prepare_output_directory(path: Path, prefix: str) -> Path:
    if prefix == _EXTERNAL_CONTROL_OUTPUT_PREFIX:
        return _prepare_transactional_directory(path, prefix)
    if path.exists() and path.is_symlink():
        raise EvidenceIOError(f"{prefix}_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise EvidenceIOError(f"{prefix}_directory_unavailable") from None
    if path.is_symlink() or not path.is_dir():
        raise EvidenceIOError(f"{prefix}_directory_invalid")
    return path


def _write_file_atomic(path: Path, content: str, prefix: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise EvidenceIOError(f"{prefix}_temporary_output_exists")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise EvidenceIOError(f"{prefix}_output_path_invalid")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise EvidenceIOError(f"{prefix}_output_write_failed") from None


def _write_transactional_index(path: Path, content: str, prefix: str) -> None:
    staging = path.parent
    final_directory = _transactional_directories.get(_transaction_key(staging))
    if final_directory is None:
        raise EvidenceIOError(f"{prefix}_transaction_missing")
    try:
        _write_file_atomic(path, content, prefix)
        if path.name != _EXTERNAL_CONTROL_MARKDOWN:
            return
        json_path = staging / _EXTERNAL_CONTROL_JSON
        if json_path.is_symlink() or not json_path.is_file():
            raise EvidenceIOError(f"{prefix}_transaction_incomplete")
        if final_directory.is_symlink():
            raise EvidenceIOError(f"{prefix}_output_directory_is_symlink")
        if final_directory.exists():
            raise EvidenceIOError(f"{prefix}_output_directory_exists")
        staging.replace(final_directory)
        _transactional_directories.pop(_transaction_key(staging), None)
    except EvidenceIOError:
        _cleanup_transaction(staging)
        raise
    except OSError:
        _cleanup_transaction(staging)
        raise EvidenceIOError(f"{prefix}_output_publish_failed") from None


def write_atomic(path: Path, content: str, prefix: str) -> None:
    if prefix == _EXTERNAL_CONTROL_WRITE_PREFIX:
        _write_transactional_index(path, content, prefix)
        return
    _write_file_atomic(path, content, prefix)


def publish_candidate(candidate: Path, final_path: Path, prefix: str) -> None:
    if final_path.exists() and (final_path.is_symlink() or not final_path.is_file()):
        raise EvidenceIOError(f"{prefix}_output_path_invalid")
    try:
        candidate.replace(final_path)
    except OSError:
        raise EvidenceIOError(f"{prefix}_output_write_failed") from None
