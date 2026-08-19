# Warehouse Analytics Contracts

## 1. Introduction And Goals

`src/lakehouse_demo/warehouse_contracts.py` is a small executable contract oracle for the dimensional warehouse. It characterizes assumptions that are easy to obscure in Spark joins and `dropDuplicates` calls:

- one client, site and model assignment per machine;
- one uptime fact per the published `(date_key, machine_key)` grain;
- one failure fact per `event_id`;
- non-null fact foreign keys that match dimension members;
- matching row counts between each gold source and its fact table.

The module is a **characterization and contract oracle**. It does not execute Spark, inspect Delta tables or prove that `notebooks/07_warehouse_model.py` conforms to these contracts. Notebook conformance still needs Databricks integration evidence built from actual source, dimension and fact rows.

## 2. Architecture Constraints

- Inputs are finite iterables of row mappings with scalar business and surrogate keys.
- The implementation uses only the Python standard library.
- It is read-only and has no Databricks, Spark, filesystem or network side effects.
- Findings describe observed violations; they do not repair, deduplicate or publish data.

## 3. System Scope And Context

The caller supplies rows from `gold_machine_uptime`, `gold_failure_events`, both warehouse facts, and the keys present in each dimension. The oracle returns an immutable tuple of findings for a test, reconciliation job or review artifact to interpret.

```text
gold uptime rows -----+                         +--> structured findings
gold failure rows ----+--> contract evaluator --+    (no writes)
uptime fact rows -----+
failure fact rows ----+
dimension key members-+
```

Dimension membership is keyed by fact foreign-key column name: `date_key`, `client_key`, `machine_key`, `model_key`, `site_key` and `fault_key`. An omitted member collection is treated as empty, so validation fails closed for facts that reference it.

## 4. Solution Strategy

The public API consists of the frozen `WarehouseContractFinding` record and the keyword-only `evaluate_warehouse_contracts` function. Inputs are materialized once so generators are supported and row counts are stable. Repeated dimension-reference violations and duplicate grains are aggregated with a `row_count` detail; missing/null grain findings remain row-level so the incomplete row shape is visible.

Findings use these codes:

| Code | Meaning |
| --- | --- |
| `machine_assignment_conflict` | A machine has more than one distinct `(client_id, site_id, model)` assignment. |
| `missing_grain_key` | A required fact-grain column is absent. |
| `null_grain_key` | A required fact-grain column is explicitly null. |
| `duplicate_uptime_grain` | More than one uptime fact has the same `(date_key, machine_key)`. |
| `duplicate_failure_grain` | More than one failure fact has the same `event_id`. |
| `missing_dimension_key` | A required foreign-key column is absent from a fact row. |
| `null_dimension_key` | A required fact foreign key is `None`. |
| `unmatched_dimension_key` | A non-null fact foreign key is absent from its dimension members. |
| `source_fact_count_mismatch` | A fact and its corresponding Gold source have different row counts; details expose only the net missing or unexpected count delta. |

Machine assignments are checked across both source collections because the shared machine dimension is used by both facts. A historical reassignment is therefore deliberately visible: the current warehouse is not modelled as a slowly changing dimension.

## 5. Building Block View

```python
from lakehouse_demo.warehouse_contracts import evaluate_warehouse_contracts

findings = evaluate_warehouse_contracts(
    uptime_source_rows=gold_uptime_rows,
    failure_source_rows=gold_failure_rows,
    uptime_fact_rows=uptime_fact_rows,
    failure_fact_rows=failure_fact_rows,
    dimension_members={
        "date_key": date_keys,
        "client_key": client_keys,
        "machine_key": machine_keys,
        "model_key": model_keys,
        "site_key": site_keys,
        "fault_key": fault_keys,
    },
)
```

Each finding contains a stable `code`, affected `dataset`, identifying `keys`, and structured `details`. Findings are sorted by check, dataset and scalar key representation, so reversing otherwise-equivalent input rows produces the same result.

## 6. Runtime View

Evaluation performs in-memory passes over the supplied rows. Grain and assignment checks group typed scalar keys; dimension members are pre-indexed as typed tokens before fact lookups. The result is empty only when none of the characterized violations is observed.

This implementation is intended for bounded fixtures and sampled reconciliation extracts, not full production tables. Inputs should use scalar values representative of the warehouse schema. A Spark-native gate is required for complete table-scale validation.

## 7. Deployment View

This is a source module, not a separately deployed service. Local tests import it from `src/`; a future Databricks reconciliation task may package or copy the same module through the repository's normal deployment path. This change adds no job, cluster, table, permission or schedule.

## 8. Cross-Cutting Concepts

- **Determinism:** assignments and findings are ordered by check, dataset and typed scalar key representation rather than input order.
- **Fail-closed membership:** an omitted dimension-member collection behaves like an empty dimension.
- **Bounded side effects:** evaluation materializes finite inputs in memory and returns values without logging or writing.
- **Traceability:** finding keys retain the fact grain or machine ID, while details retain counts or conflicting assignments.

## 9. Architecture Decisions

The oracle is intentionally separate from the Spark notebook. A standard-library implementation provides fast local feedback and makes edge cases easy to express, while the explicit limitation against claiming notebook conformance prevents local characterization from being mistaken for runtime evidence.

The API returns data rather than raising on contract violations. This allows tests and future reconciliation callers to assess all findings in one pass and choose their own severity or alerting policy.

## 10. Quality Requirements

The executable tests require valid fixtures to return an empty tuple, conflicting assignments and grain duplication to identify their business keys, missing/null/unmatched dimensions to remain distinguishable, source-to-fact loss to retain both counts, and equivalent inputs to produce identical ordering.

## 11. Risks And Technical Debt

- Equal row counts can hide an offsetting dropped row and join fan-out or a substituted identity; duplicate-grain findings reduce but do not eliminate that risk. Natural-to-surrogate identity reconciliation remains a Databricks integration responsibility.
- Python equality and Spark equality can differ for unusual values. Inputs should use the scalar key types produced by the warehouse.
- Conflicts expose the current type-1 machine-dimension assumption; they do not decide whether a reassignment is bad data or requires slowly changing dimensions.
- This oracle does not validate measure calculations, hash-key parity, Delta write behavior, permissions, runtime cost or deployment behavior.
- Integration evidence should extract representative rows from Databricks and compare the oracle findings with explicit expected outcomes.

## 12. Rollback

The module has no state or external side effects. Rollback is removal of the module, its unit tests and this document.
