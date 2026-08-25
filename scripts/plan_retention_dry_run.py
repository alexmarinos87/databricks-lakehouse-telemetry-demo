#!/usr/bin/env python3
"""Create an evidence-bound, non-mutating retention dry-run plan."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

_CORE_PATH = Path(__file__).with_name("retention_dry_run_core.py")
_CORE_SPEC = importlib.util.spec_from_file_location("_retention_dry_run_core", _CORE_PATH)
if _CORE_SPEC is None or _CORE_SPEC.loader is None:
    raise RuntimeError("retention_dry_run_core_unavailable")
_core = importlib.util.module_from_spec(_CORE_SPEC)
sys.modules[_CORE_SPEC.name] = _core
_CORE_SPEC.loader.exec_module(_core)

REPO_ROOT = _core.REPO_ROOT
DEFAULT_POLICY = _core.DEFAULT_POLICY
DEFAULT_MAX_AGE_HOURS = _core.DEFAULT_MAX_AGE_HOURS
DEFAULT_MINIMUM_RECOVERY_HOURS = _core.DEFAULT_MINIMUM_RECOVERY_HOURS
FUTURE_TOLERANCE = _core.FUTURE_TOLERANCE
MAX_INPUT_BYTES = _core.MAX_INPUT_BYTES
MAX_RELATIONS = _core.MAX_RELATIONS
MAX_FINDINGS = _core.MAX_FINDINGS
MAX_STRING_BYTES = _core.MAX_STRING_BYTES
MAX_COUNT = _core.MAX_COUNT
MAX_BYTES = _core.MAX_BYTES
MAX_VERSION = _core.MAX_VERSION
OUTPUT_JSON = _core.OUTPUT_JSON
OUTPUT_MARKDOWN = _core.OUTPUT_MARKDOWN
EXPECTED_REPOSITORY = _core.EXPECTED_REPOSITORY
EXPECTED_RETENTION_KEYS = _core.EXPECTED_RETENTION_KEYS
PlanError = _core.PlanError
positive_hours = _core.positive_hours

_INVENTORY_KEYS = {
    "schema_version",
    "target",
    "repository",
    "source_commit",
    "captured_at_utc",
    "workspace_fingerprint",
    "legal_hold",
    "legal_hold_evidence_sha256",
    "active_incident",
    "active_incident_evidence_sha256",
    "recovery",
    "relations",
}


def _validate_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    """Bind hold/incident claims to evidence and reject ambiguous counts."""

    if not isinstance(document, dict) or set(document) != _INVENTORY_KEYS:
        raise PlanError("inventory_shape_invalid")
    legal_hold_evidence = _core._fingerprint(
        document.get("legal_hold_evidence_sha256"),
        category="legal_hold_evidence_digest_invalid",
    )
    active_incident_evidence = _core._fingerprint(
        document.get("active_incident_evidence_sha256"),
        category="active_incident_evidence_digest_invalid",
    )

    delegated_document = dict(document)
    delegated_document.pop("legal_hold_evidence_sha256")
    delegated_document.pop("active_incident_evidence_sha256")
    normalized = _core._validate_inventory(delegated_document)
    normalized["legal_hold_evidence_sha256"] = legal_hold_evidence
    normalized["active_incident_evidence_sha256"] = active_incident_evidence

    for relation in normalized["relations"]:
        positive = tuple(
            relation[field] > 0
            for field in ("candidate_rows", "candidate_bytes", "candidate_versions")
        )
        if any(positive) and not all(positive):
            raise PlanError("candidate_counts_are_inconsistent")
    return normalized


def create_plan(
    policy_path: Path,
    inventory_path: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    minimum_recovery_hours: float = DEFAULT_MINIMUM_RECOVERY_HOURS,
) -> dict[str, Any]:
    """Create one exact dry-run report without invoking an external system."""

    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise PlanError("max_age_hours_invalid")
    if not math.isfinite(minimum_recovery_hours) or minimum_recovery_hours <= 0:
        raise PlanError("minimum_recovery_hours_invalid")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise PlanError("planning_time_must_be_utc")
    reference_time = reference_time.astimezone(timezone.utc)

    policy_bytes = _core._read_regular_bytes(
        policy_path, category="policy_file_invalid"
    )
    inventory_bytes = _core._read_regular_bytes(
        inventory_path, category="inventory_file_invalid"
    )
    retention = _core._load_policy(policy_bytes)
    inventory = _validate_inventory(
        _core._json_object(inventory_bytes, category="inventory")
    )
    findings = _core._build_findings(
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
                "delete_before_utc": _core._render_timestamp(
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
        "generated_at_utc": _core._render_timestamp(reference_time),
        "dry_run_only": True,
        "execution_authorized": False,
        "target": inventory["target"],
        "repository": inventory["repository"],
        "source_commit": inventory["source_commit"],
        "captured_at_utc": inventory["captured_at_text"],
        "policy_sha256": _core._sha256(policy_bytes),
        "inventory_sha256": _core._sha256(inventory_bytes),
        "workspace_fingerprint": inventory["workspace_fingerprint"],
        "legal_hold": inventory["legal_hold"],
        "legal_hold_evidence_sha256": inventory["legal_hold_evidence_sha256"],
        "active_incident": inventory["active_incident"],
        "active_incident_evidence_sha256": inventory[
            "active_incident_evidence_sha256"
        ],
        "recovery": inventory["recovery"],
        "minimum_recovery_hours": minimum_recovery_hours,
        "relation_count": len(relations),
        "required_relation_count": len(retention),
        "relations": relations,
        "findings": findings,
    }


def render_markdown(plan: Mapping[str, Any]) -> str:
    rendered = _core.render_markdown(plan)
    marker = f"- Inventory: `{plan['inventory_sha256']}`\n"
    addition = (
        f"- Legal-hold evidence: `{plan['legal_hold_evidence_sha256']}`\n"
        f"- Incident evidence: `{plan['active_incident_evidence_sha256']}`\n"
    )
    if marker not in rendered:
        raise PlanError("retention_markdown_template_mismatch")
    return rendered.replace(marker, marker + addition, 1)


def write_outputs(output_directory: Path, plan: Mapping[str, Any]) -> None:
    directory = _core._prepare_output_directory(output_directory)
    _core._write_text_atomic(
        directory / OUTPUT_JSON,
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
    )
    _core._write_text_atomic(directory / OUTPUT_MARKDOWN, render_markdown(plan))


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
