# Change Brief: Bind the CI review package to its event base

## Problem

Acceptance uses the immutable event base SHA, but review generation still supplies
origin/${{ github.base_ref }}. That remote-tracking ref can advance after the event.
A real temporary-Git reproduction with the unchanged review generator shows a
one-file candidate becoming a zero-file review when the tracking ref advances to
the tested merge; the original event SHA preserves the one-file diff. The generator
correctly interprets its input; the inconsistency is in CI wiring.

## Acceptance Criteria

- PR review generation supplies github.event.pull_request.base.sha as BASE_REF.
- Candidate head, tested merge and validation status remain distinct and unchanged.
- A moved/deleted tracking ref cannot change the immutable-event review comparison.
- An unavailable event base fails, without falling back to a newer branch tip.
- Preserve review-on-failure behavior, validate dependency and all permissions.
- Real-Git regression tests, actual acceptance against main, review generation and
  actual source-artifact publication pass on the exact integration candidate.

## Non-Goals

No review-generator rewrite or format change, signed attestation, branch-protection
mutation, automatic approval, merge, deployment, action upgrade or new workflow.
Do not copy the unrelated Databricks dependency or case-study drafts into the stack.

## Architecture Boundaries

One CI environment-value replacement, tests/test_review_event_base.py and this brief.
The generator remains unchanged and still accepts ordinary refs for local use.
Pass the SHA via the existing environment variable and quoted shell argument,
not directly interpolated executable shell text. GitHub pull_request validation
uses a tested merge commit; it must remain separately identified from the PR head.

## Data, State, Security And Cost

Only local Git metadata is read to produce review evidence. No table, row, schema,
checkpoint, grant or deployment change. CI contents: read, credential persistence,
existing action pins, timeouts, artifact retention and dependent publication remain
unchanged. No added service, schedule, privileged trigger or external write authority.
Tests operate in temporary local Git repositories with bounded fixture commands.

## Failure, Recovery And Rollback

The existing generator fails when the event commit is unavailable. Keep full commit
fetching enabled; correct checkout availability and rerun the relevant event rather
than substituting a current branch tip. Reverting the wiring restores mutable-base
review metadata and is not an equivalent provenance check. No data, permissions,
Delta or checkpoint recovery; RTO/RPO are not applicable.

## Validation Plan

Six tests include five real-Git cases plus workflow wiring: moved and deleted refs,
unavailable base, distinct candidate/merge identities, retained failure status and
immutable event input. The wiring assertion fails on the previous workflow and
passes on the correction. The real-Git cases exercise the unchanged generator.
Observe current-head full repository checks, actual acceptance, correct BASE_REF in
review-generation logs and artifact round-trip verification in GitHub CI.

## Consolidated Review Boundary

Use a new integration branch inheriting #145 and all eight source-evidence commits,
with this correction and the separate profiler CLI correction as distinct commits.
Open one draft against main so the full corrected stack can be inspected and tested
as a unit. Its cumulative scope is larger than 500 implementation lines because
it combines already-split candidates, not because this correction expands scope.
The smaller PRs retain their histories and remain open/unmodified for granular
review; no automatic supersession or acceptance is recorded. A single exact
acceptance decision can later select the integration candidate without requiring
an intermediate merge of a known-incomplete predecessor. This branch preparation
is not a merge into main, a rebase or independent approval. Review the full inherited
filesystem, source, numerical and CI boundaries as well as this one-line change.
Independent correctness/adversarial review and explicit human acceptance are pending.
