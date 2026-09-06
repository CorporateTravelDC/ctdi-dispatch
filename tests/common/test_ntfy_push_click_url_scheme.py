"""
Regression test for the 2026-08-26 C-9 fix (Opus blind review):
common.ntfy_push.send() used to pass a caller-supplied click_url straight
through to the ntfy "Click:" header with no scheme validation. The
exploited path was poller.skills.osint_monitor._push_item(), which sets
click_url to an RSS/Atom item's own <link> value -- attacker-controlled
for any feed an attacker can influence (a malicious feed, or any
Google-News-indexed page). A javascript:/data: URI passed through
unchanged would be delivered as the tap-through action on a priority-4/5
push. This locks in the corrected contract: only http/https survive;
anything else falls back to the safe per-topic default.
"""
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from common import config, ntfy_push


class _IsolatedStateDir:
    def __enter__(self):
        self._orig_state_dir = config.state_dir
        self._tmp_state_dir = tempfile.mkdtemp(prefix="ctdi-test-ntfy-state-")
        config.state_dir = lambda: self._tmp_state_dir
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


def _ok_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.side_effect = None
    return resp


@pytest.mark.parametrize("malicious_url", [
    "javascript:alert(document.cookie)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "not-a-url-at-all",
])
def test_unsafe_click_url_scheme_falls_back_to_topic_default(malicious_url):
    with _IsolatedStateDir():
        resp = _ok_response()
        captured = {}

        def _capture_post(url, data, headers, timeout):
            captured["headers"] = headers
            return resp

        with patch("common.ntfy_push.requests.post", side_effect=_capture_post):
            ok = ntfy_push.send(
                "osint-alerts", "attacker-controlled body", title="t",
                click_url=malicious_url,
            )
        assert ok is True
        assert captured["headers"]["Click"] == ntfy_push.TOPIC_CLICK["osint-alerts"], (
            f"click_url={malicious_url!r} must never reach the wire -- "
            "this is exactly the C-9 prompt-injection exposure"
        )


def test_safe_click_url_scheme_passes_through_unchanged():
    with _IsolatedStateDir():
        resp = _ok_response()
        captured = {}

        def _capture_post(url, data, headers, timeout):
            captured["headers"] = headers
            return resp

        real_url = "https://example.com/some/real/article"
        with patch("common.ntfy_push.requests.post", side_effect=_capture_post):
            ok = ntfy_push.send(
                "osint-alerts", "body", title="t", click_url=real_url,
            )
        assert ok is True
        assert captured["headers"]["Click"] == real_url


def test_no_click_url_uses_topic_default():
    with _IsolatedStateDir():
        resp = _ok_response()
        captured = {}

        def _capture_post(url, data, headers, timeout):
            captured["headers"] = headers
            return resp

        with patch("common.ntfy_push.requests.post", side_effect=_capture_post):
            ok = ntfy_push.send("cps", "body", title="t")
        assert ok is True
        assert captured["headers"]["Click"] == ntfy_push.TOPIC_CLICK["cps"]
