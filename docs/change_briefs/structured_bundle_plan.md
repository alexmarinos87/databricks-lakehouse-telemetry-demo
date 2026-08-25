# Change Brief: Capture structured Databricks bundle plans

## Problem

The deployment workflow retained `databricks bundle plan` as human-readable text.
That was useful for manual inspection, but it was not a stable machine-readable
artifact and could not become the exact input to a later apply operation.

The bundle also did not state the deployment engine or constrain the Databricks
CLI version. As Databricks transitions from the Terraform engine to the direct
engine, an unbounded CLI and implicit engine selection make plan evidence harder
to interpret and reproduce.

## Outcome

- Pin the bundle to the direct deployment engine.
- Require Databricks CLI 1.3.x or a later compatible 1.x release.
- Request JSON output from `databricks bundle plan`.
- Validate that successful plan output is a JSON object before publishing it.
- Retain the exact provider bytes as `bundle-plan.json`.
- Record the plan format and SHA-256 digest in `evidence.json`.
- Continue storing only bounded byte counts and hashes when plan generation
  fails.

## Observable contract

A successful plan-mode evidence directory contains:

```text
bundle-validate.txt
bundle-plan.json
evidence.json
summary.md
```

`evidence.json` identifies `bundle-plan.json` as JSON and contains the digest of
the exact bytes in the file. Invalid JSON, a non-object top-level value, or output
larger than the bounded capture limit fails the plan stage and does not publish a
plan file.

## Compatibility boundary

The direct engine and replayable JSON plan support require a modern Databricks
CLI. The bundle therefore declares:

```yaml
bundle:
  engine: direct
  databricks_cli_version: '>= 1.3.0, < 2.0.0'
```

If a target was previously deployed using the Terraform engine, its deployment
state must be migrated through a separately reviewed Databricks operation before
an apply. This repository change does not run `bundle deployment migrate` and
does not claim that any existing workspace state has been migrated.

## Security boundary

The JSON plan can contain workspace resource identifiers and configuration
details. It remains inside the exact-commit workflow artifact with the existing
14-day retention boundary. The GitHub step summary publishes only status,
filenames, formats, hashes, and identity fingerprints.

Failed provider output is represented by byte counts and hashes rather than raw
diagnostics. Static client-secret authentication remains prohibited.

## Non-goals

This increment does not:

- deploy a bundle;
- replay the captured plan during apply;
- migrate Terraform deployment state;
- authenticate to a real workspace;
- upload data;
- run a lakehouse workflow;
- execute SQL or change permissions;
- activate schedules;
- touch production.

Binding an apply job to this exact JSON artifact is a separate dependent
increment.

## Validation

Repository tests cover:

- direct-engine and CLI-version configuration;
- `--output json` command construction;
- exact JSON plan-file retention;
- evidence format and digest metadata;
- invalid JSON and non-object JSON rejection;
- bounded failure sanitization;
- preservation of identity-only mode and existing OIDC checks.
