# External GitHub and Databricks bootstrap

The repository has fail-closed OIDC plan/apply workflows, but GitHub repository
settings and Databricks account federation policies live outside the Git tree.
This runbook provides dry-run-first automation and read-only verification for
those control planes.

## Sensitive-value boundary

Do not commit bootstrap configuration files, admin tokens, account IDs, service
principal IDs, workspace hosts, or client IDs. Store local files and generated
verification output below `.bootstrap/`, which is ignored by Git.

The scripts accept no token command-line arguments. `GITHUB_ADMIN_TOKEN` is read
only from the process environment. Databricks bootstrap uses an already
authenticated account-admin CLI profile created with:

```bash
databricks auth login \
  --host https://accounts.cloud.databricks.com \
  --account-id <account-id>
```

Remove or expire one-time administrative credentials after verification. Do not
paste raw configuration, CLI output, access tokens, principal identifiers, or
workspace hosts into an issue or pull request.

## 1. Prepare local configuration

Create `.bootstrap/github-governance.json`:

```json
{
  "repository": "alexmarinos87/databricks-lakehouse-telemetry-demo",
  "environments": {
    "dev-plan": {
      "databricks_host": "https://<dev-workspace>",
      "databricks_client_id": "<dev-plan-app-id>",
      "reviewers": [],
      "prevent_self_review": false
    },
    "prod-plan": {
      "databricks_host": "https://<prod-workspace>",
      "databricks_client_id": "<prod-plan-app-id>",
      "reviewers": [],
      "prevent_self_review": false
    },
    "dev": {
      "databricks_host": "https://<dev-workspace>",
      "databricks_client_id": "<dev-apply-app-id>",
      "reviewers": [],
      "prevent_self_review": false
    },
    "prod": {
      "databricks_host": "https://<prod-workspace>",
      "databricks_client_id": "<prod-apply-app-id>",
      "reviewers": [{"type": "User", "id": 123456789}],
      "prevent_self_review": true
    }
  }
}
```

Replace the example production reviewer ID with a user or team that has at
least read access. The production environment requires at least one reviewer
and self-review prevention. `dev` may remain reviewer-free or use the same
protection pattern when the shared development workspace is sensitive.
`dev-plan` and `prod-plan` remain reviewer-free so plan evidence can be gathered
without authorizing an apply.

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
reuses a principal. One numeric ID must never be paired with different
application IDs.

## 2. Review dry runs

```bash
python3 scripts/bootstrap_github_governance.py \
  --config .bootstrap/github-governance.json

python3 scripts/bootstrap_databricks_oidc.py \
  --config .bootstrap/databricks-federation.json
```

Dry-run output contains fingerprints rather than raw hosts, account IDs,
numeric principal IDs, or application IDs. It describes intended changes but
does not query or prove external state.

## 3. Apply GitHub governance

Use a fine-grained token with repository administration, environment, Actions
variable, and environment-secret metadata access:

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
deletions. Raise it to one when an independent maintainer is available.

The script also creates `dev-plan`, `prod-plan`, `dev`, and `prod`, restricts
each to `main`, applies the reviewer policy from the ignored config, and sets
non-password `DATABRICKS_HOST` and `DATABRICKS_CLIENT_ID` environment
variables. A production reviewer with self-review prevention can deliberately
leave production blocked until an independent maintainer is available.

After writes, the script reads the effective settings back and fails unless:

- squash is the only enabled merge method;
- `main` is protected with strict `validate`, administrator enforcement,
  stale-review dismissal, the configured approval count, linear history,
  conversation resolution, and no force push or deletion;
- every required environment uses only an explicit `main` deployment policy;
- required reviewers and self-review prevention exactly match the ignored local
  config;
- both required environment variables exactly match the ignored local config;
- `DATABRICKS_CLIENT_SECRET` is absent from environment variables and secrets.

The read-back does not replace independent settings review. Re-query the branch
or ruleset endpoint and retain a screenshot or settings export for issue #44.

## 4. Apply Databricks federation policies

```bash
python3 scripts/bootstrap_databricks_oidc.py \
  --config .bootstrap/databricks-federation.json \
  --apply
```

Before creating any policy, the script reads each numeric service-principal ID
and requires its application ID to match the ignored local config. It rejects an
inactive principal, a subject attached to another configured principal,
duplicate subjects, and conflicting issuer or audience values.

A newly created policy is then listed again. Apply succeeds only when all four
exact GitHub environment subjects are visible on the intended principals with
the GitHub Actions issuer and configured audience.

The script does not grant workspace-admin or account-admin rights to deployment
principals. Bootstrap the minimum workspace and Unity Catalog permissions
separately and record both successful required actions and denied out-of-scope
actions in issue #44.

## 5. Verify external state without mutation

Run both verification modes after applying settings and again before an
authenticated plan:

```bash
export GITHUB_ADMIN_TOKEN=<short-lived-read-capable-admin-token>
python3 scripts/bootstrap_github_governance.py \
  --config .bootstrap/github-governance.json \
  --verify \
  --required-approvals 0 \
  > .bootstrap/github-governance-verification.json
unset GITHUB_ADMIN_TOKEN

python3 scripts/bootstrap_databricks_oidc.py \
  --config .bootstrap/databricks-federation.json \
  --verify \
  > .bootstrap/databricks-federation-verification.json
```

Verification is fail-closed and performs zero write operations:

- GitHub verification uses only `GET` requests.
- Databricks verification uses only service-principal `get` and federation
  policy `list` commands.
- Output contains status values and fingerprints rather than raw configuration.
- A missing, unreadable, duplicated, mismatched, or misplaced control fails the
  command rather than being treated as partial success.

Keep the JSON files under `.bootstrap/`. Record only the command result, accepted
commit, output file hash, and independent reviewer in issue #44. A source test
of verification logic is not proof that the external settings are active.

## 6. Trigger the plan-only development check

After both verification commands succeed and independent branch protection
evidence is recorded, add this exact comment to issue #44:

```text
/databricks-plan dev
```

Only a repository-owner comment on issue #44 is accepted. The workflow checks
out accepted `main`, requests the `dev-plan` OIDC identity, runs plan mode only,
retains bounded evidence for 14 days, and posts the run URL, artifact name, and
accepted commit back to the issue.

The command does not set `apply_changes=true`, deploy a bundle, upload source
data, run the lakehouse workflow, execute SQL grants, or activate a schedule.

Review every planned create, update, replace, delete, and permission operation
before closing issue #44. A failed command is evidence of an incomplete
bootstrap, not permission to add a static client secret.
