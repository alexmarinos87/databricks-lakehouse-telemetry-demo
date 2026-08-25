#!/usr/bin/env python3
"""Build a bounded, dry-run-only retention candidate manifest."""
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
DEFAULT_POLICY = REPO_ROOT / "governance" / "history_retention_policy.json"
EXPECTED_REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
OUTPUT_JSON = "history-retention-plan.json"
OUTPUT_MARKDOWN = "history-retention-plan.md"
MAX_INPUT_BYTES = 2_000_000
MAX_DATASETS = 20
MAX_ENTRIES = 5_000
MAX_FINDINGS = 128
MAX_STRING_BYTES = 256
FUTURE_TOLERANCE = timedelta(minutes=5)
_ALLOWED_STATES = {"committed", "failed", "started"}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class RetentionError(RuntimeError):
    """Stable invalid-input category safe to expose in logs."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _expect(condition: bool, category: str) -> None:
    if not condition:
        raise RetentionError(category)


def _read_regular_bytes(path: Path, *, category: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise RetentionError(category)
        size = path.stat().st_size
        if size < 1 or size > MAX_INPUT_BYTES:
            raise RetentionError(f"{category}_size_invalid")
        value = path.read_bytes()
    except RetentionError:
        raise
    except OSError:
        raise RetentionError(f"{category}_unreadable") from None
    if len(value) > MAX_INPUT_BYTES:
        raise RetentionError(f"{category}_size_invalid")
    return value


def _parse_object(payload: bytes, *, category: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RetentionError(f"{category}_invalid_json") from None
    if not isinstance(parsed, dict):
        raise RetentionError(f"{category}_shape_invalid")
    return parsed


def _exact_mapping(value: Any, keys: set[str], category: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RetentionError(category)
    return value


def _string(value: Any, *, category: str, maximum: int = MAX_STRING_BYTES) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise RetentionError(category)
    return value


def _identifier(value: Any, category: str) -> str:
    text = _string(value, category=category, maximum=128)
    if not _IDENTIFIER.fullmatch(text):
        raise RetentionError(category)
    return text


def _fingerprint(value: Any, category: str) -> str:
    text = _string(value, category=category, maximum=71)
    if not _SHA256.fullmatch(text):
        raise RetentionError(category)
    return text


def _timestamp(value: Any, category: str) -> datetime:
    text = _string(value, category=category, maximum=64)
    if not text.endswith("Z"):
        raise RetentionError(category)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise RetentionError(category) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise RetentionError(category)
    return parsed.astimezone(timezone.utc)


def _non_negative_int(value: Any, *, category: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise RetentionError(category)
    return value


def _positive_number(value: Any, *, category: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetentionError(category)
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0 or parsed > maximum:
        raise RetentionError(category)
    return parsed


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    raw = _read_regular_bytes(path, category="retention_policy_file_invalid")
    document = _parse_object(raw, category="retention_policy")
    _exact_mapping(
        document,
        {
            "schema_version",
            "dry_run_only",
            "inventory_max_age_hours",
            "minimum_recovery_window_hours",
            "max_candidates_per_run",
            "datasets",
        },
        "retention_policy_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "retention_policy_version_mismatch")
    _expect(document.get("dry_run_only") is True, "retention_policy_must_be_dry_run_only")
    inventory_age = _positive_number(
        document.get("inventory_max_age_hours"),
        category="inventory_max_age_hours_invalid",
        maximum=720,
    )
    recovery_window = _positive_number(
        document.get("minimum_recovery_window_hours"),
        category="minimum_recovery_window_hours_invalid",
        maximum=8_760,
    )
    maximum_candidates = _non_negative_int(
        document.get("max_candidates_per_run"),
        category="max_candidates_per_run_invalid",
        maximum=MAX_ENTRIES,
    )
    _expect(maximum_candidates > 0, "max_candidates_per_run_invalid")
    datasets = document.get("datasets")
    if not isinstance(datasets, dict) or not datasets or len(datasets) > MAX_DATASETS:
        raise RetentionError("retention_policy_datasets_invalid")
    normalized: dict[str, dict[str, int]] = {}
    for raw_id, raw_policy in datasets.items():
        dataset_id = _identifier(raw_id, "retention_policy_dataset_id_invalid")
        item = _exact_mapping(
            raw_policy,
            {"retention_days", "minimum_committed_entries"},
            "retention_dataset_policy_shape_invalid",
        )
        retention_days = _non_negative_int(
            item.get("retention_days"),
            category="retention_days_invalid",
            maximum=3_650,
        )
        minimum_committed = _non_negative_int(
            item.get("minimum_committed_entries"),
            category="minimum_committed_entries_invalid",
            maximum=100,
        )
        _expect(retention_days >= 30, "retention_days_invalid")
        _expect(minimum_committed >= 1, "minimum_committed_entries_invalid")
        normalized[dataset_id] = {
            "retention_days": retention_days,
            "minimum_committed_entries": minimum_committed,
        }
    return {
        "raw_sha256": _sha256(raw),
        "inventory_max_age_hours": inventory_age,
        "minimum_recovery_window_hours": recovery_window,
        "max_candidates_per_run": maximum_candidates,
        "datasets": normalized,
    }


def _load_inventory(path: Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    raw = _read_regular_bytes(path, category="retention_inventory_file_invalid")
    document = _parse_object(raw, category="retention_inventory")
    _exact_mapping(
        document,
        {
            "schema_version",
            "target",
            "repository",
            "source_commit",
            "captured_at_utc",
            "workspace_fingerprint",
            "datasets",
        },
        "retention_inventory_shape_invalid",
    )
    _expect(document.get("schema_version") == 1, "retention_inventory_version_mismatch")
    _expect(document.get("target") == "dev", "retention_inventory_target_must_be_dev")
    _expect(
        document.get("repository") == EXPECTED_REPOSITORY,
        "retention_inventory_repository_mismatch",
    )
    source_commit = _string(
        document.get("source_commit"),
        category="retention_inventory_source_commit_invalid",
        maximum=40,
    )
    _expect(bool(_COMMIT.fullmatch(source_commit)), "retention_inventory_source_commit_invalid")
    captured_at = _timestamp(
        document.get("captured_at_utc"), "retention_inventory_capture_timestamp_invalid"
    )
    workspace_fingerprint = _fingerprint(
        document.get("workspace_fingerprint"),
        "retention_inventory_workspace_fingerprint_invalid",
    )
    datasets = document.get("datasets")
    if not isinstance(datasets, list) or not datasets or len(datasets) > MAX_DATASETS:
        raise RetentionError("retention_inventory_datasets_invalid")
    normalized: dict[str, list[dict[str, Any]]] = {}
    total_entries = 0
    for raw_dataset in datasets:
        dataset = _exact_mapping(
            raw_dataset,
            {"dataset_id", "entries"},
            "retention_inventory_dataset_shape_invalid",
        )
        dataset_id = _identifier(
            dataset.get("dataset_id"), "retention_inventory_dataset_id_invalid"
        )
        _expect(dataset_id not in normalized, "retention_inventory_dataset_duplicate")
        entries = dataset.get("entries")
        if not isinstance(entries, list):
            raise RetentionError("retention_inventory_entries_invalid")
        total_entries += len(entries)
        _expect(total_entries <= MAX_ENTRIES, "retention_inventory_entry_limit_exceeded")
        seen_entries: set[str] = set()
        current_count = 0
        normalized_entries: list[dict[str, Any]] = []
        for raw_entry in entries:
            entry = _exact_mapping(
                raw_entry,
                {
                    "entry_id",
                    "entry_fingerprint",
                    "created_at_utc",
                    "state",
                    "current",
                    "recovery_protected",
                    "byte_count",
                },
                "retention_inventory_entry_shape_invalid",
            )
            entry_id = _identifier(entry.get("entry_id"), "retention_entry_id_invalid")
            _expect(entry_id not in seen_entries, "retention_entry_id_duplicate")
            seen_entries.add(entry_id)
            state = _string(entry.get("state"), category="retention_entry_state_invalid")
            _expect(state in _ALLOWED_STATES, "retention_entry_state_invalid")
            current = entry.get("current")
            recovery_protected = entry.get("recovery_protected")
            _expect(isinstance(current, bool), "retention_entry_current_invalid")
            _expect(
                isinstance(recovery_protected, bool),
                "retention_entry_recovery_protected_invalid",
            )
            if current:
                current_count += 1
                _expect(state == "committed", "retention_current_entry_must_be_committed")
            normalized_entries.append(
                {
                    "entry_id": entry_id,
                    "entry_fingerprint": _fingerprint(
                        entry.get("entry_fingerprint"),
                        "retention_entry_fingerprint_invalid",
                    ),
                    "created_at": _timestamp(
                        entry.get("created_at_utc"),
                        "retention_entry_timestamp_invalid",
                    ),
                    "created_at_text": entry["created_at_utc"],
                    "state": state,
                    "current": current,
                    "recovery_protected": recovery_protected,
                    "byte_count": _non_negative_int(
                        entry.get("byte_count"),
                        category="retention_entry_byte_count_invalid",
                        maximum=10**15,
                    ),
                }
            )
        _expect(current_count <= 1, "retention_inventory_multiple_current_entries")
        normalized[dataset_id] = normalized_entries
    _expect(
        set(normalized) == set(policy["datasets"]),
        "retention_inventory_dataset_coverage_mismatch",
    )
    return {
        "raw_sha256": _sha256(raw),
        "source_commit": source_commit,
        "captured_at": captured_at,
        "captured_at_text": document["captured_at_utc"],
        "workspace_fingerprint": workspace_fingerprint,
        "datasets": normalized,
    }


def _finding(category: str, dataset_id: str | None = None) -> dict[str, str]:
    value = {"category": category}
    if dataset_id is not None:
        value["dataset_id"] = dataset_id
    return value


def build_plan(
    policy_path: Path,
    inventory_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    inventory = _load_inventory(inventory_path, policy)
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise RetentionError("retention_verification_time_must_be_utc")
    reference_time = reference_time.astimezone(timezone.utc)
    findings: list[dict[str, str]] = []
    if inventory["captured_at"] > reference_time + FUTURE_TOLERANCE:
        findings.append(_finding("retention_inventory_capture_is_in_future"))
    if inventory["captured_at"] < reference_time - timedelta(
        hours=policy["inventory_max_age_hours"]
    ):
        findings.append(_finding("retention_inventory_capture_is_stale"))

    candidates: list[dict[str, Any]] = []
    dataset_summaries: list[dict[str, Any]] = []
    for dataset_id in sorted(policy["datasets"]):
        dataset_policy = policy["datasets"][dataset_id]
        entries = inventory["datasets"][dataset_id]
        committed = sorted(
            (entry for entry in entries if entry["state"] == "committed"),
            key=lambda entry: (entry["created_at"], entry["entry_id"]),
            reverse=True,
        )
        keep_ids = {
            entry["entry_id"]
            for entry in committed[: dataset_policy["minimum_committed_entries"]]
        }
        effective_hours = max(
            dataset_policy["retention_days"] * 24,
            policy["minimum_recovery_window_hours"],
        )
        cutoff = inventory["captured_at"] - timedelta(hours=effective_hours)
        protected = {
            "current": 0,
            "recovery": 0,
            "started": 0,
            "recent": 0,
            "minimum_committed": 0,
        }
        eligible = 0
        bytes_eligible = 0
        for entry in sorted(entries, key=lambda item: (item["created_at"], item["entry_id"])):
            reason: str | None = None
            if entry["current"]:
                reason = "current"
            elif entry["recovery_protected"]:
                reason = "recovery"
            elif entry["state"] == "started":
                reason = "started"
            elif entry["created_at"] >= cutoff:
                reason = "recent"
            elif entry["state"] == "committed" and entry["entry_id"] in keep_ids:
                reason = "minimum_committed"
            if reason is not None:
                protected[reason] += 1
                continue
            eligible += 1
            bytes_eligible += entry["byte_count"]
            candidates.append(
                {
                    "dataset_id": dataset_id,
                    "entry_id": entry["entry_id"],
                    "entry_fingerprint": entry["entry_fingerprint"],
                    "created_at_utc": entry["created_at_text"],
                    "state": entry["state"],
                    "byte_count": entry["byte_count"],
                    "reason": "older_than_retention_and_unprotected",
                }
            )
        dataset_summaries.append(
            {
                "dataset_id": dataset_id,
                "retention_days": dataset_policy["retention_days"],
                "minimum_committed_entries": dataset_policy["minimum_committed_entries"],
                "inventory_entries": len(entries),
                "eligible_candidates": eligible,
                "eligible_bytes": bytes_eligible,
                "protected_counts": protected,
            }
        )
    if len(candidates) > policy["max_candidates_per_run"]:
        findings.append(_finding("retention_candidate_count_exceeds_policy"))
    if len(findings) > MAX_FINDINGS:
        findings = findings[: MAX_FINDINGS - 1] + [_finding("retention_findings_truncated")]
    status = "planned" if not findings else "blocked"
    retained_candidates = candidates if status == "planned" else []
    return {
        "schema_version": 1,
        "status": status,
        "generated_at_utc": _utc_text(reference_time),
        "target": "dev",
        "repository": EXPECTED_REPOSITORY,
        "source_commit": inventory["source_commit"],
        "captured_at_utc": inventory["captured_at_text"],
        "policy_sha256": policy["raw_sha256"],
        "inventory_sha256": inventory["raw_sha256"],
        "workspace_fingerprint": inventory["workspace_fingerprint"],
        "dry_run_only": True,
        "minimum_recovery_window_hours": policy["minimum_recovery_window_hours"],
        "max_candidates_per_run": policy["max_candidates_per_run"],
        "eligible_candidate_count": len(candidates),
        "eligible_byte_count": sum(item["byte_count"] for item in candidates),
        "datasets": dataset_summaries,
        "candidates": retained_candidates,
        "findings": sorted(findings, key=lambda item: (item["category"], item.get("dataset_id", ""))),
    }


def render_markdown(plan: Mapping[str, Any]) -> str:
    lines = [
        "# History retention dry-run plan",
        "",
        f"- Status: **{plan['status']}**",
        f"- Target: `{plan['target']}`",
        f"- Source commit: `{plan['source_commit']}`",
        f"- Inventory captured: `{plan['captured_at_utc']}`",
        f"- Dry run only: `{str(plan['dry_run_only']).lower()}`",
        f"- Eligible candidates: `{plan['eligible_candidate_count']}`",
        f"- Eligible bytes: `{plan['eligible_byte_count']}`",
        "",
        "## Dataset summary",
        "",
        "| Dataset | Entries | Eligible | Retention days | Minimum committed kept |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for dataset in plan["datasets"]:
        lines.append(
            f"| `{dataset['dataset_id']}` | `{dataset['inventory_entries']}` | "
            f"`{dataset['eligible_candidates']}` | `{dataset['retention_days']}` | "
            f"`{dataset['minimum_committed_entries']}` |"
        )
    lines.extend(["", "## Findings", ""])
    if plan["findings"]:
        for finding in plan["findings"]:
            suffix = f" (`{finding['dataset_id']}`)" if finding.get("dataset_id") else ""
            lines.append(f"- `{finding['category']}`{suffix}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "This file is a review manifest only. It contains no deletion, VACUUM, "
            "DROP, SQL execution, provider command, or scheduler instruction.",
            "",
        ]
    )
    return "\n".join(lines)


def _prepare_output_directory(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise RetentionError("retention_output_directory_is_symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise RetentionError("retention_output_directory_unavailable") from None
    if path.is_symlink() or not path.is_dir():
        raise RetentionError("retention_output_directory_invalid")
    return path


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RetentionError("retention_temporary_output_exists")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise RetentionError("retention_output_path_invalid")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RetentionError("retention_output_write_failed") from None


def write_outputs(output_directory: Path, plan: Mapping[str, Any]) -> None:
    directory = _prepare_output_directory(output_directory)
    _write_atomic(
        directory / OUTPUT_JSON,
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
    )
    _write_atomic(directory / OUTPUT_MARKDOWN, render_markdown(plan))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan = build_plan(args.policy, args.inventory)
        write_outputs(args.output_dir, plan)
    except RetentionError as error:
        print(f"History retention planning failed: {error.category}", file=sys.stderr)
        return 2
    print(
        f"History retention plan {plan['status']}: "
        f"eligible={plan['eligible_candidate_count']}"
    )
    return 0 if plan["status"] == "planned" else 1


if __name__ == "__main__":
    sys.exit(main())
