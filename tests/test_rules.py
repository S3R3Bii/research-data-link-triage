import copy, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rules import compare, response_class, naive


def observation(code, second=False, **extra):
    row = dict(
        item_id="AUTHORED1",
        dataset_doi="10.0000/study42.authored",
        role="authored_example",
        started_utc=(
            "2026-10-30T20:21:00+00:00" if second else "2026-10-30T20:00:00+00:00"
        ),
        completed_utc=(
            "2026-10-30T20:21:01+00:00" if second else "2026-10-30T20:00:01+00:00"
        ),
        request_made=True,
        terminal_http_status=code,
        censor_reason=None,
        transport_error_type=None,
        data_file_availability="not_tested",
        data_file_body_downloaded=False,
        chain=[],
    )
    row.update(extra)
    return row


class RuleChecks(unittest.TestCase):
    def test_repeated_404_is_review_not_data_loss(self):
        result = compare(observation(404), observation(404, True))
        self.assertEqual(result["conservative_action"], "review_doi_candidate")
        self.assertEqual(result["data_file_availability"], "not_tested")

    def test_mixed_404_410_can_be_reviewed(self):
        self.assertEqual(
            compare(observation(404), observation(410, True))["conservative_action"],
            "review_doi_candidate",
        )

    def test_first_404_awaits_repeat(self):
        self.assertEqual(
            compare(observation(404))["conservative_action"], "await_repeat"
        )

    def test_early_repeat_cannot_confirm_review(self):
        early = observation(404, True, started_utc="2026-10-30T20:20:00+00:00")
        result = compare(observation(404), early)
        self.assertEqual(result["conservative_action"], "unknown")
        self.assertEqual(result["reason"], "repeat_too_early")

    def test_exact_interval_boundary(self):
        exact = observation(404, True, started_utc="2026-10-30T20:20:01+00:00")
        self.assertEqual(
            compare(observation(404), exact)["conservative_action"],
            "review_doi_candidate",
        )

    def test_403_never_becomes_not_found(self):
        result = compare(
            observation(403),
            observation(
                None,
                True,
                request_made=False,
                censor_reason="not_requested_after_denial",
            ),
        )
        self.assertEqual(result["naive_action"], "broken_alert")
        self.assertEqual(result["conservative_action"], "unknown")

    def test_202_is_incomplete_even_if_it_is_2xx(self):
        result = compare(observation(202), observation(202, True))
        self.assertEqual(result["first_response_class"], "accepted_not_completed")
        self.assertEqual(result["conservative_action"], "unknown")
        self.assertEqual(result["naive_action"], "no_broken_alert")

    def test_204_does_not_prove_page_content(self):
        self.assertEqual(response_class(observation(204)), "no_content_response")
        self.assertEqual(compare(observation(204))["conservative_action"], "unknown")

    def test_200_is_only_a_response_observation(self):
        result = compare(observation(200))
        self.assertEqual(result["conservative_action"], "http_response_observed")
        self.assertEqual(result["data_file_availability"], "not_tested")

    def test_later_200_preserves_first_failure(self):
        result = compare(observation(404), observation(200, True))
        self.assertEqual(result["first_response_class"], "not_found_response")
        self.assertEqual(result["reason"], "response_changed_from_not_found")

    def test_rate_limit_is_deferred(self):
        result = compare(
            observation(429, censor_reason="rate_limit_observed"),
            observation(429, True, censor_reason="rate_limit_observed"),
        )
        self.assertEqual(result["naive_action"], "broken_alert")
        self.assertEqual(result["conservative_action"], "unknown")
        self.assertEqual(result["first_response_class"], "rate_limited")

    def test_server_errors_are_deferred(self):
        for code in [500, 502, 503, 504]:
            with self.subTest(code=code):
                self.assertEqual(
                    compare(observation(code), observation(code, True))[
                        "conservative_action"
                    ],
                    "unknown",
                )

    def test_transport_failure_does_not_prove_missing(self):
        result = compare(observation(None, transport_error_type="ReadTimeout"))
        self.assertEqual(result["naive_action"], "broken_alert")
        self.assertEqual(result["conservative_action"], "unknown")

    def test_not_requested_has_no_naive_alert(self):
        result = compare(
            observation(
                None, request_made=False, censor_reason="off_allowlist_not_probed"
            )
        )
        self.assertEqual(result["naive_action"], "unknown_no_alert")
        self.assertEqual(result["conservative_action"], "unknown")

    def test_censored_redirect_cannot_be_not_found(self):
        result = compare(
            observation(302, censor_reason="off_allowlist_not_probed"),
            observation(404, True),
        )
        self.assertEqual(result["conservative_action"], "unknown")

    def test_non_html_404_header_is_still_recorded(self):
        self.assertEqual(
            response_class(
                observation(404, censor_reason="non_html_response_not_consumed")
            ),
            "not_found_response",
        )

    def test_mismatched_identity_is_unavailable(self):
        with self.assertRaises(ValueError):
            compare(observation(404), observation(404, True, item_id="OTHER"))

    def test_timezone_is_required(self):
        with self.assertRaises(ValueError):
            compare(observation(404, started_utc="2026-10-30T20:00:00"))

    def test_bad_status_and_file_claim_are_unavailable(self):
        for extra in [
            dict(terminal_http_status=True),
            dict(terminal_http_status=999),
            dict(data_file_availability="available"),
            dict(data_file_body_downloaded=True),
        ]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                compare(observation(200, **extra))
