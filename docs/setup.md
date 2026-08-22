# Setup

## GitHub

Create the repository as private while building:

```text
databricks-lakehouse-telemetry-demo
```

Keep the repository synthetic and interview-safe:

- Do not include employer data.
- Do not include client names.
- Do not include internal screenshots.
- Do not include real costs.
- Do not include proprietary architecture.
- Do not include access tokens or credentials.

Use a GitHub OAuth flow or a fine-grained personal access token scoped only to this repository. Add an expiration date to the token.

## Databricks Git Folder

In Databricks:

1. Open the workspace.
2. Go to Workspace.
3. Create or open Git folders.
4. Configure GitHub credentials.
5. Clone the private GitHub repository.
6. Open the notebooks from the cloned Git folder.

## Immutable Sample Data

The bronze notebook uses Auto Loader and expects governed CSV objects in this
default landing directory:

```text
dbfs:/FileStore/lakehouse_demo/raw_machine_events/
```

Do not upload `sample_machine_events.csv` under its original fixed name and do
not overwrite a file already present in the landing directory. The repository
planner derives a content-addressed object name from the complete SHA-256 digest.

For the default DBFS landing path:

```bash
python3 scripts/plan_ingestion_upload.py \
  --source data/sample_machine_events.csv \
  --destination-root dbfs:/FileStore/lakehouse_demo/raw_machine_events \
  --mode incremental \
  --output .ingestion/dev-upload-plan.json

python3 scripts/upload_ingestion_plan.py \
  --target dev \
  --manifest .ingestion/dev-upload-plan.json
```

The incremental object name has this shape:

```text
machine-events__incremental__sha256_<64 hex>.csv
```

The bronze notebook records these lineage columns:

```text
_source_object_name
_ingestion_mode
_replay_id
_source_content_sha256
_source_identity_valid
```

The notebook recursively preflights the bounded landing directory and fails
without printing paths when any file does not satisfy the immutable identity
contract. Move legacy fixed-name objects outside the watched source root before
adopting this increment.

## Checkpoint Contract

The bronze notebook uses these default Auto Loader state locations:

```text
dbfs:/FileStore/lakehouse_demo/_checkpoints/bronze_machine_events
dbfs:/FileStore/lakehouse_demo/_schemas/bronze_machine_events
```

Keep these paths persistent and unique to the bronze stream. Every normal
incremental or backfill run must reuse the existing checkpoint. Auto Loader uses
the checkpoint to remember processed files, and the notebook explicitly keeps
`cloudFiles.allowOverwrites` disabled.

**Do not delete or replace the checkpoint to perform a normal replay or
backfill.** A checkpoint reset is a separately reviewed recovery operation that
must consider the Bronze table, downstream event identity, schema metadata and
rollback evidence together.

## Incremental And Late Files

The sample increment is:

```text
data/increments/machine_events_increment_2026_04_03.csv
```

Plan and upload it in `incremental` mode after the initial file. Its different
content produces a different immutable destination, while the same checkpoint
continues from its existing state:

```bash
python3 scripts/plan_ingestion_upload.py \
  --source data/increments/machine_events_increment_2026_04_03.csv \
  --destination-root dbfs:/FileStore/lakehouse_demo/raw_machine_events \
  --mode incremental \
  --output .ingestion/increment-upload-plan.json

python3 scripts/upload_ingestion_plan.py \
  --target dev \
  --manifest .ingestion/increment-upload-plan.json
```

Uploading the same content again resolves to the same path. The uploader reads
the existing remote bytes, verifies the expected digest and size, and reports a
skip. Different content resolves to a new path rather than replacing the first
object.

## Explicit Backfill

Use backfill only when reprocessing an already delivered source object is an
accepted repair. A unique replay ID is mandatory:

```bash
python3 scripts/plan_ingestion_upload.py \
  --source data/sample_machine_events.csv \
  --destination-root dbfs:/FileStore/lakehouse_demo/raw_machine_events \
  --mode backfill \
  --replay-id incident-2026-08-22 \
  --output .ingestion/backfill-upload-plan.json

python3 scripts/upload_ingestion_plan.py \
  --target dev \
  --manifest .ingestion/backfill-upload-plan.json
```

The backfill object includes both the replay ID and the original content digest:

```text
machine-events__backfill__replay_incident-2026-08-22__sha256_<64 hex>.csv
```

This creates a new file identity without changing checkpoint state. Silver still
applies event-ID replay and conflict controls: identical payloads are replay
duplicates, while different payloads sharing an event ID are quarantined.

## Unity Catalog Volume Ingestion

For governed file storage, configure a Unity Catalog volume for the bronze task.
When `unity_catalog_volume` is set, the notebook resolves the raw source,
checkpoint and schema metadata paths as:

```text
/Volumes/<catalog>/<schema>/<unity_catalog_volume>/<volume_source_path>
/Volumes/<catalog>/<schema>/<unity_catalog_volume>/<volume_checkpoint_path>
/Volumes/<catalog>/<schema>/<unity_catalog_volume>/<volume_schema_location>
```

