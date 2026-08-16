# arc42 Architecture Map

This document is an evidence-backed architecture map of the repository, not a
claim about a successfully deployed workspace. Local file links identify the
implementation or configuration that supports each statement. The numbered
structure follows the official [arc42 template](https://docs.arc42.org/).

<a id="section-1"></a>
## 1. Introduction and Goals

This section follows [arc42 section 1](https://docs.arc42.org/section-1/).

The system turns synthetic construction-equipment telemetry into trusted Delta
tables, BI-ready aggregates, a dimensional warehouse, a transparent forecast,
and quality evidence. Its primary purpose is a compact, explainable data
engineering demonstration; [README.md](../README.md) defines the portfolio and
business scope.

| Stakeholder | Goal | Success signal |
| --- | --- | --- |
| Analyst or BI consumer | Query governed reliability, cost, asset, forecast, and quality outputs without rebuilding transformation logic | Published tables and saved SQL have documented grains and stable names |
| Data engineer | Ingest and transform repeatable synthetic events with traceable invalid-record handling | Source lineage, Silver quarantine, deterministic business keys, and actionable quality results |
| Platform or repository maintainer | Review, deploy, operate, and recover the demo safely in dev and prod | Reproducible checks, isolated state, bounded failures, and explicit deployment evidence |
| Interview or portfolio reader | Understand the important architectural choices and limitations | The data flow, runtime flow, deployment nodes, decisions, and risks can be explained from repository evidence |

The leading quality goals are correctness, auditability, replayability,
environment isolation, operability, security, cost restraint, and
maintainability. Section 10 turns them into measurable scenarios.

<a id="section-2"></a>
## 2. Architecture Constraints

This section follows [arc42 section 2](https://docs.arc42.org/section-2/).

| Constraint | Architectural consequence |
| --- | --- |
| Databricks-native runtime | Notebooks use Spark and Delta; Asset Bundles describe Jobs, a Lakeflow pipeline, a SQL warehouse, Unity Catalog objects, and permissions. Kubernetes is outside scope. |
| Synthetic, company-neutral data | Repository fixtures under [data/](../data/) are the only bundled business data. Production or client data is outside scope. |
| Incremental file ingestion | Bronze uses Auto Loader with durable checkpoint and schema-location state; replay cannot be reasoned about from Delta tables alone. |
| Small, explainable demonstration | Transformations favor explicit notebooks and a rolling-mean forecast over an external service or opaque model. |
| Standard-library local checks | [run_local_checks.sh](../scripts/run_local_checks.sh) compiles Python and runs unit tests; it does not execute Spark or prove Databricks behavior. |
| Repository-driven delivery | GitHub Actions and Asset Bundles are the declared delivery path. Environment approvals and secrets are repository settings, not code-owned guarantees. |
| Backward-compatible published names | Existing table, task, query, widget, catalog, schema, and path contracts should be treated as interfaces unless a migration is explicitly accepted. |

<a id="section-3"></a>
## 3. Context and Scope

This section follows [arc42 section 3](https://docs.arc42.org/section-3/),
separating business communication partners from technical interfaces.

### Business context

```text
Telemetry file producer
        | machine-event CSV
        v
Databricks Lakehouse Demo
        | governed Delta tables, saved SQL, forecast and quality evidence
        v
Analyst / BI consumer

Repository maintainer ---> source, configuration and release intent
```

| Partner | Input to the system | Output from the system |
| --- | --- | --- |
| Telemetry file producer | CSV rows following the machine-event header contract | Ingestion outcome and retained Bronze lineage; no callback interface exists |
| Analyst or BI consumer | SQL execution and filters | Gold aggregates, warehouse facts/dimensions, forecasts, quality results, and expectation metrics |
| Repository maintainer | Code, configuration, environment values, credentials references, and deployment decision | CI results, bundle plan/deploy output, workflow status, and repository documentation |

### Technical context and trust boundaries

| External system or actor | Interface | Boundary and ownership |
| --- | --- | --- |
| GitHub and GitHub Actions | Git, workflow events, environment variables and secrets | Hosts source and runners; environment protection and secret configuration are external repository settings |
| Databricks control plane | CLI, Asset Bundle APIs, Jobs, pipeline, SQL Query and permission APIs | Managed platform; repository owns desired configuration, not service availability |
| Databricks data plane | Spark job cluster, Lakeflow pipeline cluster, SQL warehouse | Executes repository code; runtime version and node types are bundle variables |
| Unity Catalog and Delta | Catalog, schema, volume, tables, grants, and pipeline event log | Persistent governed data and metadata; environment isolation depends on effective variables and permissions |
| DBFS or ADLS Gen2 | File paths and, for direct ADLS, OAuth Spark configuration | Optional external landing and state storage; identity and availability are outside repository control |
| Databricks secret service | Secret scope/key lookup from the Bronze notebook | Supplies the ADLS client secret without storing its value in source |

The system boundary contains repository-owned notebooks, Python helpers, SQL,
resource definitions, automation, tests, fixtures, and documentation. It does
not contain GitHub settings, Databricks managed services, cloud storage,
identities, secret values, BI tools, or deployed data.

<a id="section-4"></a>
## 4. Solution Strategy

This section follows [arc42 section 4](https://docs.arc42.org/section-4/).

- Use a medallion flow to preserve source-shaped Bronze data, create a trusted
  Silver event core, and publish purpose-specific Gold aggregates.
- Add a dimensional serving layer after Gold so reporting consumers can share
  dimensions and avoid repeating joins.
- Use Auto Loader `availableNow` with checkpoint and schema state for scheduled
  incremental ingestion.
- Use Delta tables as the data-plane interfaces between independently named
  workflow tasks.
- Combine an imperative error-level gate with Lakeflow declarative expectations
  and an event log for two complementary quality surfaces.
- Keep forecasting transparent with a recent-history rolling baseline and
  publish validation metrics alongside future estimates.
- Describe and deploy managed resources with Databricks Asset Bundles; use
  GitHub Actions and a Dockerized local check as the delivery control path.

These choices are observable in the repository. They are not automatically
accepted architecture decisions; section 9 records them as observed or implied
and identifies their consequences.

<a id="section-5"></a>
## 5. Building Block View

This section follows [arc42 section 5](https://docs.arc42.org/section-5/).

### Level-1 dependency view

```text
Data interfaces:
CSV -> BB-01 Ingestion -> BB-02 Trusted Core -> BB-03 Curated Analytics
                                         |               |          |
                                         |               |          +-> BB-05 Forecast
                                         |               +------------> BB-06 Quality
                                         +----------------------------> BB-06 Quality
BB-03 Curated -> BB-04 Serving -> saved SQL -> analyst
BB-04 Serving + BB-05 Forecast + BB-06 Quality -> analyst

Orchestration order:
BB-01 -> BB-02 -> BB-03 -> BB-04 -> BB-06 imperative -> BB-05
      -> BB-06 declarative

BB-07 Platform Automation deploys and invokes runtime blocks.
BB-08 Engineering Assurance validates and explains repository changes.
```

The imperative quality task is ordered after BB-04 but currently reads only
Bronze, Silver, quarantine, and Gold tables. That orchestration edge is not a
warehouse-validation data edge.

### Level-1 blackboxes

| ID | Building block | Responsibility | Provided interfaces | Depends on | Runtime and deployment relevance | Quality focus and current exposure |
| --- | --- | --- | --- | --- | --- | --- |
| BB-01 | <a id="bb-ingestion"></a>Ingestion Adapter | Resolve DBFS, UC Volume, or ADLS paths and authentication; incrementally retain source-shaped records with lineage | Machine-event CSV in; `bronze_machine_events`, checkpoint, and schema metadata out | File storage, secret service when direct ADLS OAuth is used, Spark Auto Loader, Unity Catalog | [Bronze notebook](../notebooks/01_bronze_ingest.py), [ingestion helper](../src/lakehouse_demo/azure_ingestion.py), first Job task | Schema fidelity, lineage, replay, checkpoint compatibility, and secret safety; repeated-name replay is not proven |
| BB-02 | <a id="bb-trusted"></a>Trusted Event Core | Cast and normalize fields, separate invalid rows, deduplicate `event_id`, and derive event/health fields | Bronze Delta in; `silver_machine_events` and `silver_quarantine_machine_events` out | BB-01 and Spark/Delta | [Silver notebook](../notebooks/02_silver_transform.py), second Job task | Deterministic typing, keys, quarantine, and deduplication; overwrite publication and equal-order ties remain exposures |
| BB-03 | <a id="bb-curated"></a>Curated Analytics | Aggregate trusted events into uptime, failures, cost, parts, and client asset models | Silver Delta in; five `gold_*` tables out | BB-02 and Spark/Delta | [Gold notebook](../notebooks/03_gold_models.py), third Job task | Stable grains, reconciled measures, and explainability; sequential overwrite can expose mixed versions |
| BB-04 | <a id="bb-serving"></a>Dimensional Serving and Reporting | Build shared dimensions and uptime/failure facts; provide reusable analyst SQL | Gold uptime/failure tables in; six dimensions, two facts, local SQL, and saved SQL Queries out | BB-03, Spark/Delta, SQL warehouse, Unity Catalog | [Warehouse notebook](../notebooks/07_warehouse_model.py), [SQL assets](../sql/), fourth Job task plus post-deploy query publication | Referential integrity, stable key grain, query usability; inner joins and nondeterministic machine selection can lose or merge records |
| BB-05 | <a id="bb-forecast"></a>Forecast Analytics | Backtest a rolling downtime baseline and publish next-horizon estimates with metrics and intervals | `gold_machine_uptime` in; validation and forecast Delta tables out | BB-03, BB-06 imperative gate by orchestration, Spark/Delta | [Forecast notebook](../notebooks/05_forecast_validation.py), sixth Job task | Transparent accuracy, temporal semantics, readiness criteria, and vintages; row windows and overwrite history weaken those claims |
| BB-06 | <a id="bb-quality"></a>Quality and Observability | Run imperative table/data checks and declarative expectations; expose current, historical, and managed quality evidence | Bronze/Silver/quarantine/Gold/forecast tables in; `quality_check_results`, `quality_metric_history`, expectation views, and event log out | BB-01, BB-02, BB-03, BB-05; ordered after BB-04 without currently reading it | [Imperative checks](../notebooks/04_quality_checks.py) and [Lakeflow expectations](../notebooks/06_lakeflow_quality_expectations.py), fifth and seventh tasks | Detection, durable failure evidence, ownership, and alertability; warehouse is omitted and some missing-table failures precede persistence |
| BB-07 | <a id="bb-platform"></a>Orchestration and Platform Automation | Describe targets/resources, sequence tasks, deploy bundles, apply grants, upload fixtures, run Jobs, and publish saved queries | GitHub events and environment configuration in; deployed resources, permissions, files, runs, and saved queries out | GitHub Actions, Databricks CLI/APIs, service principal, BB-01 through BB-06 | [Bundle](../databricks.yml), [resources](../resources/), [deployment workflow](../.github/workflows/deploy.yml), deployment scripts | Isolation, idempotency, bounded retries, least privilege, recovery, supply-chain integrity, and cost; several guarantees depend on external configuration |
| BB-08 | <a id="bb-assurance"></a>Engineering Assurance and Documentation | Validate syntax/contracts/fixtures and explain architecture, setup, deployment, and interview narrative | Repository source in; local/CI test results and documentation out | Python, Docker, GitHub Actions, all blocks as review subjects | [Tests](../tests/), [local checks](../scripts/run_local_checks.sh), [CI workflow](../.github/workflows/ci.yml), [documentation](../docs/) | Maintainability and credible evidence; local tests do not execute Spark or a Databricks deployment |

### Source-family ownership

Every repository-owned source family has a Level-1 owner. A family can support
more than one block without moving its primary architectural responsibility.

| Source family | Primary owner | Architectural role |
| --- | --- | --- |
| [data/](../data/) | BB-01 and BB-08 | Synthetic ingress examples and executable contract fixtures; the deploy workflow can upload the base sample |
| [notebooks/](../notebooks/) | BB-01 through BB-06 | Databricks data-plane implementation |
| [src/](../src/) | BB-01 | Importable ingestion path, identifier, and OAuth configuration helpers |
| [sql/](../sql/) | BB-04, BB-05, and BB-06 | Manual and manifest-driven reporting queries over serving, forecast, and quality outputs |
| [resources/](../resources/) | BB-07 | Job, pipeline, SQL warehouse, schema, volume, and permission desired state |
| [scripts/](../scripts/) | BB-07 and BB-08 | Post-deploy grants/query publication and portable local validation |
| [tests/](../tests/) | BB-08 | Standard-library unit and repository-contract evidence |
| [.github/workflows/](../.github/workflows/) | BB-07 and BB-08 | CI plus dev/prod validation and deployment control flow |
| [docs/](../docs/) | BB-08 | Architecture, setup, deployment, and portfolio explanations |
| [databricks.yml](../databricks.yml) | BB-07 | Bundle variables, includes, targets, and workspace roots |
| [Dockerfile.ci](../Dockerfile.ci), [.dockerignore](../.dockerignore) | BB-08 | Repeatable CI validation image and build context |
| [README.md](../README.md), [.gitignore](../.gitignore) | BB-08 | Repository entry point and local/generated-file boundary |

<a id="change-modularity"></a>
### Change modularity and candidate seams

A bounded change should have one primary Level-1 block plus the BB-08 tests and
documentation needed to prove and explain it. If a change alters another
block's published table, file, task, query, state, permission, or behavioral
interface, split it into independently reviewable changes or declare the
cross-block coupling, migration, failure, and rollback plan before editing.

The following are candidate planning seams visible in current-main structure.
They do not assert that an implementation, branch, or unmerged module exists.

| ID | Candidate seam | Primary block | Candidate scope | Boundary condition | Status |
| --- | --- | --- | --- | --- | --- |
| SEAM-01 | Architecture assurance | BB-08 | Architecture documentation and behavior-focused documentation contracts | Remains documentation/test-only; any runtime contract change moves to its owning block | Candidate only; no implementation is asserted |
| SEAM-02 | Source-event contract | BB-01 | Canonical source fields, fixture validation, and ingestion-facing contract evidence | Preserve the BB-01 to BB-02 Bronze interface; split or declare changes to Silver rules or replay state | Candidate only; no implementation is asserted |
| SEAM-03 | Warehouse contract | BB-04 | Fact/dimension grain, key, and reconciliation rules with bounded executable fixtures | Preserve BB-03 Gold inputs and BB-04 published outputs; split or declare changes to producers, quality gates, or serving schemas | Candidate only; no implementation is asserted |

<a id="section-6"></a>
## 6. Runtime View

This section follows [arc42 section 6](https://docs.arc42.org/section-6/).

<a id="runtime-happy-path"></a>
### Runtime scenario A: scheduled happy path

1. BB-01 resolves one configured storage mode, creates the schema or optional
   managed volume, and runs Auto Loader in `availableNow` mode.
2. BB-02 reads Bronze, quarantines rows with missing required business keys,
   keeps the newest `event_id` by ingestion metadata, and overwrites trusted
   Silver outputs.
3. BB-03 reads Silver and overwrites five Gold tables.
4. BB-04 derives dimensions and facts and overwrites them sequentially.
5. BB-06 checks configured Bronze/Silver/quarantine/Gold tables, overwrites
   current results, appends a summary history row, and raises if error-level
   checks failed.
6. If the gate succeeds, BB-05 overwrites backtest and forecast outputs.
7. If its pipeline configuration is valid, BB-06 refreshes expectation-backed
   materialized views and records managed metrics in the pipeline event log.

Current main selects the `CORE` pipeline edition while using `@dp.expect_all`.
Databricks documents expectations as an `ADVANCED` feature and says an
unsupported selected edition produces an error. Step 7 is therefore intended
flow, not demonstrated current behavior; see the official
[pipeline configuration guidance](https://docs.databricks.com/aws/en/ldp/configure-pipeline).

<a id="runtime-invalid-record"></a>
### Runtime scenario B: invalid or duplicate record

- BB-01 retains the source-shaped row and `_source_file`/`_ingested_at` lineage.
- BB-02 sends a row missing `event_id`, `machine_id`, timestamp, site, or client
  to quarantine. It deduplicates trusted rows by `event_id`, ordered by newest
  ingestion timestamp and source filename.
- Transformation blocks consume trusted Silver or its derivatives rather than
  quarantine. BB-06 also reads Bronze and quarantine, but its quality notebook
  does not reconcile every Bronze exclusion to quarantine.

<a id="runtime-partial-publication"></a>
### Runtime scenario C: missing table or partial publication failure

- Silver, Gold, forecast, and warehouse groups write tables one at a time using
  overwrite. If a later write fails, earlier tables can expose the new run while
  later tables still expose the previous run; there is no publication manifest.
- The imperative quality notebook records table-read failures in memory, but it
  then directly reads Silver and Gold tables. A missing dependency can therefore
  raise before `quality_check_results` and `quality_metric_history` are written.
- The Job stops at the failing task. Repository code defines no automatic table
  rollback or coordinated restoration order.

<a id="runtime-delivery-failure"></a>
### Runtime scenario D: delivery or external-service failure

1. CI builds [Dockerfile.ci](../Dockerfile.ci) and runs portable checks.
2. The deployment workflow validates and plans the selected bundle target before
   deployment, then may apply grants, upload a fixture, run the Job, and publish
   SQL Queries.
3. Authentication, lookup, API, permission, or workspace failures stop the
   current step. Previously completed external changes are not rolled back.
4. The grant helper polls SQL statements until completion without an overall
   deadline, so one external call can remain unbounded.

<a id="section-7"></a>
## 7. Deployment View

This section follows [arc42 section 7](https://docs.arc42.org/section-7/).

```text
GitHub-hosted runner
  | Docker local checks, Databricks CLI/API
  v
Databricks workspace
  +-- shared one-worker Job cluster: BB-01, BB-02, BB-03, BB-04, BB-06, BB-05
  +-- one-worker Lakeflow pipeline cluster: BB-06 declarative expectations
  +-- 2X-Small SQL warehouse: BB-04/05/06 queries and grant statements
  +-- Unity Catalog schema + managed volume + Delta tables + event log
  +-- workspace paths for synchronized bundle files and saved SQL folders

Optional external node: DBFS or ADLS Gen2 source/checkpoint/schema storage
```

| Block | GitHub runner | Job cluster | Pipeline cluster | SQL warehouse | Persistent nodes |
| --- | --- | --- | --- | --- | --- |
| BB-01 | Deploy/upload control | Bronze execution | — | — | Source files, checkpoint, schema metadata, Bronze Delta |
| BB-02 and BB-03 | Deploy control | Silver and Gold execution | — | — | Silver, quarantine, and Gold Delta tables |
| BB-04 | Deploy/query-publication control | Warehouse execution | — | Query execution | Dimensions, facts, and saved SQL metadata |
| BB-05 | Deploy control | Forecast execution | — | Query execution | Validation and forecast Delta tables |
| BB-06 | Deploy control | Imperative checks | Declarative expectations | Quality queries and grant statements | Current/history results, materialized views, event log |
| BB-07 | Workflow execution | Configures/invokes | Configures/invokes | Configures/invokes | Bundle workspace root and managed resource metadata |
| BB-08 | Docker and unit tests | Not deployed | Not deployed | Not deployed | Git repository and CI logs |

<a id="deployment-dev"></a>
### Development target

- [databricks.yml](../databricks.yml) selects development mode and a current-user
  workspace root.
- Pushes to `main` take the dev path in the [deployment workflow](../.github/workflows/deploy.yml):
  test, validate/plan, deploy, grants, optional sample upload and Job run, then
  saved-query publication.
- The workflow binds a GitHub `dev` environment, but catalog, schema, volume,
  checkpoint, and source isolation depends on supplied values. Bundle defaults
  alone do not create separate dev data/state names.
- Development mode can transform managed-resource display names, while the
  workflow passes an exact expected SQL warehouse name to both post-deploy
  helpers. The effective name-to-ID resolution therefore needs plan or runtime
  evidence.

<a id="deployment-prod"></a>
### Production target

- In the repository's GitHub Actions path, production mode uses a shared
  workspace root and is reached only through manual `workflow_dispatch` with
  target `prod`. An independently authorized CLI user can also select the bundle
  `prod` target directly.
- Jobs bind the GitHub `prod` environment. Required human approval exists only
  if that environment is configured with reviewers outside this repository.
- Prod receives the same default catalog, schema, volume, source, checkpoint,
  and schema-location values as dev unless environment variables override them.
  Effective target isolation must therefore be inspected before deployment.
- The Lakeflow resource fixes `development: true` independently of the bundle
  target, so a prod bundle does not currently select pipeline production mode.

No deployment was executed to produce this architecture document.

<a id="section-8"></a>
## 8. Cross-cutting Concepts

This section follows [arc42 section 8](https://docs.arc42.org/section-8/).

| Concept | Repository-wide rule or current implementation | Important limitation |
| --- | --- | --- |
| Data contracts and grain | Bronze has an ordered string schema; Silver uses `event_id`; Gold and warehouse notebooks declare grouping keys and hash keys in code | Contracts are distributed across notebooks, tests, SQL, and grant lists rather than represented once |
| Nulls, invalid data, and deduplication | Required-key failures go to quarantine; trusted duplicates use an ingestion-order window; dimensions use `dropDuplicates` | Quarantine reconciliation is incomplete and machine dimension selection is not deterministic when attributes change |
| Lineage and replay | Bronze adds ingestion timestamp/source filename; Auto Loader persists checkpoint and schema state outside Delta | Re-uploading an overwritten filename against retained checkpoint state has no demonstrated replay contract |
| Publication and recovery | Individual Delta writes use overwrite; quality history appends | Multi-table groups are not atomic or version-manifested, and restoration order is undocumented |
| Environment parameterization | Catalog, schema, paths, compute, groups, and identities are bundle variables | Dev/prod defaults overlap for data and state; development naming may affect exact resource lookups; pipeline development mode is fixed for both targets |
| Security and trust | GitHub secrets feed a service principal; direct ADLS can retrieve a secret-scope value; UC and object permissions are code-described | Runtime identity is not explicitly separated from deployment identity; editable owner-run saved queries require trust-model review |
| Observability | Imperative current/history tables and a Lakeflow event log expose quality information; Jobs expose task status | Alert routing/ownership is not code-defined, and some failures occur before durable quality evidence |
| Cost and capacity | One-worker Spark clusters, one 2X-Small SQL warehouse with auto-stop, paused default schedule, and non-continuous pipeline constrain the demo | Full-overwrite recomputation, manual runs, external polling, and target overrides have no explicit cost ceiling |
| Evidence boundaries | Unit tests and source contracts run locally and in Docker | Passing local checks does not demonstrate Spark semantics, deployed permissions, replay, recovery, or effective cost |

<a id="section-9"></a>
## 9. Architecture Decisions

This section follows [arc42 section 9](https://docs.arc42.org/section-9/).

**Status warning:** the following decisions are observed or implied by current
source. They are **not accepted ADRs** and do not record human approval. A future
ADR should add context, alternatives, rationale, owner, status, and consequences.

| ID | Observed or implied decision | Status | Evidence | Consequence or open question |
| --- | --- | --- | --- | --- |
| AD-01 | Use Databricks managed data and orchestration services with Asset Bundles | Observed/implied; not an accepted ADR | [Bundle](../databricks.yml) and [resources](../resources/) | Keeps the demo cohesive but couples execution evidence to a workspace |
| AD-02 | Use Bronze, Silver, Gold, then a dimensional warehouse | Observed/implied; not an accepted ADR | [Architecture overview](architecture.md) and notebooks | Separates concerns; publication consistency across layers is not transactional |
| AD-03 | Run Auto Loader as scheduled incremental `availableNow` work | Observed/implied; not an accepted ADR | [Bronze notebook](../notebooks/01_bronze_ingest.py) | Bounds a batch run but makes checkpoint/replay semantics part of correctness |
| AD-04 | Overwrite curated and serving tables on each run | Observed/implied; not an accepted ADR | [Silver](../notebooks/02_silver_transform.py), [Gold](../notebooks/03_gold_models.py), [warehouse](../notebooks/07_warehouse_model.py), and [forecast](../notebooks/05_forecast_validation.py) | Simple rebuilds; exposes mixed-version and history-loss risks |
| AD-05 | Derive warehouse surrogate keys with `xxhash64` | Observed/implied; not an accepted ADR | [Warehouse notebook](../notebooks/07_warehouse_model.py) | Stable for identical inputs; key grain, collision handling, and unknown members need explicit policy |
| AD-06 | Use a rolling mean as the forecasting baseline | Observed/implied; not an accepted ADR | [Forecast notebook](../notebooks/05_forecast_validation.py) | Explainable and inexpensive; readiness and time-window semantics need stronger definitions |
| AD-07 | Combine an imperative gate with monitor-style declarative expectations | Observed/implied; not an accepted ADR | [Quality checks](../notebooks/04_quality_checks.py) and [expectations](../notebooks/06_lakeflow_quality_expectations.py) | Offers two evidence surfaces; enforcement, ownership, and warehouse coverage are inconsistent |
| AD-08 | Publish repository SQL as owner-run Databricks SQL Queries | Observed/implied; not an accepted ADR | [Query publisher](../scripts/upsert_reporting_queries.py) | Gives governed saved assets; edit/run permissions cross a trust boundary that needs acceptance |

<a id="section-10"></a>
## 10. Quality Requirements

This section follows [arc42 section 10](https://docs.arc42.org/section-10/).
Targets below are acceptance scenarios; “gap” means current source does not yet
provide the evidence and is not a claim that the target passes.

| ID | Quality | Stimulus and environment | Measure or acceptance threshold | Current evidence or gap |
| --- | --- | --- | --- | --- |
| QS-01 | <a id="quality-contract"></a>Input-contract correctness | A base or incremental fixture is proposed in a local or CI checkout | 100% of CSV headers equal the ordered machine-event contract; every required key and timestamp is valid; event IDs do not collide across fixtures | Existing fixture tests cover headers, timestamps, and selected value constraints; Spark ingestion is not executed locally |
| QS-02 | <a id="quality-trusted"></a>Trusted-event integrity | A workflow run contains duplicates and rows with missing required keys | Silver contains 0 duplicate `event_id` values and 0 missing required keys; every excluded required-key row is accounted for in quarantine | Silver and imperative checks cover uniqueness/required fields; complete Bronze-to-Silver/quarantine reconciliation is absent |
| QS-03 | <a id="quality-reconciliation"></a>Warehouse reconciliation | Gold and warehouse publication completes | Gold uptime count equals uptime fact count; Gold failure count equals failure fact count; dimension keys are unique; facts have 0 null or unmatched foreign keys | No warehouse reconciliation gate currently exists |
| QS-04 | <a id="quality-durable-failure"></a>Durable failure evidence | Any error-level quality dependency or rule fails | Current and history result rows for the run are durably written before the task raises; the Job then prevents forecast execution | Normal rule failures persist first, but an unreadable Silver/Gold dependency can raise before writes |
| QS-05 | <a id="quality-replay"></a>Replayability | The same logical file is delivered twice, corrected under the same name, or backfilled late | The documented policy produces exactly the intended trusted events with 0 unexplained duplicates or omissions in repeated tests | Checkpoint paths and source lineage exist; repeated-name and backfill behavior is untested |
| QS-06 | <a id="quality-isolation"></a>Environment isolation | Dev and prod configurations are rendered together | There are 0 shared fully qualified writable tables, volumes, checkpoint paths, schema locations, or source mutation paths unless explicitly approved | Current defaults overlap; effective rendered isolation is not tested |
| QS-07 | <a id="quality-forecast"></a>Forecast fitness and explainability | A segment receives a client-facing readiness status | Status requires the configured sample threshold plus an accepted accuracy/coverage threshold; each published forecast retains reproducible validation inputs or vintage | Output includes metrics, but status uses sample count only and tables overwrite prior vintages |
| QS-08 | <a id="quality-bounded-operations"></a>Bounded operation and cost | A deployment API or compute task stalls or is repeatedly invoked | Every poll and Job has a declared deadline; schedules and clusters have owners and an accepted run/storage cost ceiling; timeout failure is observable | Schedule is paused and compute is small, but grant polling has no overall timeout and no explicit cost ceiling is recorded |

<a id="section-11"></a>
## 11. Risks and Technical Debt

This section follows [arc42 section 11](https://docs.arc42.org/section-11/).
Priorities are architecture-review judgments based on current-main source, not
copied risk dispositions and not proof that a deployed failure has occurred.

| ID | Priority | Category | Current-main evidence | Impact | Concrete mitigation/evidence needed |
| --- | --- | --- | --- | --- | --- |
| AR-01 | Critical | Environment isolation | [Bundle targets](../databricks.yml) share default catalog, schema, volume, source, checkpoint, and schema-location values while notebooks overwrite outputs | Dev can read or replace prod data/state if effective values are not separated | Define target-specific writable names and prove rendered dev/prod plans have zero unapproved overlap |
| AR-02 | High | Resource resolution | [Bundle targets](../databricks.yml) select development mode; the [deployment workflow](../.github/workflows/deploy.yml) passes an exact warehouse name, and both [grant](../scripts/apply_uc_grants.py) and [query](../scripts/upsert_reporting_queries.py) helpers require exact name equality | If the effective development display name differs, post-deploy grant and query publication fail after bundle resources have already changed | Resolve IDs from bundle outputs or explicitly configured effective names; prove both dev helper paths against a rendered plan and deployed resource |
| AR-03 | High | Pipeline configuration | The [pipeline resource](../resources/lakehouse_quality_expectations.yml) selects `CORE` and fixes `development: true`, while [notebook 06](../notebooks/06_lakeflow_quality_expectations.py) declares expectations; official [Databricks configuration guidance](https://docs.databricks.com/aws/en/ldp/configure-pipeline) requires `ADVANCED` for expectations and reports an error for unsupported features | The final workflow task can fail instead of publishing expectation evidence, and prod does not select pipeline production mode | Select the supported edition, make development mode target-specific, inspect rendered dev/prod plans, and attach a successful validation and refresh |
| AR-04 | High | Warehouse integrity | [Warehouse construction](../notebooks/07_warehouse_model.py) uses inner joins, machine-only nondeterministic deduplication, and hash keys whose natural grain is not documented | Rows can be silently lost, multiplied, or merged into an incorrect member | Define grains and unknown-member policy; add executable uniqueness, foreign-key, and Gold-to-fact reconciliation |
| AR-05 | High | Publication recovery | [Silver](../notebooks/02_silver_transform.py), [Gold](../notebooks/03_gold_models.py), [warehouse](../notebooks/07_warehouse_model.py), and [forecast](../notebooks/05_forecast_validation.py) overwrite table groups sequentially | An interrupted run can expose a mixture of versions and erase forecast history | Add run manifests or versioned/atomic publication boundaries and prove ordered recovery from an interrupted run |
| AR-06 | High | Quality observability | [Imperative checks](../notebooks/04_quality_checks.py) omit warehouse tables and can directly read dependencies after recording an existence failure | A bad serving model can pass the gate, while some failures leave no durable quality result | Add warehouse/reconciliation gates and a persist-before-fail path tested for missing dependencies |
| AR-07 | High | Replay | [Deployment](../.github/workflows/deploy.yml) overwrites a fixed sample filename while [Bronze](../notebooks/01_bronze_ingest.py) retains Auto Loader checkpoint state | Corrections or repeated demonstrations can be skipped or misunderstood | Adopt immutable object names or a controlled reset/backfill procedure and demonstrate repeated upload behavior |
| AR-08 | High | Forecast semantics | [Forecast validation](../notebooks/05_forecast_validation.py) uses row windows named as days, validates from sample count, and overwrites prior forecasts | Consumers can overstate readiness or cannot reconstruct what was previously issued | Define calendar and accuracy semantics, retain vintages, and execute thresholded backtests |
| AR-09 | High | Security boundary | [Saved-query publication](../scripts/upsert_reporting_queries.py) uses owner-run mode while engineers receive edit permission; deployment/runtime identity separation is not explicit | An editor may alter a query that executes with a more privileged owner context | Validate the trust model, separate identities/ownership where needed, and test allowed and denied actions |
| AR-10 | Medium | Delivery resilience | [Grant automation](../scripts/apply_uc_grants.py) polls without an overall deadline; the deploy workflow consumes a mutable CLI setup action reference | Runs can hang and supply-chain behavior can change without repository review | Add overall deadlines/job timeouts, pin external inputs, and test timeout/error paths |
| AR-11 | Medium | Assurance debt | [Tests](../tests/) mainly validate fixtures and source contracts; [local checks](../scripts/run_local_checks.sh) do not execute Spark | Green CI can be mistaken for runtime/data correctness evidence | Add executable transformation/helper tests and preserve an explicit distinction between local, integration, and deployed evidence |
| AR-12 | Medium | Decision debt | Current architecture choices are described in prose and code but no accepted ADR set records ownership or rationale | Future changes can repeat debates or unknowingly violate an assumption | Create human-reviewed ADRs for state isolation, publication, replay, key grain, quality enforcement, and query ownership |

Risks are closed only by a reviewed decision plus reproducible evidence; editing
this document alone does not close them.

<a id="section-12"></a>
## 12. Glossary

This section follows [arc42 section 12](https://docs.arc42.org/section-12/).

| Term | Meaning in this repository |
| --- | --- |
| ADLS Gen2 | Azure Data Lake Storage Gen2, an optional direct source/state backend for Bronze ingestion |
| Asset Bundle | Databricks configuration packaging notebooks, variables, targets, Jobs, pipelines, warehouses, and permissions |
| Auto Loader | Databricks incremental file ingestion used by BB-01 |
| Bronze | Source-shaped event records plus ingestion lineage |
| Delta | Table/storage interface between data-plane building blocks |
| Gold | BI-oriented aggregates derived from trusted Silver events |
| Grain | The business-level uniqueness represented by one row in a table |
| Lakeflow pipeline | Managed declarative pipeline that materializes expectation-backed quality views |
| Replay | Deliberately processing previously seen or corrected source input again |
| Silver | Typed, normalized, deduplicated trusted events and a separate invalid-row quarantine |
| Unity Catalog | Governed catalog, schema, volume, table, and permission surface |
| Warehouse | Dimensional facts and dimensions, distinct from the Databricks SQL compute warehouse |
