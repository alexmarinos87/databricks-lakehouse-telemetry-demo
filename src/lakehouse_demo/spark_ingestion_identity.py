"""Spark lineage columns for governed immutable ingestion objects."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from lakehouse_demo.ingestion_identity import (
    MODE_BACKFILL,
    MODE_INCREMENTAL,
    SPARK_OBJECT_NAME_PATTERN,
)


IDENTITY_COLUMNS = (
    "_source_object_name",
    "_ingestion_mode",
    "_replay_id",
    "_source_content_sha256",
    "_source_identity_valid",
)


def with_ingestion_identity(dataframe: DataFrame) -> DataFrame:
    """Derive immutable landing identity fields from `_source_file`."""

    if "_source_file" not in dataframe.columns:
        raise ValueError("bronze source dataframe is missing _source_file")

    source_name = F.regexp_extract(F.col("_source_file"), r"([^/]+)$", 1)
    mode = F.regexp_extract(source_name, SPARK_OBJECT_NAME_PATTERN, 1)
    replay_id = F.regexp_extract(source_name, SPARK_OBJECT_NAME_PATTERN, 2)
    content_sha256 = F.regexp_extract(source_name, SPARK_OBJECT_NAME_PATTERN, 3)
    valid = (
        mode.isin(MODE_INCREMENTAL, MODE_BACKFILL)
        & (F.length(content_sha256) == F.lit(64))
        & (
            ((mode == F.lit(MODE_INCREMENTAL)) & (F.length(replay_id) == F.lit(0)))
            | ((mode == F.lit(MODE_BACKFILL)) & (F.length(replay_id) > F.lit(0)))
        )
    )

    return (
        dataframe.withColumn("_source_object_name", source_name)
        .withColumn("_ingestion_mode", mode)
        .withColumn(
            "_replay_id",
            F.when(
                mode == F.lit(MODE_BACKFILL),
                replay_id,
            ).otherwise(F.lit(None).cast("string")),
        )
        .withColumn("_source_content_sha256", content_sha256)
        .withColumn("_source_identity_valid", valid)
    )
