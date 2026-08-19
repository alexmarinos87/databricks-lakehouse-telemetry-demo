"""Validate committed machine-event CSV fixtures without Spark dependencies."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from lakehouse_demo.azure_ingestion import MACHINE_EVENT_COLUMNS


ERROR = "error"
INFO = "info"

NO_FIXTURES = "no_fixtures"
FILE_COUNT_LIMIT_EXCEEDED = "file_count_limit_exceeded"
SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
NON_REGULAR_FILE = "non_regular_file"
FILE_READ_ERROR = "file_read_error"
FILE_IDENTITY_CHANGED = "file_identity_changed"
FILE_SIZE_LIMIT_EXCEEDED = "file_size_limit_exceeded"
TOTAL_SIZE_LIMIT_EXCEEDED = "total_size_limit_exceeded"
EMPTY_FIXTURE = "empty_fixture"
HEADER_MISMATCH = "header_mismatch"
NO_DATA_ROWS = "no_data_rows"
ROW_LIMIT_EXCEEDED = "row_limit_exceeded"
ROW_SHAPE_INVALID = "row_shape_invalid"
REQUIRED_KEY_MISSING = "required_key_missing"
UTC_TIMESTAMP_INVALID = "utc_timestamp_invalid"
DECIMAL_INVALID = "decimal_invalid"
INTEGER_FORMAT_INVALID = "integer_format_invalid"
INTEGER_RANGE_VIOLATION = "integer_range_violation"
NON_NEGATIVE_VIOLATION = "non_negative_violation"
FUEL_RANGE_VIOLATION = "fuel_range_violation"
REPLAY_DUPLICATE = "replay_duplicate"
CONFLICTING_DUPLICATE = "conflicting_duplicate"
CSV_PARSE_ERROR = "csv_parse_error"
FINDING_LIMIT_EXCEEDED = "finding_limit_exceeded"

MAX_FIXTURE_FILES = 100
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_INPUT_BYTES = 10_000_000
MAX_TOTAL_ROWS = 100_000
MAX_FINDINGS = 1_000
MAX_SOURCE_CHARS = 512
MAX_SPARK_INT = 2_147_483_647

REQUIRED_KEY_COLUMNS = ("event_id", "machine_id", "event_ts", "site_id", "client_id")
DECIMAL_COLUMNS = (
    "hour_meter",
    "temperature_c",
    "vibration_mm_s",
    "fuel_level_pct",
    "maintenance_cost_gbp",
)
INTEGER_COLUMNS = ("duration_minutes", "downtime_minutes", "part_quantity")

UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
UTC_TIMESTAMP_PATTERN = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
CANONICAL_INTEGER_PATTERN = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")

_MESSAGES = {
    NO_FIXTURES: "At least one fixture is required",
    FILE_COUNT_LIMIT_EXCEEDED: "Fixture count exceeds the configured limit",
    SYMLINK_NOT_ALLOWED: "Symbolic-link fixtures are not allowed",
    NON_REGULAR_FILE: "Fixture must be a regular file",
    FILE_READ_ERROR: "Unable to inspect, read, or decode fixture",
    FILE_IDENTITY_CHANGED: "Fixture identity or contents changed while reading",
    FILE_SIZE_LIMIT_EXCEEDED: "Fixture exceeds the configured byte limit",
    TOTAL_SIZE_LIMIT_EXCEEDED: "Total input exceeds the configured byte limit",
    EMPTY_FIXTURE: "Fixture is empty",
    HEADER_MISMATCH: "Header must exactly match the machine-event source contract",
    NO_DATA_ROWS: "Fixture contains a header but no data rows",
    ROW_LIMIT_EXCEEDED: "Row count exceeds the configured limit",
    ROW_SHAPE_INVALID: "Row has the wrong number of fields",
    REQUIRED_KEY_MISSING: "Required source key is blank",
    UTC_TIMESTAMP_INVALID: "event_ts is not a valid canonical UTC timestamp",
    DECIMAL_INVALID: "Field must be a finite decimal",
    INTEGER_FORMAT_INVALID: "Field must use canonical non-negative integer spelling",
    INTEGER_RANGE_VIOLATION: "Field exceeds the Spark INT maximum",
    NON_NEGATIVE_VIOLATION: "Field must be greater than or equal to zero",
    FUEL_RANGE_VIOLATION: "fuel_level_pct must be between 0 and 100 inclusive",
    REPLAY_DUPLICATE: "Row is identical to the first occurrence of this record ID",
    CONFLICTING_DUPLICATE: "Row conflicts with the first occurrence of this record ID",
    CSV_PARSE_ERROR: "Unable to parse CSV fixture",
    FINDING_LIMIT_EXCEEDED: "Further findings were truncated at the configured limit",
}


@dataclass(frozen=True)
class ValidationFinding:
    """One bounded validation result tied to a fixture location."""

    code: str
    severity: str
    source: str
    line: int
    field: str | None
    record_id_digest: str | None
    message: str
    first_source: str | None = None
    first_line: int | None = None


@dataclass(frozen=True)
class ValidationReport:
    """Immutable findings returned by machine-event fixture validation."""

    findings: tuple[ValidationFinding, ...]

    @property
    def error_findings(self) -> tuple[ValidationFinding, ...]:
        return tuple(item for item in self.findings if item.severity == ERROR)

    @property
    def replay_duplicates(self) -> tuple[ValidationFinding, ...]:
        return tuple(item for item in self.findings if item.code == REPLAY_DUPLICATE)

    @property
    def is_valid(self) -> bool:
        return not self.error_findings


def _bounded_source(path: Path) -> str:
    source = path.as_posix()
    if len(source) <= MAX_SOURCE_CHARS and source.isprintable():
        return source
    digest = hashlib.sha256(source.encode("utf-8", errors="backslashreplace")).hexdigest()
    return f"<path-sha256:{digest}>"


class _FindingCollector:
    """Build a report while enforcing the public finding-count ceiling."""

    def __init__(self) -> None:
        self.findings: list[ValidationFinding] = []
        self.truncated = False

    def add(
        self,
        code: str,
        source: str,
        line: int = 0,
        *,
        field: str | None = None,
        record_id_digest: str | None = None,
        first_source: str | None = None,
        first_line: int | None = None,
    ) -> bool:
        """Add one finding and return whether validation may continue."""
        if self.truncated:
            return False
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(
                ValidationFinding(
                    code,
                    INFO if code == REPLAY_DUPLICATE else ERROR,
                    source,
                    line,
                    field,
                    record_id_digest,
                    _MESSAGES[code],
                    first_source,
                    first_line,
                )
            )
            return True

        sentinel = ValidationFinding(
            FINDING_LIMIT_EXCEEDED,
            ERROR,
            "<validation>",
            0,
            None,
            None,
            _MESSAGES[FINDING_LIMIT_EXCEEDED],
        )
        if self.findings:
            self.findings[-1] = sentinel
        else:  # Defensive behavior if a caller patches the configured limit to zero.
            self.findings.append(sentinel)
        self.truncated = True
        return False

    def report(self) -> ValidationReport:
        return ValidationReport(tuple(self.findings))


def _record_id_digest(event_id: str) -> str | None:
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest() if event_id.strip() else None


def _parse_decimal(value: str) -> Decimal | None:
    try:
        result = Decimal(value.strip())
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _parse_canonical_spark_int(value: str) -> tuple[int | None, str | None]:
    if not CANONICAL_INTEGER_PATTERN.fullmatch(value):
        return None, INTEGER_FORMAT_INVALID
    maximum = str(MAX_SPARK_INT)
    if len(value) > len(maximum) or (len(value) == len(maximum) and value > maximum):
        return None, INTEGER_RANGE_VIOLATION
    return int(value), None


def _is_strict_utc_timestamp(value: str) -> bool:
    if not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        return False
    try:
        datetime.strptime(value, UTC_TIMESTAMP_FORMAT)
    except ValueError:
        return False
    return True


def _read_at_most(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    while limit > 0:
        chunk = os.read(descriptor, min(limit, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        limit -= len(chunk)
    return b"".join(chunks)


def _file_snapshot(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_fixture(
    path: Path,
    remaining_bytes: int,
    collector: _FindingCollector,
) -> tuple[bytes | None, bool]:
    """Read one verified regular file, returning content and a stop flag."""
    source = _bounded_source(path)
    try:
        before = path.lstat()
    except (OSError, ValueError):
        collector.add(FILE_READ_ERROR, source)
        return None, False
    if stat.S_ISLNK(before.st_mode):
        collector.add(SYMLINK_NOT_ALLOWED, source)
        return None, False
    if not stat.S_ISREG(before.st_mode):
        collector.add(NON_REGULAR_FILE, source)
        return None, False

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
            collector.add(NON_REGULAR_FILE, source)
            return None, False
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            collector.add(FILE_IDENTITY_CHANGED, source)
            return None, False
        if opened.st_size > MAX_FILE_BYTES:
            collector.add(FILE_SIZE_LIMIT_EXCEEDED, source)
            return None, False
        if opened.st_size > remaining_bytes:
            collector.add(TOTAL_SIZE_LIMIT_EXCEEDED, source)
            return None, True

        read_limit = min(MAX_FILE_BYTES, remaining_bytes) + 1
        content = _read_at_most(descriptor, read_limit)
        final = os.fstat(descriptor)
        if _file_snapshot(final) != _file_snapshot(opened) or len(content) != opened.st_size:
            collector.add(FILE_IDENTITY_CHANGED, source)
            return None, False
        if len(content) > MAX_FILE_BYTES:
            collector.add(FILE_SIZE_LIMIT_EXCEEDED, source)
            return None, False
        if len(content) > remaining_bytes:
            collector.add(TOTAL_SIZE_LIMIT_EXCEEDED, source)
            return None, True
        return content, False
    except (OSError, ValueError):
        collector.add(FILE_READ_ERROR, source)
        return None, False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_row(
    row: dict[str, str],
    source: str,
    line: int,
    collector: _FindingCollector,
    seen: dict[str, tuple[tuple[str, ...], str, int]],
) -> bool:
    """Validate one correctly shaped row; return whether work may continue."""
    event_id = row["event_id"]
    digest = _record_id_digest(event_id)

    for field in REQUIRED_KEY_COLUMNS:
        if not row[field].strip() and not collector.add(
            REQUIRED_KEY_MISSING, source, line, field=field, record_id_digest=digest
        ):
            return False

    if not _is_strict_utc_timestamp(row["event_ts"]) and not collector.add(
        UTC_TIMESTAMP_INVALID, source, line, field="event_ts", record_id_digest=digest
    ):
        return False

    decimals: dict[str, Decimal] = {}
    for field in DECIMAL_COLUMNS:
        value = _parse_decimal(row[field])
        if value is None:
            if not collector.add(
                DECIMAL_INVALID, source, line, field=field, record_id_digest=digest
            ):
                return False
        else:
            decimals[field] = value

    for field in INTEGER_COLUMNS:
        _, code = _parse_canonical_spark_int(row[field])
        if code and not collector.add(code, source, line, field=field, record_id_digest=digest):
            return False

    cost = decimals.get("maintenance_cost_gbp")
    if cost is not None and cost < 0 and not collector.add(
        NON_NEGATIVE_VIOLATION,
        source,
        line,
        field="maintenance_cost_gbp",
        record_id_digest=digest,
    ):
        return False

    fuel = decimals.get("fuel_level_pct")
    if fuel is not None and not Decimal("0") <= fuel <= Decimal("100") and not collector.add(
        FUEL_RANGE_VIOLATION,
        source,
        line,
        field="fuel_level_pct",
        record_id_digest=digest,
    ):
        return False

    if event_id.strip():
        fingerprint = tuple(row[column] for column in MACHINE_EVENT_COLUMNS)
        first = seen.get(event_id)
        if first is None:
            seen[event_id] = (fingerprint, source, line)
        else:
            first_fingerprint, first_source, first_line = first
            code = REPLAY_DUPLICATE if fingerprint == first_fingerprint else CONFLICTING_DUPLICATE
            if not collector.add(
                code,
                source,
                line,
                field="event_id",
                record_id_digest=digest,
                first_source=first_source,
                first_line=first_line,
            ):
                return False
    return True


def _validate_content(
    content: bytes,
    source: str,
    collector: _FindingCollector,
    seen: dict[str, tuple[tuple[str, ...], str, int]],
    total_rows: int,
) -> tuple[int, bool]:
    if not content:
        collector.add(EMPTY_FIXTURE, source)
        return total_rows, collector.truncated
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        collector.add(FILE_READ_ERROR, source)
        return total_rows, collector.truncated

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        collector.add(EMPTY_FIXTURE, source)
        return total_rows, collector.truncated
    except csv.Error:
        collector.add(CSV_PARSE_ERROR, source, 1)
        return total_rows, collector.truncated
    if tuple(header) != MACHINE_EVENT_COLUMNS:
        collector.add(HEADER_MISMATCH, source, 1)
        return total_rows, collector.truncated

    file_rows = 0
    try:
        for values in reader:
            if total_rows >= MAX_TOTAL_ROWS:
                collector.add(ROW_LIMIT_EXCEEDED, source, reader.line_num)
                return total_rows, True
            total_rows += 1
            file_rows += 1
            if len(values) != len(MACHINE_EVENT_COLUMNS):
                if not collector.add(ROW_SHAPE_INVALID, source, reader.line_num):
                    return total_rows, True
                continue
            row = dict(zip(MACHINE_EVENT_COLUMNS, values))
            if not _validate_row(row, source, reader.line_num, collector, seen):
                return total_rows, True
    except csv.Error:
        collector.add(CSV_PARSE_ERROR, source, max(reader.line_num, 1))
        return total_rows, collector.truncated

    if not file_rows:
        collector.add(NO_DATA_ROWS, source, 1)
    return total_rows, collector.truncated


def _collect_paths(paths: Iterable[str | Path]) -> tuple[list[Path], bool]:
    unique: dict[str, Path] = {}
    for position, value in enumerate(paths, start=1):
        if position > MAX_FIXTURE_FILES:
            return [], True
        path = Path(value)
        key = path.as_posix()
        if key not in unique:
            unique[key] = path
    return sorted(unique.values(), key=lambda item: item.as_posix()), False


def validate_machine_event_files(paths: Iterable[str | Path]) -> ValidationReport:
    """Validate bounded CSV fixtures in deterministic path and row order."""
    collector = _FindingCollector()
    fixtures, too_many = _collect_paths(paths)
    if too_many:
        collector.add(FILE_COUNT_LIMIT_EXCEEDED, "<input>")
        return collector.report()
    if not fixtures:
        collector.add(NO_FIXTURES, "<input>")
        return collector.report()

    total_bytes = 0
    total_rows = 0
    seen: dict[str, tuple[tuple[str, ...], str, int]] = {}
    for path in fixtures:
        content, stop = _read_fixture(path, MAX_TOTAL_INPUT_BYTES - total_bytes, collector)
        if stop or collector.truncated:
            break
        if content is None:
            continue
        total_bytes += len(content)
        total_rows, stop = _validate_content(
            content, _bounded_source(path), collector, seen, total_rows
        )
        if stop or collector.truncated:
            break
    return collector.report()
