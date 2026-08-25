# Change Brief: Plan guarded history retention

## Problem

The operational policy defines retention expectations for quality, forecast and
expectation histories, but the repository deliberately has no automatic deletion
job. That protects recoverability, yet it also leaves no executable way to turn a
workspace inventory into a bounded review manifest.

A future operator must not infer candidates from table age alone. Current
publications, active runs, recovery-protected generations and the minimum rollback
set must remain outside any deletion proposal.

## Outcome

Add an offline, development-only planner:

```bash
python3 scripts/plan_history_retention.py \
  --inventory .bootstrap/evidence/dev/history-retention-inventory.json \
  --output-dir .bootstrap/evidence/dev/history-retention-plan
```

The planner consumes one sanitized inventory and the repository policy at:

```text
governance/history_retention_policy.json
```

It produces:

```text
history-retention-plan.json
history-retention-plan.md
```

The output is a dry-run review manifest. It contains no SQL, provider command,
`VACUUM`, `DROP`, deletion request, workflow dispatch or scheduler definition.

## Policy

The policy is schema version 1 and requires:

- `dry_run_only: true`;
- a bounded maximum inventory age;
- a minimum recovery window;
- a bounded maximum candidate count;
- explicit retention days for every governed history family;
- a minimum number of committed entries retained for each family.

The retention days must match the expectations in
`governance/operational_alert_policy.json`.

The initial families are:

```text
quality_check_results
quality_metric_history
forecast_history
forecast_publication_manifest
expectation_event_log
```

## Inventory contract

The input is development-only and contains exactly:

```text
schema_version
target
repository
source_commit
captured_at_utc
workspace_fingerprint
datasets
```

Each dataset contains an exact policy dataset ID and a bounded list of entries.
Each entry contains:

```text
entry_id
entry_fingerprint
created_at_utc
state
current
recovery_protected
byte_count
```

Allowed states are `committed`, `failed` and `started`. Raw table contents,
workspace URLs, credentials, SQL output and provider responses are outside the
contract.

Every policy dataset must appear exactly once. Omitting a family is invalid
rather than silently producing a partial retention plan.

## Candidate rules

An entry is eligible only when all of the following are true:

1. It is older than both the dataset retention threshold and the minimum recovery
   window.
2. It is not the current entry.
3. It is not recovery-protected.
4. It is not in `started` state.
5. If committed, it is not one of the newest committed entries protected by the
   dataset minimum.

The planner sorts committed entries deterministically by timestamp and entry ID.
It does not select an arbitrary winner when the inventory is ambiguous.

Current entries must be committed, and a dataset may have at most one current
entry. Duplicate dataset IDs, duplicate entry IDs, unsupported states and unknown
fields fail closed.

## Blocking behaviour

The plan is `blocked` and its actionable candidate list is suppressed when:

- the inventory is stale;
- the inventory timestamp is materially in the future;
- the eligible candidate count exceeds the policy maximum.

The report still records aggregate eligible counts and bytes so a human can
reduce the scope and regenerate the inventory. A blocked report is not a deletion
manifest.

## Evidence boundary

The report contains:

- source and inventory provenance;
- policy and inventory SHA-256 digests;
- workspace and entry fingerprints;
- retention thresholds;
- aggregate protected counts;
- bounded candidate IDs and fingerprints when the plan is accepted;
- stable finding categories.

It excludes raw data values, table contents, workspace addresses, access tokens,
provider output and executable deletion instructions.

## Security and execution boundary

The planner:

- uses the Python standard library only;
- performs no network or subprocess call;
- reads no credential environment variable;
- accepts only regular bounded files;
- rejects symbolic-link inputs and output directories;
- writes output atomically;
- is restricted to `target: dev`.

A successful dry run does not authorize deletion. A later apply increment must
require separate human approval, re-read the exact manifest, preserve the
recovery window and reconcile the workspace after mutation.

## Validation

Tests cover:

- deterministic selection of old unprotected entries;
- protection of current, recent, active, recovery-protected and minimum committed
  entries;
- duplicate current entries and missing datasets;
- stale and future inventories;
- candidate-limit blocking with candidate suppression;
- unknown fields, duplicate entries and non-development targets;
- symbolic-link output rejection;
- retention-policy alignment with operational expectations;
- deterministic sanitized JSON and Markdown output.

## Rollback

Source rollback is a normal revert. Existing dry-run evidence remains review
material only. Reverting the planner must never be treated as approval to delete
history manually or bypass recovery protections.
