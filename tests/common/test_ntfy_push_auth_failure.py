"""
Regression test for the 2026-08-25 C-6 fix (Opus blind review):
common.ntfy_push.send() used to return True on an ntfy 401/403 response,
on the theory that ntfy's own messages_published counter climbing through
the same window meant the send probably went out anyway. That was never
confirmed per-message, and a permanently wrong/revoked NTFY_TOKEN produces
the identical status code forever -- the live dedup file had accumulated
1,274 real entries under the old code, none of which were ever reported
as a failure. This locks in the corrected contract: a 401/403 must never
be reported as a successful send, even though the resend is still
suppressed to avoid duplicate-alerting a message that might genuinely
have landed.
"""
import tempfile
from unittest.mock import patch, MagicMock

import pytest
import requests

from common import config, ntfy_push


class _IsolatedStateDir:
    """Isolates common.config.state_dir() so this test's dedup writes never
    touch the real /var/lib/corporatetraveldc/pusher-ntfy-ambiguous-status-dedup.json
    -- same isolation pattern as tests/shared/test_watchlist.py's _IsolatedDB."""

    def __enter__(self):
        self._orig_state_dir = config.state_dir
        self._tmp_state_dir = tempfile.mkdtemp(prefix="ctdi-test-ntfy-state-")
        config.state_dir = lambda: self._tmp_state_dir
        # ntfy_push's module-level dedup instance was constructed against
        # the real state_dir() at import time -- rebuild it against the
        # isolated dir so this test can't read/write real production state.
        self._orig_dedup = ntfy_push._ambiguous_dedup
        ntfy_push._ambiguous_dedup = ntfy_push.PushDedup(
            "ntfy-ambiguous-status", dedup_secs=ntfy_push._AMBIGUOUS_STATUS_TTL_SECS
        )
        return self

    def __exit__(self, *_):
        import shutil
        config.state_dir = self._orig_state_dir
        ntfy_push._ambiguous_dedup = self._orig_dedup
        shutil.rmtree(self._tmp_state_dir, ignore_errors=True)


def _fake_403_response():
    resp = MagicMock()
    resp.status_code = 403
    resp.text = "forbidden"
    err = requests.exceptions.HTTPError(response=resp)
    resp.raise_for_status.side_effect = err
    return resp


@pytest.mark.parametrize("status", [401, 403])
def test_send_returns_false_on_auth_failure(status):
    with _IsolatedStateDir():
        resp = MagicMock()
        resp.status_code = status
        resp.text = "auth failed"
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        with patch("common.ntfy_push.requests.post", return_value=resp):
            ok = ntfy_push.send("ops-health", "test message", title="test")
    assert ok is False, (
        "a 401/403 from ntfy must never be reported as a successful send -- "
        "this is exactly the C-6 regression (revoked token indistinguishable "
        "from healthy delivery)"
    )


def test_send_suppresses_resend_but_still_reports_failure_on_repeat():
    """The dedup window still exists to avoid duplicate-alerting a message
    that might have genuinely landed despite the auth error -- but every
    call within that window must still report failure, not success."""
    with _IsolatedStateDir():
        resp = MagicMock()
        resp.status_code = 403
        resp.text = "auth failed"
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        with patch("common.ntfy_push.requests.post", return_value=resp):
            first = ntfy_push.send("ops-health", "same message", title="same title")
            second = ntfy_push.send("ops-health", "same message", title="same title")
    assert first is False
    assert second is False
