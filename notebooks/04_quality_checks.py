# Databricks notebook source
# MAGIC %md
# MAGIC # 04 - Quality checks
# MAGIC
# MAGIC Run shared medallion and warehouse checks, append detailed and run-level
# MAGIC evidence, and only then fail on error-level findings.

# COMMAND ----------

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


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
            workspace_root_text = str(Path("/Workspace") / workspace_root_text.lstrip("/"))
        sys.path.insert(0, str(Path(workspace_root_text) / "src"))
    except Exception:
        return


_add_project_src_to_path()

from lakehouse_demo.spark_quality import (  # noqa: E402
    QUALITY_TABLE_NAMES,
    evaluate_quality_tables,
    quality_results_dataframe,
    summarize_quality_results,
)

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

table_names = {
    "bronze": f"{catalog}.{schema}.bronze_machine_events",
    "silver": f"{catalog}.{schema}.silver_machine_events",
    "quarantine": f"{catalog}.{schema}.silver_quarantine_machine_events",
    "gold_machine_uptime": f"{catalog}.{schema}.gold_machine_uptime",
    "gold_failure_events": f"{catalog}.{schema}.gold_failure_events",
    "gold_maintenance_costs": f"{catalog}.{schema}.gold_maintenance_costs",
    "gold_parts_usage": f"{catalog}.{schema}.gold_parts_usage",
    "gold_client_asset_summary": f"{catalog}.{schema}.gold_client_asset_summary",
    "dim_client": f"{catalog}.{schema}.dim_client",
    "dim_date": f"{catalog}.{schema}.dim_date",
    "dim_fault": f"{catalog}.{schema}.dim_fault",
    "dim_machine": f"{catalog}.{schema}.dim_machine",
    "dim_model": f"{catalog}.{schema}.dim_model",
    "dim_site": f"{catalog}.{schema}.dim_site",
    "fact_machine_failure_event": f"{catalog}.{schema}.fact_machine_failure_event",
    "fact_machine_uptime_daily": f"{catalog}.{schema}.fact_machine_uptime_daily",
}
if tuple(table_names) != QUALITY_TABLE_NAMES:
    raise ValueError("Quality table mapping does not match the shared contract")

quality_table = f"{catalog}.{schema}.quality_check_results"
quality_history_table = f"{catalog}.{schema}.quality_metric_history"

# COMMAND ----------

candidate_frames = {}
unavailable_tables = {}
for logical_name, table_name in table_names.items():
    try:
        candidate_frames[logical_name] = spark.table(table_name)
    except Exception:
        # Provider diagnostics are deliberately excluded from durable evidence.
        unavailable_tables[logical_name] = table_name

quality_results = evaluate_quality_tables(
    candidate_frames,
    unavailable_tables=unavailable_tables,
)
quality_run_id = str(uuid.uuid4())
checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
results_df = quality_results_dataframe(
    spark,
    quality_results,
    quality_run_id=quality_run_id,
    checked_at=checked_at,
)
quality_history_df = summarize_quality_results(results_df)

# COMMAND ----------

(
    results_df.write.format("delta")
    .mode("append")
    .option("mergeSchema", True)
    .saveAsTable(quality_table)
)
(
    quality_history_df.write.format("delta")
    .mode("append")
    .option("mergeSchema", True)
    .saveAsTable(quality_history_table)
)

# COMMAND ----------

display(results_df.orderBy("status", "severity", "check_name"))

failed_error_checks = results_df.where(
    (results_df.status == "fail") & (results_df.severity == "error")
).count()
if failed_error_checks:
    raise AssertionError(
        f"{failed_error_checks} error-level data quality checks failed; "
        f"evidence is stored under quality_run_id={quality_run_id}"
    )
