# Change Brief: Verify setup-action compatibility with the pinned CLI

## Problem

Dependency PR #146 updates five setup-action references but leaves the exact-pin
contract expecting the old revision. The existing CI failure is that stale expected
pin, not a demonstrated runtime incompatibility. All steps still explicitly request
CLI 1.14.1; an action tag is not the requested binary version.

## Acceptance Criteria

- Synchronize the test's exact action SHA without removing pin/count assertions.
- Keep CLI 1.14.1 and the existing bundle/runtime compatibility baseline unchanged.
- Run the actual acceptance script against the event base before installation.
- Exercise the proposed action and require version JSON for release 1.14.1, with
  IsSnapshot false and an empty Prerelease; wrong or malformed output fails.
- The version command uses an isolated home and does not inherit auth tokens.
- Run exact-head portable CI, acceptance, installer smoke and artifact compatibility;
  report observed results separately from synthetic local subprocess tests.

## Non-Goals

No runtime version upgrade, workspace authentication, OIDC, SQL, bundle plan/apply,
data upload, permissions, deployment or automatic acceptance. No new required-check
settings. The path-filtered smoke workflow is not a universal required check.

## Architecture Boundaries

Modify only the expected SHA in tests/test_deployment_contract.py. Add a separate
CLI Installation Compatibility workflow, check_cli_installation.py, ten behavioral
and wiring tests, and this brief. Keep the inherited five workflow changes and
existing deployment gates unchanged. Do not couple this dependency candidate to
portfolio integration #147 or edit that branch from this workstream.

The smoke workflow checks out github.sha and passes the immutable PR base or
push-before SHA to the existing acceptance script. A missing base fails closed.
It then invokes the exact proposed action with explicit version 1.14.1 and runs
only the local version command, without the optional network update-check flag.
Version/build semantics were inspected in the upstream v1.14.1 version command and
build.Info definition; the setup action passes its explicit version input through
to setup_release.sh rather than its default VERSION file.

## Data, State And Side Effects

The installer downloads a public CLI release into the hosted runner's temporary
storage and adds its directory to that job's PATH. The checker creates and removes
a private temporary home/configuration directory. It prints only version and status
categories, not the binary's other metadata or stderr. No business data, table
grain, replay, checkpoint, schema or publication changes. No new artifact is stored.
The existing bot branch is deliberately maintained by one non-forced fast-forward;
Dependabot's automatic conflict repair may no longer apply after an author edit.

## Security, Permissions And Cost

contents: read only, non-persistent checkout credentials, immutable action pins,
five-minute job timeout and per-ref cancellation. No environment or secret mapping.
Child environment contains only PATH, HOME, XDG_CONFIG_HOME and an absent config
file path. Each version invocation has a ten-second timeout. JSON parsing rejects
more than 16 KiB after command completion; this is not a hostile-binary memory
sandbox. The action/binary remain a reviewed supply-chain trust boundary; version
self-reporting is not a binary signature verification. One extra short job runs
only for relevant PR/main changes. No Databricks compute is started.

## Failure And Recovery

Missing binary, timeout, command error, invalid/duplicate JSON and incorrect release
identity fail the smoke job. Raw errors are not echoed and there is no fallback to
latest. Correct the candidate and rerun in a fresh hosted runner. Rollback reverts
this bounded follow-up, retaining the original dependency PR as blocked until its
contract is synchronized. No Delta, schema, checkpoint or ACL recovery is involved.

## Validation And Review

Local tests include a real synthetic executable to prove argument/environment
isolation, plus fault injection and wiring checks. They do not install Databricks.
The copied baseline contract is checked against its original Git blob before the
one-line modification. Full local acceptance cannot be claimed from a subset;
observe the actual script and actual installer in GitHub CI. Independent correctness
and adversarial reviews and explicit human acceptance remain pending. Inspect the
expected SHA, smoke gate ordering, version parsing and child environment boundary.
