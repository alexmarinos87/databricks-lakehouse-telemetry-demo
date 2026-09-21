#!/usr/bin/env python3
"""Require executed, passing runtime tests rather than an empty/partial success."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

RUNTIME_DIRECTORY = Path(__file__).resolve().parents[1] / "tests_runtime"
RUNTIME_PATTERN = "test_spark_*_runtime.py"


def main() -> int:
    try:
        suite = unittest.TestLoader().discover(
            str(RUNTIME_DIRECTORY), pattern=RUNTIME_PATTERN,
        )
    except (ImportError, OSError):
        print("Spark runtime discovery failed.", file=sys.stderr)
        return 1
    if suite.countTestCases() == 0:
        print("Spark runtime suite is empty; no runtime evidence was produced.", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped or result.expectedFailures:
        print("Spark runtime evidence is incomplete: skipped or expected-failure tests.",
              file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
