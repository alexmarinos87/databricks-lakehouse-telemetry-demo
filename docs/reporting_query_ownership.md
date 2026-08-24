# Reporting Query Ownership And Execution Policy

## Decision

Repository-published Databricks SQL queries are governed source assets, not shared workspace drafts.

They use:

```text
run_as_mode = VIEWER
source_of_truth = repository
workspace_editing = admin_only
```

The owner is an administrative lifecycle concern. Owner credentials are never the intended data-access boundary for these queries. Interactive users and any job or alert integration must execute using the effective viewer or job-run identity.

## Permission matrix

| Principal | Permission | Rationale |
| --- | --- | --- |
| Administrators | `CAN_MANAGE` | Emergency repair, ownership transfer, deletion and permission recovery |
| Engineers | `CAN_RUN` | Execute and inspect governed output without changing repository SQL |
| Analysts | `CAN_RUN` | Execute governed reporting queries |
| Publishing service principal | `CAN_MANAGE` | Reconcile query definitions and explicit ACLs from the repository |

The publisher does not grant `CAN_EDIT` to engineers or analysts. Proposed SQL changes are reviewed through Git and republished from an accepted commit.

## Publication sequence

For each asset the publisher:

1. Verifies the current Databricks identity matches the declared publisher application ID.
2. Enumerates all accessible queries with bounded pagination and rejects duplicate active display names.
3. Creates or updates the query with `run_as_mode = VIEWER`.
4. Reads the query back and verifies its display name, SQL text, warehouse, catalog, schema, parent path, execution mode and repository tags.
5. Replaces explicit permissions with the governed matrix.
6. Reads permissions back and verifies the expected principals and levels.
7. Rejects unexpected explicit groups or service principals.

Query state is verified before permissions change, so a legacy owner-run query cannot retain broad editor access if migration to viewer execution failed.

## Ownership lifecycle

Databricks query ownership is not transferred automatically by this repository. Ownership transfer is a workspace-admin operation and is handled separately from query execution.

Before removing or disabling the current human owner, a workspace administrator must:

1. Identify every governed query from its repository tags and display-name prefix.
2. Transfer administrative ownership to another active user where required.
3. Re-run the publisher under the declared service-principal identity.
4. Confirm every query remains viewer-run and its explicit ACL matches policy.
5. Retain the before-and-after owner, query IDs, effective permissions and reviewer.

An ownership change must not change SQL text, execution mode, warehouse binding, target catalog/schema or explicit access levels.

## Legacy migration

An existing query with `run_as_mode = OWNER` is migrated by updating the query definition to `VIEWER` and verifying the result before replacing its ACL.

The publisher fails closed when:

- the authenticated application ID is not the declared publisher;
- multiple active queries share one governed display name;
- the query cannot be updated to viewer execution;
- the read-back definition differs from repository state;
- an expected permission is absent;
- an engineer or analyst receives edit/manage access;
- an unexpected explicit group or service principal remains on the query.

The publisher does not delete duplicate or legacy queries automatically. An administrator must preserve evidence, resolve ownership and retire the unwanted object through a separately reviewed action.

## Rollback

Source rollback is a revert of the eventual squash commit. Runtime rollback must not restore owner-run execution. If a publication introduces an invalid query definition, republish the preceding accepted SQL while retaining `VIEWER` mode and the same permission matrix.

If permissions cannot be reconciled, remove access through an administrator, preserve the query ID and ACL evidence, and stop publication until the policy can be re-applied. Do not grant editor access as a temporary workaround.

## Evidence boundary

Repository tests prove policy parsing, command construction, identity checks, query read-back validation and ACL normalization against representative API payloads. They do not prove effective Databricks ownership, inherited permissions, warehouse access or viewer-credential execution. Those require authenticated workspace evidence.
