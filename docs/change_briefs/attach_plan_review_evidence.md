# Change Brief: Attach policy review to every captured Databricks plan

## Problem

The repository can capture an exact structured Databricks direct-engine plan and
can review that plan offline, but the two controls were independent. A plan
workflow could therefore publish a technically valid artifact without producing
the repository policy decision that classifies destructive actions, target
crossover, permission changes, or unredacted values.

Duplicating review steps in each GitHub workflow would create drift between:

- manual development planning;
- manual production planning;
- the owner-only `/databricks-plan dev` command.

All three paths already call the same bounded plan-capture implementation.

## Outcome

Run the structured plan reviewer inside the shared plan-capture boundary after a
successful identity check, bundle validation, and JSON plan capture.

Every successful plan capture now produces:

```text
bundle-plan.json
bundle-validate.txt
evidence.json
summary.md
databricks-plan-review.json
databricks-plan-review.md
```

The review files are written into the existing evidence directory, so the
existing workflow artifact step retains them without introducing another upload
or another source of artifact naming and provenance.

## Execution order

```text
authenticated service-principal verification
  → bundle validation
  → exact direct-plan JSON capture
  → repository policy review
  → sanitized evidence and summary
  → workflow artifact retention
```

Identity-only mode does not invoke the reviewer.

## Failure behaviour

### Accepted plan

The main evidence record remains `status: succeeded` and includes bounded review
metadata:

- review status and schema version;
- policy path;
- review JSON and Markdown filenames;
- plan digest;
- resource and finding counts.

### Structurally valid but blocked plan

The reviewer first writes its full sanitized blocked evidence. The plan capture
then records:

```text
status: failed
failure.stage: review
failure.category: plan_blocked
review.status: blocked
```

This ordering preserves the reason for the block before the workflow fails. The
existing `!cancelled()` artifact steps can retain the directory even though the
capture command exits non-zero.

### Malformed or unsupported direct plan

The exact provider plan and main evidence remain available, while the capture
records a stable review-stage failure category. No fabricated review decision is
written.

## Security boundary

The integration:

- invokes no additional external process or network request;
- reuses the already captured local plan file;
- uses the repository-owned review policy from the accepted source commit;
- does not copy raw resource addresses, state values, lineage, or credentials
  into the main evidence record;
- carries only sanitized review metadata through a bounded `EvidenceError`.

It does not request another OIDC token, authenticate again, deploy, upload data,
execute workflows or SQL, change permissions, or touch production.

## Compatibility

The owner-only issue command and both manual target plan jobs already call
`scripts/capture_databricks_plan.py`. No workflow-specific review implementation
is required. Future plan-producing workflows must use the same capture entry
point or explicitly reproduce this evidence contract.

A future Databricks direct-plan schema change remains fail-closed in the reviewer
and therefore blocks plan publication until the parser and policy are reviewed.

## Validation

Tests prove:

- a valid direct plan produces accepted review JSON and Markdown;
- the main summary exposes the accepted decision;
- a destructive plan writes blocked review evidence before capture failure;
- the blocked plan returns `review/plan_blocked` rather than losing evidence;
- malformed direct JSON fails at the review boundary without fabricating review
  files;
- identity-only mode performs no review;
- existing OIDC, static-secret, timeout, output-path, and provider-failure
  controls remain intact;
- the source order is plan capture, review, then success publication.

## Rollback

Source rollback is a normal revert. Removing this integration must not be treated
as permission to apply a plan without an equivalent policy decision in the
retained artifact.
