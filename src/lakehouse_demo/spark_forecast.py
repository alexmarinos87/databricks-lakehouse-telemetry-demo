"""Executable PySpark downtime forecast and readiness evaluation.

All functions operate on DataFrames only. Databricks notebooks own persistence;
local Spark tests execute the same calendar-window and readiness behaviour.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F


SEGMENT_COLUMNS = ("site_id", "client_id", "model")
MODEL_NAME = "rolling_mean_baseline"
WINDOW_SEMANTICS = "calendar_days"

STATUS_VALIDATED = "validated_baseline"
STATUS_INSUFFICIENT_HISTORY = "insufficient_validation_history"
STATUS_THRESHOLDS_NOT_CONFIGURED = "thresholds_not_configured"
STATUS_ACCURACY_THRESHOLD_FAILED = "accuracy_threshold_failed"
KNOWN_FORECAST_STATUSES = (
    STATUS_VALIDATED,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_THRESHOLDS_NOT_CONFIGURED,
    STATUS_ACCURACY_THRESHOLD_FAILED,
)

_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_GENERATED_AT_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z"
)


@dataclass(frozen=True)
class ForecastConfig:
    """Validated forecast-window and client-readiness controls."""

    baseline_window_days: int
    forecast_horizon_days: int
    min_validation_observations: int
    max_mae_downtime_minutes: float | None = None
    min_interval_coverage_pct: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "baseline_window_days",
            "forecast_horizon_days",
            "min_validation_observations",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

        if (self.max_mae_downtime_minutes is None) != (
            self.min_interval_coverage_pct is None
        ):
            raise ValueError(
                "max_mae_downtime_minutes and min_interval_coverage_pct "
                "must be configured together"
            )

        if self.max_mae_downtime_minutes is not None and (
            isinstance(self.max_mae_downtime_minutes, bool)
            or not math.isfinite(self.max_mae_downtime_minutes)
            or self.max_mae_downtime_minutes < 0
        ):
            raise ValueError(
                "max_mae_downtime_minutes must be a finite non-negative number"
            )

        if self.min_interval_coverage_pct is not None and (
            isinstance(self.min_interval_coverage_pct, bool)
            or not math.isfinite(self.min_interval_coverage_pct)
            or not 0 <= self.min_interval_coverage_pct <= 100
        ):
            raise ValueError(
                "min_interval_coverage_pct must be between 0 and 100"
            )

    @property
    def thresholds_configured(self) -> bool:
        return self.max_mae_downtime_minutes is not None


def _require_columns(frame: DataFrame, required: set[str], *, label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _validate_run_context(forecast_run_id: str, generated_at_utc: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(forecast_run_id):
        raise ValueError(
            "forecast_run_id must contain 1-128 safe alphanumeric identifier characters"
        )
    if not _GENERATED_AT_PATTERN.fullmatch(generated_at_utc):
        raise ValueError("generated_at_utc must use the UTC shape YYYY-MM-DDTHH:MM:SSZ")


def _generated_timestamp(generated_at_utc: str):
    return F.to_timestamp(F.lit(generated_at_utc), "yyyy-MM-dd'T'HH:mm:ssX")


def _daily_actuals(gold_uptime: DataFrame) -> DataFrame:
    return (
        gold_uptime.groupBy("event_date", *SEGMENT_COLUMNS)
        .agg(
            F.sum(F.coalesce(F.col("downtime_minutes"), F.lit(0))).alias(
                "actual_downtime_minutes"
            ),
            F.countDistinct("machine_id").alias("machine_count"),
            F.round(F.avg("uptime_pct"), 2).alias("avg_uptime_pct"),
            F.round(F.avg("avg_health_score"), 2).alias("avg_health_score"),
        )
        .where(F.col("event_date").isNotNull())
    )


def build_forecast_frames(
    gold_uptime: DataFrame,
    *,
    config: ForecastConfig,
    forecast_run_id: str,
    generated_at_utc: str,
) -> Mapping[str, DataFrame]:
    """Build calendar-window validation and forecast DataFrames.

    ``validated_baseline`` is emitted only when explicit MAE and coverage
    thresholds are configured and both pass. Date ordinals—not epoch seconds—
    define window membership, so daylight-saving transitions cannot alter it.
    """

    _validate_run_context(forecast_run_id, generated_at_utc)
    _require_columns(
        gold_uptime,
        {
            "event_date",
            "site_id",
            "client_id",
            "machine_id",
            "model",
            "downtime_minutes",
            "uptime_pct",
            "avg_health_score",
        },
        label="gold uptime",
    )

    daily = _daily_actuals(gold_uptime)
    if daily.limit(1).count() == 0:
        raise ValueError("gold uptime must contain at least one dated row")

    validation_window = (
        Window.partitionBy(*SEGMENT_COLUMNS)
        .orderBy(F.col("_event_day_number"))
        .rangeBetween(-config.baseline_window_days, -1)
    )
    validation_base = (
        daily.withColumn(
            "_event_day_number",
            F.datediff(F.col("event_date"), F.lit("1970-01-01").cast("date")),
        )
        .withColumn(
            "forecast_downtime_minutes",
            F.avg("actual_downtime_minutes").over(validation_window),
        )
        .withColumn(
            "history_day_count",
            F.count("actual_downtime_minutes").over(validation_window),
        )
        .withColumn("history_start_date", F.min("event_date").over(validation_window))
        .where(F.col("history_day_count") >= 1)
        .withColumn(
            "forecast_downtime_minutes",
            F.round("forecast_downtime_minutes", 2),
        )
        .withColumn(
            "history_calendar_span_days",
            F.datediff("event_date", "history_start_date"),
        )
        .withColumn(
            "absolute_error_minutes",
            F.round(
                F.abs(
                    F.col("actual_downtime_minutes")
                    - F.col("forecast_downtime_minutes")
                ),
                2,
            ),
        )
        .withColumn(
            "squared_error_minutes",
            F.pow(
                F.col("actual_downtime_minutes")
                - F.col("forecast_downtime_minutes"),
                2,
            ),
        )
        .withColumn(
            "absolute_percentage_error",
            F.when(
                F.col("actual_downtime_minutes") > 0,
                F.abs(
                    F.col("actual_downtime_minutes")
                    - F.col("forecast_downtime_minutes")
                )
                / F.col("actual_downtime_minutes"),
            ),
        )
        .withColumn(
            "residual_minutes",
            F.round(
                F.col("actual_downtime_minutes")
                - F.col("forecast_downtime_minutes"),
                2,
            ),
        )
        .withColumn("forecast_run_id", F.lit(forecast_run_id))
        .withColumn("validation_generated_at", _generated_timestamp(generated_at_utc))
        .withColumn("model_name", F.lit(MODEL_NAME))
        .withColumn("window_semantics", F.lit(WINDOW_SEMANTICS))
        .withColumn("baseline_window_days", F.lit(config.baseline_window_days))
    )

    metrics = validation_base.groupBy(*SEGMENT_COLUMNS).agg(
        F.count(F.lit(1)).alias("validation_observation_count"),
        F.round(F.avg("absolute_error_minutes"), 2).alias("mae_downtime_minutes"),
        F.round(F.sqrt(F.avg("squared_error_minutes")), 2).alias(
            "rmse_downtime_minutes"
        ),
        F.round(F.avg("absolute_percentage_error") * 100, 2).alias("mape_pct"),
        F.round(F.stddev_samp("residual_minutes"), 2).alias(
            "residual_stddev_minutes"
        ),
        F.max("event_date").alias("latest_validation_date"),
    )

    validation = (
        validation_base.join(
            metrics.select(
                *SEGMENT_COLUMNS,
                "mae_downtime_minutes",
                "residual_stddev_minutes",
            ),
            list(SEGMENT_COLUMNS),
            "left",
        )
        .withColumn(
            "validation_interval_padding_minutes",
            F.coalesce(
                F.col("residual_stddev_minutes"),
                F.col("mae_downtime_minutes"),
                F.lit(0.0),
            ),
        )
        .withColumn(
            "validation_interval_lower_minutes",
            F.greatest(
                F.lit(0.0),
                F.round(
                    F.col("forecast_downtime_minutes")
                    - F.col("validation_interval_padding_minutes"),
                    2,
                ),
            ),
        )
        .withColumn(
            "validation_interval_upper_minutes",
            F.round(
                F.col("forecast_downtime_minutes")
                + F.col("validation_interval_padding_minutes"),
                2,
            ),
        )
        .withColumn(
            "covered_by_validation_interval",
            (
                F.col("actual_downtime_minutes")
                >= F.col("validation_interval_lower_minutes")
            )
            & (
                F.col("actual_downtime_minutes")
                <= F.col("validation_interval_upper_minutes")
            ),
        )
        .select(
            "forecast_run_id",
            "validation_generated_at",
            "model_name",
            "window_semantics",
            "baseline_window_days",
            "event_date",
            *SEGMENT_COLUMNS,
            "machine_count",
            "actual_downtime_minutes",
            "forecast_downtime_minutes",
            "history_day_count",
            "history_start_date",
            "history_calendar_span_days",
            "absolute_error_minutes",
            "squared_error_minutes",
            "absolute_percentage_error",
            "residual_minutes",
            "validation_interval_lower_minutes",
            "validation_interval_upper_minutes",
            "covered_by_validation_interval",
        )
    )

    coverage = validation.groupBy(*SEGMENT_COLUMNS).agg(
        F.round(
            F.avg(
                F.when(
                    F.col("covered_by_validation_interval"), F.lit(1.0)
                ).otherwise(F.lit(0.0))
            )
            * 100,
            2,
        ).alias("backtest_interval_coverage_pct")
    )
    summary = (
        metrics.join(coverage, list(SEGMENT_COLUMNS), "left")
        .withColumn(
            "meets_min_validation_samples",
            F.col("validation_observation_count")
            >= F.lit(config.min_validation_observations),
        )
    )

    latest_dates = daily.groupBy(*SEGMENT_COLUMNS).agg(
        F.max("event_date").alias("latest_actual_date")
    )
    latest_history = (
        daily.join(latest_dates, list(SEGMENT_COLUMNS), "inner")
        .where(
            (F.col("event_date") <= F.col("latest_actual_date"))
            & (
                F.col("event_date")
                >= F.date_sub(
                    F.col("latest_actual_date"),
                    config.baseline_window_days - 1,
                )
            )
        )
        .groupBy(*SEGMENT_COLUMNS, "latest_actual_date")
        .agg(
            F.min("event_date").alias("forecast_history_start_date"),
            F.count(F.lit(1)).alias("forecast_history_day_count"),
            F.round(F.avg("actual_downtime_minutes"), 2).alias(
                "forecast_downtime_minutes"
            ),
            F.round(F.avg("avg_uptime_pct"), 2).alias("recent_avg_uptime_pct"),
            F.round(F.avg("avg_health_score"), 2).alias(
                "recent_avg_health_score"
            ),
            F.max("machine_count").alias("machine_count"),
        )
        .withColumn(
            "forecast_history_calendar_span_days",
            F.datediff("latest_actual_date", "forecast_history_start_date") + F.lit(1),
        )
    )

    configured = config.thresholds_configured
    max_mae = config.max_mae_downtime_minutes
    min_coverage = config.min_interval_coverage_pct
    forecast = (
        latest_history.join(summary, list(SEGMENT_COLUMNS), "left")
        .withColumn(
            "validation_observation_count",
            F.coalesce(F.col("validation_observation_count"), F.lit(0)),
        )
        .withColumn(
            "meets_min_validation_samples",
            F.coalesce(F.col("meets_min_validation_samples"), F.lit(False)),
        )
        .withColumn("thresholds_configured", F.lit(configured))
        .withColumn("max_mae_downtime_minutes", F.lit(max_mae).cast("double"))
        .withColumn(
            "min_interval_coverage_pct", F.lit(min_coverage).cast("double")
        )
        .withColumn(
            "meets_mae_threshold",
            F.when(
                F.lit(configured),
                F.col("mae_downtime_minutes").isNotNull()
                & (F.col("mae_downtime_minutes") <= F.lit(max_mae).cast("double")),
            ).otherwise(F.lit(False)),
        )
        .withColumn(
            "meets_interval_coverage_threshold",
            F.when(
                F.lit(configured),
                F.col("backtest_interval_coverage_pct").isNotNull()
                & (
                    F.col("backtest_interval_coverage_pct")
                    >= F.lit(min_coverage).cast("double")
                ),
            ).otherwise(F.lit(False)),
        )
        .withColumn(
            "forecast_status",
            F.when(
                ~F.col("meets_min_validation_samples"),
                F.lit(STATUS_INSUFFICIENT_HISTORY),
            )
            .when(
                ~F.col("thresholds_configured"),
                F.lit(STATUS_THRESHOLDS_NOT_CONFIGURED),
            )
            .when(
                F.col("meets_mae_threshold")
                & F.col("meets_interval_coverage_threshold"),
                F.lit(STATUS_VALIDATED),
            )
            .otherwise(F.lit(STATUS_ACCURACY_THRESHOLD_FAILED)),
        )
        .withColumn(
            "prediction_error_padding_minutes",
            F.coalesce(
                F.col("residual_stddev_minutes"),
                F.col("mae_downtime_minutes"),
                F.lit(0.0),
            ),
        )
        .withColumn(
            "forecast_date",
            F.date_add(F.col("latest_actual_date"), config.forecast_horizon_days),
        )
        .withColumn(
            "prediction_interval_lower_minutes",
            F.greatest(
                F.lit(0.0),
                F.round(
                    F.col("forecast_downtime_minutes")
                    - F.col("prediction_error_padding_minutes"),
                    2,
                ),
            ),
        )
        .withColumn(
            "prediction_interval_upper_minutes",
            F.round(
                F.col("forecast_downtime_minutes")
                + F.col("prediction_error_padding_minutes"),
                2,
            ),
        )
        .withColumn("forecast_run_id", F.lit(forecast_run_id))
        .withColumn("forecast_generated_at", _generated_timestamp(generated_at_utc))
        .withColumn("model_name", F.lit(MODEL_NAME))
        .withColumn("window_semantics", F.lit(WINDOW_SEMANTICS))
        .withColumn("baseline_window_days", F.lit(config.baseline_window_days))
        .withColumn("forecast_horizon_days", F.lit(config.forecast_horizon_days))
        .withColumn(
            "min_validation_observations",
            F.lit(config.min_validation_observations),
        )
        .withColumn(
            "model_notes",
            F.lit(
                "Rolling mean of observations in the prior calendar-day window. "
                "Client-ready validation requires explicit MAE and coverage thresholds."
            ),
        )
        .select(
            "forecast_run_id",
            "forecast_generated_at",
            "forecast_date",
            "latest_actual_date",
            "model_name",
            "window_semantics",
            "baseline_window_days",
            "forecast_horizon_days",
            "min_validation_observations",
            *SEGMENT_COLUMNS,
            "machine_count",
            "forecast_downtime_minutes",
            "prediction_interval_lower_minutes",
            "prediction_interval_upper_minutes",
            "recent_avg_uptime_pct",
            "recent_avg_health_score",
            "forecast_history_day_count",
            "forecast_history_start_date",
            "forecast_history_calendar_span_days",
            "validation_observation_count",
            "mae_downtime_minutes",
            "rmse_downtime_minutes",
            "mape_pct",
            "backtest_interval_coverage_pct",
            "latest_validation_date",
            "thresholds_configured",
            "max_mae_downtime_minutes",
            "min_interval_coverage_pct",
            "meets_min_validation_samples",
            "meets_mae_threshold",
            "meets_interval_coverage_threshold",
            "forecast_status",
            "model_notes",
        )
    )

    if forecast.limit(1).count() == 0:
        raise ValueError("no downtime forecast rows were generated")
    return {"validation": validation, "forecast": forecast}
