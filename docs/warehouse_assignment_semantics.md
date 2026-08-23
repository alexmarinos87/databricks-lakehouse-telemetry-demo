# Warehouse assignment and unknown-member semantics

## Decision

The repository uses `effective_dated_assignment_v1` for machine ownership and
location. A machine assignment consists of:

```text
machine_id
client_id
site_id
model
effective_from
effective_to
```

`effective_from` is the first event date carrying a distinct assignment.
`effective_to` is the day before the next distinct assignment and is null for
the current assignment.

Uptime and failure evidence have equal authority. The implementation does not
silently prefer one source when both report different client, site, or model
values for the same machine and date.

## Unknown-member policy

Trusted warehouse facts do not use a fabricated `Unknown`, `-1`, or hash-derived
surrogate member when a mandatory business identity is missing or unresolved.

Instead:

1. Preserve the source event in Gold or quarantine evidence.
2. Resolve it against effective-dated assignment history.
3. Keep unresolved or ambiguous rows in a separate result.
4. Block trusted fact publication while those findings remain.
5. Repair the source or assignment history and rerun reconciliation.

This avoids converting a real governance defect into a plausible-looking
warehouse member. The synthetic product treats client, site, model, machine,
date, and fault identities as mandatory.

## Conflict rules

Publication is blocked when:

- required assignment identities are blank;
- one machine has more than one distinct assignment on the same date;
- effective ranges overlap;
- a machine has zero or multiple current assignments;
- a fact resolves to zero assignments;
- a fact resolves to more than one assignment;
- resolved and unresolved partitions do not reconcile to the input count.

A machine with a same-day conflict is excluded from trusted assignment history;
all conflicting evidence is retained for diagnosis. The builder never selects a
winner by source order, arrival time, lexical order, or aggregate function.

## Reassignment example

```text
2026-04-01  M-1  client-A  site-1  model-X
2026-04-05  M-1  client-B  site-2  model-X
```

produces:

```text
M-1  client-A  site-1  model-X  2026-04-01  2026-04-04
M-1  client-B  site-2  model-X  2026-04-05  null
```

A fact dated 2026-04-03 resolves to the first assignment. A fact dated
2026-04-06 resolves to the second.

## Late-arriving assignment

A newly discovered earlier assignment can change effective ranges for all later
periods. Therefore a late assignment is not appended blindly. Rebuild the
machine's complete history from retained source evidence, then reconcile every
affected fact by natural identity and measure before publication.

Example: if an assignment effective 2026-03-20 arrives after facts for April
were published, preserve current Delta versions, rebuild the machine's history,
resolve all facts from 2026-03-20 onward, and compare old/new keys and business
identities. Do not patch only the latest fact.

## Executable interface

`src/lakehouse_demo/spark_assignment_history.py` provides:

- `build_assignment_history()` — combines bounded named evidence sources,
  records missing identities and same-day conflicts, and builds non-overlapping
  periods for conflict-free machines;
- `audit_assignment_history()` — checks grain, identities, ranges, and current
  membership;
- `resolve_assignment_as_of()` — left-resolves events into resolved,
  unresolved, and ambiguous partitions without losing input rows or assigning
  a placeholder member.

The machine-readable policy is
`governance/warehouse_assignment_policy.json`.

## Warehouse integration boundary

The current warehouse builder still derives dimensions directly from Gold and
blocks conflicting assignments through its existing publication audit. This
increment defines and tests the stronger effective-dated contract first. A
later migration may publish assignment history as a dimension or bridge after
reviewing existing table schemas and report compatibility.

Do not represent this repository-only contract as a completed live warehouse
migration.

## Recovery and rollback

For a failed assignment publication:

1. Preserve the source evidence and exact finding codes.
2. Record affected machine IDs only in appropriately restricted evidence; broad
   logs should retain counts rather than business values.
3. Record current Delta versions for assignment history and affected facts.
4. Correct or quarantine the conflicting evidence.
5. Rebuild complete history for affected machines.
6. Rerun assignment, natural-identity, count, foreign-key, and measure
   reconciliation.
7. Publish only when all mandatory findings are empty.

Source rollback is a revert of the eventual squash commit. A deployed rollback
must restore assignment history and affected facts together; restoring only one
side can create valid-looking but incorrect foreign keys.
