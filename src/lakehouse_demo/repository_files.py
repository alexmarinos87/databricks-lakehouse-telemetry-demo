"""Bounded, race-aware reads and non-overwriting writes for repository evidence."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


DEFAULT_MAX_FILES = 100
DEFAULT_MAX_FILE_BYTES = 2_000_000
DEFAULT_MAX_TOTAL_BYTES = 10_000_000
MAX_PUBLIC_PATH_CHARS = 512


class RepositoryFileError(RuntimeError):
    """A sanitized repository-file boundary failure."""

    def __init__(self, category: str, source: str = "<input>") -> None:
        self.category = category
        self.source = source
        super().__init__(f"{category}: {source}")


@dataclass(frozen=True)
class RepositoryFileSnapshot:
    """Verified bytes and identity for one repository-contained regular file."""

    relative_path: str
    content: bytes
    sha256: str
    size_bytes: int
    identity: tuple[int, int, int, int, int]


def _public_source(value: str) -> str:
    if len(value) <= MAX_PUBLIC_PATH_CHARS and value.isprintable():
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="backslashreplace")).hexdigest()
    return f"<path-sha256:{digest}>"


def normalize_repository_root(value: str | Path) -> Path:
    """Return a verified, non-symlink repository directory."""
    root = Path(value)
    try:
        metadata = root.lstat()
    except (OSError, ValueError) as exc:
        raise RepositoryFileError("repository_root_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise RepositoryFileError("repository_root_symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise RepositoryFileError("repository_root_not_directory")
    try:
        return root.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise RepositoryFileError("repository_root_unavailable") from exc


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_at_most(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    while limit > 0:
        chunk = os.read(descriptor, min(limit, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        limit -= len(chunk)
    return b"".join(chunks)


def _resolve_file(root: Path, value: str | Path) -> tuple[Path, str]:
    supplied = Path(value)
    candidate = supplied if supplied.is_absolute() else root / supplied
    source = _public_source(supplied.as_posix())
    try:
        supplied_metadata = candidate.lstat()
    except (OSError, ValueError) as exc:
        raise RepositoryFileError("repository_file_unavailable", source) from exc
    if stat.S_ISLNK(supplied_metadata.st_mode):
        raise RepositoryFileError("repository_file_symlink", source)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise RepositoryFileError("repository_file_unavailable", source) from exc
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise RepositoryFileError(
            "repository_file_outside_root", _public_source(supplied.as_posix())
        ) from exc
    return resolved, relative


def _read_one(
    path: Path,
    relative_path: str,
    *,
    max_file_bytes: int,
    remaining_bytes: int,
) -> RepositoryFileSnapshot:
    source = _public_source(relative_path)
    try:
        before = path.lstat()
    except (OSError, ValueError) as exc:
        raise RepositoryFileError("repository_file_unavailable", source) from exc
    if stat.S_ISLNK(before.st_mode):
        raise RepositoryFileError("repository_file_symlink", source)
    if not stat.S_ISREG(before.st_mode):
        raise RepositoryFileError("repository_file_not_regular", source)
    if before.st_size > max_file_bytes:
        raise RepositoryFileError("repository_file_too_large", source)
    if before.st_size > remaining_bytes:
        raise RepositoryFileError("repository_input_too_large", source)

    descriptor: int | None = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOCTTY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RepositoryFileError("repository_file_not_regular", source)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise RepositoryFileError("repository_file_changed", source)
        if opened.st_size > max_file_bytes:
            raise RepositoryFileError("repository_file_too_large", source)
        if opened.st_size > remaining_bytes:
            raise RepositoryFileError("repository_input_too_large", source)

        content = _read_at_most(descriptor, min(max_file_bytes, remaining_bytes) + 1)
        final = os.fstat(descriptor)
        if _identity(opened) != _identity(final) or len(content) != opened.st_size:
            raise RepositoryFileError("repository_file_changed", source)
        if len(content) > max_file_bytes:
            raise RepositoryFileError("repository_file_too_large", source)
        if len(content) > remaining_bytes:
            raise RepositoryFileError("repository_input_too_large", source)
        return RepositoryFileSnapshot(
            relative_path=relative_path,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            identity=_identity(final),
        )
    except RepositoryFileError:
        raise
    except (OSError, ValueError) as exc:
        raise RepositoryFileError("repository_file_read_failed", source) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def read_repository_files(
    repository_root: str | Path,
    paths: Iterable[str | Path],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> tuple[RepositoryFileSnapshot, ...]:
    """Read unique repository files in deterministic relative-path order."""
    if max_files <= 0 or max_file_bytes <= 0 or max_total_bytes <= 0:
        raise RepositoryFileError("repository_read_limit_invalid")
    root = normalize_repository_root(repository_root)

    resolved_by_relative: dict[str, Path] = {}
    for position, value in enumerate(paths, start=1):
        if position > max_files:
            raise RepositoryFileError("repository_file_count_exceeded")
        resolved, relative = _resolve_file(root, value)
        resolved_by_relative.setdefault(relative, resolved)
    if not resolved_by_relative:
        raise RepositoryFileError("repository_files_missing")
    if len(resolved_by_relative) > max_files:
        raise RepositoryFileError("repository_file_count_exceeded")

    snapshots: list[RepositoryFileSnapshot] = []
    total_bytes = 0
    for relative, path in sorted(resolved_by_relative.items()):
        snapshot = _read_one(
            path,
            relative,
            max_file_bytes=max_file_bytes,
            remaining_bytes=max_total_bytes - total_bytes,
        )
        snapshots.append(snapshot)
        total_bytes += snapshot.size_bytes
    return tuple(snapshots)


def verify_repository_files_unchanged(
    repository_root: str | Path,
    expected: Iterable[RepositoryFileSnapshot],
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> None:
    """Fail if any previously read file was replaced or changed."""
    expected_items = tuple(expected)
    observed = read_repository_files(
        repository_root,
        (item.relative_path for item in expected_items),
        max_files=max(len(expected_items), 1),
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    if len(observed) != len(expected_items):
        raise RepositoryFileError("repository_file_set_changed")
    for before, after in zip(expected_items, observed):
        if (
            before.relative_path != after.relative_path
            or before.sha256 != after.sha256
            or before.size_bytes != after.size_bytes
            or before.identity != after.identity
        ):
            raise RepositoryFileError("repository_file_changed", before.relative_path)


def _verify_output_parent(destination: Path) -> None:
    """Reject a symbolic-link or non-directory existing output ancestor."""
    current = destination.parent
    missing: list[Path] = []
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    try:
        metadata = current.lstat()
    except (OSError, ValueError) as exc:
        raise RepositoryFileError("output_parent_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise RepositoryFileError("output_parent_symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise RepositoryFileError("output_parent_not_directory")
    for item in reversed(missing):
        if item.exists() or item.is_symlink():
            raise RepositoryFileError("output_parent_changed")


def write_new_text_package(
    output_dir: str | Path,
    files: Mapping[str, str],
) -> Path:
    """Create a new evidence directory without overwriting existing state."""
    destination = Path(output_dir)
    if not files:
        raise RepositoryFileError("output_files_missing")
    for name in files:
        path = Path(name)
        if (
            path.is_absolute()
            or path.name != name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or not name.isprintable()
            or len(name) > 255
        ):
            raise RepositoryFileError("output_filename_invalid", _public_source(name))
    _verify_output_parent(destination)
    if destination.exists() or destination.is_symlink():
        raise RepositoryFileError(
            "output_directory_exists", _public_source(destination.as_posix())
        )

    try:
        destination.mkdir(parents=True, exist_ok=False)
        metadata = destination.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RepositoryFileError("output_directory_unavailable")
        for name, content in sorted(files.items()):
            target = destination / name
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        return destination
    except RepositoryFileError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except (OSError, ValueError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryFileError("output_directory_unavailable") from exc
