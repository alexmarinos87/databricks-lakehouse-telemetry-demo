# Change Brief: Build a combined portfolio source-evidence snapshot

## Problem

Draft #138 profiles synthetic fixtures, while reporting assets and validation
entrypoints remain separate. A reviewer needs one reproducible, offline package
showing what the selected source contains without mistaking it for workspace
execution. This is the planned snapshot increment in #46, stacked on corrective
PR #142 and therefore also dependent on #138 and #137. No predecessor is accepted.

## Acceptance Criteria

- One command produces deterministic JSON and Markdown in a new directory.
- Dataset aggregates reuse the existing validator/profiler, preserve replay counts
  and do not inflate operational measures with identical duplicate events.
- All manifest-listed reporting SQL files and fixed evidence entrypoints are
  inventoried with relative paths, byte sizes and SHA-256 digests.
- A content digest covers the snapshot, including the selected source inventory.
- Invalid or ambiguous manifests, unsafe/missing/non-regular/oversized inputs,
  changed inputs, altered fixture selection and existing outputs fail closed.
- No raw CSV rows, SQL text, manifest descriptions, local absolute paths or
  environment values are copied into the package.
- Test execution, SQL execution, Git commit provenance, live governance and
  Databricks runtime remain explicitly unverified by this command.
- Exact-candidate CI, artifact compatibility and review-package generation pass;
  independent reviews and human acceptance remain separate gates.

## Non-Goals

No CI workflow changes or automatic publication in this increment. No repository
settings, network/provider requests, subprocesses, Databricks plans or deployment.
No Spark/SQL execution, business-logic changes, data migration, new dependencies,
query publication, permissions, checkpoints, schedules or production activity.
No cryptographic authenticity, clean Git checkout, complete dependency graph or
hostile-filesystem sandbox claim. Hashes identify selected bytes, not trusted Git
history. No static declaration of passed repository tests is accepted as evidence.

## Architecture Boundaries

Four new files only: the snapshot module, CLI, behavioural tests and this brief.
The existing dataset profiler and corrected non-overwriting writer are reused
without changing their contracts. Fixed documentation, validation and builder
source entrypoints are hashed but not executed. Manifest-listed SQL is read only.
Keep implementation below approximately 500 lines excluding tests and this brief.

## Data, State And Side Effects

Inputs are the default synthetic sample/increment files, the reporting manifest,
its listed SQL, and a fixed source-evidence allowlist. Read ceilings are 100 files,
2 MB per file and 10 MB total; reporting SQL is additionally capped at 100 KB each,
with at most 50 assets and 32 fixture files. Aggregate grain is unchanged from
#138: first validated observation per unique event_id. A before/after snapshot
and fixture-set recheck detect source drift before publication. There is no
replay, checkpoint or schema migration. Output is two UTF-8 files in a fresh local
directory. Identical selected bytes produce identical output without timestamps.

## Security, Permissions And Cost

Caller-controlled source/output directories and ordinary local file permissions
only; retain #142's trust boundary. Do not point the tool at private/live data.
Source hashes do not validate SQL semantics, privacy or authenticated provenance.
No tokens, ignored bootstrap configuration or environment values are read.
Service cost and compute SKU delta: N/A. Local output is bounded by selected input
limits. No scheduler or background process is introduced.

## Failure And Recovery

Failures return a nonzero exit code with bounded categories rather than provider
messages or raw input. No successful package is emitted on input failure. Reuse
#142's conservative cleanup: unverifiable partial output or newly created parent
directories may remain for inspection. Correct the input and rerun into a fresh
caller-controlled output directory. Rollback removes this additive CLI/module;
no data, permissions or checkpoint recovery is needed. RTO/RPO: N/A.

## Validation Plan

Behavioural tests cover actual repository fixtures, deterministic package bytes,
replay-safe aggregates, manifest validation, missing/symlink/oversized assets,
input/selection drift, source-digest binding, CLI success/failure and non-overwrite.
Run the repository acceptance script and generate its review package where a full
checkout is available. This execution container cannot resolve GitHub for cloning;
local compilation is not a full-suite substitute. Exact-head GitHub CI must supply
actual full-repository evidence, with local and remote checks reported separately.
Spark and authenticated Databricks tests are not applicable to the new offline
builder. Independent correctness and adversarial reviews remain required before
acceptance; author review and green tests do not replace them.

## Operator Command

```bash
python3 scripts/build_portfolio_snapshot.py --output-dir .review/portfolio-snapshot
```

The output directory must not exist. Use a fresh name for every rerun. Optional
`--repository-root` selects a caller-controlled source checkout; it does not assert
that the directory matches a Git commit. The command does not run tests or SQL.
