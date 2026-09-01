# Accepted-main delivery-check evidence

The repository distinguishes two questions:

1. **Does branch protection require the reviewed checks?**
2. **Did those checks actually pass on the exact commit selected for a plan?**

`scripts/verify_github_governance.py` answers the first question. The read-only
`scripts/verify_main_check_runs.py` answers the second.

## Required checks

The accepted commit must have exactly one latest check run for each of:

```text
validate
Round-trip synthetic review evidence
```

Each required run must:

- be bound to the supplied 40-character commit SHA;
- be reported by the `github-actions` app;
- have `status: completed`;
- have `conclusion: success`.

Other check names may exist. They do not satisfy or invalidate these two required
checks. More than one latest run with the same required name is treated as
ambiguous and blocks verification.

## Read-only invocation

The script accepts no token argument. It reads the short-lived token from
`GITHUB_TOKEN` and permits only the public GitHub API origin.

```bash
export GITHUB_TOKEN=<short-lived-token>
python3 scripts/verify_main_check_runs.py \
  --repository alexmarinos87/databricks-lakehouse-telemetry-demo \
  --commit <accepted-main-sha> \
  --output-dir .bootstrap/evidence/accepted-main-checks \
  --timeout-seconds 30
unset GITHUB_TOKEN
```

The request is bounded to one latest-check page:

```text
GET /repos/<owner>/<repository>/commits/<sha>/check-runs
    ?filter=latest
    &per_page=100
```

A response that is oversized, malformed or indicates omitted pages fails closed.

## Evidence outputs

The output directory receives:

```text
main-check-runs-verification.json
main-check-runs-verification.md
```

Evidence records only:

- repository and accepted commit;
- required check names;
- bounded status and conclusion values;
- expected and observed app slug;
- per-check verification booleans;
- stable blocker or failure categories.

It excludes GitHub tokens, check output, annotations, URLs, provider diagnostics,
Databricks hosts and Databricks client IDs.

## Blocked and failed states

A **blocked** result means GitHub returned a usable inventory but a required check
was missing, ambiguous, incomplete, unsuccessful, sourced from an unexpected app,
or bound to a different commit.

A **failed** result means the verifier could not establish a complete trustworthy
inventory, for example because the token was absent, the request failed, the API
origin was not approved, or the response was malformed, oversized or truncated.

Neither result authorizes bypassing the gate, renaming a check merely to obtain a
green status, or adding a static Databricks secret.

## Workflow boundary

This verifier does not install the Databricks CLI, request an OIDC token, validate
a bundle, create a plan, deploy, upload data, execute SQL or mutate permissions.
A dependent workflow increment may use `checks: read` and require verified
accepted-main evidence before any Databricks command. That integration remains
subject to human acceptance.
