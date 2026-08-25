# Retention dry-run planning

The retention expectations in `governance/operational_alert_policy.json` are
review policy, not an automatic deletion schedule. Before cleanup is considered,
capture a sanitized development inventory and generate a dry-run plan:

```bash
python3 scripts/plan_retention_dry_run.py \
  --inventory .bootstrap/evidence/dev/retention-inventory.json \
  --output-dir .bootstrap/evidence/dev/retention-dry-run
```

The planner is offline and does not execute `DELETE`, `VACUUM`, or `DROP`. It
performs no Databricks, SQL, storage, GitHub, network, or external-command
operation.

## Inventory boundary

The schema-version-1 inventory contains exactly:

```text
target: dev
repository
source_commit
captured_at_utc
workspace_fingerprint
legal_hold
legal_hold_evidence_sha256
active_incident
active_incident_evidence_sha256
recovery
relations
```

The legal-hold and active-incident Booleans must each carry a protected evidence
digest. A bare `false` value is not accepted. Keep the raw legal/operational
readback in the protected evidence system and put only its SHA-256 digest here.

Use relation fingerprints rather than catalog, schema, table, volume, path, or
checkpoint names. Keep raw query output and provider diagnostics outside the
sanitized manifest.

Each relation maps one policy retention key to:

```text
relation_fingerprint
current_version
recovery_version
latest_committed_at_utc
candidate_latest_at_utc
candidate_rows
candidate_bytes
candidate_versions
evidence_sha256
```

Candidate rows, bytes, and versions must be either all zero or all positive. This
prevents a partially populated inventory from understating cleanup volume.

The required policy keys are:

```text
quality_check_results_days
quality_metric_history_days
forecast_history_days
forecast_publication_manifest_days
expectation_event_log_days
```

## Safety gates

Planning blocks when:

- a legal hold or active incident exists;
- protected hold or incident evidence is absent or malformed;
- recovery evidence is unverified;
- the recovery window is shorter than seven days by default;
- inventory is stale or materially in the future;
- a required relation is missing;
- a candidate is not older than its policy cutoff;
- a candidate is later than the latest committed evidence;
- recovery or candidate version counts exceed current state;
- development and production boundaries are mixed.

A ready plan still contains:

```text
dry_run_only: true
execution_authorized: false
```

## Outputs

```text
retention-dry-run-plan.json
retention-dry-run-plan.md
```

Outputs include policy and inventory digests, legal-hold and incident evidence
digests, computed cutoffs, relation fingerprints, bounded counts, versions,
recovery evidence, and stable findings. They exclude relation names, paths, row
content, provider output, and credentials.

Human approval is still required before any separately implemented cleanup. A
future executor must consume an exact accepted plan, recheck legal holds,
incidents, recovery, and current versions immediately before mutation, use a
bounded target allowlist, and record partial failure and rollback evidence.
