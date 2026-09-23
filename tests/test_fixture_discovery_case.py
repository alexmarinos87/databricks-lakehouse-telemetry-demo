"""Exercise real directory discovery with explicit POSIX/Windows case semantics."""

from __future__ import annotations

import fnmatch
import hashlib
import ntpath
import posixpath
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo import fixture_discovery as discovery
from lakehouse_demo.repository_files import read_repository_files

# Save originals before patching the shared os.path module on either platform.
NORMALIZERS = (("posix", posixpath.normcase), ("windows", ntpath.normcase))


class FixtureDiscoveryCaseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "data/increments"
        self.directory.mkdir(parents=True)

    def create_files(self, names):
        for name in names:
            (self.directory / name).write_bytes(b"synthetic:" + name.encode("utf-8"))

    def test_mixed_suffix_selection_is_identical_without_lowercasing_names(self):
        self.create_files(("a.csv", "b.CSV", "c.CsV", "UPPER_STEM.csv", ".hidden.csv", "d.csv.tmp"))
        (self.directory / "nested").mkdir()
        (self.directory / "nested/inner.csv").write_bytes(b"not an immediate child")
        expected = (
            "data/increments/.hidden.csv", "data/increments/UPPER_STEM.csv",
            "data/increments/a.csv",
        )
        for platform, normalizer in NORMALIZERS:
            with self.subTest(platform=platform):
                with mock.patch.object(fnmatch.os.path, "normcase", side_effect=normalizer):
                    self.assertEqual(expected, discovery.discover_increment_paths(self.root))

    def test_excluded_suffixes_do_not_consume_the_matching_file_budget(self):
        self.create_files(("a.csv", "b.csv", "c.CSV", "d.CsV"))
        for platform, normalizer in NORMALIZERS:
            with self.subTest(platform=platform):
                with mock.patch.object(fnmatch.os.path, "normcase", side_effect=normalizer), \
                     mock.patch.object(discovery, "MAX_INCREMENT_FILES", 2):
                    self.assertEqual(
                        ("data/increments/a.csv", "data/increments/b.csv"),
                        discovery.discover_increment_paths(self.root),
                    )

    def test_excluded_suffixes_still_count_toward_directory_scan_budget(self):
        self.create_files(("a.CSV", "b.CsV", "c.txt"))
        for platform, normalizer in NORMALIZERS:
            with self.subTest(platform=platform):
                with mock.patch.object(fnmatch.os.path, "normcase", side_effect=normalizer), \
                     mock.patch.object(discovery, "MAX_DIRECTORY_ENTRIES", 2):
                    with self.assertRaises(discovery.FixtureDiscoveryError) as raised:
                        discovery.discover_increment_paths(self.root)
                self.assertEqual("fixture_directory_entry_limit_exceeded", raised.exception.category)

    def test_discovered_paths_bind_the_same_real_file_bytes_under_both_semantics(self):
        self.create_files(("UPPER_STEM.csv", "excluded.CSV"))
        for platform, normalizer in NORMALIZERS:
            with self.subTest(platform=platform):
                with mock.patch.object(fnmatch.os.path, "normcase", side_effect=normalizer):
                    paths = discovery.discover_increment_paths(self.root)
                snapshots = read_repository_files(self.root, paths)
                self.assertEqual(1, len(snapshots))
                result = snapshots[0]
                self.assertEqual("data/increments/UPPER_STEM.csv", result.relative_path)
                self.assertEqual(b"synthetic:UPPER_STEM.csv", result.content)
                self.assertEqual(hashlib.sha256(result.content).hexdigest(), result.sha256)

    def test_platform_probe_exercises_the_original_case_normalization_difference(self):
        for platform, normalizer in NORMALIZERS:
            with self.subTest(platform=platform):
                with mock.patch.object(fnmatch.os.path, "normcase", side_effect=normalizer):
                    self.assertEqual(platform == "windows", fnmatch.fnmatch("a.CSV", "*.csv"))
                    self.assertTrue(fnmatch.fnmatch("a.csv", "*.csv"))


if __name__ == "__main__":
    unittest.main()
