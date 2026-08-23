# Engineering Risk Register

This register separates repository-source mitigation from Databricks runtime evidence and external settings evidence. A source control, unit test, local Spark test, or agent review cannot by itself close a workspace or settings risk.

- Last reviewed: **2026-08-23**
- Accepted source baseline: **`c8997354fd502eae7dc4c7d03bba8e005853950f`**
- Machine-readable source: [`governance/engineering_risks.json`](../governance/engineering_risks.json)
- Validation command: `python3 scripts/validate_engineering_risks.py`

## Status model

- `source`: `mitigated`, `partial`, `open`, or `not_applicable`.
- `runtime`: `evidenced`, `pending`, `blocked`, or `not_applicable`.
- `external`: `evidenced`, `pending`, `blocked`, or `not_applicable`.
- A risk is closed only when every applicable layer is complete and no pending evidence remains.

## Summary

| ID | Priority | Source | Runtime | External | Risk |
| --- | --- | --- | --- | --- | --- |
| R-001 | critical | mitigated | blocked | blocked | Databricks deployment authentication is not proven in the workspace |
| R-002 | high | mitigated | blocked | blocked | Development resource names may differ from post-deploy lookups |
| R-003 | high | mitigated | blocked | blocked | Lakeflow expectations mode and edition require workspace validation |
| R-004 | high | mitigated | pending | not_applicable | Local Spark evidence cannot prove Databricks runtime behaviour |
| R-005 | high | partial | pending | not_applicable | Effective-dated assignment policy is not yet the warehouse construction path |
| R-006 | high | partial | pending | pending | Attributed-downtime semantics are not yet integrated into accepted main outputs |
| R-007 | high | mitigated | not_applicable | blocked | Main-branch protection is an unverified external setting |
| R-008 | medium | mitigated | pending | not_applicable | Deployment operations and supply-chain inputs require runtime observation |
| R-009 | high | partial | pending | not_applicable | Silver, Gold, and warehouse can expose mixed multi-table publications |
| R-010 | high | mitigated | pending | blocked | Auto Loader replay semantics require live verification |
| R-011 | high | partial | pending | blocked | Owner-run editable saved queries may cross a privilege boundary |
| R-012 | high | mitigated | blocked | blocked | Development and production isolation requires rendered-plan proof |
| R-013 | high | mitigated | pending | blocked | Quality enforcement requires Databricks persistence and Lakeflow proof |
| R-014 | high | mitigated | pending | pending | Forecast readiness and versioned publication require business and runtime evidence |
| R-015 | high | mitigated | blocked | blocked | Deployment and runtime identity separation is not proven effective |
| R-016 | medium | mitigated | blocked | blocked | Operational alerts and retention are policy only |
| R-017 | medium | mitigated | pending | not_applicable | Runtime compatibility can drift through partial upgrades |

## Detailed risks

### R-001 — Databricks deployment authentication is not proven in the workspace

- Priority: **critical**
- Layer status: **source=mitigated; runtime=blocked; external=blocked**
- Owner: **Platform engineering**
- Summary: The repository now uses GitHub OIDC, rejects static secrets, and performs an identity preflight, but environment values and federation policies have not produced a successful protected-main plan run.
- Source evidence: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`scripts/capture_databricks_plan.py`](../scripts/capture_databricks_plan.py), [`scripts/bootstrap_databricks_oidc.py`](../scripts/bootstrap_databricks_oidc.py)
- Pending evidence: Environment-scoped Databricks host and client identifiers are configured.; Federation policies accept the four declared GitHub environment subjects.; A protected-main plan-only run proves identity, bundle validation, and bundle plan.
- Dependencies: issue #44
- Next action: Complete issue #44, then run development with apply_changes=false and retain the exact-commit plan artifact.

### R-002 — Development resource names may differ from post-deploy lookups

