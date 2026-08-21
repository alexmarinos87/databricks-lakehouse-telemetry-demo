# Databricks notebook source
# MAGIC %md
# MAGIC # 07 - Warehouse model
# MAGIC
# MAGIC Publish a reconciled star schema from the Gold layer using the same construction and publication-audit functions executed in local Spark CI.

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

from lakehouse_demo.spark_warehouse import (  # noqa: E402
    FAILURE_FACT,
    UPTIME_FACT,
    build_warehouse_frames,
)
from lakehouse_demo.warehouse_publication import (  # noqa: E402
    audit_warehouse_publication,
)

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

gold_uptime_table = f"{catalog}.{schema}.gold_machine_uptime"
gold_failure_table = f"{catalog}.{schema}.gold_failure_events"

warehouse_tables = {
    "dim_client": f"{catalog}.{schema}.dim_client",
    "dim_date": f"{catalog}.{schema}.dim_date",
    "dim_fault": f"{catalog}.{schema}.dim_fault",
    "dim_machine": f"{catalog}.{schema}.dim_machine",
    "dim_model": f"{catalog}.{schema}.dim_model",
    "dim_site": f"{catalog}.{schema}.dim_site",
    FAILURE_FACT: f"{catalog}.{schema}.fact_machine_failure_event",
    UPTIME_FACT: f"{catalog}.{schema}.fact_machine_uptime_daily",
}

# COMMAND ----------

gold_uptime = spark.table(gold_uptime_table)
gold_failures = spark.table(gold_failure_table)
warehouse_frames = build_warehouse_frames(gold_uptime, gold_failures)

missing_outputs = sorted(set(warehouse_tables).difference(warehouse_frames))
if missing_outputs:
    raise ValueError(
        "Warehouse transformation did not return required outputs: "
        + ", ".join(missing_outputs)
    )

findings = audit_warehouse_publication(
    gold_uptime=gold_uptime,
    gold_failures=gold_failures,
    warehouse_frames=warehouse_frames,
)
if findings:
    finding_summary = "; ".join(
        f"{finding.code}:{finding.dataset}:{finding.count}"
        for finding in findings
    )
    raise ValueError(
        "Warehouse reconciliation failed before publication: "
        + finding_summary
    )

print(
    "Warehouse count, grain, referential, natural-identity, and measure-level "
    "reconciliation passed before Delta publication"
)

# COMMAND ----------

for dataset_name, table_name in warehouse_tables.items():
    (
        warehouse_frames[dataset_name]
        .write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", True)
        .saveAsTable(table_name)
    )

display(
    spark.table(warehouse_tables[FAILURE_FACT]).orderBy(
        "event_ts_utc",
        ascending=False,
    )
)
