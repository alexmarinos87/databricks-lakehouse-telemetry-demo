# Change Brief: Verify development alert-delivery evidence

## Problem

The operational policy defines alert conditions, owners, detection delays and
runbooks, but repository source cannot prove that a notification destination is
configured or that a test alert was delivered, acknowledged and resolved.

Without a strict evidence boundary, a delivery review could accept a screenshot
or note that refers to the wrong alert, wrong owner, duplicate notifications,
late delivery, an unresolved runbook or a production event.

## Outcome

Add an offline development evidence verifier:

```bash
python3 scripts/verify_alert_delivery_evidence.py \
  --evidence .bootstrap/evidence/dev/alert-delivery-evidence.json \
  --output-dir .bootstrap/evidence/dev/alert-delivery-verification
```

The verifier consumes the accepted alert definitions in:

```text
governance/operational_alert_policy.json
```

It writes:

```text
alert-delivery-verification.json
alert-delivery-verification.md
```

Exit status is:

```text
0  verified
1  structurally valid evidence blocked by findings
2  invalid policy, manifest, timestamp or path
```

## Manifest boundary

The schema-version-1 manifest is development-only and requires:

```text
schema_version
target
repository
source_commit
captured_at_utc
workspace_fingerprint
alert_event_id
alert_id
severity
owner
deployed_asset_fingerprint
destination_fingerprint
triggered_at_utc
delivered_at_utc
acknowledged_at_utc
resolved_at_utc
delivery_attempts
notification_count
delivery_status
acknowledging_owner
runbook
test_alert
evidence_sha256
```

The manifest contains fingerprints instead of workspace URLs, query identifiers
or notification endpoints. `test_alert` must be true and `target` must be `dev`.
Production evidence is not admitted through this gate.

## Policy checks

The verifier requires the alert ID to exist in the accepted operational policy
and checks that:

- severity matches the policy;
- owner matches the policy;
- acknowledging owner matches the policy owner key;
- the runbook link is exact and resolves to a regular repository file;
- delivery occurs within `maximum_detection_delay_minutes`;
- exactly one notification was delivered;
- delivery status is `delivered`;
- delivery attempts remain within the bounded range;
- deployed-asset and destination fingerprints are distinct.

The policy must continue to list all five required external evidence families:

```text
deployed_query_or_dashboard_identifier
notification_destination_identifier
test_alert_delivery_timestamp
acknowledging_owner
resolved_runbook_link
```

## Time and provenance checks

The verifier requires:

- one lowercase 40-character source commit;
- the public repository identity;
- UTC timestamps ending in `Z`;
- trigger, delivery, acknowledgement, resolution and capture in monotonic order;
- no materially future timestamp;
- a capture no older than the configured maximum age;
- bounded event IDs and SHA-256 fingerprints.

The default maximum age is 72 hours and can be reduced with
`--max-age-hours`.

## Blocking findings

Representative findings include:

```text
alert_delivery_delay_exceeds_policy
alert_notification_count_unexpected
alert_delivery_not_confirmed
alert_evidence_owner_mismatch
alert_evidence_acknowledging_owner_mismatch
alert_evidence_runbook_mismatch
alert_evidence_runbook_unresolved
alert_evidence_timestamps_out_of_order
alert_evidence_capture_is_stale
alert_asset_and_destination_fingerprints_overlap
```

Unknown alert IDs, production targets, non-test events, malformed timestamps,
unsupported fields, raw endpoint fields and unsafe paths are invalid input rather
than warnings.

## Evidence boundary

The verification output retains:

- source, policy and evidence digests;
- workspace, deployed-asset and destination fingerprints;
- alert ID, severity and owner key;
- bounded event identity;
- delivery timestamps and delay;
- attempts and notification count;
- stable finding categories.

It excludes destination URLs, email addresses, webhook bodies, tokens, provider
responses, raw telemetry rows, SQL output and notification message content.

## Security and execution boundary

The verifier:

- uses the Python standard library only;
- performs no network or subprocess call;
- reads no credential environment variable;
- accepts bounded regular files only;
- rejects symbolic-link input and output paths;
- writes outputs atomically;
- performs no alert trigger or delivery operation.

A `verified` report proves only that the supplied sanitized evidence is complete
and consistent with accepted policy. A human must inspect the protected evidence
referenced by its digest and confirm the destination and acknowledging owner.

## Validation

Tests cover:

- a complete verified development test alert;
- delayed delivery and duplicate notification fan-out;
- severity, owner, acknowledging-owner and runbook drift;
- failed delivery, time-order drift and missing runbook files;
- stale, future and overlapping-fingerprint evidence;
- unknown alerts, production targets and non-test events;
- forbidden raw fields, bounded delivery attempts and policy drift;
- symbolic-link output rejection;
- deterministic sanitized JSON and Markdown output.

## Rollback

Source rollback is a normal revert. Reverting the verifier does not disable or
remove a real alert destination. Do not use rollback to claim delivery evidence
without an accepted verification report.
