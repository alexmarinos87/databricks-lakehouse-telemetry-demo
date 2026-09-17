# Change Brief: Align runtime evidence documentation with current source

## Problem

The runtime guide names lower-level Gold/warehouse functions as direct notebook
entrypoints and describes high downtime as an unresolved warning. Current notebooks
call governed wrappers; the source defines attributed_incident_v1 with independent
attributed downtime. The guide also predates manifest-last history publication.
These stale descriptions impair review even when the implementation is correct.

## Acceptance Criteria

- Name the functions actually imported and called in Gold/warehouse notebooks.
- Describe current attributed-incident semantics and manifest-last visibility,
  without claiming cross-table transactions or effective workspace behavior.
- Explain the committed-fixture reconciliation suite and its bounded numeric scope.
- Keep source artifact flags distinct from a separately executed Spark job.
- Test documented names against notebook ASTs and the source semantic constant;
  retain exact-candidate CI evidence separately from the documentation's claims.

## Non-Goals And Architecture Boundaries

Three paths only: docs/spark_runtime_evidence.md, one portable documentation test,
and this brief. No executable product, interface, source fixture, workflow, numeric
policy, runtime version, permission or deployment behavior is changed. Existing
source-evidence inventory remains unchanged; this guide is not silently added.

## Data, State, Permissions And Cost

Documentation tests read source with the standard library; notebooks are parsed,
not imported or executed. Normal existing CI may run, including Spark because its
existing path filter includes this guide. No workspace authentication, table,
checkpoint, grant, customer data, business-data upload or production operation.

## Failure, Recovery And Validation

A mismatched documented function or semantic version fails a portable test. Revert
these three paths to recover; no schema/data/checkpoint/permission recovery is
needed. Compile and check whitespace locally; observe source-backed tests in the
complete CI checkout. Runtime pass counts are reported only from observed logs,
not invented in this guide. The documentation-only increment may use the lightweight
read-only review path; author checks are not independent approval of the whole
integration candidate. Full-candidate independent reviews and explicit human
acceptance remain pending. No merge is authorized.
