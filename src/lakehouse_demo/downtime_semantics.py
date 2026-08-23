"""Approved downtime-impact semantics shared by warehouse and quality logic."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


SEMANTICS_VERSION = 1
DURATION_COLUMN = "duration_minutes"
OBSERVED_COLUMN = "observed_minutes"
DOWNTIME_COLUMN = "downtime_minutes"
IMPACT_RATIO_COLUMN = "downtime_impact_ratio_pct"


def downtime_impact_ratio(
    *,
    downtime_column: str = DOWNTIME_COLUMN,
    observed_column: str = OBSERVED_COLUMN,
):
    """Return outage-impact minutes per observed minute as an uncapped percentage.

    The result is null when no positive observation duration exists. Values over
    100 are valid because downtime is an impact estimate, not a partition of the
    observed event duration.
    """

    return F.when(
        F.col(observed_column) > 0,
        F.round(
            F.col(downtime_column) / F.col(observed_column) * 100,
            2,
        ),
    ).otherwise(F.lit(None).cast("double"))


def invalid_downtime_impact_rows(dataframe: DataFrame) -> DataFrame:
    """Return rows violating the approved aggregate downtime contract."""

    required = {DOWNTIME_COLUMN, OBSERVED_COLUMN, IMPACT_RATIO_COLUMN}
    missing = sorted(required.difference(dataframe.columns))
    if missing:
        raise ValueError(
            "uptime fact is missing downtime-impact columns: " + ", ".join(missing)
        )

    expected_ratio = downtime_impact_ratio()
    invalid = (
        F.col(DOWNTIME_COLUMN).isNull()
        | (F.col(DOWNTIME_COLUMN) < 0)
        | F.col(OBSERVED_COLUMN).isNull()
        | (F.col(OBSERVED_COLUMN) < 0)
        | (
            (F.col(OBSERVED_COLUMN) > 0)
            & ~F.col(IMPACT_RATIO_COLUMN).eqNullSafe(expected_ratio)
        )
        | (
            (F.col(OBSERVED_COLUMN) <= 0)
            & F.col(IMPACT_RATIO_COLUMN).isNotNull()
        )
    )
    return dataframe.where(invalid)
