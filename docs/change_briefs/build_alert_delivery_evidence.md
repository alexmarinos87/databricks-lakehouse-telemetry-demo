# Change Brief: Build protected alert-delivery evidence

## Problem

The repository can verify a sanitized development test-alert manifest, but the
manifest previously had to be assembled manually. A reviewer could receive a
correctly shaped manifest whose `evidence_sha256` was copied from another event,
calculated from a different file, or supplied without checking the protected
artifact at all.

The collection boundary must remain separate from provider operations. The
repository should hash an already collected protected artifact and immediately
run the accepted verifier without learning the notification endpoint or sending
another alert.

## Outcome

Add an offline package builder:

```bash
python3 scripts/build_alert_delivery_evidence.py \
  --metadata .bootstrap/evidence/dev/alert-delivery-metadata.json \
  --artifact-root .bootstrap/protected/dev/alert-delivery \
  --output-dir .bootstrap/evidence/dev/alert-delivery-package
```

It produces:

```text
alert-delivery-evidence.json
alert-delivery-evidence-summary.md
alert-delivery-verification.json
alert-delivery-verification.md
```

The builder does not create a destination, trigger an alert, send a notification,
query Databricks, execute SQL or contact a provider.

## Metadata contract

The metadata document contains every field required by the accepted
schema-version-1 alert verifier except `evidence_sha256`. It adds exactly one
protected-artifact descriptor:

```json
{
  "protected_artifact": {
    "path": "delivery/evidence.json",
    "expected_sha256": "sha256:<64 lowercase hex characters>"
  }
}
```

Raw destination URLs, email addresses, webhook payloads, provider responses and
credentials are not metadata fields. Unknown fields fail closed.

The artifact path is relative to the supplied protected root. Absolute paths,
`..`, backslashes, symbolic links, non-regular files and paths outside the root
are rejected.

## Digest binding

The builder reads the protected artifact through a bounded regular-file handle,
rejects descriptor changes during the read, calculates SHA-256 and requires an
exact match with `expected_sha256`.

Only after the match does it create the verifier manifest. The protected path and
descriptor are removed and replaced by:

```text
evidence_sha256
```

The generated manifest is then passed directly to
`scripts/verify_alert_delivery_evidence.py`. A blocked verifier result remains
blocked; the package builder never relabels it as successful.

The verifier manifest is first written to a hidden candidate path. It is published
under the public output name only after the accepted verifier has parsed and
classified it. Invalid verifier input removes the candidate and leaves no public
manifest.

## Evidence boundary

The package retains:

- exact repository and source commit;
- alert and bounded event IDs;
- alert timestamps and ownership metadata;
- workspace, deployed-asset and destination fingerprints;
- metadata and protected-artifact SHA-256 digests;
- protected-artifact byte count;
- verifier status and stable findings.

It excludes:

- the protected artifact path;
- protected artifact contents;
- notification destination URLs;
- email addresses;
- webhook or notification bodies;
- credentials and tokens;
- provider responses;
- raw telemetry rows.

## Security boundary

The builder:

- uses the Python standard library only;
- performs no network or subprocess call;
- reads no credential environment variable;
- rejects symbolic-link metadata, roots, path components and outputs;
- requires the public output directory to remain outside the protected artifact root;
- uses `O_NOFOLLOW` where supported;
- bounds metadata and protected-artifact bytes;
- publishes no public manifest when the accepted verifier rejects the input;
- writes the manifest and summary atomically;
- delegates policy and semantic checks to the accepted verifier.

A verified package establishes that the supplied protected artifact and sanitized
metadata agree with the repository contract. A human must still inspect the
protected evidence and confirm that it was collected from the intended external
destination.

## Validation

Tests cover:

- a complete verified package;
- calculated digest and expected-digest equality;
- omission of protected paths from every public output;
- digest mismatch before verifier invocation;
- path traversal, absolute paths, backslashes and symbolic links;
- raw endpoint fields and caller-supplied verifier digests;
- preservation of a blocked verifier result;
- output-root separation so package files cannot contaminate protected evidence;
- invalid verifier input cleanup with no public manifest or candidate residue;
- symbolic-link metadata, roots and output directories;
- absence of network, subprocess and notification-delivery surfaces.

## Rollback

Source rollback is a normal revert. Existing packages remain review evidence and
do not trigger, cancel or alter any external alert. Reverting the builder must not
be used to accept a manually assembled manifest without independently verifying
its protected artifact.
