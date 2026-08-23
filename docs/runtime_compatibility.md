# Runtime compatibility programme

## Current baseline

The repository treats runtime compatibility as one coordinated set:

```text
Python              3.11
Java                17
PySpark             3.5.0
Py4J                 0.10.9.7
Databricks Runtime  15.4.x-scala2.12
GitHub runner        ubuntu-24.04
```

The machine-readable source is
`governance/runtime_compatibility.json`. The validator
`scripts/validate_runtime_compatibility.py` resolves the versions from the
Dockerfiles, hashed Spark requirements, bundle, and Spark workflow and emits an
exact evidence fingerprint.

This is a repository-tested baseline, not a general statement that every
workspace, cloud, connector, or library combination is supported.

## Why upgrades are coordinated

Python, Java, PySpark, Py4J, Databricks Runtime, Delta APIs, and Lakeflow APIs
interact. Updating only one major component can produce a locally installable
but operationally invalid combination. Dependabot proposals for Python 3.14,
PySpark 4.x, or a standalone Py4J change therefore remain blocked until one PR
proposes and validates a complete matrix.

## Upgrade gate

A candidate upgrade must provide:

1. Official support evidence for the proposed Python, Java, Spark, Py4J, and
   Databricks Runtime combination.
2. Immutable Docker image digests and fully hashed Python dependencies.
3. All standard repository tests.
4. All executable Spark tests, including ingestion, medallion, warehouse,
   quality, forecast readiness, and publication recovery.
5. Databricks bundle validation and plan evidence from development.
6. A controlled development runtime execution using synthetic data.
7. API review for DataFrame, Delta, Lakeflow, and bundle behavior changes.
8. Before/after runtime and resource measurements for the representative Spark
   suite.
9. A rollback image digest, prior requirements file, and prior DBR value.
10. A production decision made only after development evidence is accepted.

A candidate remains `blocked_pending_complete_matrix` until every required item
is retained. Passing local installation is not enough.

## Prohibited partial changes

The policy fails closed on:

- a partial major upgrade;
- a floating runtime version;
- an unhashed Spark dependency;
- a merge without exact matrix evidence;
- a production upgrade before development runtime evidence.

Do not merge a standalone Py4J bump merely because it is smaller. Py4J is part
of the Spark compatibility set.

## Performance evidence

Use the same fixture sizes, CPU and memory limits, Spark partitions, and test
selection before and after an upgrade. Record at least:

```text
image build duration
Spark startup duration
full runtime-suite duration
peak container memory
failed/retried test count
output schema differences
```

A performance regression is not automatically a blocker, but it must be
explained and accepted rather than hidden by changing the benchmark workload.

## Rollback

A runtime upgrade rollback must restore the complete prior set:

```text
Docker image digests
Python minor version
Java major version
PySpark and Py4J hashes
Databricks Runtime value
workflow runner when changed
```

After rollback, rerun the standard and Spark suites and capture a new plan for
the restored Databricks target. Do not mix one component from the failed
candidate with the accepted baseline unless a new complete matrix is reviewed.

## Evidence boundary

The repository validator catches drift from the accepted files and records
hashes. It does not query vendor support matrices or execute Databricks. Current
vendor support and development-runtime evidence must be collected when a real
upgrade candidate is opened.
