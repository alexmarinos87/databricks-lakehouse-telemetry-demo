# Change Brief: <short outcome>

Copy this template into the issue, task description or pull request before implementation.

## Problem

What user, operational or engineering problem are we solving? Include evidence rather than only a proposed solution.

## Acceptance Criteria

- [ ] Observable behaviour that must work.
- [ ] Failure behaviour that must be proven.
- [ ] Required tests, validation or reconciliation evidence.

## Non-Goals

- Work deliberately excluded from this change.
- Adjacent cleanup that should remain untouched.

## Architecture Boundaries

- Components and files allowed to change:
- Interfaces, schemas or behaviours that must remain compatible:
- Maximum intended scope or reason a larger change is justified:

## Data, State And Side Effects

- Inputs and outputs:
- Table grain, keys and null rules:
- Late-data, schema-evolution and deduplication rules:
- Checkpoint, replay and source-file identity strategy:
- Read/write behaviour:
- Idempotency, retry and partial-failure behaviour:
- Backfill or migration requirements:

## Security, Permissions And Cost

- Identities, grants or secrets involved:
- Least-privilege expectation:
- Compute, storage or external-service cost impact:
- Expected runs/day, compute SKU/runtime delta, storage growth and cost ceiling (or N/A):

## Failure And Recovery

- Expected failure modes:
- Detection and observability:
- Rollback or forward-recovery procedure:
- Data recovery or reconciliation procedure:
- Pre-change Delta versions/snapshots and restore order (or N/A):
- Checkpoint and permission/query-ACL rollback (or N/A):
- Recovery validation and required RTO/RPO (or N/A):

## Validation Plan

- Automated checks:
- Runtime/integration checks:
- Manual inspection points:
- Checks that cannot run in the current environment:
