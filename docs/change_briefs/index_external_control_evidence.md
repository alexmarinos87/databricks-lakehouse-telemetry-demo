# Change Brief: Index effective external-control evidence

## Problem

The repository can independently verify GitHub governance, Databricks federation
and deployment/runtime privilege evidence. Those reports can currently be reviewed
in isolation, however. A reviewer could accidentally combine:

- GitHub protection evidence from one `main` commit;
- Databricks federation evidence from another collection window;
- identity privilege evidence from a different source commit;
- reports produced by verifier source that has since changed.

The result could look complete while never describing one coherent external-control
state.

## Outcome

Add an offline index builder:

```bash
python3 scripts/build_external_control_evidence_index.py \
  --metadata .bootstrap/evidence/dev/external-control-index-metadata.json \
  --evidence-root .bootstrap/evidence/external-controls \
  --output-dir .bootstrap/evidence/dev/external-control-index
```

The command reads the policy at:

```text
governance/external_control_evidence_policy.json
```

and produces:

```text
external-control-evidence-index.json
external-control-evidence-index.md
```

It performs no GitHub or Databricks request and changes no external setting.

## Required controls

Exactly three controls must appear in policy and metadata order:

```text
github_governance
databricks_federation
identity_privilege
```

Each metadata entry declares:

```text
control_id
report_path
expected_report_sha256
expected_verifier_sha256
workflow_run_fingerprint
```

Report paths are relative to a separately supplied evidence root. Verifier paths
come from repository policy and resolve beneath the supplied repository root.

## Exact source binding

The index binds all controls to one forty-character source commit.

For GitHub governance evidence, the verified report must state:

```text
branch: main
branch_head_sha: <the index source commit>
main_protected: true
```

It must also retain the strict status check, administrator enforcement, linear
history, force-push and deletion prevention, conversation resolution, stale-review
dismissal and four verified main-only environments.

Identity privilege evidence must reference the same source commit and must contain
a complete verified required-evidence set for distinct deployment and runtime
identities.

Databricks federation evidence has no source-commit field because it describes
account state. Its exact report bytes and the exact current federation-verifier
source bytes are therefore bound into the same index and collection window.

## Verifier-source drift protection

For every control, the builder reads the current repository verifier file and
calculates its SHA-256 digest. It requires equality with the independently recorded
`expected_verifier_sha256`.

A report cannot be indexed under newer or different verification logic without a
deliberate metadata refresh and review.

## Freshness and collection coherence

The initial policy requires:

```text
maximum evidence age: 72 hours
maximum report capture spread: 4 hours
```

The index capture and all report generation timestamps must not be stale,
materially future-dated or later than the index capture. The difference between
the earliest and latest report generation times must remain inside the configured
spread.

This prevents combining individually valid reports collected too far apart to
represent one coherent control state.

## Effective-state checks

A report is not accepted merely because it contains `status: verified`.

The index also checks key effective fields:

- GitHub `main` protection and the complete branch/environment control set;
- exact GitHub branch-head equality with the source commit;
- active, non-admin, secretless Databricks principals;
- exact Databricks federation-policy results;
- complete identity allow/deny evidence;
- distinct deployment and runtime identity fingerprints;
- empty findings in every report.

Semantic drift produces a blocked index. Structural, path or digest errors fail
closed without producing an index.

## Path and input safety

The builder:

- accepts bounded regular JSON files only;
- rejects symbolic-link roots, path components and files;
- rejects absolute, traversal, backslash and non-canonical paths;
- rejects duplicate report paths and report digests;
- verifies every report and verifier digest;
- bounds policy, metadata, report and verifier bytes;
- writes JSON and Markdown atomically;
- uses the Python standard library only.

## Evidence boundary

The index retains:

- repository and source commit;
- policy and metadata digests;
- report and verifier digests;
- report generation timestamps;
- workflow-run fingerprints;
- freshness limits;
- stable findings.

It excludes:

- report paths;
- report bodies;
- workspace URLs;
- account, application and numeric principal IDs;
- provider diagnostics;
- tokens, secrets and credentials.

The output always records:

```text
external_mutation_authorized: false
```

A verified index is review evidence. It does not authorize settings mutation,
deployment, plan, apply, data access or another evidence collection run.

## Validation

Tests cover:

- one complete verified three-control index;
- GitHub, federation and identity effective-state drift;
- source-commit, repository, status and findings mismatches;
- stale, future, post-capture and over-wide evidence windows;
- report substitution and verifier-source drift;
- duplicate, reordered and non-canonical metadata;
- symbolic-link reports and outputs;
- deterministic sanitized JSON and Markdown;
- absence of network, subprocess, credential and mutation surfaces.

## Rollback

Source rollback is a normal revert. Existing indexes remain evidence of the exact
reports and verifier versions they name. Reverting the builder does not invalidate
those external reports, undo external state or authorize combining reports
manually without equivalent source, digest and freshness checks.
