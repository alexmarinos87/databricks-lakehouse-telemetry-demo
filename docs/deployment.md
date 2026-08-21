# Deployment

This project deploys Databricks resources with Databricks Asset Bundles and GitHub Actions.

## Recommended Pattern

Use Databricks-native deployment for this repository:

```text
Pull request / main update
  -> Dockerized repository validation only

Manual deployment dispatch, apply_changes=false
  -> Dockerized repository validation
  -> GitHub OIDC token request
  -> Databricks service-principal identity preflight
  -> Databricks bundle validate
  -> Databricks bundle plan
  -> retained plan evidence and human review

Manual deployment dispatch, apply_changes=true
  -> the same authenticated validation and plan path
  -> environment approval for the apply job
  -> fresh identity preflight
  -> Databricks bundle deploy
  -> optional sample-data upload
  -> optional workflow run
  -> SQL Query publication
```

Kubernetes is not part of this deployment because the project does not run a long-lived external service. Docker is used only to make the validation environment repeatable in CI.

A merge to `main` never deploys Databricks resources. Validation and planning are explicit workflow-dispatch operations, and applying changes requires the separate `apply_changes` input.

## GitHub Environments

Create two GitHub environments:

- `dev`
- `prod`

Require reviewers on the `prod` environment. Require a reviewer on `dev` as well when it is a shared integration workspace. The deployment jobs use the selected environment in their GitHub OIDC subject, so the environment names are part of the Databricks trust policy and must not be renamed casually.

For this repository, the expected GitHub OIDC subjects are:

```text
repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:dev
repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:prod
```

Configure a separate Databricks federation policy for each environment, restricted to the repository, environment subject and audience prescribed by the Databricks workload identity federation setup. Do not use a broad repository-owner or organization-wide subject when an environment-qualified subject is available.

## Workload Identity Federation

The workflow uses short-lived GitHub OIDC authentication through Databricks unified authentication:

```text
DATABRICKS_AUTH_TYPE=github-oidc
```

The four Databricks plan/apply jobs have only these additional GitHub permissions:

```yaml
permissions:
  contents: read
  id-token: write
```

`id-token: write` allows the job to request a short-lived GitHub OIDC token. It does not grant repository write access. The Databricks federation policy remains responsible for deciding whether that token can act as the deployment service principal.

Follow the official Databricks GitHub Actions workload identity federation procedure to:

1. Create or select the Databricks service principal used by this project.
2. Add the environment-qualified federation policies shown above.
3. Configure the provider audience exactly as required by the Databricks account or workspace policy.
4. Grant only the workspace and Unity Catalog privileges required by the target deployment.
5. Run a plan-only dispatch and retain the successful identity and plan evidence before approving any apply job.

The repository does not create the external federation policy. That bootstrap is an administrator-controlled Databricks and GitHub settings operation.

## GitHub Environment Variables

Configure these variables independently in the `dev` and `prod` GitHub environments:

```text
DATABRICKS_HOST
DATABRICKS_CLIENT_ID
```

`DATABRICKS_CLIENT_ID` is the application ID of the Databricks service principal trusted by that environment's federation policy. The workflow also accepts the same names as environment secrets for migration compatibility, but neither value is a password.

Do not map `DATABRICKS_CLIENT_SECRET` into the workflow. The identity preflight fails closed when a static client secret is present, preventing accidental fallback from GitHub OIDC to a long-lived credential.

The service principal should be a member of, or resolve to, the deployment principal configured by `DATABRICKS_CI_SERVICE_PRINCIPAL`.

## Other GitHub Variables

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

## Authenticated Plan Evidence

`scripts/capture_databricks_plan.py` owns the plan-only control boundary. It:

1. Requires the GitHub Actions OIDC request context and `DATABRICKS_AUTH_TYPE=github-oidc`.
2. Rejects a mapped static client secret.
3. Calls `databricks current-user me` with a finite deadline.
4. Verifies that the authenticated application ID matches `DATABRICKS_CLIENT_ID`.
5. Runs bounded `bundle validate` and `bundle plan` commands for the selected target.
6. Writes successful command output, hashes, GitHub run provenance and identity fingerprints to an evidence directory.
7. Writes only bounded output metadata when a command fails; raw failed provider output is not copied into broad logs or the evidence JSON.

Each plan job publishes a GitHub step summary and retains the evidence directory as an immutable-name workflow artifact for 14 days. The artifact name includes the selected target, exact Git commit and workflow attempt. It contains no OIDC token, client secret or raw principal identifier.

A successful evidence artifact proves that the selected commit authenticated as the configured service principal and produced a validation/plan result at that time. It does not prove that the workspace remained unchanged afterwards, that an apply will succeed, or that the plan is safe without human review.

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

Static repository tests verify this contract. They do not prove the effective Databricks plan, workspace permissions, existing resource state or a successful pipeline refresh. Before the first deployment after this change, inspect both authenticated plans and confirm that no target override or GitHub variable collapses the two writable namespaces.

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

Pull requests and pushes run Dockerized repository checks only. Databricks validation, planning and deployment are manual:

1. Open the GitHub Actions workflow named `Deploy Databricks Bundle`.
2. Click `Run workflow` and select the exact branch or commit intended for review.
3. Select `dev` or `prod`.
4. Leave `apply_changes` disabled and run the workflow.
5. Review the completed `bundle validate` and `bundle plan` artifact and step summary for the selected target.
6. Resolve every unexpected create, replace, delete, permission or target-isolation change before proceeding.
7. Re-run the workflow for the accepted commit and target with `apply_changes` enabled.
8. Review the fresh plan produced by that apply-enabled run before approving its environment-protected deploy job.
9. Choose whether to upload sample data and run the lakehouse workflow. Both options default to disabled.
10. Complete the configured environment approval only when the fresh plan is acceptable.

An apply-enabled dispatch always runs the authenticated plan job first, and the deploy job has a `needs` dependency on that successful job. GitHub therefore keeps the plan, selected commit, target and approval request inside the same workflow run. A separate earlier plan-only dispatch remains useful review evidence, but it is not treated as authorization for a later commit.

The evidence hashes do not cryptographically bind mutable external Databricks state after planning. An environment approval, a fresh same-run plan and a final identity preflight remain required human and runtime controls.

## Failure And Recovery

- Missing environment variables, missing OIDC context, an inactive principal, an unexpected principal or a static client secret fail before validation or deployment.
- Identity, validation and plan child processes have finite deadlines.
- Failed command output is represented by bounded byte counts and SHA-256 hashes rather than copied into the public job summary.
- A failed plan job blocks the dependent deploy job.
- A failed apply can leave partially changed Databricks state; inspect the workflow and Databricks deployment history, reconcile against the accepted plan and use a forward fix or reviewed bundle rollback.
- Sample upload, workflow execution, table grants and query publication remain explicit post-deploy operations and can fail after the bundle itself succeeds. Their existing bounded helpers and run evidence must be reviewed independently.

## Databricks Results

After deployment, Databricks assets appear in their product areas:

- Jobs: `Jobs & Pipelines > Jobs`
- Lakeflow pipelines: `Jobs & Pipelines > Pipelines`
- SQL warehouse: `SQL Warehouses`
- Saved SQL queries: `SQL > Queries`
- Schema, volume and tables: `Catalog`

The Workspace Git folder remains source code only.
