import sys, unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from http_guards import check_url, public_url, retry_deadline, repeat_due
from identifiers import canonical_doi


class TransportGuards(unittest.TestCase):
    def test_real_doi_wrappers_and_case(self):
        self.assertEqual(
            canonical_doi(" HTTPS://doi.org/10.6084/M9.FIGSHARE.123.v1 "),
            "10.6084/m9.figshare.123.v1",
        )

    def test_ambiguous_identifier_inputs(self):
        for value in [
            "10.5061/a?token=x",
            "10.5061/a#b",
            "10.5061/a b",
            "10.5061/a%2fb",
            "https://evil.example/10.5061/a",
            "https://user:pass@doi.org/10.5061/a",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_doi(value)

    def test_public_repository_urls(self):
        for value in [
            "https://doi.org/10.5061/dryad.example",
            "https://datadryad.org/stash/dataset/doi:10.5061/example",
            "https://figshare.com/articles/dataset/example/123",
            "https://institution.figshare.com/articles/dataset/example/123",
        ]:
            with self.subTest(value=value):
                self.assertIsNone(check_url(value))

    def test_private_and_offsite_targets(self):
        for value in [
            "http://figshare.com/x",
            "https://127.0.0.1/",
            "https://10.0.0.1/",
            "https://[::1]/",
            "https://8.8.8.8/",
            "https://figshare.com.evil.example/x",
            "https://localhost/x",
            "file:///private.txt",
            "https://doi.org:444/x",
        ]:
            with self.subTest(value=value):
                self.assertIsNotNone(check_url(value))

    def test_credentials_and_signed_queries(self):
        for value in [
            "https://u:p@figshare.com/x",
            "https://figshare.com/x?token=abc",
            "https://figshare.com/x?signature=INVALID_EXAMPLE_VALUE",
        ]:
            with self.subTest(value=value):
                self.assertIsNotNone(check_url(value))

    def test_file_bodies_are_not_targets(self):
        for value in [
            "https://ndownloader.figshare.com/files/123",
            "https://zenodo.org/records/1/files/data.csv",
            "https://figshare.com/ndownloader/files/123",
        ]:
            with self.subTest(value=value):
                self.assertEqual(check_url(value), "file_download_not_probed")

    def test_public_receipt_strips_query_fragment(self):
        self.assertEqual(
            public_url("https://figshare.com/articles/x/123?utm_source=test#details"),
            "https://figshare.com/articles/x/123",
        )
        self.assertEqual(
            public_url("https://private.example/secret"), "redacted_disallowed_target"
        )

    def test_retry_after_seconds_and_floor(self):
        t = "2026-10-30T20:00:00+00:00"
        self.assertEqual(retry_deadline("120", t), "2026-10-30T20:02:00+00:00")
        self.assertEqual(retry_deadline("1", t), "2026-10-30T20:01:00+00:00")

    def test_retry_after_http_date(self):
        self.assertEqual(
            retry_deadline(
                "Fri, 30 Oct 2026 20:05:00 GMT", "2026-10-30T20:00:00+00:00"
            ),
            "2026-10-30T20:05:00+00:00",
        )

    def test_repeat_uses_latest_actual_observation(self):
        first = [
            dict(completed_utc="2026-10-30T20:00:00+00:00"),
            dict(completed_utc="2026-10-30T20:01:00+00:00"),
        ]
        with self.assertRaises(ValueError):
            repeat_due(first, datetime(2026, 10, 30, 20, 20, 59, tzinfo=timezone.utc))
        self.assertEqual(
            repeat_due(
                first, datetime(2026, 10, 30, 20, 21, tzinfo=timezone.utc)
            ).isoformat(),
            "2026-10-30T20:21:00+00:00",
        )

    def test_repeat_requires_first_evidence(self):
        with self.assertRaises(ValueError):
            repeat_due([], datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
