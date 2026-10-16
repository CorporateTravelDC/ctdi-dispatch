"""Parity audit 2026-10-15: the Jumpseat phase lookup sent a registration to ACARS Drama with no
LADD check. A LADD-listed (or unverifiable) registration must never leave the box."""
from __future__ import annotations

import types

from common import acars


def _setup(monkeypatch, ladd):
    calls = []
    monkeypatch.setattr(acars, "_JUMPSEAT_TOKEN", "placeholder-not-a-token")
    monkeypatch.setattr(acars.requests, "get", lambda *a, **k: calls.append(k) or types.SimpleNamespace(status_code=404))
    import common.db as db
    if isinstance(ladd, Exception):
        def boom(_r):
            raise ladd
        monkeypatch.setattr(db, "faa_is_ladd", boom)
    else:
        monkeypatch.setattr(db, "faa_is_ladd", lambda _r: ladd)
    return calls


def test_ladd_listed_registration_is_never_sent(monkeypatch):
    calls = _setup(monkeypatch, True)
    assert acars._query_jumpseat_phase("N00000", 0) is None and calls == []


def test_lookup_failure_fails_closed(monkeypatch):
    calls = _setup(monkeypatch, RuntimeError("db down"))
    assert acars._query_jumpseat_phase("N00000", 0) is None and calls == []


def test_unlisted_registration_is_queried(monkeypatch):
    calls = _setup(monkeypatch, False)
    acars._query_jumpseat_phase("N00000", 0)
    assert len(calls) == 1
