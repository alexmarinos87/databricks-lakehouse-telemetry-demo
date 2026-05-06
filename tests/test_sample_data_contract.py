import csv
import unittest
from collections import Counter
from pathlib import Path


DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_machine_events.csv"

EXPECTED_COLUMNS = [
    "event_id",
    "machine_id",
    "event_ts",
    "site_id",
    "client_id",
    "model",
    "hour_meter",
    "event_type",
    "status",
    "fault_code",
    "severity",
    "temperature_c",
    "vibration_mm_s",
    "fuel_level_pct",
    "duration_minutes",
    "downtime_minutes",
    "maintenance_cost_gbp",
    "part_code",
    "part_quantity",
    "operator_shift",
]


def load_rows():
    with DATA_FILE.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class SampleDataContractTest(unittest.TestCase):
    def test_header_matches_expected_schema(self):
        with DATA_FILE.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)

        self.assertEqual(EXPECTED_COLUMNS, header)

    def test_rows_have_required_business_keys(self):
        for row in load_rows():
            with self.subTest(event_id=row.get("event_id")):
                self.assertTrue(row["event_id"])
                self.assertTrue(row["machine_id"])
                self.assertTrue(row["event_ts"])
                self.assertTrue(row["site_id"])
                self.assertTrue(row["client_id"])

    def test_numeric_fields_parse(self):
        numeric_columns = [
            "hour_meter",
            "temperature_c",
            "vibration_mm_s",
            "fuel_level_pct",
            "duration_minutes",
            "downtime_minutes",
            "maintenance_cost_gbp",
            "part_quantity",
        ]

        for row in load_rows():
            for column in numeric_columns:
                with self.subTest(event_id=row["event_id"], column=column):
                    float(row[column])

    def test_sample_contains_dedupe_scenario(self):
        event_counts = Counter(row["event_id"] for row in load_rows())
        duplicate_event_ids = [event_id for event_id, count in event_counts.items() if count > 1]

        self.assertEqual(["E0008"], duplicate_event_ids)

    def test_sample_contains_reporting_scenarios(self):
        rows = load_rows()
        statuses = {row["status"] for row in rows}
        part_codes = {row["part_code"] for row in rows}

        self.assertIn("FAULT", statuses)
        self.assertIn("MAINTENANCE", statuses)
        self.assertGreater(len(part_codes - {"NONE"}), 0)


if __name__ == "__main__":
    unittest.main()
