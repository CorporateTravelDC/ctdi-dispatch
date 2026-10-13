"""2026-10-13 ntfy audit: an intermittent 401/403 on a valid token is a dropped message (3 of 3
were never stored by ntfy), so it gets one delayed retry; a body over the server's 65536-byte limit
is truncated instead of rejected as an attachment (400 40014)."""
from unittest.mock import MagicMock, patch

import requests

from common import ntfy_push
from tests.common.test_ntfy_push_auth_failure import _IsolatedStateDir


def _resp(status):
    r = MagicMock(status_code=status, text="forbidden" if status >= 400 else "")
    r.raise_for_status.side_effect = requests.exceptions.HTTPError(response=r) if status >= 400 else None
    return r


def test_one_delayed_retry_recovers_a_transient_403(monkeypatch):
    monkeypatch.setattr(ntfy_push.time, "sleep", lambda s: None)
    with _IsolatedStateDir(), patch("common.ntfy_push.requests.post", side_effect=[_resp(403), _resp(200)]) as post:
        assert ntfy_push.send("ops-health", "m", title="t") is True
    assert post.call_count == 2


def test_a_persistent_403_is_retried_once_then_reported_failed(monkeypatch):
    monkeypatch.setattr(ntfy_push.time, "sleep", lambda s: None)
    with _IsolatedStateDir(), patch("common.ntfy_push.requests.post", return_value=_resp(403)) as post:
        assert ntfy_push.send("ops-health", "m", title="t") is False
    assert post.call_count == 2


def test_an_oversize_body_is_truncated_below_the_server_limit():
    big = "é" * 40000                                                  # 80,000 bytes of 2-byte chars
    with _IsolatedStateDir(), patch("common.ntfy_push.requests.post", return_value=_resp(200)) as post:
        assert ntfy_push.send("dispatch-debriefs", big, title="t") is True
    sent = post.call_args.kwargs["data"]
    assert len(sent) <= ntfy_push._MAX_BODY_BYTES and sent.decode("utf-8").endswith("ntfy limit 65536]")
