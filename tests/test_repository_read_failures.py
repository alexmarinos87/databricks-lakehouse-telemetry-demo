"""Exercise actual repository-file descriptors with controlled OS failure seams."""

from __future__ import annotations

import errno
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_demo import repository_files as files


class RepositoryReadFailureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "input.txt"
        self.path.write_bytes(b"original")
        self.real_open = os.open
        self.real_read = os.read
        self.real_fstat = os.fstat
        self.descriptors = []

    @staticmethod
    def close_if_open(descriptor):
        try:
            os.close(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise

    def remember(self, descriptor):
        self.descriptors.append(descriptor)
        # Mutation probes must not leak a descriptor even when an assertion fails.
        self.addCleanup(self.close_if_open, descriptor)
        return descriptor

    def tracked_open(self, path, flags):
        return self.remember(self.real_open(path, flags))

    def assert_closed(self):
        self.assertTrue(self.descriptors, "The test must reach a real descriptor")
        for descriptor in self.descriptors:
            with self.assertRaises(OSError) as raised:
                self.real_fstat(descriptor)
            self.assertEqual(errno.EBADF, raised.exception.errno)

    def assert_read_failure(self, category, **limits):
        with self.assertRaises(files.RepositoryFileError) as raised:
            files.read_repository_files(self.root, ["input.txt"], **limits)
        self.assertEqual(category, raised.exception.category)
        self.assertNotIn("private-provider-detail", str(raised.exception))
        self.assert_closed()

    def test_multichunk_read_is_bounded_exact_and_closes_descriptor(self):
        payload = b"a" * (2 * 64 * 1024 + 17)
        self.path.write_bytes(payload)
        with mock.patch.object(files.os, "open", side_effect=self.tracked_open), \
             mock.patch.object(files.os, "read", wraps=self.real_read) as read:
            result, = files.read_repository_files(
                self.root, ["input.txt"], max_file_bytes=len(payload),
            )
        self.assertEqual(payload, result.content)
        self.assertEqual(len(payload), result.size_bytes)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), result.sha256)
        self.assertGreaterEqual(read.call_count, 3)
        self.assertTrue(all(0 < call.args[1] <= 64 * 1024 for call in read.call_args_list))
        self.assert_closed()

    def test_read_error_is_sanitized_and_closes_descriptor(self):
        with mock.patch.object(files.os, "open", side_effect=self.tracked_open), \
             mock.patch.object(files.os, "read", side_effect=OSError("private-provider-detail")):
            self.assert_read_failure("repository_file_read_failed")

    def test_opened_and_final_stat_errors_close_descriptor(self):
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                calls = 0

                def stat_with_failure(descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == fail_at:
                        raise OSError("private-provider-detail")
                    return self.real_fstat(descriptor)

                with mock.patch.object(files.os, "open", side_effect=self.tracked_open), \
                     mock.patch.object(files.os, "fstat", side_effect=stat_with_failure):
                    self.assert_read_failure("repository_file_read_failed")
                self.assertEqual(fail_at, calls)

    def test_growth_and_truncation_during_read_are_rejected(self):
        for operation in ("grow", "truncate"):
            with self.subTest(operation=operation):
                self.path.write_bytes(b"a" * (64 * 1024 + 17))
                changed = False

                def read_then_resize(descriptor, limit):
                    nonlocal changed
                    chunk = self.real_read(descriptor, limit)
                    if not changed:
                        changed = True
                        if operation == "grow":
                            with self.path.open("ab") as handle:
                                handle.write(b"extra")
                        else:
                            with self.path.open("wb"):
                                pass
                    return chunk

                with mock.patch.object(files.os, "open", side_effect=self.tracked_open), \
                     mock.patch.object(files.os, "read", side_effect=read_then_resize):
                    self.assert_read_failure("repository_file_changed")
                self.assertTrue(changed)

    def test_same_size_rewrite_during_read_is_rejected(self):
        self.path.write_bytes(b"a" * (64 * 1024 + 17))
        changed = False

        def read_then_rewrite(descriptor, limit):
            nonlocal changed
            chunk = self.real_read(descriptor, limit)
            if not changed:
                changed = True
                metadata = self.path.stat()
                with self.path.open("r+b") as handle:
                    handle.seek(64 * 1024)
                    handle.write(b"b" * 17)
                # Do not depend on the filesystem clock's timestamp resolution.
                os.utime(self.path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000))
            return chunk

        with mock.patch.object(files.os, "open", side_effect=self.tracked_open), \
             mock.patch.object(files.os, "read", side_effect=read_then_rewrite):
            self.assert_read_failure("repository_file_changed")
        self.assertTrue(changed)

    def test_short_read_with_unchanged_metadata_is_rejected(self):
        calls = 0

        def read_then_eof(descriptor, limit):
            nonlocal calls
            calls += 1
            return self.real_read(descriptor, min(limit, 2)) if calls == 1 else b""

        with mock.patch.object(files.os, "open", side_effect=self.tracked_open), \
             mock.patch.object(files.os, "read", side_effect=read_then_eof):
            self.assert_read_failure("repository_file_changed")
        self.assertEqual(2, calls)

    def test_replacement_between_inspection_and_open_is_rejected(self):
        replacement = self.root / "replacement.txt"
        replacement.write_bytes(b"replacement")

        def replace_then_open(path, flags):
            os.replace(replacement, path)
            return self.tracked_open(path, flags)

        with mock.patch.object(files.os, "open", side_effect=replace_then_open):
            self.assert_read_failure("repository_file_changed")
        self.assertEqual(b"replacement", self.path.read_bytes())

    def test_non_regular_opened_descriptor_is_rejected_before_read(self):
        read_descriptor, write_descriptor = os.pipe()
        self.remember(read_descriptor)
        self.addCleanup(self.close_if_open, write_descriptor)
        with mock.patch.object(files.os, "open", return_value=read_descriptor), \
             mock.patch.object(files.os, "read") as read:
            self.assert_read_failure("repository_file_not_regular")
            read.assert_not_called()

    def test_growth_before_open_rechecks_both_byte_budgets(self):
        for limit, category in (
            ("max_file_bytes", "repository_file_too_large"),
            ("max_total_bytes", "repository_input_too_large"),
        ):
            with self.subTest(limit=limit):
                self.path.write_bytes(b"abc")

                def grow_then_open(path, flags):
                    with self.path.open("ab") as handle:
                        handle.write(b"def")
                    return self.tracked_open(path, flags)

                with mock.patch.object(files.os, "open", side_effect=grow_then_open), \
                     mock.patch.object(files.os, "read") as read:
                    self.assert_read_failure(category, **{limit: 4})
                    read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
