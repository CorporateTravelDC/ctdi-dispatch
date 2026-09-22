"""
tests/web/test_webhooks.py

Coverage for the inbound webhook receivers:
  - POST /webhooks/limoanywhere/reservations
  - POST /webhooks/ringcentral/events
  - POST /webhooks/3cx/events

Verifies: credential-gating (503 when unset), auth rejection (401 on bad
secret), successful accept + storage + ntfy fire on valid secret, and the
RingCentral Validation-Token handshake.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes.webhooks import router


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestWebhookGating(unittest.TestCase):
    """No secret configured -> 503, not silent 200 or a stack trace."""

    @patch("web.routes.webhooks.config.get", return_value="")
    def test_limoanywhere_503_when_unconfigured(self, mock_get):
        client = _make_client()
        resp = client.post("/webhooks/limoanywhere/reservations", json={"reservation_event": "reservation.created"})
        self.assertEqual(resp.status_code, 503)

    @patch("web.routes.webhooks.config.get", return_value="")
    def test_ringcentral_503_when_unconfigured(self, mock_get):
        client = _make_client()
        resp = client.post("/webhooks/ringcentral/events", json={"event": "call.ended"})
        self.assertEqual(resp.status_code, 503)

    @patch("web.routes.webhooks.config.get", return_value="")
    def test_3cx_503_when_unconfigured(self, mock_get):
        client = _make_client()
        resp = client.post("/webhooks/3cx/events", json={"event_type": "call.answered"})
        self.assertEqual(resp.status_code, 503)


class TestWebhookAuth(unittest.TestCase):
    """Configured but wrong/missing secret -> 401."""

    @patch("web.routes.webhooks.config.get", return_value="real-secret")
    def test_limoanywhere_401_on_bad_secret(self, mock_get):
        client = _make_client()
        resp = client.post(
            "/webhooks/limoanywhere/reservations",
            json={"reservation_event": "reservation.created"},
            headers={"x-webhook-secret": "wrong"},
        )
        self.assertEqual(resp.status_code, 401)

    @patch("web.routes.webhooks.config.get", return_value="real-secret")
    def test_limoanywhere_401_on_missing_secret_header(self, mock_get):
        client = _make_client()
        resp = client.post("/webhooks/limoanywhere/reservations", json={"reservation_event": "reservation.created"})
        self.assertEqual(resp.status_code, 401)


class TestWebhookAccept(unittest.TestCase):
    """Correct secret -> 200, event stored, ntfy fired."""

    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    @patch("web.routes.webhooks.config.get", return_value="real-secret")
    def test_limoanywhere_accepted_and_stored(self, mock_get, mock_insert, mock_ntfy):
        client = _make_client()
        resp = client.post(
            "/webhooks/limoanywhere/reservations",
            json={
                "reservation_event": "reservation.created",
                "id": "RES-123",
                "passenger": {"name": "J. Smith"},
            },
            headers={"x-webhook-secret": "real-secret"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["event"], "reservation.created")
        mock_insert.assert_called_once()
        self.assertEqual(mock_insert.call_args.kwargs["source"], "limoanywhere")
        self.assertEqual(mock_insert.call_args.kwargs["external_ref"], "RES-123")
        mock_ntfy.assert_called_once()

    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    @patch("web.routes.webhooks.config.get", return_value="real-secret")
    def test_3cx_accepted_and_stored(self, mock_get, mock_insert, mock_ntfy):
        client = _make_client()
        resp = client.post(
            "/webhooks/3cx/events",
            json={"event_type": "call.answered", "call_id": "CALL-1"},
            headers={"x-webhook-secret": "real-secret"},
        )
        self.assertEqual(resp.status_code, 200)
        mock_insert.assert_called_once()
        self.assertEqual(mock_insert.call_args.kwargs["source"], "3cx")
        mock_ntfy.assert_called_once()


class TestLimoAnywhereAutoWatchlist(unittest.TestCase):
    """2026-09-21: flight/train extraction -> auto-watchlist call."""

    @patch("web.routes.webhooks.requests.post")
    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    def test_flight_identifier_extracted_and_tracked(self, mock_insert, mock_ntfy, mock_post):
        with patch("web.routes.webhooks.config.get") as mock_get:
            mock_get.side_effect = lambda key, default="": (
                "real-secret" if key == "LIMOANYWHERE_WEBHOOK_SECRET"
                else "fake-admin-token" if key == "DISPATCH_ADMIN_TOKEN"
                else default
            )
            client = _make_client()
            resp = client.post(
                "/webhooks/limoanywhere/reservations",
                json={
                    "reservation_event": "reservation.created",
                    "id": "RES-1",
                    "passenger": {"name": "J. Smith"},
                    "flight_number": "dl123",
                },
                headers={"x-webhook-secret": "real-secret"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["auto_tracked"], "flight DAL123")
        mock_post.assert_called_once()
        call = mock_post.call_args
        self.assertEqual(call.args[0], "http://127.0.0.1:8000/api/v1/watchlist/flights")
        self.assertEqual(call.kwargs["json"]["identifier"], "DAL123")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer fake-admin-token")

    @patch("web.routes.webhooks.requests.post")
    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    def test_train_identifier_extracted_and_tracked(self, mock_insert, mock_ntfy, mock_post):
        with patch("web.routes.webhooks.config.get") as mock_get:
            mock_get.side_effect = lambda key, default="": (
                "real-secret" if key == "LIMOANYWHERE_WEBHOOK_SECRET"
                else "fake-admin-token" if key == "DISPATCH_ADMIN_TOKEN"
                else default
            )
            client = _make_client()
            resp = client.post(
                "/webhooks/limoanywhere/reservations",
                json={
                    "reservation_event": "reservation.created",
                    "id": "RES-2",
                    "trip": {"train_number": "2171"},
                },
                headers={"x-webhook-secret": "real-secret"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["auto_tracked"], "train 2171")
        mock_post.assert_called_once()
        call = mock_post.call_args
        self.assertEqual(call.args[0], "http://127.0.0.1:8000/api/v1/watchlist/trains")
        self.assertEqual(call.kwargs["json"]["identifier"], "2171")

    @patch("web.routes.webhooks.requests.post")
    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    @patch("web.routes.webhooks.config.get", return_value="real-secret")
    def test_no_identifier_no_watchlist_call(self, mock_get, mock_insert, mock_ntfy, mock_post):
        client = _make_client()
        resp = client.post(
            "/webhooks/limoanywhere/reservations",
            json={"reservation_event": "reservation.created", "id": "RES-3",
                 "passenger": {"name": "J. Smith"}},
            headers={"x-webhook-secret": "real-secret"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("auto_tracked", resp.json())
        mock_post.assert_not_called()

    @patch("web.routes.webhooks.requests.post", side_effect=Exception("connection refused"))
    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    def test_watchlist_call_failure_does_not_break_webhook(self, mock_insert, mock_ntfy, mock_post):
        with patch("web.routes.webhooks.config.get") as mock_get:
            mock_get.side_effect = lambda key, default="": (
                "real-secret" if key == "LIMOANYWHERE_WEBHOOK_SECRET"
                else "fake-admin-token" if key == "DISPATCH_ADMIN_TOKEN"
                else default
            )
            client = _make_client()
            resp = client.post(
                "/webhooks/limoanywhere/reservations",
                json={"reservation_event": "reservation.created", "id": "RES-4",
                     "flight_number": "UAL456"},
                headers={"x-webhook-secret": "real-secret"},
            )
        # Watchlist call raised -- webhook itself must still succeed, event
        # still persisted (mock_insert already called before this point).
        self.assertEqual(resp.status_code, 200)
        mock_insert.assert_called_once()

    @patch("web.routes.webhooks.requests.post")
    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    def test_no_admin_token_skips_watchlist_call(self, mock_insert, mock_ntfy, mock_post):
        with patch("web.routes.webhooks.config.get") as mock_get:
            # LIMOANYWHERE_WEBHOOK_SECRET configured, DISPATCH_ADMIN_TOKEN not.
            mock_get.side_effect = lambda key, default="": (
                "real-secret" if key == "LIMOANYWHERE_WEBHOOK_SECRET" else default
            )
            client = _make_client()
            resp = client.post(
                "/webhooks/limoanywhere/reservations",
                json={"reservation_event": "reservation.created", "id": "RES-5",
                     "flight_number": "AA789"},
                headers={"x-webhook-secret": "real-secret"},
            )
        self.assertEqual(resp.status_code, 200)
        mock_post.assert_not_called()


class TestFlightIdentifierNormalization(unittest.TestCase):
    """Unit coverage for the IATA->ICAO normalization helper directly."""

    def test_iata_two_letter_mapped_to_icao(self):
        from web.routes.webhooks import _normalize_flight_identifier
        self.assertEqual(_normalize_flight_identifier("DL123"), "DAL123")
        self.assertEqual(_normalize_flight_identifier("ua456"), "UAL456")

    def test_already_icao_passthrough(self):
        from web.routes.webhooks import _normalize_flight_identifier
        self.assertEqual(_normalize_flight_identifier("AAL789"), "AAL789")

    def test_unmapped_two_letter_prefix_passed_through(self):
        from web.routes.webhooks import _normalize_flight_identifier
        self.assertEqual(_normalize_flight_identifier("XY100"), "XY100")

    def test_garbage_returns_none(self):
        from web.routes.webhooks import _normalize_flight_identifier
        self.assertIsNone(_normalize_flight_identifier("not a flight"))
        self.assertIsNone(_normalize_flight_identifier(""))


class TestRingCentralHandshake(unittest.TestCase):
    """Validation-Token must be echoed back verbatim, no secret required."""

    def test_validation_token_echoed(self):
        client = _make_client()
        resp = client.post(
            "/webhooks/ringcentral/events",
            headers={"Validation-Token": "abc123"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("validation-token"), "abc123")

    @patch("web.routes.webhooks._fire_ntfy_dual")
    @patch("web.routes.webhooks.db.insert_webhook_event")
    @patch("web.routes.webhooks.config.get", return_value="real-secret")
    def test_real_event_after_handshake(self, mock_get, mock_insert, mock_ntfy):
        client = _make_client()
        resp = client.post(
            "/webhooks/ringcentral/events",
            json={"event": "call.ended", "uuid": "EVT-1"},
            headers={"x-webhook-secret": "real-secret"},
        )
        self.assertEqual(resp.status_code, 200)
        mock_insert.assert_called_once()
        self.assertEqual(mock_insert.call_args.kwargs["source"], "ringcentral")


if __name__ == "__main__":
    unittest.main()
