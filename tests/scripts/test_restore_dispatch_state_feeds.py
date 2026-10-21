"""2026-10-20: the dispatch-context-guardian restore snapshot reads /api/v1/feeds' own per-feed
fields (push_covered, optional, error 'awaiting_credentials') instead of pairing a REST feed
with a push feed of the identical name -- NOTAM's push twin is push:fns, so the notam REST
fallback showed as failing. Fixture rows only."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2]
          / ".claude/skills/dispatch-context-guardian/scripts/restore_dispatch_state.py")


def _fmt():
    spec = importlib.util.spec_from_file_location("restore_dispatch_state", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.fmt_feeds


def _line(out: str, name: str) -> str:
    return next(l.strip() for l in out.splitlines() if l.split()[1] == name)


def test_api_fields_mark_idle_by_design_feeds():
    rows = [
        # notam REST fallback while push:fns covers it (the API nulls `error`, keeps pull_error)
        {"feed_name": "notam", "age_seconds": 90000, "stale_threshold_seconds": 3600,
         "push_covered": True, "error": None, "pull_error": "awaiting_credentials"},
        {"feed_name": "push:fns", "age_seconds": 30, "stale_threshold_seconds": 600,
         "push_covered": False, "error": None},
        {"feed_name": "dca_fids", "age_seconds": 5000, "stale_threshold_seconds": 900,
         "push_covered": False, "optional": True, "error": "HTTP 503"},
        {"feed_name": "kpler_maritime", "age_seconds": None, "stale_threshold_seconds": 3600,
         "push_covered": False, "error": "awaiting_credentials"},
        {"feed_name": "metar", "age_seconds": 9000, "stale_threshold_seconds": 3600,
         "push_covered": False, "error": None},
        {"feed_name": "tfr", "age_seconds": 100, "stale_threshold_seconds": 3600,
         "push_covered": False, "error": "HTTP 500"},
    ]
    out = _fmt()({"feeds": rows})
    assert _line(out, "notam").startswith("○") and "(REST fallback idle; push covers)" in _line(out, "notam")
    assert _line(out, "push:fns").startswith("✓")
    assert _line(out, "dca_fids").startswith("○") and _line(out, "dca_fids").endswith("(optional)")
    assert _line(out, "kpler_maritime").startswith("○") and "(not configured)" in _line(out, "kpler_maritime")
    assert _line(out, "metar").startswith("✗")                      # stale, nothing covers it
    assert _line(out, "tfr").startswith("✗") and "err=HTTP 500" in _line(out, "tfr")


def test_push_covered_false_is_not_overridden_by_a_same_named_push_feed():
    rows = [{"feed_name": "nws", "age_seconds": 9000, "stale_threshold_seconds": 3600,
             "push_covered": False, "error": None},
            {"feed_name": "push:nws", "age_seconds": 10, "stale_threshold_seconds": 600, "error": None}]
    assert _line(_fmt()({"feeds": rows}), "nws").startswith("✗")


def test_old_snapshot_without_push_covered_keeps_the_name_pairing():
    rows = [{"feed_name": "nws", "age_seconds": 9000, "stale_threshold_seconds": 3600, "error": None},
            {"feed_name": "push:nws", "age_seconds": 10, "stale_threshold_seconds": 600, "error": None}]
    assert _line(_fmt()({"feeds": rows}), "nws").startswith("○")
