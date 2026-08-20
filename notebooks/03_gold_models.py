# Databricks notebook source
# MAGIC %md
# MAGIC # 03 - Gold models
# MAGIC
# MAGIC Build BI-ready aggregate Delta tables from cleaned Silver events using the same DataFrame functions executed in local Spark CI.

# COMMAND ----------

import sys
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
            workspace_root_text = str(
                Path("/Workspace") / workspace_root_text.lstrip("/")
            )
        sys.path.insert(0, str(Path(workspace_root_text) / "src"))
    except Exception:
        return


_add_project_src_to_path()

from lakehouse_demo.spark_medallion import build_gold_frames  # noqa: E402

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

silver_table = f"{catalog}.{schema}.silver_machine_events"

gold_tables = {
    "gold_machine_uptime": f"{catalog}.{schema}.gold_machine_uptime",
    "gold_failure_events": f"{catalog}.{schema}.gold_failure_events",
    "gold_maintenance_costs": f"{catalog}.{schema}.gold_maintenance_costs",
    "gold_parts_usage": f"{catalog}.{schema}.gold_parts_usage",
    "gold_client_asset_summary": (
        f"{catalog}.{schema}.gold_client_asset_summary"
    ),
}

# COMMAND ----------

silver = spark.table(silver_table)
gold_frames = build_gold_frames(silver)

missing_outputs = sorted(set(gold_tables).difference(gold_frames))
if missing_outputs:
    raise ValueError(
        "Gold transformation did not return required outputs: "
        + ", ".join(missing_outputs)
    )

# COMMAND ----------

for dataset_name, table_name in gold_tables.items():
    (
        gold_frames[dataset_name]
        .write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", True)
        .saveAsTable(table_name)
    )

display(
    spark.table(gold_tables["gold_client_asset_summary"]).orderBy(
        "client_id",
        "site_id",
        "machine_id",
    )
)
