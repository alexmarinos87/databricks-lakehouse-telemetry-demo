"""Compose bounded, deterministic portfolio evidence from local source only."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from lakehouse_demo.dataset_profile import (
    DEFAULT_INCREMENT_GLOB,
    DEFAULT_SAMPLE,
    DatasetProfileError,
    default_machine_event_sources,
    profile_machine_event_files,
    render_dataset_profile_markdown,
)
from lakehouse_demo.repository_files import (
    RepositoryFileError,
    normalize_repository_root,
    read_repository_files,
    verify_repository_files_unchanged,
    write_new_text_package,
)

MANIFEST_PATH = "sql/reporting_assets/manifest.json"
MAX_REPORTING_ASSETS = 50
MAX_SQL_BYTES = 100_000
MAX_FIXTURE_FILES = 32
SNAPSHOT_JSON = "portfolio-snapshot.json"
SNAPSHOT_MARKDOWN = "portfolio-snapshot.md"
VALIDATION_COMMANDS = (
    "scripts/run_local_checks.sh",
    "scripts/run_acceptance_checks.sh",
    "scripts/run_spark_runtime_checks.sh",
    "python3 scripts/generate_review_package.py --base origin/main --output .review/review-package.md",
)
EVIDENCE_PATHS = (
    "README.md",
    "docs/architecture.md",
    "docs/deployment.md",
    "docs/engineering_risk_register.md",
    "scripts/run_local_checks.sh",
    "scripts/run_acceptance_checks.sh",
    "scripts/run_spark_runtime_checks.sh",
    "scripts/generate_review_package.py",
    "scripts/build_portfolio_snapshot.py",
    "src/lakehouse_demo/portfolio_snapshot.py",
    "src/lakehouse_demo/dataset_profile.py",
    "src/lakehouse_demo/fixture_discovery.py",
    "src/lakehouse_demo/repository_files.py",
    "src/lakehouse_demo/azure_ingestion.py",
    "src/lakehouse_demo/machine_event_contract.py",
)


class PortfolioSnapshotError(RuntimeError):
    """An offline evidence failure exposing only a stable category."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PortfolioSnapshotError("reporting_manifest_duplicate_key")
        result[key] = value
    return result


def _reporting_paths(content: bytes) -> tuple[str, ...]:
    try:
        manifest = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PortfolioSnapshotError("reporting_manifest_invalid_json") from exc
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= MAX_REPORTING_ASSETS:
        raise PortfolioSnapshotError("reporting_manifest_invalid_shape")
    paths: set[str] = set()
    names: set[str] = set()
    for asset in manifest:
        if not isinstance(asset, dict) or set(asset) != {"file", "display_name", "description"}:
            raise PortfolioSnapshotError("reporting_manifest_invalid_shape")
        name = asset["display_name"]
        description = asset["description"]
        filename = asset["file"]
        if (
            not isinstance(name, str) or not name.strip() or len(name) > 200
            or not name.isprintable() or name != name.strip()
            or not isinstance(description, str) or len(description) > 2_000
            or not description.isprintable()
            or not isinstance(filename, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,94}\.sql", filename)
        ):
            raise PortfolioSnapshotError("reporting_manifest_invalid_entry")
        if filename in paths or name in names:
            raise PortfolioSnapshotError("reporting_manifest_duplicate_asset")
        paths.add(filename)
        names.add(name)
    return tuple(f"sql/reporting_assets/{name}" for name in sorted(paths))


def _fixture_paths(root: Path) -> tuple[str, ...]:
    paths = default_machine_event_sources(root)
    if len(paths) > MAX_FIXTURE_FILES:
        raise PortfolioSnapshotError("fixture_file_count_exceeded")
    for path in paths[1:]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.csv", Path(path).name):
            raise PortfolioSnapshotError("fixture_filename_invalid")
    return tuple(sorted(paths))


