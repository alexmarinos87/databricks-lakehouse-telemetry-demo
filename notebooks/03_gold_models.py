# Databricks notebook source
# MAGIC %md
# MAGIC # 03 - Gold models
# MAGIC
# MAGIC Build the five governed Gold outputs from the latest committed Silver
# MAGIC generation and publish them together behind a manifest-last boundary.

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
from lakehouse_demo.downtime_pipeline import (  # noqa: E402
    build_governed_gold_frames,
)
from lakehouse_demo.spark_family_publication import (  # noqa: E402
    STATE_COMMITTED,
    STATE_FAILED,
    STATE_STARTED,
    audit_family_publication,
    build_family_manifest,
    latest_committed_run_id,
    publication_state_for_run,
    transition_family_manifest,
    validate_publication_run_id,
    with_publication_run_id,
)

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")
dbutils.widgets.text("gold_publication_run_id", "")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

publication_family = "gold"
run_id_column = "gold_publication_run_id"
gold_publication_run_id = dbutils.widgets.get("gold_publication_run_id").strip()
if not gold_publication_run_id:
    gold_publication_run_id = f"manual_{uuid.uuid4().hex}"
validate_publication_run_id(gold_publication_run_id)

silver_table = f"{catalog}.{schema}.silver_machine_events"

current_names = {
    "gold_machine_uptime": "gold_machine_uptime",
    "gold_failure_events": "gold_failure_events",
    "gold_maintenance_costs": "gold_maintenance_costs",
    "gold_parts_usage": "gold_parts_usage",
    "gold_client_asset_summary": "gold_client_asset_summary",
}
history_names = {
    dataset_name: f"{dataset_name}_history"
    for dataset_name in current_names
}
manifest_name = "gold_publication_manifest"

current_tables = {
    dataset_name: f"{catalog}.{schema}.{object_name}"
    for dataset_name, object_name in current_names.items()
}
history_tables = {
    dataset_name: f"{catalog}.{schema}.{object_name}"
    for dataset_name, object_name in history_names.items()
}
manifest_table = f"{catalog}.{schema}.{manifest_name}"


def utc_now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sql_string_literal(value):
    return "'" + value.replace("'", "''") + "'"

# COMMAND ----------

silver = spark.table(silver_table)
gold_frames = build_governed_gold_frames(silver)

missing_outputs = sorted(set(current_names).difference(gold_frames))
unexpected_outputs = sorted(set(gold_frames).difference(current_names))
if missing_outputs or unexpected_outputs:
    raise ValueError(
        "Gold transformation output family does not match the governed contract"
    )

publication_frames = with_publication_run_id(
    {
        dataset_name: gold_frames[dataset_name]
        for dataset_name in current_names
    },
    publication_run_id=gold_publication_run_id,
    run_id_column=run_id_column,
)
publication_started_at_utc = utc_now_text()
started_manifest = build_family_manifest(
    publication_frames,
    publication_family=publication_family,
    publication_run_id=gold_publication_run_id,
    run_id_column=run_id_column,
    publication_started_at_utc=publication_started_at_utc,
)

# COMMAND ----------


def _object_type(qualified_name):
    if not spark.catalog.tableExists(qualified_name):
        return None
    return spark.catalog.getTable(qualified_name).tableType.upper()


def _preflight_relations():
    for current_table in current_tables.values():
        relation_type = _object_type(current_table)
        if relation_type is not None and relation_type != "VIEW":
            raise RuntimeError(
                "Versioned Gold publication requires every current Gold "
                "relation to be a view. Preserve and rename legacy physical "
                "tables before retrying."
            )

    for durable_table in [*history_tables.values(), manifest_table]:
        if _object_type(durable_table) == "VIEW":
            raise RuntimeError(
                "Gold history and publication manifest relations must be tables"
            )


def _merge_manifest(manifest_frame):
    temporary_view = f"_gold_manifest_{uuid.uuid4().hex}"
    manifest_frame.createOrReplaceTempView(temporary_view)
    manifest_identifier = quote_sql_identifier(catalog, schema, manifest_name)
    temporary_identifier = quote_sql_identifier(temporary_view)
    try:
        spark.sql(
            f"""
            MERGE INTO {manifest_identifier} AS target
            USING {temporary_identifier} AS source
              ON target.publication_family = source.publication_family
             AND target.publication_run_id = source.publication_run_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """
        )
    finally:
        spark.catalog.dropTempView(temporary_view)


def _replace_history_run(dataset_name):
    history_identifier = quote_sql_identifier(
        catalog,
        schema,
        history_names[dataset_name],
    )
    run_literal = sql_string_literal(gold_publication_run_id)
    spark.sql(
        f"DELETE FROM {history_identifier} "
        f"WHERE {quote_sql_identifier(run_id_column)} = {run_literal}"
    )
    (
        publication_frames[dataset_name]
        .write.format("delta")
        .mode("append")
        .option("mergeSchema", True)
        .saveAsTable(history_tables[dataset_name])
    )


def _current_view_query(dataset_name):
    history_identifier = quote_sql_identifier(
        catalog,
        schema,
        history_names[dataset_name],
    )
    manifest_identifier = quote_sql_identifier(catalog, schema, manifest_name)
    run_identifier = quote_sql_identifier(run_id_column)
    return f"""
        SELECT
          history.*,
          latest_publication.publication_completed_at_utc
        FROM {history_identifier} AS history
        INNER JOIN (
          SELECT
            publication_run_id,
            publication_completed_at_utc
          FROM (
            SELECT
              publication_run_id,
              publication_completed_at_utc,
              ROW_NUMBER() OVER (
                ORDER BY
                  publication_completed_at_utc DESC,
                  publication_run_id DESC
              ) AS publication_rank
            FROM {manifest_identifier}
            WHERE publication_family = '{publication_family}'
              AND publication_state = 'COMMITTED'
          ) AS ranked_publications
          WHERE publication_rank = 1
        ) AS latest_publication
          ON history.{run_identifier} = latest_publication.publication_run_id
    """


