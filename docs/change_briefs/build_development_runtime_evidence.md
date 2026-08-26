# Change Brief: Build protected controlled-development runtime evidence

## Problem

The accepted runtime verifier can admit a sanitized manifest for one controlled
Databricks development execution, but operators previously had to place more than
thirty SHA-256 values into that manifest manually. A digest could be copied from a
different run, calculated from the wrong file, or supplied without checking the
protected artifact.

The repository needs an offline package builder that binds approval, plan,
execution, publication, quality, query, grant and rollback records to protected
files without authenticating to Databricks or executing another run.

## Outcome

Add:

```bash
python3 scripts/build_development_runtime_evidence.py \
  --metadata .bootstrap/evidence/dev/runtime-metadata.json \
  --artifact-root .bootstrap/protected/dev/runtime \
  --output-dir .bootstrap/evidence/dev/runtime-package
```

The command writes one new output directory containing:

```text
development-runtime-evidence.json
development-runtime-evidence-summary.md
development-runtime-verification.json
development-runtime-verification.md
```

The output directory must not already exist. The package is assembled under a
private staging directory and the complete directory is published with one atomic
rename. A failure cannot overwrite an earlier package or leave a partial public
package.

The command performs no deployment, job, pipeline, SQL, permission, checkpoint,
alert, retention, scheduler or production operation.

## Protected artifact registry

The metadata has the accepted runtime-manifest structure plus one
`protected_artifacts` registry. Every registry entry contains:

```text
artifact_id
path
expected_sha256
```

Digest fields in the verifier manifest are represented in metadata by explicit
artifact references. The builder binds protected evidence for:

- human apply approval;
- the accepted bundle plan and plan review;
- the execution-level record;
- all nine evidence families;
- all sixteen mandatory assertions;
- rollback execution and recovery point.

Each registered path is unique, every reference must resolve, and unused registry
entries fail closed.

## Evidence-role separation

A protected file cannot satisfy unrelated proof roles merely because several
metadata fields reference the same artifact ID.

The following anchor records must all be distinct and cannot be reused by a
family or assertion:

```text
apply approval
accepted plan
accepted plan review
execution record
rollback record
recovery point
```

Each runtime evidence family must also have its own protected record. Assertions
may share a record only with other assertions for the same governed family, or
with that same family's evidence record. Cross-family sharing is rejected.

This preserves legitimate reuse, such as one quality report supporting several
quality assertions, without allowing one generic file to stand in for approval,
plan, execution, grants and rollback evidence simultaneously.

## Exact package completeness

The builder requires exactly the accepted families:

```text
bronze
silver
gold
forecast
warehouse
quality
expectations
queries
grants
```

It also requires exactly the verifier's sixteen assertion IDs. Missing,
duplicate or unsupported family and assertion descriptors are invalid package
metadata rather than a partial successful package.

## Path and digest safety

Protected paths must be relative to the supplied root. The builder rejects:

- absolute paths and `..` traversal;
- non-canonical aliases such as repeated separators, `.` components or trailing
  slashes;
- backslashes and control characters;
- symbolic-link roots, path components and files;
- non-regular, empty or oversized files;
- duplicate artifact IDs or paths;
- missing and unused artifact references;
- changed file identity during a read;
- expected-digest mismatch;
- aggregate protected evidence above the configured bound.

It uses `O_NOFOLLOW` where supported and requires public outputs to remain outside
the protected artifact root.

## Publication sequence

The builder calculates all protected-artifact digests and generates the sanitized
verifier manifest without artifact IDs or paths.

It then:

1. creates a private sibling staging directory;
2. writes the candidate manifest inside that directory;
3. invokes the accepted `verify_development_runtime_evidence.py` implementation;
4. writes verification JSON and Markdown;
5. publishes the sanitized manifest inside the staging directory;
6. writes the package summary;
7. atomically renames the complete staging directory to the requested output
   path.

Invalid verifier input removes the staging directory and leaves no public
manifest. A structurally valid blocked result remains blocked and cannot be
relabeled by the builder.

## Evidence boundary

Public package output retains:

- exact source commit and execution fingerprint;
- accepted plan, approval and protected evidence digests;
- deployment and runtime identity fingerprints;
- evidence-family and assertion metadata;
- rollback and recovery-point digests;
- protected artifact count and aggregate byte count;
- verifier status and stable findings.

It excludes:

- protected paths and contents;
- raw principal identifiers;
- resource and table names;
- table contents and query results;
- provider responses;
- workspace URLs;
- credentials and tokens.

## Validation

Tests cover:

- a complete verified package;
- calculated digests for every registered protected artifact;
- omission of paths and artifact IDs from public output;
- expected-digest mismatch before publication;
- unknown, duplicate and unused artifact entries;
- non-canonical paths that could otherwise alias the same protected file;
- exact family and assertion descriptor sets;
- preservation of a blocked verifier result;
- distinct approval, plan, execution, rollback and recovery evidence;
- family-level and cross-family artifact-reuse boundaries;
- legitimate sharing within one governed evidence family;
- an existing output directory that must not be overwritten;
- late verifier-output failure with no partial public package;
- invalid verifier input with no public or staging residue;
- traversal, symbolic links and protected-root output contamination;
- absence of network, subprocess, credential and provider-operation surfaces.

## Human authority boundary

A verified package proves that supplied metadata is bound to supplied protected
artifacts and satisfies the accepted repository verifier. It does not prove that
external evidence was collected honestly and does not authorize another apply or
runtime execution. Human review of the protected evidence remains required.

## Rollback

Source rollback is a normal revert. Existing packages remain review evidence.
Reverting this builder does not undo a Databricks execution and must not be used to
accept a manually assembled runtime manifest without independently binding every
protected artifact.
