# Change Brief: Publish portfolio source evidence through CI

## Problem

PR #143 builds the combined source snapshot but does not retain it as a workflow
artifact. Reviewers need the actual snapshot, its exact tested checkout and run
identity, and evidence that downloaded files match the producer bytes. This is
source-evidence increment 4 of 4 in #46, dependent on #137, #138, #142 and #143.
All predecessors remain candidates; preparing this increment does not accept them.

## Acceptance Criteria

- CI builds and publishes the snapshot only after the existing validate job passes.
- The PR head and base are recorded separately from the actual tested merge commit;
  Git verifies the checkout identity, merge parents and selected source bytes.
- Push-to-main execution records the exact push commit, not an inferred PR identity.
- Repository, event, run ID and attempt are bounded; invalid context fails closed.
- Original snapshot JSON/Markdown bytes and their source-only claims are unchanged.
- A third file, ci-provenance.json, binds the two payload hashes, producer files,
  checkout tree, candidate refs and run identity without claiming human acceptance.
- Upload is an explicit three-file allowlist, non-overwriting, uniquely named by
  checkout/run/attempt, with seven-day retention and hidden files excluded.
- Download verification rejects missing, extra, non-regular, symbolic-link or
  changed files against the retained producer bytes, not the downloaded manifest.
- The successful summary appears only after downloaded-byte verification passes.
- Exact-head CI, artifact compatibility, actual portfolio publication and generated
  review-package evidence are recorded separately from local validation.

## Non-Goals

No merge, automatic acceptance, deploy, provider authentication, Databricks plan,
SQL execution, data upload, grants, checkpoints, schedules, retention operation or
production work. No branch-protection or required-check setting change. No new
static credentials, external services, dependencies, runtime transforms or data
contracts. No signed attestation or independent certification of CI context.

## Architecture Boundaries

Four paths: append one dependent job to .github/workflows/ci.yml; add the standalone
prepare/verify-download CLI, its behavioural tests and this brief. Existing validate
steps, artifact-compatibility workflow, snapshot builder, source fixtures, README,
PROJECT_REFERENCE.md and Databricks workflows remain unchanged. Reuse the immutable
checkout/upload/download action SHAs already exercised in Artifact Compatibility.

The new job needs validate, checks out github.sha explicitly and invokes the source
builder before the packaging CLI. The latter reads local Git only: one commit
metadata read, one bounded exact-path tree inventory, then a checkout recheck. It
compares selected bytes with Git blob identities and source SHA-256 records. It also
binds its own script and CI workflow bytes to the same checkout. The original
snapshot's git_commit_provenance remains not_verified because that builder did not
verify it; the separate CI envelope reports the additional selected-byte check.

## Data, State And Side Effects

Input/output grain: one source package and one CI envelope per checkout/run/attempt.
No business-row, deduplication, replay, table, checkpoint or schema changes.
Only three UTF-8 artifact files are published. Candidate/base/tested refs, tree,
run IDs and hashes are public provenance; no source rows, SQL text or tokens are
added to the envelope. Unselected local files are outside its claim, not evidence
of a clean entire worktree. Temporary directories live under RUNNER_TEMP. Output
uses #142's non-overwriting writer; uncertain partial output is not uploaded.

The offline helper accepts CI metadata from its environment; only the inspected
workflow and GitHub run record establish that context. A local caller can supply
metadata, so the JSON alone is not an authentication or acceptance credential.
A PR can change its own code and workflow; review remains required. Consumers must
check the exact successful publication job and repository/run/attempt, not merely
artifact presence. An artifact may already exist if a later download check fails.

## Security, Permissions And Cost

Inherit contents: read only; no environment, secrets mapping, id-token permission,
privileged pull_request_target event, workflow dispatch or background schedule.
The existing push-to-main and pull_request triggers are unchanged. The job has a
five-minute timeout and per-ref concurrency cancellation. It does not rerun the
Docker test suite: validate remains the dependency. Git reads have ten-second
command timeouts, restricted subprocess environments and bounded parsed output.

Retain the caller-controlled filesystem boundary from #142. Package input is
exactly two files; prepared/downloaded input exactly three, with a 1 MB per-file
and 3 MB total limit. Selected source reads retain 2 MB per-file and 10 MB total
limits, at most 100 listed sources plus two producer paths. Git paths are canonical
and restricted before use, and no shell or network Git commands are invoked.
Seven-day artifact storage and one bounded hosted job per eligible CI run are the
added cost. No cloud compute SKU or Databricks resource is started.

## Failure And Recovery

Invalid context, stale checkout, wrong merge parents, dirty/uncommitted selected
bytes, malformed source evidence or failed package generation prevent upload.
Upload/download failure or mismatched downloaded bytes fails the publication job;
no success summary follows. Existing artifacts are never silently overwritten.
Rerunning a job supplies a new attempt number and a fresh hosted workspace. Inspect
uncertain partial local output before removing it; automatic recovery does not
recursively delete existing directories. Rollback reverts these four-path changes.
Data recovery, Delta restore, checkpoint/ACL rollback and RTO/RPO: not applicable.

## Validation Plan And Review

Local tests use real temporary Git repositories and injected filesystem/provider
failures. Cover push and merge-parent identities, changed/untracked source and
producer bytes, strict metadata, repeated attempt provenance, unchanged snapshots,
source-only flags, duplicate JSON keys, input limits, symlinks, existing output,
exact downloaded shape/bytes, sanitized CLI failures and workflow gate contracts.
The actual builder plus GitHub artifact service must also be exercised by the new
job; unit tests do not substitute for this integration evidence.

The local session could not resolve github.com for cloning. A local subset contains
fetched, blob-verified base CI/helper files and the new candidate files, not a full
upstream checkout. Local focused tests and compilation can run there; the full
acceptance script cannot. Full repository checks and generated review packages
must be observed in exact-candidate CI. Record these distinctions in the PR.

Independent correctness/maintainability review and adversarial production review
remain required, as does explicit human acceptance. Author self-review and green
checks do not discharge any predecessor or current review gate.

## Human Inspection Points

Review the needs: validate edge and permissions; exact checkout/head/base wiring;
source blob verification and provenance boundaries; explicit upload paths, lifetime
and rerun naming; download comparison against producer bytes; failure handling and
whether the artifact's run actually completed successfully. Do not use this source
artifact as a deployment approval or as effective workspace governance evidence.
