"""Behavioural regressions for package creation and failure ownership."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo.repository_files import RepositoryFileError, write_new_text_package


class RepositoryPackageFailureTest(unittest.TestCase):
    def test_losing_creator_preserves_winning_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "package"
            mkdir = Path.mkdir

            def competing_creator(path, *args, **kwargs):
                mkdir(path, *args, **kwargs)
                (path / "winner.txt").write_text("keep me", encoding="utf-8")
                raise FileExistsError("raw filesystem diagnostic")

            with mock.patch.object(Path, "mkdir", competing_creator):
                with self.assertRaises(RepositoryFileError) as raised:
                    write_new_text_package(output, {"evidence.json": "{}"})
            self.assertTrue((output / "winner.txt").exists(), "winning output was deleted")
            self.assertEqual("keep me", (output / "winner.txt").read_text(encoding="utf-8"))
            self.assertEqual("output_directory_exists", raised.exception.category)
            self.assertNotIn("raw filesystem diagnostic", str(raised.exception))
            self.assertFalse((output / "evidence.json").exists())

    def test_mkdir_failure_never_attempts_recursive_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "package"
            with mock.patch.object(Path, "mkdir", side_effect=PermissionError("private")):
                with mock.patch("lakehouse_demo.repository_files.shutil.rmtree") as cleanup:
                    with self.assertRaises(RepositoryFileError) as raised:
                        write_new_text_package(output, {"evidence.json": "{}"})
            cleanup.assert_not_called()
            self.assertEqual("output_directory_unavailable", raised.exception.category)
            self.assertNotIn("private", str(raised.exception))

    def test_partial_write_removes_owned_package_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "package"
            sentinel = root / "unrelated.txt"
            sentinel.write_text("keep", encoding="utf-8")
            original_open = Path.open

            def fail_second_file(path, *args, **kwargs):
                if path == output / "b.md":
                    self.assertTrue((output / "a.json").exists())
                    raise OSError("private write diagnostic")
                return original_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", fail_second_file):
                with self.assertRaises(RepositoryFileError) as raised:
                    write_new_text_package(output, {"a.json": "{}", "b.md": "summary"})
            self.assertFalse(output.exists())
            self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
            self.assertEqual("output_directory_unavailable", raised.exception.category)
            self.assertNotIn("private write diagnostic", str(raised.exception))

    def test_replaced_directory_is_not_deleted_after_write_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "package"
            displaced = root / "displaced"
            original_open = Path.open

            def replace_then_fail(path, *args, **kwargs):
                if path == output / "b.md":
                    output.rename(displaced)
                    output.mkdir()
                    with original_open(output / "winner.txt", "w", encoding="utf-8") as handle:
                        handle.write("keep me")
                    raise OSError("replaced")
                return original_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", replace_then_fail):
                with self.assertRaises(RepositoryFileError):
                    write_new_text_package(output, {"a.json": "{}", "b.md": "summary"})
            self.assertTrue((output / "winner.txt").exists(), "replacement output was deleted")
            self.assertEqual("keep me", (output / "winner.txt").read_text(encoding="utf-8"))
            self.assertEqual("{}", (displaced / "a.json").read_text(encoding="utf-8"))

    def test_symlink_ancestor_above_existing_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            (real / "nested").mkdir(parents=True)
            linked = root / "linked"
            os.symlink(real, linked)
            with self.assertRaises(RepositoryFileError) as raised:
                write_new_text_package(linked / "nested" / "package", {"evidence.json": "{}"})
            self.assertEqual("output_parent_symlink", raised.exception.category)
            self.assertFalse((real / "nested" / "package").exists())

    def test_symlink_ancestor_above_missing_parents_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            (real / "nested").mkdir(parents=True)
            linked = root / "linked"
            os.symlink(real, linked)
            with self.assertRaises(RepositoryFileError) as raised:
                write_new_text_package(linked / "nested" / "new" / "package", {"a.md": "a"})
            self.assertEqual("output_parent_symlink", raised.exception.category)
            self.assertFalse((real / "nested" / "new").exists())

    def test_new_nested_output_still_succeeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new" / "nested" / "package"
            result = write_new_text_package(output, {"evidence.json": "{}\n"})
            self.assertEqual(output, result)
            self.assertEqual("{}\n", (output / "evidence.json").read_text(encoding="utf-8"))

    def test_unencodable_text_cleans_up_owned_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "package"
            with self.assertRaises(RepositoryFileError) as raised:
                write_new_text_package(output, {"a.json": "{}", "b.md": "\ud800"})
            self.assertEqual("output_directory_unavailable", raised.exception.category)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
