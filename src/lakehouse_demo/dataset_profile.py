"""Build a deterministic, source-only profile of machine-event fixtures."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from lakehouse_demo.azure_ingestion import MACHINE_EVENT_COLUMNS
from lakehouse_demo.machine_event_contract import validate_machine_event_files
from lakehouse_demo.repository_files import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    RepositoryFileError,
    RepositoryFileSnapshot,
    normalize_repository_root,
    read_repository_files,
    verify_repository_files_unchanged,
    write_new_text_package,
)


PROFILE_SCHEMA_VERSION = 1
PROFILE_KIND = "synthetic_machine_event_profile"
PROFILE_JSON = "dataset-profile.json"
PROFILE_MARKDOWN = "dataset-profile.md"
DEFAULT_SAMPLE = "data/sample_machine_events.csv"
DEFAULT_INCREMENT_GLOB = "data/increments/*.csv"


class DatasetProfileError(RuntimeError):
    """A sanitized profile construction failure."""

    def __init__(self, category: str, details: Iterable[str] = ()) -> None:
        self.category = category
        self.details = tuple(sorted(set(details)))
        suffix = f": {','.join(self.details)}" if self.details else ""
        super().__init__(f"{category}{suffix}")


def default_machine_event_sources(repository_root: str | Path) -> tuple[str, ...]:
    """Return the committed sample followed by deterministic increment paths."""
    try:
        root = normalize_repository_root(repository_root)
    except RepositoryFileError as exc:
        raise DatasetProfileError(exc.category) from exc
    sources = [DEFAULT_SAMPLE]
    sources.extend(
        path.relative_to(root).as_posix()
        for path in sorted(
            root.glob(DEFAULT_INCREMENT_GLOB), key=lambda item: item.as_posix()
        )
    )
    return tuple(sources)


def _validated_snapshots(
    repository_root: str | Path,
    sources: Iterable[str | Path],
) -> tuple[RepositoryFileSnapshot, ...]:
    try:
        root = normalize_repository_root(repository_root)
        snapshots = read_repository_files(
            root,
            sources,
            max_file_bytes=DEFAULT_MAX_FILE_BYTES,
            max_total_bytes=DEFAULT_MAX_TOTAL_BYTES,
        )
    except RepositoryFileError as exc:
        raise DatasetProfileError(exc.category) from exc

    report = validate_machine_event_files(root / item.relative_path for item in snapshots)
    if report.error_findings:
        raise DatasetProfileError(
            "machine_event_validation_failed",
            (item.code for item in report.error_findings),
        )
    try:
        verify_repository_files_unchanged(
            root,
            snapshots,
            max_file_bytes=DEFAULT_MAX_FILE_BYTES,
            max_total_bytes=DEFAULT_MAX_TOTAL_BYTES,
        )
    except RepositoryFileError as exc:
        raise DatasetProfileError(exc.category) from exc
    return snapshots


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _parse_snapshot_rows(
    snapshot: RepositoryFileSnapshot,
) -> tuple[list[dict[str, str]], int]:
    try:
        text = snapshot.content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if tuple(reader.fieldnames or ()) != MACHINE_EVENT_COLUMNS:
            raise DatasetProfileError("machine_event_profile_header_mismatch")
        rows = list(reader)
    except UnicodeError as exc:
        raise DatasetProfileError("machine_event_profile_decode_failed") from exc
    except csv.Error as exc:
        raise DatasetProfileError("machine_event_profile_parse_failed") from exc
    return rows, len(rows)


def profile_machine_event_files(
    repository_root: str | Path,
    sources: Iterable[str | Path],
) -> dict[str, object]:
    """Return a deterministic aggregate profile without retaining raw rows."""
    snapshots = _validated_snapshots(repository_root, sources)

    physical_rows = 0
    event_ids: set[str] = set()
    machines: set[str] = set()
    clients: set[str] = set()
    sites: set[str] = set()
    models: set[str] = set()
    timestamps: list[str] = []
    statuses: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    fault_codes: set[str] = set()
    part_codes: set[str] = set()
    duration_total = 0
    downtime_total = 0
    part_quantity_total = 0
    maintenance_cost_total = Decimal("0")
    fault_rows = 0
    maintenance_rows = 0
    input_files: list[dict[str, object]] = []

    seen_fingerprints: dict[str, tuple[str, ...]] = {}
    replay_duplicate_rows = 0
    for snapshot in snapshots:
        rows, file_row_count = _parse_snapshot_rows(snapshot)
        input_files.append(
            {
                "path": snapshot.relative_path,
                "sha256": snapshot.sha256,
                "size_bytes": snapshot.size_bytes,
                "physical_row_count": file_row_count,
            }
        )
        for row in rows:
            physical_rows += 1
            event_id = row["event_id"]
            fingerprint = tuple(row[column] for column in MACHINE_EVENT_COLUMNS)
            first = seen_fingerprints.get(event_id)
            if first is None:
                seen_fingerprints[event_id] = fingerprint
            elif first == fingerprint:
                replay_duplicate_rows += 1
            else:  # The source validator should make this state unreachable.
                raise DatasetProfileError("machine_event_profile_conflicting_duplicate")

            if first is not None:
                continue

            event_ids.add(event_id)
            machines.add(row["machine_id"])
            clients.add(row["client_id"])
            sites.add(row["site_id"])
            models.add(row["model"])
            timestamps.append(row["event_ts"])
            statuses[row["status"]] += 1
            event_types[row["event_type"]] += 1
            duration_total += int(row["duration_minutes"])
            downtime_total += int(row["downtime_minutes"])
            part_quantity_total += int(row["part_quantity"])
            maintenance_cost_total += Decimal(row["maintenance_cost_gbp"])

            if row["status"] == "FAULT":
                fault_rows += 1
            if row["status"] == "MAINTENANCE" or row["event_type"] == "maintenance":
                maintenance_rows += 1
            if row["fault_code"] not in {"", "OK", "NONE"}:
                fault_codes.add(row["fault_code"])
            if row["part_code"] not in {"", "NONE"}:
                part_codes.add(row["part_code"])

    if not physical_rows:
        raise DatasetProfileError("machine_event_profile_rows_missing")

    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_kind": PROFILE_KIND,
        "evidence_boundary": "repository_source_only",
        "input": {
            "file_count": len(input_files),
            "files": input_files,
        },
        "rows": {
            "physical_row_count": physical_rows,
            "unique_event_id_count": len(event_ids),
            "replay_duplicate_row_count": replay_duplicate_rows,
            "conflicting_duplicate_row_count": 0,
        },
        "coverage": {
            "observation_start": min(timestamps),
            "observation_end": max(timestamps),
            "machine_count": len(machines),
            "client_count": len(clients),
            "site_count": len(sites),
            "model_count": len(models),
        },
        "operations": {
            "aggregate_grain": "unique_event_id_first_observation",
            "status_counts": dict(sorted(statuses.items())),
            "event_type_counts": dict(sorted(event_types.items())),
            "fault_event_row_count": fault_rows,
            "maintenance_event_row_count": maintenance_rows,
            "distinct_fault_code_count": len(fault_codes),
            "distinct_part_code_count": len(part_codes),
            "duration_minutes_total": duration_total,
            "downtime_minutes_total": downtime_total,
            "maintenance_cost_gbp_total": _decimal_text(maintenance_cost_total),
            "part_quantity_total": part_quantity_total,
        },
    }


def render_dataset_profile_markdown(profile: dict[str, object]) -> str:
    """Render the machine-readable profile as a concise review summary."""
    rows = profile["rows"]
    coverage = profile["coverage"]
    operations = profile["operations"]
    inputs = profile["input"]
    assert isinstance(rows, dict)
    assert isinstance(coverage, dict)
    assert isinstance(operations, dict)
    assert isinstance(inputs, dict)

    lines = [
        "# Synthetic machine-event dataset profile",
        "",
        (
            "This report is deterministic repository-source evidence. It does not prove "
            "Databricks execution, live client data, branch protection, or production "
            "operation."
        ),
        "",
        "## Coverage",
        "",
        "| Measure | Value |",
        "| --- | ---: |",
        f"| Source files | {inputs['file_count']} |",
        f"| Physical rows | {rows['physical_row_count']} |",
        f"| Unique event IDs | {rows['unique_event_id_count']} |",
        f"| Identical replay rows | {rows['replay_duplicate_row_count']} |",
        f"| Machines | {coverage['machine_count']} |",
        f"| Clients | {coverage['client_count']} |",
        f"| Sites | {coverage['site_count']} |",
        f"| Models | {coverage['model_count']} |",
        f"| Observation start | `{coverage['observation_start']}` |",
        f"| Observation end | `{coverage['observation_end']}` |",
        "",
        "## Operational evidence",
        "",
        (
            "Operational measures use the first validated observation for each unique "
            "`event_id`; identical replay rows are reported separately and do not inflate "
            "these aggregates."
        ),
        "",
        "| Measure | Value |",
        "| --- | ---: |",
        f"| Fault rows | {operations['fault_event_row_count']} |",
        f"| Maintenance rows | {operations['maintenance_event_row_count']} |",
        f"| Distinct fault codes | {operations['distinct_fault_code_count']} |",
        f"| Distinct recorded parts | {operations['distinct_part_code_count']} |",
        f"| Duration minutes | {operations['duration_minutes_total']} |",
        f"| Attributed downtime minutes | {operations['downtime_minutes_total']} |",
        f"| Recorded maintenance cost (GBP) | {operations['maintenance_cost_gbp_total']} |",
        "",
        "## Input provenance",
        "",
    ]
    files = inputs["files"]
    assert isinstance(files, list)
    for item in files:
        assert isinstance(item, dict)
        lines.append(
            f"- `{item['path']}` — {item['physical_row_count']} rows, "
            f"SHA-256 `{item['sha256']}`"
        )
    return "\n".join(lines) + "\n"


def write_dataset_profile_package(
    profile: dict[str, object],
    output_dir: str | Path,
) -> Path:
    """Persist a new JSON and Markdown profile package without overwriting."""
    json_text = json.dumps(profile, indent=2, sort_keys=True) + "\n"
    markdown = render_dataset_profile_markdown(profile)
    try:
        return write_new_text_package(
            output_dir,
            {
                PROFILE_JSON: json_text,
                PROFILE_MARKDOWN: markdown,
            },
        )
    except RepositoryFileError as exc:
        raise DatasetProfileError(exc.category) from exc
