#!/usr/bin/env python3
"""Build source-only portfolio evidence; never execute tests, SQL or a provider CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.portfolio_snapshot import (  # noqa: E402
    PortfolioSnapshotError,
    build_portfolio_snapshot,
    write_portfolio_snapshot_package,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        snapshot = build_portfolio_snapshot(args.repository_root)
        write_portfolio_snapshot_package(snapshot, args.output_dir)
    except PortfolioSnapshotError as exc:
        print(json.dumps({"status": "failed", "category": exc.category}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "created",
        "evidence_boundary": snapshot["evidence_boundary"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "physical_rows": snapshot["dataset_profile"]["rows"]["physical_row_count"],
        "unique_event_ids": snapshot["dataset_profile"]["rows"]["unique_event_id_count"],
        "reporting_assets": snapshot["reporting"]["asset_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
