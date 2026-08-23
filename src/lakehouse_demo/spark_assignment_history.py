"""Effective-dated machine assignment and fail-closed resolution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F


SEMANTIC_VERSION = "effective_dated_assignment_v1"
ASSIGNMENT_ATTRIBUTES = ("client_id", "site_id", "model")
SOURCE_COLUMNS = ("event_date", "machine_id", *ASSIGNMENT_ATTRIBUTES)
HISTORY_COLUMNS = (
    "machine_id",
    *ASSIGNMENT_ATTRIBUTES,
    "effective_from",
    "effective_to",
    "is_current",
    "assignment_evidence_source_count",
    "assignment_semantics_version",
)


@dataclass(frozen=True, order=True)
class AssignmentFinding:
    code: str
    observed_count: int
    detail: str


@dataclass(frozen=True)
class AssignmentHistoryResult:
    history: DataFrame
    rejected_evidence: DataFrame
    findings: tuple[AssignmentFinding, ...]

    @property
    def can_publish(self) -> bool:
        return not self.findings


@dataclass(frozen=True)
class AssignmentResolutionResult:
    resolved: DataFrame
    unresolved: DataFrame
    ambiguous: DataFrame
    findings: tuple[AssignmentFinding, ...]

    @property
    def can_publish(self) -> bool:
        return not self.findings


def _missing_columns(dataframe: DataFrame, columns: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(columns).difference(dataframe.columns)))


def _normalise_source(dataframe: DataFrame, *, source_name: str) -> DataFrame:
    missing = _missing_columns(dataframe, SOURCE_COLUMNS)
    if missing:
        raise ValueError(
            f"assignment source {source_name} is missing columns: {', '.join(missing)}"
        )
    return dataframe.select(
        F.to_date("event_date").alias("event_date"),
        F.trim(F.col("machine_id").cast("string")).alias("machine_id"),
        *[
            F.trim(F.col(column).cast("string")).alias(column)
            for column in ASSIGNMENT_ATTRIBUTES
        ],
        F.lit(source_name).alias("assignment_evidence_source"),
    )


def build_assignment_history(
    sources: Mapping[str, DataFrame],
) -> AssignmentHistoryResult:
    """Build non-overlapping periods without choosing a conflict winner.

    Evidence from all named sources has equal authority. A machine with more than
    one distinct assignment on the same date is excluded from trusted history and
    retained in ``rejected_evidence``. Callers must block publication when any
    finding is returned.
    """

    if not sources:
        raise ValueError("at least one assignment evidence source is required")
    if len(sources) > 20:
        raise ValueError("assignment evidence source count exceeds the bounded limit")
    normalised: list[DataFrame] = []
    for name in sorted(sources):
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ValueError("assignment evidence source name is invalid")
        normalised.append(_normalise_source(sources[name], source_name=name.strip()))

    evidence = normalised[0]
    for source in normalised[1:]:
        evidence = evidence.unionByName(source)

    findings: list[AssignmentFinding] = []
    blank_predicate = F.col("event_date").isNull() | F.col("machine_id").isNull() | (
        F.length(F.col("machine_id")) == 0
    )
    for column in ASSIGNMENT_ATTRIBUTES:
        blank_predicate = blank_predicate | F.col(column).isNull() | (
            F.length(F.col(column)) == 0
        )
    invalid = evidence.where(blank_predicate)
    invalid_count = int(invalid.count())
    if invalid_count:
        findings.append(
            AssignmentFinding(
                code="missing_assignment_identity",
                observed_count=invalid_count,
                detail="Assignment evidence contains a missing date or identity",
            )
        )

    valid = evidence.where(~blank_predicate)
    daily = valid.select(
        "event_date",
        "machine_id",
        *ASSIGNMENT_ATTRIBUTES,
        "assignment_evidence_source",
    ).distinct()
    assignment_struct = F.struct(
        *[F.col(column) for column in ASSIGNMENT_ATTRIBUTES]
    )
    conflicts = (
        daily.groupBy("machine_id", "event_date")
        .agg(F.countDistinct(assignment_struct).alias("assignment_count"))
        .where(F.col("assignment_count") > 1)
    )
    conflict_count = int(conflicts.count())
    if conflict_count:
        findings.append(
            AssignmentFinding(
                code="same_day_assignment_conflict",
                observed_count=conflict_count,
                detail="A machine has multiple assignments on the same date",
            )
        )

    conflict_machines = conflicts.select("machine_id").distinct()
    rejected_conflicts = daily.join(
        conflicts.select("machine_id", "event_date"),
        ["machine_id", "event_date"],
        "inner",
    )
    rejected_evidence = invalid.unionByName(
        rejected_conflicts.select(invalid.columns), allowMissingColumns=False
    ).distinct()

    trusted_daily = (
        daily.join(conflict_machines, "machine_id", "left_anti")
        .groupBy("machine_id", "event_date", *ASSIGNMENT_ATTRIBUTES)
        .agg(
            F.countDistinct("assignment_evidence_source").alias(
                "daily_evidence_source_count"
            )
        )
    )

    ordered = Window.partitionBy("machine_id").orderBy("event_date")
    changed = F.lit(False)
    for column in ASSIGNMENT_ATTRIBUTES:
        changed = changed | ~F.col(column).eqNullSafe(F.lag(column).over(ordered))
    grouped = (
        trusted_daily.withColumn(
            "_assignment_changed",
            F.when(F.lag("event_date").over(ordered).isNull(), F.lit(1))
            .when(changed, F.lit(1))
            .otherwise(F.lit(0)),
        )
        .withColumn(
            "_assignment_group",
            F.sum("_assignment_changed").over(
                ordered.rowsBetween(Window.unboundedPreceding, Window.currentRow)
            ),
        )
    )
    periods = (
        grouped.groupBy("machine_id", "_assignment_group", *ASSIGNMENT_ATTRIBUTES)
        .agg(
            F.min("event_date").alias("effective_from"),
            F.sum("daily_evidence_source_count").cast("long").alias(
                "assignment_evidence_source_count"
            ),
        )
    )
    period_order = Window.partitionBy("machine_id").orderBy("effective_from")
    history = (
        periods.withColumn(
            "effective_to",
            F.date_sub(F.lead("effective_from").over(period_order), 1),
        )
        .withColumn("is_current", F.col("effective_to").isNull())
        .withColumn("assignment_semantics_version", F.lit(SEMANTIC_VERSION))
        .select(*HISTORY_COLUMNS)
    )

    findings.extend(audit_assignment_history(history))
    return AssignmentHistoryResult(
        history=history,
        rejected_evidence=rejected_evidence,
        findings=tuple(sorted(set(findings))),
    )


def audit_assignment_history(history: DataFrame) -> tuple[AssignmentFinding, ...]:
    missing = _missing_columns(history, HISTORY_COLUMNS)
    if missing:
        return (
            AssignmentFinding(
                code="missing_assignment_history_columns",
                observed_count=len(missing),
                detail="Assignment history is missing required columns",
            ),
        )

    findings: list[AssignmentFinding] = []
    duplicate_count = int(
        history.groupBy("machine_id", "effective_from")
        .count()
        .where(F.col("count") > 1)
        .count()
    )
    if duplicate_count:
        findings.append(
            AssignmentFinding(
                code="assignment_history_grain_duplicate",
                observed_count=duplicate_count,
                detail="Assignment history contains duplicate effective starts",
            )
        )

    blank_predicate = F.col("machine_id").isNull() | (
        F.length(F.trim(F.col("machine_id"))) == 0
    )
    for column in ASSIGNMENT_ATTRIBUTES:
        blank_predicate = blank_predicate | F.col(column).isNull() | (
            F.length(F.trim(F.col(column))) == 0
        )
    blank_count = int(
        history.where(blank_predicate | F.col("effective_from").isNull()).count()
    )
    if blank_count:
        findings.append(
            AssignmentFinding(
                code="assignment_history_identity_missing",
                observed_count=blank_count,
                detail="Assignment history contains missing identity values",
            )
        )

    ordered = Window.partitionBy("machine_id").orderBy("effective_from")
    positioned = (
        history.withColumn("_previous_effective_to", F.lag("effective_to").over(ordered))
        .withColumn("_assignment_position", F.row_number().over(ordered))
    )
    overlap_count = int(
        positioned.where(
            (F.col("_assignment_position") > 1)
            & (
                F.col("_previous_effective_to").isNull()
                | (
                    F.col("effective_from")
                    <= F.col("_previous_effective_to")
                )
            )
        ).count()
    )
    if overlap_count:
        findings.append(
            AssignmentFinding(
                code="assignment_history_range_overlap",
                observed_count=overlap_count,
                detail="Assignment history contains overlapping effective ranges",
            )
        )

    current_duplicates = int(
        history.where(F.col("is_current"))
        .groupBy("machine_id")
        .count()
        .where(F.col("count") > 1)
        .count()
    )
    machines_without_current = int(
        history.select("machine_id")
        .distinct()
        .join(
            history.where(F.col("is_current")).select("machine_id").distinct(),
            "machine_id",
            "left_anti",
        )
        .count()
    )
    current_problem_count = current_duplicates + machines_without_current
    if current_problem_count:
        findings.append(
            AssignmentFinding(
                code="assignment_history_current_member_invalid",
                observed_count=current_problem_count,
                detail="A machine does not have exactly one current assignment",
            )
        )

    invalid_range_count = int(
        history.where(
            F.col("effective_to").isNotNull()
            & (F.col("effective_to") < F.col("effective_from"))
        ).count()
    )
    if invalid_range_count:
        findings.append(
            AssignmentFinding(
                code="assignment_history_range_invalid",
                observed_count=invalid_range_count,
                detail="Assignment effective range ends before it begins",
            )
        )
    return tuple(sorted(findings))


def resolve_assignment_as_of(
    events: DataFrame,
    history: DataFrame,
    *,
    event_date_column: str = "event_date",
) -> AssignmentResolutionResult:
    """Resolve events without dropping or assigning an Unknown surrogate member."""

    event_missing = _missing_columns(events, ("machine_id", event_date_column))
    history_missing = _missing_columns(
        history,
        ("machine_id", *ASSIGNMENT_ATTRIBUTES, "effective_from", "effective_to"),
    )
    if event_missing:
        raise ValueError(
            "assignment resolution input is missing columns: "
            + ", ".join(event_missing)
        )
    if history_missing:
        raise ValueError(
            "assignment history is missing columns: " + ", ".join(history_missing)
        )

    event_columns = tuple(events.columns)
    prepared = events.withColumn(
        "_assignment_event_row_id", F.monotonically_increasing_id()
    )
    joined = prepared.alias("event").join(
        history.alias("assignment"),
        (F.col("event.machine_id") == F.col("assignment.machine_id"))
        & (
            F.to_date(F.col(f"event.{event_date_column}"))
            >= F.col("assignment.effective_from")
        )
        & (
            F.col("assignment.effective_to").isNull()
            | (
                F.to_date(F.col(f"event.{event_date_column}"))
                <= F.col("assignment.effective_to")
            )
        ),
        "left",
    )
    row_window = Window.partitionBy(F.col("event._assignment_event_row_id"))
    matched = joined.withColumn(
        "_assignment_match_count",
        F.sum(
            F.when(F.col("assignment.machine_id").isNotNull(), F.lit(1)).otherwise(
                F.lit(0)
            )
        ).over(row_window),
    )
    selected = matched.select(
        *[F.col(f"event.{column}").alias(column) for column in event_columns],
        F.col("event._assignment_event_row_id").alias("_assignment_event_row_id"),
        *[
            F.col(f"assignment.{column}").alias(f"resolved_{column}")
            for column in ASSIGNMENT_ATTRIBUTES
        ],
        F.col("assignment.effective_from").alias("assignment_effective_from"),
        F.col("assignment.effective_to").alias("assignment_effective_to"),
        "_assignment_match_count",
    )

    resolved_rows = selected.where(F.col("_assignment_match_count") == 1)
    unresolved_rows = selected.where(F.col("_assignment_match_count") == 0)
    ambiguous_rows = selected.where(F.col("_assignment_match_count") > 1)
    resolved_event_count = int(
        resolved_rows.select("_assignment_event_row_id").distinct().count()
    )
    unresolved_event_count = int(
        unresolved_rows.select("_assignment_event_row_id").distinct().count()
    )
    ambiguous_event_count = int(
        ambiguous_rows.select("_assignment_event_row_id").distinct().count()
    )

    resolved = resolved_rows.drop(
        "_assignment_match_count", "_assignment_event_row_id"
    )
    unresolved = unresolved_rows.drop(
        "_assignment_match_count", "_assignment_event_row_id"
    )
    ambiguous = ambiguous_rows.drop(
        "_assignment_match_count", "_assignment_event_row_id"
    )

    findings: list[AssignmentFinding] = []
    if unresolved_event_count:
        findings.append(
            AssignmentFinding(
                code="assignment_unresolved",
                observed_count=unresolved_event_count,
                detail="Events do not resolve to an effective assignment",
            )
        )
    if ambiguous_event_count:
        findings.append(
            AssignmentFinding(
                code="assignment_ambiguous",
                observed_count=ambiguous_event_count,
                detail="Events resolve to multiple effective assignments",
            )
        )
    input_count = int(events.count())
    partitioned_count = (
        resolved_event_count + unresolved_event_count + ambiguous_event_count
    )
    if partitioned_count != input_count:
        findings.append(
            AssignmentFinding(
                code="assignment_resolution_count_mismatch",
                observed_count=abs(partitioned_count - input_count),
                detail="Assignment resolution did not preserve event count",
            )
        )
    return AssignmentResolutionResult(
        resolved=resolved,
        unresolved=unresolved,
        ambiguous=ambiguous,
        findings=tuple(sorted(findings)),
    )
