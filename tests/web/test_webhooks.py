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
