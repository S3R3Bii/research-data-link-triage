"""Exercise bounded transport with authored responses and no network access."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observe_links import observe, BODY_CAP


class CaptureBounds(unittest.TestCase):
    def capture(self, responses):
        session = Mock()
        session.get.side_effect = responses
        for response in responses:
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as folder:
            with patch(
                "observe_links.socket.getaddrinfo",
                return_value=[(None, None, None, None, ("8.8.8.8", 443))],
            ):
                row = observe(
                    dict(
                        item_id="AUTHORED",
                        dataset_doi="10.0000/authored",
                        role="authored_example",
                    ),
                    Path(folder),
                    session,
                    {},
                    {},
                )
        return row, session

    def response(self, status, headers, chunks):
        response = Mock(status_code=status, headers=headers)
        response.iter_content.return_value = iter(chunks)
        return response

    def test_truncated_html_keeps_prefix_and_bound(self):
        response = self.response(
            200, {"Content-Type": "text/html"}, [b"a" * BODY_CAP, b"b"]
        )
        row, _ = self.capture([response])
        self.assertEqual(row["chain"][0]["stored_bytes"], BODY_CAP)
        self.assertTrue(row["chain"][0]["body_truncated"])
        self.assertEqual(row["data_file_availability"], "not_tested")

    def test_non_html_body_is_not_consumed(self):
        response = self.response(
            200, {"Content-Type": "application/octet-stream"}, [b"not read"]
        )
        row, _ = self.capture([response])
        response.iter_content.assert_not_called()
        self.assertEqual(row["censor_reason"], "non_html_response_not_consumed")
        self.assertFalse(row["chain"][0]["body_consumed"])

    def test_unsupported_redirect_is_not_requested(self):
        response = self.response(
            302,
            {"Location": "https://example.com/private", "Content-Type": "text/html"},
            [],
        )
        row, session = self.capture([response])
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(row["terminal_http_status"], 302)
        self.assertIsNotNone(row["censor_reason"])

    def test_rate_limit_keeps_retry_deadline(self):
        response = self.response(
            429, {"Content-Type": "text/html", "Retry-After": "90"}, [b"wait"]
        )
        row, session = self.capture([response])
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(row["censor_reason"], "rate_limit_observed")
        self.assertIsNotNone(row["chain"][0]["retry_not_before_utc"])


if __name__ == "__main__":
    unittest.main()
