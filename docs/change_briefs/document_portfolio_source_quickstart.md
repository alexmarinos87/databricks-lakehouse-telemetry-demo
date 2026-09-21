# Change Brief: Make the source-evidence overview discoverable

## Problem

The integrated builder works but is absent from the landing-page instructions.
A reviewer should be able to run the no-workspace overview and understand its
files, safe reruns, and distinction from CI or authenticated runtime evidence.

## Acceptance Criteria

- README links a focused guide and shows the existing source-builder command.
- Preserve the existing 100-line landing-page budget and all current claims/links.
- Execute the guide's actual command with only its output location redirected to
  a temporary directory; validate the real JSON/Markdown and source-only flags.
- A repeated command must fail and leave all original package bytes unchanged.
- Guide links resolve inside the repository. No new application behavior is added.
- Observe current-head CI, the actual acceptance script, and artifact verification.

## Non-Goals And Architecture Boundaries

Only README, docs/portfolio_source_quickstart.md, tests/test_portfolio_quickstart.py
and this brief change. No new CLI, source logic, workflow, dependency, fixture,
validator, numerical policy, schema, deployment behavior or acceptance authority.
The tests lock the parsed Markdown command to expected arguments before executing
it without a shell. This is documentation and regression evidence for existing
behavior, not another gate framework.

## Data, State, Permissions And Cost

The documentation itself has no side effects. Tests run the existing source-only
builder into disposable local directories, with a fifteen-second subprocess
limit. No network, Databricks compute, credentials, grants, table, checkpoint,
replay, schema, business-data upload or production operation is introduced.
README already belongs to the selected-source inventory: its hash and snapshot
digest change, while the dataset profile, reporting catalogue and verification
flags must remain unchanged. The guide is not silently added to that inventory.

## Failure, Recovery And Rollback

The guide instructs the reader to correct source/layout failures and select a new
output directory, not overwrite evidence or delete uncertain output automatically.
The actual builder retains its existing conservative partial-output behavior.
Rollback reverts these four documentation/test changes and needs no table/schema,
checkpoint or permission recovery. RTO/RPO and Delta restore are not applicable.

## Validation And Review

Four tests cover landing-page command/budget, local links, the real builder and
safe repeated invocation. Compile and check syntax/whitespace locally; do not
claim full CLI integration from an incomplete source subset. GitHub CI must run
the complete tests and actual acceptance script. Download and inspect the fresh
artifact to confirm that only expected selected-source metadata changed.

This small documentation/test-only increment has no executable product or contract
effect, so a single read-only documentation review is proportionate. Author review
of commands and claims is not represented as independent approval. The full #147
candidate's independent correctness/adversarial reviews and explicit human
acceptance remain outstanding. The separate dependency #146 is not incorporated.
