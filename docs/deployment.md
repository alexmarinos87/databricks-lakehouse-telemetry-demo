# Deployment

This project deploys Databricks resources with Databricks Asset Bundles and a manual GitHub Actions workflow.

## Recommended Pattern

```text
Pull request or merge to main
  -> Dockerized repository validation only

Manual dispatch, apply_changes=false
  -> repository validation
  -> GitHub OIDC token request
  -> Databricks service-principal identity preflight
  -> bounded bundle validate
  -> bounded bundle plan
  -> retained exact-commit evidence
  -> human plan review

Manual dispatch, apply_changes=true
  -> the same authenticated plan path
  -> environment approval
  -> fresh identity preflight
  -> bundle deploy
  -> optional immutable sample upload
  -> optional workflow execution
  -> relation grants and saved-query publication
```

A merge to `main` never deploys Databricks resources. The workflow is `workflow_dispatch` only. Leave `apply_changes` disabled for a plan-only run; an apply job cannot start unless the same workflow run's target-specific plan job succeeds.

Docker is used only for repeatable repository validation. Kubernetes is not part of this deployment because the project does not run a long-lived external service.

## GitHub Environments

Create four environments:

```text
dev-plan
prod-plan
dev
prod
```

`dev-plan` and `prod-plan` generate authenticated evidence before any apply approval. `dev` and `prod` protect the state-changing jobs. Require reviewers on `prod`; require a reviewer on `dev` as well when it is a shared integration workspace. Plan environments may omit reviewers so evidence can be generated before an apply decision, but all four environments must restrict deployment branches to `main`.

The workflow also fails when `GITHUB_REF` is not `refs/heads/main`, so environment branch rules are defense in depth rather than the only control.

Expected subjects:

```text
repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:dev-plan
repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:prod-plan
repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:dev
repo:alexmarinos87/databricks-lakehouse-telemetry-demo:environment:prod
```

Configure one Databricks workload-identity federation policy per subject and restrict it to the repository, environment-qualified subject, and Databricks audience required by the target account or workspace. The repository does not create this external trust relationship.

## Workload Identity Federation

The four Databricks plan/apply jobs use Databricks unified authentication:

```text
DATABRICKS_AUTH_TYPE=github-oidc
```

Each of those jobs has only these additional GitHub permissions:

```yaml
permissions:
  contents: read
  id-token: write
```

`id-token: write` permits a short-lived GitHub OIDC token request. It does not grant repository write access. Databricks remains responsible for accepting the token and mapping it to the configured service principal.

Configure these values independently in `dev-plan`, `prod-plan`, `dev`, and `prod`:

```text
DATABRICKS_HOST
DATABRICKS_CLIENT_ID
```

`DATABRICKS_CLIENT_ID` is the application ID of the service principal trusted by the environment's federation policy. The workflow accepts these names as environment variables or migration-compatible environment secrets, but neither is a password.

Do not configure or map `DATABRICKS_CLIENT_SECRET`. The identity preflight fails closed when a static client secret is present.

The deployment principal should resolve to `DATABRICKS_CI_SERVICE_PRINCIPAL` and should receive only the workspace and Unity Catalog privileges needed for the selected target.

## Other GitHub Variables

Target-specific defaults are:

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

`DATABRICKS_CATALOG` is an optional shared catalog fallback. The workflow deliberately does not use shared `DATABRICKS_SCHEMA` or `DATABRICKS_VOLUME` fallbacks because they could collapse development and production into one writable namespace.

## Authenticated Plan Evidence

`scripts/capture_databricks_plan.py` owns the OIDC identity and plan-evidence boundary. It:

1. Requires GitHub Actions OIDC context and `DATABRICKS_AUTH_TYPE=github-oidc`.
2. Rejects a mapped static client secret.
3. Calls `databricks current-user me` with a finite deadline.
4. Verifies that the authenticated application ID matches `DATABRICKS_CLIENT_ID`.
5. Runs bounded `bundle validate` and `bundle plan` commands for the selected target.
6. Writes successful output, output hashes, GitHub run provenance, and identity fingerprints to a bounded evidence directory.
7. Stores only byte counts and hashes when a failed provider response may contain sensitive information.

Plan jobs publish a step summary and retain the evidence directory for 14 days. Artifact names include the target, exact Git commit, and workflow attempt.

A successful artifact proves that the selected commit authenticated as the configured service principal and produced validation and plan output at that time. It does not prove that external workspace state remained unchanged, that apply will succeed, or that a plan is safe without human review.

The evidence does not cryptographically bind mutable Databricks state after planning. The same-run plan, environment approval, and fresh apply identity preflight remain necessary controls.

