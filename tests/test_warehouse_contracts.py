import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lakehouse_demo.warehouse_contracts import (  # noqa: E402
    FAILURE_FACT,
    UPTIME_FACT,
    WarehouseContractFinding,
    evaluate_warehouse_contracts,
)


DIMENSION_MEMBERS = {
    "date_key": [20250101],
    "client_key": [101],
    "machine_key": [201, 202],
    "model_key": [301, 302],
    "site_key": [401, 402],
    "fault_key": [501],
}


def uptime_source(machine_id="M-001", site_id="S-001", model="EX-100"):
    return {
        "event_date": "2025-01-01",
        "machine_id": machine_id,
        "client_id": "C-001",
        "site_id": site_id,
        "model": model,
    }


def uptime_fact(machine_key=201, **overrides):
    row = {
        "date_key": 20250101,
        "client_key": 101,
        "machine_key": machine_key,
        "model_key": 301,
        "site_key": 401,
    }
    row.update(overrides)
    return row


def failure_source(event_id="E-001", machine_id="M-001"):
    return {
        "event_id": event_id,
        "machine_id": machine_id,
        "client_id": "C-001",
        "site_id": "S-001",
        "model": "EX-100",
    }


def failure_fact(event_id="E-001", **overrides):
    row = {
        "event_id": event_id,
        "date_key": 20250101,
        "client_key": 101,
        "machine_key": 201,
        "model_key": 301,
        "site_key": 401,
        "fault_key": 501,
    }
    row.update(overrides)
    return row


def evaluate(
    *,
    uptime_sources=None,
    failure_sources=None,
    uptime_facts=None,
    failure_facts=None,
    dimension_members=None,
):
    return evaluate_warehouse_contracts(
        uptime_source_rows=uptime_sources if uptime_sources is not None else [uptime_source()],
        failure_source_rows=(
            failure_sources if failure_sources is not None else [failure_source()]
        ),
        uptime_fact_rows=uptime_facts if uptime_facts is not None else [uptime_fact()],
        failure_fact_rows=(failure_facts if failure_facts is not None else [failure_fact()]),
        dimension_members=(
            dimension_members if dimension_members is not None else DIMENSION_MEMBERS
        ),
    )


