# AI-Assisted Delivery Workflow

This repository uses agents for implementation, criticism, testing and explanation. Human acceptance remains the final engineering gate.

```text
bounded change brief
  -> candidate implementation
  -> independent code review
  -> adversarial production review
  -> automated evidence
  -> review package
  -> human acceptance
  -> merge and deploy
```

## 1. Define A Bounded Change

Start with `docs/change_brief_template.md`. Specify the problem, observable acceptance criteria, non-goals, architecture boundaries, state changes, permissions, failure behaviour and rollback. Prefer a small outcome that can be understood independently; split changes above roughly 500 implementation lines unless their coupling is explicit and justified.

## 2. Implement On An Isolated Branch

- Branch from the latest `main`.
- Inspect the dirty worktree before editing.
- Stage explicit paths instead of using `git add -A` in a mixed worktree.
- Keep behaviour changes, tests and necessary documentation in the same bounded change.
- Do not merge with unresolved blocking findings or findings lacking a disposition. A consciously deferred non-blocking finding needs a rationale, owner and target date.

## 3. Separate The Review Roles

Use different contexts or agents for these roles whenever a change affects runtime/data semantics, schemas or interfaces, keys/write modes, checkpoints/replay, deployment/workflows, IAM/secrets, stateful tooling, external/destructive effects, availability, material cost, or the enforcement tooling itself. Documentation- or test-only changes without executable or contract effects may use a single read-only review when the pull request explains why that lightweight path is safe.

### Correctness And Maintainability Reviewer

Review without editing. Look for incorrect assumptions, unnecessary abstraction, unclear ownership, poor testability, compatibility breaks and technical debt. Report each finding with severity, file/line evidence, impact and remediation.

### Production Adversary

Ask what breaks under retries, partial writes, duplicate or late data, schema changes, missing permissions, unavailable services, large inputs and concurrent runs. Inspect observability, recovery, security and cost rather than only the happy path.

### Final Evidence Reviewer

Do not modify code. Produce a review package covering:

- what changed and why;
- architecture and important data/control paths;
- state, side effects and permissions;
- assumptions and failure modes;
- security, observability and cost implications;
- automated evidence and checks not run;
- rollback and recovery;
- unresolved questions or debt;
- up to ten exact `path:line` parts of the diff that deserve human inspection, with a reason for each.

Agent agreement is not acceptance. Similar agents can repeat the same mistaken assumption.

## 4. Run Automated Gates

Run:

```bash
scripts/run_acceptance_checks.sh
python3 scripts/generate_review_package.py \
  --base origin/main \
  --output .review/review-package.md
```

The acceptance script validates the current index, exports that exact candidate to an isolated temporary tree, compiles Python and runs unit tests there, then checks the complete branch diff for whitespace errors. Unrelated dirty working-tree files therefore cannot alter the candidate's test evidence. CI repeats the portable checks in Docker and publishes the structured review package for pull requests.

When the current `HEAD` has staged changes, the package compares the merge base to the staged index so a pre-commit candidate is not omitted. Otherwise it compares committed refs. In pull-request CI it records the exact PR head, the tested checkout/merge SHA, the base-ref tip and the merge base separately. Its changed-file shortlist is heuristic; the final reviewer supplies the semantic `path:line` inspection points.

The repository contract script reads exact Git-index bytes when Git metadata is present, refuses staged or branch-changed files that have different unstaged bytes, rejects symlinks and uninspectable/oversized text, validates JSON/reporting assets, and performs a narrow known-signature secret check. It is not history-aware secret scanning or a substitute for GitHub push protection/GitGuardian. These gates also do not prove Spark or Databricks runtime behaviour. Changes to transformations, bundles, permissions or deployment helpers need proportionate integration evidence, or an explicit record that the check remains unrun.

## 5. Use The Pull Request As The Durable Record

Complete `.github/pull_request_template.md`. Link the change brief, list exact commands and results, record independent findings and dispositions, and state rollback steps. Generated `.review/` files are temporary evidence and are not committed.

## 6. Perform Human Acceptance

The human reviewer does not need to retype or inspect every line, but should understand and be able to defend:

- the problem and why this design solves it;
- architecture and important control/data flow;
- state mutations and external side effects;
- failure, retry, recovery and rollback behaviour;
- permissions, secrets and trust boundaries;
- operational, performance and cost implications;
- tests proving the important behaviours and their limitations;
- unresolved risks being consciously accepted.

Only the human reviewer marks the acceptance decision. Until that explicit decision, keep the pull request unmerged.

## Late-Night Pattern

When the human reviewer is tired, agents may prepare candidate code, tests, critiques, documentation and a review package on an isolated branch. Defer acceptance and merge until the reviewer is fresh. Unattended work must not deploy, modify production state or bypass the acceptance gate.

## Repository Settings To Configure

Repository files cannot enforce every control. Configure a GitHub ruleset for `main` that requires pull requests, the CI validation check and resolved conversations, and prevents force-pushes and branch deletion. If a second human maintainer is available, require one human approval. Enable secret-scanning push protection and dependency/security update tooling where the repository plan supports them.
