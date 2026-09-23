# Change Brief: Validate portfolio and strict runtime evidence together

## Problem and exact inputs

The independent draft candidates had no complete combined execution evidence.
Their Spark workflow changes overlap and must not overwrite each other's gates.

- Main baseline: `701f342695794300fcf0d928621250b7ba60c15a`.
- Portfolio source/fixture candidate #147: `07c0d6b782d59b01b1342a6c4907357f5a3bbeb6`.
- Corrected timestamp/runner candidate #148: `328d2008b564cfd26d01f663776a70fbae82114c`.

## Acceptance Criteria

Preserve the complete #147 tree and overlay the exact #148 changed blobs, except
for the explicit union of the Spark workflow. Both event path lists must retain
fixture/source-profile dependencies AND runner/acceptance paths. Retain immutable
acceptance-base identity, pre-build acceptance, warnings, pins, read-only checkout,
two CPUs, 4 GiB and the twenty-minute timeout. Run full exact-index acceptance,
combined Spark tests, review-package generation, actual source-artifact round trip
and separate artifact compatibility. Report observed counts from the final logs;
individual candidate results are not substitutes for combined evidence.

## Architecture Boundaries and Scope

This is an isolated main-based integration snapshot, not a merge into main or a
rewrite of either input PR. Its large main comparison is inherited component
work, not a broad new implementation. The only new executable integration logic
combines the workflow edits; product changes remain the separately documented
timestamp correction. Existing fixture-wiring and runner tests check both halves.
The component briefs retain their semantic, operational and rollback boundaries.

## Non-Goals, State and Side Effects

Exclude #146, numeric-policy changes, warning suppression, new dependencies,
permissions, deployment, schedules and authenticated workspace operations.
No source fixture, table, schema, checkpoint, replay rule, Delta/catalog state or
Unity Catalog grant changes beyond the previously documented candidate behavior.
Do not label source-only artifacts as runtime evidence: the source builder does
not execute Spark, even when the separate Spark job passes on the same candidate.
No predecessor PR is automatically accepted, closed or superseded.

## Security, Cost, Failure and Recovery

Keep existing job permissions and compute/time bounds. Additional cost is a
normal bounded candidate CI run, not workspace compute or increased job ceilings.
Any failed combined gate blocks handoff as a successful combination. Repair and
rerun the candidate rather than disable a gate. Discard the isolated candidate to
undo integration; component reverts retain their own risks. Delta/checkpoint/ACL
restore and data RTO/RPO are N/A for this source-only integration operation.

## Validation and Review

Inspect the path-list union, immutable base and acceptance order, exact input blob
identities, strict runner and timestamp conversion. Local focused tests use a
source subset because repository cloning fails DNS and Docker/PySpark are absent;
they are not full repository acceptance. Full execution evidence must come from
GitHub CI. Independent correctness/adversarial reviews and explicit acceptance
of the exact combined candidate remain pending. No merge or deployment authority
is inferred from this snapshot or its automated checks.
