"""Bound default increment discovery without treating unreadable input as empty."""

from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path

MAX_DIRECTORY_ENTRIES = 1_000
MAX_INCREMENT_FILES = 99  # Leave one slot for the mandatory sample in the reader.


class FixtureDiscoveryError(ValueError):
    """A stable discovery category without raw paths or operating-system messages."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _directory(path: Path, *, optional: bool = False) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        if optional:
            return None
        raise FixtureDiscoveryError("fixture_directory_unavailable") from exc
    except (OSError, ValueError) as exc:
        raise FixtureDiscoveryError("fixture_directory_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise FixtureDiscoveryError("fixture_directory_symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise FixtureDiscoveryError("fixture_directory_not_directory")
    return metadata


def _identity(metadata: os.stat_result | None) -> tuple[int, ...] | None:
    if metadata is None:
        return None
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def discover_increment_paths(root: Path) -> tuple[str, ...]:
    """List immediate CSV entries under a normalized, caller-controlled root.

    Missing increments are optional; a present but unreadable or malformed
    directory is not. File type/content validation remains the reader's job.
    These checks detect observed drift, not hostile ancestor-rename attacks.
    """
    data = root / "data"
    directory = data / "increments"
    parent_before = _directory(data)
    before = _directory(directory, optional=True)
    paths: list[str] = []
    if before is not None:
        try:
            with os.scandir(directory) as entries:
                for count, entry in enumerate(entries, start=1):
                    if count > MAX_DIRECTORY_ENTRIES:
                        raise FixtureDiscoveryError("fixture_directory_entry_limit_exceeded")
                    if fnmatch.fnmatch(entry.name, "*.csv"):
                        if len(paths) >= MAX_INCREMENT_FILES:
                            raise FixtureDiscoveryError("repository_file_count_exceeded")
                        paths.append(f"data/increments/{entry.name}")
        except FixtureDiscoveryError:
            raise
        except (OSError, ValueError) as exc:
            raise FixtureDiscoveryError("fixture_directory_read_failed") from exc
    if (_identity(_directory(data)) != _identity(parent_before)
            or _identity(_directory(directory, optional=True)) != _identity(before)):
        raise FixtureDiscoveryError("fixture_directory_changed")
    return tuple(sorted(paths))
