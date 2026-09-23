#!/usr/bin/env python3
"""Bind a source snapshot to the CI checkout; verify its downloaded bytes offline."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo.portfolio_snapshot import (  # noqa: E402
    PortfolioSnapshotError,
    build_portfolio_snapshot,
    render_portfolio_snapshot_markdown,
)
from lakehouse_demo.repository_files import (  # noqa: E402
    RepositoryFileError,
    normalize_repository_root,
    read_repository_files,
    verify_repository_files_unchanged,
    write_new_text_package,
)

REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
SNAPSHOT_FILES = ("portfolio-snapshot.json", "portfolio-snapshot.md")
ARTIFACT_FILES = (*SNAPSHOT_FILES, "ci-provenance.json")
PRODUCER_FILES = (".github/workflows/ci.yml", "scripts/prepare_portfolio_ci_artifact.py")
SOURCE_ONLY_VERIFICATION = {
    "machine_event_contract": "passed",
    "repository_test_suite": "not_run",
    "spark_runtime": "not_run",
    "sql_execution": "not_run",
    "databricks_runtime": "not_run",
    "effective_github_governance": "not_verified",
    "git_commit_provenance": "not_verified",
    "deployment_authorized": False,
}


class ArtifactError(RuntimeError):
    """A stable category without raw paths, input values or Git diagnostics."""


def canonical(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError("snapshot_duplicate_key")
        result[key] = value
    return result


def package_bytes(directory: Path, names: tuple[str, ...]) -> dict[str, bytes]:
    root = normalize_repository_root(directory)
    entries = tuple(itertools.islice(root.iterdir(), len(names) + 1))
    if {item.name for item in entries} != set(names) or len(entries) != len(names):
        raise ArtifactError("package_file_set_invalid")
    snapshots = read_repository_files(
        root, names, max_files=len(names), max_file_bytes=1_000_000,
        max_total_bytes=3_000_000,
    )
    return {item.relative_path: item.content for item in snapshots}


def git(root: Path, *args: str) -> bytes:
    # No network commands, shell, inherited tokens, replacement objects or global config.
    environment = {
        "PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(root), *args],
            capture_output=True, check=True, timeout=10, env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArtifactError("git_read_failed") from exc
    # Commands return commit metadata or <=102 exact-path tree entries, never file contents.
    if len(result.stdout) > 100_000:
        raise ArtifactError("git_output_too_large")
    return result.stdout


def ci_context(environment: Mapping[str, str]) -> dict:
    def required(name: str, pattern: str) -> str:
        value = environment.get(name, "")
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ArtifactError("ci_context_invalid")
        return value

    if (environment.get("GITHUB_ACTIONS") != "true"
            or environment.get("GITHUB_REPOSITORY") != REPOSITORY
            or environment.get("CI_VALIDATION_RESULT") != "success"):
        raise ArtifactError("ci_context_invalid")
    event = required("GITHUB_EVENT_NAME", r"pull_request|push")
    checkout = required("GITHUB_SHA", r"[0-9a-f]{40}")
    candidate = required("CANDIDATE_HEAD_SHA", r"[0-9a-f]{40}")
    base = environment.get("CANDIDATE_BASE_SHA", "")
    if event == "pull_request":
        required("GITHUB_REF", r"refs/pull/[1-9][0-9]{0,9}/merge")
        base = required("CANDIDATE_BASE_SHA", r"[0-9a-f]{40}")
    elif environment.get("GITHUB_REF") != "refs/heads/main" or candidate != checkout or base:
        raise ArtifactError("ci_context_invalid")
    return {
        "repository": REPOSITORY, "event_name": event,
        "run_id": int(required("GITHUB_RUN_ID", r"[1-9][0-9]{0,18}")),
        "run_attempt": int(required("GITHUB_RUN_ATTEMPT", r"[1-9][0-9]{0,8}")),
        "candidate_head": candidate, "candidate_base": base or None,
        "tested_checkout": checkout, "validation_job_result": "success",
    }


def checkout_identity(root: Path, context: dict) -> str:
    lines = git(root, "show", "-s", "--format=%H%n%T%n%P", "HEAD").decode("ascii").splitlines()
    if len(lines) != 3 or lines[0] != context["tested_checkout"] or not re.fullmatch(r"[0-9a-f]{40}", lines[1]):
        raise ArtifactError("checkout_identity_mismatch")
    if context["event_name"] == "pull_request" and lines[2].split() != [
        context["candidate_base"], context["candidate_head"],
    ]:
        raise ArtifactError("pull_request_parents_mismatch")
    return lines[1]


def source_records(snapshot: dict) -> list[dict]:
    records = snapshot.get("sources")
    if not isinstance(records, list) or not 1 <= len(records) <= 100:
        raise ArtifactError("snapshot_sources_invalid")
    paths = []
    for item in records:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            raise ArtifactError("snapshot_sources_invalid")
        path, digest, size = item["path"], item["sha256"], item["size_bytes"]
        if (not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,256}", path)
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or type(size) is not int or not 0 <= size <= 2_000_000):
            raise ArtifactError("snapshot_sources_invalid")
        paths.append(path)
    if paths != sorted(set(paths)) or not {
        "scripts/build_portfolio_snapshot.py", "src/lakehouse_demo/portfolio_snapshot.py",
    }.issubset(paths):
        raise ArtifactError("snapshot_sources_invalid")
    return records


def prepare(root: Path, snapshot_dir: Path, output_dir: Path, environment: Mapping[str, str]) -> dict:
    context = ci_context(environment)
    root = normalize_repository_root(root)
    tree = checkout_identity(root, context)
    contents = package_bytes(snapshot_dir, SNAPSHOT_FILES)
    payload = json.loads(contents[SNAPSHOT_FILES[0]], object_pairs_hook=unique_object)
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1 or payload.get("snapshot_kind") != "portfolio_source_evidence"
            or payload.get("evidence_boundary") != "repository_source_only"
            or payload.get("verification") != SOURCE_ONLY_VERIFICATION
            or payload["verification"].get("deployment_authorized") is not False):
        raise ArtifactError("snapshot_boundary_invalid")
    digest = payload.pop("snapshot_sha256", None)
    if digest != hashlib.sha256(canonical(payload).encode()).hexdigest():
        raise ArtifactError("snapshot_digest_mismatch")
    records = source_records(payload)
    paths = sorted({item["path"] for item in records} | set(PRODUCER_FILES))
    captured = read_repository_files(root, paths, max_files=102)
    actual = {item.relative_path: item for item in captured}
    if set(actual) != set(paths):
        raise ArtifactError("source_alias_invalid")
    for item in records:
        observed = actual[item["path"]]
        if (item["sha256"], item["size_bytes"]) != (observed.sha256, observed.size_bytes):
            raise ArtifactError("snapshot_source_changed")
    entries = git(root, "ls-tree", "-z", "--full-tree", context["tested_checkout"], "--", *paths)
    matched = set()
    for entry in entries.rstrip(b"\0").split(b"\0"):
        header, path = entry.decode("ascii").split("\t", 1)
        mode, kind, oid = header.split()
        if path not in actual or path in matched or mode not in {"100644", "100755"} or kind != "blob":
            raise ArtifactError("source_not_committed")
        content = actual[path].content
        blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if oid != blob:
            raise ArtifactError("source_not_committed")
        matched.add(path)
    if matched != set(paths):
        raise ArtifactError("source_not_committed")

    # A supplied digest proves consistency, not derivation from these source bytes.
    # Rebuild both representations before output, then retain the capture rechecks.
    try:
        rebuilt = build_portfolio_snapshot(root)
    except PortfolioSnapshotError as exc:
        raise ArtifactError("snapshot_rebuild_failed") from exc
    if contents[SNAPSHOT_FILES[0]] != canonical(rebuilt).encode("utf-8"):
        raise ArtifactError("snapshot_derivation_mismatch")
    if contents[SNAPSHOT_FILES[1]] != render_portfolio_snapshot_markdown(rebuilt).encode("utf-8"):
        raise ArtifactError("snapshot_markdown_mismatch")

    verify_repository_files_unchanged(root, captured)
    if checkout_identity(root, context) != tree or package_bytes(snapshot_dir, SNAPSHOT_FILES) != contents:
        raise ArtifactError("capture_changed")
    provenance = {
        "schema_version": 1, "artifact_kind": "portfolio_ci_source_evidence",
        "evidence_boundary": "repository_source_only", **context, "tested_tree": tree,
        "snapshot_sha256": digest, "selected_source_bytes_match_checkout": True,
        "acceptance": "pending_human_review", "deployment_authorized": False,
        "files": {name: {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
                  for name, content in sorted(contents.items())},
        "producer_files": [{"path": path, "sha256": actual[path].sha256} for path in PRODUCER_FILES],
    }
    texts = {name: content.decode("utf-8") for name, content in contents.items()}
    write_new_text_package(output_dir, {**texts, "ci-provenance.json": canonical(provenance)})
    return provenance


def verify_download(expected_dir: Path, downloaded_dir: Path) -> None:
    # Compare against the retained producer bytes, not a manifest supplied by the download.
    if package_bytes(expected_dir, ARTIFACT_FILES) != package_bytes(downloaded_dir, ARTIFACT_FILES):
        raise ArtifactError("download_bytes_mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("prepare")
    seal.add_argument("--repository-root", type=Path, default=ROOT)
    seal.add_argument("--snapshot-dir", type=Path, required=True)
    seal.add_argument("--output-dir", type=Path, required=True)
    verify = subparsers.add_parser("verify-download")
    verify.add_argument("--expected-dir", type=Path, required=True)
    verify.add_argument("--downloaded-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.repository_root, args.snapshot_dir, args.output_dir, os.environ)
        else:
            verify_download(args.expected_dir, args.downloaded_dir)
    except (ArtifactError, RepositoryFileError) as exc:
        category = exc.category if isinstance(exc, RepositoryFileError) else str(exc)
        print(json.dumps({"status": "failed", "category": category}), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({"status": "failed", "category": "artifact_input_invalid"}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "prepared" if args.command == "prepare" else "download_verified"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
