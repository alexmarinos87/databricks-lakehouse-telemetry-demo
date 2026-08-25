# Change Brief: Plan guarded retention as a dry run

## Problem

The repository defines bounded retention expectations but intentionally has no
automatic cleanup job. The next useful source increment is a deterministic way to
turn real development inventory into a review package without creating a deletion
path.

Without a strict planner, a spreadsheet or free-form note could omit legal holds,
active incidents, recovery evidence, policy cutoffs, current versions, or the fact
that candidates came from different environments.

## Outcome

Add `scripts/plan_retention_dry_run.py`. It reads the repository retention policy
and one sanitized development inventory, computes the exact cutoff for every
required evidence family, validates recovery and version boundaries, and writes:

```text
retention-dry-run-plan.json
retention-dry-run-plan.md
```

Every output states:

```text
dry_run_only: true
execution_authorized: false
```

A ready report is review evidence only and never authorizes mutation.

## Acceptance criteria

- Only `target: dev` and the exact public repository are accepted.
- Every policy retention key must be represented exactly once.
- Relation fingerprints must be unique and raw relation names are excluded.
- Cutoffs are computed from policy days and inventory capture time.
- Candidates must be older than the computed cutoff.
- Legal holds and active incidents block readiness.
- Recovery evidence must be verified with a minimum seven-day window by default.
- Recovery versions cannot exceed current versions.
- Inventory freshness, timestamps, counts, bytes, versions, files, and findings are bounded.
- Symbolic links, unknown fields, duplicate keys, and unsupported policy evolution fail closed.
- No network, subprocess, SQL, Databricks, storage, or deletion command is available.

## Non-goals

This change does not query a workspace, enumerate real tables, approve retention,
execute cleanup, delete rows or files, vacuum Delta history, drop objects, alter
checkpoints, activate schedules, change permissions, contact production, or claim
legal approval.

## Control flow

```text
repository policy + protected read-only inventory
  -> strict schema and bounded-value validation
  -> target, hold, incident and recovery checks
  -> policy-derived cutoffs
  -> candidate/version reconciliation
  -> sanitized ready/blocked dry-run report
  -> human review
```

## Failure behaviour

Exit status is:

```text
0  dry-run plan ready for review
1  structurally valid inventory blocked by findings
2  malformed, unsupported or unsafe input/output path
```

Representative findings include:

```text
legal_hold_is_active
active_incident_blocks_retention
recovery_evidence_is_not_verified
recovery_window_is_too_short
required_retention_relation_missing
candidate_boundary_is_not_older_than_cutoff
recovery_version_exceeds_current
inventory_is_stale
```

## Security, cost and side-effect boundary

The planner uses the Python standard library only, accepts at most one megabyte
per input and 32 relations, and stores fingerprints rather than resource names.
Candidate rows, bytes, and versions are bounded before arithmetic or rendering.
It cannot start a provider command and never authorizes mutation.

The plan helps expose potential cleanup volume and recovery risk before an
executor exists. It does not prove the inventory is honest; humans must inspect
the protected evidence behind each digest.

## Compatibility

The planner supports operational policy schema version 1 and the current five
retention expectation keys. Policy evolution must update the planner, focused
tests, guide, and change brief together. Unknown keys fail closed.

## Validation

Focused tests cover ready sanitized output, missing relations, holds, incidents,
recovery gaps, cutoff violations, version inconsistencies, stale/future inventory,
duplicate/unknown/raw fields, unique fingerprints, numeric bounds,
development-only enforcement, symbolic-link rejection, and the absence of any
mutating or network-capable code path.

No Spark Runtime run is required because the increment changes offline planning
tooling, standard-library tests, and documentation only. Real inventory and any
future cleanup remain external and unrun.

## Rollback

Source rollback is a normal revert. Existing dry-run reports remain historical
review evidence. Reverting the planner does not authorize cleanup and must not be
used to ignore a blocked finding.
