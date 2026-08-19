#!/usr/bin/env python3
"""Generate structured evidence for a human pull-request review."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

SURFACE_RULES = (
    (("databricks.yml", "resources/", ".github/workflows/", "Dockerfile"), "deployment/infrastructure", 100),
    (("notebooks/", "src/"), "runtime/data flow", 90),
    (("scripts/",), "automation/tooling", 80),
    (("sql/",), "reporting/data contract", 70),
    (("tests/",), "test evidence", 50),
    (("AGENTS.md", "README.md", "docs/", ".github/pull_request_template.md"), "governance/docs", 30),
)

INSPECTION_GUIDANCE = {
    "deployment/infrastructure": "resolved target values, permissions, side effects, rollback and environment isolation",
    "runtime/data flow": "grain, keys, nulls, retries, partial writes, reconciliation and runtime cost",
    "automation/tooling": "bounded execution, error handling, secret leakage and idempotency",
    "reporting/data contract": "query semantics, access model, compatibility and empty/null behaviour",
    "test evidence": "whether assertions execute behaviour and cover failure paths rather than only source text",
    "governance/docs": "whether instructions match executable behaviour and preserve the human acceptance gate",
    "other": "contract compatibility, side effects and missing validation",
}


def run_git(*args: str, repo_root: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.rstrip()


def classify_path(path: str) -> tuple[str, int]:
    for prefixes, label, priority in SURFACE_RULES:
        if any(path == prefix or path.startswith(prefix) for prefix in prefixes):
            return label, priority
    return "other", 10


def parse_numstat(output: str) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for line in output.splitlines():
        if not line:
            continue
        additions_text, deletions_text, path = line.split("\t", 2)
        category, priority = classify_path(path)
        files.append(
            {
                "path": path,
                "additions": None if additions_text == "-" else int(additions_text),
                "deletions": None if deletions_text == "-" else int(deletions_text),
                "category": category,
                "priority": priority,
            }
        )
    return files


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_package(
    base_ref: str,
    head_ref: str,
    tested_ref: str,
    validation_status: str,
    repo_root: Path = REPO_ROOT,
) -> str:
    base_tip_sha = run_git("rev-parse", base_ref, repo_root=repo_root)
    head_sha = run_git("rev-parse", head_ref, repo_root=repo_root)
    tested_sha = run_git("rev-parse", tested_ref, repo_root=repo_root)
    current_head_sha = run_git("rev-parse", "HEAD", repo_root=repo_root)
    merge_base_sha = run_git("merge-base", base_ref, head_ref, repo_root=repo_root)
    branch = os.environ.get("GITHUB_HEAD_REF") or run_git(
        "rev-parse", "--abbrev-ref", head_ref, repo_root=repo_root
    )
    status_lines = run_git("status", "--porcelain", repo_root=repo_root).splitlines()
    staged_paths = run_git("diff", "--cached", "--name-only", repo_root=repo_root).splitlines()
    include_index = head_sha == current_head_sha and bool(staged_paths)

    commits = run_git(
        "log", "--format=- `%h` %s", f"{merge_base_sha}..{head_sha}", repo_root=repo_root
    )
    if include_index:
        comparison_target = f"staged index (HEAD plus {len(staged_paths)} staged path(s))"
        numstat = run_git(
            "diff", "--cached", "--no-renames", "--numstat", merge_base_sha, repo_root=repo_root
        )
    else:
        comparison_target = "committed candidate head"
        numstat = run_git(
            "diff", "--no-renames", "--numstat", merge_base_sha, head_sha, repo_root=repo_root
        )
    files = parse_numstat(numstat)
    changed_surfaces = sorted({str(item["category"]) for item in files})
    total_additions = sum(int(item["additions"] or 0) for item in files)
    total_deletions = sum(int(item["deletions"] or 0) for item in files)

    lines = [
        "# Morning Review Package",
        "",
        "> Structured review evidence only. This package is not an approval and does not authorize merge or deployment.",
        "",
        "## Change Identity",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Generated | {datetime.now(timezone.utc).isoformat()} |",
        f"| Branch | `{markdown_cell(branch)}` |",
        f"| Base ref tip | `{markdown_cell(base_ref)}` at `{base_tip_sha}` |",
        f"| Merge base | `{merge_base_sha}` |",
        f"| Candidate head | `{markdown_cell(head_ref)}` at `{head_sha}` |",
        f"| Tested checkout | `{markdown_cell(tested_ref)}` at `{tested_sha}` |",
        f"| Comparison target | {comparison_target} |",
        f"| Scope | {len(files)} files, +{total_additions}/-{total_deletions} |",
        f"| Validation status supplied by caller | {markdown_cell(validation_status)} |",
        f"| Worktree | {'clean' if not status_lines else f'dirty ({len(status_lines)} entries)'} |",
        "",
    ]

    if status_lines:
        lines.extend(
            [
                "### Worktree Status",
                "",
                "Staged entries are included when the comparison target is the staged index. "
                "Unstaged and untracked entries remain outside the candidate comparison.",
                "",
                *[f"- `{markdown_cell(line)}`" for line in status_lines],
                "",
            ]
        )

    lines.extend(["## Commits", "", commits or "- No commits in comparison.", ""])
    lines.extend(["## Changed Surfaces", ""])
    lines.extend([f"- {surface}" for surface in changed_surfaces] or ["- None"])
    lines.extend(["", "## Changed Files", "", "| Path | Surface | Added | Deleted |", "| --- | --- | ---: | ---: |"])

    for item in sorted(files, key=lambda entry: str(entry["path"])):
        added = "binary" if item["additions"] is None else item["additions"]
        deleted = "binary" if item["deletions"] is None else item["deletions"]
        lines.append(
            f"| `{markdown_cell(item['path'])}` | {item['category']} | {added} | {deleted} |"
        )

    lines.extend(["", "## Heuristic Changed-File Shortlist", ""])
    prioritized = sorted(
        files,
        key=lambda item: (-int(item["priority"]), -(int(item["additions"] or 0) + int(item["deletions"] or 0)), str(item["path"])),
    )[:10]
    for index, item in enumerate(prioritized, start=1):
        guidance = INSPECTION_GUIDANCE[str(item["category"])]
        lines.append(f"{index}. `{item['path']}` — inspect {guidance}.")
    if not prioritized:
        lines.append("1. No changed files detected.")

    lines.extend(
        [
            "",
            "This ordering is mechanical, not an acceptance judgement. The final reviewer must "
            "replace or augment it with up to ten exact `path:line` inspection points and reasons.",
        ]
    )

    lines.extend(
        [
            "",
            "## Required Reviewer Analysis",
            "",
            "- [ ] Explain what changed and why this design was selected.",
            "- [ ] Trace the important architecture and data/control paths.",
            "- [ ] Identify state changes, external side effects and affected users/systems.",
            "- [ ] Challenge assumptions and production failure modes.",
            "- [ ] Review permissions, secrets, trust boundaries and data exposure.",
            "- [ ] Review observability, performance and cost implications.",
            "- [ ] Record exact automated evidence, failures and checks not run.",
            "- [ ] Confirm rollback, recovery, backfill and reconciliation steps.",
            "- [ ] Record unresolved questions, debt, owner and follow-up date.",
            "",
            "## Automated Evidence",
            "",
            "- Required local gate: `scripts/run_acceptance_checks.sh`",
            "- CI gate: inspect the pull request's `validate` job and logs.",
            "- Runtime evidence: not inferred by this generator; attach it explicitly when required.",
            "",
            "## Human Acceptance — Human Reviewer Only",
            "",
            "- [ ] I understand the architecture, state changes and important paths.",
            "- [ ] I reviewed failure modes, rollback, permissions and operational/cost implications.",
            "- [ ] I accept the stated evidence limitations and unresolved risks.",
            "- [ ] I accept this exact change for merge.",
            "",
            "Decision: **Pending**",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main", help="Base ref used to find the merge base.")
    parser.add_argument("--head", default="HEAD", help="Head ref to review.")
    parser.add_argument(
        "--tested-ref",
        default="HEAD",
        help="Checkout or merge ref on which validation actually ran.",
    )
    parser.add_argument("--output", default="-", help="Output path, or - for stdout.")
    parser.add_argument("--validation-status", default="not supplied")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        package = render_package(
            args.base,
            args.head,
            args.tested_ref,
            args.validation_status,
        )
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR: unable to generate review package: {exc}", file=sys.stderr)
        return 1

    if args.output == "-":
        print(package)
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(package, encoding="utf-8")
        print(f"Review package written to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
