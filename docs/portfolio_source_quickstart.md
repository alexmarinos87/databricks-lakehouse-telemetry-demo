# Portfolio source evidence: no-workspace quickstart

Use this guide from a checkout containing the source-evidence builder. A draft
pull request is a candidate, not a claim that these changes are already on main.
The overview needs Python 3.11 or later and the standard library only. It does not
need Docker, Spark, a Databricks workspace, credentials, or a package installation.
Run commands from the repository root, using caller-controlled source and output
locations. Never substitute private or customer data for the synthetic fixtures.

## Build the overview

```bash
python3 scripts/build_portfolio_snapshot.py --output-dir .review/portfolio-snapshot
```

The output directory must not exist. The [builder](../scripts/build_portfolio_snapshot.py)
reads the sample and optional increments, validates the machine-event contract,
and inventories the reporting manifest's SQL files and selected source documents.
It writes two files:

| File | What to read |
| --- | --- |
| `portfolio-snapshot.md` | Human-readable coverage, replay counts, operational aggregates, reporting catalogue, and evidence limits |
| `portfolio-snapshot.json` | The same source evidence as structured data, including selected-file hashes and the snapshot digest |

With the current fixtures, expect 31 physical rows, 30 unique event IDs, one
identical replay, and 15 reporting assets. These numbers describe the selected
synthetic source, not rows ingested into a workspace. Identical replays do not
inflate the operational totals. SQL files are inventoried, not executed.

The command validates the source contract; it does not run the repository test
suite, Spark, SQL, or Databricks. The JSON therefore retains explicit `not_run`
and `not_verified` fields. A hash identifies bytes; it is not a signature, proof
of a clean Git checkout, or human acceptance.

## Rerun without destroying evidence

A second run with the same directory fails with `output_directory_exists` rather
than overwriting it. Choose another new directory, such as
`.review/portfolio-snapshot-rerun`, and change only `--output-dir`. Identical
selected bytes produce identical snapshot files, regardless of output location.
Do not edit the generated JSON to claim additional validation.

Invalid fixtures, conflicting event IDs, unreadable or malformed increment
directories, changed inputs, and excessive file or numeric representations fail
instead of publishing a successful overview. Correct the source or directory
permissions, then use a fresh output directory. A missing or empty optional
increments directory is supported; an unreadable one is not equivalent to empty.
Inspect uncertain partial output before removing anything manually.

For only the dataset profile, use the [standalone profiler](../scripts/profile_synthetic_dataset.py)
with its own new output directory:

```bash
python3 scripts/profile_synthetic_dataset.py --output-dir .review/dataset-profile
```

## Read the CI version

In the exact pull request's successful CI run, open the **Publish portfolio source
evidence** job and its artifact named `portfolio-source-<tested-checkout>-<run-id>-<attempt>`.
It contains the two snapshot files plus `ci-provenance.json`. Artifacts have a
seven-day lifetime; retain the exact package and its run reference for your review.

The envelope records the candidate head separately from the tested merge commit,
the base, checkout tree, run and attempt, and payload hashes. The
[CI packager](../scripts/prepare_portfolio_ci_artifact.py) checks selected bytes
against Git and reconstructs both snapshot representations. CI then downloads the
artifact and compares it with the producer bytes. Check that this verification
step and the job succeeded: artifact presence alone does not establish success.

CI provenance remains unsigned and source-only. It is not a deployment approval,
evidence of workspace permissions, or proof of a live Databricks run. Do not
combine an old artifact with a newer commit's checks. Refer to the actual run for
test results; do not change the source snapshot's flags to match the CI status.

## Validate and review separately

The [delivery workflow](ai_delivery_workflow.md) explains index-based acceptance,
review packages, and the human decision. The [architecture](architecture.md) and
[reporting manifest](../sql/reporting_assets/manifest.json) explain the data flow
and the inventoried queries. Workspace execution is a separate workflow described
in [setup](setup.md), with its own permissions, cost, and approval boundaries.
