import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo.repository_files import (
    RepositoryFileError,
    read_repository_files,
    verify_repository_files_unchanged,
    write_new_text_package,
)


class RepositoryFilesTest(unittest.TestCase):
    def test_reads_unique_files_in_relative_path_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "b.txt").write_text("b", encoding="utf-8")
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            snapshots = read_repository_files(root, ["b.txt", "a.txt", "a.txt"])
            self.assertEqual(["a.txt", "b.txt"], [item.relative_path for item in snapshots])
            self.assertEqual([b"alpha", b"b"], [item.content for item in snapshots])
            self.assertEqual([5, 1], [item.size_bytes for item in snapshots])
            self.assertEqual(64, len(snapshots[0].sha256))

    def test_rejects_outside_symlink_and_non_regular_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            with self.assertRaises(RepositoryFileError) as outside_error:
                read_repository_files(root, [outside])
            self.assertEqual("repository_file_outside_root", outside_error.exception.category)

            target = root / "target.txt"
            target.write_text("target", encoding="utf-8")
            link = root / "link.txt"
            os.symlink(target, link)
            with self.assertRaises(RepositoryFileError) as link_error:
                read_repository_files(root, [link])
            self.assertEqual("repository_file_symlink", link_error.exception.category)

            directory = root / "directory"
            directory.mkdir()
            with self.assertRaises(RepositoryFileError) as directory_error:
                read_repository_files(root, [directory])
            self.assertEqual("repository_file_not_regular", directory_error.exception.category)

    def test_enforces_file_count_per_file_and_total_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.txt").write_text("1234", encoding="utf-8")
            (root / "b.txt").write_text("5678", encoding="utf-8")
            with self.assertRaises(RepositoryFileError) as count_error:
                read_repository_files(root, ["a.txt", "b.txt"], max_files=1)
            self.assertEqual("repository_file_count_exceeded", count_error.exception.category)
            with self.assertRaises(RepositoryFileError) as file_error:
                read_repository_files(root, ["a.txt"], max_file_bytes=3)
            self.assertEqual("repository_file_too_large", file_error.exception.category)
            with self.assertRaises(RepositoryFileError) as total_error:
                read_repository_files(root, ["a.txt", "b.txt"], max_total_bytes=7)
            self.assertEqual("repository_input_too_large", total_error.exception.category)

    def test_detects_replacement_after_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "a.txt"
            path.write_text("first", encoding="utf-8")
            snapshots = read_repository_files(root, [path])
            path.write_text("second", encoding="utf-8")
            with self.assertRaises(RepositoryFileError) as raised:
                verify_repository_files_unchanged(root, snapshots)
            self.assertEqual("repository_file_changed", raised.exception.category)

    def test_read_failure_is_sanitized(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "a.txt"
            path.write_text("content", encoding="utf-8")
            with mock.patch(
                "lakehouse_demo.repository_files.os.open",
                side_effect=OSError("raw provider diagnostic"),
            ):
                with self.assertRaises(RepositoryFileError) as raised:
                    read_repository_files(root, [path])
            self.assertEqual("repository_file_read_failed", raised.exception.category)
            self.assertNotIn("raw provider diagnostic", str(raised.exception))

    def test_writes_new_text_package_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "package"
            write_new_text_package(output, {"b.md": "b\n", "a.json": "{}\n"})
            self.assertEqual("{}\n", (output / "a.json").read_text(encoding="utf-8"))
            self.assertEqual("b\n", (output / "b.md").read_text(encoding="utf-8"))
            with self.assertRaises(RepositoryFileError) as raised:
                write_new_text_package(output, {"other.txt": "other"})
            self.assertEqual("output_directory_exists", raised.exception.category)
            self.assertFalse((output / "other.txt").exists())

    def test_invalid_output_shape_leaves_no_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "package"
            with self.assertRaises(RepositoryFileError) as raised:
                write_new_text_package(output, {"../escape.txt": "bad"})
            self.assertEqual("output_filename_invalid", raised.exception.category)
            self.assertFalse(output.exists())

    def test_symlink_output_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_parent = root / "real"
            real_parent.mkdir()
            linked_parent = root / "linked"
            os.symlink(real_parent, linked_parent)
            with self.assertRaises(RepositoryFileError) as raised:
                write_new_text_package(
                    linked_parent / "package", {"evidence.json": "{}\n"}
                )
            self.assertEqual("output_parent_symlink", raised.exception.category)
            self.assertFalse((real_parent / "package").exists())


if __name__ == "__main__":
    unittest.main()
