#!/usr/bin/env python3
"""Validate and display the repository evidence workflow catalogue."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOGUE = REPO_ROOT / "governance" / "evidence_workflow_catalogue.json"
MAX_CATALOGUE_BYTES = 100_000
EXPECTED_REPOSITORY = "alexmarinos87/databricks-lakehouse-telemetry-demo"
EXPECTED_STAGE_IDS = (
    "external_readiness",
    "effective_external_controls",
    "external_control_index",
    "development_plan",
    "development_runtime_evidence",
    "operational_evidence",
)
ALLOWED_MODES = {"read_only", "offline_evidence", "plan_only"}
STAGE_KEYS = {
    "order",
    "stage_id",
    "title",
    "mode",
    "entrypoints",
    "owner_command",
    "requires",
    "authorizes",
}
_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")


class CatalogueError(RuntimeError):
    """Stable catalogue failure category safe to expose."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _text(value: Any, category: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise CatalogueError(category)
    return value


def _load_json(path: Path) -> tuple[Mapping[str, Any], bytes]:
    try:
        if path.is_symlink() or not path.is_file():
            raise CatalogueError("catalogue_not_regular")
        payload = path.read_bytes()
    except OSError:
        raise CatalogueError("catalogue_unavailable") from None
    if not payload or len(payload) > MAX_CATALOGUE_BYTES:
        raise CatalogueError("catalogue_size_invalid")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CatalogueError("catalogue_invalid_json") from None
    if not isinstance(document, dict):
        raise CatalogueError("catalogue_shape_invalid")
    return document, payload


def _validate_entrypoint(value: Any, repository_root: Path) -> str:
    entrypoint = _text(value, "entrypoint_invalid", 200)
    if (
        not entrypoint.startswith("scripts/")
        or not entrypoint.endswith(".py")
        or ".." in Path(entrypoint).parts
        or "\\" in entrypoint
    ):
        raise CatalogueError("entrypoint_invalid")
    path = repository_root / entrypoint
    if path.is_symlink() or not path.is_file():
        raise CatalogueError("entrypoint_missing")
    return entrypoint


def load_catalogue(
    path: Path = DEFAULT_CATALOGUE,
    *,
    repository_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    document, _ = _load_json(path)
    if set(document) != {"schema_version", "repository", "stages"}:
        raise CatalogueError("catalogue_shape_invalid")
    if document.get("schema_version") != 1:
        raise CatalogueError("catalogue_version_mismatch")
    if document.get("repository") != EXPECTED_REPOSITORY:
        raise CatalogueError("catalogue_repository_mismatch")

    stages = document.get("stages")
    if not isinstance(stages, list) or len(stages) != len(EXPECTED_STAGE_IDS):
        raise CatalogueError("catalogue_stage_count_invalid")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for expected_order, (expected_id, raw_stage) in enumerate(
        zip(EXPECTED_STAGE_IDS, stages, strict=True), start=1
    ):
        if not isinstance(raw_stage, dict) or set(raw_stage) != STAGE_KEYS:
            raise CatalogueError("catalogue_stage_shape_invalid")
        order = raw_stage.get("order")
        if isinstance(order, bool) or order != expected_order:
            raise CatalogueError("catalogue_stage_order_invalid")
        stage_id = _text(raw_stage.get("stage_id"), "catalogue_stage_id_invalid", 64)
        if not _ID_PATTERN.fullmatch(stage_id) or stage_id != expected_id:
            raise CatalogueError("catalogue_stage_id_invalid")
        if stage_id in seen_ids:
            raise CatalogueError("catalogue_stage_id_duplicate")
        seen_ids.add(stage_id)
        title = _text(raw_stage.get("title"), "catalogue_stage_title_invalid")
        mode = raw_stage.get("mode")
        if mode not in ALLOWED_MODES:
            raise CatalogueError("catalogue_stage_mode_invalid")

        raw_entrypoints = raw_stage.get("entrypoints")
        if not isinstance(raw_entrypoints, list):
            raise CatalogueError("catalogue_entrypoints_invalid")
        entrypoints = [
            _validate_entrypoint(item, repository_root) for item in raw_entrypoints
        ]
        if len(set(entrypoints)) != len(entrypoints):
            raise CatalogueError("catalogue_entrypoint_duplicate")

        owner_command = raw_stage.get("owner_command")
        if owner_command is not None:
            owner_command = _text(
                owner_command, "catalogue_owner_command_invalid", 128
            )
        if mode == "plan_only":
            if owner_command != "/databricks-plan dev" or entrypoints:
                raise CatalogueError("catalogue_plan_stage_invalid")
        elif owner_command is not None:
            raise CatalogueError("catalogue_owner_command_invalid")

        requires = raw_stage.get("requires")
        if not isinstance(requires, list) or any(
            not isinstance(item, str) for item in requires
        ):
            raise CatalogueError("catalogue_requires_invalid")
        if requires != ([] if expected_order == 1 else [EXPECTED_STAGE_IDS[expected_order - 2]]):
            raise CatalogueError("catalogue_dependency_invalid")

        authorizes = raw_stage.get("authorizes")
        if authorizes != []:
            raise CatalogueError("catalogue_authority_boundary_invalid")

        normalized.append(
            {
                "order": order,
                "stage_id": stage_id,
                "title": title,
                "mode": mode,
                "entrypoints": entrypoints,
                "owner_command": owner_command,
                "requires": list(requires),
                "authorizes": [],
            }
        )

    return {
        "schema_version": 1,
        "repository": EXPECTED_REPOSITORY,
        "stages": normalized,
        "automatic_action_authorized": False,
    }


def render_text(catalogue: Mapping[str, Any]) -> str:
    lines = ["Evidence workflow", ""]
    for stage in catalogue["stages"]:
        commands = list(stage["entrypoints"])
        if stage["owner_command"]:
            commands.append(stage["owner_command"])
        lines.append(
            f"{stage['order']}. {stage['title']} [{stage['mode']}]"
        )
        for command in commands:
            lines.append(f"   - {command}")
    lines.extend(
        [
            "",
            "Automatic action authorized: false",
            "This catalogue describes evidence gates; it authorizes no apply, deployment or production action.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        catalogue = load_catalogue(args.catalogue)
    except CatalogueError as error:
        print(f"Evidence workflow catalogue invalid: {error.category}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(catalogue, indent=2, sort_keys=True))
    else:
        print(render_text(catalogue), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
