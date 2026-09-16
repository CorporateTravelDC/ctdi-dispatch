"""
tests/poller/test_ground_news_fetcher.py

Coverage for poller/fetchers/ground_news.py: credential-gated skip,
successful fetch + storage, auth-pending (GroundNewsAuthError) handling,
and generic transport-failure handling -- same shape as
tests/poller/test_eurocontrol_jasdat_fetchers.py.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from common.ground_news_client import GroundNewsAuthError
from poller.fetchers import ground_news


class TestGroundNewsFetcher(unittest.TestCase):

    @patch("poller.fetchers.ground_news.db.upsert_feed_skip")
    @patch("poller.fetchers.ground_news.credentials_configured", return_value=False)
    def test_skips_when_no_credentials(self, mock_configured, mock_skip):
        result = ground_news.run()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "awaiting_credentials")
        mock_skip.assert_called_once()
        self.assertEqual(mock_skip.call_args.args[2], "awaiting_credentials")

    @patch("poller.fetchers.ground_news.db.upsert_feed_skip")
    @patch("poller.fetchers.ground_news.fetch_my_feed",
           side_effect=GroundNewsAuthError("not yet available"))
    @patch("poller.fetchers.ground_news.credentials_configured", return_value=True)
    def test_auth_pending_is_a_skip_not_an_error(self, mock_configured, mock_fetch, mock_skip):
        """Credentials configured but Ground News hasn't confirmed a real
        endpoint yet (see common/ground_news_client.py's _login()
        placeholder) -- this must read as a graceful skip, not a feed
        error, so it doesn't page anyone before sign-off exists."""
        result = ground_news.run()
        self.assertTrue(result["skipped"])
        mock_skip.assert_called_once()

    @patch("poller.fetchers.ground_news.db.upsert_feed")
    @patch("poller.fetchers.ground_news.db.upsert_ground_news_items", return_value=3)
    @patch("poller.fetchers.ground_news.fetch_my_feed")
    @patch("poller.fetchers.ground_news.credentials_configured", return_value=True)
    def test_success_with_credentials(self, mock_configured, mock_fetch, mock_store, mock_upsert_feed):
        mock_fetch.return_value = [
            {"title": "A", "link": "https://ground.news/a", "summary": "", "published": "",
             "source": "Ground News", "bias_distribution": {"left": 1, "center": 2, "right": 0},
             "factuality": "high", "blindspot": False},
            {"title": "B", "link": "https://ground.news/b", "summary": "", "published": "",
             "source": "Ground News", "bias_distribution": {"left": 0, "center": 1, "right": 3},
             "factuality": None, "blindspot": True},
            {"title": "C", "link": "https://ground.news/c", "summary": "", "published": "",
             "source": "Ground News", "bias_distribution": {"left": 2, "center": 0, "right": 0},
             "factuality": "mixed", "blindspot": False},
        ]
        result = ground_news.run()
        self.assertEqual(result["count"], 3)
        mock_store.assert_called_once()
        mock_upsert_feed.assert_called_once()
        self.assertIsNone(mock_upsert_feed.call_args.kwargs.get("error"))

    @patch("poller.fetchers.ground_news.db.upsert_feed")
    @patch("poller.fetchers.ground_news.fetch_my_feed",
           side_effect=requests.RequestException("connection reset"))
    @patch("poller.fetchers.ground_news.credentials_configured", return_value=True)
    def test_transport_failure_records_error(self, mock_configured, mock_fetch, mock_upsert_feed):
        result = ground_news.run()
        self.assertIn("error", result)
        mock_upsert_feed.assert_called_once()
        self.assertIsNotNone(mock_upsert_feed.call_args.kwargs.get("error"))


if __name__ == "__main__":
    unittest.main()
