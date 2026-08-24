# Change Brief: Govern Saved-Query Ownership And Execution

## Problem

Repository reporting queries currently use `run_as_mode = OWNER` while engineers receive `CAN_EDIT`. A user able to modify SQL can therefore change a query that a legacy alert or workflow might execute using the owner’s credentials. The publisher also sets permissions without reading the query or ACL back, so ownership-era drift and unexpected explicit principals can remain undetected.

## Outcome

- Publish every governed query with `run_as_mode = VIEWER`.
- Treat repository SQL as the source of truth.
- Limit engineers and analysts to `CAN_RUN`.
- Retain `CAN_MANAGE` only for administrators and the publishing service principal.
- Verify the authenticated publisher application before any query operation.
- Verify query definition before replacing permissions.
- Verify explicit ACLs after replacement and reject unexpected groups or service principals.
- Document administrative ownership transfer, legacy owner-run migration and rollback.

## Acceptance Criteria

- A strict JSON policy defines execution mode, editing boundary, ownership lifecycle and the permission matrix.
- `OWNER` and `CAN_EDIT` are rejected by policy validation.
- The publisher requires the current Databricks application ID to match the declared publisher ID.
- Existing owner-run queries are updated to `VIEWER` before permissions change.
- Query read-back must match repository SQL, warehouse, catalog, schema, parent path, display name, execution mode and tags.
- Permissions read-back must contain the four expected principals at the exact governed levels.
- Engineers and analysts cannot receive `CAN_EDIT` or `CAN_MANAGE`.
- Unexpected explicit groups and service principals fail publication.
- Duplicate active display names still fail closed and are not deleted automatically.
- Timeouts, pagination, asset-size and sanitized-error controls remain intact.
- Unit and source contracts cover identity mismatch, owner-run migration, state drift, ACL drift and rollback documentation.

## Non-Goals

- This increment does not transfer a live query owner.
- It does not authenticate to a workspace, publish a query or mutate permissions.
- It does not prove inherited workspace access or warehouse `CAN_USE` permissions.
- It does not create alerts, schedules, dashboards or jobs from saved queries.
- It does not grant workspace editing to bypass repository review.
- It does not automatically delete duplicate, abandoned or legacy queries.

## Security Boundary

Viewer-run execution removes owner credentials from the intended query-execution path. Administrative ownership remains necessary for lifecycle management, but it is not treated as delegated data access.

A malicious or mistaken workspace edit is overwritten only when the publisher can first prove its declared identity and the exact read-back state. The publisher does not include SQL text, principal names, command arguments or raw provider diagnostics in broad failure messages.

## Migration

The publisher updates each existing active query to `VIEWER`, verifies that state, then replaces its explicit ACL. If the update or verification fails, permissions are not changed.

Before disabling an owner, a workspace administrator transfers administrative ownership where required and records the affected query IDs, old and new owner, execution mode and effective permissions. The repository does not automate ownership transfer because that is a separate administrator action and provider capability boundary.

## Failure And Recovery

- Publisher identity mismatch: stop before warehouse or query inventory calls.
- Duplicate active name: stop without choosing or deleting a query.
- Query definition mismatch: stop before ACL replacement.
- Permission mismatch: stop and retain the query ID for administrator repair.
- Unexpected explicit principal: stop; do not silently preserve or remove access without review.

Rollback republishes the preceding accepted SQL while retaining `VIEWER` mode and the governed permission matrix. Reverting source alone does not restore or transfer workspace ownership.

## Evidence Boundary

Repository tests prove policy and API-payload handling. Effective query ownership, viewer credentials, inherited permissions, warehouse access and ownership transfer remain authenticated Databricks evidence.
