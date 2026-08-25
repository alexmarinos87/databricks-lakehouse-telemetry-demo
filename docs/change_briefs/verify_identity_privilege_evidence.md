# Change Brief: Verify Databricks identity privilege evidence

## Problem

The repository defines distinct deployment and runtime service principals and a
machine-readable allow/deny matrix in
`config/identity_privilege_contract.json`. Repository tests prove that the
bundle, grants, job and pipeline express that intent, but issue #44 still
requires effective development evidence.

Without one strict admission boundary, a delivery review could accidentally
accept:

- production observations under the development gate;
- stale or materially future evidence;
- the same principal represented as both deployment and runtime;
- missing or duplicate required observations;
- a successful privileged action where denial was expected;
- a denied action where runtime capability was required;
- a permission claim that does not belong to the selected identity;
- raw workspace, provider or credential material copied into review evidence.

## Outcome

Add `scripts/verify_identity_privilege_evidence.py`, an offline verifier that
compares one bounded development evidence manifest with the accepted executable
identity contract.

The verifier writes:

```text
identity-privilege-verification.json
identity-privilege-verification.md
```

Exit status is:

```text
0  all required development evidence verified
1  structurally valid evidence blocked by findings
2  invalid contract, manifest, time boundary or output path
```

## Manifest boundary

The input manifest has schema version 1 and exactly these top-level fields:

```text
schema_version
target
repository
source_commit
captured_at_utc
workspace_fingerprint
identities
observations
```

It must use:

```text
target: dev
repository: alexmarinos87/databricks-lakehouse-telemetry-demo
```

Deployment and runtime identities are represented only by distinct SHA-256
fingerprints. Every observation contains one bounded evidence ID, identity,
capability list, expectation, outcome, approved method, UTC timestamp and
evidence digest.

The schema deliberately excludes raw principal identifiers, workspace URLs,
provider responses, table values, SQL output, tokens and secrets.

## Required evidence rules

The verifier requires every ID from
`config/identity_privilege_contract.json` exactly once and maps it to a fixed
identity, capability set, expectation and safe collection method.

| Evidence ID | Identity | Capabilities | Method | Outcome |
| --- | --- | --- | --- | --- |
| `deployment_principal_can_assign_runtime_service_principal` | deployment | `manage_bundle_jobs_and_pipelines` | `resource_readback` | succeeded |
| `runtime_principal_can_execute_job_and_pipeline` | runtime | `run_lakehouse_job`, `run_quality_pipeline` | `workflow_run` | succeeded |
| `deployment_principal_cannot_select_curated_tables` | deployment | `select_curated_tables` | `denied_live_attempt` | denied |
| `runtime_principal_cannot_deploy_bundle` | runtime | `bundle_deploy` | `permission_readback` | denied |
| `deployment_principal_cannot_run_job_as_itself` | deployment | `run_lakehouse_job_as_self` | `resource_readback` | denied |

A `denied_live_attempt` is permitted only for a bounded read-only operation.
The repository does not instruct an operator to attempt a mutating deployment or
permission change merely to demonstrate denial.

Additional observations are allowed only when their capabilities and expectation
match the selected identity's accepted allow/deny matrix.

## Freshness and provenance

The verifier requires:

- one lowercase 40-character source commit;
- one public repository identity;
- one development target;
- one workspace fingerprint;
- two distinct principal fingerprints;
- UTC timestamps ending in `Z`;
- capture and observation timestamps no older than the configured maximum age;
- no timestamp materially in the future;
- no observation later than the manifest capture boundary.

The default maximum age is 72 hours and can be reduced with
`--max-age-hours`.

## Findings

Representative blocking findings include:

```text
identity_fingerprints_overlap
evidence_capture_is_stale
evidence_capture_is_in_future
required_evidence_missing
required_evidence_contract_mismatch
required_capability_not_succeeded
expected_denial_not_observed
observation_capability_expectation_mismatch
observation_error
observation_not_tested
```

Malformed shapes, duplicate evidence IDs, unknown target or repository,
unsupported contract evolution and unsafe file paths fail as invalid input
rather than being silently interpreted.

## Security and execution boundary

The verifier:

- uses the Python standard library only;
- performs no subprocess or network call;
- reads no environment credential;
- accepts at most 64 observations;
- bounds each input file to 1 MB;
- bounds strings and findings;
- rejects symbolic-link input and output directories;
- writes outputs atomically;
- logs only stable categories and aggregate counts.

It does not authenticate to GitHub or Databricks, request OIDC, run a bundle
command, execute SQL, upload data, run a job or pipeline, change permissions,
modify checkpoints, activate alerts, enforce retention, enable schedules or
touch production.

## Human authority boundary

A `verified` report means that the supplied sanitized development observations
are internally complete and consistent with the accepted source contract. It
does not establish that the observations were collected honestly without human
review.

The reviewer must still inspect the referenced evidence digests in the protected
system, confirm the source commit and environment, and attach the bounded report
to issue #44 or the delivery queue.

## Compatibility

The verifier intentionally supports only the current schema-version-1 identity
contract and the five current required evidence IDs. Contract changes must update
the verifier, tests and documentation in the same reviewed increment. Unknown
contract evolution fails closed.

## Validation

Focused tests cover:

- complete verified development evidence;
- missing required evidence;
- required success returning denial;
- expected denial unexpectedly succeeding;
- method and capability mismatch;
- deployment/runtime fingerprint overlap;
- stale and future timestamps;
- unknown capabilities and extra raw fields;
- duplicate observations;
- non-development target rejection;
- symbolic-link output rejection;
- sanitized deterministic JSON and Markdown output.

## Rollback

Source rollback is a normal revert. A revert removes the admission helper but
does not invalidate or delete previously captured external evidence. Do not use
rollback to represent unverified evidence as accepted; retain the last verified
report and repeat collection under the resulting accepted source contract.
