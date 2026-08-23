# Operational health runbook

## Scope

This runbook covers repository-defined health evidence for ingestion identity,
data quality, forecast publication, and deployment/runtime identity. It does not
claim that a live notification channel or Databricks dashboard is configured.

The source-controlled alert contract is
`governance/operational_alert_policy.json`; bounded diagnostic SQL is in
`sql/operational_health.sql`.

## Evidence collection

For every incident retain:

- target and exact Git commit;
- Databricks workflow or pipeline run ID;
- quality or forecast run ID;
- observed alert ID and severity;
- bounded counts and timestamps;
- relevant Delta versions before any repair;
- operator, reviewer, action, and result;
- a link to the corresponding runbook section.

Do not copy raw telemetry rows, source file content, tokens, client IDs, provider
diagnostics, or unbounded query output into issue comments.

## Quality errors

Trigger: `quality_error_check_failed`.

1. Read the latest `quality_metric_history` row and the corresponding bounded
   `quality_check_results` records.
2. Separate error-level failures from warning-level semantic review.
3. Identify the first failing layer: Bronze, Silver, Gold, warehouse, forecast,
   or publication manifest.
4. Preserve the failed run evidence before rerunning or repairing data.
5. Use a forward fix where possible. Restore Delta versions only with an
   approved, object-by-object recovery plan.
6. Confirm a later quality run has zero failed error checks; do not erase the
   failed historical record.

## Stale quality evidence

Trigger: `quality_evidence_stale` after 24 hours.

1. Confirm whether the workflow schedule is intentionally paused.
2. Check the latest job state and task dependency that prevented the quality
   notebook from running.
3. Check cluster policy, runtime identity, schema access, and source arrival.
4. Do not create a synthetic passing quality row. Repair the blocked workflow
   and retain the new real run ID.

## Invalid ingestion identity

Trigger: `invalid_ingestion_identity`.

1. Stop further Bronze processing for the affected landing root.
2. Enumerate bounded source-object names without printing source content.
3. Move unmanaged legacy names outside the watched landing root through a
   separately approved storage operation.
4. Recreate the upload plan from preserved repository source bytes, or obtain
   independently verified source evidence for external writers.
5. Reuse the existing Auto Loader checkpoint. Do not delete it merely to replay
   a file.
6. If invalid rows already reached Bronze, preserve current Delta versions and
   assess downstream event IDs before any deletion or repair.

## Forecast publication

Triggers: `forecast_publication_failed` or `forecast_publication_stuck`.

Use `docs/runbooks/forecast_publication_recovery.md`. A `STARTED` run older than
60 minutes is a review candidate, not automatic permission to delete history.
Confirm that no active repair run is using the same `forecast_run_id`.

## Deployment/runtime identity mismatch

Trigger: `deployment_or_runtime_identity_mismatch`.

1. Stop before plan, upload, or workflow execution.
2. Compare fingerprints in the retained identity evidence with the configured
   deployment and runtime client IDs.
3. Verify exact-subject federation policies for the selected GitHub environment.
4. Verify the job and quality pipeline `run_as` values in the authenticated plan.
5. Review effective permissions against `docs/runtime_identity.md` and
   `governance/runtime_identity_policy.json`.
6. Remove an unexpected broad privilege rather than adding workspace-admin to
   make the run pass.

## Retention review

The policy contains expectations, not an automatic deletion job. Review actual
retention at least quarterly:

- quality check details: 90 days;
- quality metric summaries: 180 days;
- forecast histories: 180 days;
- forecast publication manifests: 365 days;
- expectation event log: 90 days.

Before deleting or vacuuming any Delta evidence, confirm legal/portfolio needs,
recovery windows, the latest committed publication, and the absence of active
incident investigations. Record affected table versions and the earliest
retained timestamp.

## Alert delivery boundary

A live alert is complete only when evidence records:

```text
deployed query or dashboard identifier
notification destination identifier
test-alert delivery timestamp
acknowledging owner
resolved runbook link
```

Until those fields exist, describe the repository as containing alert policy
and diagnostic assets—not enabled production alerting.
