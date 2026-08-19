# Machine-Event Source Fixture Contract

## Purpose And Boundary

`src/lakehouse_demo/machine_event_contract.py` provides executable, standard-library validation for the synthetic machine-event CSV files committed under `data/`. It checks the repository's source-fixture boundary before those files are uploaded or ingested.

This module does not run in the Databricks workflow and does not read a bronze, silver, gold or warehouse table. It does not prove Auto Loader, Spark casts, streaming checkpoints, Delta writes or Databricks runtime behaviour. Those paths still require proportionate integration evidence in Databricks.

## Building Blocks

```text
bounded committed CSV fixture paths
  -> safe regular-file reader
  -> machine-event field validation
  -> bounded immutable ValidationReport
       -> error_findings
       -> replay_duplicates
       -> is_valid
```

The module reuses `MACHINE_EVENT_COLUMNS` from `azure_ingestion.py`, which is the source-shaped column contract used by bronze ingestion. It has no third-party dependencies and performs no writes.

## Source Contract

Every fixture must have:

- the exact source header, including order;
- at least one data row;
- populated `event_id`, `machine_id`, `event_ts`, `site_id` and `client_id` keys;
- an `event_ts` in the exact `YYYY-MM-DDTHH:MM:SSZ` UTC representation and with a valid calendar value;
- finite decimals for `hour_meter`, `temperature_c`, `vibration_mm_s`, `fuel_level_pct` and `maintenance_cost_gbp`;
- a non-negative `maintenance_cost_gbp`;
- `fuel_level_pct` between 0 and 100 inclusive;
- canonical non-negative Spark `INT` values for `duration_minutes`, `downtime_minutes` and `part_quantity`.

Canonical integer spelling is `0` or an unsigned base-10 value without leading zeroes, a sign, whitespace, a decimal point or exponent. The maximum is `2147483647`, matching the positive limit of the Spark `int` target used by the silver notebook. Examples accepted are `0`, `1` and `2147483647`; examples rejected are `-1`, `+1`, `01`, `1.0`, `1e1`, values with surrounding whitespace and `2147483648`.

The contract deliberately does **not** assert that `downtime_minutes` is less than or equal to `duration_minutes`. The sample data demonstrates that the fields may currently represent different business intervals. A business decision and downstream reconciliation design are required before enforcing that relationship.

## Duplicate Identity

Duplicate identity is evaluated across all files supplied in one call. A later row with the same `event_id` and exactly the same source fields is an informational `replay_duplicate`. A later row with the same `event_id` but any different source field is a `conflicting_duplicate` error. Paths are validated in sorted order, so the first occurrence and finding order are deterministic.

Findings never include candidate headers, row values or raw event IDs. When a row has a usable event ID, the finding carries only its fixed-size SHA-256 digest. Duplicate findings also carry the first source location and line number; path strings longer than 512 characters are replaced by a fixed-size SHA-256 label.

## Filesystem And Resource Bounds

Before reading, each path is inspected with `lstat`. Symbolic links and non-regular files are rejected. Regular files are opened with `O_NOFOLLOW`, `O_NONBLOCK` and `O_NOCTTY` where the platform provides them, then checked with `fstat` before and after the bounded read to detect a non-regular descriptor, descriptor-identity change or file-metadata change. The nonblocking flag prevents a regular-file-to-FIFO path race from hanging validation before the descriptor check.

Validation has explicit ceilings suitable for the small committed fixtures:

| Resource | Maximum |
| --- | ---: |
| Supplied fixture path entries | 100 |
| Bytes per fixture | 2,000,000 |
| Total accepted input bytes | 10,000,000 |
| Total data rows | 100,000 |
| Returned findings | 1,000 |
| Displayed source-path characters | 512 |

The path iterator is stopped after the first entry beyond the file-count ceiling, including repeated entries, and exact duplicate paths within that bounded input are validated once. File bytes are read only after size and identity checks, and reading is capped at the per-file limit plus one byte. Reaching the row or total-size limit, or attempting to exceed the finding limit, stops further validation and returns an explicit error. Diagnostics use fixed messages and schema field names rather than candidate content.

## Finding Codes

| Code | Meaning |
| --- | --- |
| `no_fixtures` | No unique input paths were supplied. |
| `file_count_limit_exceeded` | The input has too many supplied fixture path entries. |
| `symlink_not_allowed` | A fixture path is a symbolic link. |
| `non_regular_file` | A fixture path or opened descriptor is not a regular file. |
| `file_read_error` | A fixture cannot be inspected, opened, read or decoded as UTF-8. |
| `file_identity_changed` | The path identity or file metadata changed during the bounded read. |
| `file_size_limit_exceeded` | One fixture exceeds the per-file byte ceiling. |
| `total_size_limit_exceeded` | Accepted fixture bytes would exceed the total ceiling. |
| `empty_fixture` | A fixture contains no bytes or CSV records. |
| `header_mismatch` | The header differs in name or order. |
| `no_data_rows` | A valid header has no data rows. |
| `row_limit_exceeded` | Data rows exceed the total row ceiling. |
| `row_shape_invalid` | A data row has the wrong field count. |
| `required_key_missing` | A required source key is blank. |
| `utc_timestamp_invalid` | `event_ts` is not the canonical valid UTC representation. |
| `decimal_invalid` | A decimal field is blank, non-numeric or non-finite. |
| `integer_format_invalid` | An integer field does not use canonical non-negative spelling. |
| `integer_range_violation` | An integer field exceeds the Spark `INT` maximum. |
| `non_negative_violation` | Maintenance cost is negative. |
| `fuel_range_violation` | Fuel is outside the inclusive 0–100 range. |
| `conflicting_duplicate` | A repeated event ID has a different payload. |
| `replay_duplicate` | A repeated event ID has an identical payload. |
| `csv_parse_error` | Python's strict CSV parser cannot read a header or row. |
| `finding_limit_exceeded` | Further diagnostics were truncated at the finding ceiling. |

All codes except `replay_duplicate` have error severity. Each finding contains its code, severity, source path, line, schema field, optional fixed-size record-ID digest and fixed diagnostic message.

## Quality Scenarios

The behavior tests prove that:

- all current sample and incremental fixtures have no errors;
- their single identical `E0008` repeat is classified as a replay duplicate without exposing the raw ID;
- absent paths, empty files and header-only files fail explicitly;
- symlinks, non-regular paths, oversized files and excessive aggregate inputs fail before unbounded reads;
- malformed CSV, row-shape drift, missing keys and invalid timestamps fail without echoing candidate content;
- decimal, integer, non-negative and fuel-boundary rules execute against row values;
- identical and conflicting duplicates have different dispositions;
- caller file order does not change the report;
- downtime greater than duration remains allowed pending a business-semantics decision.

Run the repository's normal local checks:

```bash
scripts/run_local_checks.sh
```

## Operations, Cost And Rollback

Validation is local and bounded by the limits above. It uses no credentials, external services, compute clusters, storage writes or production state. The validator retains accepted input text only while processing each file and keeps bounded event fingerprints and findings in memory.

Failure leaves all inputs unchanged and returns structured errors to the caller. Rollback is a Git revert that removes this module, its tests and this document; no data restore, checkpoint reset, permission rollback or backfill is required.
