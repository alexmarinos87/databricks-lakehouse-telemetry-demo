# Repository Agent Instructions

AI-generated work in this repository is a candidate change, not an accepted change. Automated tests and agent reviews provide evidence; they do not replace human engineering judgement.

## Human Authority

- Never merge or deploy an AI-assisted change until the user explicitly accepts that exact change or pull request.
- An instruction to implement, test, commit, push or open a pull request is not approval to merge.
- Open draft pull requests by default unless the user explicitly requests a ready pull request.
- Another agent's approval is review evidence, not human acceptance.

## Before Editing

1. Read `docs/ai_delivery_workflow.md` and the relevant architecture and deployment documentation.
2. Inspect `git status` and preserve unrelated or untracked user work.
3. Define a bounded change using `docs/change_brief_template.md`.
4. State the acceptance criteria, non-goals, affected data/state, failure behaviour and rollback path.
5. Prefer one reviewable outcome. Changes above roughly 500 implementation lines should be split or explicitly justified; line count never overrides risk-based review triggers.

## Implementation Boundaries

- Do not perform unrelated cleanup or broad rewrites.
- Keep credentials and secret values out of source, logs, generated packages and examples.
- Preserve backward-compatible table and workflow contracts unless the change brief explicitly authorizes a migration.
- Make state-changing scripts bounded, idempotent where practical and explicit about partial failure.
- Record checks that could not be run; never imply that static contract tests validate Databricks runtime behaviour.

## Databricks And Data Invariants

Review every relevant change for:

- table grain, business keys, null handling, deduplication and referential integrity;
- late-arriving data, schema evolution, checkpoint compatibility and replay behaviour;
- overwrite, append and merge semantics, including backfill and rollback consequences;
- environment isolation for catalogs, schemas, volumes, checkpoints and resource names;
- workflow dependencies, retries, timeouts and partially completed runs;
- Unity Catalog permissions, service-principal scope and secret handling;
- observability, reconciliation, data-quality thresholds and alert ownership;
- cluster, SQL warehouse, storage and repeated-computation cost.

## Required Evidence

Before handoff:

1. Run `scripts/run_acceptance_checks.sh`.
2. Add or update tests for the important behaviour, not only source-text assertions.
3. Obtain an independent correctness/maintainability review for every full-review change.
4. Obtain an adversarial production review for every full-review change that asks what can fail, lose data, leak access or create unexpected cost.
5. Generate a review package with `scripts/generate_review_package.py`.
6. Report passed, failed and unrun checks separately.

## Review Conduct

- Review agents should be read-only unless explicitly assigned remediation work.
- Findings must include severity, evidence, impact and a concrete recommendation.
- Do not hide unresolved findings in prose. Record their disposition in the pull request.
- Never merge with an unresolved blocking finding or an undispositioned finding. A deferred non-blocking finding needs a rationale, owner and target date.
- The final handoff must identify the highest-risk files or code paths for human inspection.

### Full-Review Triggers

Independent and adversarial reviews are mandatory when a change affects runtime/data semantics, schemas or interfaces, table keys or write modes, checkpoints or replay, workflows or bundle/deployment behavior, IAM/grants/secrets, stateful automation, destructive or external side effects, availability, material cost, or the tooling that enforces these gates. A documentation- or test-only change with no executable or contract effect may use one read-only review, but the pull request must state why the lightweight path is safe.

## Human Acceptance Gate

The human reviewer should be able to explain the problem, architecture, data/control flow, state changes, side effects, failure modes, rollback, permissions, cost implications and the evidence supporting the change. Until then, the decision remains **pending** and the pull request must not be merged.
