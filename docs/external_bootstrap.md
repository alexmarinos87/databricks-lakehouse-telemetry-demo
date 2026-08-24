# External GitHub and Databricks bootstrap

The repository has fail-closed OIDC plan/apply workflows, but GitHub repository
settings and Databricks account federation policies live outside the Git tree.
This runbook provides dry-run-first automation for those control planes.

## Sensitive-value boundary

Do not commit bootstrap configuration files, admin tokens, account IDs, service
principal IDs, workspace hosts, or client IDs. Store local files below
`.bootstrap/`, which is ignored by Git.

The scripts accept no token command-line arguments. `GITHUB_ADMIN_TOKEN` is read
only from the process environment. Databricks bootstrap uses an already
authenticated account-admin CLI profile created with:

```bash
databricks auth login \
  --host https://accounts.cloud.databricks.com \
  --account-id <account-id>
```

Remove or expire one-time administrative credentials after verification.

## 1. Prepare local configuration

Create `.bootstrap/github-governance.json`:

```json
{
  "repository": "alexmarinos87/databricks-lakehouse-telemetry-demo",
  "environments": {
    "dev-plan": {"databricks_host": "https://<dev-workspace>", "databricks_client_id": "<dev-plan-app-id>"},
    "prod-plan": {"databricks_host": "https://<prod-workspace>", "databricks_client_id": "<prod-plan-app-id>"},
    "dev": {"databricks_host": "https://<dev-workspace>", "databricks_client_id": "<dev-apply-app-id>"},
    "prod": {"databricks_host": "https://<prod-workspace>", "databricks_client_id": "<prod-apply-app-id>"}
  }
}
```

Create `.bootstrap/databricks-federation.json` using the matching application
IDs and Databricks numeric service-principal IDs:

```json
{
  "repository": "alexmarinos87/databricks-lakehouse-telemetry-demo",
  "account_host": "https://accounts.cloud.databricks.com",
  "account_id": "<account-id>",
  "audience": "https://github.com/alexmarinos87",
  "principals": {
    "dev-plan": {"numeric_id": "<numeric-id>", "application_id": "<application-id>"},
    "prod-plan": {"numeric_id": "<numeric-id>", "application_id": "<application-id>"},
    "dev": {"numeric_id": "<numeric-id>", "application_id": "<application-id>"},
    "prod": {"numeric_id": "<numeric-id>", "application_id": "<application-id>"}
  }
}
```

A single principal may technically carry several policies, but distinct
external workload identities improve audit attribution and independent
revocation. Keep each environment mapping explicit even when an approved design
reuses a principal.

## 2. Review dry runs

```bash
python3 scripts/bootstrap_github_governance.py \
  --config .bootstrap/github-governance.json

python3 scripts/bootstrap_databricks_oidc.py \
  --config .bootstrap/databricks-federation.json
```

Dry-run output contains fingerprints rather than host or client-ID values.

## 3. Apply GitHub governance

Use a fine-grained token with repository administration, environment, and
Actions-variable write access:

```bash
export GITHUB_ADMIN_TOKEN=<one-time-admin-token>
python3 scripts/bootstrap_github_governance.py \
  --config .bootstrap/github-governance.json \
  --apply \
  --required-approvals 0
unset GITHUB_ADMIN_TOKEN
```

The zero-approval setting preserves a sole-maintainer workflow while still
requiring pull requests, current `validate` status, resolved conversations,
linear history, administrator enforcement, and blocking force pushes and branch
deletion. Raise it to one when an independent maintainer is available.

The script also creates `dev-plan`, `prod-plan`, `dev`, and `prod`, restricts
each to `main`, and sets non-password `DATABRICKS_HOST` and
`DATABRICKS_CLIENT_ID` environment variables.

Re-query `main` and confirm `protected: true` before continuing.

## 4. Apply Databricks federation policies

```bash
python3 scripts/bootstrap_databricks_oidc.py \
  --config .bootstrap/databricks-federation.json \
  --apply
```

The script verifies or creates exact GitHub environment subjects under the
GitHub Actions issuer. It fails when an existing policy for a subject has a
different audience or issuer. It does not grant workspace-admin or account-admin
rights to the deployment principals.

Bootstrap the minimum workspace and Unity Catalog permissions separately and
record both successful required actions and denied out-of-scope actions in issue
#44.

## 5. Trigger the plan-only development check

After the scripts verify successfully, add this exact comment to issue #44:

```text
/databricks-plan dev
```

Only a repository-owner comment on issue #44 is accepted. The workflow checks out accepted `main`
and runs a read-only external-readiness preflight before downloading the Databricks CLI
or invoking any Databricks command. The preflight
verifies all of the following in one pass:

- the checked-out commit still matches the current `main` branch head;
- the workflow event SHA matches the checked-out commit and repository identity;
- the branch-state token is sent only to `https://api.github.com`;
- GitHub reports `main` as protected;
- the required `validate` status context is active;
- the job runs with GitHub OIDC context and `DATABRICKS_AUTH_TYPE=github-oidc`;
- `DATABRICKS_HOST` and `DATABRICKS_CLIENT_ID` are present and structurally valid;
- no static `DATABRICKS_CLIENT_SECRET` is mapped.

The preflight writes `external-readiness.json` and
`external-readiness-summary.md`. These files contain booleans, blocker
categories, commit identities, and fingerprints rather than raw workspace hosts,
client IDs, request tokens, or provider diagnostics. Every independent blocker
is reported in the same run instead of stopping at the first missing value.

When readiness is blocked, the workflow uploads the sanitized evidence, posts
the blocker categories to issue #44, skips Databricks CLI installation, and
fails the gate. When readiness is ready, the workflow requests the `dev-plan`
OIDC identity, runs plan mode only, retains bounded evidence for 14 days, and
posts the run URL, artifact name, and accepted commit back to the issue.

The command does not set `apply_changes=true`, deploy a bundle, upload source
data, run the lakehouse workflow, execute SQL grants, or activate a schedule.

Review every planned create, update, replace, delete, and permission operation
before closing issue #44. A failed command is evidence of an incomplete
bootstrap, not permission to add a static client secret.
