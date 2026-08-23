# Change Brief: Separate bundle deployment from lakehouse runtime identity

## Problem

The accepted bundle used `lakehouse-demo-ci` for GitHub OIDC deployment and, by
omission, allowed jobs and pipelines to inherit their creator as the effective
execution identity. That couples control-plane deployment permissions to data
processing and makes it difficult to prove that the deployer cannot read or
modify curated data.

## Outcome

- Introduce `lakehouse-demo-runtime` as a distinct bundle variable.
- Configure the Lakeflow Job and Spark Declarative Pipeline with explicit
  resource-level `run_as` values.
- Keep `lakehouse-demo-ci` as resource manager and query publisher.
- Move schema `SELECT` and `MODIFY` privileges to the runtime principal.
- Grant the runtime principal the catalog, schema and volume access needed for
  notebook and pipeline execution.
- Record allowed and denied capabilities in a machine-readable contract.
- Document the Databricks Service Principal User bootstrap relationship and the
  live denied-action evidence still required.

## Non-goals

- This change does not create either service principal in Databricks.
- It does not grant account-admin or workspace-admin rights.
- It does not execute a job, pipeline, bundle plan or denied-action test.
- It does not introduce a dedicated fixture-loader identity. The bounded
  synthetic upload remains a documented deployment-principal exception.
- It does not transfer ownership of existing deployed jobs or pipelines without
  an authenticated bundle deployment.

## Security boundaries

The deployment principal may manage the bundle, project grants and saved query
assets. It must not run the data workflow as itself or receive curated table
`SELECT`/`MODIFY` through the schema grant contract.

The runtime principal may process project data and update project tables and
Auto Loader state. It must not deploy bundles, manage resource permissions or
change GitHub/Databricks federation settings.

The deployment principal needs the Service Principal User role on the runtime
principal solely to assign `run_as`. Failure to assign the runtime identity must
be fixed at that relationship rather than by granting administrator access.

## Compatibility and migration

The first deployment changes the effective Run as identity of the existing job
and expectations pipeline. Before apply, inspect the authenticated plan for
those changes and verify that the runtime principal has access to the target
catalog, schema, volume, SQL functions and any secret scopes used by ingestion.

The job and pipeline names, task graph, table names and schedules remain
unchanged.

## Failure and rollback

A missing runtime service principal or missing Service Principal User relation
must block deployment. A missing runtime data privilege should fail the
development execution without granting data access to the deployer.

Source rollback is a revert of the squash commit. Runtime rollback restores the
previous job and pipeline Run as identities through a reviewed bundle change and
then reconciles any runs executed under the wrong principal.

## Validation

Repository validation checks:

- distinct principal defaults;
- explicit job and pipeline `run_as`;
- deployer management versus runtime execution permissions;
- absence of deployer schema `SELECT`/`MODIFY`;
- runtime catalog/schema/volume grants;
- required and denied external evidence.

Authenticated development evidence remains mandatory before production apply.
