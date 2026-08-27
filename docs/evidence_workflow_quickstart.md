# Evidence workflow quickstart

This page is the shortest safe route through the repository's external-control and development-runtime evidence tooling. It is an operator index, not an authorisation to change GitHub or Databricks state.

## 1. Confirm external readiness

Run the read-only preflight before installing or invoking the Databricks CLI:

```bash
python3 scripts/check_external_readiness.py \
  --repository alexmarinos87/databricks-lakehouse-telemetry-demo \
  --expected-ref refs/heads/main \
  --expected-sha "$(git rev-parse HEAD)" \
  --output-dir .bootstrap/evidence/external-readiness
```

A blocked result must remain blocked. Do not substitute a static Databricks client secret for missing OIDC configuration.

## 2. Verify effective external controls

After the separately authorised bootstrap operations, collect the three read-only reports:

```bash
python3 scripts/verify_github_governance.py \
  --github-config .bootstrap/github-governance.json \
  --runtime-config .bootstrap/runtime-identity.json \
  --output-dir .bootstrap/evidence/github-governance \
  --required-approvals 0

python3 scripts/verify_databricks_federation.py \
  --deployment-config .bootstrap/databricks-federation.json \
  --runtime-config .bootstrap/runtime-identity.json \
  --output-dir .bootstrap/evidence/databricks-federation

python3 scripts/verify_identity_privilege_evidence.py \
  --evidence .bootstrap/evidence/dev/identity-privilege-evidence.json \
  --output-dir .bootstrap/evidence/dev/identity-privilege-verification
```

Each report must have `status: verified`, no findings, the accepted repository and source commit, and no raw credentials or provider diagnostics.

## 3. Index one coherent control state

Bind those reports to their exact bytes, verifier versions, source commit and collection window:

```bash
python3 scripts/build_external_control_evidence_index.py \
  --metadata .bootstrap/evidence/dev/external-control-index-metadata.json \
  --evidence-root .bootstrap/evidence/external-controls \
  --output-dir .bootstrap/evidence/dev/external-control-index
```

A verified index records `external_mutation_authorized: false`. It is review evidence, not deployment authority.

## 4. Capture and review a plan-only run

Use the owner-only GitHub command only after the external-control index is accepted:

```text
/databricks-plan dev
```

The command must remain plan-only. Review the retained structured plan with the repository policy before any apply is considered.

## 5. Package controlled development-runtime evidence

After a separate human approval and one development-only execution, bind the protected approval, plan, publication, quality, query, grant and rollback records:

```bash
python3 scripts/build_development_runtime_evidence.py \
  --metadata .bootstrap/evidence/dev/runtime-metadata.json \
  --artifact-root .bootstrap/protected/dev/runtime \
  --output-dir .bootstrap/evidence/dev/runtime-package
```

The package must cover all nine evidence families and all sixteen mandatory assertions. A blocked verifier result remains blocked.

## 6. Verify operational evidence separately

Alert delivery and retention remain separate operational authorities:

```bash
python3 scripts/build_alert_delivery_evidence.py \
  --metadata .bootstrap/evidence/dev/alert-delivery-metadata.json \
  --artifact-root .bootstrap/protected/dev/alerts \
  --output-dir .bootstrap/evidence/dev/alert-delivery-package

python3 scripts/plan_history_retention.py \
  --inventory .bootstrap/evidence/dev/history-retention-inventory.json \
  --output-dir .bootstrap/evidence/dev/history-retention-plan
```

The alert command only packages evidence from an already delivered test alert. The retention command is dry-run-only and must not delete, vacuum or mutate history.

## Stop conditions

Stop the workflow and create a bounded repair PR when any of the following occurs:

- `main` is not protected or `validate` is not required;
- GitHub, federation or identity evidence is blocked, failed, stale or from another commit;
- a plan contains an unexplained delete, replacement or cross-target resource;
- deployment and runtime identities overlap or an expected denial succeeds;
- any runtime evidence family refers to another execution;
- production contact is reported;
- protected evidence, credentials, workspace URLs or provider diagnostics appear in public output.

No command on this page authorises production activity, unattended deployment, permission mutation, alert activation, retention execution or scheduler activation.
