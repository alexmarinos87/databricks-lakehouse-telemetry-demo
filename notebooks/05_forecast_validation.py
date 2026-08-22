# Databricks notebook source
# MAGIC %md
# MAGIC # 05 - Forecast validation
# MAGIC
# MAGIC Build a calendar-window downtime forecast, persist retry-safe history,
# MAGIC and expose only the latest committed publication through governed views.

# COMMAND ----------

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pyspark.sql import functions as F


def _add_project_src_to_path():
    cwd = Path.cwd()
    for base_path in [cwd, *cwd.parents]:
        src_path = base_path / "src"
        if src_path.exists():
            sys.path.insert(0, str(src_path))
            return

    try:
        notebook_path = (
            dbutils.notebook.entry_point.getDbutils()
            .notebook()
            .getContext()
            .notebookPath()
            .get()
        )
        workspace_root = PurePosixPath(notebook_path).parent.parent
        workspace_root_text = str(workspace_root)
        if not workspace_root_text.startswith("/Workspace/"):
            workspace_root_text = str(
                Path("/Workspace") / workspace_root_text.lstrip("/")
            )
        sys.path.insert(0, str(Path(workspace_root_text) / "src"))
    except Exception:
        return


_add_project_src_to_path()

from lakehouse_demo.azure_ingestion import quote_sql_identifier  # noqa: E402
from lakehouse_demo.spark_forecast import (  # noqa: E402
    ForecastConfig,
    build_forecast_frames,
)
from lakehouse_demo.spark_forecast_publication import (  # noqa: E402
    STATE_COMMITTED,
    STATE_FAILED,
    STATE_STARTED,
    audit_publication_run,
    build_publication_manifest,
    latest_committed_run_id,
    publication_state_for_run,
    validate_run_id,
)

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")
dbutils.widgets.text("baseline_window_days", "2")
dbutils.widgets.text("forecast_horizon_days", "1")
dbutils.widgets.text("min_validation_observations", "2")
dbutils.widgets.text("max_mae_downtime_minutes", "")
dbutils.widgets.text("min_interval_coverage_pct", "")
dbutils.widgets.text("forecast_run_id", "")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")


def get_positive_int_widget(widget_name):
    value = int(dbutils.widgets.get(widget_name))
    if value < 1:
        raise ValueError(f"{widget_name} must be a positive integer")
    return value


def get_optional_float_widget(widget_name):
    raw_value = dbutils.widgets.get(widget_name).strip()
    if not raw_value:
        return None
    return float(raw_value)


def utc_now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


forecast_run_id = dbutils.widgets.get("forecast_run_id").strip()
if not forecast_run_id:
    forecast_run_id = f"manual_{uuid.uuid4().hex}"
validate_run_id(forecast_run_id)

publication_started_at_utc = utc_now_text()
generated_at_utc = publication_started_at_utc
config = ForecastConfig(
    baseline_window_days=get_positive_int_widget("baseline_window_days"),
    forecast_horizon_days=get_positive_int_widget("forecast_horizon_days"),
    min_validation_observations=get_positive_int_widget(
        "min_validation_observations"
    ),
    max_mae_downtime_minutes=get_optional_float_widget(
        "max_mae_downtime_minutes"
    ),
    min_interval_coverage_pct=get_optional_float_widget(
        "min_interval_coverage_pct"
    ),
)

gold_machine_uptime = f"{catalog}.{schema}.gold_machine_uptime"
forecast_validation_history_name = "gold_downtime_forecast_validation_history"
forecast_history_name = "gold_downtime_forecast_history"
publication_manifest_name = "gold_downtime_forecast_publication_manifest"
forecast_validation_view_name = "gold_downtime_forecast_validation"
forecast_view_name = "gold_downtime_forecast"

forecast_validation_history_table = (
    f"{catalog}.{schema}.{forecast_validation_history_name}"
)
forecast_history_table = f"{catalog}.{schema}.{forecast_history_name}"
publication_manifest_table = f"{catalog}.{schema}.{publication_manifest_name}"
forecast_validation_view = f"{catalog}.{schema}.{forecast_validation_view_name}"
forecast_view = f"{catalog}.{schema}.{forecast_view_name}"

# COMMAND ----------


def _object_type(qualified_name):
    if not spark.catalog.tableExists(qualified_name):
        return None
    return spark.catalog.getTable(qualified_name).tableType.upper()


