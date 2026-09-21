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
    except (ImportError, OSError):
        print("Spark runtime discovery failed.", file=sys.stderr)
        return 1
    if suite.countTestCases() == 0:
        print("Spark runtime suite is empty; no runtime evidence was produced.", file=sys.stderr)
        return 1
    # Match unittest.main: show diagnostics unless Python -W options override it.
    warning_policy = None if sys.warnoptions else "default"
    result = unittest.TextTestRunner(
        verbosity=2 if options.v else 1, warnings=warning_policy,
    ).run(suite)
    if result.skipped or result.expectedFailures:
        print("Spark runtime evidence is incomplete: skipped or expected-failure tests.",
              file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