- Priority: **high**
- Layer status: **source=mitigated; runtime=blocked; external=blocked**
- Owner: **Platform engineering**
- Summary: Target presets and helper lookups now use deterministic target-qualified names, but the rendered workspace names have not been authenticated and inspected.
- Source evidence: [`databricks.yml`](../databricks.yml), [`scripts/apply_uc_grants.py`](../scripts/apply_uc_grants.py), [`scripts/upsert_reporting_queries.py`](../scripts/upsert_reporting_queries.py)
- Pending evidence: Authenticated dev and prod plans show the expected job, pipeline, warehouse, schema, and volume names.; Post-deploy helpers resolve exactly one intended resource in development.
- Dependencies: issue #44
- Next action: Inspect the authenticated development plan and compare every helper lookup with the rendered resource name.

### R-003 — Lakeflow expectations mode and edition require workspace validation

- Priority: **high**
- Layer status: **source=mitigated; runtime=blocked; external=blocked**
- Owner: **Data engineering**
- Summary: The pipeline source uses the expectations-capable edition and target-controlled development mode, but no development refresh has proved the effective configuration or expectation metrics.
- Source evidence: [`resources/lakehouse_quality_expectations.yml`](../resources/lakehouse_quality_expectations.yml), [`databricks.yml`](../databricks.yml), [`notebooks/06_lakeflow_quality_expectations.py`](../notebooks/06_lakeflow_quality_expectations.py)
- Pending evidence: A rendered plan confirms target-specific pipeline mode and edition.; A development pipeline update completes and emits expectation metrics.
- Dependencies: issue #44
- Next action: Run the expectations pipeline in development after authenticated plan approval and retain update and event-log evidence.

### R-004 — Local Spark evidence cannot prove Databricks runtime behaviour

- Priority: **high**
- Layer status: **source=mitigated; runtime=pending; external=not_applicable**
- Owner: **Repository maintainer**
- Summary: The repository now separates source contracts from executable local Spark suites, but local Python, Java, and PySpark evidence remains different from deployed Databricks Runtime, Delta, Auto Loader, and Lakeflow behaviour.
- Source evidence: [`.github/workflows/spark-runtime.yml`](../.github/workflows/spark-runtime.yml), [`Dockerfile.spark-ci`](../Dockerfile.spark-ci), [`tests_runtime/test_spark_warehouse_runtime.py`](../tests_runtime/test_spark_warehouse_runtime.py), [`tests_runtime/test_spark_forecast_runtime.py`](../tests_runtime/test_spark_forecast_runtime.py)
- Pending evidence: The accepted bundle executes in a development Databricks Runtime matching the declared compatibility baseline.; Delta writes, Auto Loader discovery, Lakeflow expectations, and Unity Catalog views are verified separately from local Spark.
- Dependencies: None.
- Next action: Retain the local/runtime evidence boundary and add authenticated development execution before any production-readiness claim.

### R-005 — Effective-dated assignment policy is not yet the warehouse construction path

- Priority: **high**
- Layer status: **source=partial; runtime=pending; external=not_applicable**
- Owner: **Data engineering**
- Summary: A fail-closed effective-dated assignment contract and unknown-member policy exist, while the current warehouse builder still contains its earlier embedded assignment construction and has not migrated live facts to the new contract.
- Source evidence: [`governance/warehouse_assignment_policy.json`](../governance/warehouse_assignment_policy.json), [`src/lakehouse_demo/spark_assignment_history.py`](../src/lakehouse_demo/spark_assignment_history.py), [`tests_runtime/test_spark_assignment_history_runtime.py`](../tests_runtime/test_spark_assignment_history_runtime.py), [`src/lakehouse_demo/spark_warehouse.py`](../src/lakehouse_demo/spark_warehouse.py)
- Pending evidence: The warehouse notebook and builder use the effective-dated assignment contract directly.; Late-arriving assignment and reassignment recovery rebuild affected history without silent fact loss.; A development migration proves fact reconciliation and rollback.
- Dependencies: None.
- Next action: Wire assignment history into warehouse construction as a bounded source increment, then plan the live migration separately.

### R-006 — Attributed-downtime semantics are not yet integrated into accepted main outputs

