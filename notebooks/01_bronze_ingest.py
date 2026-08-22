# Databricks notebook source
# MAGIC %md
# MAGIC # 01 - Bronze ingest
# MAGIC
# MAGIC Incrementally ingest immutable construction-equipment telemetry objects
# MAGIC into a Delta bronze table with Auto Loader. Bronze keeps the source-shaped
# MAGIC records and records content-addressed lineage for incremental and explicit
# MAGIC backfill objects.

# COMMAND ----------

import sys
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

from lakehouse_demo.azure_ingestion import (  # noqa: E402
    AzureIngestionConfig,
    build_adls_oauth_conf,
    quote_sql_identifier,
    resolve_ingestion_paths,
)
from lakehouse_demo.ingestion_identity import parse_object_name  # noqa: E402
from lakehouse_demo.spark_ingestion_identity import (  # noqa: E402
    IDENTITY_COLUMNS,
    with_ingestion_identity,
)
from lakehouse_demo.spark_medallion import raw_machine_event_schema  # noqa: E402

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "lakehouse_demo")
dbutils.widgets.text(
    "source_path", "dbfs:/FileStore/lakehouse_demo/raw_machine_events"
)
dbutils.widgets.text(
    "checkpoint_path",
    "dbfs:/FileStore/lakehouse_demo/_checkpoints/bronze_machine_events",
)
dbutils.widgets.text(
    "schema_location",
    "dbfs:/FileStore/lakehouse_demo/_schemas/bronze_machine_events",
)
dbutils.widgets.text("unity_catalog_volume", "")
dbutils.widgets.text("create_unity_catalog_volume", "true")
dbutils.widgets.text("volume_source_path", "raw_machine_events")
dbutils.widgets.text("volume_checkpoint_path", "_checkpoints/bronze_machine_events")
dbutils.widgets.text("volume_schema_location", "_schemas/bronze_machine_events")
dbutils.widgets.text("azure_storage_account", "")
dbutils.widgets.text("azure_container", "")
dbutils.widgets.text(
    "azure_source_path", "lakehouse_demo/raw_machine_events"
)
dbutils.widgets.text(
    "azure_checkpoint_path", "lakehouse_demo/_checkpoints/bronze_machine_events"
)
dbutils.widgets.text(
    "azure_schema_location", "lakehouse_demo/_schemas/bronze_machine_events"
)
dbutils.widgets.text("azure_tenant_id", "")
dbutils.widgets.text("azure_client_id", "")
dbutils.widgets.text("azure_client_secret_scope", "")
dbutils.widgets.text("azure_client_secret_key", "")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
unity_catalog_volume = dbutils.widgets.get("unity_catalog_volume").strip()
create_unity_catalog_volume = (
    dbutils.widgets.get("create_unity_catalog_volume").strip().lower() == "true"
)

azure_client_secret_scope = dbutils.widgets.get(
    "azure_client_secret_scope"
).strip()
azure_client_secret_key = dbutils.widgets.get("azure_client_secret_key").strip()
azure_client_secret = ""
if azure_client_secret_scope or azure_client_secret_key:
    if not azure_client_secret_scope or not azure_client_secret_key:
        raise ValueError(
            "Both azure_client_secret_scope and azure_client_secret_key "
            "are required together"
        )
    azure_client_secret = dbutils.secrets.get(
        scope=azure_client_secret_scope,
        key=azure_client_secret_key,
    )

ingestion_config = AzureIngestionConfig(
    catalog=catalog,
    schema=schema,
    source_path=dbutils.widgets.get("source_path"),
    checkpoint_path=dbutils.widgets.get("checkpoint_path"),
    schema_location=dbutils.widgets.get("schema_location"),
    unity_catalog_volume=unity_catalog_volume,
    volume_source_path=dbutils.widgets.get("volume_source_path"),
    volume_checkpoint_path=dbutils.widgets.get("volume_checkpoint_path"),
    volume_schema_location=dbutils.widgets.get("volume_schema_location"),
    azure_storage_account=dbutils.widgets.get("azure_storage_account"),
    azure_container=dbutils.widgets.get("azure_container"),
    azure_source_path=dbutils.widgets.get("azure_source_path"),
    azure_checkpoint_path=dbutils.widgets.get("azure_checkpoint_path"),
    azure_schema_location=dbutils.widgets.get("azure_schema_location"),
    azure_tenant_id=dbutils.widgets.get("azure_tenant_id"),
    azure_client_id=dbutils.widgets.get("azure_client_id"),
    azure_client_secret=azure_client_secret,
)
ingestion_paths = resolve_ingestion_paths(ingestion_config)

