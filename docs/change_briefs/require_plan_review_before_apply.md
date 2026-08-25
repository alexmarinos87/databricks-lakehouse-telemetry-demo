# Change Brief: Require accepted plan review before apply

## Problem

The accepted deployment workflow already downloads the exact same-run plan
artifact and verifies provenance, identity, filenames, byte counts and hashes
before installing the Databricks CLI. The plan artifact now also contains a
repository policy decision, but an apply verifier must not trust that uploaded
decision blindly.

Without apply-side verification, an artifact could be incomplete, its review
could be substituted, or repository review policy could change after the review
files were generated. Exact plan replay alone proves byte identity, not that the
bytes remain accepted by the repository policy used at apply time.

## Outcome

Extend `scripts/verify_bundle_plan_artifact.py` so an apply job requires and
independently recomputes the structured review before the existing workflow can
install the Databricks CLI.

The existing workflow order remains:

```text
download same-run artifact
  → verify provenance, plan bytes and accepted review
  → independently recompute review from exact plan and current policy
  → install Databricks CLI
  → fresh apply-identity preflight
  → replay bundle-plan.json
```

No additional workflow permission or network request is introduced.

## Required artifact inventory

A valid plan artifact now requires:

```text
evidence.json
summary.md
bundle-validate.txt
bundle-plan.json
databricks-plan-review.json
databricks-plan-review.md
```

Validation and plan warning files remain optional only when their metadata and
bytes agree exactly.

## Stored review checks

The verifier requires:

- schema version 2;
- status `accepted`;
- zero findings;
- target and source commit matching the apply job;
- plan SHA-256 and byte count matching `bundle-plan.json`;
- exact review JSON shape;
- exact review filenames and policy path in `evidence.json`;
- metadata resource/finding counts matching the review JSON;
- Markdown that is exactly rendered from the stored JSON.

A blocked review cannot be converted into a successful plan merely by changing
the main evidence status.

## Independent recomputation

The verifier loads the current repository policy:

```text
governance/databricks_plan_review_policy.json
```

It reparses the exact downloaded plan and independently recomputes the review.
The recomputed decision must be `accepted`, and every stable field must match the
stored decision. `generated_at_utc` is the only intentionally non-deterministic
field excluded from equality.

This detects:

- missing review files;
- substituted or tampered review JSON;
- review Markdown drift;
- review metadata drift;
- plan/review digest disagreement;
- a plan no longer accepted by current repository policy;
- stale or fabricated action counts, findings or resource fingerprints.

## Failure categories

Representative fail-closed categories include:

```text
artifact_required_file_missing
plan_review_not_accepted
plan_review_contains_findings
plan_review_markdown_mismatch
plan_review_metadata_not_accepted
plan_review_recomputation_failed
plan_review_recomputation_not_accepted
plan_review_recomputation_mismatch
```

The command prints only the stable category, never plan values, resource
addresses, provider state or credentials.

## Security boundary

The verifier remains offline and standard-library-only apart from importing the
repository's local reviewer module. It performs no subprocess or network call.

It does not:

- request GitHub OIDC;
- authenticate to Databricks;
- validate, plan or deploy a bundle;
- upload data;
- execute workflows or SQL;
- change permissions, checkpoints, alerts, retention or schedules;
- touch production.

Because the existing GitHub workflow calls this verifier before
`databricks/setup-cli`, a failure prevents any provider command in the apply job.

## Human authority boundary

An accepted source-policy review is necessary but not sufficient for apply.
Environment approval remains required, and the reviewer must inspect the exact
retained artifact and external governance/federation evidence. The recomputation
does not approve a deployment automatically.

## Compatibility

Plan artifacts generated before policy-review evidence was attached are now
ineligible for apply. Regenerate them from the accepted source commit rather
than adding a compatibility bypass.

A future direct-plan or review schema change must update the capture, reviewer,
artifact verifier and tests together. Unknown fields and versions fail closed.

## Validation

Tests cover:

- a complete accepted artifact;
- provenance mismatch;
- plan substitution and non-object JSON;
- missing or symbolic-link review files;
- blocked review relabeling;
- review JSON tampering with internally consistent metadata/Markdown;
- review Markdown tampering;
- current policy rejecting a previously accepted plan;
- strict evidence and review metadata shapes;
- optional warning integrity;
- verifier ordering before CLI installation in both targets.

## Rollback

Source rollback is a normal revert. Rolling back this control must not be used to
apply an older artifact lacking accepted review evidence. Regenerate and review
a new plan under the resulting accepted source instead.
