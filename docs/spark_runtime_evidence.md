# Local Spark Runtime Evidence

The standard repository suite validates Python syntax, source contracts, review evidence, and committed fixtures without installing Spark. The separate `Spark Runtime` workflow executes the shared transformation functions in a real local Apache Spark process.

## Runtime alignment

The runtime image uses:

- Python 3.11;
- Apache PySpark 3.5.0;
- Java 17;
- UTC Spark session semantics;
- a pinned Python Bookworm image digest;
- hash-verified Python packages.

This matches the repository's Databricks Runtime 15.4 LTS Spark major/minor line more closely than source-text inspection alone. It is still local open-source Spark rather than a Databricks cluster.

## Notebook wiring

The Databricks notebooks own platform orchestration and persistence, but no longer maintain independent copies of the transformation logic:

- `01_bronze_ingest.py` obtains the ordered source schema from `raw_machine_event_schema`;
- `02_silver_transform.py` calls `build_silver_frames` and reconciles Bronze, Silver, quarantine, identical replay, and conflicting-payload counts before writing;
- `03_gold_models.py` writes the five DataFrames returned by `build_gold_frames`;
- `04_quality_checks.py` calls `evaluate_quality_tables`, persists append-only detailed and run-level evidence, and only then raises on error-level findings;
- `07_warehouse_model.py` calls `build_warehouse_frames`, executes `audit_warehouse_publication`, and refuses to publish any warehouse table when aggregate, referential, natural-identity, or measure-level findings remain.

Repository contracts enforce these call paths. Silver writes quarantine evidence before evaluating the conflict gate, and writes the trusted Silver table only when no conflicting payloads share an event ID. Warehouse publication auditing likewise occurs before the first warehouse Delta write. Quality checks append their detailed result rows and run summary before the deliberate failure gate. The DataFrame transformations and quality checks executed by local Spark CI are therefore the same functions the Databricks workflow invokes before persistence.

## Medallion evidence

`tests_runtime/test_spark_medallion_runtime.py` creates source-shaped Bronze rows with explicit ingestion timestamps and source-file lineage. It then executes the shared DataFrame functions and proves:

1. the Bronze schema is ordered and source-shaped;
2. malformed required keys enter quarantine;
3. accepted, quarantined, and identical replay rows reconcile to the Bronze count;
4. identical source payloads sharing an event ID are treated as replay, with the latest delivery selected deterministically;
5. exact source payloads receive a bounded SHA-256 evidence digest without storing the internal comparison JSON;
6. different source payloads sharing an event ID are all quarantined and no arbitrary winner enters Silver;
7. strings are normalized and operational measures receive Spark types;
8. a late event remains present in Silver and Gold outputs;
9. failure and parts outputs contain the expected event grain;
10. missing lineage input fails closed.

Replay and conflict classification compares the exact ordered source payload, excluding ingestion timestamp and source-file lineage. The digest is evidence only; conflict classification uses the exact payload representation rather than relying on hash equality.

The replay scenario uses a second immutable source-file name with a later ingestion timestamp. It proves transformation-level replay handling. It does **not** prove that Auto Loader reprocesses a corrected file delivered under the same object name and checkpoint.

## Warehouse evidence

`tests_runtime/test_spark_warehouse_runtime.py` executes dimensional warehouse construction and the aggregate/referential audit over bounded Gold fixtures. It proves:

1. Gold uptime rows reconcile one-for-one to daily uptime facts;
2. Gold failure rows reconcile one-for-one to event-grain failure facts;
3. date, machine, client, site, model, and fault dimensions contain the expected members;
4. fact grains are unique and required dimension keys are non-null;
5. machine assignments are derived from both uptime and failure sources, so a failure-only machine is represented;
6. conflicting client/site/model assignments for one machine fail construction rather than selecting an arbitrary row;
7. removing a dimension member produces an `unmatched_dimension_key` finding;
8. duplicating a fact produces both grain and source-count findings;
9. missing required warehouse datasets and empty uptime input fail closed.