for conf_key, conf_value in build_adls_oauth_conf(ingestion_config).items():
    spark.conf.set(conf_key, conf_value)

source_path = ingestion_paths.source_path.rstrip("/")
checkpoint_path = ingestion_paths.checkpoint_path
schema_location = ingestion_paths.schema_location

bronze_table = f"{catalog}.{schema}.bronze_machine_events"

# COMMAND ----------

schema_identifier = quote_sql_identifier(catalog, schema)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema_identifier}")

if unity_catalog_volume and create_unity_catalog_volume:
    volume_identifier = quote_sql_identifier(
        catalog,
        schema,
        unity_catalog_volume,
    )
    spark.sql(
        f"CREATE VOLUME IF NOT EXISTS {volume_identifier} "
        "COMMENT 'Managed volume for lakehouse demo ingestion files and "
        "Auto Loader state'"
    )

# COMMAND ----------


def _is_directory(file_info):
    is_dir = getattr(file_info, "isDir", None)
    if callable(is_dir):
        return bool(is_dir())
    return str(file_info.path).endswith("/")


def _list_landing_files(root_path, *, max_entries=1000, max_depth=8):
    pending = [(root_path, 0)]
    files = []
    inspected_entries = 0

    while pending:
        current_path, depth = pending.pop()
        if depth > max_depth:
            raise ValueError("Landing directory exceeds the bounded nesting depth")
        try:
            entries = dbutils.fs.ls(current_path)
        except Exception:
            raise ValueError("Landing directory could not be enumerated") from None

        for entry in entries:
            inspected_entries += 1
            if inspected_entries > max_entries:
                raise ValueError("Landing directory exceeds the bounded entry count")
            if _is_directory(entry):
                pending.append((entry.path, depth + 1))
            else:
                files.append(entry.path)

    return files


def _preflight_immutable_landing(root_path):
    landing_files = _list_landing_files(root_path)
    invalid_count = 0
    for landing_file in landing_files:
        object_name = str(landing_file).rstrip("/").rsplit("/", 1)[-1]
        try:
            parse_object_name(object_name)
        except ValueError:
            invalid_count += 1

    if invalid_count:
        raise ValueError(
            f"{invalid_count} landing objects violate the immutable identity contract"
        )
    if not landing_files:
        raise ValueError("No immutable landing objects are available for ingestion")


_preflight_immutable_landing(source_path)

if spark.catalog.tableExists(bronze_table):
    existing_bronze = spark.table(bronze_table)
    missing_identity_columns = sorted(set(IDENTITY_COLUMNS).difference(existing_bronze.columns))
    if missing_identity_columns and existing_bronze.limit(1).count():
        raise ValueError(
            "Existing bronze data predates the immutable source identity contract; "
            "complete the documented migration before ingestion"
        )
    if not missing_identity_columns:
        invalid_existing_rows = existing_bronze.where(
            ~F.coalesce(F.col("_source_identity_valid"), F.lit(False))
        ).limit(1).count()
        if invalid_existing_rows:
            raise ValueError(
                "Existing bronze data contains an invalid immutable source identity"
            )

# COMMAND ----------

raw_schema = raw_machine_event_schema()

bronze_stream = with_ingestion_identity(
    spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "csv")
    .option("cloudFiles.schemaLocation", schema_location)
    .option("cloudFiles.includeExistingFiles", True)
    .option("cloudFiles.allowOverwrites", False)
    .option("header", True)
    .schema(raw_schema)
    .load(source_path)
    .withColumn("_ingested_at", F.current_timestamp())
    .withColumn("_source_file", F.input_file_name())
)

# COMMAND ----------

query = (
    bronze_stream.writeStream.format("delta")
    .option("checkpointLocation", checkpoint_path)
    .option("mergeSchema", True)
    .trigger(availableNow=True)
    .toTable(bronze_table)
)

query.awaitTermination()

bronze = spark.table(bronze_table)
row_count = bronze.count()
if row_count == 0:
    raise ValueError(f"No records were ingested from files in {source_path}")

invalid_identity_count = bronze.where(
    ~F.coalesce(F.col("_source_identity_valid"), F.lit(False))
).limit(1).count()
if invalid_identity_count:
    raise ValueError("Bronze contains rows without a governed immutable source identity")

display(bronze)
