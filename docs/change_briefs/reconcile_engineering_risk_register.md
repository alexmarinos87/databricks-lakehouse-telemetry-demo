# Change Brief: Reconcile the Engineering Risk Register

## Problem

The repository risk register predates the latest ingestion, runtime identity,
assignment, downtime, quality, forecast, operational, compatibility, and
manifest-last publication controls. Several entries still describe removed
source defects as open implementation gaps and cite old pull requests or short
commit hashes instead of current repository evidence.

That drift creates two opposite failure modes:

- source controls can be overlooked and rebuilt unnecessarily;
- a source mitigation can be mistaken for Databricks, GitHub, notification, or
  consumer proof and the risk can be closed too early.

## Outcome

Introduce one machine-readable risk register under:

```text
governance/engineering_risk_register.json
```

Render the human-readable document deterministically with:

```text
scripts/render_engineering_risk_register.py
```

Every risk separates:

```text
lifecycle
source_status
runtime_status
source_evidence
external_dependencies
residual_risk
next_evidence
```

The register retains stable IDs `R-001` through `R-017`. It adds explicit
operational-alert and runtime-compatibility risks rather than hiding those
external evidence gaps in narrative documentation.

## Acceptance Criteria

- Every risk remains `open` unless one durable closure review includes source or
  setting evidence, effective runtime evidence where applicable, rollback
  implications, and a named human reviewer.
- `source_mitigated` cannot be paired with implied closure; it must retain
  `runtime_evidence_pending` or `externally_blocked`.
- Repository-owned unresolved work is represented as `source_gap_open`.
- Controls that only external settings can enforce are represented as
  `not_source_controlled`.
- Every source evidence path exists in the accepted repository tree.
- Source evidence references current files, not feature branches, PR numbers,
  pull URLs, or commit hashes.
- Externally blocked risks name their exact dependency class.
- The Markdown file is an exact deterministic rendering of the JSON source.
- Tests enforce structure, statuses, evidence paths, critical control coverage,
  open G12 ownership work, issue #44 blockers, and notification activation debt.

## Non-Goals

- This increment does not close a risk.
- It does not authenticate to Databricks or execute a bundle.
- It does not change GitHub branch protection, service principals, saved-query
  ownership, alert destinations, retention jobs, or runtime versions.
- It does not replace issue #46 as the incremental delivery queue.
- It does not claim that local Spark evidence is Databricks Runtime evidence.

## Evidence Boundary

The JSON register is a current source-of-truth assessment of repository
controls. Its tests prove internal consistency, current file references, and
explicit evidence boundaries. They cannot prove external settings or runtime
behaviour.

`as_of_date` records when the source assessment was made. Effective-state
evidence remains a separately retained workflow, workspace, settings, consumer,
or notification record.

## Failure And Recovery

A stale Markdown document fails CI because it no longer matches the renderer.
An invalid status, missing evidence path, duplicate ID, stale delivery
reference, hidden external blocker, or falsely closed lifecycle also fails CI.

Rollback is a normal revert of the eventual squash commit. That restores the
previous narrative register but also restores its known evidence drift; no
Databricks or GitHub runtime state is changed by rollback.
