"""Denominator fixtures keep unavailable references out of binary metrics."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyze import fraction, summarize


class Denominators(unittest.TestCase):
    def test_zero_denominator_stays_unavailable(self):
        self.assertIsNone(fraction(0, 0)["value"])
        self.assertIsNone(
            summarize([], "conservative_action")["conditional_precision"]["value"]
        )

    def test_unknown_reference_is_not_binary_truth(self):
        rows = [
            dict(reference_label="unknown", conservative_action="review_doi_candidate"),
            dict(reference_label="available_landing", conservative_action="unknown"),
            dict(
                reference_label="not_found_control",
                conservative_action="review_doi_candidate",
            ),
        ]
        result = summarize(rows, "conservative_action")
        self.assertEqual(
            (result["reference_evaluable"], result["reference_unknown"]), (2, 1)
        )
        self.assertEqual(
            (
                result["alert_count"],
                result["tp_control"],
                result["fp_available_reference"],
            ),
            (2, 1, 0),
        )
        self.assertEqual(
            (result["tn_available_reference"], result["deferred_unknown"]), (1, 1)
        )
        self.assertEqual(result["conditional_precision"]["denominator"], 1)


if __name__ == "__main__":
    unittest.main()
