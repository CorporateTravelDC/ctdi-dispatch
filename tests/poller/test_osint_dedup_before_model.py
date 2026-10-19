"""2026-10-19 (lean-out L2, F7): osint_monitor checks for an already-stored item
BEFORE it calls the model. An item already in osint_items used to be narrated
by the model every 15 min and the narrative thrown away by the INSERT OR
IGNORE in osint_save_item."""
from __future__ import annotations

import os

os.environ.setdefault("DISPATCH_DB_BACKEND", "sqlite")

from poller.skills import osint_monitor as om  # noqa: E402


class FakeDB:
    def __init__(self, stored: set[str]):
        self.stored = set(stored)
        self.saved: list[str] = []
        self.exists_calls = 0

    def osint_get_scopes(self, enabled_only=True):
        return [{"id": 1, "label": "scope", "scope_type": "keyword",
                 "query_terms": "alpha", "feed_urls": "https://example.invalid/feed",
                 "push_threshold": "CRITICAL"}]

    def osint_prune_items(self, days):
        return 0

    def osint_item_exists(self, h):
        self.exists_calls += 1
        return h in self.stored

    def osint_save_item(self, **kw):
        self.saved.append(kw["content_hash"])
        new = kw["content_hash"] not in self.stored
        self.stored.add(kw["content_hash"])
        return new


def _run(monkeypatch, stored_titles):
    items = [{"title": f"alpha story {i}", "url": f"https://example.invalid/{i}", "summary": "alpha"}
             for i in range(3)]
    stored = {om._content_hash(it["url"], it["title"]) for it in items if it["title"] in stored_titles}
    fake = FakeDB(stored)
    calls = []
    monkeypatch.setattr(om, "db", fake)
    monkeypatch.setattr(om, "LLAMA_BASE_URL", "http://llama.invalid")
    monkeypatch.setattr(om, "_fetch_feed", lambda url: items)
    monkeypatch.setattr(om, "_score_item", lambda item, terms, st="keyword": (8, "HIGH"))
    monkeypatch.setattr(om, "_generate_narrative", lambda item, *a, **k: calls.append(item["title"]) or "n")
    monkeypatch.setattr(om, "log_usage", lambda *a, **k: None)
    om.main()
    return fake, calls


def test_stored_items_never_reach_the_model(monkeypatch):
    fake, calls = _run(monkeypatch, {"alpha story 0", "alpha story 2"})
    assert calls == ["alpha story 1"]
    assert len(fake.saved) == 1          # stored items are not re-saved either
    assert fake.exists_calls == 3


def test_all_new_items_still_narrated(monkeypatch):
    fake, calls = _run(monkeypatch, set())
    assert calls == ["alpha story 0", "alpha story 1", "alpha story 2"]
    assert len(fake.saved) == 3


def test_existence_check_failure_fails_open(monkeypatch):
    def boom(h):
        raise RuntimeError("db down")
    items = [{"title": "alpha x", "url": "https://example.invalid/x", "summary": ""}]
    fake = FakeDB(set())
    fake.osint_item_exists = boom
    calls = []
    monkeypatch.setattr(om, "db", fake)
    monkeypatch.setattr(om, "LLAMA_BASE_URL", "http://llama.invalid")
    monkeypatch.setattr(om, "_fetch_feed", lambda url: items)
    monkeypatch.setattr(om, "_score_item", lambda item, terms, st="keyword": (8, "HIGH"))
    monkeypatch.setattr(om, "_generate_narrative", lambda item, *a, **k: calls.append(1) or "n")
    monkeypatch.setattr(om, "log_usage", lambda *a, **k: None)
    om.main()
    assert calls == [1] and len(fake.saved) == 1
