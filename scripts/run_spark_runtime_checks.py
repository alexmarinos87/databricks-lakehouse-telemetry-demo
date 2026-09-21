#!/usr/bin/env python3
"""Require executed, passing runtime tests rather than an empty/partial success."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

RUNTIME_DIRECTORY = Path(__file__).resolve().parents[1] / "tests_runtime"
RUNTIME_PATTERN = "test_spark_*_runtime.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", choices=("tests_runtime",), default="tests_runtime")
    parser.add_argument("-p", choices=(RUNTIME_PATTERN,), default=RUNTIME_PATTERN)
    parser.add_argument("-v", action="store_true")
    options = parser.parse_args()
    try:
        suite = unittest.TestLoader().discover(
            str(RUNTIME_DIRECTORY.parent / options.s), pattern=options.p,
        )
        discovered_count = suite.countTestCases()
    except (ImportError, OSError, SystemExit):
        print("Spark runtime discovery failed.", file=sys.stderr)
        return 1
    if discovered_count == 0:
        print("Spark runtime suite is empty; no runtime evidence was produced.", file=sys.stderr)
        return 1
    # Match unittest.main: show diagnostics unless Python -W options override it.
    warning_policy = None if sys.warnoptions else "default"
    try:
        result = unittest.TextTestRunner(
            verbosity=2 if options.v else 1, warnings=warning_policy,
        ).run(suite)
    except SystemExit:
        print("Spark runtime execution exited before completing.", file=sys.stderr)
        return 1
    if result.skipped or result.expectedFailures:
        print("Spark runtime evidence is incomplete: skipped or expected-failure tests.",
              file=sys.stderr)
        return 1
    if result.shouldStop or result.testsRun != discovered_count:
        print(f"Spark runtime evidence is incomplete: ran {result.testsRun} of "
              f"{discovered_count} discovered tests or received a stop request.",
              file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
