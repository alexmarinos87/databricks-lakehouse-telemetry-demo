#!/usr/bin/env python3
"""Create a deterministic, content-addressed ingestion upload manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.ingestion_identity import (  # noqa: E402
    MODE_BACKFILL,
    MODE_INCREMENTAL,
    plan_ingestion_uploads,
    write_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--destination-root", required=True)
    parser.add_argument(
        "--mode",
        choices=(MODE_INCREMENTAL, MODE_BACKFILL),
        default=MODE_INCREMENTAL,
    )
    parser.add_argument("--replay-id", default="")
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = plan_ingestion_uploads(
        args.source,
        repository_root=args.repository_root,
        destination_root=args.destination_root,
        mode=args.mode,
        replay_id=args.replay_id,
    )
    write_manifest(manifest, args.output)
    print(
        json.dumps(
            {
                "plan_id": manifest["plan_id"],
                "mode": manifest["mode"],
                "file_count": len(manifest["entries"]),
                "checkpoint_policy": manifest["checkpoint_policy"],
                "allow_overwrites": manifest["allow_overwrites"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
