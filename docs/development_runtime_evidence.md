# Controlled development runtime evidence

This guide defines the bounded evidence package for one explicitly approved
Databricks development execution. Use it only after GitHub governance,
workload-identity federation, least privilege, and an exact accepted plan have
been independently verified.

Run the offline admission command with a sanitized manifest:

```bash
python3 scripts/verify_development_runtime_evidence.py \
  --evidence .bootstrap/evidence/dev/development-runtime-evidence.json \
  --output-dir .bootstrap/evidence/dev/development-runtime-verification
```

The verifier does not contact GitHub or Databricks, execute SQL, run a workflow,
deploy a bundle, upload data, or change permissions.

## Manifest provenance

The schema-version-1 manifest contains only:

```text
schema_version
target
repository
source_commit
captured_at_utc
apply
execution
evidence_families
assertions
rollback
```

It must identify:

```text
target: dev
repository: alexmarinos87/databricks-lakehouse-telemetry-demo
```

`apply` binds the execution to reviewed approval and plan evidence:

```text
authorized: true
approved_at_utc
approval_sha256
accepted_plan_sha256
accepted_plan_review_sha256
workflow_run_fingerprint
```

Do not put approver names, raw workflow URLs, tokens, provider responses, or
workspace hosts in the manifest. Keep them in the protected evidence system and
use SHA-256 digests or fingerprints here.

## Execution boundary

`execution` records:

```text
execution_fingerprint
evidence_sha256
started_at_utc
completed_at_utc
production_contact: false
deployment_principal_fingerprint
runtime_principal_fingerprint
```

The execution evidence digest must reference the protected run-level record that
supports the timestamps, production-contact result, and effective identities. A
bare Boolean is not accepted without this protected evidence reference.

The default maximum duration is four hours and the default freshness window is
72 hours. Both can be reduced at review time. Deployment and runtime principal
fingerprints must differ.

## Required evidence families

Each family must appear exactly once, refer to the same execution fingerprint,
have at least one bounded evidence record, and carry its own digest:

```text
bronze
silver
gold
forecast
warehouse
quality
expectations
queries
grants
```

Resource names, table names, row contents, query text, principal IDs, and
provider diagnostics do not belong in the manifest.

## Required assertions

All assertions must have status `passed`:

```text
source_upload_is_immutable
checkpoint_is_reused
identical_replay_is_idempotent
conflicting_event_is_quarantined
silver_publication_is_committed
gold_publication_is_committed
forecast_publication_is_committed
warehouse_publication_is_committed
current_views_expose_committed_generations_only
source_to_target_is_reconciled
quality_evidence_is_persisted
expectations_are_evaluated
saved_queries_are_viewer_run
effective_grants_are_verified
deployment_denials_are_verified
runtime_denials_are_verified
```

Each assertion references one protected evidence digest. A failed or untested
assertion blocks admission; it is never converted into a warning.

## Rollback evidence

The controlled exercise must also record:

```text
tested: true
completed_at_utc
evidence_sha256
recovery_point_sha256
```

Rollback means a bounded recovery rehearsal or verified restoration path for the
development state. It must not delete production data or erase failed-run
evidence.

## Outputs and interpretation

Successful or blocked evaluation writes:

```text
development-runtime-verification.json
development-runtime-verification.md
```

The report contains source, manifest, execution, family, assertion and rollback
digests; timestamps; fingerprints; counts; statuses; and stable findings. It
excludes raw provider and data content.

A `verified` report means the supplied manifest is complete and internally
consistent with this contract. Human review remains required to inspect the
protected evidence behind every digest. Verification does not authorize another
deployment, production access, cleanup operation, or scheduler activation.
