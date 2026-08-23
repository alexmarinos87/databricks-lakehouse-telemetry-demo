# Deployment and runtime identity model

The repository separates the identity that changes Databricks resources from the
identity that processes lakehouse data.

## Identities

| Identity | Default name | Purpose |
| --- | --- | --- |
| Deployment | `lakehouse-demo-ci` | GitHub OIDC plan/apply, bundle ownership, grants and saved-query publication |
| Runtime | `lakehouse-demo-runtime` | Lakeflow Job and Spark Declarative Pipeline execution |

`databricks.yml` exposes both values independently. The workflow job and quality
pipeline explicitly use the runtime service principal through resource-level
`run_as`. They do not inherit the bundle deployer as their execution identity.

The deployment principal needs the Databricks **Service Principal User** role on
the runtime principal so it can assign that service principal as `run_as`. That
role is an external bootstrap permission; it must not be replaced with workspace
administrator access.

## Capability matrix

The executable source contract is
`config/identity_privilege_contract.json`. Its important boundaries are:

### Deployment principal

Allowed:

- authenticate through the protected GitHub environments;
- validate, plan and deploy the accepted bundle;
- manage the project job, pipeline, schema, volume grants and saved queries;
- execute the bounded, content-addressed synthetic fixture upload.

Denied by design:

- run the lakehouse workflow or quality pipeline as itself;
- select curated tables;
- modify lakehouse tables;
- inspect Auto Loader checkpoint content.

### Runtime principal

Allowed:

- run the workflow and quality pipeline;
- read immutable input objects;
- write checkpoint and schema metadata;
- create, read and modify project tables and views.

Denied by design:

- deploy the bundle;
- manage job or pipeline permissions;
- change saved-query permissions;
- change GitHub environments or Databricks federation policies.

## Unity Catalog grants

The bundle-managed schema grants give the deployment principal `MANAGE`,
`USE_SCHEMA` and `CREATE_VOLUME`. It does not receive schema `SELECT` or
`MODIFY`.

The runtime principal receives `USE_SCHEMA`, `CREATE_TABLE`, `CREATE_VOLUME`,
`SELECT`, `MODIFY`, `READ_VOLUME` and `WRITE_VOLUME`. The bounded post-deploy
grant helper supplies both identities with `USE CATALOG` and `USE SCHEMA`, and
supplies the runtime principal with volume access.

The deployment principal temporarily retains `READ_VOLUME` and `WRITE_VOLUME`
on the project volume solely for the optional synthetic fixture upload. This is
a documented exception rather than an implied right to read or mutate curated
tables. A future loader-identity increment can remove it without changing the
job and pipeline `run_as` contract.

## Saved SQL query boundary

Saved queries are deployment-managed assets. Interactive executions in the new
Databricks SQL editor use viewer credentials, so analysts require their own
warehouse and Unity Catalog access. The project does not schedule saved queries
as the deployment principal.

## External setup

Before the first authenticated plan:

1. Create `lakehouse-demo-ci` and `lakehouse-demo-runtime` as distinct service
   principals.
2. Configure GitHub OIDC only for the deployment principal subjects documented
   in `docs/external_bootstrap.md`.
3. Grant the deployment principal the Service Principal User role on the
   runtime principal.
4. Bootstrap only the control-plane privileges required to deploy the bundle
   and manage project grants.
5. Grant the runtime principal no GitHub administration, bundle deployment or
   account-level federation administration rights.
6. Populate `DATABRICKS_RUNTIME_SERVICE_PRINCIPAL` only when the accepted
   runtime principal name differs from the repository default.

## Required live evidence

Repository tests prove configuration intent. The following denied and required
actions must still be demonstrated in a development workspace and attached to
issue #44 or the delivery queue:

- deployment principal can assign the runtime principal to the job and pipeline;
- runtime principal can execute the complete workflow and expectations pipeline;
- deployment principal is denied `SELECT` on a curated table;
- runtime principal is denied bundle deployment or resource permission changes;
- the deployed job and pipeline report `lakehouse-demo-runtime` as their
  effective Run as identity;
- the deployment principal cannot change the job to run as itself without an
  explicitly reviewed bundle change.

A successful repository CI run or bundle plan is not live denied-action
evidence.

## Failure and rollback

If deployment fails because the deployer cannot assign `run_as`, correct the
Service Principal User relationship rather than making the deployer a workspace
admin or reverting to deployer execution.

If the runtime principal lacks a required table or volume privilege, add the
smallest privilege to the runtime role and rerun the development evidence. Do
not grant data privileges to the deployer as a shortcut.

Source rollback is a revert of the eventual squash commit. After deployment,
rollback must also restore the prior job and pipeline Run as identities and
reconcile any work that executed under the wrong principal.
