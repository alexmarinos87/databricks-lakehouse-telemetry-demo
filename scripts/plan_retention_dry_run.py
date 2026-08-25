#!/usr/bin/env python3
"""Create a bounded non-mutating retention review plan from sanitized inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = REPO_ROOT / "governance" / "operational_alert_policy.json"
DEFAULT_MAX_AGE_HOURS = 72.0
DEFAULT_MINIMUM_RECOVERY_HOURS = 168.0
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_INPUT_BYTES = 1_000_000
MAX_RELATIONS = 32
MAX_FINDINGS = 128
MAX_STRING_BYTES = 512
MAX_COUNT = 10**12
MAX_BYTES = 10**15
MAX_VERSION = 10**12
OUTPUT_JSON = "retention-dry-run-plan.json"
OUTPUT_MARKDOWN = "retention-dry-run-plan.md"
EXPECTED_REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
EXPECTED_RETENTION_KEYS = {
    "quality_check_results_days",
    "quality_metric_history_days",
    "forecast_history_days",
    "forecast_publication_manifest_days",
    "expectation_event_log_days",
}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class PlanError(RuntimeError):
    """Stable invalid-input category safe to expose in logs."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def positive_hours(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of hours") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of hours")
    return parsed


def _expect(condition: bool, category: str) -> None:
    if not condition:
        raise PlanError(category)


