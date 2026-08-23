#!/usr/bin/env python3
"""Validate layered engineering-risk policy and its human-readable register."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "governance" / "engineering_risks.json"
MARKDOWN_PATH = REPO_ROOT / "docs" / "engineering_risk_register.md"

_TOP_LEVEL_KEYS = {
    "schema_version",
    "last_reviewed",
    "source_baseline_sha",
    "owners",
    "risks",
}
_OWNER_KEYS = {"display_name", "responsibilities"}
_RISK_KEYS = {
    "id",
    "title",
    "priority",
    "source_status",
    "runtime_status",
    "external_status",
    "owner",
    "summary",
    "source_evidence",
    "pending_evidence",
    "next_action",
    "dependencies",
}
_EVIDENCE_KEYS = {"path", "control"}
_PRIORITIES = {"critical", "high", "medium", "low"}
_SOURCE_STATUSES = {"mitigated", "partial", "open", "not_applicable"}
_EXECUTION_STATUSES = {"evidenced", "pending", "blocked", "not_applicable"}
_DEPENDENCY_PATTERN = re.compile(r"(?:issue|PR) #[1-9][0-9]*\Z")
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_RISK_PATTERN = re.compile(r"R-([0-9]{3})\Z")


class RiskPolicyError(ValueError):
    """Raised when the risk policy or generated register is inconsistent."""


def _require_non_empty_string(value: Any, *, label: str, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise RiskPolicyError(f"{label} must be a bounded non-empty string")
    return value.strip()


def _require_string_list(
    value: Any,
    *,
    label: str,
    allow_empty: bool = False,
    maximum_items: int = 50,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RiskPolicyError(f"{label} must be a list")
    if not allow_empty and not value:
        raise RiskPolicyError(f"{label} must not be empty")
    if len(value) > maximum_items:
        raise RiskPolicyError(f"{label} exceeds the bounded item limit")
    normalized = tuple(
        _require_non_empty_string(item, label=f"{label} item") for item in value
    )
    if len(normalized) != len(set(normalized)):
        raise RiskPolicyError(f"{label} must not contain duplicates")
    return normalized


def _safe_evidence_path(path_text: str, *, repo_root: Path) -> str:
    relative = PurePosixPath(path_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise RiskPolicyError("source evidence path is unsafe")
    path = repo_root.joinpath(*relative.parts)
    if not path.is_file() or path.is_symlink():
        raise RiskPolicyError(f"source evidence path does not exist: {path_text}")
    return relative.as_posix()


def _is_closed(risk: Mapping[str, Any]) -> bool:
    source_complete = risk["source_status"] in {"mitigated", "not_applicable"}
    runtime_complete = risk["runtime_status"] in {"evidenced", "not_applicable"}
    external_complete = risk["external_status"] in {"evidenced", "not_applicable"}
    return source_complete and runtime_complete and external_complete


def load_policy(
    path: Path = POLICY_PATH, *, repo_root: Path = REPO_ROOT
) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RiskPolicyError("engineering risk policy could not be loaded") from None
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_KEYS:
        raise RiskPolicyError("engineering risk policy has an invalid top-level shape")
    if payload["schema_version"] != 1:
        raise RiskPolicyError("engineering risk policy schema version is unsupported")
    try:
        date.fromisoformat(payload["last_reviewed"])
    except (TypeError, ValueError):
        raise RiskPolicyError("last_reviewed must be an ISO date") from None
    if not isinstance(payload["source_baseline_sha"], str) or not _SHA_PATTERN.fullmatch(
        payload["source_baseline_sha"]
    ):
        raise RiskPolicyError("source_baseline_sha must be a lowercase 40-character SHA")

    owners = payload["owners"]
    if not isinstance(owners, dict) or not owners or len(owners) > 20:
        raise RiskPolicyError("owners must be a bounded non-empty object")
    for owner_id, owner in owners.items():
        _require_non_empty_string(owner_id, label="owner ID", maximum=64)
        if not isinstance(owner, dict) or set(owner) != _OWNER_KEYS:
            raise RiskPolicyError(f"owner {owner_id} has an invalid shape")
        _require_non_empty_string(
            owner["display_name"], label=f"owner {owner_id} display name", maximum=100
        )
        _require_string_list(
            owner["responsibilities"],
            label=f"owner {owner_id} responsibilities",
            maximum_items=20,
        )

    risks = payload["risks"]
    if not isinstance(risks, list) or not risks or len(risks) > 100:
        raise RiskPolicyError("risks must be a bounded non-empty list")
    seen_titles: set[str] = set()
    seen_paths_by_risk: dict[str, set[str]] = {}
    for index, risk in enumerate(risks, start=1):
        if not isinstance(risk, dict) or set(risk) != _RISK_KEYS:
            raise RiskPolicyError("risk entry has an invalid shape")
        risk_id = _require_non_empty_string(risk["id"], label="risk ID", maximum=5)
        match = _RISK_PATTERN.fullmatch(risk_id)
        if match is None or int(match.group(1)) != index:
            raise RiskPolicyError("risk IDs must be ordered and contiguous from R-001")
        title = _require_non_empty_string(
            risk["title"], label=f"{risk_id} title", maximum=200
        )
        if title in seen_titles:
            raise RiskPolicyError("risk titles must be unique")
        seen_titles.add(title)
        if risk["priority"] not in _PRIORITIES:
            raise RiskPolicyError(f"{risk_id} has an invalid priority")
        if risk["source_status"] not in _SOURCE_STATUSES:
            raise RiskPolicyError(f"{risk_id} has an invalid source status")
        if risk["runtime_status"] not in _EXECUTION_STATUSES:
            raise RiskPolicyError(f"{risk_id} has an invalid runtime status")
        if risk["external_status"] not in _EXECUTION_STATUSES:
            raise RiskPolicyError(f"{risk_id} has an invalid external status")
        if risk["owner"] not in owners:
            raise RiskPolicyError(f"{risk_id} refers to an unknown owner")
        _require_non_empty_string(
            risk["summary"], label=f"{risk_id} summary", maximum=1200
        )
        _require_non_empty_string(
            risk["next_action"], label=f"{risk_id} next action", maximum=600
        )

        evidence = risk["source_evidence"]
        if not isinstance(evidence, list) or not evidence or len(evidence) > 20:
            raise RiskPolicyError(f"{risk_id} source evidence must be bounded and non-empty")
        seen_paths: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict) or set(item) != _EVIDENCE_KEYS:
                raise RiskPolicyError(f"{risk_id} source evidence has an invalid shape")
            path_text = _safe_evidence_path(
                _require_non_empty_string(
                    item["path"], label=f"{risk_id} evidence path", maximum=300
                ),
                repo_root=repo_root,
            )
            if path_text in seen_paths:
                raise RiskPolicyError(f"{risk_id} repeats a source evidence path")
            seen_paths.add(path_text)
            _require_non_empty_string(
                item["control"], label=f"{risk_id} evidence control", maximum=600
            )
        seen_paths_by_risk[risk_id] = seen_paths

        pending = _require_string_list(
            risk["pending_evidence"],
            label=f"{risk_id} pending evidence",
            allow_empty=True,
            maximum_items=20,
        )
        dependencies = _require_string_list(
            risk["dependencies"],
            label=f"{risk_id} dependencies",
            allow_empty=True,
            maximum_items=20,
        )
        for dependency in dependencies:
            if not _DEPENDENCY_PATTERN.fullmatch(dependency):
                raise RiskPolicyError(f"{risk_id} has an invalid dependency reference")
        if not _is_closed(risk) and not pending:
            raise RiskPolicyError(f"{risk_id} is open but has no pending evidence")
        if _is_closed(risk) and pending:
            raise RiskPolicyError(f"{risk_id} is closed but still lists pending evidence")
        if risk["external_status"] == "blocked" and not dependencies:
            raise RiskPolicyError(f"{risk_id} has blocked external work without a dependency")
        if risk["source_status"] == "mitigated" and len(seen_paths) < 2:
            raise RiskPolicyError(f"{risk_id} source mitigation needs at least two evidence paths")

    return payload


def _relative_markdown_link(path_text: str) -> str:
    return "../" + path_text


def render_markdown(policy: Mapping[str, Any]) -> str:
    owners = policy["owners"]
    risks: Sequence[Mapping[str, Any]] = policy["risks"]
    lines = [
        "# Engineering Risk Register",
        "",
        "This register separates repository-source mitigation from Databricks runtime evidence and external settings evidence. A source control, unit test, local Spark test, or agent review cannot by itself close a workspace or settings risk.",
        "",
        f"- Last reviewed: **{policy['last_reviewed']}**",
        f"- Accepted source baseline: **`{policy['source_baseline_sha']}`**",
        "- Machine-readable source: [`governance/engineering_risks.json`](../governance/engineering_risks.json)",
        "- Validation command: `python3 scripts/validate_engineering_risks.py`",
        "",
        "## Status model",
        "",
        "- `source`: `mitigated`, `partial`, `open`, or `not_applicable`.",
        "- `runtime`: `evidenced`, `pending`, `blocked`, or `not_applicable`.",
        "- `external`: `evidenced`, `pending`, `blocked`, or `not_applicable`.",
        "- A risk is closed only when every applicable layer is complete and no pending evidence remains.",
        "",
        "## Summary",
        "",
        "| ID | Priority | Source | Runtime | External | Risk |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for risk in risks:
        lines.append(
            f"| {risk['id']} | {risk['priority']} | {risk['source_status']} | "
            f"{risk['runtime_status']} | {risk['external_status']} | {risk['title']} |"
        )
    lines.extend(["", "## Detailed risks", ""])

    for risk in risks:
        owner = owners[risk["owner"]]["display_name"]
        lines.extend(
            [
                f"### {risk['id']} — {risk['title']}",
                "",
                f"- Priority: **{risk['priority']}**",
                (
                    "- Layer status: **"
                    f"source={risk['source_status']}; "
                    f"runtime={risk['runtime_status']}; "
                    f"external={risk['external_status']}**"
                ),
                f"- Owner: **{owner}**",
                f"- Summary: {risk['summary']}",
                "- Source evidence:",
            ]
        )
        for item in risk["source_evidence"]:
            lines.append(
                f"  - [`{item['path']}`]({_relative_markdown_link(item['path'])}) — "
                f"{item['control']}"
            )
        lines.append("- Pending evidence:")
        for item in risk["pending_evidence"]:
            lines.append(f"  - {item}")
        if risk["dependencies"]:
            lines.append("- Dependencies: " + ", ".join(risk["dependencies"]))
        else:
            lines.append("- Dependencies: None.")
        lines.extend([f"- Next action: {risk['next_action']}", ""])

    lines.extend(
        [
            "## Closure rule",
            "",
            "Closing a risk requires the machine-readable layer states and the Markdown register to change together, all linked source evidence to remain resolvable, applicable runtime or external evidence to be retained, rollback implications to be recorded, and a human reviewer to accept the exact change. Repository-source mitigation is not runtime closure. Agent confidence is not closure.",
            "",
        ]
    )
    return "\n".join(lines)


def validate_markdown(
    policy: Mapping[str, Any], path: Path = MARKDOWN_PATH
) -> None:
    try:
        observed = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise RiskPolicyError("engineering risk Markdown could not be loaded") from None
    expected = render_markdown(policy)
    if observed != expected:
        raise RiskPolicyError(
            "engineering risk Markdown is stale; regenerate it with --write"
        )


def summary(policy: Mapping[str, Any]) -> dict[str, Any]:
    risks: Sequence[Mapping[str, Any]] = policy["risks"]
    return {
        "schema_version": policy["schema_version"],
        "last_reviewed": policy["last_reviewed"],
        "source_baseline_sha": policy["source_baseline_sha"],
        "risk_count": len(risks),
        "closed_count": sum(_is_closed(risk) for risk in risks),
        "source_mitigated_count": sum(
            risk["source_status"] == "mitigated" for risk in risks
        ),
        "source_partial_or_open_count": sum(
            risk["source_status"] in {"partial", "open"} for risk in risks
        ),
        "runtime_pending_or_blocked_count": sum(
            risk["runtime_status"] in {"pending", "blocked"} for risk in risks
        ),
        "external_pending_or_blocked_count": sum(
            risk["external_status"] in {"pending", "blocked"} for risk in risks
        ),
        "status": "valid",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate the Markdown register after validating the JSON policy.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        policy = load_policy()
        if args.write:
            MARKDOWN_PATH.write_text(render_markdown(policy), encoding="utf-8")
        validate_markdown(policy)
    except RiskPolicyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary(policy), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
