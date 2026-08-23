# Databricks deployment and runtime identity boundary

## Purpose

The GitHub OIDC deployment principal manages bundle resources. It is not the
identity that processes telemetry, owns checkpoint progress, or writes runtime
Delta outputs. Jobs and the Lakeflow quality pipeline use a distinct service
principal through their `run_as` settings.

This separation limits the effect of either credential boundary:

```text
GitHub OIDC deployer
  -> validate and plan
  -> create or update bundle resources
  -> manage resource permissions and reporting assets
  -> trigger an already deployed workflow

Databricks runtime principal
  -> read immutable source objects
  -> update Auto Loader checkpoint and schema state
  -> create, read and modify project tables/views
  -> execute notebook tasks and the quality pipeline
```

## Required configuration

Configure these non-password values in all plan and apply environments:

```text
DATABRICKS_CLIENT_ID
DATABRICKS_RUNTIME_CLIENT_ID
```

`DATABRICKS_CLIENT_ID` identifies the deployment service principal.
`DATABRICKS_RUNTIME_CLIENT_ID` identifies the distinct runtime service
principal and is passed to the bundle variable
`runtime_service_principal_name`.

For optional synthetic upload, the workflow temporarily authenticates as the
runtime identity, verifies it with `capture_databricks_plan.py --mode identity`,
and retains the fingerprint evidence before writing to the source volume. The
deployment identity no longer receives volume read/write privileges from the
bundle grants.

The deployer must have permission to assign the runtime service principal as a
job and pipeline run-as identity. That external entitlement is a bootstrap
requirement; it is not replaced with workspace-admin access.

## Privilege matrix

| Capability | Deployer | Runtime |
| --- | --- | --- |
| Bundle validate/plan/deploy | Required | Denied |
| Manage job/pipeline/warehouse permissions | Required | Denied |
| Publish governed SQL queries and grants | Required | Denied |
| Job and pipeline `run_as` | Denied | Required |
| Use project schema | Metadata/lifecycle only | Required |
| Create and modify runtime tables | Denied after bootstrap | Required |
| Read/write source and checkpoint volume | Denied | Required |
| Manage schema or volume | Required | Denied |
| Static client secret | Prohibited | Prohibited |

The deployer retains `MANAGE`, `USE_SCHEMA`, `CREATE_TABLE`, and
`CREATE_VOLUME` at schema level so it can reconcile bundle-owned resources and
permissions. It does not receive `MODIFY`, `SELECT`, `READ_VOLUME`, or
`WRITE_VOLUME` from `resources/access_controls.yml`. The runtime principal gets
the data-plane privileges but no `MANAGE` privilege.

## Evidence requirements

Repository tests prove configuration intent only. Before a controlled runtime
execution, issue #44/G7 must retain:

1. An authenticated plan showing the runtime application ID on both run-as
   resources.
2. Effective job and pipeline permissions showing the deployer can manage and
   the runtime identity cannot.
3. Effective Unity Catalog grants showing the runtime data-plane privileges and
   absence of runtime `MANAGE` privileges.
4. Effective volume grants showing the deployer lacks read/write and runtime
   lacks manage.
5. A successful runtime identity preflight for optional upload.
6. Safe denied-capability evidence. Prefer read-only effective-permission
   exports; do not attempt destructive actions merely to prove denial.

The machine-readable expected policy is
`governance/runtime_identity_policy.json`.

## Failure and rollback

- Missing runtime client ID or an inability to assign `run_as` must block the
  plan/apply workflow. Do not fall back to the deployer as runtime.
- If a job or pipeline executes as the deployer, stop the workflow and correct
  the resource configuration before processing data.
- If runtime lacks a required data privilege, grant only the named privilege on
  the target project securable and rerun the plan. Do not add workspace-admin.
- If deployer unexpectedly has data-plane access, remove that grant and retain
  the before/after effective-permission evidence.
- Source rollback is a revert of the squash commit. Runtime rollback must also
  restore the prior job/pipeline run-as settings and re-review any output written
  under the wrong principal.

No repository test, plan, or identity fingerprint is proof that the external
Databricks permissions are effective until the workspace evidence is captured.
