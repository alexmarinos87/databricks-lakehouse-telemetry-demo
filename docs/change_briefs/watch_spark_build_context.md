# Change Brief: Watch Spark Docker context selectors

## Problem

At combined candidate `4f51ca4`, both Spark Runtime event filters omit
`.dockerignore` and `Dockerfile.spark-ci.dockerignore`. The job builds with
`docker build -f Dockerfile.spark-ci ... .`; the Dockerfile uses `COPY . .`.
Changes to these ignore files can alter the runtime image without selecting the
Spark workflow. This is a coverage gap, not a demonstrated incorrect prior image.

Docker documents root ignore-file filtering and the precedence of a
[Dockerfile-specific ignore file](https://docs.docker.com/build/concepts/context/#dockerignore-files).
GitHub documents [independent event path filters](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onpushpull_requestpull_request_targetpathspaths-ignore).

## Acceptance Criteria

- Both existing event filters contain both exact ignore-file paths once.
- Every previous filter, job step, action pin, permission and resource limit stays.
- Tests fail on the original four missing event/path combinations, then pass.
- Full exact-index acceptance, review generation, Spark and source publication run
  on the final combined candidate in existing CI; report observed results only.

## Scope, Interfaces And Non-Goals

Only the Spark workflow, `tests/test_spark_build_context_wiring.py` and this brief
change. The executable delta is four additive filter entries. Do not create or
edit ignore files, broaden to all paths, change images/dependencies, suppress
warnings, alter test discovery, remove assertions or change deployment behavior.
No data transformations, monetary policy, schema, keys, null handling, checkpoints,
replay, fixtures, permissions, API or artifact-schema changes.

## State, Cost, Failure And Recovery

The existing Spark job may additionally run for changes to the two named paths.
It retains two CPUs, 4 GiB, a twenty-minute job limit, contents: read, immutable
pins and per-ref cancellation. No schedule, credentials, provider operations or
workspace compute. Per-run budget does not increase; total CI usage can increase
when these files change. Revert only this increment to restore the coverage gap.
Delta versions, checkpoint/ACL restore, data recovery and RTO/RPO are N/A.

## Validation And Review

Three standard-library tests inspect this repository's known block-style workflow
and its actual Docker build arguments. They are source-contract checks, not a
replacement YAML parser, Docker engine or GitHub event scheduler. No isolated
ignore-file-only push or PR event is claimed. Verify that removing the four new
entries reproduces the original workflow byte-for-byte.

Local source-subset checks cannot replace full GitHub acceptance: cloning fails
DNS and Docker is absent in the authoring environment. Independent correctness
and adversarial reviews remain required for this workflow change. Green checks
and author probes do not grant human acceptance. Main and predecessor PRs remain
unchanged; the existing combined draft PR retains separately reviewable commits.
