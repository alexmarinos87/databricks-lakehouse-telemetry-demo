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
- `02_silver_transform.py` calls `build_silver_frames` and reconciles Bronze, Silver, quarantine, and replay counts before writing;
- `03_gold_models.py` writes the five DataFrames returned by `build_gold_frames`;
- `07_warehouse_model.py` calls `build_warehouse_frames`, executes `audit_warehouse`, and refuses to publish any warehouse table when findings remain.

Repository contracts enforce these call paths and ensure the warehouse audit appears before the first Delta write. As a result, the DataFrame transformations executed by local Spark CI are the same functions the Databricks workflow invokes before persistence.

## Medallion evidence

`tests_runtime/test_spark_medallion_runtime.py` creates source-shaped Bronze rows with explicit ingestion timestamps and source-file lineage. It then executes the shared DataFrame functions and proves:

1. the Bronze schema is ordered and source-shaped;
2. malformed required keys enter quarantine;
3. accepted, quarantined, and deduplicated rows reconcile to the Bronze count;
4. the latest delivery of one event ID wins deterministically;
5. strings are normalized and operational measures receive Spark types;
6. a late event remains present in Silver and Gold outputs;
7. failure and parts outputs contain the expected event grain;
8. missing lineage input fails closed.

The replay scenario uses a second immutable source-file name with a later ingestion timestamp. It proves transformation-level deduplication. It does **not** prove that Auto Loader reprocesses a corrected file delivered under the same object name and checkpoint.

## Warehouse evidence

`tests_runtime/test_spark_warehouse_runtime.py` executes dimensional warehouse construction and an independent Spark audit over bounded Gold fixtures. It proves:

1. Gold uptime rows reconcile one-for-one to daily uptime facts;
2. Gold failure rows reconcile one-for-one to event-grain failure facts;
3. date, machine, client, site, model, and fault dimensions contain the expected members;
4. fact grains are unique and required dimension keys are non-null;
5. machine assignments are derived from both uptime and failure sources, so a failure-only machine is represented;
6. conflicting client/site/model assignments for one machine fail construction rather than selecting an arbitrary row;
7. removing a dimension member produces an `unmatched_dimension_key` finding;
8. duplicating a fact produces both grain and source-count findings;
9. missing required warehouse datasets and empty uptime input fail closed.

The audit checks aggregate count parity, duplicate grains, null foreign keys, and unmatched foreign keys. Equal source and fact counts do not alone prove row identity; full natural-key reconciliation remains a separate improvement.

## Evidence boundary

Passing this workflow demonstrates executable Spark DataFrame semantics for the shared pure transformations. It does not demonstrate:

- Auto Loader discovery, checkpoint, or schema-location behaviour;
- Delta transaction, overwrite, schema-evolution, or table-history behaviour;
- Unity Catalog permissions, volumes, or effective identities;
- Databricks Asset Bundle rendering or deployment;
- Lakeflow expectations or event-log publication;
- cloud storage, cluster policy, cost, or recovery behaviour.

Authenticated Databricks validation and plan evidence, followed by a deliberately approved development run, remain separate controls.