Run the workflow with a managed volume:

```bash
databricks bundle run -t dev \
  --var="unity_catalog_volume=lakehouse_demo_files" \
  lakehouse_demo_workflow
```

The bronze notebook creates the managed volume by default when
`create_unity_catalog_volume` is `true`. If your workspace uses pre-provisioned
volumes, set:

```bash
--var="create_unity_catalog_volume=false"
```

Plan and upload to the CLI-compatible `dbfs:/Volumes` path:

```bash
python3 scripts/plan_ingestion_upload.py \
  --source data/sample_machine_events.csv \
  --destination-root dbfs:/Volumes/main/lakehouse_demo/lakehouse_demo_files/raw_machine_events \
  --mode incremental \
  --output .ingestion/volume-upload-plan.json

python3 scripts/upload_ingestion_plan.py \
  --target dev \
  --manifest .ingestion/volume-upload-plan.json
```

The uploader never adds `--overwrite`. It bounds local files to 10 MiB, verifies
remote content with `databricks fs cat`, and fails if an immutable destination
contains different bytes.

## Azure ADLS Ingestion

The same bronze notebook can read from Azure Data Lake Storage Gen2 by setting
the bundle variables below. When `azure_storage_account` and `azure_container`
are set, the notebook resolves paths as:

```text
abfss://<azure_container>@<azure_storage_account>.dfs.core.windows.net/<azure_source_path>
```

Minimum Azure path variables:

```bash
databricks bundle run -t dev \
  --var="azure_storage_account=<storage-account>" \
  --var="azure_container=<container>" \
  --var="azure_source_path=lakehouse_demo/raw_machine_events" \
  lakehouse_demo_workflow
```

If the workspace already uses Unity Catalog external locations or a cluster
identity with access to the ADLS path, no secret variables are required. For
service-principal OAuth, store the client secret in a Databricks secret scope and
also set:

```bash
--var="azure_tenant_id=<tenant-id>"
--var="azure_client_id=<client-id>"
--var="azure_client_secret_scope=<secret-scope>"
--var="azure_client_secret_key=<secret-key>"
```

The repository uploader targets `dbfs:/` paths. For direct ADLS delivery, use an
object-store-native uploader that implements the same immutable object grammar,
full content digest, no-overwrite rule and explicit replay ID. Do not substitute
an ADLS overwrite behind an existing Auto Loader path.

## Run Order

Run these notebooks in order:

1. `notebooks/01_bronze_ingest.py`
2. `notebooks/02_silver_transform.py`
3. `notebooks/03_gold_models.py`
4. `notebooks/04_quality_checks.py`
5. `notebooks/05_forecast_validation.py`

`notebooks/06_lakeflow_quality_expectations.py` is pipeline source code. Refresh
it through the Databricks bundle workflow or the deployed
`lakehouse_quality_expectations` pipeline resource rather than running it as a
standalone notebook.

Then run:

```text
sql/gold_reporting_queries.sql
```

The bronze notebook uses `availableNow`, so each run processes files available
when the notebook starts and then stops. Later runs pick up newly arrived
immutable files from the same landing directory.

## Workflow Job Deployment

This repository includes a Databricks bundle job configuration for running the
notebooks as a workflow:

- `databricks.yml` defines bundle variables and deployment targets.
- `resources/lakehouse_workflow.yml` defines the dependent notebook tasks and the shared job cluster.
- `resources/access_controls.yml` defines Unity Catalog grants for the schema and managed volume.
- `resources/sql_reporting.yml` defines a reporting SQL warehouse.

For automated deployment through GitHub Actions, see `docs/deployment.md`.

Authenticate the Databricks CLI before validating or deploying:

```bash
databricks auth login --host <workspace-url>
```

From the repository root, validate and deploy the development target:

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
```

Run the deployed workflow:

```bash
databricks bundle run -t dev lakehouse_demo_workflow
```

Refresh only the quality-expectations pipeline:

```bash
databricks bundle run -t dev lakehouse_quality_expectations
```

The default schedule is paused. Change `schedule_pause_status` to `UNPAUSED`
only after the workflow has run successfully in your workspace.

The shared job cluster defaults to `spark_version` `15.4.x-scala2.12` and
`node_type_id` `i3.xlarge`. Override `node_type_id` for Azure, Google Cloud or
smaller workspace policies.

## Expected Result

After a successful run, the schema contains:

- `bronze_machine_events`
- `silver_machine_events`
- `silver_quarantine_machine_events`
- `gold_machine_uptime`
- `gold_failure_events`
- `gold_maintenance_costs`
- `gold_parts_usage`
- `gold_client_asset_summary`
- `quality_check_results`
- `quality_metric_history`
- `gold_downtime_forecast_validation`
- `gold_downtime_forecast`
- `quality_expectation_silver_machine_events`
- `quality_expectation_gold_machine_uptime`
- `quality_expectation_downtime_forecast`
- `quality_expectation_event_log`
