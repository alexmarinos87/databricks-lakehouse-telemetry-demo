#!/usr/bin/env python3
"""Validate the coordinated Python, Java, Spark, Py4J and DBR baseline."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "governance" / "runtime_compatibility.json"

_REQUIRED_TOP_LEVEL = {
    "schema_version",
    "active_baseline",
    "upgrade_candidates",
    "rules",
}
_REQUIRED_BASELINE = {
    "status",
    "python",
    "java",
    "pyspark",
    "py4j",
    "databricks_runtime",
    "github_runner",
    "evidence",
}
_REQUIRED_CANDIDATE = {
    "id",
    "status",
    "requested_changes",
    "required_evidence",
}
_REQUIRED_RULES = {
    "partial_major_upgrade",
    "floating_runtime_version",
    "unhashed_python_dependency",
    "merge_without_exact_matrix_evidence",
    "production_upgrade_before_dev_runtime_evidence",
}
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+._A-Za-z0-9]*)?\Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy(path: Path = POLICY_PATH) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("runtime compatibility policy could not be loaded") from None
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_TOP_LEVEL:
        raise ValueError("runtime compatibility policy has an invalid top-level shape")
    if payload["schema_version"] != 1:
        raise ValueError("runtime compatibility schema version is unsupported")
    baseline = payload["active_baseline"]
    if not isinstance(baseline, dict) or set(baseline) != _REQUIRED_BASELINE:
        raise ValueError("active compatibility baseline has an invalid shape")
    if baseline["status"] != "accepted_current":
        raise ValueError("active compatibility baseline must be accepted_current")
    for key in ("python", "java", "pyspark", "py4j"):
        if not isinstance(baseline[key], str) or not _VERSION.fullmatch(baseline[key]):
            raise ValueError(f"active compatibility baseline has invalid {key}")
    if not isinstance(baseline["evidence"], list) or not baseline["evidence"]:
        raise ValueError("active compatibility baseline must name evidence files")
    for relative in baseline["evidence"]:
        if not isinstance(relative, str) or not (REPO_ROOT / relative).is_file():
            raise ValueError("active compatibility evidence path does not exist")
    candidates = payload["upgrade_candidates"]
    if not isinstance(candidates, list) or not candidates or len(candidates) > 20:
        raise ValueError("upgrade candidates must be a bounded non-empty list")
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != _REQUIRED_CANDIDATE:
            raise ValueError("upgrade candidate has an invalid shape")
        candidate_id = candidate["id"]
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen_ids:
            raise ValueError("upgrade candidate IDs must be unique")
        seen_ids.add(candidate_id)
        if candidate["status"] != "blocked_pending_complete_matrix":
            raise ValueError("unaccepted upgrade candidate must remain blocked")
        if not isinstance(candidate["requested_changes"], dict) or not candidate[
            "requested_changes"
        ]:
            raise ValueError("upgrade candidate must request at least one change")
        evidence = candidate["required_evidence"]
        if (
            not isinstance(evidence, list)
            or not evidence
            or len(evidence) != len(set(evidence))
        ):
            raise ValueError("upgrade candidate evidence must be unique and non-empty")
    rules = payload["rules"]
    if not isinstance(rules, dict) or set(rules) != _REQUIRED_RULES:
        raise ValueError("compatibility rules have an invalid shape")
    if any(value != "prohibited" for value in rules.values()):
        raise ValueError("compatibility safety rules must remain prohibited")
    return payload


def _single_match(pattern: str, text: str, *, label: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise ValueError(f"could not resolve one {label} from repository evidence")
    return unique[0]


def observed_baseline() -> dict[str, str]:
    docker_ci = (REPO_ROOT / "Dockerfile.ci").read_text(encoding="utf-8")
    docker_spark = (REPO_ROOT / "Dockerfile.spark-ci").read_text(encoding="utf-8")
    requirements = (REPO_ROOT / "requirements-spark.txt").read_text(encoding="utf-8")
    bundle = (REPO_ROOT / "databricks.yml").read_text(encoding="utf-8")
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "spark-runtime.yml"
    ).read_text(encoding="utf-8")

    python_ci = _single_match(
        r"^FROM python:([0-9]+\.[0-9]+)(?:[-@])",
        docker_ci,
        label="CI Python version",
    )
    python_spark = _single_match(
        r"^FROM python:([0-9]+\.[0-9]+)(?:[-@])",
        docker_spark,
        label="Spark Python version",
    )
    if python_ci != python_spark:
        raise ValueError("CI and Spark images use different Python minor versions")
    java = _single_match(
        r"openjdk-([0-9]+)-jre-headless",
        docker_spark,
        label="Java major version",
    )
    pyspark = _single_match(
        r"^pyspark==([^\s\\]+)", requirements, label="PySpark version"
    )
    py4j = _single_match(
        r"^py4j==([^\s\\]+)", requirements, label="Py4J version"
    )
    if "--hash=sha256:" not in requirements:
        raise ValueError("Spark requirements must use SHA-256 hashes")
    databricks_runtime = _single_match(
        r"^    default: (15\.4\.x-scala2\.12)$",
        bundle.split("  spark_version:\n", 1)[1].split("  node_type_id:\n", 1)[0],
        label="Databricks Runtime",
    )
    runner = _single_match(
        r"runs-on: (ubuntu-[0-9]+\.[0-9]+)",
        workflow,
        label="GitHub runner",
    )
    return {
        "python": python_ci,
        "java": java,
        "pyspark": pyspark,
        "py4j": py4j,
        "databricks_runtime": databricks_runtime,
        "github_runner": runner,
    }


def validate_repository(policy: Mapping[str, Any]) -> dict[str, Any]:
    observed = observed_baseline()
    expected = {
        key: policy["active_baseline"][key]
        for key in (
            "python",
            "java",
            "pyspark",
            "py4j",
            "databricks_runtime",
            "github_runner",
        )
    }
    if observed != expected:
        raise ValueError("repository runtime evidence does not match active baseline")
    evidence_hashes = {
        relative: _sha256(REPO_ROOT / relative)
        for relative in policy["active_baseline"]["evidence"]
    }
    fingerprint_source = json.dumps(
        {"baseline": expected, "evidence_hashes": evidence_hashes},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": policy["schema_version"],
        "status": "valid",
        "baseline": expected,
        "baseline_fingerprint": hashlib.sha256(fingerprint_source).hexdigest(),
        "evidence_hashes": evidence_hashes,
        "blocked_candidate_count": len(policy["upgrade_candidates"]),
    }


def main() -> int:
    print(json.dumps(validate_repository(load_policy()), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
