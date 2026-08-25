#!/usr/bin/env python3
"""Compatibility entry point for bounded Databricks plan evidence."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from plan_evidence import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    sys.exit(main())