def _preflight_relations():
    for current_name in (forecast_validation_view, forecast_view):
        relation_type = _object_type(current_name)
        if relation_type is not None and relation_type != "VIEW":
            raise RuntimeError(
                "Versioned forecast publication requires the current forecast "
                "relations to be views. Preserve and rename the legacy tables "
                "before retrying."
            )

    for history_name in (
        forecast_validation_history_table,
        forecast_history_table,
        publication_manifest_table,
    ):
        relation_type = _object_type(history_name)
        if relation_type == "VIEW":
            raise RuntimeError(
                "Forecast history and publication manifest relations must be tables"
            )


def _sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def _merge_manifest(manifest_frame):
    temporary_view = f"_forecast_manifest_{uuid.uuid4().hex}"
    manifest_frame.createOrReplaceTempView(temporary_view)
    manifest_identifier = quote_sql_identifier(
        catalog,
        schema,
        publication_manifest_name,
    )
    temporary_identifier = quote_sql_identifier(temporary_view)
    try:
        if not spark.catalog.tableExists(publication_manifest_table):
            (
                manifest_frame.write.format("delta")
                .mode("errorifexists")
                .saveAsTable(publication_manifest_table)
            )
            return

        spark.sql(
            f"""
            MERGE INTO {manifest_identifier} AS target
            USING {temporary_identifier} AS source
              ON target.forecast_run_id = source.forecast_run_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """
        )
    finally:
        spark.catalog.dropTempView(temporary_view)


def _replace_history_run(history_name, history_table, frame):
    if spark.catalog.tableExists(history_table):
        spark.sql(
            "DELETE FROM "
            f"{quote_sql_identifier(catalog, schema, history_name)} "
            f"WHERE forecast_run_id = {_sql_literal(forecast_run_id)}"
        )
    (
        frame.write.format("delta")
        .mode("append")
        .option("mergeSchema", True)
        .saveAsTable(history_table)
    )


def _current_view_query(history_name):
    history_identifier = quote_sql_identifier(catalog, schema, history_name)
    manifest_identifier = quote_sql_identifier(
        catalog,
        schema,
        publication_manifest_name,
    )
    return f"""
        SELECT
          history.*,
          latest_publication.publication_completed_at_utc
        FROM {history_identifier} AS history
        INNER JOIN (
          SELECT
            forecast_run_id,
            publication_completed_at_utc
          FROM (
            SELECT
              forecast_run_id,
              publication_completed_at_utc,
              ROW_NUMBER() OVER (
                ORDER BY
                  publication_completed_at_utc DESC,
                  forecast_run_id DESC
              ) AS publication_rank
            FROM {manifest_identifier}
            WHERE publication_state = 'COMMITTED'
          ) AS ranked_publications
          WHERE publication_rank = 1
        ) AS latest_publication
          ON history.forecast_run_id = latest_publication.forecast_run_id
    """


def _apply_current_view(current_name, history_name):
    qualified_name = f"{catalog}.{schema}.{current_name}"
    relation_type = _object_type(qualified_name)
    if relation_type not in (None, "VIEW"):
        raise RuntimeError("Current forecast relation must be absent or a view")

    verb = "ALTER VIEW" if relation_type == "VIEW" else "CREATE VIEW"
    current_identifier = quote_sql_identifier(catalog, schema, current_name)
    spark.sql(
        f"{verb} {current_identifier} AS\n"
        + _current_view_query(history_name)
    )


def _create_current_views():
    _apply_current_view(
        forecast_validation_view_name,
        forecast_validation_history_name,
    )
    _apply_current_view(forecast_view_name, forecast_history_name)


def _publication_findings_text(findings):
    return ",".join(
        sorted(
            {
                f"{finding.dataset}:{finding.code}"
                for finding in findings
            }
        )
    )


_preflight_relations()

existing_state = None
if spark.catalog.tableExists(publication_manifest_table):
    existing_state = publication_state_for_run(
        spark.table(publication_manifest_table),
        forecast_run_id,
    )

if existing_state == STATE_COMMITTED:
    if not spark.catalog.tableExists(forecast_history_table) or not spark.catalog.tableExists(
        forecast_validation_history_table
    ):
        raise RuntimeError(
            "Committed forecast manifest is missing a required history table"
        )
    committed_findings = audit_publication_run(
        manifest=spark.table(publication_manifest_table),
        forecast_history=spark.table(forecast_history_table),
        validation_history=spark.table(forecast_validation_history_table),
        forecast_run_id=forecast_run_id,
    )
    if committed_findings:
        raise RuntimeError(
            "Committed forecast publication failed reconciliation: "
            + _publication_findings_text(committed_findings)
        )
