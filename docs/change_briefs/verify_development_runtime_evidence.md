# Change Brief: Verify controlled development runtime evidence

## Problem

The repository now verifies external governance, workload identity, exact plan
review, and deployment/runtime privilege evidence through separate fail-closed
tools. The next acceptance gap is the result of one controlled development run.

Without one strict admission boundary, evidence from different executions could
be combined, a partial run could be described as complete, production contact
could be hidden, failed assertions could be omitted, or rollback-free evidence
could be accepted. The initial candidate also allowed execution-level state to be
asserted without an execution evidence digest; adversarial review closed that gap.

## Outcome

Add an offline verifier for one controlled development run. The public entry point
is `scripts/verify_development_runtime_evidence.py`; stable parsing and semantic
logic are isolated in `scripts/development_runtime_evidence_core.py` so the
entry-point admission layer can require protected execution evidence explicitly.

The verifier binds one run to:

- the accepted source commit;
- explicit apply approval;
- exact plan and plan-review digests;
- one workflow-run fingerprint;
- one execution fingerprint and execution evidence digest;
- distinct deployment and runtime identities;
- nine evidence families;
- sixteen mandatory assertions;
- tested rollback evidence.

It writes:

```text
development-runtime-verification.json
development-runtime-verification.md
```

## Acceptance criteria

- Only `target: dev` and the exact public repository are accepted.
- Approval, plan, plan-review, workflow-run, and execution evidence are mandatory.
- Approval must precede execution.
- Deployment and runtime fingerprints must differ.
- Production contact blocks the run.
- All families and assertions must refer to one execution.
- Every mandatory assertion must be `passed`.
- Evidence must be fresh, UTC, bounded, and inside the execution/capture window.
- Execution duration is bounded to four hours by default.
- Rollback must be tested and carry recovery evidence.
- Output contains digests, fingerprints, counts, timestamps, statuses, and stable findings only.
- Symbolic links, unknown fields, duplicate IDs, and unsupported evolution fail closed.

## Non-goals

This change does not collect or fabricate external evidence. It does not activate
GitHub protection, configure Databricks federation, authenticate to a workspace,
approve an apply, deploy a bundle, upload data, execute a workflow or SQL, change
permissions, modify checkpoints, enable schedules, activate alerts, run
retention, or contact production.

## Control flow

```text
protected external evidence
  -> sanitized schema-version-1 manifest
  -> exact shape and bounded-value validation
  -> approval, source, execution and execution-evidence binding
  -> family and assertion reconciliation
  -> freshness, identity, target and rollback checks
  -> sanitized verified/blocked report
  -> human inspection of every protected digest
```

## Failure behaviour

Exit status is:

```text
0  complete evidence verified
1  structurally valid evidence blocked by findings
2  malformed, unsupported or unsafe input/output path
```

Representative categories include:

```text
execution_shape_invalid
execution_evidence_digest_invalid
development_apply_was_not_authorized
production_contact_was_reported
deployment_and_runtime_identities_overlap
required_evidence_family_missing
required_assertion_missing
runtime_assertion_failed
runtime_assertion_not_tested
execution_duration_exceeds_limit
rollback_was_not_tested
```

The verifier logs only stable categories and aggregate counts.

## Security and side-effect boundary

The implementation uses only the Python standard library, performs no network or
child-process call, reads no environment credential, and rejects symbolic-link
input or output paths. It accepts at most one megabyte of manifest data, 32
evidence families, 64 assertions, and 128 findings.

A successful report admits supplied evidence for human review. It does not prove
that the protected evidence was collected honestly and does not authorize another deployment.

## Compatibility

The verifier supports schema version 1, the current nine families, and the
current sixteen assertions. The execution object now requires `evidence_sha256`;
pre-review manifests without it are intentionally ineligible. Future evolution
must update entry point, core, tests, guide, and change brief together.

## Validation

Focused tests cover complete evidence-bound verification, missing execution
evidence, missing families/assertions, unauthorized apply, production contact,
identity overlap, failed and untested assertions, execution/family mismatch,
duration limits, stale/future evidence, rollback gaps, duplicate/unknown/raw
fields, development-only enforcement, sanitization, and symbolic-link rejection.

No Spark Runtime test is triggered because this increment changes only offline
evidence tooling, standard-library tests, and documentation. Real Databricks
runtime evidence remains external and unrun.

## Rollback

Source rollback is a normal revert. Previously captured evidence remains
unchanged. Reverting the validator must never be treated as acceptance of a run
that failed this contract.
