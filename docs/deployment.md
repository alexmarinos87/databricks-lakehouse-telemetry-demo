# Deployment

This project deploys Databricks resources with Databricks Asset Bundles and GitHub Actions.

## Recommended Pattern

Use Databricks-native deployment for this repository:

```text
GitHub Actions
  -> Dockerized local validation
  -> Databricks bundle validate and plan
  -> Databricks bundle deploy
  -> optional sample-data upload
  -> optional workflow run
  -> SQL Query publication
```

Kubernetes is not part of this deployment because the project does not run a long-lived external service. Docker is used only to make the validation environment repeatable in CI.

## GitHub Environments

Create two GitHub environments:

- `dev`
- `prod`

Require reviewers on the `prod` environment. This gives the production deployment manual approval without adding approval logic to the workflow file.

## GitHub Secrets

Use a Databricks service principal for deployment. Store these GitHub repository or environment secrets:

```text
DATABRICKS_HOST
DATABRICKS_CLIENT_ID
DATABRICKS_CLIENT_SECRET
```

The service principal should be a member of the deployment group configured by `DATABRICKS_CI_SERVICE_PRINCIPAL`.

## GitHub Variables

The workflow uses separate catalog, schema and volume settings for each target. Set these repository or environment variables when the defaults do not match the workspace:

```text
DATABRICKS_DEV_CATALOG=main
DATABRICKS_DEV_SCHEMA=lakehouse_demo_dev
DATABRICKS_DEV_VOLUME=lakehouse_demo_dev_files

DATABRICKS_PROD_CATALOG=main
DATABRICKS_PROD_SCHEMA=lakehouse_demo_prod
DATABRICKS_PROD_VOLUME=lakehouse_demo_prod_files

DATABRICKS_NODE_TYPE_ID=i3.xlarge
DATABRICKS_ADMIN_GROUP=lakehouse-demo-admins
DATABRICKS_ENGINEER_GROUP=lakehouse-demo-engineers
DATABRICKS_ANALYST_GROUP=lakehouse-demo-analysts
DATABRICKS_CI_SERVICE_PRINCIPAL=lakehouse-demo-ci
```

`DATABRICKS_CATALOG` remains an optional shared fallback for the two catalog variables. The workflow deliberately does not fall back to the former shared `DATABRICKS_SCHEMA` or `DATABRICKS_VOLUME` variables because that could make development and production write to the same schema or managed volume.

## Target Isolation Contract

The committed bundle defaults keep development and production writable state separate:

| State surface | Development | Production |
| --- | --- | --- |
| Schema | `lakehouse_demo_dev` | `lakehouse_demo_prod` |
| Managed volume | `lakehouse_demo_dev_files` | `lakehouse_demo_prod_files` |
| DBFS source root | `dbfs:/FileStore/lakehouse_demo/dev/` | `dbfs:/FileStore/lakehouse_demo/prod/` |
| Direct ADLS root | `lakehouse_demo/dev/` | `lakehouse_demo/prod/` |
| Pipeline mode | Development | Production |

The workflow selects the matching target-specific catalog, schema and volume before `validate`, `plan`, `deploy`, sample upload, workflow execution, grants and SQL query publication. Command-line bundle variables have higher precedence than target defaults, so the workflow values must remain target-specific.

Development mode normally adds a user-specific resource-name prefix. This bundle explicitly clears that prefix because its post-deploy grant and query helpers resolve the deterministic resource names already containing `${bundle.target}`. This makes the CI development deployment a shared, deterministic target rather than a per-developer sandbox. Personal experiments should use a separate target instead of reusing `dev`.

The declarative quality pipeline uses the `ADVANCED` edition because it defines expectations. Its development flag is controlled by the target presets rather than being fixed in the resource definition.

Static repository tests verify this contract. They do not prove the effective Databricks plan, workspace permissions, existing resource state or a successful pipeline refresh. Before the first deployment after this change, inspect both rendered plans and confirm that no target override or GitHub variable collapses the two writable namespaces.

## Least-Privilege Model

Create these Databricks groups:

```text
lakehouse-demo-admins
lakehouse-demo-engineers
lakehouse-demo-analysts
```

Use the service principal only for deployment automation. Do not use a personal token for CI/CD.

The bundle manages:

- Job permissions in `resources/lakehouse_workflow.yml`.
- Pipeline permissions in `resources/lakehouse_quality_expectations.yml`.
- SQL warehouse permissions in `resources/sql_reporting.yml`.
- Schema and volume grants in `resources/access_controls.yml`.
- Reporting table grants in `scripts/apply_uc_grants.py` after tables are created.
- Saved SQL Query permissions through `scripts/upsert_reporting_queries.py`.

The first deployment still needs a workspace administrator to bootstrap the deployment service principal with enough rights to create or manage the target catalog objects. At minimum, grant the service principal:

```text
USE CATALOG on the target catalog
CREATE SCHEMA on the target catalog when the schema does not exist
MANAGE on the target schema when the schema already exists and grants will be managed by deployment
```

After that bootstrap, the bundle and post-deploy scripts keep project permissions in code.

## Deploy Flow

Pull requests and pushes run Dockerized local checks. Pushes to `main` deploy the `dev` target.

Production is manual:

1. Open the GitHub Actions workflow named `Deploy Databricks Bundle`.
2. Click `Run workflow`.
3. Select `prod`.
4. Choose whether to upload sample data and run the lakehouse workflow.
5. Approve the `prod` environment deployment.

## Databricks Results

After deployment, Databricks assets appear in their product areas:

- Jobs: `Jobs & Pipelines > Jobs`
- Lakeflow pipelines: `Jobs & Pipelines > Pipelines`
- SQL warehouse: `SQL Warehouses`
- Saved SQL queries: `SQL > Queries`
- Schema, volume and tables: `Catalog`

The Workspace Git folder remains source code only.
