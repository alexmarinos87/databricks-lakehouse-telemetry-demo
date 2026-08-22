"""Immutable object identity and replay planning for Auto Loader inputs.

The module is deliberately independent of Databricks APIs. It validates local
repository files, derives content-addressed landing names, and validates the
portable JSON manifest consumed by the bounded Databricks CLI uploader.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


MANIFEST_SCHEMA_VERSION = 1
CHECKPOINT_POLICY = "reuse_existing_checkpoint"
MODE_INCREMENTAL = "incremental"
MODE_BACKFILL = "backfill"
SUPPORTED_MODES = (MODE_INCREMENTAL, MODE_BACKFILL)

MAX_FILES = 20
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_MANIFEST_BYTES = 100_000
MAX_SOURCE_NAME_LENGTH = 96

_SAFE_REPLAY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SAFE_SOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,91}\.csv\Z")
_SAFE_TARGET_ROOT = re.compile(r"dbfs:/[A-Za-z0-9._/@=-][A-Za-z0-9._/@=+-]*\Z")

# Positional groups are exported for Spark regexp_extract:
# 1 mode, 2 replay ID (blank for incremental), 3 SHA-256. Incremental
# identity deliberately depends on bytes rather than the local source basename.
SPARK_OBJECT_NAME_PATTERN = (
    r"^machine-events__(incremental|backfill)__"
    r"(?:replay_([A-Za-z0-9][A-Za-z0-9._-]{0,63})__)?"
    r"sha256_([0-9a-f]{64})\.csv$"
)
_OBJECT_NAME = re.compile(SPARK_OBJECT_NAME_PATTERN)

_TOP_LEVEL_KEYS = {
    "schema_version",
    "plan_id",
    "mode",
    "replay_id",
    "checkpoint_policy",
    "allow_overwrites",
    "destination_root",
    "entries",
}
_ENTRY_KEYS = {
    "source_file",
    "source_name",
    "size_bytes",
    "sha256",
    "object_name",
    "destination_path",
}


@dataclass(frozen=True)
class IngestionObjectIdentity:
    mode: str
    replay_id: str | None
    sha256: str


@dataclass(frozen=True)
class ValidatedUploadEntry:
    source_file: str
    source_path: Path
    source_name: str
    size_bytes: int
    sha256: str
    object_name: str
    destination_path: str


@dataclass(frozen=True)
class ValidatedUploadManifest:
    schema_version: int
    plan_id: str
    mode: str
    replay_id: str | None
    checkpoint_policy: str
    allow_overwrites: bool
    destination_root: str
    entries: tuple[ValidatedUploadEntry, ...]


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative_source(path: Path, repository_root: Path) -> tuple[str, Path]:
    root = repository_root.resolve(strict=True)
    candidate = path if path.is_absolute() else root / path
    try:
        lstat_result = candidate.lstat()
    except OSError:
        raise ValueError("source file could not be inspected") from None
    if candidate.is_symlink():
        raise ValueError("source file must not be a symbolic link")
    if not candidate.is_file():
        raise ValueError("source path must be a regular file")

    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("source file must remain inside the repository root")
    if not os.path.isfile(resolved) or not os.path.samestat(lstat_result, resolved.stat()):
        raise ValueError("source file identity changed during validation")

    relative = resolved.relative_to(root).as_posix()
    if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
        raise ValueError("source file path is unsafe")
    return relative, resolved


def _read_bounded_file(path: Path) -> bytes:
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("source file must not be empty")
    if size > MAX_FILE_BYTES:
        raise ValueError("source file exceeds the bounded size limit")
    try:
        payload = path.read_bytes()
    except OSError:
        raise ValueError("source file could not be read") from None
    if len(payload) != size:
        raise ValueError("source file changed while it was read")
    return payload


def sanitize_source_name(source_name: str) -> str:
    """Return a bounded CSV basename safe for manifest evidence."""

    raw_name = Path(source_name).name
    if raw_name != source_name or not raw_name.lower().endswith(".csv"):
        raise ValueError("source name must be a CSV basename")

    stem = raw_name[:-4]
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    safe_stem = re.sub(r"_+", "_", safe_stem).strip("._-")
    if not safe_stem:
        raise ValueError("source name does not contain a safe basename")

    max_stem_length = MAX_SOURCE_NAME_LENGTH - len(".csv")
    safe_name = f"{safe_stem[:max_stem_length]}.csv"
    if not _SAFE_SOURCE_NAME.fullmatch(safe_name):
        raise ValueError("source name could not be normalized safely")
    return safe_name


def validate_replay_id(mode: str, replay_id: str | None) -> str | None:
    clean_mode = (mode or "").strip().lower()
    clean_replay_id = (replay_id or "").strip() or None
    if clean_mode not in SUPPORTED_MODES:
        raise ValueError("ingestion mode must be incremental or backfill")
    if clean_mode == MODE_INCREMENTAL and clean_replay_id is not None:
        raise ValueError("incremental ingestion must not define a replay ID")
    if clean_mode == MODE_BACKFILL:
        if clean_replay_id is None:
            raise ValueError("backfill ingestion requires a replay ID")
        if not _SAFE_REPLAY_ID.fullmatch(clean_replay_id):
            raise ValueError("replay ID contains unsupported characters or length")
    return clean_replay_id


def validate_destination_root(destination_root: str) -> str:
    root = (destination_root or "").strip().rstrip("/")
    if not root.startswith("dbfs:/"):
        raise ValueError("destination root must be a dbfs:/ path")
    if not _SAFE_TARGET_ROOT.fullmatch(root):
        raise ValueError("destination root contains unsupported characters")
    parts = PurePosixPath(root.removeprefix("dbfs:")).parts
    if ".." in parts:
        raise ValueError("destination root must not contain parent traversal")
    if len(root) > 512:
        raise ValueError("destination root is too long")
    return root


def build_object_name(
    *,
    mode: str,
    sha256: str,
    replay_id: str | None = None,
) -> str:
    clean_mode = (mode or "").strip().lower()
    clean_replay_id = validate_replay_id(clean_mode, replay_id)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise ValueError("source SHA-256 must be 64 lowercase hexadecimal characters")
    if clean_mode == MODE_INCREMENTAL:
        return f"machine-events__incremental__sha256_{sha256}.csv"
    return (
        f"machine-events__backfill__replay_{clean_replay_id}__"
        f"sha256_{sha256}.csv"
    )


def parse_object_name(object_name: str) -> IngestionObjectIdentity:
    match = _OBJECT_NAME.fullmatch(object_name or "")
    if match is None:
        raise ValueError("landing object name does not match the immutable identity contract")
    mode, replay_id, sha256 = match.groups()
    validated_replay_id = validate_replay_id(mode, replay_id or None)
    return IngestionObjectIdentity(
        mode=mode,
        replay_id=validated_replay_id,
        sha256=sha256,
    )


def plan_ingestion_uploads(
    source_paths: Iterable[str | Path],
    *,
    repository_root: str | Path,
    destination_root: str,
    mode: str = MODE_INCREMENTAL,
    replay_id: str | None = None,
) -> dict[str, Any]:
    root = Path(repository_root)
    clean_mode = (mode or "").strip().lower()
    clean_replay_id = validate_replay_id(clean_mode, replay_id)
    clean_destination_root = validate_destination_root(destination_root)

    candidates = [Path(path) for path in source_paths]
    if not candidates:
        raise ValueError("at least one source file is required")
    if len(candidates) > MAX_FILES:
        raise ValueError("source file count exceeds the bounded limit")

    entries: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()
    total_bytes = 0

    for candidate in candidates:
        source_file, resolved = _safe_relative_source(candidate, root)
        if source_file in seen_sources:
            raise ValueError("source file is repeated in the upload plan")
        seen_sources.add(source_file)

        payload = _read_bounded_file(resolved)
        total_bytes += len(payload)
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("total source bytes exceed the bounded limit")

        digest = _sha256_bytes(payload)
        source_name = sanitize_source_name(resolved.name)
        object_name = build_object_name(
            mode=clean_mode,
            replay_id=clean_replay_id,
            sha256=digest,
        )
        destination_path = f"{clean_destination_root}/{object_name}"
        if destination_path in seen_destinations:
            raise ValueError("multiple source files resolve to one destination")
        seen_destinations.add(destination_path)

        entries.append(
            {
                "source_file": source_file,
                "source_name": source_name,
                "size_bytes": len(payload),
                "sha256": digest,
                "object_name": object_name,
                "destination_path": destination_path,
            }
        )

    entries.sort(key=lambda entry: entry["source_file"])
    unsigned_manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "mode": clean_mode,
        "replay_id": clean_replay_id,
        "checkpoint_policy": CHECKPOINT_POLICY,
        "allow_overwrites": False,
        "destination_root": clean_destination_root,
        "entries": entries,
    }
    plan_id = _sha256_bytes(_canonical_json(unsigned_manifest))
    return {"plan_id": plan_id, **unsigned_manifest}


def write_manifest(manifest: Mapping[str, Any], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    if len(rendered.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ValueError("upload manifest exceeds the bounded size limit")
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output)


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has an invalid shape")


def validate_manifest(
    manifest: Mapping[str, Any], *, repository_root: str | Path
) -> ValidatedUploadManifest:
    if not isinstance(manifest, Mapping):
        raise ValueError("upload manifest must be a JSON object")
    _require_exact_keys(manifest, _TOP_LEVEL_KEYS, label="upload manifest")

    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ValueError("upload manifest schema version is unsupported")
    mode = manifest["mode"]
    if not isinstance(mode, str):
        raise ValueError("upload manifest mode is invalid")
    replay_id_value = manifest["replay_id"]
    if replay_id_value is not None and not isinstance(replay_id_value, str):
        raise ValueError("upload manifest replay ID is invalid")
    replay_id = validate_replay_id(mode, replay_id_value)
    if manifest["checkpoint_policy"] != CHECKPOINT_POLICY:
        raise ValueError("upload manifest must reuse the existing checkpoint")
    if manifest["allow_overwrites"] is not False:
        raise ValueError("upload manifest must prohibit overwrites")
    if not isinstance(manifest["destination_root"], str):
        raise ValueError("upload manifest destination root is invalid")
    destination_root = validate_destination_root(manifest["destination_root"])

    entries_raw = manifest["entries"]
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ValueError("upload manifest entries must be a non-empty list")
    if len(entries_raw) > MAX_FILES:
        raise ValueError("upload manifest entry count exceeds the bounded limit")

    root = Path(repository_root)
    validated_entries: list[ValidatedUploadEntry] = []
    total_bytes = 0
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()

    for raw_entry in entries_raw:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("upload manifest entry must be a JSON object")
        _require_exact_keys(raw_entry, _ENTRY_KEYS, label="upload manifest entry")
        if any(
            not isinstance(raw_entry[key], str) or not raw_entry[key]
            for key in (
                "source_file",
                "source_name",
                "sha256",
                "object_name",
                "destination_path",
            )
        ):
            raise ValueError("upload manifest entry contains an invalid string field")
        if not isinstance(raw_entry["size_bytes"], int) or isinstance(
            raw_entry["size_bytes"], bool
        ):
            raise ValueError("upload manifest entry size is invalid")

        source_file, resolved = _safe_relative_source(
            Path(raw_entry["source_file"]), root
        )
        if source_file != raw_entry["source_file"]:
            raise ValueError("upload manifest source path is not canonical")
        if source_file in seen_sources:
            raise ValueError("upload manifest contains a duplicate source file")
        seen_sources.add(source_file)

        payload = _read_bounded_file(resolved)
        size_bytes = len(payload)
        total_bytes += size_bytes
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("upload manifest total bytes exceed the bounded limit")
        digest = _sha256_bytes(payload)
        if raw_entry["size_bytes"] != size_bytes or raw_entry["sha256"] != digest:
            raise ValueError("local source file no longer matches the upload manifest")

        source_name = sanitize_source_name(resolved.name)
        if raw_entry["source_name"] != source_name:
            raise ValueError("upload manifest source name is not canonical")
        object_name = build_object_name(
            mode=mode,
            replay_id=replay_id,
            sha256=digest,
        )
        if raw_entry["object_name"] != object_name:
            raise ValueError("upload manifest object name is inconsistent")
        if parse_object_name(object_name).sha256 != digest:
            raise ValueError("upload manifest object identity is inconsistent")
        destination_path = f"{destination_root}/{object_name}"
        if raw_entry["destination_path"] != destination_path:
            raise ValueError("upload manifest destination path is inconsistent")
        if destination_path in seen_destinations:
            raise ValueError("upload manifest contains a duplicate destination")
        seen_destinations.add(destination_path)

        validated_entries.append(
            ValidatedUploadEntry(
                source_file=source_file,
                source_path=resolved,
                source_name=source_name,
                size_bytes=size_bytes,
                sha256=digest,
                object_name=object_name,
                destination_path=destination_path,
            )
        )

    unsigned_manifest = {
        key: manifest[key]
        for key in (
            "schema_version",
            "mode",
            "replay_id",
            "checkpoint_policy",
            "allow_overwrites",
            "destination_root",
            "entries",
        )
    }
    expected_plan_id = _sha256_bytes(_canonical_json(unsigned_manifest))
    if manifest["plan_id"] != expected_plan_id:
        raise ValueError("upload manifest plan ID does not match its contents")

    return ValidatedUploadManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        plan_id=expected_plan_id,
        mode=mode,
        replay_id=replay_id,
        checkpoint_policy=CHECKPOINT_POLICY,
        allow_overwrites=False,
        destination_root=destination_root,
        entries=tuple(validated_entries),
    )


def load_manifest(
    manifest_path: str | Path, *, repository_root: str | Path
) -> ValidatedUploadManifest:
    path = Path(manifest_path)
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("upload manifest must be a regular file")
        size = path.stat().st_size
    except OSError:
        raise ValueError("upload manifest could not be inspected") from None
    if size <= 0 or size > MAX_MANIFEST_BYTES:
        raise ValueError("upload manifest size is outside the bounded limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("upload manifest could not be parsed") from None
    return validate_manifest(payload, repository_root=repository_root)