def _read_regular_bytes(path: Path, *, category: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise PlanError(category)
        size = path.stat().st_size
        if size < 1 or size > MAX_INPUT_BYTES:
            raise PlanError(f"{category}_size_invalid")
        payload = path.read_bytes()
        if not payload or len(payload) > MAX_INPUT_BYTES:
            raise PlanError(f"{category}_size_invalid")
        return payload
    except PlanError:
        raise
    except OSError:
        raise PlanError(f"{category}_unreadable") from None


def _json_object(payload: bytes, *, category: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PlanError(f"{category}_invalid_json") from None
    if not isinstance(document, dict):
        raise PlanError(f"{category}_unexpected_shape")
    return document


def _exact_mapping(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PlanError(category)
    return value


def _string(value: Any, *, category: str, maximum_bytes: int = MAX_STRING_BYTES) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise PlanError(category)
    return value


def _timestamp(value: Any, *, category: str) -> datetime:
    text = _string(value, category=category, maximum_bytes=64)
    if not text.endswith("Z"):
        raise PlanError(category)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise PlanError(category) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PlanError(category)
    return parsed.astimezone(timezone.utc)


def _fingerprint(value: Any, *, category: str) -> str:
    text = _string(value, category=category, maximum_bytes=71)
    if not _SHA256.fullmatch(text):
        raise PlanError(category)
    return text


def _bounded_int(value: Any, *, category: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise PlanError(category)
    return value


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_policy(payload: bytes) -> dict[str, int]:
    document = _json_object(payload, category="policy")
    expected = {"schema_version", "owners", "retention_expectations", "alerts", "delivery"}
    _exact_mapping(document, expected, "policy_shape_invalid")
    _expect(document.get("schema_version") == 1, "policy_schema_version_mismatch")
    retention = document.get("retention_expectations")
    if not isinstance(retention, dict) or set(retention) != EXPECTED_RETENTION_KEYS:
        raise PlanError("retention_expectations_shape_invalid")
    normalized: dict[str, int] = {}
    for key, days in retention.items():
        if isinstance(days, bool) or not isinstance(days, int) or days < 30 or days > 3650:
            raise PlanError("retention_expectation_days_invalid")
        normalized[key] = days
    delivery = document.get("delivery")
    if not isinstance(delivery, dict) or delivery.get("repository_state") != "policy_only":
        raise PlanError("policy_delivery_boundary_invalid")
    return normalized


def _validate_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    _exact_mapping(
        document,
        {
            "schema_version",
            "target",
            "repository",
            "source_commit",
            "captured_at_utc",
            "workspace_fingerprint",
            "legal_hold",
            "active_incident",
            "recovery",
            "relations",
        },
        "inventory_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "inventory_schema_version_mismatch")
    _expect(document.get("target") == "dev", "inventory_target_must_be_dev")
    _expect(document.get("repository") == EXPECTED_REPOSITORY, "inventory_repository_mismatch")
    source_commit = _string(
        document.get("source_commit"), category="source_commit_invalid", maximum_bytes=40
    )
    _expect(bool(_COMMIT.fullmatch(source_commit)), "source_commit_invalid")
    captured_at = _timestamp(document.get("captured_at_utc"), category="captured_at_invalid")
    workspace_fingerprint = _fingerprint(
        document.get("workspace_fingerprint"), category="workspace_fingerprint_invalid"
    )
    for field in ("legal_hold", "active_incident"):
        if not isinstance(document.get(field), bool):
            raise PlanError(f"{field}_invalid")

    recovery = _exact_mapping(
        document.get("recovery"),
        {"verified", "evidence_sha256", "recovery_window_hours"},
        "recovery_shape_invalid",
    )
    if not isinstance(recovery.get("verified"), bool):
        raise PlanError("recovery_verified_invalid")
    recovery_window = recovery.get("recovery_window_hours")
    if (
        isinstance(recovery_window, bool)
        or not isinstance(recovery_window, (int, float))
        or not math.isfinite(float(recovery_window))
        or recovery_window <= 0
        or recovery_window > 24 * 3650
    ):
        raise PlanError("recovery_window_hours_invalid")
    normalized_recovery = {
        "verified": recovery["verified"],
        "evidence_sha256": _fingerprint(
            recovery.get("evidence_sha256"), category="recovery_evidence_digest_invalid"
        ),
        "recovery_window_hours": float(recovery_window),
    }

    raw_relations = document.get("relations")
    if not isinstance(raw_relations, list) or len(raw_relations) > MAX_RELATIONS:
        raise PlanError("relations_shape_invalid")
    relations: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_fingerprints: set[str] = set()
    for raw_relation in raw_relations:
        relation = _exact_mapping(
            raw_relation,
            {
                "retention_key",
                "relation_fingerprint",
                "current_version",
                "recovery_version",
                "latest_committed_at_utc",
                "candidate_latest_at_utc",
                "candidate_rows",
                "candidate_bytes",
                "candidate_versions",
                "evidence_sha256",
            },
            "relation_shape_invalid",
        )
        key = _string(relation.get("retention_key"), category="retention_key_invalid")
        _expect(key in EXPECTED_RETENTION_KEYS, "retention_key_unsupported")
        _expect(key not in seen_keys, "retention_key_duplicate")
        seen_keys.add(key)
        relation_fingerprint = _fingerprint(
            relation.get("relation_fingerprint"), category="relation_fingerprint_invalid"
        )
        _expect(
            relation_fingerprint not in seen_fingerprints,
            "relation_fingerprint_duplicate",
        )
        seen_fingerprints.add(relation_fingerprint)
        relations.append(
            {
                "retention_key": key,
                "relation_fingerprint": relation_fingerprint,
                "current_version": _bounded_int(
                    relation.get("current_version"),
                    category="current_version_invalid",
                    maximum=MAX_VERSION,
                ),
                "recovery_version": _bounded_int(
                    relation.get("recovery_version"),
                    category="recovery_version_invalid",
                    maximum=MAX_VERSION,
                ),
                "latest_committed_at_utc": _timestamp(
                    relation.get("latest_committed_at_utc"),
                    category="latest_committed_at_invalid",
                ),
                "latest_committed_at_text": relation["latest_committed_at_utc"],
                "candidate_latest_at_utc": _timestamp(
                    relation.get("candidate_latest_at_utc"),
                    category="candidate_latest_at_invalid",
                ),
                "candidate_latest_at_text": relation["candidate_latest_at_utc"],
                "candidate_rows": _bounded_int(
                    relation.get("candidate_rows"),
                    category="candidate_rows_invalid",
                    maximum=MAX_COUNT,
                ),
                "candidate_bytes": _bounded_int(
                    relation.get("candidate_bytes"),
                    category="candidate_bytes_invalid",
                    maximum=MAX_BYTES,
                ),
                "candidate_versions": _bounded_int(
                    relation.get("candidate_versions"),
                    category="candidate_versions_invalid",
                    maximum=MAX_VERSION,
                ),
                "evidence_sha256": _fingerprint(
                    relation.get("evidence_sha256"),
                    category="relation_evidence_digest_invalid",
                ),
            }
        )

    return {
        "target": "dev",
        "repository": EXPECTED_REPOSITORY,
        "source_commit": source_commit,
        "captured_at_utc": captured_at,
        "captured_at_text": document["captured_at_utc"],
        "workspace_fingerprint": workspace_fingerprint,
        "legal_hold": document["legal_hold"],
        "active_incident": document["active_incident"],
        "recovery": normalized_recovery,
        "relations": relations,
    }


def _finding(category: str, scope: str | None = None) -> dict[str, str]:
    finding = {"category": category}
    if scope is not None:
        finding["scope"] = scope
    return finding


def _build_findings(
    inventory: Mapping[str, Any],
    retention: Mapping[str, int],
    *,
    now: datetime,
    max_age_hours: float,
    minimum_recovery_hours: float,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    captured_at = inventory["captured_at_utc"]
    oldest_allowed = now - timedelta(hours=max_age_hours)
    if captured_at < oldest_allowed:
        findings.append(_finding("inventory_is_stale"))
    if captured_at > now + FUTURE_TOLERANCE:
        findings.append(_finding("inventory_is_in_future"))
    if inventory["legal_hold"]:
        findings.append(_finding("legal_hold_is_active"))
    if inventory["active_incident"]:
        findings.append(_finding("active_incident_blocks_retention"))
    if not inventory["recovery"]["verified"]:
        findings.append(_finding("recovery_evidence_is_not_verified"))
    if inventory["recovery"]["recovery_window_hours"] < minimum_recovery_hours:
        findings.append(_finding("recovery_window_is_too_short"))

    relations = {item["retention_key"]: item for item in inventory["relations"]}
    for key in sorted(retention):
        relation = relations.get(key)
        if relation is None:
            findings.append(_finding("required_retention_relation_missing", key))
            continue
        cutoff = captured_at - timedelta(days=retention[key])
        if relation["recovery_version"] > relation["current_version"]:
            findings.append(_finding("recovery_version_exceeds_current", key))
        if relation["latest_committed_at_utc"] > captured_at + FUTURE_TOLERANCE:
            findings.append(_finding("latest_commit_is_after_inventory", key))
        if relation["candidate_latest_at_utc"] > relation["latest_committed_at_utc"]:
            findings.append(_finding("candidate_is_after_latest_commit", key))
        has_candidates = any(
            relation[field] > 0
            for field in ("candidate_rows", "candidate_bytes", "candidate_versions")
        )
        if has_candidates and relation["candidate_latest_at_utc"] >= cutoff:
            findings.append(_finding("candidate_boundary_is_not_older_than_cutoff", key))
        if relation["candidate_versions"] > relation["current_version"]:
            findings.append(_finding("candidate_version_count_exceeds_current", key))

    deduplicated = {
        (finding["category"], finding.get("scope", "")): finding for finding in findings
    }
    ordered = [deduplicated[key] for key in sorted(deduplicated)]
    if len(ordered) > MAX_FINDINGS:
        ordered = ordered[: MAX_FINDINGS - 1]
        ordered.append(_finding("findings_truncated"))
    return ordered


def create_plan(
    policy_path: Path,
    inventory_path: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    minimum_recovery_hours: float = DEFAULT_MINIMUM_RECOVERY_HOURS,
) -> dict[str, Any]:
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise PlanError("max_age_hours_invalid")
    if not math.isfinite(minimum_recovery_hours) or minimum_recovery_hours <= 0:
        raise PlanError("minimum_recovery_hours_invalid")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise PlanError("planning_time_must_be_utc")
    reference_time = reference_time.astimezone(timezone.utc)

    policy_bytes = _read_regular_bytes(policy_path, category="policy_file_invalid")
    inventory_bytes = _read_regular_bytes(inventory_path, category="inventory_file_invalid")
    retention = _load_policy(policy_bytes)
    inventory = _validate_inventory(_json_object(inventory_bytes, category="inventory"))
    findings = _build_findings(
        inventory,
        retention,
        now=reference_time,
        max_age_hours=max_age_hours,
        minimum_recovery_hours=minimum_recovery_hours,
    )

    relations = []
    by_key = {item["retention_key"]: item for item in inventory["relations"]}
    for key in sorted(retention):
        relation = by_key.get(key)
        if relation is None:
            continue
        relations.append(
            {
                "retention_key": key,
                "retention_days": retention[key],
                "delete_before_utc": _render_timestamp(
                    inventory["captured_at_utc"] - timedelta(days=retention[key])
                ),
                "relation_fingerprint": relation["relation_fingerprint"],
                "current_version": relation["current_version"],
                "recovery_version": relation["recovery_version"],
                "latest_committed_at_utc": relation["latest_committed_at_text"],
                "candidate_latest_at_utc": relation["candidate_latest_at_text"],
                "candidate_rows": relation["candidate_rows"],
                "candidate_bytes": relation["candidate_bytes"],
                "candidate_versions": relation["candidate_versions"],
                "evidence_sha256": relation["evidence_sha256"],
            }
        )

    return {
        "schema_version": 1,
        "status": "ready" if not findings else "blocked",
        "generated_at_utc": _render_timestamp(reference_time),
        "dry_run_only": True,
        "execution_authorized": False,
        "target": inventory["target"],
        "repository": inventory["repository"],
        "source_commit": inventory["source_commit"],
        "captured_at_utc": inventory["captured_at_text"],
        "policy_sha256": _sha256(policy_bytes),
        "inventory_sha256": _sha256(inventory_bytes),
        "workspace_fingerprint": inventory["workspace_fingerprint"],
        "legal_hold": inventory["legal_hold"],
        "active_incident": inventory["active_incident"],
        "recovery": inventory["recovery"],
        "minimum_recovery_hours": minimum_recovery_hours,
        "relation_count": len(relations),
        "required_relation_count": len(retention),
        "relations": relations,
        "findings": findings,
    }


def render_markdown(plan: Mapping[str, Any]) -> str:
    lines = [
        "# Retention dry-run plan",
        "",
        f"- Status: **{plan['status']}**",
        f"- Target: `{plan['target']}`",
        f"- Source commit: `{plan['source_commit']}`",
        f"- Captured: `{plan['captured_at_utc']}`",
        f"- Policy: `{plan['policy_sha256']}`",
        f"- Inventory: `{plan['inventory_sha256']}`",
        f"- Relations: `{plan['relation_count']}/{plan['required_relation_count']}`",
        "- Dry run only: `true`",
        "- Execution authorized: `false`",
        "",
        "## Findings",
        "",
    ]
    if plan["findings"]:
        for finding in plan["findings"]:
            scope = f" (`{finding['scope']}`)" if finding.get("scope") else ""
            lines.append(f"- `{finding['category']}`{scope}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Candidate summary",
            "",
            "| Retention key | Days | Delete before | Rows | Bytes | Versions |",
            "| --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for relation in plan["relations"]:
        lines.append(
            f"| `{relation['retention_key']}` | `{relation['retention_days']}` | "
            f"`{relation['delete_before_utc']}` | `{relation['candidate_rows']}` | "
            f"`{relation['candidate_bytes']}` | `{relation['candidate_versions']}` |"
        )
    lines.extend(
        [
            "",
            "The plan contains relation fingerprints and bounded counts, not table names, "
            "paths, row content, provider output or credentials.",
            "A ready dry run is review evidence only. It contains no deletion, VACUUM, "
            "DROP or retention-execution command and never authorizes mutation.",
            "",
        ]
    )
    return "\n".join(lines)


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise PlanError("output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise PlanError("output_directory_could_not_be_created") from None
    if path.is_symlink() or not path.is_dir():
        raise PlanError("output_directory_invalid")
    return path


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise PlanError("plan_temporary_file_exists")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise PlanError("plan_output_path_invalid")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise PlanError("plan_output_write_failed") from None


def write_outputs(output_directory: Path, plan: Mapping[str, Any]) -> None:
    directory = _prepare_output_directory(output_directory)
    _write_text_atomic(
        directory / OUTPUT_JSON,
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
    )
    _write_text_atomic(directory / OUTPUT_MARKDOWN, render_markdown(plan))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--max-age-hours", type=positive_hours, default=DEFAULT_MAX_AGE_HOURS
    )
    parser.add_argument(
        "--minimum-recovery-hours",
        type=positive_hours,
        default=DEFAULT_MINIMUM_RECOVERY_HOURS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan = create_plan(
            args.policy,
            args.inventory,
            max_age_hours=args.max_age_hours,
            minimum_recovery_hours=args.minimum_recovery_hours,
        )
        write_outputs(args.output_dir, plan)
    except PlanError as error:
        print(f"Retention dry-run planning failed: {error.category}", file=sys.stderr)
        return 2
    print(
        f"Retention dry-run plan {plan['status']}: "
        f"relations={plan['relation_count']}/{plan['required_relation_count']}"
    )
    return 0 if plan["status"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
