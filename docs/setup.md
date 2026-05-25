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

## Sample Data

The bronze notebook uses Auto Loader and expects CSV files in this default landing directory:

```text
dbfs:/FileStore/lakehouse_demo/raw_machine_events/
```

Upload the local sample file so it lands at:

```text
dbfs:/FileStore/lakehouse_demo/raw_machine_events/sample_machine_events.csv
```

If you upload files somewhere else, change the `source_path` widget in `01_bronze_ingest.py`.

The bronze notebook also uses these default Auto Loader state locations:

```text
dbfs:/FileStore/lakehouse_demo/_checkpoints/bronze_machine_events
dbfs:/FileStore/lakehouse_demo/_schemas/bronze_machine_events
```

Keep these paths persistent and unique to the bronze stream. Auto Loader uses the checkpoint to skip files it has already processed. To replay the same landing files from scratch, use a new checkpoint path or clear both the target bronze table and the checkpoint/schema paths.

## Azure ADLS Ingestion

The same bronze notebook can read from Azure Data Lake Storage Gen2 by setting the bundle variables below. When `azure_storage_account` and `azure_container` are set, the notebook resolves paths as:

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

If the workspace already uses Unity Catalog external locations or a cluster identity with access to the ADLS path, no secret variables are required. For service-principal OAuth, store the client secret in a Databricks secret scope and also set:

```bash
--var="azure_tenant_id=<tenant-id>"
--var="azure_client_id=<client-id>"
--var="azure_client_secret_scope=<secret-scope>"
--var="azure_client_secret_key=<secret-key>"
```

The sample increment file is:

```text
data/increments/machine_events_increment_2026_04_03.csv
```

Drop it into the same DBFS or ADLS landing directory after the first run to validate that Auto Loader picks up only the new file on the next run.

## Run Order

Run these notebooks in order:

1. `notebooks/01_bronze_ingest.py`
2. `notebooks/02_silver_transform.py`
3. `notebooks/03_gold_models.py`
4. `notebooks/04_quality_checks.py`

Then run:

```text
sql/gold_reporting_queries.sql
```

The bronze notebook uses `availableNow`, so each run processes files available when the notebook starts and then stops. Later runs pick up newly arrived files from the same landing directory.

## Workflow Job Deployment

This repository includes a Databricks bundle job configuration for running the notebooks as a workflow:

- `databricks.yml` defines bundle variables and deployment targets.
- `resources/lakehouse_workflow.yml` defines the dependent notebook tasks and the shared job cluster.

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

The default schedule is paused. Change `schedule_pause_status` to `UNPAUSED` only after the workflow has run successfully in your workspace.

The shared job cluster defaults to `spark_version` `15.4.x-scala2.12` and `node_type_id` `i3.xlarge`. Override `node_type_id` for Azure, Google Cloud or smaller workspace policies.

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
