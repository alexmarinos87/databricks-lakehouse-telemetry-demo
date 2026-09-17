"""Exact cost arithmetic and real-profile regressions without a currency rounding policy."""

import itertools
import random
import sys
import tempfile
import unittest
from decimal import Decimal, Inexact, ROUND_DOWN, ROUND_UP, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lakehouse_demo import dataset_profile as subject


def total_text(values):
    total = Decimal("0")
    for value in values:
        total = subject._add_profile_cost(total, value)
    return subject._decimal_text(total)


class ExactCostArithmeticTest(unittest.TestCase):
    def test_default_precision_does_not_round_long_source_amounts(self):
        with localcontext() as context:
            context.prec = 28
            self.assertEqual("1234567890123456789012345678.91", total_text([
                "1234567890123456789012345678.90", "0.01",
            ]))

    def test_caller_precision_rounding_exponents_traps_and_flags_are_unchanged(self):
        for rounding in (ROUND_DOWN, ROUND_UP):
            with self.subTest(rounding=rounding), localcontext() as context:
                context.prec = 2
                context.rounding = rounding
                context.Emax = 2
                context.Emin = -2
                context.traps[Inexact] = True
                context.clear_flags()
                before = repr(context)
                self.assertEqual("123456789.0100001", total_text(["123456789.01", "0.0000001"]))
                self.assertEqual(before, repr(context))

    def test_permutations_preserve_small_terms(self):
        values = ["1234567890123456789012345678.90", "0.01", "0.0001"]
        observed = {total_text(items) for items in itertools.permutations(values)}
        self.assertEqual({"1234567890123456789012345678.9101"}, observed)

    def test_extreme_coefficients_and_exponents_fail_before_formatting(self):
        for value in ("9" * 1001, "1e1001", "1e-1001", "0e100000000"):
            with self.subTest(value_length=len(value)):
                with self.assertRaises(subject.DatasetProfileError) as raised:
                    total_text([value])
                self.assertEqual("machine_event_profile_cost_limit_exceeded", raised.exception.category)
                self.assertEqual(raised.exception.category, str(raised.exception))

    def test_maximum_supported_span_remains_exact(self):
        expected = "9" * 1000 + "0" * 1000 + "." + "0" * 999 + "1"
        self.assertEqual(expected, total_text(["9" * 1000 + "e1000", "1e-1000"]))

    def test_zero_and_exponent_notation_are_canonical(self):
        self.assertEqual("0", total_text(["-0.00", "0e-1000"]))
        self.assertEqual("12.5", total_text(["1.25e1", "0.000"]))

    def test_nonfinite_negative_and_invalid_amounts_fail_closed(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity", "-1", "not-a-number"):
            with self.subTest(value=value):
                with self.assertRaises(subject.DatasetProfileError):
                    total_text([value])

    def test_random_amounts_match_independent_integer_reference(self):
        randomizer = random.Random(20260913)
        units = [randomizer.randrange(10**15) for _ in range(200)]
        values = [f"{value // 10**6}.{value % 10**6:06d}" for value in units]
        expected_units = sum(units)
        expected = f"{expected_units // 10**6}.{expected_units % 10**6:06d}".rstrip("0").rstrip(".")
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(expected, total_text(values))

    def test_existing_fixture_amount_stays_unchanged(self):
        self.assertEqual("4230", total_text(["4230"]))


class DatasetCostProfileTest(unittest.TestCase):
    def profile(self, costs, replay=False):
        # Reuse genuine fixture construction and the real source validator/profiler.
        from test_dataset_profile import make_row, write_csv
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [make_row(f"E{index}", "2026-04-01T06:00:00Z", cost=cost)
                    for index, cost in enumerate(costs)]
            if replay:
                rows.append(rows[0])
            write_csv(root / "events.csv", rows)
            return subject.profile_machine_event_files(root, ["events.csv"])

    def test_real_profile_keeps_exact_total_and_excludes_replay(self):
        result = self.profile(["1234567890123456789012345678.90", "0.01"], replay=True)
        self.assertEqual("1234567890123456789012345678.91",
                         result["operations"]["maintenance_cost_gbp_total"])
        self.assertEqual(3, result["rows"]["physical_row_count"])
        self.assertEqual(2, result["rows"]["unique_event_id_count"])
        self.assertEqual(1, result["rows"]["replay_duplicate_row_count"])

    def test_real_profile_is_independent_of_caller_arithmetic(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            self.assertEqual("123.456", self.profile(["123.45", "0.006"])
                             ["operations"]["maintenance_cost_gbp_total"])

    def test_real_profile_rejects_oversized_decimal_representation(self):
        for cost in ("1e1001", "1e-1001", "9" * 1001):
            with self.subTest(length=len(cost)):
                with self.assertRaises(subject.DatasetProfileError) as raised:
                    self.profile([cost])
                self.assertEqual("machine_event_profile_cost_limit_exceeded", raised.exception.category)

    def test_committed_source_profile_remains_compatible(self):
        result = subject.profile_machine_event_files(ROOT, subject.default_machine_event_sources(ROOT))
        self.assertEqual("4230", result["operations"]["maintenance_cost_gbp_total"])
        self.assertEqual(31, result["rows"]["physical_row_count"])
        self.assertEqual(30, result["rows"]["unique_event_id_count"])


if __name__ == "__main__":
    unittest.main()
