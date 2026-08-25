# Change Brief: Review authenticated Databricks direct-plan evidence

## Problem

The repository captures an exact `bundle-plan.json` from the Databricks direct
deployment engine and can replay that same artifact during an approved apply.
The remaining source gap is a repeatable decision about the actions contained
inside that structured plan. A reviewer written for Terraform-style text would
not inspect the artifact that the accepted plan and apply workflows actually
use.

## Outcome

Add an offline reviewer that converts the already-captured Databricks direct-engine JSON plan into a deterministic, sanitized accept/block decision.

The reviewer:

- requires direct plan schema version 2;
- validates the bounded top-level plan and per-resource entry shapes;
- accepts only the direct-engine actions `skip`, `resize`, `update`,
  `update_id`, `create`, `recreate`, and `delete`;
- verifies update/recreate entry actions agree with their field-change actions;
- classifies destructive deletes separately from `gone` state-only cleanup;
- applies explicit target limits to creation, change, deletion, recreation,
  cleanup, and permission-sensitive resources;
- detects opposite-target fragments in resource addresses, field paths,
  dependencies, and JSON state values;
- blocks apparently unredacted secret, token, authorization, or password
  values;
- stores only plan hashes, lineage and resource-address fingerprints.

## Inputs

```text
--plan-file       exact captured bundle-plan.json
--policy          repository-owned JSON review policy
--target          dev or prod policy selector
--source-commit   exact 40-character source commit
--output-dir      regular evidence directory
```

The repository policy is:

```text
governance/databricks_plan_review_policy.json
```

It does not permit destructive deletes or recreation in either target. It
permits a bounded number of `gone` delete entries because those represent
state-only cleanup after planning has already established that the remote
resource no longer exists.

## Outputs

```text
databricks-plan-review.json
databricks-plan-review.md
```

The JSON record includes:

- target and source commit;
- plan SHA-256 and byte count;
- direct plan and CLI versions;
- lineage fingerprint, serial and filtered-resource count;
- action and permission-sensitive resource counts;
- hashed resource evidence;
- stable findings;
- final `accepted` or `blocked` status.

It excludes raw resource addresses, lineage, provider IDs, remote state, new
state, field values, and secret-like material.

## Failure model

The command exits:

```text
0  accepted by repository policy
1  structurally valid plan blocked by one or more findings
2  invalid policy, malformed plan, unsupported direct-plan schema,
   inconsistent action classification, or unsafe evidence path
```

Unknown top-level fields, unknown entry fields, unsupported actions, malformed
dependencies, excessive nesting, excessive resources or changes, unsafe
symbolic links, and non-UTF-8 or oversized inputs fail closed.

## Security boundary

The reviewer is standard-library-only and does not invoke the Databricks CLI.
It does not:

- authenticate to GitHub or Databricks;
- request an OIDC token;
- validate, plan, or deploy a bundle;
- upload source data;
- execute a workflow or SQL statement;
- change permissions, checkpoints, alerts, retention, or schedules.

A plan accepted by this source control is still subject to human review, exact
artifact verification, protected-environment approval, and the independent
external governance, federation, and least-privilege gates.

## Compatibility

The accepted bundle pins the direct deployment engine and captures
`databricks bundle plan --output json`. The current direct-engine artifact has:

```text
plan_version
cli_version
lineage
serial
plan
not_selected
```

Each entry may contain:

```text
id
depends_on
action
gone
new_state
remote_state
changes
```

The reviewer requires plan version 2 and fails closed when a future CLI changes
this contract. Supporting a new plan version requires a reviewed policy and
parser change before an apply can use it.

## Validation

Repository tests cover:

- clean create and update plans;
- an empty no-change plan;
- destructive delete, recreate, and state-only cleanup classification;
- target crossover hidden inside structured state;
- permission-sensitive resource limits;
- raw and redacted sensitive values;
- unsupported plan versions, actions, fields, and inconsistent nested actions;
- invalid policies and unknown targets;
- excessive JSON depth;
- invalid source commits;
- sanitized evidence and unsafe output directories.

## Rollback

Source rollback is a normal revert of the squash commit. Existing plan
artifacts remain unchanged. Removing the reviewer must not be interpreted as
permission to apply an unreviewed plan.