- Priority: **high**
- Layer status: **source=partial; runtime=pending; external=pending**
- Owner: **Data engineering**
- Summary: The semantic policy permits attributed downtime above observation coverage, but accepted main does not yet materialize the preferred load, exceedance flag, and semantic version through Gold, warehouse, quality, and reporting.
- Source evidence: [`governance/downtime_semantics.json`](../governance/downtime_semantics.json), [`src/lakehouse_demo/spark_downtime_semantics.py`](../src/lakehouse_demo/spark_downtime_semantics.py), [`docs/downtime_semantics.md`](../docs/downtime_semantics.md)
- Pending evidence: The additive semantic fields are accepted and merged into the Gold and warehouse publication path.; Development Delta schema evolution, quality checks, Lakeflow expressions, and reporting labels are verified.; Any real domain owner approval is linked separately from repository acceptance.
- Dependencies: PR #75
- Next action: Review the exact governed-output integration candidate, then validate its additive schema in development before apply.

### R-007 — Main-branch protection is an unverified external setting

- Priority: **high**
- Layer status: **source=mitigated; runtime=not_applicable; external=blocked**
- Owner: **Platform engineering**
- Summary: CI, PR templates, human acceptance rules, and a dry-run governance bootstrap exist in source, but the current branch endpoint has not proved that main requires those controls.
- Source evidence: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`scripts/bootstrap_github_governance.py`](../scripts/bootstrap_github_governance.py), [`AGENTS.md`](../AGENTS.md)
- Pending evidence: GitHub reports main as protected by a PR-only rule or ruleset.; The current validation check and conversation resolution are required.; Force push and branch deletion are disabled and squash-only linear history is active.
- Dependencies: issue #44
- Next action: Restore repository settings through an authorised settings path and retain the branch or ruleset response.

### R-008 — Deployment operations and supply-chain inputs require runtime observation

- Priority: **medium**
- Layer status: **source=mitigated; runtime=pending; external=not_applicable**
- Owner: **Platform engineering**
- Summary: Repository helpers now bound subprocesses and SQL polling, workflows use fixed runners and immutable action commits, and dependencies are monitored, but real provider timeout and cancellation behaviour has not been observed.
- Source evidence: [`scripts/apply_uc_grants.py`](../scripts/apply_uc_grants.py), [`scripts/upsert_reporting_queries.py`](../scripts/upsert_reporting_queries.py), [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`.github/dependabot.yml`](../.github/dependabot.yml)
- Pending evidence: A development plan and grant run demonstrate effective timeout and cancellation behaviour.; Provider failures remain sanitized and do not leave unattended operations running.
- Dependencies: None.
- Next action: Exercise bounded operations in an approved development run and retain provider-side terminal states.

### R-009 — Silver, Gold, and warehouse can expose mixed multi-table publications

- Priority: **high**
- Layer status: **source=partial; runtime=pending; external=not_applicable**
- Owner: **Data engineering**
- Summary: Forecast publication now uses run histories and a committed-manifest boundary, while Silver, Gold, and warehouse still overwrite related Delta tables sequentially and can expose mixed generations after interruption.
- Source evidence: [`src/lakehouse_demo/spark_forecast_publication.py`](../src/lakehouse_demo/spark_forecast_publication.py), [`notebooks/05_forecast_validation.py`](../notebooks/05_forecast_validation.py), [`notebooks/02_silver_transform.py`](../notebooks/02_silver_transform.py), [`notebooks/03_gold_models.py`](../notebooks/03_gold_models.py), [`notebooks/07_warehouse_model.py`](../notebooks/07_warehouse_model.py)
- Pending evidence: Silver, Gold, and warehouse each gain run-level version or staging identities and a visibility boundary.; Retries and interruption after each write are covered by executable recovery scenarios.; Storage, retention, orphan cleanup, and rollback costs are documented.
- Dependencies: None.
- Next action: Implement versioned publication one output family at a time without claiming cross-table ACID.

### R-010 — Auto Loader replay semantics require live verification

- Priority: **high**
- Layer status: **source=mitigated; runtime=pending; external=blocked**
- Owner: **Data engineering**
- Summary: Content-addressed incremental objects and explicit replay IDs now avoid overwriting checkpointed paths, but live Files API, volume, checkpoint, and Auto Loader discovery behaviour has not been exercised.
- Source evidence: [`src/lakehouse_demo/ingestion_identity.py`](../src/lakehouse_demo/ingestion_identity.py), [`scripts/upload_ingestion_plan.py`](../scripts/upload_ingestion_plan.py), [`notebooks/01_bronze_ingest.py`](../notebooks/01_bronze_ingest.py), [`tests_runtime/test_spark_ingestion_identity_runtime.py`](../tests_runtime/test_spark_ingestion_identity_runtime.py)
- Pending evidence: An identical development upload is a verified remote no-op.; A new increment and an explicit backfill are each discovered once with the existing checkpoint.; Bronze and Silver reconcile replay and conflict outcomes.
- Dependencies: issue #44
- Next action: Run the immutable uploader and Auto Loader path in development after authenticated plan approval.

