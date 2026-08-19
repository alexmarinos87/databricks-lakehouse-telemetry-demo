import csv
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo import machine_event_contract as contract  # noqa: E402


def valid_row(event_id="TEST-001"):
    return {
        "event_id": event_id,
        "machine_id": "MCH-TEST",
        "event_ts": "2026-04-01T06:00:00Z",
        "site_id": "SITE-TEST",
        "client_id": "CLIENT-TEST",
        "model": "Test Loader",
        "hour_meter": "12.5",
        "event_type": "telemetry",
        "status": "RUNNING",
        "fault_code": "OK",
        "severity": "none",
        "temperature_c": "72.5",
        "vibration_mm_s": "2.1",
        "fuel_level_pct": "50",
        "duration_minutes": "60",
        "downtime_minutes": "0",
        "maintenance_cost_gbp": "0",
        "part_code": "NONE",
        "part_quantity": "0",
        "operator_shift": "day",
    }


def write_fixture(path, rows, header=contract.MACHINE_EVENT_COLUMNS):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(column, "") for column in header])


def error_codes(report):
    return [finding.code for finding in report.error_findings]


class MachineEventContractTest(unittest.TestCase):
    def validate_rows(self, *rows, header=contract.MACHINE_EVENT_COLUMNS):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.csv"
            write_fixture(fixture, rows, header=header)
            return contract.validate_machine_event_files([fixture])

    def test_current_fixtures_have_no_errors_and_one_replay_duplicate(self):
        fixtures = sorted((REPO_ROOT / "data").rglob("*.csv"))

        report = contract.validate_machine_event_files(fixtures)

        self.assertTrue(report.is_valid)
        self.assertEqual((), report.error_findings)
        self.assertEqual(1, len(report.replay_duplicates))
        duplicate = report.replay_duplicates[0]
        self.assertEqual(contract.REPLAY_DUPLICATE, duplicate.code)
        self.assertEqual(
            hashlib.sha256(b"E0008").hexdigest(),
            duplicate.record_id_digest,
        )
        self.assertEqual(10, duplicate.line)
        self.assertEqual(9, duplicate.first_line)
        self.assertNotIn("E0008", repr(report.findings))

    def test_no_fixture_paths_is_an_explicit_error(self):
        report = contract.validate_machine_event_files([])

        self.assertEqual([contract.NO_FIXTURES], error_codes(report))

    def test_header_must_match_without_echoing_candidate_content(self):
        candidate_header_value = "PRIVATE-" + ("x" * 1_000)
        changed_header = list(contract.MACHINE_EVENT_COLUMNS)
        changed_header[0] = candidate_header_value

        report = self.validate_rows(valid_row(), header=tuple(changed_header))

        self.assertEqual([contract.HEADER_MISMATCH], error_codes(report))
        self.assertNotIn(candidate_header_value, repr(report.findings))
        self.assertLess(len(report.error_findings[0].message), 200)

    def test_empty_fixture_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "empty.csv"
            fixture.write_bytes(b"")

            report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.EMPTY_FIXTURE], error_codes(report))

    def test_header_only_fixture_is_an_explicit_error(self):
        report = self.validate_rows()

        self.assertEqual([contract.NO_DATA_ROWS], error_codes(report))

    def test_bad_row_shape_is_reported_without_echoing_values(self):
        candidate_value = "DO-NOT-ECHO"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "shape.csv"
            with fixture.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(contract.MACHINE_EVENT_COLUMNS)
                writer.writerow([candidate_value])

            report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.ROW_SHAPE_INVALID], error_codes(report))
        self.assertNotIn(candidate_value, repr(report.findings))

    def test_invalid_field_content_is_not_echoed(self):
        candidate_value = "PRIVATE-NUMERIC-CONTENT"
        row = valid_row()
        row["hour_meter"] = candidate_value

        report = self.validate_rows(row)

        self.assertEqual([contract.DECIMAL_INVALID], error_codes(report))
        self.assertNotIn(candidate_value, repr(report.findings))

    def test_diagnostic_source_location_is_bounded(self):
        candidate_path = "private-" + ("x" * 1_000)

        report = contract.validate_machine_event_files([candidate_path])

        self.assertEqual([contract.FILE_READ_ERROR], error_codes(report))
        self.assertLessEqual(len(report.findings[0].source), 80)
        self.assertNotIn(candidate_path, repr(report.findings))

    def test_invalid_path_returns_a_bounded_structured_error(self):
        candidate_path = "private\x00fixture.csv"

        report = contract.validate_machine_event_files([candidate_path])

        self.assertEqual([contract.FILE_READ_ERROR], error_codes(report))
        self.assertTrue(report.findings[0].source.startswith("<path-sha256:"))
        self.assertNotIn(candidate_path, repr(report.findings))

    def test_malformed_csv_returns_a_bounded_parse_error(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "malformed.csv"
            header = ",".join(contract.MACHINE_EVENT_COLUMNS)
            fixture.write_text(f'{header}\n"unterminated', encoding="utf-8")

            report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.CSV_PARSE_ERROR], error_codes(report))
        self.assertNotIn("unterminated", repr(report.findings))

    def test_non_utf8_fixture_returns_a_structured_read_error(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.csv"
            fixture.write_bytes(b"\xff\xfe\x00")

            report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.FILE_READ_ERROR], error_codes(report))

    def test_symbolic_link_fixture_is_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are not supported on this platform")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.csv"
            link = root / "link.csv"
            write_fixture(target, [valid_row()])
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            report = contract.validate_machine_event_files([link])

        self.assertEqual([contract.SYMLINK_NOT_ALLOWED], error_codes(report))

    def test_regular_file_open_uses_platform_safety_flags(self):
        if not getattr(os, "O_NOFOLLOW", 0):
            self.skipTest("O_NOFOLLOW is not available on this platform")

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.csv"
            write_fixture(fixture, [valid_row()])
            real_open = os.open
            captured_flags = []

            def open_and_capture(path, flags):
                captured_flags.append(flags)
                return real_open(path, flags)

            with patch.object(contract.os, "open", side_effect=open_and_capture):
                report = contract.validate_machine_event_files([fixture])

        self.assertTrue(report.is_valid)
        self.assertTrue(captured_flags[0] & os.O_NOFOLLOW)
        for flag_name in ("O_NONBLOCK", "O_NOCTTY"):
            flag = getattr(os, flag_name, 0)
            if flag:
                self.assertTrue(captured_flags[0] & flag)

    def test_opened_descriptor_must_still_be_regular(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.csv"
            write_fixture(fixture, [valid_row()])

            with patch.object(
                contract.os,
                "fstat",
                return_value=SimpleNamespace(st_mode=0),
            ):
                report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.NON_REGULAR_FILE], error_codes(report))

    def test_opened_descriptor_identity_must_match_lstat(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.csv"
            write_fixture(fixture, [valid_row()])
            metadata = fixture.stat()
            mismatched = SimpleNamespace(
                st_mode=metadata.st_mode,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino + 1,
            )

            with patch.object(contract.os, "fstat", return_value=mismatched):
                report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.FILE_IDENTITY_CHANGED], error_codes(report))

    def test_descriptor_metadata_change_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.csv"
            write_fixture(fixture, [valid_row()])
            opened = fixture.stat()
            changed = SimpleNamespace(
                st_dev=opened.st_dev,
                st_ino=opened.st_ino,
                st_size=opened.st_size,
                st_mtime_ns=opened.st_mtime_ns + 1,
                st_ctime_ns=opened.st_ctime_ns,
            )

            with patch.object(contract.os, "fstat", side_effect=[opened, changed]):
                report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.FILE_IDENTITY_CHANGED], error_codes(report))

    def test_non_regular_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture_directory = Path(directory) / "not-a-file.csv"
            fixture_directory.mkdir()

            report = contract.validate_machine_event_files([fixture_directory])

        self.assertEqual([contract.NON_REGULAR_FILE], error_codes(report))

    def test_per_file_size_limit_is_enforced_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "large.csv"
            write_fixture(fixture, [valid_row()])
            limit = fixture.stat().st_size - 1

            with patch.object(contract, "MAX_FILE_BYTES", limit):
                report = contract.validate_machine_event_files([fixture])

        self.assertEqual([contract.FILE_SIZE_LIMIT_EXCEEDED], error_codes(report))

    def test_total_input_size_limit_is_enforced_across_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.csv"
            second = root / "b.csv"
            write_fixture(first, [valid_row("SIZE-1")])
            write_fixture(second, [valid_row("SIZE-2")])
            total_limit = first.stat().st_size + second.stat().st_size - 1

            with patch.object(contract, "MAX_TOTAL_INPUT_BYTES", total_limit):
                report = contract.validate_machine_event_files([second, first])

        self.assertEqual([contract.TOTAL_SIZE_LIMIT_EXCEEDED], error_codes(report))

    def test_file_count_limit_stops_before_file_access(self):
        with patch.object(contract, "MAX_FIXTURE_FILES", 1):
            report = contract.validate_machine_event_files(["missing-a.csv", "missing-b.csv"])

        self.assertEqual([contract.FILE_COUNT_LIMIT_EXCEEDED], error_codes(report))

    def test_file_count_limit_also_bounds_repeated_path_entries(self):
        with patch.object(contract, "MAX_FIXTURE_FILES", 1):
            report = contract.validate_machine_event_files(["missing.csv", "missing.csv"])

        self.assertEqual([contract.FILE_COUNT_LIMIT_EXCEEDED], error_codes(report))

    def test_total_row_limit_is_enforced(self):
        with patch.object(contract, "MAX_TOTAL_ROWS", 1):
            report = self.validate_rows(valid_row("ROW-1"), valid_row("ROW-2"))

        self.assertEqual([contract.ROW_LIMIT_EXCEEDED], error_codes(report))

    def test_finding_count_is_bounded_and_reports_truncation(self):
        row = valid_row()
        for field in contract.REQUIRED_KEY_COLUMNS:
            row[field] = ""

        with patch.object(contract, "MAX_FINDINGS", 2):
            report = self.validate_rows(row)

        self.assertEqual(2, len(report.findings))
        self.assertEqual(contract.FINDING_LIMIT_EXCEEDED, report.findings[-1].code)

    def test_required_source_keys_cannot_be_blank(self):
        rows = []
        for index, field in enumerate(contract.REQUIRED_KEY_COLUMNS):
            row = valid_row(f"REQUIRED-{index}")
            row[field] = " "
            rows.append(row)

        report = self.validate_rows(*rows)

        missing_fields = {
            finding.field
            for finding in report.error_findings
            if finding.code == contract.REQUIRED_KEY_MISSING
        }
        self.assertEqual(set(contract.REQUIRED_KEY_COLUMNS), missing_fields)

    def test_timestamps_require_exact_utc_shape_and_valid_calendar_values(self):
        invalid_values = (
            "2026-04-01T06:00:00+00:00",
            "2026-04-01 06:00:00Z",
            "2026-04-01T06:00:00.123Z",
            "2026-02-30T06:00:00Z",
            "٢٠٢٦-٠٤-٠١T٠٦:٠٠:٠٠Z",
        )
        rows = []
        for index, value in enumerate(invalid_values):
            row = valid_row(f"TIME-{index}")
            row["event_ts"] = value
            rows.append(row)

        report = self.validate_rows(*rows)

        timestamp_errors = [
            finding
            for finding in report.error_findings
            if finding.code == contract.UTC_TIMESTAMP_INVALID
        ]
        self.assertEqual(len(invalid_values), len(timestamp_errors))
        self.assertEqual({"event_ts"}, {finding.field for finding in timestamp_errors})

    def test_decimal_columns_require_finite_decimal_values(self):
        rows = []
        invalid_values = ("not-a-number", "NaN", "Infinity")
        for index, field in enumerate(contract.DECIMAL_COLUMNS):
            row = valid_row(f"DECIMAL-{index}")
            row[field] = invalid_values[index % len(invalid_values)]
            rows.append(row)

        report = self.validate_rows(*rows)

        decimal_errors = [
            finding
            for finding in report.error_findings
            if finding.code == contract.DECIMAL_INVALID
        ]
        self.assertEqual(set(contract.DECIMAL_COLUMNS), {finding.field for finding in decimal_errors})

    def test_maintenance_cost_must_be_non_negative(self):
        row = valid_row("NEGATIVE-COST")
        row["maintenance_cost_gbp"] = "-0.01"

        report = self.validate_rows(row)

        violations = [
            finding
            for finding in report.error_findings
            if finding.code == contract.NON_NEGATIVE_VIOLATION
        ]
        self.assertEqual(["maintenance_cost_gbp"], [finding.field for finding in violations])

    def test_integer_columns_accept_canonical_spark_int_boundaries(self):
        lower = valid_row("INT-LOWER")
        upper = valid_row("INT-UPPER")
        for field in contract.INTEGER_COLUMNS:
            lower[field] = "0"
            upper[field] = str(contract.MAX_SPARK_INT)

        report = self.validate_rows(lower, upper)

        self.assertTrue(report.is_valid)

    def test_integer_columns_reject_noncanonical_and_fractional_spellings(self):
        invalid_values = ("-1", "+1", "01", "1.0", "1e1", " 1 ")
        rows = []
        for index, value in enumerate(invalid_values):
            row = valid_row(f"INT-FORMAT-{index}")
            row[contract.INTEGER_COLUMNS[index % len(contract.INTEGER_COLUMNS)]] = value
            rows.append(row)

        report = self.validate_rows(*rows)

        format_errors = [
            finding
            for finding in report.error_findings
            if finding.code == contract.INTEGER_FORMAT_INVALID
        ]
        self.assertEqual(len(invalid_values), len(format_errors))

    def test_integer_columns_reject_values_above_spark_int_maximum(self):
        rows = []
        for index, field in enumerate(contract.INTEGER_COLUMNS):
            row = valid_row(f"INT-RANGE-{index}")
            row[field] = str(contract.MAX_SPARK_INT + 1)
            rows.append(row)
        very_large = valid_row("INT-RANGE-LONG")
        very_large["duration_minutes"] = "9" * 5_000
        rows.append(very_large)

        report = self.validate_rows(*rows)

        range_errors = [
            finding
            for finding in report.error_findings
            if finding.code == contract.INTEGER_RANGE_VIOLATION
        ]
        self.assertEqual(4, len(range_errors))
        self.assertEqual(set(contract.INTEGER_COLUMNS), {finding.field for finding in range_errors})

    def test_fuel_level_includes_boundaries_and_rejects_out_of_range_values(self):
        rows = []
        for index, value in enumerate(("0", "100", "-0.01", "100.01")):
            row = valid_row(f"FUEL-{index}")
            row["fuel_level_pct"] = value
            rows.append(row)

        report = self.validate_rows(*rows)

        range_errors = [
            finding
            for finding in report.error_findings
            if finding.code == contract.FUEL_RANGE_VIOLATION
        ]
        self.assertEqual(2, len(range_errors))

    def test_identical_duplicate_is_an_informational_replay(self):
        row = valid_row("REPLAY-001")

        report = self.validate_rows(row, dict(row))

        self.assertTrue(report.is_valid)
        self.assertEqual(1, len(report.replay_duplicates))
        duplicate = report.replay_duplicates[0]
        self.assertEqual(contract.REPLAY_DUPLICATE, duplicate.code)
        self.assertEqual(2, duplicate.first_line)
        self.assertEqual(3, duplicate.line)
        self.assertNotIn("REPLAY-001", repr(report.findings))

    def test_different_payload_for_same_event_id_is_an_error(self):
        first = valid_row("CONFLICT-001")
        second = dict(first, model="Different Loader")

        report = self.validate_rows(first, second)

        conflicts = [
            finding
            for finding in report.error_findings
            if finding.code == contract.CONFLICTING_DUPLICATE
        ]
        self.assertEqual(1, len(conflicts))
        self.assertEqual(2, conflicts[0].first_line)
        self.assertEqual(3, conflicts[0].line)
        self.assertNotIn("CONFLICT-001", repr(report.findings))

    def test_downtime_may_exceed_duration_until_business_semantics_are_decided(self):
        row = valid_row("DOWNTIME-001")
        row["duration_minutes"] = "5"
        row["downtime_minutes"] = "55"

        report = self.validate_rows(row)

        self.assertTrue(report.is_valid)

    def test_file_order_does_not_change_duplicate_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "a.csv"
            second_path = root / "b.csv"
            row = valid_row("CROSS-FILE-001")
            write_fixture(first_path, [row])
            write_fixture(second_path, [dict(row)])

            forward = contract.validate_machine_event_files([first_path, second_path])
            reversed_order = contract.validate_machine_event_files([second_path, first_path])

        self.assertEqual(forward, reversed_order)
        self.assertEqual(1, len(forward.replay_duplicates))


if __name__ == "__main__":
    unittest.main()
