#!/usr/bin/env python3
"""Render the governed engineering risk register from its JSON source."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTER = REPO_ROOT / "governance" / "engineering_risk_register.json"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "engineering_risk_register.md"

PRIORITY_LABELS = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
}
SOURCE_STATUS_LABELS = {
    "source_mitigated": "Source mitigated",
    "source_gap_open": "Source gap open",
    "not_source_controlled": "Not source-controlled",
}
RUNTIME_STATUS_LABELS = {
    "runtime_evidence_pending": "Runtime evidence pending",
    "externally_blocked": "Externally blocked",
    "not_applicable": "Not applicable",
}


def load_register(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("engineering risk register could not be parsed") from None
    if not isinstance(payload, dict):
        raise ValueError("engineering risk register must be a JSON object")
    return payload


def render_register(register: Mapping[str, Any]) -> str:
    risks = register["risks"]
    lines = [
        "# Engineering Risk Register",
        "",
        f"**Evidence reviewed:** {register['as_of_date']}",
        "",
        str(register["repository_evidence_boundary"]),
        "",
        "Every risk remains open until its closure rule is met. A source mitigation "
        "removes or bounds a repository defect; it does not prove effective Databricks, "
        "GitHub, notification, ownership, or consumer behaviour.",
        "",
        "The complete residual-risk statements, current evidence paths, external "
        "dependencies, and next evidence requirements are governed in "
        "[`governance/engineering_risk_register.json`](../governance/engineering_risk_register.json).",
        "",
        "## Status model",
        "",
        "| Dimension | Value | Meaning |",
        "| --- | --- | --- |",
        "| Source | Source mitigated | Repository code, configuration, tests, or policy address the identified source defect. |",
        "| Source | Source gap open | A repository-owned design or implementation gap remains. |",
        "| Source | Not source-controlled | The relevant control is an external setting; repository automation can only describe or bootstrap it. |",
        "| Runtime | Runtime evidence pending | The source control exists, but effective Databricks or consumer behaviour has not been proved. |",
        "| Runtime | Externally blocked | Required workspace, identity, repository-setting, or notification bootstrap is unavailable or incomplete. |",
        "| Runtime | Not applicable | No external runtime evidence is required for that risk. |",
        "",
        "## Current risks",
        "",
        "| Risk | Priority | Source status | Runtime status | Title |",
        "| --- | --- | --- | --- | --- |",
    ]
    for risk in risks:
        lines.append(
            "| {id} | {priority} | {source} | {runtime} | {title} |".format(
                id=risk["id"],
                priority=PRIORITY_LABELS[risk["priority"]],
                source=SOURCE_STATUS_LABELS[risk["source_status"]],
                runtime=RUNTIME_STATUS_LABELS[risk["runtime_status"]],
                title=risk["title"].replace("|", r"\|"),
            )
        )

    blocked = [
        risk for risk in risks if risk["runtime_status"] == "externally_blocked"
    ]
    lines.extend(["", "## External blockers", ""])
    for risk in blocked:
        dependencies = ", ".join(
            f"`{dependency}`" for dependency in risk["external_dependencies"]
        )
        lines.append(f"- **{risk['id']}** — {dependencies}.")

    lines.extend(
        [
            "",
            "## Closure rule",
            "",
            "A risk may be closed only through one durable review record that includes:",
            "",
            "1. the accepted source change or the verified external setting;",
            "2. reproducible local and, where applicable, effective runtime evidence;",
            "3. residual-risk and rollback implications;",
            "4. the named human reviewer who accepted closure;",
            "5. an update to both the JSON source and this rendered document.",
            "",
            "Agent confidence, a green source-only test, a stale pull-request reference, or "
            "an intended-but-unapplied setting is not closure.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rendered = render_register(load_register(args.register))
    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError:
            print("engineering risk register Markdown is missing", file=sys.stderr)
            return 1
        if current != rendered:
            print(
                "engineering risk register Markdown is out of date; rerun the renderer",
                file=sys.stderr,
            )
            return 1
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