`tests_runtime/test_spark_warehouse_identity_runtime.py` executes the composite publication audit and focused identity/measure checks. It proves:

1. the clean warehouse passes count, grain, referential, natural-identity, and measure-level reconciliation;
2. machine, client, site, model, fault, and date identities are reconstructed through their dimension keys rather than trusted from duplicate fact attributes;
3. replacing an uptime fact's machine key with a different but valid dimension key is detected even when counts, fact-grain uniqueness, and foreign-key membership remain valid;
4. replacing a valid `date_key` with another valid date member produces missing and unexpected identity findings;
5. replacing a failure fact event ID is detected even when source and fact counts remain equal;
6. changing the redundant fact `event_date` while retaining the correct date dimension key produces a measure mismatch;
7. direct minute drift and derived percentage drift are reported by dataset and column;
8. failure count, maintenance cost, part quantity, and nullable sensor drift are detected with exact null-safe comparisons;
9. findings contain only code, dataset/column, and count rather than source values.

The measure audit joins Gold rows to fact rows through reconstructed identities. It compares the direct uptime and failure values written into the facts and independently derives idle, downtime, and maintenance percentages from Gold minutes. All measure mismatch counts for one fact family are materialized in a single Spark aggregation.

The composite publication audit therefore covers source/fact count parity, duplicate fact grains, null foreign keys, unmatched foreign keys, exact natural-identity set membership, redundant fact-date consistency, and direct or derived measure equality.

## Quality evidence

`tests_runtime/test_spark_quality_runtime.py` executes the shared medallion and warehouse quality evaluator and proves:

1. required table readability and population outcomes are represented as bounded result rows;
2. an unavailable table creates durable evidence without attempting dependent DataFrame checks or persisting provider diagnostics;
3. Silver event-ID uniqueness, required fields, and technical metric bounds are executable;
4. warehouse uptime and failure fact grains and required dimension keys are executable quality gates;
5. uptime percentage bounds and status-minute partitioning are enforced independently of the publication audit;
6. failure count, downtime, maintenance cost, and part quantity technical invariants are enforced;
7. downtime above observed duration remains an explicit warning while the business definition is unresolved, rather than being silently accepted or incorrectly promoted to an approved hard rule;
8. detailed and summary DataFrames share one `quality_run_id` and `checked_at`, with separate error and warning failure counts.

`04_quality_checks.py` resolves every required medallion, Gold, dimension, and fact table, but does not let one missing table abort evidence construction. It appends `quality_check_results`, then appends `quality_metric_history`, and only afterwards fails the workflow when `failed_error_check_count` is non-zero. Detailed findings contain logical check names, status, severity, bounded detail, and counts; raw exception text and row values are not persisted.

This is append-only evidence at the notebook boundary, not a cross-table transaction. A failure while writing the evidence tables themselves can still prevent complete persistence, and an authenticated Databricks run is required to prove Delta schema evolution and append behaviour.

## Evidence boundary

Passing this workflow demonstrates executable Spark DataFrame semantics for the shared pure transformations. It does not demonstrate:

- Auto Loader discovery, checkpoint, or schema-location behaviour;
- Delta transaction, overwrite, schema-evolution, or table-history behaviour;
- Unity Catalog permissions, volumes, or effective identities;
- Databricks Asset Bundle rendering or deployment;
- Lakeflow expectations or event-log publication;
- cloud storage, cluster policy, cost, or recovery behaviour;
- that the business meaning or acceptable bounds of downtime, observed duration, or percentages have been approved.

Measure equality proves that the warehouse represents the current Gold semantics. It does not by itself prove that those semantics are the correct business definition; for example, the repository still permits downtime to exceed observed duration until that rule is decided.

Authenticated Databricks validation and plan evidence, followed by a deliberately approved development run, remain separate controls.
