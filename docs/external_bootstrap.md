# External GitHub and Databricks bootstrap

The repository has fail-closed OIDC plan/apply workflows, but GitHub repository
settings and Databricks account federation policies live outside the Git tree.
This runbook provides dry-run-first automation and independent read-only
verification for those control planes.

## Sensitive-value boundary

Do not commit bootstrap configuration files, admin tokens, account IDs, service
principal IDs, workspace hosts, or client IDs. Store local files below
`.bootstrap/`, which is ignored by Git.

The scripts accept no token command-line arguments. `GITHUB_ADMIN_TOKEN` is read
only from the process environment. Databricks bootstrap and verification use an
already authenticated account-admin CLI profile created with:

```bash
databricks auth login \
  --host https://accounts.cloud.databricks.com \
  --account-id <account-id>
```

Do not export `DATABRICKS_TOKEN` or `DATABRICKS_CLIENT_SECRET` for the
verification step. Remove or expire one-time administrative credentials after
verification.

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

Create `.bootstrap/runtime-identity.json` using the same deployment application
IDs and distinct runtime identities:

```json
{
  "repository": "alexmarinos87/databricks-lakehouse-telemetry-demo",
  "account_host": "https://accounts.cloud.databricks.com",
  "account_id": "<account-id>",
  "audience": "https://github.com/alexmarinos87",
  "environments": {
    "dev-plan": {
      "deployment_client_id": "<dev-plan-app-id>",
      "runtime_client_id": "<dev-runtime-app-id>",
      "runtime_numeric_id": "<dev-runtime-numeric-id>"
    },
    "prod-plan": {
      "deployment_client_id": "<prod-plan-app-id>",
      "runtime_client_id": "<prod-runtime-app-id>",
      "runtime_numeric_id": "<prod-runtime-numeric-id>"
    },
    "dev": {
      "deployment_client_id": "<dev-apply-app-id>",
      "runtime_client_id": "<dev-runtime-app-id>",
      "runtime_numeric_id": "<dev-runtime-numeric-id>"
    },
    "prod": {
      "deployment_client_id": "<prod-apply-app-id>",
      "runtime_client_id": "<prod-runtime-app-id>",
      "runtime_numeric_id": "<prod-runtime-numeric-id>"
    }
  }
}
```

Create `.bootstrap/databricks-federation.json` using the matching deployment
application IDs and Databricks numeric service-principal IDs:

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

python3 scripts/bootstrap_runtime_identity.py \
  --config .bootstrap/runtime-identity.json

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

python3 scripts/bootstrap_runtime_identity.py \
  --config .bootstrap/runtime-identity.json \
  --apply-github
```

The zero-approval setting preserves a sole-maintainer workflow while still
requiring pull requests, current `validate` and
`Round-trip synthetic review evidence` statuses, resolved conversations, linear
history, administrator enforcement, and blocking force pushes and branch
deletion. Raise it to one when an independent maintainer is available.

The scripts create `dev-plan`, `prod-plan`, `dev`, and `prod`, restrict each to
`main`, and set non-password `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, and
`DATABRICKS_RUNTIME_CLIENT_ID` environment variables.

### Verify effective GitHub state

Do not treat successful write requests as closure evidence. While the one-time
administrative token is still available, run the independent read-only verifier:

```bash
python3 scripts/verify_github_governance.py \
  --github-config .bootstrap/github-governance.json \
  --runtime-config .bootstrap/runtime-identity.json \
  --output-dir .bootstrap/evidence/github-governance \
  --required-approvals 0
```

The verifier performs GET requests only. It compares effective repository merge
settings, the branch endpoint, the exact required status-context set in full
`main` protection, all four environment branch policies, deployment and runtime
variables, and the absence of `DATABRICKS_CLIENT_SECRET` as either a variable or
secret.

It writes:

```text
.bootstrap/evidence/github-governance/github-governance-verification.json
.bootstrap/evidence/github-governance/github-governance-verification.md
```

The evidence contains stable drift categories and SHA-256 fingerprints rather
than workspace hosts, deployment client IDs, runtime client IDs, tokens, or
secret values. A non-zero result is evidence of incomplete or drifting settings;
do not weaken the expected policy merely to make verification pass.

Only after the verifier records `status: verified` should the one-time token be
removed:

```bash
unset GITHUB_ADMIN_TOKEN
```

Re-query `main` and confirm `protected: true` before continuing.

## 4. Apply Databricks federation policies

```bash
python3 scripts/bootstrap_databricks_oidc.py \
  --config .bootstrap/databricks-federation.json \
  --apply

python3 scripts/bootstrap_runtime_identity.py \
  --config .bootstrap/runtime-identity.json \
  --apply-databricks
```

The scripts verify or create exact GitHub environment subjects under the GitHub
Actions issuer. They fail when an existing policy for a subject has a different
audience or issuer. They do not grant workspace-admin or account-admin rights
to the deployment or runtime principals.

## 5. Verify effective Databricks federation state

Do not treat successful policy-create responses as closure evidence. While the
account-admin CLI session is still available, run the independent read-only
verifier:

```bash
python3 scripts/verify_databricks_federation.py \
  --deployment-config .bootstrap/databricks-federation.json \
  --runtime-config .bootstrap/runtime-identity.json \
  --output-dir .bootstrap/evidence/databricks-federation \
  --timeout-seconds 60
```

The verifier performs only these account inventory operations:

```text
databricks account service-principals get
databricks account service-principal-federation-policy list
databricks account service-principal-secrets list
```

It independently proves that numeric IDs resolve to the configured application
IDs, deployment and runtime identities stay globally separate, every principal
is active and not an account administrator, every configured GitHub environment
has exactly one matching issuer/audience/subject policy, no unexpected policy
broadens trust, and no OAuth client secret remains attached to a referenced
principal.

It writes:

```text
.bootstrap/evidence/databricks-federation/databricks-federation-verification.json
.bootstrap/evidence/databricks-federation/databricks-federation-verification.md
```

The evidence contains fingerprints, counts, booleans, roles, environments and
stable drift categories. It excludes account hosts, account IDs, application
IDs, numeric service-principal IDs, policy IDs, secret IDs, credential values
and raw provider diagnostics.

Require `status: verified` before proceeding. A non-zero result is evidence of
incomplete or drifting federation state. Do not add a static OAuth secret or
weaken the expected issuer, audience, subject or identity-separation boundary to
make verification pass.

Bootstrap the minimum workspace and Unity Catalog permissions separately and
record both successful required actions and denied out-of-scope actions in issue
#44. This account-level verifier does not prove workspace assignment, Service
Principal User relationships, or Unity Catalog privileges.

## 6. Trigger the plan-only development check

After both independent verifiers record `status: verified`, add this exact
comment to issue #44:

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