def _record(snapshot: Any) -> dict[str, Any]:
    return {
        "path": snapshot.relative_path,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def build_portfolio_snapshot(repository_root: str | Path) -> dict[str, Any]:
    """Describe selected bytes, not Git acceptance, SQL validity or runtime state."""
    try:
        root = normalize_repository_root(repository_root)
        fixtures = _fixture_paths(root)
        manifest = read_repository_files(root, [MANIFEST_PATH], max_file_bytes=100_000)[0]
        assets = _reporting_paths(manifest.content)
        snapshots = read_repository_files(root, (*fixtures, MANIFEST_PATH, *EVIDENCE_PATHS, *assets))
        by_path = {item.relative_path: item for item in snapshots}
        # Reject aliases as well as missing evidence in the supplied source layout.
        if set(by_path) != set((*fixtures, MANIFEST_PATH, *EVIDENCE_PATHS, *assets)):
            raise PortfolioSnapshotError("source_inventory_path_mismatch")
        verify_repository_files_unchanged(root, [manifest])
        for path in assets:
            item = by_path[path]
            if item.size_bytes > MAX_SQL_BYTES:
                raise PortfolioSnapshotError("reporting_sql_too_large")
            try:
                text = item.content.decode("utf-8")
            except UnicodeError as exc:
                raise PortfolioSnapshotError("reporting_sql_not_utf8") from exc
            if not text.strip():
                raise PortfolioSnapshotError("reporting_sql_empty")

        profile = profile_machine_event_files(root, fixtures)
        profile_inputs = [
            {key: item[key] for key in ("path", "sha256", "size_bytes")}
            for item in profile["input"]["files"]
        ]
        if profile_inputs != [_record(by_path[path]) for path in fixtures]:
            raise PortfolioSnapshotError("dataset_profile_source_mismatch")
        verify_repository_files_unchanged(root, snapshots)
        if _fixture_paths(root) != fixtures:
            raise PortfolioSnapshotError("fixture_selection_changed")
    except (RepositoryFileError, DatasetProfileError) as exc:
        raise PortfolioSnapshotError(exc.category) from exc
    except (OSError, ValueError) as exc:
        raise PortfolioSnapshotError("source_read_failed") from exc

    payload: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_kind": "portfolio_source_evidence",
        "evidence_boundary": "repository_source_only",
        "dataset_profile": profile,
        "reporting": {"asset_count": len(assets), "assets": [_record(by_path[path]) for path in assets]},
        "validation_entrypoints": list(VALIDATION_COMMANDS),
        "verification": {
            "machine_event_contract": "passed",
            "repository_test_suite": "not_run",
            "spark_runtime": "not_run",
            "sql_execution": "not_run",
            "databricks_runtime": "not_run",
            "effective_github_governance": "not_verified",
            "git_commit_provenance": "not_verified",
            "deployment_authorized": False,
        },
        "sources": [_record(item) for item in snapshots],
    }
    payload["snapshot_sha256"] = hashlib.sha256(_json_text(payload).encode("utf-8")).hexdigest()
    return payload


def render_portfolio_snapshot_markdown(snapshot: dict[str, Any]) -> str:
    """Render a builder-produced snapshot without embedding source rows or SQL."""
    lines = [
        "# Portfolio source-evidence snapshot", "",
        "This package describes selected local source bytes and synthetic fixture aggregates. "
        "It does not prove a clean Git checkout, accepted commit, successful repository tests, "
        "SQL execution, live Databricks behaviour, effective governance or production operation.", "",
        f"Snapshot SHA-256: `{snapshot['snapshot_sha256']}`", "",
    ]
    # Reuse the profiler's presentation without changing its aggregate semantics.
    for line in render_dataset_profile_markdown(snapshot["dataset_profile"]).splitlines():
        lines.append("#" + line if line.startswith("#") else line)
    lines.extend(["", "## Reporting source catalogue", "",
                  "These SQL files are inventoried, not executed or semantically validated.", "",
                  "| Repository path | Bytes | SHA-256 |", "| --- | ---: | --- |"])
    for asset in snapshot["reporting"]["assets"]:
        lines.append(f"| `{asset['path']}` | {asset['size_bytes']} | `{asset['sha256']}` |")
    lines.extend(["", "## Validation entrypoints", "",
                  "These commands are listed for the reviewer; the snapshot builder does not run them.", "",
                  "```bash", *snapshot["validation_entrypoints"], "```", "",
                  "## Verification boundary", "", "| Evidence | Result |", "| --- | --- |"])
    for key, value in sorted(snapshot["verification"].items()):
        lines.append(f"| `{key}` | `{json.dumps(value)}` |")
    lines.extend(["", "## Selected-source provenance", "",
                  "Hashes identify selected bytes only. They are not signatures or proof of Git provenance.", "",
                  "| Repository path | Bytes | SHA-256 |", "| --- | ---: | --- |"])
    for item in snapshot["sources"]:
        lines.append(f"| `{item['path']}` | {item['size_bytes']} | `{item['sha256']}` |")
    return "\n".join(lines) + "\n"


def write_portfolio_snapshot_package(snapshot: dict[str, Any], output_dir: str | Path) -> Path:
    """Write one internally consistent builder result to a new local directory."""
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    digest = hashlib.sha256(_json_text(payload).encode("utf-8")).hexdigest()
    if snapshot.get("snapshot_sha256") != digest:
        raise PortfolioSnapshotError("snapshot_digest_mismatch")
    try:
        return write_new_text_package(output_dir, {
            SNAPSHOT_JSON: _json_text(snapshot),
            SNAPSHOT_MARKDOWN: render_portfolio_snapshot_markdown(snapshot),
        })
    except RepositoryFileError as exc:
        raise PortfolioSnapshotError(exc.category) from exc
