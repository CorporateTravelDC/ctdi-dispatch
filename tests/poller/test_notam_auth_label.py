"""tests/poller/test_notam_auth_label.py -- 2026-10-12: a NOTAM REST auth failure
with credentials configured is 'auth_failed' (a counted failure), not
'awaiting_credentials' (a deliberate skip). The old shared label left one
transient failure looking like missing keys for 44 h."""
from __future__ import annotations

from poller.fetchers import notam


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(notam.db, "upsert_feed_skip", lambda f, t, r: calls.append(("skip", r)))
    monkeypatch.setattr(notam.db, "upsert_feed", lambda f, t, **kw: calls.append(("feed", kw.get("error"))))
    notam._token_cache["access_token"] = None
    notam._token_cache["expires_at"] = 0
    return calls


def test_missing_credentials_is_a_skip(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(notam, "_client_id", lambda: "")
    monkeypatch.setattr(notam, "_client_secret", lambda: "")
    assert notam.run() == {"skipped": True, "reason": "awaiting_credentials"}
    assert calls == [("skip", "awaiting_credentials")]


def test_auth_failure_with_credentials_is_a_counted_error(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(notam, "_client_id", lambda: "id")
    monkeypatch.setattr(notam, "_client_secret", lambda: "secret")

    def boom(*a, **k):
        raise RuntimeError("401 Unauthorized")
    monkeypatch.setattr(notam.requests, "post", boom)
    assert notam.run() == {"skipped": True, "reason": "auth_failed"}
    assert calls == [("feed", "auth_failed")]
