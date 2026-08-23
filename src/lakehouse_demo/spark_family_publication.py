"""Generic manifest-last evidence for versioned dataset-family publication.

The helpers operate on Spark DataFrames only. Databricks notebooks own Delta
writes and view metadata, while local Spark tests execute state selection,
retry, fingerprint, and partial-publication behaviour.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from lakehouse_demo.spark_forecast_publication import dataset_evidence


STATE_STARTED = "STARTED"
STATE_COMMITTED = "COMMITTED"
STATE_FAILED = "FAILED"
KNOWN_PUBLICATION_STATES = (STATE_STARTED, STATE_COMMITTED, STATE_FAILED)

_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_FAILURE_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_UTC_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

_MANIFEST_SCHEMA = StructType(
    [
        StructField("publication_family", StringType(), False),
        StructField("publication_run_id", StringType(), False),
        StructField("publication_state", StringType(), False),
        StructField("publication_started_at_utc", StringType(), False),
        StructField("publication_completed_at_utc", StringType(), True),
        StructField("failure_code", StringType(), True),
        StructField("dataset_names_json", StringType(), False),
        StructField("dataset_evidence_json", StringType(), False),
    ]
)
_REQUIRED_MANIFEST_COLUMNS = {field.name for field in _MANIFEST_SCHEMA.fields}


@dataclass(frozen=True, order=True)
class FamilyPublicationFinding:
    """Bounded aggregate evidence that never contains business row values."""

    code: str
    dataset: str
    count: int


def validate_publication_name(value: str, *, label: str) -> str:
    candidate = (value or "").strip()
    if not _NAME_PATTERN.fullmatch(candidate):
        raise ValueError(f"{label} must use 1-64 lowercase identifier characters")
    return candidate


def validate_publication_run_id(value: str) -> str:
    candidate = (value or "").strip()
    if not _RUN_ID_PATTERN.fullmatch(candidate):
        raise ValueError(
            "publication_run_id must contain 1-128 safe alphanumeric identifier characters"
        )
    return candidate


def validate_utc_timestamp(value: str, *, label: str) -> str:
    candidate = (value or "").strip()
    if not _UTC_PATTERN.fullmatch(candidate):
        raise ValueError(f"{label} must use the UTC shape YYYY-MM-DDTHH:MM:SSZ")
    return candidate


def validate_run_id_column(value: str) -> str:
    candidate = (value or "").strip()
    if not _NAME_PATTERN.fullmatch(candidate):
        raise ValueError("run_id_column must be a safe lowercase identifier")
    return candidate


def with_publication_run_id(
    frames: Mapping[str, DataFrame],
    *,
    publication_run_id: str,
    run_id_column: str,
) -> dict[str, DataFrame]:
    """Add one stable run identity to every member of a dataset family."""

    run_id = validate_publication_run_id(publication_run_id)
    column_name = validate_run_id_column(run_id_column)
    if not frames:
        raise ValueError("publication family must contain at least one dataset")

    result: dict[str, DataFrame] = {}
    for raw_name, frame in frames.items():
        dataset_name = validate_publication_name(raw_name, label="dataset name")
        if dataset_name in result:
            raise ValueError("publication family contains a duplicate dataset name")
        if column_name in frame.columns:
            mismatches = frame.where(
                F.col(column_name).isNull()
                | (F.col(column_name) != F.lit(run_id))
            ).limit(1).count()
            if mismatches:
                raise ValueError("dataset already contains a different publication run ID")
            result[dataset_name] = frame
        else:
            result[dataset_name] = frame.withColumn(column_name, F.lit(run_id))
    return result


def _require_columns(frame: DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _dataset_evidence_payload(
    frames: Mapping[str, DataFrame],
    *,
    publication_run_id: str,
    run_id_column: str,
) -> tuple[str, str]:
    if not frames:
        raise ValueError("publication family must contain at least one dataset")

    dataset_names: list[str] = []
    evidence: dict[str, dict[str, object]] = {}
    spark_session = None
    for raw_name in sorted(frames):
        dataset_name = validate_publication_name(raw_name, label="dataset name")
        frame = frames[raw_name]
        spark_session = spark_session or frame.sparkSession
        _require_columns(frame, {run_id_column}, label=dataset_name)
        mismatches = frame.where(
            F.col(run_id_column).isNull()
            | (F.col(run_id_column) != F.lit(publication_run_id))
        ).limit(1).count()
        if mismatches:
            raise ValueError(f"{dataset_name} contains a different publication run ID")
        dataset_names.append(dataset_name)
        dataset_result = dataset_evidence(frame)
        evidence[dataset_name] = {
            "row_count": dataset_result.row_count,
            "columns_json": dataset_result.columns_json,
            "schema_sha256": dataset_result.schema_sha256,
            "payload_sha256": dataset_result.payload_sha256,
        }

    if spark_session is None:
        raise ValueError("publication family does not contain Spark DataFrames")
    return (
        json.dumps(dataset_names, separators=(",", ":")),
        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
    )


def build_family_manifest(
    frames: Mapping[str, DataFrame],
    *,
    publication_family: str,
    publication_run_id: str,
    run_id_column: str,
    publication_started_at_utc: str,
) -> DataFrame:
    """Build one STARTED manifest row from exact candidate DataFrames."""

    family = validate_publication_name(
        publication_family,
        label="publication_family",
    )
    run_id = validate_publication_run_id(publication_run_id)
    column_name = validate_run_id_column(run_id_column)
    started_at = validate_utc_timestamp(
        publication_started_at_utc,
        label="publication_started_at_utc",
    )
    dataset_names_json, evidence_json = _dataset_evidence_payload(
        frames,
        publication_run_id=run_id,
        run_id_column=column_name,
    )
    spark = next(iter(frames.values())).sparkSession
    return spark.createDataFrame(
        [
            (
                family,
                run_id,
                STATE_STARTED,
                started_at,
                None,
                None,
                dataset_names_json,
                evidence_json,
            )
        ],
        schema=_MANIFEST_SCHEMA,
    )


def transition_family_manifest(
    manifest: DataFrame,
    *,
    publication_state: str,
    publication_completed_at_utc: str | None = None,
    failure_code: str | None = None,
) -> DataFrame:
    """Return a terminal or STARTED representation without recalculating evidence."""

    _require_columns(manifest, _REQUIRED_MANIFEST_COLUMNS, label="manifest")
    if manifest.limit(2).count() != 1:
        raise ValueError("manifest transition requires exactly one row")
    if publication_state not in KNOWN_PUBLICATION_STATES:
        raise ValueError("publication_state is not recognised")

    if publication_state == STATE_STARTED:
        if publication_completed_at_utc is not None or failure_code is not None:
            raise ValueError("STARTED publication cannot contain terminal evidence")
        completed_at = None
        normalized_failure = None
    else:
        if publication_completed_at_utc is None:
            raise ValueError("terminal publication requires completion time")
        completed_at = validate_utc_timestamp(
            publication_completed_at_utc,
            label="publication_completed_at_utc",
        )
        if publication_state == STATE_FAILED:
            normalized_failure = (failure_code or "").strip()
            if not _FAILURE_CODE_PATTERN.fullmatch(normalized_failure):
                raise ValueError("FAILED publication requires a bounded failure_code")
        else:
            if failure_code is not None:
                raise ValueError("COMMITTED publication cannot contain failure_code")
            normalized_failure = None

    return (
        manifest.withColumn("publication_state", F.lit(publication_state))
        .withColumn(
            "publication_completed_at_utc",
            F.lit(completed_at).cast("string"),
        )
        .withColumn(
            "failure_code",
            F.lit(normalized_failure).cast("string"),
        )
    )


def _manifest_rows(
    manifest: DataFrame,
    *,
    publication_family: str,
    publication_run_id: str,
):
    _require_columns(manifest, _REQUIRED_MANIFEST_COLUMNS, label="manifest")
    return (
        manifest.where(
            (F.col("publication_family") == F.lit(publication_family))
            & (F.col("publication_run_id") == F.lit(publication_run_id))
        )
        .limit(2)
        .collect()
    )


def publication_state_for_run(
    manifest: DataFrame,
    *,
    publication_family: str,
    publication_run_id: str,
) -> str | None:
    family = validate_publication_name(
        publication_family,
        label="publication_family",
    )
    run_id = validate_publication_run_id(publication_run_id)
    rows = _manifest_rows(
        manifest,
        publication_family=family,
        publication_run_id=run_id,
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("manifest contains duplicate publication run rows")
    state = str(rows[0]["publication_state"])
    if state not in KNOWN_PUBLICATION_STATES:
        raise ValueError("manifest contains an unknown publication state")
    return state


def latest_committed_run_id(
    manifest: DataFrame,
    *,
    publication_family: str,
) -> str | None:
    family = validate_publication_name(
        publication_family,
        label="publication_family",
    )
    _require_columns(manifest, _REQUIRED_MANIFEST_COLUMNS, label="manifest")
    duplicate_runs = (
        manifest.where(F.col("publication_family") == F.lit(family))
        .groupBy("publication_run_id")
        .count()
        .where(F.col("count") > 1)
        .limit(1)
        .count()
    )
    if duplicate_runs:
        raise ValueError("manifest contains duplicate publication run rows")

    rows = (
        manifest.where(
            (F.col("publication_family") == F.lit(family))
            & (F.col("publication_state") == F.lit(STATE_COMMITTED))
            & F.col("publication_completed_at_utc").isNotNull()
        )
        .orderBy(
            F.col("publication_completed_at_utc").desc(),
            F.col("publication_run_id").desc(),
        )
        .select("publication_run_id")
        .limit(1)
        .collect()
    )
    return None if not rows else str(rows[0]["publication_run_id"])


def _parse_dataset_names(raw_value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("manifest dataset names are invalid JSON") from None
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(value, str) for value in parsed)
    ):
        raise ValueError("manifest dataset names are invalid")
    normalized = tuple(
        validate_publication_name(value, label="dataset name") for value in parsed
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError("manifest dataset names contain duplicates")
    return normalized


def _parse_evidence(raw_value: str) -> dict[str, dict[str, object]]:
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("manifest dataset evidence is invalid JSON") from None
    if not isinstance(parsed, dict):
        raise ValueError("manifest dataset evidence is invalid")
    result: dict[str, dict[str, object]] = {}
    for raw_name, raw_evidence in parsed.items():
        dataset_name = validate_publication_name(raw_name, label="dataset name")
        if not isinstance(raw_evidence, dict) or set(raw_evidence) != {
            "row_count",
            "columns_json",
            "schema_sha256",
            "payload_sha256",
        }:
            raise ValueError("manifest dataset evidence has an invalid shape")
        if (
            not isinstance(raw_evidence["row_count"], int)
            or isinstance(raw_evidence["row_count"], bool)
            or raw_evidence["row_count"] < 0
            or any(
                not isinstance(raw_evidence[key], str) or not raw_evidence[key]
                for key in ("columns_json", "schema_sha256", "payload_sha256")
            )
        ):
            raise ValueError("manifest dataset evidence contains invalid values")
        result[dataset_name] = raw_evidence
    return result


def _columns_from_json(raw_value: str, *, dataset: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        raise ValueError(f"{dataset} manifest columns are invalid JSON") from None
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(value, str) or not value for value in parsed)
        or len(parsed) != len(set(parsed))
    ):
        raise ValueError(f"{dataset} manifest columns are invalid")
    return tuple(parsed)


def audit_family_publication(
    *,
    manifest: DataFrame,
    histories: Mapping[str, DataFrame],
    publication_family: str,
    publication_run_id: str,
    run_id_column: str,
) -> tuple[FamilyPublicationFinding, ...]:
    """Compare one committed manifest row with persisted family histories."""

    family = validate_publication_name(
        publication_family,
        label="publication_family",
    )
    run_id = validate_publication_run_id(publication_run_id)
    column_name = validate_run_id_column(run_id_column)
    rows = _manifest_rows(
        manifest,
        publication_family=family,
        publication_run_id=run_id,
    )
    if not rows:
        return (
            FamilyPublicationFinding(
                code="missing_manifest_run",
                dataset=f"{family}_publication_manifest",
                count=1,
            ),
        )
    if len(rows) != 1:
        return (
            FamilyPublicationFinding(
                code="duplicate_manifest_run",
                dataset=f"{family}_publication_manifest",
                count=1,
            ),
        )
    row = rows[0]
    if row["publication_state"] != STATE_COMMITTED:
        return (
            FamilyPublicationFinding(
                code="run_not_committed",
                dataset=f"{family}_publication_manifest",
                count=1,
            ),
        )

    findings: list[FamilyPublicationFinding] = []
    try:
        dataset_names = _parse_dataset_names(row["dataset_names_json"])
        expected_evidence = _parse_evidence(row["dataset_evidence_json"])
    except ValueError:
        return (
            FamilyPublicationFinding(
                code="manifest_evidence_unreadable",
                dataset=f"{family}_publication_manifest",
                count=1,
            ),
        )

    if set(dataset_names) != set(expected_evidence):
        findings.append(
            FamilyPublicationFinding(
                code="manifest_dataset_mismatch",
                dataset=f"{family}_publication_manifest",
                count=1,
            )
        )
    if set(histories) != set(dataset_names):
        findings.append(
            FamilyPublicationFinding(
                code="history_dataset_mismatch",
                dataset=f"{family}_publication_manifest",
                count=len(set(histories).symmetric_difference(dataset_names)) or 1,
            )
        )

    for dataset_name in dataset_names:
        history = histories.get(dataset_name)
        expected = expected_evidence.get(dataset_name)
        if history is None or expected is None:
            continue
        try:
            _require_columns(history, {column_name}, label=dataset_name)
            columns = _columns_from_json(
                str(expected["columns_json"]),
                dataset=dataset_name,
            )
            persisted = history.where(F.col(column_name) == F.lit(run_id))
            actual = dataset_evidence(persisted, columns=columns)
        except ValueError:
            findings.append(
                FamilyPublicationFinding(
                    code="history_schema_unreadable",
                    dataset=dataset_name,
                    count=1,
                )
            )
            continue

        expected_count = int(expected["row_count"])
        if actual.row_count != expected_count:
            findings.append(
                FamilyPublicationFinding(
                    code="history_row_count_mismatch",
                    dataset=dataset_name,
                    count=abs(actual.row_count - expected_count) or 1,
                )
            )
        if actual.schema_sha256 != expected["schema_sha256"]:
            findings.append(
                FamilyPublicationFinding(
                    code="history_schema_mismatch",
                    dataset=dataset_name,
                    count=1,
                )
            )
        if actual.payload_sha256 != expected["payload_sha256"]:
            findings.append(
                FamilyPublicationFinding(
                    code="history_payload_mismatch",
                    dataset=dataset_name,
                    count=1,
                )
            )

    return tuple(sorted(findings))


def select_latest_committed_frames(
    *,
    manifest: DataFrame,
    histories: Mapping[str, DataFrame],
    publication_family: str,
    run_id_column: str,
) -> dict[str, DataFrame]:
    """Return each history filtered to one latest committed family run."""

    column_name = validate_run_id_column(run_id_column)
    run_id = latest_committed_run_id(
        manifest,
        publication_family=publication_family,
    )
    selected: dict[str, DataFrame] = {}
    for raw_name, history in histories.items():
        dataset_name = validate_publication_name(raw_name, label="dataset name")
        _require_columns(history, {column_name}, label=dataset_name)
        selected[dataset_name] = (
            history.limit(0)
            if run_id is None
            else history.where(F.col(column_name) == F.lit(run_id))
        )
    return selected