else:
    if existing_state not in (None, STATE_STARTED, STATE_FAILED):
        raise RuntimeError(
            "Forecast publication manifest contains an unsupported state"
        )

    forecast_frames = build_forecast_frames(
        spark.table(gold_machine_uptime),
        config=config,
        forecast_run_id=forecast_run_id,
        generated_at_utc=generated_at_utc,
    )
    validation = forecast_frames["validation"]
    forecast = forecast_frames["forecast"]

    started_manifest = build_publication_manifest(
        validation,
        forecast,
        publication_state=STATE_STARTED,
        publication_started_at_utc=publication_started_at_utc,
        forecast_generated_at_utc=generated_at_utc,
    )
    publication_committed = False
    failure_stage = "write_started_manifest"

    try:
        _merge_manifest(started_manifest)

        failure_stage = "write_validation_history"
        _replace_history_run(
            forecast_validation_history_name,
            forecast_validation_history_table,
            validation,
        )

        failure_stage = "write_forecast_history"
        _replace_history_run(
            forecast_history_name,
            forecast_history_table,
            forecast,
        )

        failure_stage = "reconcile_persisted_history"
        publication_completed_at_utc = utc_now_text()
        expected_committed_manifest = (
            started_manifest.withColumn(
                "publication_state",
                F.lit(STATE_COMMITTED),
            )
            .withColumn(
                "publication_completed_at_utc",
                F.lit(publication_completed_at_utc),
            )
        )
        persisted_findings = audit_publication_run(
            manifest=expected_committed_manifest,
            forecast_history=spark.table(forecast_history_table),
            validation_history=spark.table(forecast_validation_history_table),
            forecast_run_id=forecast_run_id,
        )
        if persisted_findings:
            raise RuntimeError(
                "Forecast history failed pre-commit reconciliation: "
                + _publication_findings_text(persisted_findings)
            )

        failure_stage = "commit_manifest"
        _merge_manifest(expected_committed_manifest)
        publication_committed = True

    except Exception:
        if not publication_committed:
            try:
                failed_manifest = build_publication_manifest(
                    validation,
                    forecast,
                    publication_state=STATE_FAILED,
                    publication_started_at_utc=publication_started_at_utc,
                    forecast_generated_at_utc=generated_at_utc,
                    publication_completed_at_utc=utc_now_text(),
                )
                _merge_manifest(failed_manifest)
            except Exception:
                pass
        raise RuntimeError(
            f"Forecast publication failed during {failure_stage}; "
            "the incomplete run remains hidden from current views"
        ) from None

_create_current_views()

# COMMAND ----------

manifest_frame = spark.table(publication_manifest_table)
expected_current_run_id = latest_committed_run_id(manifest_frame)
current_manifest_rows = (
    manifest_frame.where(F.col("forecast_run_id") == expected_current_run_id)
    .select("forecast_row_count", "validation_row_count")
    .limit(2)
    .collect()
)
if len(current_manifest_rows) != 1:
    raise RuntimeError("Current forecast manifest row could not be resolved")

published_forecast = spark.table(forecast_view)
published_validation = spark.table(forecast_validation_view)
published_forecast_run_ids = {
    row["forecast_run_id"]
    for row in published_forecast.select("forecast_run_id").distinct().collect()
}
published_validation_run_ids = {
    row["forecast_run_id"]
    for row in published_validation.select("forecast_run_id").distinct().collect()
}
expected_forecast_count = int(current_manifest_rows[0]["forecast_row_count"])
expected_validation_count = int(current_manifest_rows[0]["validation_row_count"])
expected_validation_run_ids = (
    {expected_current_run_id} if expected_validation_count else set()
)
if (
    published_forecast_run_ids != {expected_current_run_id}
    or published_validation_run_ids != expected_validation_run_ids
    or published_forecast.count() != expected_forecast_count
    or published_validation.count() != expected_validation_count
):
    raise RuntimeError(
        "Current forecast views did not resolve to the committed publication"
    )

display(
    published_forecast.orderBy(
        "forecast_status",
        F.col("forecast_downtime_minutes").desc(),
    )
)
