#!/usr/bin/env python3
"""Review a captured Databricks direct-engine bundle plan against repository policy.

The command is offline: it reads the exact JSON plan retained by the plan-evidence
workflow, validates the direct-engine schema used by this repository, and emits a
sanitized accept/block decision. It never invokes GitHub, Databricks, or Terraform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MAX_PLAN_BYTES = 4_000_000
MAX_POLICY_BYTES = 100_000
MAX_RESOURCES = 500
MAX_CHANGES_PER_RESOURCE = 1_000
MAX_DEPENDENCIES_PER_RESOURCE = 200
MAX_FINDINGS = 100
MAX_JSON_NODES = 100_000
MAX_JSON_DEPTH = 32
MAX_STRING_BYTES = 1_000_000

ALLOWED_ACTIONS = {
    "skip",
    "resize",
    "update",
    "update_id",
    "create",
    "recreate",
    "delete",
}
CHANGE_ACTIONS = {"resize", "update", "update_id"}
ACTION_SEVERITY = {
    "skip": 1,
    "resize": 2,
    "update": 3,
    "update_id": 4,
    "create": 5,
    "recreate": 6,
    "delete": 7,
}
TOP_LEVEL_REQUIRED = {"plan_version", "cli_version", "plan"}
TOP_LEVEL_ALLOWED = TOP_LEVEL_REQUIRED | {
    "lineage",
    "serial",
    "not_selected",
}
PLAN_ENTRY_ALLOWED = {
    "id",
    "depends_on",
    "action",
    "gone",
    "new_state",
    "remote_state",
    "changes",
}
CHANGE_ALLOWED = {"action", "reason", "old", "new", "remote"}
DEPENDENCY_ALLOWED = {"node", "label"}

_PERMISSION_FRAGMENTS = (
    ".permissions",
    ".grants",
    "permission",
    "grant",
    "access_control",
    "service_principal_role",
    "mws_permission_assignment",
    "workspace_conf",
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(client[_-]?secret|access[_-]?token|authorization|password|"
    r"secret[_-]?value|token[_-]?value)"
)
_REDACTED_VALUES = {
    "(sensitive value)",
    "<redacted>",
    "[redacted]",
    "***",
    "redacted",
}
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


class ReviewError(RuntimeError):
    """A stable failure category safe to print without provider values."""

    def __init__(self, stage: str, category: str) -> None:
        super().__init__(f"{stage}: {category}")
        self.stage = stage
        self.category = category


@dataclass(frozen=True)
class TargetPolicy:
    required_plan_version: int
    allow_delete: bool
    allow_recreate: bool
    allow_gone_delete: bool
    forbidden_fragments: tuple[str, ...]
    max_create: int
    max_change: int
    max_delete: int
    max_recreate: int
    max_gone_delete: int
    max_permission_sensitive_resources: int


@dataclass(frozen=True)
class ParsedResource:
    address: str
    action: str
    gone: bool
    permission_sensitive: bool
    crosses_target_boundary: bool
    contains_unredacted_sensitive_value: bool


@dataclass(frozen=True)
class ParsedPlan:
    plan_version: int
    cli_version: str
    lineage: str | None
    serial: int | None
    not_selected: int
    resources: tuple[ParsedResource, ...]
    sha256: str
    byte_count: int


@dataclass
class _Inspection:
    forbidden_fragments: tuple[str, ...]
    nodes: int = 0
    crosses_boundary: bool = False
    unredacted_sensitive: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_regular_bytes(path: Path, *, limit: int, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise ReviewError("configuration", f"{label}_is_not_a_regular_file")
        if path.stat().st_size > limit:
            raise ReviewError("configuration", f"{label}_exceeds_size_limit")
        payload = path.read_bytes()
    except ReviewError:
        raise
    except OSError:
        raise ReviewError("configuration", f"{label}_could_not_be_read") from None
    if len(payload) > limit:
        raise ReviewError("configuration", f"{label}_exceeds_size_limit")
    return payload


def _decode_json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReviewError("configuration", f"{label}_is_invalid_json") from None
    if not isinstance(document, dict):
        raise ReviewError("configuration", f"{label}_shape_is_invalid")
    return document


def _bounded_non_negative_int(value: Any, *, label: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ReviewError("configuration", f"{label}_is_invalid")
    return value


def _bounded_string(
    value: Any,
    *,
    stage: str,
    label: str,
    maximum: int = 1_024,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ReviewError(stage, f"{label}_is_invalid")
    if (
        (not value and not allow_empty)
        or len(value.encode("utf-8")) > maximum
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ReviewError(stage, f"{label}_is_invalid")
    return value


def load_policy(path: Path, target: str) -> TargetPolicy:
    document = _decode_json_object(
        _read_regular_bytes(path, limit=MAX_POLICY_BYTES, label="review_policy"),
        label="review_policy",
    )
    if set(document) != {"schema_version", "required_plan_version", "targets"}:
        raise ReviewError("configuration", "review_policy_shape_is_invalid")
    if document["schema_version"] != 2:
        raise ReviewError("configuration", "review_policy_version_is_unsupported")
    required_plan_version = _bounded_non_negative_int(
        document["required_plan_version"],
        label="required_plan_version",
        maximum=100,
    )
    if required_plan_version == 0:
        raise ReviewError("configuration", "required_plan_version_is_invalid")

    targets = document["targets"]
    if not isinstance(targets, dict) or target not in targets:
        raise ReviewError("configuration", "review_target_is_not_configured")
    raw = targets[target]
    expected_keys = {
        "allow_delete",
        "allow_recreate",
        "allow_gone_delete",
        "forbidden_fragments",
        "max_create",
        "max_change",
        "max_delete",
        "max_recreate",
        "max_gone_delete",
        "max_permission_sensitive_resources",
    }
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise ReviewError("configuration", "target_policy_shape_is_invalid")
    for field in ("allow_delete", "allow_recreate", "allow_gone_delete"):
        if not isinstance(raw[field], bool):
            raise ReviewError("configuration", f"{field}_is_invalid")

    fragments = raw["forbidden_fragments"]
    if not isinstance(fragments, list) or not fragments or len(fragments) > 50:
        raise ReviewError("configuration", "forbidden_fragments_are_invalid")
    normalized: list[str] = []
    for fragment in fragments:
        text = _bounded_string(
            fragment,
            stage="configuration",
            label="forbidden_fragment",
            maximum=256,
        ).strip().lower()
        if not text:
            raise ReviewError("configuration", "forbidden_fragment_is_invalid")
        normalized.append(text)
    if len(set(normalized)) != len(normalized):
        raise ReviewError("configuration", "forbidden_fragments_are_duplicated")

    limits = {
        name: _bounded_non_negative_int(raw[name], label=name, maximum=MAX_RESOURCES)
        for name in (
            "max_create",
            "max_change",
            "max_delete",
            "max_recreate",
            "max_gone_delete",
            "max_permission_sensitive_resources",
        )
    }
    return TargetPolicy(
        required_plan_version=required_plan_version,
        allow_delete=raw["allow_delete"],
        allow_recreate=raw["allow_recreate"],
        allow_gone_delete=raw["allow_gone_delete"],
        forbidden_fragments=tuple(normalized),
        **limits,
    )


def _is_redacted(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _REDACTED_VALUES
    if isinstance(value, list):
        return bool(value) and all(_is_redacted(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(_is_redacted(item) for item in value.values())
    return False


def _inspect_json_value(
    value: Any,
    *,
    path: str,
    inspection: _Inspection,
    depth: int = 0,
) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ReviewError("plan", "plan_json_depth_exceeds_limit")
    inspection.nodes += 1
    if inspection.nodes > MAX_JSON_NODES:
        raise ReviewError("plan", "plan_json_node_count_exceeds_limit")

    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = _bounded_string(
                raw_key,
                stage="plan",
                label="json_key",
                maximum=1_024,
            )
            lowered_key = key.lower()
            if _SENSITIVE_KEY.search(lowered_key) and not _is_redacted(child):
                inspection.unredacted_sensitive = True
            if any(fragment in lowered_key for fragment in inspection.forbidden_fragments):
                inspection.crosses_boundary = True
            _inspect_json_value(
                child,
                path=f"{path}.{key}",
                inspection=inspection,
                depth=depth + 1,
            )
        return

    if isinstance(value, list):
        if len(value) > MAX_JSON_NODES:
            raise ReviewError("plan", "plan_json_collection_exceeds_limit")
        for index, child in enumerate(value):
            _inspect_json_value(
                child,
                path=f"{path}[{index}]",
                inspection=inspection,
                depth=depth + 1,
            )
        return

    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise ReviewError("plan", "plan_string_exceeds_size_limit")
        lowered = value.lower()
        if any(fragment in lowered for fragment in inspection.forbidden_fragments):
            inspection.crosses_boundary = True
        if lowered.startswith("bearer ") or lowered.startswith("dapi"):
            inspection.unredacted_sensitive = True
        return

    if value is None or isinstance(value, (bool, int, float)):
        return
    raise ReviewError("plan", "plan_contains_unsupported_json_value")


def _validate_dependency(value: Any, inspection: _Inspection) -> None:
    if not isinstance(value, dict) or not set(value) <= DEPENDENCY_ALLOWED or "node" not in value:
        raise ReviewError("plan", "dependency_shape_is_invalid")
    node = _bounded_string(value["node"], stage="plan", label="dependency_node")
    _inspect_json_value(node, path="depends_on.node", inspection=inspection)
    if "label" in value:
        label = _bounded_string(
            value["label"],
            stage="plan",
            label="dependency_label",
            maximum=1_024,
            allow_empty=True,
        )
        _inspect_json_value(label, path="depends_on.label", inspection=inspection)


def _validate_changes(
    value: Any,
    *,
    entry_action: str,
    inspection: _Inspection,
) -> bool:
    if value is None:
        changes: dict[str, Any] = {}
    elif isinstance(value, dict):
        changes = value
    else:
        raise ReviewError("plan", "changes_shape_is_invalid")
    if len(changes) > MAX_CHANGES_PER_RESOURCE:
        raise ReviewError("plan", "change_count_exceeds_limit")

    permission_sensitive = False
    actionable: list[str] = []
    for raw_path, raw_change in changes.items():
        field_path = _bounded_string(
            raw_path,
            stage="plan",
            label="change_path",
            maximum=1_024,
        )
        lowered_path = field_path.lower()
        if any(fragment in lowered_path for fragment in _PERMISSION_FRAGMENTS):
            permission_sensitive = True
        if any(fragment in lowered_path for fragment in inspection.forbidden_fragments):
            inspection.crosses_boundary = True
        if not isinstance(raw_change, dict) or not set(raw_change) <= CHANGE_ALLOWED:
            raise ReviewError("plan", "change_shape_is_invalid")
        if "action" not in raw_change:
            raise ReviewError("plan", "change_action_is_missing")
        action = raw_change["action"]
        if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
            raise ReviewError("plan", "change_action_is_unsupported")
        if action != "skip":
            actionable.append(action)
        if "reason" in raw_change:
            _bounded_string(
                raw_change["reason"],
                stage="plan",
                label="change_reason",
                maximum=256,
                allow_empty=True,
            )
        for field in ("old", "new", "remote"):
            if field in raw_change:
                _inspect_json_value(
                    raw_change[field],
                    path=f"changes.{field_path}.{field}",
                    inspection=inspection,
                )

    if entry_action == "skip" and actionable:
        raise ReviewError("plan", "skip_entry_contains_actionable_changes")
    if entry_action in CHANGE_ACTIONS | {"recreate"}:
        if not actionable:
            raise ReviewError("plan", "actionable_entry_has_no_changes")
        highest = max(actionable, key=ACTION_SEVERITY.__getitem__)
        if highest != entry_action:
            raise ReviewError("plan", "entry_action_does_not_match_changes")
    return permission_sensitive


def _parse_resource(
    address: str,
    value: Any,
    *,
    forbidden_fragments: tuple[str, ...],
) -> ParsedResource:
    address = _bounded_string(
        address,
        stage="plan",
        label="resource_address",
        maximum=1_024,
    )
    if not address.startswith("resources."):
        raise ReviewError("plan", "resource_address_is_invalid")
    if not isinstance(value, dict) or not set(value) <= PLAN_ENTRY_ALLOWED:
        raise ReviewError("plan", "plan_entry_shape_is_invalid")
    if "action" not in value:
        raise ReviewError("plan", "plan_entry_action_is_missing")
    action = value["action"]
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        raise ReviewError("plan", "plan_entry_action_is_unsupported")
    gone = value.get("gone", False)
    if not isinstance(gone, bool):
        raise ReviewError("plan", "plan_entry_gone_is_invalid")
    if gone and action != "delete":
        raise ReviewError("plan", "gone_is_only_valid_for_delete")

    inspection = _Inspection(forbidden_fragments=forbidden_fragments)
    _inspect_json_value(address, path="resource_address", inspection=inspection)

    if "id" in value:
        resource_id = _bounded_string(
            value["id"],
            stage="plan",
            label="resource_id",
            maximum=1_024,
            allow_empty=True,
        )
        _inspect_json_value(resource_id, path="id", inspection=inspection)

    dependencies = value.get("depends_on", [])
    if not isinstance(dependencies, list):
        raise ReviewError("plan", "dependencies_shape_is_invalid")
    if len(dependencies) > MAX_DEPENDENCIES_PER_RESOURCE:
        raise ReviewError("plan", "dependency_count_exceeds_limit")
    for dependency in dependencies:
        _validate_dependency(dependency, inspection)

    for field in ("new_state", "remote_state"):
        if field in value:
            _inspect_json_value(
                value[field],
                path=field,
                inspection=inspection,
            )

    permission_sensitive = any(
        fragment in address.lower() for fragment in _PERMISSION_FRAGMENTS
    )
    permission_sensitive = (
        _validate_changes(
            value.get("changes"),
            entry_action=action,
            inspection=inspection,
        )
        or permission_sensitive
    )
    return ParsedResource(
        address=address,
        action=action,
        gone=gone,
        permission_sensitive=permission_sensitive,
        crosses_target_boundary=inspection.crosses_boundary,
        contains_unredacted_sensitive_value=inspection.unredacted_sensitive,
    )


def parse_plan(path: Path, *, policy: TargetPolicy) -> ParsedPlan:
    raw = _read_regular_bytes(path, limit=MAX_PLAN_BYTES, label="plan_file")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReviewError("plan", "plan_is_invalid_json") from None
    if not isinstance(document, dict):
        raise ReviewError("plan", "plan_shape_is_invalid")
    if not TOP_LEVEL_REQUIRED <= set(document) or not set(document) <= TOP_LEVEL_ALLOWED:
        raise ReviewError("plan", "plan_shape_is_invalid")

    plan_version = document["plan_version"]
    if (
        isinstance(plan_version, bool)
        or not isinstance(plan_version, int)
        or plan_version != policy.required_plan_version
    ):
        raise ReviewError("plan", "plan_version_is_unsupported")
    cli_version = _bounded_string(
        document["cli_version"],
        stage="plan",
        label="cli_version",
        maximum=128,
    )

    lineage: str | None = None
    if "lineage" in document:
        lineage = _bounded_string(
            document["lineage"],
            stage="plan",
            label="lineage",
            maximum=1_024,
            allow_empty=True,
        )
    serial: int | None = None
    if "serial" in document:
        serial = _bounded_non_negative_int(
            document["serial"],
            label="serial",
            maximum=2**63 - 1,
        )
    not_selected = _bounded_non_negative_int(
        document.get("not_selected", 0),
        label="not_selected",
        maximum=MAX_RESOURCES,
    )

    raw_plan = document["plan"]
    if not isinstance(raw_plan, dict):
        raise ReviewError("plan", "plan_resource_map_is_invalid")
    if len(raw_plan) > MAX_RESOURCES:
        raise ReviewError("plan", "plan_resource_count_exceeds_limit")
    resources = tuple(
        _parse_resource(
            address,
            raw_plan[address],
            forbidden_fragments=policy.forbidden_fragments,
        )
        for address in sorted(raw_plan)
    )
    return ParsedPlan(
        plan_version=plan_version,
        cli_version=cli_version,
        lineage=lineage,
        serial=serial,
        not_selected=not_selected,
        resources=resources,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
    )


def _add_finding(
    findings: list[dict[str, str]],
    category: str,
    scope: str,
) -> None:
    if len(findings) >= MAX_FINDINGS:
        if not any(item["category"] == "findings_truncated" for item in findings):
            findings.append({"category": "findings_truncated", "scope": "global"})
        return
    findings.append({"category": category, "scope": scope})


def review_plan(
    parsed: ParsedPlan,
    *,
    policy: TargetPolicy,
    target: str,
    source_commit: str,
) -> dict[str, Any]:
    if not _SHA_PATTERN.fullmatch(source_commit):
        raise ReviewError("configuration", "source_commit_is_invalid")

    counts = {action: 0 for action in sorted(ALLOWED_ACTIONS)}
    counts.update({"change": 0, "destructive_delete": 0, "gone_delete": 0})
    permission_sensitive = 0
    findings: list[dict[str, str]] = []
    resource_evidence: list[dict[str, Any]] = []

    for resource in parsed.resources:
        counts[resource.action] += 1
        if resource.action in CHANGE_ACTIONS:
            counts["change"] += 1
        if resource.action == "delete":
            if resource.gone:
                counts["gone_delete"] += 1
            else:
                counts["destructive_delete"] += 1
        if resource.permission_sensitive:
            permission_sensitive += 1

        scope = _fingerprint(resource.address)
        if resource.crosses_target_boundary:
            _add_finding(findings, "resource_crosses_target_boundary", scope)
        if resource.contains_unredacted_sensitive_value:
            _add_finding(
                findings,
                "plan_contains_unredacted_sensitive_value",
                scope,
            )
        resource_evidence.append(
            {
                "action": resource.action,
                "address_fingerprint": scope,
                "gone": resource.gone,
                "permission_sensitive": resource.permission_sensitive,
                "target_boundary_match": not resource.crosses_target_boundary,
                "sensitive_values_redacted": (
                    not resource.contains_unredacted_sensitive_value
                ),
            }
        )

    if counts["create"] > policy.max_create:
        _add_finding(findings, "create_count_exceeds_policy", "plan")
    if counts["change"] > policy.max_change:
        _add_finding(findings, "change_count_exceeds_policy", "plan")
    if counts["destructive_delete"] > policy.max_delete:
        _add_finding(findings, "delete_count_exceeds_policy", "plan")
    if counts["recreate"] > policy.max_recreate:
        _add_finding(findings, "recreate_count_exceeds_policy", "plan")
    if counts["gone_delete"] > policy.max_gone_delete:
        _add_finding(findings, "gone_delete_count_exceeds_policy", "plan")
    if counts["destructive_delete"] and not policy.allow_delete:
        _add_finding(findings, "delete_is_not_allowed", "plan")
    if counts["recreate"] and not policy.allow_recreate:
        _add_finding(findings, "recreate_is_not_allowed", "plan")
    if counts["gone_delete"] and not policy.allow_gone_delete:
        _add_finding(findings, "gone_delete_is_not_allowed", "plan")
    if permission_sensitive > policy.max_permission_sensitive_resources:
        _add_finding(
            findings,
            "permission_resource_count_exceeds_policy",
            "plan",
        )

    return {
        "schema_version": 2,
        "status": "accepted" if not findings else "blocked",
        "generated_at_utc": _utc_now(),
        "target": target,
        "source_commit": source_commit,
        "plan_sha256": "sha256:" + parsed.sha256,
        "plan_bytes": parsed.byte_count,
        "plan_version": parsed.plan_version,
        "cli_version": parsed.cli_version,
        "lineage_fingerprint": (
            _fingerprint(parsed.lineage) if parsed.lineage else None
        ),
        "serial": parsed.serial,
        "not_selected": parsed.not_selected,
        "resource_count": len(parsed.resources),
        "resource_actions": counts,
        "permission_sensitive_resources": permission_sensitive,
        "resources": resource_evidence,
        "findings": findings,
    }


def render_summary(evidence: Mapping[str, Any]) -> str:
    actions = evidence.get("resource_actions", {})
    findings = evidence.get("findings", [])
    lines = [
        "# Databricks direct-plan review",
        "",
        f"- Status: **{evidence.get('status', 'unknown')}**",
        f"- Target: `{evidence.get('target', '')}`",
        f"- Source commit: `{evidence.get('source_commit', '')}`",
        f"- Plan digest: `{evidence.get('plan_sha256', '')}`",
        f"- Direct plan version: `{evidence.get('plan_version', '')}`",
        f"- CLI version: `{evidence.get('cli_version', '')}`",
        f"- Resources: `{evidence.get('resource_count', 0)}`",
        f"- Create/change/delete/recreate: `{actions.get('create', 0)}` / "
        f"`{actions.get('change', 0)}` / "
        f"`{actions.get('destructive_delete', 0)}` / "
        f"`{actions.get('recreate', 0)}`",
        f"- State-only gone deletes: `{actions.get('gone_delete', 0)}`",
        f"- Permission-sensitive resources: "
        f"`{evidence.get('permission_sensitive_resources', 0)}`",
        f"- Findings: `{len(findings) if isinstance(findings, list) else 0}`",
    ]
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, dict):
                lines.append(
                    f"  - `{finding.get('category', '')}` in "
                    f"`{finding.get('scope', '')}`"
                )
    lines.extend(
        [
            "",
            "Resource addresses, lineage and sensitive values are never copied into this record.",
            "Acceptance is a repository policy gate, not permission to apply without human approval.",
            "",
        ]
    )
    return "\n".join(lines)


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise ReviewError("configuration", "output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ReviewError(
            "configuration",
            "output_directory_could_not_be_created",
        ) from None
    if path.is_symlink() or not path.is_dir():
        raise ReviewError("configuration", "output_directory_is_not_regular")
    return path


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_evidence(output_directory: Path, evidence: Mapping[str, Any]) -> None:
    prepared = _prepare_output_directory(output_directory)
    _write_text_atomic(
        prepared / "databricks-plan-review.json",
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
    )
    _write_text_atomic(
        prepared / "databricks-plan-review.md",
        render_summary(evidence),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-file", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=("dev", "prod"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        policy = load_policy(args.policy, args.target)
        parsed = parse_plan(args.plan_file, policy=policy)
        evidence = review_plan(
            parsed,
            policy=policy,
            target=args.target,
            source_commit=args.source_commit,
        )
        write_evidence(args.output_dir, evidence)
    except ReviewError as error:
        print(f"Databricks plan review failed: {error.category}", file=sys.stderr)
        return 2
    return 0 if evidence["status"] == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())
