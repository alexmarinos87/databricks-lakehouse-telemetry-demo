# Engineering Risk Register

This register is an audit snapshot, not proof that every risk is actively causing a failure. `Open — confirmed` means the cited configuration or behaviour was reproduced or directly observed; `Open — validate` means impact still needs runtime proof. The repository maintainer owns triage until a named owner accepts an item.

## R-001 — Databricks deployment authentication fails

- Priority/status: **Critical — Open, confirmed**
- Owner/target: Repository maintainer; before the next deployment (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Actions run 31846624702](https://github.com/alexmarinos87/databricks-lakehouse-telemetry-demo/actions/runs/31846624702) reached dev bundle validation with a host but empty client ID/secret and failed authentication.
- Required closure: Configure environment-scoped workload identity federation or valid service-principal authentication, add an auth preflight, and attach a successful validation/deployment run.
- Closure evidence: Pending.

## R-002 — Development-mode names break exact post-deploy lookups

- Priority/status: **Critical — Open, confirmed from rendered configuration**
- Owner/target: Repository maintainer; before the next dev deployment (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: The dev bundle applies development-mode prefixes, while the [deploy workflow](../.github/workflows/deploy.yml#L124-L186), [grant helper](../scripts/apply_uc_grants.py#L62-L67), and [query helper](../scripts/upsert_reporting_queries.py#L39-L47) search for the exact unprefixed warehouse name. Schema, volume and event-log consumers also use literal variable values rather than deployed resource outputs.
- Required closure: Inspect saved dev/prod plans, bind consumers to resource outputs or an explicit naming preset, and prove schema, volume, event-log and warehouse resolution end to end.
- Closure evidence: Pending.

## R-003 — The expectations pipeline uses an unsupported edition and production mode

- Priority/status: **Critical — Open, confirmed configuration defect**
- Owner/target: Repository maintainer; before the next deployment (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: The [pipeline resource](../resources/lakehouse_quality_expectations.yml#L16-L27) sets `edition: CORE` while its notebook defines expectations, and it fixes `development: true` for both targets. [Databricks pipeline configuration](https://docs.databricks.com/aws/en/ldp/configure-pipeline) documents expectations as an `ADVANCED` feature and rejects unsupported pipeline features.
- Required closure: Select `ADVANCED`, make development mode target-specific, validate rendered dev/prod plans, and attach a successful pipeline update/refresh.
- Closure evidence: Pending.

## R-004 — Local tests overstate runtime confidence

- Priority/status: **High — Open, confirmed**
- Owner/target: Repository maintainer; before production readiness (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Local checks](../scripts/run_local_checks.sh) compile Python and run unit tests, while many existing tests—such as the [warehouse contracts](../tests/test_warehouse_model_contract.py)—assert source text rather than executing PySpark transformations or deployment helpers.
- Required closure: Add executable transformation and helper tests for grain, keys, reconciliation, retries and failure behavior; keep runtime checks distinct from source contracts.
- Closure evidence: Pending.

## R-005 — Warehouse joins and keys can silently lose or merge records

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Warehouse construction](../notebooks/07_warehouse_model.py) uses inner dimension joins, nondeterministic machine deduplication and a daily machine key that assumes machine IDs cannot change site/model within a day.
- Required closure: Define unknown-member handling and key grain, make dimension selection deterministic, and add executable foreign-key/count reconciliation tests.
- Closure evidence: Pending.

## R-006 — Warehouse percentages lack reconciled business bounds

- Priority/status: **High — Open, confirmed with sample data**
- Owner/target: Repository maintainer; before production readiness (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: The [sample events](../data/sample_machine_events.csv) produce downtime above observed duration for one machine/day, and [warehouse facts](../notebooks/07_warehouse_model.py) have no percentage-bound or duration-reconciliation gate.
- Required closure: Define downtime semantics, add warehouse invariants and reconciliation expectations, and execute edge-case transformation tests.
- Closure evidence: Pending.

## R-007 — Main-branch checks are advisory

- Priority/status: **High — Open, confirmed from GitHub settings**
- Owner/target: Repository maintainer; before accepting production changes (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: GitHub reported no repository rulesets and no protection for `main`; [repository rules](https://github.com/alexmarinos87/databricks-lakehouse-telemetry-demo/settings/rules) therefore do not require the CI check or pull-request review.
- Required closure: Require pull requests, CI and resolved conversations; prevent force-push/deletion; require a human approval when a second human maintainer exists.
- Closure evidence: Pending.

## R-008 — Deployment polling and supply-chain inputs are not bounded or immutable

- Priority/status: **Medium — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness (triage by 2026-08-28)
- Observed: 2026-08-14
- Evidence: [Grant application](../scripts/apply_uc_grants.py) polls without an overall deadline, while workflows and container definitions use mutable action, branch or floating image tags.
- Required closure: Add polling deadlines and job timeouts, pin actions/CLI/container versions, and enable dependency update automation.
- Closure evidence: Pending.

## R-009 — Sequential multi-table overwrites can expose a mixed publication

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Silver](../notebooks/02_silver_transform.py), [Gold](../notebooks/03_gold_models.py), [forecast](../notebooks/05_forecast_validation.py) and [warehouse](../notebooks/07_warehouse_model.py) outputs are overwritten table by table, so interruption can expose inconsistent versions.
- Required closure: Define atomic or versioned publication boundaries, attach run-level manifests/reconciliation, and prove recovery from interruption in every table group.
- Closure evidence: Pending.

## R-010 — Reusing a source filename may not trigger replay

- Priority/status: **High — Open, validate in Databricks**
- Owner/target: Repository maintainer; before relying on repeatable sample loads (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: The [deploy workflow](../.github/workflows/deploy.yml#L137-L144) overwrites one source object name while [bronze ingestion](../notebooks/01_bronze_ingest.py) retains Auto Loader checkpoint state.
- Required closure: Define replay semantics, use immutable source object names or an explicit reset/backfill procedure, and test repeated upload behavior end to end.
- Closure evidence: Pending.

## R-011 — Owner-run editable reporting queries may cross a privilege boundary

- Priority/status: **High — Open, validate access model**
- Owner/target: Repository maintainer; before analyst access is enabled (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Query publication](../scripts/upsert_reporting_queries.py#L55-L69) grants engineers edit access and analysts run access to owner-executed saved queries.
- Required closure: Confirm the trust model, separate query ownership from deployment identity, and prove editors cannot turn owner-run assets into an elevation path.
- Closure evidence: Pending.

## R-012 — Dev and prod data/state resources can collide

- Priority/status: **Critical — Open, validate rendered targets**
- Owner/target: Repository maintainer; before the next deployment (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Bundle variables and targets](../databricks.yml) inherit the same default catalog, schema, volume, source, checkpoint and schema-location values, while notebooks perform full overwrites.
- Required closure: Assign target-specific catalogs/schemas/volumes/checkpoints, inspect effective plans, and prove dev cannot read, replace or checkpoint production data.
- Closure evidence: Pending.

## R-013 — Quality stages do not enforce warehouse or durable failure evidence

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Quality checks](../notebooks/04_quality_checks.py) omit warehouse tables and can raise on a missing table before persisting accumulated results. The [Lakeflow expectations](../notebooks/06_lakeflow_quality_expectations.py) monitor with `expect_all` rather than failing or dropping invalid rows.
- Required closure: Define enforced warehouse/reconciliation gates, persist failure evidence safely, and document which expectations monitor, drop or fail the update.
- Closure evidence: Pending.

## R-014 — Forecast validation labels are weaker than their business meaning

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before client-facing use (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Forecast validation](../notebooks/05_forecast_validation.py) labels row windows as days, marks a segment validated from sample count without an accuracy threshold, and overwrites previously issued forecasts.
- Required closure: Define calendar/time semantics and accuracy thresholds, retain forecast vintages, and execute backtests that demonstrate the client-facing claim.
- Closure evidence: Pending.

## R-015 — Deployment and runtime privileges are coupled

- Priority/status: **High — Open, validate least privilege**
- Owner/target: Repository maintainer; before production readiness (triage by 2026-08-21)
- Observed: 2026-08-14
- Evidence: [Unity Catalog grants](../resources/access_controls.yml#L31-L39) give the CI principal broad create/modify/read/write capabilities, bundle resources also give it management permissions, and no explicit `run_as` separation is configured.
- Required closure: Inspect the rendered effective owner/runtime identity, separate deployment/runtime identities where practical, minimize grants per resource and task, and test denied actions as well as required actions.
- Closure evidence: Pending.

## Closure Rule

Closing a risk requires a linked pull request, reproducible test or run evidence, rollback implications, and the human reviewer who accepted closure. Confidence from an agent is not closure. Changes to priority, status, owner or target date belong in the same durable review record.
