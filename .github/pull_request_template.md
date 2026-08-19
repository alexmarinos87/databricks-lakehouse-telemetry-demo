## Problem And Outcome

What problem does this solve, and what observable outcome should reviewers expect?

## Change Brief

- Acceptance criteria:
- Non-goals:
- Architecture boundaries:

## Architecture And Data Flow

Describe the important control/data paths, table grain and keys, interfaces, and compatibility decisions.

- Late data, schema evolution and deduplication:
- Checkpoint, replay and source-file identity strategy:

## State And Side Effects

- Data or infrastructure written:
- Idempotency and retry behaviour:
- External systems or people affected:

## Failure, Recovery And Rollback

- Expected failure modes and detection:
- Partial-failure behaviour:
- Rollback or forward-recovery steps:
- Backfill or reconciliation needs:
- Pre-change Delta versions/snapshots and restore order (or N/A):
- Checkpoint and permission/query-ACL rollback (or N/A):
- Recovery validation and required RTO/RPO (or N/A):

## Security, Permissions And Cost

- Identity, grant or secret changes:
- Trust-boundary implications:
- Compute, storage or service-cost impact:
- Runs/day, compute SKU/runtime delta, storage growth and cost ceiling (or N/A):

## Validation Evidence

| Check | Result | Evidence or limitation |
| --- | --- | --- |
| `scripts/run_acceptance_checks.sh` | Not run | |
| Runtime/integration validation | Not run | |
| Security/static analysis | Not run | |

## Independent Review Findings

List correctness and adversarial-review findings with severity, disposition and evidence. Every deferred finding needs a rationale, owner and target date. Agent approval is evidence, not acceptance.

## Highest-Value Human Inspection Points

Replace or augment the generated heuristic with up to ten exact `path:line` locations and a reason to inspect each.

1. `path:line` — reason to inspect.

## Unresolved Questions Or Debt

- Pending review. Replace only after actively checking for unresolved debt.

## Human Acceptance — Human Reviewer Only

- [ ] I understand the architecture, state changes and important data/control paths.
- [ ] I reviewed failure modes, rollback, permissions and operational/cost implications.
- [ ] The evidence is proportionate, and limitations are explicitly recorded.
- [ ] I accept this exact change for merge.

Decision: **Pending**
