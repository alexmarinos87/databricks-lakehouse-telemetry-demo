"""Reject malformed resource budgets before inspecting repository inputs."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo import repository_files as files


class IntegerSubclass(int):
    """Configuration accepts built-in integers, not arbitrary numeric objects."""


class RepositoryReadLimitTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "input.txt").write_bytes(b"abc")

    def test_invalid_limits_fail_before_root_lookup_or_path_iteration(self):
        invalid = (
            ("zero", 0), ("negative", -1), ("true", True), ("false", False),
            ("integral_float", 1.0), ("fractional_float", 1.5),
            ("nan", float("nan")), ("infinity", float("inf")),
            ("negative_infinity", -float("inf")), ("text", "4"),
            ("none", None), ("decimal", Decimal("4")),
            ("decimal_nan", Decimal("NaN")), ("fraction", Fraction(4)),
            ("object", object()), ("integer_subclass", IntegerSubclass(4)),
        )
        for parameter in ("max_files", "max_file_bytes", "max_total_bytes"):
            for label, value in invalid:
                with self.subTest(parameter=parameter, value=label):
                    consumed = []

                    def paths():
                        consumed.append("iterated")
                        yield "input.txt"

                    error = None
                    with mock.patch.object(
                        files, "normalize_repository_root",
                        wraps=files.normalize_repository_root,
                    ) as root_lookup:
                        try:
                            files.read_repository_files(
                                self.root, paths(), **{parameter: value},
                            )
                        except Exception as exc:
                            error = exc
                    self.assertIsInstance(error, files.RepositoryFileError)
                    self.assertEqual("repository_read_limit_invalid", error.category)
                    self.assertEqual("repository_read_limit_invalid: <input>", str(error))
                    root_lookup.assert_not_called()
                    self.assertEqual([], consumed)

    def test_invalid_limit_diagnostic_does_not_echo_supplied_value(self):
        for parameter in ("max_files", "max_file_bytes", "max_total_bytes"):
            with self.subTest(parameter=parameter):
                with self.assertRaises(files.RepositoryFileError) as raised:
                    files.read_repository_files(
                        self.root, ["input.txt"],
                        **{parameter: "private-provider-detail"},
                    )
                self.assertEqual("repository_read_limit_invalid: <input>", str(raised.exception))

    def test_exact_positive_integer_budgets_preserve_bytes_hashes_and_empty_file(self):
        (self.root / "a.txt").write_bytes(b"ab")
        (self.root / "b.txt").write_bytes(b"c")
        (self.root / "c.txt").write_bytes(b"")
        paths = ["c.txt", "b.txt", "a.txt"]
        observed = files.read_repository_files(
            self.root, paths, max_files=3, max_file_bytes=2, max_total_bytes=3,
        )
        self.assertEqual(["a.txt", "b.txt", "c.txt"], [item.relative_path for item in observed])
        self.assertEqual([b"ab", b"c", b""], [item.content for item in observed])
        self.assertEqual([2, 1, 0], [item.size_bytes for item in observed])
        for item in observed:
            self.assertEqual(hashlib.sha256(item.content).hexdigest(), item.sha256)
        self.assertEqual(observed, files.read_repository_files(self.root, paths))
        files.verify_repository_files_unchanged(
            self.root, observed, max_file_bytes=2, max_total_bytes=3,
        )
        for limits, category in (
            ({"max_file_bytes": 1}, "repository_file_too_large"),
            ({"max_total_bytes": 2}, "repository_input_too_large"),
        ):
            with self.subTest(limits=limits):
                with self.assertRaises(files.RepositoryFileError) as raised:
                    files.read_repository_files(self.root, paths, **limits)
                self.assertEqual(category, raised.exception.category)

    def test_count_limit_still_counts_duplicate_paths_and_stops_one_over_budget(self):
        consumed = []

        def paths():
            for index in range(10):
                consumed.append(index)
                yield "input.txt"

        with mock.patch.object(files.os, "open") as opened:
            with self.assertRaises(files.RepositoryFileError) as raised:
                files.read_repository_files(self.root, paths(), max_files=2)
            opened.assert_not_called()
        self.assertEqual("repository_file_count_exceeded", raised.exception.category)
        self.assertEqual([0, 1, 2], consumed)

    def test_recheck_rejects_invalid_byte_limits_with_the_same_category(self):
        expected = files.read_repository_files(self.root, ["input.txt"])
        for parameter in ("max_file_bytes", "max_total_bytes"):
            with self.subTest(parameter=parameter):
                with self.assertRaises(files.RepositoryFileError) as raised:
                    files.verify_repository_files_unchanged(
                        self.root, expected, **{parameter: float("nan")},
                    )
                self.assertEqual("repository_read_limit_invalid", raised.exception.category)


if __name__ == "__main__":
    unittest.main()
