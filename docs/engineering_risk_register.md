# Engineering Risk Register

This register is an audit snapshot, not proof that every risk is actively causing a failure. `Open — confirmed` means the cited configuration or behaviour was reproduced or directly observed. `Open — validate` means impact still needs runtime proof. `Source mitigated — validate` means a reviewed repository change removed the identified source defect, but effective Databricks or GitHub state still requires independent evidence. The repository maintainer owns triage until a named owner accepts an item.

## R-001 — Databricks deployment authentication fails

- Priority/status: **Critical — Open, confirmed**
- Owner/target: Repository maintainer; before the next deployment
- Observed: 2026-08-14
- Evidence: [Actions run 31846624702](https://github.com/alexmarinos87/databricks-lakehouse-telemetry-demo/actions/runs/31846624702) reached dev bundle validation with a host but empty client ID/secret and failed authentication.
- Required closure: Configure environment-scoped workload identity federation or valid service-principal authentication, add an auth preflight, and attach a successful validation run before any apply-enabled dispatch.
- Closure evidence: Pending.

## R-002 — Development-mode names break exact post-deploy lookups

- Priority/status: **High — Source mitigated, validate effective names**
- Owner/target: Repository maintainer; before the next dev deployment
- Observed: 2026-08-14; source mitigation merged 2026-08-19
- Evidence: The previous dev bundle applied development-mode prefixes while the [deploy workflow](../.github/workflows/deploy.yml), [grant helper](../scripts/apply_uc_grants.py), and [query helper](../scripts/upsert_reporting_queries.py) searched for exact unprefixed resource names. [PR #27](https://github.com/alexmarinos87/databricks-lakehouse-telemetry-demo/pull/27) now clears the target name prefix and keeps deterministic target-qualified names.
- Required closure: Inspect authenticated dev/prod plans and prove schema, volume, event-log, job, pipeline and SQL warehouse resolution end to end.
- Closure evidence: Source configuration merged in commit `77ec43b`; runtime evidence pending.

## R-003 — The expectations pipeline used an unsupported edition and fixed development mode

- Priority/status: **High — Source mitigated, validate pipeline refresh**
- Owner/target: Repository maintainer; before the next deployment
- Observed: 2026-08-14; source mitigation merged 2026-08-19
- Evidence: The previous [pipeline resource](../resources/lakehouse_quality_expectations.yml) selected `CORE` while its notebook defined expectations and fixed `development: true` for both targets. [PR #27](https://github.com/alexmarinos87/databricks-lakehouse-telemetry-demo/pull/27) selects `ADVANCED` and delegates development mode to target presets.
- Required closure: Validate rendered dev/prod plans and attach a successful development pipeline update and refresh with expectation metrics.
- Closure evidence: Source configuration merged in commit `77ec43b`; Databricks runtime evidence pending.

## R-004 — Local tests overstate runtime confidence

- Priority/status: **High — Open, confirmed**
- Owner/target: Repository maintainer; before production readiness
- Observed: 2026-08-14
- Evidence: [Local checks](../scripts/run_local_checks.sh) compile Python and run unit tests, while many existing tests—such as the [warehouse contracts](../tests/test_warehouse_model_contract.py)—assert source text rather than executing PySpark transformations or deployment helpers.
- Required closure: Add executable transformation and helper tests for grain, keys, reconciliation, retries and failure behaviour; keep runtime checks distinct from source contracts.
- Closure evidence: Pending.

## R-005 — Warehouse joins and keys can silently lose or merge records

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness
- Observed: 2026-08-14
- Evidence: [Warehouse construction](../notebooks/07_warehouse_model.py) uses inner dimension joins, nondeterministic machine deduplication and a daily machine key that assumes machine IDs cannot change site/model within a day.
- Required closure: Define unknown-member handling and key grain, make dimension selection deterministic, and add executable foreign-key/count reconciliation tests.
- Closure evidence: Pending.

## R-006 — Warehouse percentages lack reconciled business bounds

- Priority/status: **High — Open, confirmed with sample data**
- Owner/target: Repository maintainer; before production readiness
- Observed: 2026-08-14
- Evidence: The [sample events](../data/sample_machine_events.csv) produce downtime above observed duration for one machine/day, and [warehouse facts](../notebooks/07_warehouse_model.py) have no percentage-bound or duration-reconciliation gate.
- Required closure: Define downtime semantics, add warehouse invariants and reconciliation expectations, and execute edge-case transformation tests.
- Closure evidence: Pending.

## R-007 — Main-branch checks are advisory

- Priority/status: **High — Open, confirmed from GitHub settings**
- Owner/target: Repository maintainer; before accepting production changes
- Observed: 2026-08-14; reconfirmed 2026-08-19
- Evidence: GitHub reports no protection for `main`; repository settings therefore do not require the CI check, pull-request review or resolved conversations.
- Required closure: Require pull requests, CI and resolved conversations; prevent force-push and deletion; require a human approval when a second human maintainer exists.
- Closure evidence: Pending. Repository files cannot enforce this setting.

## R-008 — Deployment polling and supply-chain inputs are not bounded or immutable

- Priority/status: **Medium — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness
- Observed: 2026-08-14; trigger mitigation merged 2026-08-19
- Evidence: [Grant application](../scripts/apply_uc_grants.py) polls without an overall deadline, while workflows and container definitions use mutable action, branch or floating image tags. [PR #28](https://github.com/alexmarinos87/databricks-lakehouse-telemetry-demo/pull/28) removed automatic push deployment and made apply intent explicit, reducing accidental execution but not resolving polling or supply-chain mutability.
- Required closure: Add polling deadlines and job timeouts, pin actions, CLI setup and container inputs by immutable digest or reviewed version, and enable dependency update automation.
- Closure evidence: Manual plan-first deployment merged in commit `359f21d`; remaining controls pending.

## R-009 — Sequential multi-table overwrites can expose a mixed publication

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness
- Observed: 2026-08-14
- Evidence: [Silver](../notebooks/02_silver_transform.py), [Gold](../notebooks/03_gold_models.py), [forecast](../notebooks/05_forecast_validation.py) and [warehouse](../notebooks/07_warehouse_model.py) outputs are overwritten table by table, so interruption can expose inconsistent versions.
- Required closure: Define atomic or versioned publication boundaries, attach run-level manifests/reconciliation, and prove recovery from interruption in every table group.
- Closure evidence: Pending.

## R-010 — Reusing a source filename may not trigger replay

- Priority/status: **High — Open, validate in Databricks**
- Owner/target: Repository maintainer; before relying on repeatable sample loads
- Observed: 2026-08-14; trigger behaviour changed 2026-08-19
- Evidence: The [deploy workflow](../.github/workflows/deploy.yml) can still overwrite one source object name when an operator explicitly enables sample upload, while [bronze ingestion](../notebooks/01_bronze_ingest.py) retains Auto Loader checkpoint state. PR #28 made upload manual and default-off but did not define replay semantics.
- Required closure: Define replay semantics, use immutable source object names or an explicit reset/backfill procedure, and test repeated upload behaviour end to end.
- Closure evidence: Pending.

## R-011 — Owner-run editable reporting queries may cross a privilege boundary

- Priority/status: **High — Open, validate access model**
- Owner/target: Repository maintainer; before analyst access is enabled
- Observed: 2026-08-14
- Evidence: [Query publication](../scripts/upsert_reporting_queries.py) grants engineers edit access and analysts run access to owner-executed saved queries.
- Required closure: Confirm the trust model, separate query ownership from deployment identity, and prove editors cannot turn owner-run assets into an elevation path.
- Closure evidence: Pending.

## R-012 — Dev and prod data/state resources could collide

- Priority/status: **High — Source mitigated, validate rendered targets**
- Owner/target: Repository maintainer; before the next deployment
- Observed: 2026-08-14; source mitigation merged 2026-08-19
- Evidence: The previous [bundle variables and targets](../databricks.yml) inherited the same schema, volume, source, checkpoint and schema-location values while notebooks performed full overwrites. [PR #27](https://github.com/alexmarinos87/databricks-lakehouse-telemetry-demo/pull/27) added disjoint target defaults and target-specific workflow variables.
- Required closure: Inspect effective authenticated plans and prove dev cannot read, replace or checkpoint production data; decide how any pre-existing shared state will be migrated or abandoned.
- Closure evidence: Source configuration merged in commit `77ec43b`; rendered-plan and workspace evidence pending.

## R-013 — Quality stages do not enforce warehouse or durable failure evidence

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before production readiness
- Observed: 2026-08-14
- Evidence: [Quality checks](../notebooks/04_quality_checks.py) omit warehouse tables and can raise on a missing table before persisting accumulated results. The [Lakeflow expectations](../notebooks/06_lakeflow_quality_expectations.py) monitor with `expect_all` rather than failing or dropping invalid rows.
- Required closure: Define enforced warehouse/reconciliation gates, persist failure evidence safely, and document which expectations monitor, drop or fail the update.
- Closure evidence: Pending.

## R-014 — Forecast validation labels are weaker than their business meaning

- Priority/status: **High — Open, confirmed from implementation**
- Owner/target: Repository maintainer; before client-facing use
- Observed: 2026-08-14
- Evidence: [Forecast validation](../notebooks/05_forecast_validation.py) labels row windows as days, marks a segment validated from sample count without an accuracy threshold, and overwrites previously issued forecasts.
- Required closure: Define calendar/time semantics and accuracy thresholds, retain forecast vintages, and execute backtests that demonstrate the client-facing claim.
- Closure evidence: Pending.

## R-015 — Deployment and runtime privileges are coupled

- Priority/status: **High — Open, validate least privilege**
- Owner/target: Repository maintainer; before production readiness
- Observed: 2026-08-14
- Evidence: [Unity Catalog grants](../resources/access_controls.yml) give the CI principal broad create/modify/read/write capabilities, bundle resources also give it management permissions, and no explicit `run_as` separation is configured.
- Required closure: Inspect the rendered effective owner/runtime identity, separate deployment/runtime identities where practical, minimize grants per resource and task, and test denied actions as well as required actions.
- Closure evidence: Pending.

## Closure Rule

Closing a risk requires a linked pull request, reproducible test or run evidence, rollback implications, and the human reviewer who accepted closure. A repository-source mitigation is not runtime closure. Confidence from an agent is not closure. Changes to priority, status, owner or target date belong in the same durable review record.
