# Databricks notebook source
# MAGIC %md
# MAGIC # 05 - Forecast validation
# MAGIC
# MAGIC Build a transparent calendar-window downtime forecast with explicit
# MAGIC client-readiness thresholds and executable backtest evidence.

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

from lakehouse_demo.spark_forecast import (  # noqa: E402
    ForecastConfig,
    build_forecast_frames,
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


forecast_run_id = dbutils.widgets.get("forecast_run_id").strip()
if not forecast_run_id:
    forecast_run_id = f"manual_{uuid.uuid4().hex}"

generated_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
forecast_validation_table = (
    f"{catalog}.{schema}.gold_downtime_forecast_validation"
)
forecast_table = f"{catalog}.{schema}.gold_downtime_forecast"

# COMMAND ----------

forecast_frames = build_forecast_frames(
    spark.table(gold_machine_uptime),
    config=config,
    forecast_run_id=forecast_run_id,
    generated_at_utc=generated_at_utc,
)
validation = forecast_frames["validation"]
forecast = forecast_frames["forecast"]

# COMMAND ----------

(
    validation.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(forecast_validation_table)
)

(
    forecast.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(forecast_table)
)

display(
    forecast.orderBy(
        "forecast_status",
        F.col("forecast_downtime_minutes").desc(),
    )
)