### R-011 — Owner-run editable saved queries may cross a privilege boundary

- Priority: **high**
- Layer status: **source=partial; runtime=pending; external=blocked**
- Owner: **Platform engineering**
- Summary: Saved-query publication is bounded and analysts receive run access, but engineers retain edit access to owner-executed assets and the effective owner-versus-editor trust model is not yet governed or tested.
- Source evidence: [`scripts/upsert_reporting_queries.py`](../scripts/upsert_reporting_queries.py), [`scripts/apply_uc_grants.py`](../scripts/apply_uc_grants.py), [`resources/sql_reporting.yml`](../resources/sql_reporting.yml)
- Pending evidence: A declared saved-query owner is separate from untrusted editors where required.; The edit, run, and manage matrix is reviewed against owner-run execution semantics.; Effective permissions and denied edit/elevation paths are tested in development.
- Dependencies: issue #44
- Next action: Define saved-query ownership and edit policy before enabling broad analyst or engineer access.

### R-012 — Development and production isolation requires rendered-plan proof

- Priority: **high**
- Layer status: **source=mitigated; runtime=blocked; external=blocked**
- Owner: **Platform engineering**
- Summary: Target defaults now separate schema, volume, source, checkpoint, schema metadata, and pipeline mode, but no authenticated plan has proved that pre-existing or effective workspace state is isolated.
- Source evidence: [`databricks.yml`](../databricks.yml), [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`tests/test_target_isolation_contract.py`](../tests/test_target_isolation_contract.py)
- Pending evidence: Authenticated dev and prod plans contain disjoint writable paths and resources.; Any pre-existing shared state is migrated, archived, or explicitly abandoned.; Development cannot read, write, or checkpoint production data.
- Dependencies: issue #44
- Next action: Review both plan artifacts before the first apply and record the disposition of legacy shared state.

### R-013 — Quality enforcement requires Databricks persistence and Lakeflow proof

- Priority: **high**
- Layer status: **source=mitigated; runtime=pending; external=blocked**
- Owner: **Data engineering**
- Summary: Shared quality logic now covers medallion and warehouse outputs, persists detailed and run-level evidence before failure, and keeps Lakeflow monitoring semantics explicit, but deployed persistence and event-log behaviour are unproven.
- Source evidence: [`src/lakehouse_demo/spark_quality.py`](../src/lakehouse_demo/spark_quality.py), [`notebooks/04_quality_checks.py`](../notebooks/04_quality_checks.py), [`notebooks/06_lakeflow_quality_expectations.py`](../notebooks/06_lakeflow_quality_expectations.py), [`tests_runtime/test_spark_quality_runtime.py`](../tests_runtime/test_spark_quality_runtime.py)
- Pending evidence: Development writes durable quality evidence before a deliberately failed task terminates.; Lakeflow event-log metrics match the declared expectations.; Reporting and operational-health queries select the latest run correctly.
- Dependencies: issue #44
- Next action: Execute a controlled development quality failure after authenticated deployment and retain both Delta and event-log evidence.

### R-014 — Forecast readiness and versioned publication require business and runtime evidence

