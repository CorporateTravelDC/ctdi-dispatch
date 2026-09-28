"""
tests/poller/test_eurocontrol_jasdat_fetchers.py

Coverage for poller/fetchers/eurocontrol.py and poller/fetchers/jasdat.py:
credential-gated skip, successful fetch + storage, and failure handling --
same shape as the existing NOTAM fetcher's own behavior.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from poller.fetchers import eurocontrol, jasdat


class TestEurocontrolFetcher(unittest.TestCase):

    @patch("poller.fetchers.eurocontrol.db.upsert_feed_skip")
    @patch("poller.fetchers.eurocontrol.config.get", return_value="")
    def test_skips_when_no_credentials(self, mock_get, mock_skip):
        result = eurocontrol.run()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "awaiting_credentials")
        mock_skip.assert_called_once()
        self.assertEqual(mock_skip.call_args.args[2], "awaiting_credentials")

    @patch("poller.fetchers.eurocontrol.db.upsert_feed")
    @patch("poller.fetchers.eurocontrol.db.upsert_international_aviation_records", return_value=0)
    @patch("poller.fetchers.eurocontrol.requests.get")
    @patch("poller.fetchers.eurocontrol.config.get", return_value="configured-value")
    def test_success_with_credentials(self, mock_get, mock_requests_get, mock_store, mock_upsert_feed):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_requests_get.return_value = mock_resp

        result = eurocontrol.run()
        self.assertEqual(result["count"], 0)
        mock_requests_get.assert_called_once()
        mock_upsert_feed.assert_called_once()
        self.assertIsNone(mock_upsert_feed.call_args.kwargs.get("error"))

    @patch("poller.fetchers.eurocontrol.db.upsert_feed")
    @patch("poller.fetchers.eurocontrol.requests.get", side_effect=ConnectionError("no route"))
    @patch("poller.fetchers.eurocontrol.config.get", return_value="configured-value")
    def test_failure_records_error(self, mock_get, mock_requests_get, mock_upsert_feed):
        result = eurocontrol.run()
        self.assertIn("error", result)
        mock_upsert_feed.assert_called_once()
        self.assertIsNotNone(mock_upsert_feed.call_args.kwargs.get("error"))


class TestJasdatFetcher(unittest.TestCase):

    @patch("poller.fetchers.jasdat.db.upsert_feed_skip")
    @patch("poller.fetchers.jasdat.config.get", return_value="")
    def test_skips_when_no_credentials(self, mock_get, mock_skip):
        result = jasdat.run()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "awaiting_credentials")

    @patch("poller.fetchers.jasdat.db.upsert_feed")
    @patch("poller.fetchers.jasdat.db.upsert_international_aviation_records", return_value=2)
    @patch("poller.fetchers.jasdat.requests.get")
    @patch("poller.fetchers.jasdat.config.get", return_value="configured-value")
    def test_success_with_credentials(self, mock_get, mock_requests_get, mock_store, mock_upsert_feed):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"items": [{"id": "J1"}, {"id": "J2"}]}
        mock_requests_get.return_value = mock_resp

        result = jasdat.run()
        self.assertEqual(result["count"], 2)
        mock_store.assert_called_once()
        stored_records = mock_store.call_args.args[1]
        self.assertEqual(len(stored_records), 2)
        self.assertEqual(stored_records[0]["record_type"], "notam")


if __name__ == "__main__":
    unittest.main()
