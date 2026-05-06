# Setup

## GitHub

Create the repository as private while building:

```text
databricks-lakehouse-jcb-demo
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

The bronze notebook expects this default path:

```text
dbfs:/FileStore/lakehouse_demo/sample_machine_events.csv
```

Upload the local sample file there:

```text
data/sample_machine_events.csv
```

If you upload the file somewhere else, change the `source_path` widget in `01_bronze_ingest.py`.

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