- Priority: **high**
- Layer status: **source=mitigated; runtime=pending; external=pending**
- Owner: **Data engineering**
- Summary: Calendar-day windows, explicit MAE and interval-coverage thresholds, four readiness states, retained histories, and a committed-manifest boundary are implemented, but thresholds and live Delta/view behaviour are not yet approved or executed.
- Source evidence: [`src/lakehouse_demo/spark_forecast.py`](../src/lakehouse_demo/spark_forecast.py), [`src/lakehouse_demo/spark_forecast_publication.py`](../src/lakehouse_demo/spark_forecast_publication.py), [`notebooks/05_forecast_validation.py`](../notebooks/05_forecast_validation.py), [`tests_runtime/test_spark_forecast_publication_runtime.py`](../tests_runtime/test_spark_forecast_publication_runtime.py)
- Pending evidence: A human approves readiness thresholds for the intended synthetic demonstration claim.; Development proves Delta MERGE and DELETE behaviour, current views, schema evolution, and grants.; A failed or interrupted publication visibly falls back to the preceding committed run.
- Dependencies: issue #44
- Next action: Approve explicit thresholds, then execute and recover a controlled development publication before client-facing use.

### R-015 — Deployment and runtime identity separation is not proven effective

- Priority: **high**
- Layer status: **source=mitigated; runtime=blocked; external=blocked**
- Owner: **Platform engineering**
- Summary: The bundle now binds jobs and pipelines to a runtime service principal separate from the GitHub OIDC deployment identity, with a machine-readable privilege matrix and dry-run bootstrap, but effective grants and denied actions are unknown.
- Source evidence: [`governance/runtime_identity_policy.json`](../governance/runtime_identity_policy.json), [`scripts/bootstrap_runtime_identity.py`](../scripts/bootstrap_runtime_identity.py), [`resources/lakehouse_workflow.yml`](../resources/lakehouse_workflow.yml), [`resources/lakehouse_quality_expectations.yml`](../resources/lakehouse_quality_expectations.yml)
- Pending evidence: Deployment identity can manage the bundle but cannot perform runtime data processing outside its duties.; Runtime identity can execute required tasks but cannot deploy or administer workspace resources.; Required and denied actions are captured for both identities in development.
- Dependencies: issue #44
- Next action: Bootstrap identities after OIDC setup, inspect plans, then execute least-privilege positive and negative tests.

### R-016 — Operational alerts and retention are policy only

- Priority: **medium**
- Layer status: **source=mitigated; runtime=blocked; external=blocked**
- Owner: **Data engineering**
- Summary: The repository defines owners, alert conditions, detection targets, retention expectations, bounded SQL, and runbooks, but no notification destination, acknowledgement path, retention job, or live dashboard is deployed.
- Source evidence: [`governance/operational_alert_policy.json`](../governance/operational_alert_policy.json), [`sql/operational_health.sql`](../sql/operational_health.sql), [`docs/runbooks/operational_health.md`](../docs/runbooks/operational_health.md), [`scripts/validate_operational_observability.py`](../scripts/validate_operational_observability.py)
- Pending evidence: A deployed query or dashboard identifier is recorded.; A named notification destination receives and acknowledges a test alert.; Retention operations run in dry-run mode with recovery-window and Delta-version evidence before deletion.
- Dependencies: issue #44
- Next action: Select an external notification destination and authenticated development workspace before activating delivery or retention.

### R-017 — Runtime compatibility can drift through partial upgrades

- Priority: **medium**
- Layer status: **source=mitigated; runtime=pending; external=not_applicable**
- Owner: **Repository maintainer**
- Summary: The current Python, Java, PySpark, Py4J, Databricks Runtime, and runner baseline is machine readable and partial major upgrades are prohibited, but a future upgrade still requires complete local and Databricks evidence.
- Source evidence: [`governance/runtime_compatibility.json`](../governance/runtime_compatibility.json), [`scripts/validate_runtime_compatibility.py`](../scripts/validate_runtime_compatibility.py), [`requirements-spark.txt`](../requirements-spark.txt), [`databricks.yml`](../databricks.yml)
- Pending evidence: Any candidate upgrade changes Python, Java, PySpark, Py4J, and Databricks Runtime coherently.; The complete standard and Spark suites pass on the candidate matrix.; Development Databricks execution and representative performance are captured before production consideration.
- Dependencies: None.
- Next action: Treat runtime upgrades as a dedicated compatibility programme rather than independent dependency merges.

## Closure rule

Closing a risk requires the machine-readable layer states and the Markdown register to change together, all linked source evidence to remain resolvable, applicable runtime or external evidence to be retained, rollback implications to be recorded, and a human reviewer to accept the exact change. Repository-source mitigation is not runtime closure. Agent confidence is not closure.