def _apply_current_view(dataset_name):
    qualified_name = current_tables[dataset_name]
    relation_type = _object_type(qualified_name)
    if relation_type not in (None, "VIEW"):
        raise RuntimeError("Current Gold relation must be absent or a view")
    verb = "ALTER VIEW" if relation_type == "VIEW" else "CREATE VIEW"
    current_identifier = quote_sql_identifier(
        catalog,
        schema,
        current_names[dataset_name],
    )
    spark.sql(f"{verb} {current_identifier} AS\n" + _current_view_query(dataset_name))


def _apply_current_views():
    for dataset_name in sorted(current_names):
        _apply_current_view(dataset_name)


def _finding_summary(findings):
    return ",".join(
        sorted(
            {
                f"{finding.dataset}:{finding.code}:{finding.count}"
                for finding in findings
            }
        )
    )


_preflight_relations()

existing_state = None
if spark.catalog.tableExists(manifest_table):
    existing_state = publication_state_for_run(
        spark.table(manifest_table),
        publication_family=publication_family,
        publication_run_id=gold_publication_run_id,
    )

if existing_state == STATE_COMMITTED:
    missing_histories = [
        table_name
        for table_name in history_tables.values()
        if not spark.catalog.tableExists(table_name)
    ]
    if missing_histories:
        raise RuntimeError(
            "Committed Gold manifest is missing required history tables"
        )
    committed_findings = audit_family_publication(
        manifest=spark.table(manifest_table),
        histories={
            dataset_name: spark.table(table_name)
            for dataset_name, table_name in history_tables.items()
        },
        publication_family=publication_family,
        publication_run_id=gold_publication_run_id,
        run_id_column=run_id_column,
    )
    if committed_findings:
        raise RuntimeError(
            "Committed Gold publication failed reconciliation: "
            + _finding_summary(committed_findings)
        )
    _apply_current_views()
    display(
        spark.table(current_tables["gold_client_asset_summary"]).orderBy(
            "client_id",
            "site_id",
            "machine_id",
        )
    )
    dbutils.notebook.exit(
        f"Gold publication {gold_publication_run_id} was already committed"
    )

if existing_state not in (None, STATE_STARTED, STATE_FAILED):
    raise RuntimeError("Gold publication manifest contains an unsupported state")

# COMMAND ----------

for dataset_name, table_name in history_tables.items():
    (
        publication_frames[dataset_name]
        .limit(0)
        .write.format("delta")
        .mode("ignore")
        .saveAsTable(table_name)
    )
(
    started_manifest.limit(0)
    .write.format("delta")
    .mode("ignore")
    .saveAsTable(manifest_table)
)
_apply_current_views()

failure_stage = "write_started_manifest"
publication_committed = False

try:
    _merge_manifest(started_manifest)

    for dataset_name in sorted(current_names):
        failure_stage = f"write_{dataset_name}_history"
        _replace_history_run(dataset_name)

    failure_stage = "reconcile_persisted_history"
    expected_committed_manifest = transition_family_manifest(
        started_manifest,
        publication_state=STATE_COMMITTED,
        publication_completed_at_utc=utc_now_text(),
    )
    persisted_findings = audit_family_publication(
        manifest=expected_committed_manifest,
        histories={
            dataset_name: spark.table(table_name)
            for dataset_name, table_name in history_tables.items()
        },
        publication_family=publication_family,
        publication_run_id=gold_publication_run_id,
        run_id_column=run_id_column,
    )
    if persisted_findings:
        raise RuntimeError(
            "Gold history failed pre-commit reconciliation: "
            + _finding_summary(persisted_findings)
        )

    failure_stage = "commit_manifest"
    _merge_manifest(expected_committed_manifest)
    publication_committed = True

except Exception:
    if not publication_committed:
        try:
            failed_manifest = transition_family_manifest(
                started_manifest,
                publication_state=STATE_FAILED,
                publication_completed_at_utc=utc_now_text(),
                failure_code=failure_stage,
            )
            _merge_manifest(failed_manifest)
        except Exception:
            pass
    raise RuntimeError(
        f"Gold publication failed during {failure_stage}; "
        "the incomplete generation remains hidden from current views"
    ) from None

# COMMAND ----------

manifest_frame = spark.table(manifest_table)
expected_current_run_id = latest_committed_run_id(
    manifest_frame,
    publication_family=publication_family,
)
if expected_current_run_id != gold_publication_run_id:
    raise RuntimeError("Current Gold publication did not resolve the committed run")

for dataset_name in sorted(current_names):
    published = spark.table(current_tables[dataset_name])
    expected_count = publication_frames[dataset_name].count()
    expected_ids = {gold_publication_run_id} if expected_count else set()
    actual_ids = {
        row[run_id_column]
        for row in published.select(run_id_column).distinct().collect()
    }
    if actual_ids != expected_ids or published.count() != expected_count:
        raise RuntimeError(
            "Current Gold views did not resolve to the committed publication"
        )

display(
    spark.table(current_tables["gold_client_asset_summary"]).orderBy(
        "client_id",
        "site_id",
        "machine_id",
    )
)
