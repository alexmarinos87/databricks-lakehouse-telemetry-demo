# Change Brief: Reject root-level probe and sentinel artifacts

## Problem

Ten scratch files were accidentally committed at repository root while connector
capabilities were being inspected. The existing contract suite validates tracked
file safety, JSON, reporting assets and known secret signatures, but it does not
classify root-level scratch markers as invalid repository content.

## Acceptance Criteria

- [ ] CI fails when a regular root file uses the double-underscore sentinel form
      such as `__probe__` or `__invalid__`.
- [ ] CI fails for the extensionless root marker `nonexistent` involved in the
      incident.
- [ ] Every incident path is covered by the policy.
- [ ] Normal root project files remain accepted.
- [ ] The guard runs automatically through standard-library test discovery.

## Non-Goals

- Rewriting Git history or force-moving `main`.
- Deleting the incident files; that is isolated in the preceding cleanup PR.
- Banning Python package `__init__.py` files below the repository root.
- General filename style enforcement or unrelated repository cleanup.

## Architecture Boundaries

- Components and files allowed to change:
  - `tests/test_repository_root_hygiene.py`
  - this change brief
- No runtime, data, workflow, bundle or public interface changes.
- The policy applies only to regular files directly under repository root.

## Data, State And Side Effects

- Input: the candidate repository root visible to the test runner.
- Output: a deterministic test pass or an offender list.
- External reads or writes: none.
- Databricks state, tables, checkpoints, data and permissions: unaffected.

## Security, Permissions And Cost

- Credentials and provider identities: none.
- Permissions: ordinary repository-file read access in CI.
- Cost: one bounded directory listing during the standard test suite.

## Failure And Recovery

- A prohibited file causes the CI suite to fail with its exact root filename.
- Recovery is to remove the scratch artifact on the candidate branch and rerun CI.
- Rollback is a source revert; no external state requires restoration.

## Validation Plan

- Prove the accepted repository root has no offenders.
- Prove all ten incident filenames are rejected by the policy.
- Prove representative normal root files remain accepted.
- Run exact-head `CI / validate` and artifact compatibility on the stacked PR.
- Inspect the two-file diff and confirm no runtime or provider side effect.
