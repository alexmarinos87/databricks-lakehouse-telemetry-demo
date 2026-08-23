"""Pure-Python characterization checks for the dimensional warehouse contract.

The checks in this module operate on already-materialized row mappings. They are
small enough to run in local unit tests and intentionally do not import Spark.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeAlias


Row: TypeAlias = Mapping[str, object]
KeyValues: TypeAlias = tuple[tuple[str, object], ...]

UPTIME_FACT = "fact_machine_uptime_daily"
FAILURE_FACT = "fact_machine_failure_event"

_UPTIME_GRAIN = ("date_key", "machine_key")
_FAILURE_GRAIN = ("event_id",)
_MACHINE_ASSIGNMENT = ("client_id", "site_id", "model")
_MACHINE_ASSIGNMENT_GRAIN = ("machine_id", "event_date")
_UPTIME_DIMENSION_KEYS = (
    "date_key",
    "client_key",
    "machine_key",
    "model_key",
    "site_key",
)
_FAILURE_DIMENSION_KEYS = _UPTIME_DIMENSION_KEYS + ("fault_key",)
_CHECK_ORDER = {
    "machine_assignment_conflict": 10,
    "missing_grain_key": 20,
    "null_grain_key": 30,
    "duplicate_uptime_grain": 40,
    "duplicate_failure_grain": 50,
    "missing_dimension_key": 60,
    "null_dimension_key": 70,
    "unmatched_dimension_key": 80,
    "source_fact_count_mismatch": 90,
}
_DATASET_ORDER = {
    "machine_assignment": 10,
    UPTIME_FACT: 20,
    FAILURE_FACT: 30,
}


@dataclass(frozen=True)
class WarehouseContractFinding:
    """A deterministic, structured warehouse contract violation."""

    code: str
    dataset: str
    keys: KeyValues = ()
    details: KeyValues = ()


def _stable_value(value: object) -> str:
    """Return a comparison token for scalar warehouse keys."""
    return f"{type(value).__module__}.{type(value).__qualname__}:{value!r}"


def _key_values(row: Row, columns: tuple[str, ...]) -> KeyValues:
    return tuple((column, row.get(column)) for column in columns)


def _finding_sort_key(finding: WarehouseContractFinding) -> tuple[object, ...]:
    return (
        _CHECK_ORDER[finding.code],
        _DATASET_ORDER[finding.dataset],
        tuple((name, _stable_value(value)) for name, value in finding.keys),
        tuple((name, _stable_value(value)) for name, value in finding.details),
    )


def _assignment_conflicts(rows: tuple[Row, ...]) -> list[WarehouseContractFinding]:
    groups: dict[
        tuple[str, str],
        tuple[KeyValues, dict[tuple[str, ...], KeyValues]],
    ] = {}
    for row in rows:
        assignment_grain = _key_values(row, _MACHINE_ASSIGNMENT_GRAIN)
        grain_token = tuple(_stable_value(value) for _, value in assignment_grain)
        _, assignments = groups.setdefault(grain_token, (assignment_grain, {}))
        assignment = _key_values(row, _MACHINE_ASSIGNMENT)
        assignment_token = tuple(_stable_value(value) for _, value in assignment)
        assignments.setdefault(assignment_token, assignment)

    findings: list[WarehouseContractFinding] = []
    for assignment_grain, assignments_by_token in groups.values():
        assignments = tuple(assignments_by_token.values())
        if len(assignments) < 2:
            continue
        ordered_assignments = tuple(
            sorted(
                assignments,
                key=lambda assignment: tuple(
                    (name, _stable_value(value)) for name, value in assignment
                ),
            )
        )
        findings.append(
            WarehouseContractFinding(
                code="machine_assignment_conflict",
                dataset="machine_assignment",
                keys=assignment_grain,
                details=(("assignments", ordered_assignments),),
            )
        )
    return findings


def _grain_findings(
    rows: tuple[Row, ...],
    *,
    dataset: str,
    grain: tuple[str, ...],
) -> list[WarehouseContractFinding]:
    findings: list[WarehouseContractFinding] = []
    for row in rows:
        locator = _key_values(row, grain)
        for column in grain:
            if column not in row:
                code = "missing_grain_key"
            elif row[column] is None:
                code = "null_grain_key"
            else:
                continue
            findings.append(
                WarehouseContractFinding(
                    code=code,
                    dataset=dataset,
                    keys=locator + (("grain_column", column),),
                )
            )
    return findings


def _duplicate_grain_findings(
    rows: tuple[Row, ...],
    *,
    dataset: str,
    grain: tuple[str, ...],
    code: str,
) -> list[WarehouseContractFinding]:
    groups: dict[tuple[str, ...], tuple[KeyValues, int]] = {}
    for row in rows:
        if any(column not in row or row[column] is None for column in grain):
            continue
        keys = _key_values(row, grain)
        token = tuple(_stable_value(value) for _, value in keys)
        existing_keys, count = groups.get(token, (keys, 0))
        groups[token] = (existing_keys, count + 1)

    return [
        WarehouseContractFinding(
            code=code,
            dataset=dataset,
            keys=keys,
            details=(("row_count", count),),
        )
        for keys, count in groups.values()
        if count > 1
    ]


def _dimension_findings(
    rows: tuple[Row, ...],
    *,
    dataset: str,
    grain: tuple[str, ...],
    required_keys: tuple[str, ...],
    dimension_members: Mapping[str, frozenset[str]],
) -> list[WarehouseContractFinding]:
    grouped: dict[
        tuple[str, tuple[tuple[str, str], ...]],
        tuple[str, KeyValues, int],
    ] = {}

    for row in rows:
        locator = _key_values(row, grain)
        for dimension_key in required_keys:
            value = row.get(dimension_key)
            if dimension_key not in row:
                code = "missing_dimension_key"
            elif value is None:
                code = "null_dimension_key"
            elif _stable_value(value) not in dimension_members.get(
                dimension_key, frozenset()
            ):
                code = "unmatched_dimension_key"
            else:
                continue

            keys = locator + ((dimension_key, value),)
            token = (
                code,
                tuple((name, _stable_value(key_value)) for name, key_value in keys),
            )
            _, existing_keys, count = grouped.get(token, (code, keys, 0))
            grouped[token] = (code, existing_keys, count + 1)

    return [
        WarehouseContractFinding(
            code=code,
            dataset=dataset,
            keys=keys,
            details=(("row_count", count),),
        )
        for code, keys, count in grouped.values()
    ]


def _row_count_mismatch_finding(
    source_rows: tuple[Row, ...],
    fact_rows: tuple[Row, ...],
    *,
    dataset: str,
) -> list[WarehouseContractFinding]:
    difference = len(fact_rows) - len(source_rows)
    if difference == 0:
        return []
    return [
        WarehouseContractFinding(
            code="source_fact_count_mismatch",
            dataset=dataset,
            details=(
                ("source_row_count", len(source_rows)),
                ("fact_row_count", len(fact_rows)),
                ("net_missing_row_count", max(-difference, 0)),
                ("net_unexpected_row_count", max(difference, 0)),
            ),
        )
    ]


def evaluate_warehouse_contracts(
    *,
    uptime_source_rows: Iterable[Row],
    failure_source_rows: Iterable[Row],
    uptime_fact_rows: Iterable[Row],
    failure_fact_rows: Iterable[Row],
    dimension_members: Mapping[str, Iterable[object]],
) -> tuple[WarehouseContractFinding, ...]:
    """Evaluate warehouse row contracts and return deterministically ordered findings.

    ``dimension_members`` maps fact foreign-key column names (for example,
    ``site_key``) to the keys present in the corresponding dimensions. An
    omitted member collection is treated as empty, so references fail closed.
    Assignment conflicts are evaluated at machine-and-date grain; a change on a
    later date is a versioned assignment rather than a conflict.
    """
    uptime_sources = tuple(uptime_source_rows)
    failure_sources = tuple(failure_source_rows)
    uptime_facts = tuple(uptime_fact_rows)
    failure_facts = tuple(failure_fact_rows)
    members = {
        key: frozenset(_stable_value(value) for value in values)
        for key, values in dimension_members.items()
    }

    findings: list[WarehouseContractFinding] = []
    findings.extend(_assignment_conflicts(uptime_sources + failure_sources))
    findings.extend(
        _grain_findings(
            uptime_facts,
            dataset=UPTIME_FACT,
            grain=_UPTIME_GRAIN,
        )
    )
    findings.extend(
        _grain_findings(
            failure_facts,
            dataset=FAILURE_FACT,
            grain=_FAILURE_GRAIN,
        )
    )
    findings.extend(
        _duplicate_grain_findings(
            uptime_facts,
            dataset=UPTIME_FACT,
            grain=_UPTIME_GRAIN,
            code="duplicate_uptime_grain",
        )
    )
    findings.extend(
        _duplicate_grain_findings(
            failure_facts,
            dataset=FAILURE_FACT,
            grain=_FAILURE_GRAIN,
            code="duplicate_failure_grain",
        )
    )
    findings.extend(
        _dimension_findings(
            uptime_facts,
            dataset=UPTIME_FACT,
            grain=_UPTIME_GRAIN,
            required_keys=_UPTIME_DIMENSION_KEYS,
            dimension_members=members,
        )
    )
    findings.extend(
        _dimension_findings(
            failure_facts,
            dataset=FAILURE_FACT,
            grain=_FAILURE_GRAIN,
            required_keys=_FAILURE_DIMENSION_KEYS,
            dimension_members=members,
        )
    )
    findings.extend(
        _row_count_mismatch_finding(uptime_sources, uptime_facts, dataset=UPTIME_FACT)
    )
    findings.extend(
        _row_count_mismatch_finding(failure_sources, failure_facts, dataset=FAILURE_FACT)
    )
    return tuple(sorted(findings, key=_finding_sort_key))
