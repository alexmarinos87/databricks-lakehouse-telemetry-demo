# Change Brief: Preserve exact bounded source-profile cost totals

## Problem

The profiler adds Decimal values with the caller's arithmetic context. At default
28-digit precision, adding 1234567890123456789012345678.90 and 0.01 yields
1234567890123456789012345679, not the exact 1234567890123456789012345678.91.
A lower caller precision changes the result again. These are valid finite source
amounts. Reconstructing an artifact with the same arithmetic cannot expose the error.
Compact extreme exponents can also expand into output much larger than the input.

This is a source-evidence correction for #46, based on #144's completed CI-gate
increment. It does not change #138's branch or accept any predecessor. Separating
this correction keeps numerical semantics reviewable apart from CI wiring.

## Acceptance Criteria

- Cost addition uses a private explicit context, never the caller's arithmetic.
- Inexact results raise a sanitized profile error instead of silently rounding.
- Caller precision, rounding, exponent bounds, traps and flags remain unchanged.
- Each cost coefficient has at most 1,000 digits and its exponent is in [-1000, 1000].
  Out-of-budget representations fail before addition or fixed-point rendering.
- Precision derives from those bounds and the existing 100,000-row source ceiling.
- Existing fixture totals and replay exclusion, schema and source-only flags remain.
- Arithmetic tests and actual validator/profile tests pass, followed by full CI,
  the actual exact-index acceptance script, and actual artifact publication.

## Non-Goals

No penny quantization, currency conversion, financial business policy, source-schema
migration, Spark/SQL runtime change, new dependency, deployment or live-data action.
No change to the repository source validator, checkpoint, replay identity or writer.
No claim that source evidence or CI substitutes for independent review/acceptance.

## Architecture Boundaries

Modify only dataset_profile.py and add a focused test file and this brief. The
existing first-observation-per-event loop remains the aggregation grain. Only the
cost addition is replaced. The source validator retains its own row/byte ceilings.

## Precision And Representation Budget

Let D=1000 coefficient digits, E=1000 absolute exponent, and N=100000 rows.
The greatest input integer span is D+E digits, while alignment to the smallest
exponent needs at most another E places. Summing N non-negative inputs requires
at most len(str(N)) additional digits. Precision D+2E+len(str(N))+1 therefore
covers the full sum with an extra guard digit. Inexact and overflow traps are a
fail-closed backstop. Exponent bounds are explicit and do not inherit mutable
caller/default settings. This is an output/resource budget, not a monetary limit
or a rounding policy. Previously accepted syntactically finite representations
outside this budget now fail profiling deliberately, including extreme zero
exponents; they are not silently normalized. Existing source fixtures are within it.

## Data, State, Security And Cost

Read bounded synthetic source only. Maintain output schema, unique-event grain,
identical-replay exclusion and existing new-directory output. No credentials,
network calls, grants, table writes or scheduled work. Added computation is one
small private context and exact bounded addition per unique event. Expanded cost
output is bounded by the digit/row budget, not unbounded exponent expansion.

## Failure, Recovery And Rollback

Budget violations use machine_event_profile_cost_limit_exceeded; unexpected
Decimal failures use machine_event_profile_cost_arithmetic_failed without raw
input. Existing callers retain their usual profile-error handling. No successful
package is produced on failure. Correct out-of-budget input and regenerate into
a fresh directory. No data/schema/checkpoint/permission recovery is involved.
Reverting only this fix restores known rounding and is not an equivalent fallback.
Delta snapshots, RTO/RPO and migration steps are not applicable.

## Validation And Review

Nine arithmetic tests cover long amounts, changed contexts, ordering, budget edges,
invalid amounts, canonical zero and an independent scaled-integer reference. Four
integration tests exercise the real validator/profile with replay, limited caller
precision, excessive representations and committed fixtures. Local arithmetic
extraction tests are not represented as full-module or repository integration.
Current-head GitHub CI must run all tests, the actual acceptance script and the
artifact round trip. Generated review evidence must distinguish candidate/head
from tested merge, and actual gates from unrun local/Databricks checks.

Inspect the budget proof, _add_profile_cost, its call after replay exclusion and
the actual-profile tests. Independent correctness/adversarial review and explicit
human acceptance remain pending. No merge or deployment is authorized.