class WarehouseContractsTest(unittest.TestCase):
    def test_valid_rows_return_stable_empty_findings(self):
        self.assertEqual((), evaluate())
        self.assertEqual((), evaluate())

    def test_same_day_assignment_change_reports_conflict_and_duplicate_uptime_grain(self):
        sources = [
            uptime_source(site_id="S-002", model="EX-200"),
            uptime_source(),
        ]
        facts = [
            uptime_fact(site_key=402, model_key=302),
            uptime_fact(),
        ]

        findings = evaluate(uptime_sources=sources, uptime_facts=facts)

        self.assertEqual(
            ["machine_assignment_conflict", "duplicate_uptime_grain"],
            [finding.code for finding in findings],
        )
        self.assertEqual((("machine_id", "M-001"),), findings[0].keys)
        self.assertEqual(
            (("date_key", 20250101), ("machine_key", 201)),
            findings[1].keys,
        )
        self.assertEqual((("row_count", 2),), findings[1].details)

    def test_conflicts_are_deterministic_and_identify_each_machine(self):
        rows = [
            uptime_source("M-002", "S-002", "EX-200"),
            uptime_source("M-001", "S-002", "EX-200"),
            uptime_source("M-002"),
            uptime_source("M-001"),
        ]

        forwards = evaluate(
            uptime_sources=rows,
            uptime_facts=[uptime_fact(201), uptime_fact(202)],
        )
        backwards = evaluate(
            uptime_sources=reversed(rows),
            uptime_facts=[uptime_fact(202), uptime_fact(201)],
        )
        conflicts = [
            finding for finding in forwards if finding.code == "machine_assignment_conflict"
        ]

        self.assertEqual(forwards, backwards)
        self.assertEqual(
            [(("machine_id", "M-001"),), (("machine_id", "M-002"),)],
            [finding.keys for finding in conflicts],
        )
        self.assertTrue(all(finding.details[0][0] == "assignments" for finding in conflicts))

    def test_duplicate_failure_event_id_reports_failure_fact_grain(self):
        findings = evaluate(
            failure_sources=[failure_source(), failure_source()],
            failure_facts=[failure_fact(), failure_fact()],
        )

        self.assertIn(
            WarehouseContractFinding(
                code="duplicate_failure_grain",
                dataset=FAILURE_FACT,
                keys=(("event_id", "E-001"),),
                details=(("row_count", 2),),
            ),
            findings,
        )

    def test_two_machines_on_the_same_date_are_distinct_uptime_facts(self):
        findings = evaluate(
            uptime_sources=[uptime_source("M-001"), uptime_source("M-002")],
            uptime_facts=[uptime_fact(201), uptime_fact(202)],
        )

        self.assertNotIn("duplicate_uptime_grain", [finding.code for finding in findings])

    def test_missing_and_null_fact_grain_keys_are_reported_without_duplicates(self):
        missing_date = uptime_fact()
        del missing_date["date_key"]
        findings = evaluate(
            uptime_facts=[missing_date, uptime_fact(machine_key=None)],
            failure_facts=[failure_fact(event_id=None)],
        )

        self.assertEqual(
            ["missing_grain_key", "null_grain_key", "null_grain_key"],
            [
                finding.code
                for finding in findings
                if finding.code in {"missing_grain_key", "null_grain_key"}
            ],
        )
        self.assertNotIn(
            "duplicate_uptime_grain",
            [finding.code for finding in findings],
        )

    def test_null_missing_and_unmatched_dimension_keys_are_reported(self):
        incomplete_uptime = uptime_fact(client_key=None, site_key=999)
        del incomplete_uptime["model_key"]
        findings = evaluate(uptime_facts=[incomplete_uptime])

        dimension_findings = [
            finding
            for finding in findings
            if finding.code.endswith("dimension_key") and finding.dataset == UPTIME_FACT
        ]

        self.assertEqual(
            ["missing_dimension_key", "null_dimension_key", "unmatched_dimension_key"],
            [finding.code for finding in dimension_findings],
        )
        self.assertEqual(
            {("model_key", None), ("client_key", None), ("site_key", 999)},
            {finding.keys[-1] for finding in dimension_findings},
        )

    def test_failure_fact_reports_unmatched_fault_member(self):
        findings = evaluate(failure_facts=[failure_fact(fault_key=999)])

        self.assertIn(
            WarehouseContractFinding(
                code="unmatched_dimension_key",
                dataset=FAILURE_FACT,
                keys=(("event_id", "E-001"), ("fault_key", 999)),
                details=(("row_count", 1),),
            ),
            findings,
        )

    def test_source_to_fact_count_loss_is_reported_for_each_fact(self):
        findings = evaluate(
            uptime_sources=[uptime_source(), uptime_source("M-002")],
            failure_sources=[failure_source(), failure_source("E-002")],
            uptime_facts=[uptime_fact()],
            failure_facts=[],
        )

        losses = [
            finding
            for finding in findings
            if finding.code == "source_fact_count_mismatch"
        ]

        self.assertEqual([UPTIME_FACT, FAILURE_FACT], [finding.dataset for finding in losses])
        self.assertEqual(
            [
                (
                    ("source_row_count", 2),
                    ("fact_row_count", 1),
                    ("net_missing_row_count", 1),
                    ("net_unexpected_row_count", 0),
                ),
                (
                    ("source_row_count", 2),
                    ("fact_row_count", 0),
                    ("net_missing_row_count", 2),
                    ("net_unexpected_row_count", 0),
                ),
            ],
            [finding.details for finding in losses],
        )

    def test_source_to_fact_count_gain_is_reported(self):
        findings = evaluate(
            uptime_sources=[uptime_source()],
            uptime_facts=[uptime_fact(201), uptime_fact(202)],
        )

        mismatch = next(
            finding
            for finding in findings
            if finding.code == "source_fact_count_mismatch"
            and finding.dataset == UPTIME_FACT
        )
        self.assertEqual(
            (
                ("source_row_count", 1),
                ("fact_row_count", 2),
                ("net_missing_row_count", 0),
                ("net_unexpected_row_count", 1),
            ),
            mismatch.details,
        )

    def test_typed_assignment_and_dimension_membership_do_not_merge_bool_and_int(self):
        findings = evaluate(
            uptime_sources=[
                uptime_source(site_id=1),
                uptime_source(site_id=True),
            ],
            uptime_facts=[uptime_fact(client_key=True)],
        )

        self.assertIn("machine_assignment_conflict", [finding.code for finding in findings])
        self.assertTrue(
            any(
                finding.code == "unmatched_dimension_key"
                and finding.keys[-1] == ("client_key", True)
                for finding in findings
            )
        )

    def test_omitted_dimension_member_collection_fails_closed(self):
        members = dict(DIMENSION_MEMBERS)
        del members["site_key"]

        findings = evaluate(dimension_members=members)

        self.assertTrue(
            any(
                finding.code == "unmatched_dimension_key"
                and finding.keys[-1] == ("site_key", 401)
                for finding in findings
            )
        )


if __name__ == "__main__":
    unittest.main()
