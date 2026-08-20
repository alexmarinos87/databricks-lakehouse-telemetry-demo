# Databricks notebook source
# MAGIC %md
# MAGIC # 02 - Silver transform
# MAGIC
# MAGIC Clean, type, quarantine, classify replays, and validate raw machine event data using the same DataFrame functions executed in local Spark CI.

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

from lakehouse_demo.spark_medallion import (  # noqa: E402
    build_silver_frames,
    reconcile_silver,
)

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

bronze_table = f"{catalog}.{schema}.bronze_machine_events"
silver_table = f"{catalog}.{schema}.silver_machine_events"
quarantine_table = f"{catalog}.{schema}.silver_quarantine_machine_events"

# COMMAND ----------

bronze = spark.table(bronze_table)
silver_frames = build_silver_frames(bronze)
silver = silver_frames["silver"]
quarantine = silver_frames["quarantine"]

reconciliation = reconcile_silver(bronze, silver, quarantine)
if not reconciliation.is_reconciled:
    raise ValueError(
        "Silver publication does not reconcile Bronze, quarantine, and "
        "identical replay rows"
    )

print(
    "Silver reconciliation: "
    f"bronze={reconciliation.bronze_rows}, "
    f"silver={reconciliation.silver_rows}, "
    f"invalid_quarantine={reconciliation.invalid_quarantine_rows}, "
    f"conflicting_quarantine={reconciliation.conflicting_quarantine_rows}, "
    f"identical_replays={reconciliation.deduplicated_rows}, "
    f"conflicting_event_ids={reconciliation.conflicting_event_ids}"
)

# COMMAND ----------

# Persist rejected rows first. If conflicting payloads share an event ID, this
# evidence is retained while the previously trusted Silver table remains intact.
(
    quarantine.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(quarantine_table)
)

if reconciliation.has_conflicts:
    raise ValueError(
        "Silver publication blocked because conflicting payloads share "
        "one or more event IDs; inspect the quarantine table"
    )

(
    silver.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", True)
    .saveAsTable(silver_table)
)

display(spark.table(silver_table).orderBy("event_ts_utc", "machine_id"))
