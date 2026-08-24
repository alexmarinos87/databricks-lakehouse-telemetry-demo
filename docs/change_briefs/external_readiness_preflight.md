# Change Brief: Preflight External Readiness Before Databricks Plan Evidence

## Outcome

Make the owner-triggered development plan command evaluate every externally
required GitHub and Databricks configuration gate before installing the
Databricks CLI or invoking provider commands.

This is a bounded enabling increment for G13 and G14. It does not claim that
branch protection, GitHub environments, workload-identity federation, or
Databricks permissions are configured.

## Problem

The existing `/databricks-plan dev` workflow validates configuration inside the
plan-capture helper. A blocked run therefore:

- downloads and installs the Databricks CLI before discovering an absent host;
- stops at the first missing configuration value;
- does not include active branch protection or the required validation context
  in the same machine-readable evidence record;
- requires operators to combine workflow logs, the branch endpoint, and issue
  comments to understand the complete external gate.

The latest accepted run demonstrated this boundary: both Databricks environment
values were absent and `main` was unprotected, but the persisted failure category
identified only the first missing host value.

## Decision

Add a standard-library `check_external_readiness.py` preflight to the existing
owner-only issue-comment workflow.

The preflight reads the public/current GitHub branch state with the short-lived
workflow token and evaluates:

1. accepted checkout SHA equals the current `main` head;
2. workflow event SHA equals the accepted checkout SHA;
3. workflow repository identity equals the expected repository;
4. `main` is protected;
5. `validate` is a required status context;
6. GitHub Actions and OIDC request context are complete;
7. unified authentication is configured as `github-oidc`;
8. Databricks host and client ID are present and structurally valid;
9. no static client secret is present.

All failed checks are retained as deterministic blocker identifiers. Raw hosts,
client IDs, GitHub tokens, OIDC request tokens, and provider error messages are
not persisted.

## Workflow sequence

```text
owner comment on issue #44
  -> checkout accepted main
  -> verify checkout branch
  -> capture external readiness
  -> upload readiness evidence
  -> if blocked: skip CLI and Databricks commands, fail gate, comment blockers
  -> if ready: install CLI, verify identity, validate bundle, capture plan
```

The existing plan artifact remains the evidence container. A blocked run contains
only readiness evidence; a ready run also contains identity, validation, and plan
evidence.

## Evidence

Repository validation must prove:

- all readiness blockers are evaluated rather than first-failure only;
- protected branch, current head, workflow-event SHA, repository identity, and required check are distinct controls;
- missing and malformed Databricks values are distinguished;
- API failures are sanitized and do not suppress independent environment gaps;
- the workflow token can be sent only to the fixed GitHub.com API endpoint;
- raw hosts, client IDs, client secrets, GitHub tokens, and OIDC request tokens
  do not appear in evidence or summaries;
- the output directory rejects symbolic links;
- the workflow cannot reach CLI installation or plan capture after a blocked
  readiness result;
- the exact owner-only trigger and plan-only side-effect boundary remain intact.

## Non-goals

This increment does not:

- activate branch protection or repository rulesets;
- create GitHub environments or set their values;
- create Databricks service principals or federation policies;
- authenticate to Databricks during repository CI;
- execute a bundle plan during pull-request validation;
- deploy resources, upload data, run workflows, execute SQL, mutate grants, or
  enable schedules;
- authorize a development or production apply.

## Failure and recovery

A branch-state API failure is recorded as
`github_branch_state_unavailable` with a bounded category and no provider
message. Other environment blockers remain visible in the same evidence.

Rollback is source-only: remove the preflight and workflow gate to return to the
previous first-failure plan-capture path. No external state or Databricks object
requires rollback.
