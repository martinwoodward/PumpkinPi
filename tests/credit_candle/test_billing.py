import unittest

from .support import period
from pumpkinpi_core.billing import (
    BillingError, exact_microcredits, normalize_usage, response_period,
)


MAPPING = {
    "unit_type": "credit", "product": "copilot", "sku": "ai-credit",
    "quantity_field": "grossQuantity",
}


class BillingTests(unittest.TestCase):
    def test_exact_aggregation_and_precision_bounds(self):
        self.assertEqual(exact_microcredits(["1.000001", "2.5"]), 3_500_001)
        self.assertEqual(exact_microcredits([
            "1.000000000000000000000000000000", "0.000001"]), 1_000_001)
        for values in (["0.0000001"], [True], [1.2], ["bad"], ["-1"],
                       ["NaN"], ["Infinity"], ["1", "-0.5"]):
            with self.assertRaises(BillingError):
                exact_microcredits(values)

    def test_response_period_requires_exact_month(self):
        self.assertEqual(response_period({"year": 2026, "month": 9}), "2026-09")
        for value in ("2026-09-extra", {"year": True, "month": 9},
                      {"year": 2026, "month": "9"}):
            with self.assertRaises(BillingError):
                response_period(value)

    def test_mapping_filters_exactly_and_period_is_immutable(self):
        current = period()
        data = {"timePeriod": current, "usageItems": [
            {"unitType": "credit", "product": "copilot", "sku": "ai-credit",
             "grossQuantity": "2.25"},
            {"unitType": "money", "product": "copilot", "sku": "ai-credit",
             "grossQuantity": "999"},
        ]}
        source = {"mapping": MAPPING, "allowance_microcredits": 5_000_000}
        self.assertEqual(normalize_usage(data, source, current), (2_250_000, 5_000_000))
        with self.assertRaises(BillingError):
            normalize_usage(data, source, "2025-01")
        with self.assertRaises(BillingError):
            normalize_usage(data, {"allowance_microcredits": 1}, current)

    def test_empty_report_and_unlimited_require_explicit_configuration(self):
        current = period()
        data = {"timePeriod": current, "usageItems": []}
        source = {"mapping": MAPPING, "allowance_microcredits": 10}
        self.assertEqual(normalize_usage(data, source, current), (0, 10))
        source.update(unlimited=True, allowance_microcredits=None)
        self.assertEqual(normalize_usage(data, source, current), (0, None))
