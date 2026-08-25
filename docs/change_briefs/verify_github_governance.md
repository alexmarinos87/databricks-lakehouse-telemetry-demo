# Change Brief: Verify effective GitHub governance after bootstrap

## Problem

The repository contains dry-run-first automation for repository settings,
branch protection and four Databricks deployment environments. The bootstrap
currently performs only narrow post-write checks: it confirms that `main`
reports as protected and that non-squash merge methods are disabled.

That is not sufficient closure evidence. A partial, stale or manually altered
configuration can still leave any of the following wrong while the bootstrap
appears successful:

- required checks are not strict or do not include `validate`;
- administrators can bypass protection;
- force pushes, deletion or non-linear history remain allowed;
- review or conversation-resolution settings drift;
- an environment accepts branches other than `main`;
- deployment or runtime client IDs do not match the ignored bootstrap files;
- a static `DATABRICKS_CLIENT_SECRET` variable or secret exists.

Issue #44 requires independently verified effective settings, not only source
configuration or successful write requests.

## Outcome

Add one read-only command that compares effective GitHub state with the two
ignored bootstrap configurations:

```bash
python3 scripts/verify_github_governance.py \
  --github-config .bootstrap/github-governance.json \
  --runtime-config .bootstrap/runtime-identity.json \
  --output-dir .bootstrap/evidence/github-governance \
  --required-approvals 0
```

The verifier checks:

- repository merge and merged-branch cleanup settings;
- the branch endpoint and full `main` protection document;
- strict required checks containing `validate`;
- administrator enforcement, linear history, conversation resolution, review
  count, stale-review dismissal, force-push prevention and deletion prevention;
- existence of `dev-plan`, `prod-plan`, `dev` and `prod`;
- custom deployment-branch policies scoped exactly to `main`;
- exact `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID` and
  `DATABRICKS_RUNTIME_CLIENT_ID` environment values;
- absence of `DATABRICKS_CLIENT_SECRET` as either an environment variable or
  environment secret.

It writes deterministic JSON and Markdown evidence and exits non-zero when any
drift or read failure is detected.

## Security boundary

`GITHUB_ADMIN_TOKEN` is read only from the process environment and is never
accepted as a command-line argument. The client supports GET requests only and
sends the token only to the fixed public GitHub API host.

Evidence contains repository and environment names, branch state, stable drift
categories and SHA-256 fingerprints of expected non-password values. It never
stores the token, workspace hosts, deployment client IDs, runtime client IDs or
secret values.

The output directory must be a regular directory and cannot be a symbolic link.
API failures are reduced to bounded categories without provider response bodies.
Inventory pagination is finite and fails closed if results appear truncated.

## Compatibility and migration

The verifier consumes the existing ignored `github-governance.json` and
`runtime-identity.json` structures. It does not change the bootstrap writer
schemas or GitHub settings.

A sole-maintainer repository uses `--required-approvals 0`. Change the argument
to one only when an independent maintainer is configured and the protection
rule is intentionally updated.

Existing settings may initially produce a blocked report. That is expected
evidence of drift, not a reason to weaken the verifier.

## Failure and rollback

- Invalid or inconsistent local configuration exits with code 2 before API use.
- Effective drift or a sanitized API failure writes evidence and exits with
  code 1.
- A complete exact match writes `status=verified` and exits with code 0.

The command performs no writes, so runtime rollback is unnecessary. Source
rollback is a normal revert of the squash commit.

## Non-goals

This increment does not:

- activate branch protection or change repository settings;
- create or modify GitHub environments, variables or secrets;
- configure Databricks federation or privileges;
- authenticate to Databricks;
- validate, plan or deploy a bundle;
- upload data, execute SQL, run workflows or activate schedules;
- claim issue #44 is closed before the verifier is run with real administrative
  read access and its evidence is reviewed.

## Validation

Repository validation must prove:

- strict and consistent config parsing;
- verified effective-state handling;
- simultaneous reporting of repository, protection and environment drift;
- explicit approval-count comparison;
- detection of static client-secret variables and secrets;
- bounded sanitized API failure evidence;
- output-directory symlink rejection;
- no sensitive expected value in generated evidence;
- documentation of the exact read-only verification sequence.
