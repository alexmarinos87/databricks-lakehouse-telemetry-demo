#!/usr/bin/env python3
"""Generate deterministic source evidence for the committed synthetic dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.dataset_profile import (  # noqa: E402
    default_machine_event_sources,
    profile_machine_event_files,
    write_dataset_profile_package,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--source",
        action="append",
        help="Repository-relative machine-event CSV; repeat for multiple files",
    )
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = args.source or default_machine_event_sources(args.repository_root)
    profile = profile_machine_event_files(args.repository_root, sources)
    write_dataset_profile_package(profile, args.output_dir)
    rows = profile["rows"]
    coverage = profile["coverage"]
    assert isinstance(rows, dict)
    assert isinstance(coverage, dict)
    print(
        json.dumps(
            {
                "output_dir": str(Path(args.output_dir)),
                "physical_rows": rows["physical_row_count"],
                "unique_event_ids": rows["unique_event_id_count"],
                "machines": coverage["machine_count"],
                "evidence_boundary": profile["evidence_boundary"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