## Target Isolation Contract

| State surface | Development | Production |
| --- | --- | --- |
| Schema | `lakehouse_demo_dev` | `lakehouse_demo_prod` |
| Managed volume | `lakehouse_demo_dev_files` | `lakehouse_demo_prod_files` |
| DBFS source root | `dbfs:/FileStore/lakehouse_demo/dev/` | `dbfs:/FileStore/lakehouse_demo/prod/` |
| Direct ADLS root | `lakehouse_demo/dev/` | `lakehouse_demo/prod/` |
| Pipeline mode | Development | Production |

The workflow supplies the matching target-specific catalog, schema, and volume to the plan helper, bundle deploy, workflow run, grants, immutable uploader, and query publisher.

Static repository checks verify configuration intent. They do not prove the effective Databricks plan, existing resource state, effective permissions, Files API behavior, or successful runtime execution. Review the completed `bundle validate` and `bundle plan` evidence for both targets before first apply and after every material deployment change.

Development clears the normal per-user resource-name prefix because post-deploy helpers resolve deterministic names already containing the bundle target. Personal experiments should use a separate target rather than the shared `dev` target.

The expectation pipeline uses the `ADVANCED` edition, and target presets control development mode.

## Governed Sample Upload

Sample upload is optional and disabled by default. An apply-enabled dispatch can select:

```text
demo_dataset:
  initial
  increment-2026-04-03

ingestion_mode:
  incremental
  backfill

backfill_id:
  required for backfill
```

The workflow calls:

```text
scripts/plan_ingestion_upload.py
scripts/upload_ingestion_plan.py
```

It does not use a fixed landing filename, `--overwrite`, or a remove command. Incremental identity is the full content SHA-256. Backfill requires a bounded replay ID and produces a new object identity. Both modes reuse the established Auto Loader checkpoint.

Before first use, move legacy fixed-name files outside the watched landing root. Do not reset the checkpoint to replay an ordinary file. A checkpoint reset is a separately approved incident operation requiring Bronze and downstream reconciliation.

## Least-Privilege Model

Create:

```text
lakehouse-demo-admins
lakehouse-demo-engineers
lakehouse-demo-analysts
```

Use the service principal only for deployment automation. Do not use a personal token for CI/CD.

The repository manages:

- job permissions in `resources/lakehouse_workflow.yml`;
- pipeline permissions in `resources/lakehouse_quality_expectations.yml`;
- SQL warehouse permissions in `resources/sql_reporting.yml`;
- schema and volume grants in `resources/access_controls.yml`;
- runtime-created table, view, and materialized-view grants in `scripts/apply_uc_grants.py`;
- saved-query permissions in `scripts/upsert_reporting_queries.py`.

An administrator must bootstrap the service principal with the minimum rights required to create or manage the target catalog objects. Avoid a broad workspace-admin shortcut.

## Deploy Flow

1. Open `Deploy Databricks Bundle`.
2. Select the exact `main` commit to review.
3. Select `dev` or `prod`.
4. Leave `apply_changes` disabled.
5. Review the completed `bundle validate` and `bundle plan` artifact and summary.
6. Resolve unexpected creates, replacements, deletions, permissions, or target-isolation changes.
7. Re-run the accepted commit and target with `apply_changes` enabled.
8. Review the fresh same-run plan before granting environment approval.
9. Optionally select an immutable sample fixture and ingestion mode.
10. Optionally run the lakehouse workflow.
11. Grant environment approval only when the exact plan is acceptable.

An apply-enabled dispatch always runs its authenticated plan job first. The deploy job has a `needs` dependency on that successful job, keeping the selected target, commit, plan, and approval request in the same workflow run.

## Failure And Recovery

- Missing OIDC context, host/client configuration, an inactive or unexpected identity, or a static client secret fails before plan or apply.
- Identity, validation, plan, grant, query-publication, and upload child processes have finite deadlines.
- Failed plan output is represented by bounded metadata rather than echoed into the broad job summary.
- A failed plan blocks its deploy job.
- A failed apply may leave partial Databricks changes; reconcile against the accepted plan and use a reviewed forward fix or bundle rollback.
- Immutable upload, workflow execution, grants, and query publication are distinct post-deploy operations and may fail after the bundle itself succeeds.
- Forecast history migration and manifest-last recovery follow `docs/runbooks/forecast_publication_recovery.md`.

## Databricks Results

After deployment, assets appear under Jobs & Pipelines, SQL Warehouses, SQL Queries, and Catalog. The Workspace Git folder remains source code only.
