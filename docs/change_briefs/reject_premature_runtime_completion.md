# Change Brief: Reject premature runtime completion

## Problem

Author subprocess probes against PR #148 head `c6af60c` showed successful exits
without complete execution: `load_tests` raising `SystemExit(0)`, a suite stopping
after one of its two declared tests, and a declared suite returning without
running any tests. A custom suite raising `SystemExit(0)` during execution also
escapes the old launcher successfully. These probes use Python 3.13.5 locally;
Python 3.11 evidence must come from the existing pinned CI environment.

Normal module import-time SystemExit was already converted into a loader error
locally. It is not claimed as another reproduced defect. The public unittest
`wasSuccessful()` result describes tests run so far, not whether every discovered
test ran. The runner must separately verify completion.

## Acceptance Criteria

- Discovery/counting and execution SystemExit return a bounded nonzero failure,
  without printing arbitrary exit details.
- Actual testsRun equals the count captured before execution and shouldStop is
  false; zero, partial, excess or explicitly stopped execution cannot pass.
- A normally completed custom suite still passes. Argument parsing/help, warning
  options, skips, expected failures and ordinary errors keep their existing rules.
- Real subprocess regressions fail before the fix and pass afterwards; run exact
  acceptance and the complete Spark suite on the final candidate in CI.

## Non-Goals

No new test inventory, hostile-code sandbox, module discovery restrictions,
warning suppression, global signal handling or changes to Spark/data semantics.
A suite that misreports both its declared count and result is outside this guard;
this does not prove that every possible test was discovered. KeyboardInterrupt
is not caught by these SystemExit handlers.

## Architecture Boundaries

Only the runtime launcher, its portable tests and this brief change. Capture the
count before unittest may consume the suite. Catch SystemExit inside discovery
and execution only, not argparse help/usage exits. Seven added test methods use
real child processes, including a completed custom-suite positive control.

## Data, State And Side Effects

No table, schema, monetary policy, source fixture, checkpoint, replay, lineage,
permission or publication change. Inputs are local unittest suites; outputs are
existing text diagnostics and process status. No network or provider operations
are added. Partial execution never grants complete runtime evidence.

## Security, Permissions And Cost

No dependency, credential, action pin, workflow permission or trigger changes.
Existing two-CPU, 4-GiB, twenty-minute runtime CI bounds remain. Additional work
is standard-library subprocess tests and constant-time count/result checks.
No schedule, deployment or workspace compute; no increase to the CI job ceiling.

## Failure And Recovery

Correct the failing discovery hook or custom suite and rerun, rather than marking
missing execution as success. Reverting this correction restores the false-success
paths. Delta versions, checkpoint/ACL restore and RTO/RPO are not applicable.
Independent correctness/adversarial reviews and exact human acceptance remain
pending; author tests do not constitute those reviews or approval to merge.

## Validation Plan

Run the launcher regression suite, compilation, Python 3.11 grammar and whitespace
checks locally. Run scripts/run_acceptance_checks.sh and real Spark in CI. Local
GitHub clone is unavailable (DNS failure) and Docker/PySpark are absent; a checked
source subset is not a full repository checkout. Inspect the two SystemExit
handlers and the saved-count/shouldStop check. Review the separately constructed
#147/#148 integration candidate using its own complete CI evidence.
