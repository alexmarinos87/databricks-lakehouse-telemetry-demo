"""Retry-safe Spark evidence for versioned forecast publication.

The helpers in this module operate on DataFrames only. Databricks notebooks own
Delta writes and view creation, while local Spark tests execute manifest,
fingerprint, visibility, and reconciliation behaviour.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
)


STATE_STARTED = "STARTED"
STATE_COMMITTED = "COMMITTED"
STATE_FAILED = "FAILED"
KNOWN_PUBLICATION_STATES = (STATE_STARTED, STATE_COMMITTED, STATE_FAILED)

_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_UTC_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

_MANIFEST_SCHEMA = StructType(
    [
        StructField("forecast_run_id", StringType(), False),
        StructField("publication_state", StringType(), False),
        StructField("publication_started_at_utc", StringType(), False),
        StructField("publication_completed_at_utc", StringType(), True),
        StructField("forecast_generated_at_utc", StringType(), False),
        StructField("model_name", StringType(), False),
        StructField("window_semantics", StringType(), False),
        StructField("baseline_window_days", LongType(), False),
        StructField("forecast_row_count", LongType(), False),
        StructField("validation_row_count", LongType(), False),
        StructField("forecast_columns_json", StringType(), False),
        StructField("validation_columns_json", StringType(), False),
        StructField("forecast_schema_sha256", StringType(), False),
        StructField("validation_schema_sha256", StringType(), False),
        StructField("forecast_payload_sha256", StringType(), False),
        StructField("validation_payload_sha256", StringType(), False),
    ]
)

_REQUIRED_MANIFEST_COLUMNS = {field.name for field in _MANIFEST_SCHEMA.fields}


@dataclass(frozen=True)
class DatasetEvidence:
    """Bounded, order-independent evidence for one publication dataset."""

    row_count: int
    columns_json: str
    schema_sha256: str
    payload_sha256: str


@dataclass(frozen=True, order=True)
class PublicationFinding:
    """A bounded aggregate finding that never includes business row values."""

    code: str
    dataset: str
    count: int


def validate_run_id(forecast_run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(forecast_run_id):
        raise ValueError(
            "forecast_run_id must contain 1-128 safe alphanumeric identifier characters"
        )


def validate_utc_timestamp(value: str, *, label: str) -> None:
    if not _UTC_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must use the UTC shape YYYY-MM-DDTHH:MM:SSZ")


def _require_columns(frame: DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _canonical_schema_json(frame: DataFrame, columns: Sequence[str]) -> str:
    by_name = {field.name: field for field in frame.schema.fields}
    fields = []
    for name in columns:
        field = by_name[name]
        fields.append(
            {
                "name": field.name,
                "type": field.dataType.jsonValue(),
                "nullable": field.nullable,
                "metadata": field.metadata,
            }
        )
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def dataset_evidence(
    frame: DataFrame,
    *,
    columns: Sequence[str] | None = None,
) -> DatasetEvidence:
    """Return bounded evidence without collecting business rows.

    The fingerprint combines row count, canonical schema, minimum and maximum
    row SHA-256 values, and a high-range sum of row xxhash64 values. It is
    order-independent and suitable for retry/reconciliation evidence, but is
    not presented as a cryptographic proof against a malicious collision.
    """

    selected_columns = tuple(sorted(columns or frame.columns))
    if not selected_columns:
        raise ValueError("publication dataset must contain at least one column")
    _require_columns(frame, set(selected_columns), label="publication dataset")

    schema_json = _canonical_schema_json(frame, selected_columns)
    schema_sha256 = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()
    columns_json = json.dumps(selected_columns, separators=(",", ":"))

    payload = F.to_json(
        F.struct(*[F.col(name).alias(name) for name in selected_columns]),
        {
            "ignoreNullFields": "false",
            "dateFormat": "yyyy-MM-dd",
            "timestampFormat": "yyyy-MM-dd'T'HH:mm:ss.SSSXXX",
            "timeZone": "UTC",
        },
    )
    row_evidence = frame.select(
        F.sha2(payload, 256).alias("_row_sha256"),
        F.xxhash64(payload).cast("decimal(38,0)").alias("_row_hash64"),
    )
    aggregate = row_evidence.agg(
        F.count(F.lit(1)).alias("row_count"),
        F.min("_row_sha256").alias("minimum_row_sha256"),
        F.max("_row_sha256").alias("maximum_row_sha256"),
        F.coalesce(
            F.sum("_row_hash64"),
            F.lit(0).cast("decimal(38,0)"),
        ).alias("row_hash64_sum"),
    ).collect()[0]

    material = json.dumps(
        {
            "row_count": int(aggregate["row_count"]),
            "minimum_row_sha256": aggregate["minimum_row_sha256"] or "",
            "maximum_row_sha256": aggregate["maximum_row_sha256"] or "",
            "row_hash64_sum": str(aggregate["row_hash64_sum"]),
            "schema_sha256": schema_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return DatasetEvidence(
        row_count=int(aggregate["row_count"]),
        columns_json=columns_json,
        schema_sha256=schema_sha256,
        payload_sha256=hashlib.sha256(material.encode("utf-8")).hexdigest(),
    )


def _single_non_null_value(
    frame: DataFrame,
    column: str,
    *,
    label: str,
    allow_empty: bool = False,
):
    _require_columns(frame, {column}, label=label)
    values = (
        frame.select(column)
        .where(F.col(column).isNotNull())
        .distinct()
        .limit(2)
        .collect()
    )
    if not values and allow_empty:
        return None
    if len(values) != 1:
        raise ValueError(f"{label} must contain exactly one non-null {column}")
    return values[0][column]


def _candidate_metadata(
    validation: DataFrame,
    forecast: DataFrame,
) -> tuple[str, str, str, int]:
    run_id = str(
        _single_non_null_value(
            forecast,
            "forecast_run_id",
            label="forecast",
        )
    )
    validate_run_id(run_id)

    validation_run_id = _single_non_null_value(
        validation,
        "forecast_run_id",
        label="validation",
        allow_empty=True,
    )
    if validation_run_id is not None and str(validation_run_id) != run_id:
        raise ValueError("forecast and validation use different forecast_run_id values")

    model_name = str(
        _single_non_null_value(
            forecast,
            "model_name",
            label="forecast",
        )
    )
    window_semantics = str(
        _single_non_null_value(
            forecast,
            "window_semantics",
            label="forecast",
        )
    )
    baseline_window_days = int(
        _single_non_null_value(
            forecast,
            "baseline_window_days",
            label="forecast",
        )
    )
    for column, expected in (
        ("model_name", model_name),
        ("window_semantics", window_semantics),
        ("baseline_window_days", baseline_window_days),
    ):
        validation_value = _single_non_null_value(
            validation,
            column,
            label="validation",
            allow_empty=True,
        )
        if validation_value is not None and validation_value != expected:
            raise ValueError(f"forecast and validation use different {column} values")

    return run_id, model_name, window_semantics, baseline_window_days


def build_publication_manifest(
    validation: DataFrame,
    forecast: DataFrame,
    *,
    publication_state: str,
    publication_started_at_utc: str,
    forecast_generated_at_utc: str,
    publication_completed_at_utc: str | None = None,
) -> DataFrame:
    """Build one manifest row from the exact candidate or persisted history."""

    if publication_state not in KNOWN_PUBLICATION_STATES:
        raise ValueError("publication_state is not recognised")
    validate_utc_timestamp(
        publication_started_at_utc,
        label="publication_started_at_utc",
    )
    validate_utc_timestamp(
        forecast_generated_at_utc,
        label="forecast_generated_at_utc",
    )
    if publication_state == STATE_COMMITTED:
        if publication_completed_at_utc is None:
            raise ValueError("committed publication requires completion time")
        validate_utc_timestamp(
            publication_completed_at_utc,
            label="publication_completed_at_utc",
        )
    elif publication_completed_at_utc is not None:
        validate_utc_timestamp(
            publication_completed_at_utc,
            label="publication_completed_at_utc",
        )

    run_id, model_name, window_semantics, baseline_window_days = (
        _candidate_metadata(validation, forecast)
    )
    forecast_evidence = dataset_evidence(forecast)
    validation_evidence = dataset_evidence(validation)

    return forecast.sparkSession.createDataFrame(
        [
            (
                run_id,
                publication_state,
                publication_started_at_utc,
                publication_completed_at_utc,
                forecast_generated_at_utc,
                model_name,
                window_semantics,
                baseline_window_days,
                forecast_evidence.row_count,
                validation_evidence.row_count,
                forecast_evidence.columns_json,
                validation_evidence.columns_json,
                forecast_evidence.schema_sha256,
                validation_evidence.schema_sha256,
                forecast_evidence.payload_sha256,
                validation_evidence.payload_sha256,
            )
        ],
        schema=_MANIFEST_SCHEMA,
    )


def latest_committed_run_id(manifest: DataFrame) -> str | None:
    _require_columns(manifest, _REQUIRED_MANIFEST_COLUMNS, label="manifest")
    duplicate_runs = (
        manifest.groupBy("forecast_run_id")
        .count()
        .where(F.col("count") > 1)
        .limit(1)
        .count()
    )
    if duplicate_runs:
        raise ValueError("manifest contains duplicate forecast_run_id rows")

    rows = (
        manifest.where(F.col("publication_state") == STATE_COMMITTED)
        .orderBy(
            F.col("publication_completed_at_utc").desc_nulls_last(),
            F.col("forecast_run_id").desc(),
        )
        .select("forecast_run_id")
        .limit(1)
        .collect()
    )
    if not rows:
        return None
    return str(rows[0]["forecast_run_id"])


def _manifest_row(manifest: DataFrame, run_id: str):
    rows = (
        manifest.where(F.col("forecast_run_id") == run_id)
        .limit(2)
        .collect()
    )
    if not rows:
        return None
    if len(rows) > 1:
        raise ValueError("manifest contains duplicate forecast_run_id rows")
    return rows[0]


def _columns_from_manifest(raw_value: str, *, dataset: str) -> tuple[str, ...]:
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


def publication_state_for_run(manifest: DataFrame, forecast_run_id: str) -> str | None:
    """Return the unique state for a run, or ``None`` when it is unseen."""

    validate_run_id(forecast_run_id)
    _require_columns(manifest, _REQUIRED_MANIFEST_COLUMNS, label="manifest")
    row = _manifest_row(manifest, forecast_run_id)
    if row is None:
        return None
    state = str(row["publication_state"])
    if state not in KNOWN_PUBLICATION_STATES:
        raise ValueError("manifest contains an unknown publication state")
    return state


def audit_publication_run(
    *,
    manifest: DataFrame,
    forecast_history: DataFrame,
    validation_history: DataFrame,
    forecast_run_id: str,
) -> tuple[PublicationFinding, ...]:
    """Compare one committed manifest row with its persisted history rows."""

    validate_run_id(forecast_run_id)
    _require_columns(manifest, _REQUIRED_MANIFEST_COLUMNS, label="manifest")
    findings: list[PublicationFinding] = []

    try:
        row = _manifest_row(manifest, forecast_run_id)
    except ValueError:
        return (
            PublicationFinding(
                code="duplicate_manifest_run",
                dataset="forecast_publication_manifest",
                count=1,
            ),
        )
    if row is None:
        return (
            PublicationFinding(
                code="missing_manifest_run",
                dataset="forecast_publication_manifest",
                count=1,
            ),
        )
    if row["publication_state"] != STATE_COMMITTED:
        return (
            PublicationFinding(
                code="run_not_committed",
                dataset="forecast_publication_manifest",
                count=1,
            ),
        )

    _require_columns(
        forecast_history,
        {"forecast_run_id"},
        label="forecast history",
    )
    _require_columns(
        validation_history,
        {"forecast_run_id"},
        label="validation history",
    )
    forecast_rows = forecast_history.where(
        F.col("forecast_run_id") == forecast_run_id
    )
    validation_rows = validation_history.where(
        F.col("forecast_run_id") == forecast_run_id
    )

    for dataset, frame, prefix in (
        ("forecast_history", forecast_rows, "forecast"),
        ("validation_history", validation_rows, "validation"),
    ):
        try:
            columns = _columns_from_manifest(
                row[f"{prefix}_columns_json"],
                dataset=dataset,
            )
            evidence = dataset_evidence(frame, columns=columns)
        except ValueError:
            findings.append(
                PublicationFinding(
                    code="manifest_schema_unreadable",
                    dataset=dataset,
                    count=1,
                )
            )
            continue

        expected_count = int(row[f"{prefix}_row_count"])
        if evidence.row_count != expected_count:
            findings.append(
                PublicationFinding(
                    code="history_row_count_mismatch",
                    dataset=dataset,
                    count=abs(evidence.row_count - expected_count) or 1,
                )
            )
        if evidence.schema_sha256 != row[f"{prefix}_schema_sha256"]:
            findings.append(
                PublicationFinding(
                    code="history_schema_mismatch",
                    dataset=dataset,
                    count=1,
                )
            )
        if evidence.payload_sha256 != row[f"{prefix}_payload_sha256"]:
            findings.append(
                PublicationFinding(
                    code="history_payload_mismatch",
                    dataset=dataset,
                    count=1,
                )
            )

    return tuple(sorted(findings))


def select_latest_committed_frames(
    *,
    manifest: DataFrame,
    forecast_history: DataFrame,
    validation_history: DataFrame,
) -> Mapping[str, DataFrame | str]:
    """Return only the latest committed run after reconciling its evidence."""

    run_id = latest_committed_run_id(manifest)
    if run_id is None:
        raise ValueError("manifest does not contain a committed forecast run")

    findings = audit_publication_run(
        manifest=manifest,
        forecast_history=forecast_history,
        validation_history=validation_history,
        forecast_run_id=run_id,
    )
    if findings:
        codes = ",".join(sorted({finding.code for finding in findings}))
        raise ValueError(f"latest committed forecast publication is inconsistent: {codes}")

    return {
        "forecast_run_id": run_id,
        "forecast": forecast_history.where(F.col("forecast_run_id") == run_id),
        "validation": validation_history.where(F.col("forecast_run_id") == run_id),
    }
