import csv
import sys
import unittest
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.azure_ingestion import MACHINE_EVENT_COLUMNS  # noqa: E402


SAMPLE_FILE = REPO_ROOT / "data" / "sample_machine_events.csv"
INCREMENT_FILES = sorted((REPO_ROOT / "data" / "increments").glob("*.csv"))


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_event_ts(row):
    return datetime.fromisoformat(row["event_ts"].replace("Z", "+00:00"))


class IncrementalIngestionContractTest(unittest.TestCase):
    def test_increment_files_exist(self):
        self.assertGreaterEqual(len(INCREMENT_FILES), 1)

    def test_increment_headers_match_machine_event_contract(self):
        for increment_file in INCREMENT_FILES:
            with increment_file.open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle))

            self.assertEqual(list(MACHINE_EVENT_COLUMNS), header)

    def test_increment_event_ids_do_not_collide_with_initial_sample(self):
        sample_ids = {row["event_id"] for row in read_rows(SAMPLE_FILE)}

        for increment_file in INCREMENT_FILES:
            increment_ids = [row["event_id"] for row in read_rows(increment_file)]

            with self.subTest(increment_file=increment_file.name):
                self.assertFalse(sample_ids.intersection(increment_ids))
                self.assertEqual(len(increment_ids), len(set(increment_ids)))

    def test_increment_events_arrive_after_initial_sample(self):
        sample_max_ts = max(parse_event_ts(row) for row in read_rows(SAMPLE_FILE))

        for increment_file in INCREMENT_FILES:
            for row in read_rows(increment_file):
                with self.subTest(increment_file=increment_file.name, event_id=row["event_id"]):
                    self.assertGreater(parse_event_ts(row), sample_max_ts)


if __name__ == "__main__":
    unittest.main()
