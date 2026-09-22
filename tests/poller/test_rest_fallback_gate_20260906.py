"""
tests/poller/test_rest_fallback_gate_20260906.py

Work-order item 1 (2026-09-06, "solidify the REST fallbacks"):

  * FetchLoop's push gate under the startup hold-off: a push heartbeat that
    went stale BEFORE this poller started (LOCKDOWN shed / restart) must not
    fire a REST pull while the startup push grace is open; one that went
    stale AFTER boot (the feed came up and died) falls back at once; once
    the grace closes a still-stale pre-boot heartbeat falls back.
  * Per-feed push_max_age (amtrak stamps per 300 s poll, not per 30 s).
  * amtrak is a scheduled fetcher and a valid refresh_feed target.
  * failover-kickover-guardrail: no forced kickover while the thermal guard
    has the stack shed (tier 2) or inside the post-restore grace, and the
    REST-stale clock is measured from the restore, never across a shed.

The global conftest isolates db._db_path to a tmp file; these tests never
touch the trigger dir or the network (mod.run is patched out).
"""
from __future__ import annotations

import asyncio
import importlib.util
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import common.db as db
from ingest import failover
from poller import main as poller_main


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_guardrail():
    """scripts/ is not a package; load the guardrail script as a module."""
    path = REPO_ROOT / "scripts" / "failover-kickover-guardrail.py"
    spec = importlib.util.spec_from_file_location("failover_kickover_guardrail", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fresh_db():
    db.close_thread_connection()
    db.init_db()
    yield
    db.close_thread_connection()


def _run(coro):
    return asyncio.run(coro)


def _make_loop(name="nws", push_feed="nws", **kw) -> poller_main.FetchLoop:
    loop = poller_main.FetchLoop(name=name, module=f"poller.fetchers.{name}",
                                 interval=300, push_feed=push_feed, **kw)
    loop.not_before = 0.0
    return loop


# -- FetchLoop push gate + startup grace ---------------------------------------

class TestStartupPushGrace:

    def test_pre_boot_stale_heartbeat_does_not_fire_rest_during_grace(self, fresh_db, monkeypatch):
        boot = time.time()
        # Heartbeat last stamped 10 min before this poller started (shed).
        db.upsert_feed("push:nws", boot - 600, None)
        monkeypatch.setattr(poller_main, "_boot_time", boot)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_HOLDOFF_S", 120.0)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_PUSH_GRACE_S", 120.0)
        loop = _make_loop()
        ran = []
        # time patch OUTERMOST: mock.patch resolves its target through
        # importlib.import_module, which the inner patch replaces.
        with patch("poller.main.time.time", return_value=boot + 165):
            with patch("importlib.import_module") as imp:
                imp.return_value.run = lambda: ran.append(1)
                _run(loop.maybe_run())
        assert ran == [], "REST fired during the startup push grace on a pre-boot heartbeat"
        assert loop._last_run == boot + 165  # deferred like a healthy push, re-check next interval

    def test_pre_boot_stale_heartbeat_fires_rest_after_grace(self, fresh_db, monkeypatch):
        boot = time.time()
        db.upsert_feed("push:nws", boot - 600, None)
        monkeypatch.setattr(poller_main, "_boot_time", boot)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_HOLDOFF_S", 120.0)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_PUSH_GRACE_S", 120.0)
        loop = _make_loop()
        ran = []
        # time patch OUTERMOST: mock.patch resolves its target through
        # importlib.import_module, which the inner patch replaces.
        with patch("poller.main.time.time", return_value=boot + 241):
            with patch("importlib.import_module") as imp:
                imp.return_value.run = lambda: ran.append(1)
                _run(loop.maybe_run())
        assert ran == [1], "push still stale after HOLDOFF+GRACE must fall back to REST"

    def test_post_boot_stale_heartbeat_fires_rest_immediately(self, fresh_db, monkeypatch):
        boot = time.time()
        # Feed came up after boot, stamped once, then died 5 min ago.
        db.upsert_feed("push:nws", boot + 20, None)
        monkeypatch.setattr(poller_main, "_boot_time", boot)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_HOLDOFF_S", 120.0)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_PUSH_GRACE_S", 120.0)
        loop = _make_loop()
        ran = []
        # time patch OUTERMOST: mock.patch resolves its target through
        # importlib.import_module, which the inner patch replaces.
        with patch("poller.main.time.time", return_value=boot + 165):
            with patch("importlib.import_module") as imp:
                imp.return_value.run = lambda: ran.append(1)
                _run(loop.maybe_run())
        assert ran == [1], "a heartbeat stamped after boot that went stale is a live failure"

    def test_fresh_heartbeat_still_defers_during_grace(self, fresh_db, monkeypatch):
        boot = time.time()
        monkeypatch.setattr(poller_main, "_boot_time", boot)
        loop = _make_loop()
        ran = []
        with patch("poller.main.time.time", return_value=boot + 165):
            with patch("importlib.import_module") as imp:
                imp.return_value.run = lambda: ran.append(1)
                db.upsert_feed("push:nws", boot + 160, None)
                _run(loop.maybe_run())
        assert ran == []

    def test_grace_disabled_restores_old_behaviour(self, fresh_db, monkeypatch):
        boot = time.time()
        db.upsert_feed("push:fns", boot - 600, None)
        monkeypatch.setattr(poller_main, "_boot_time", boot)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_PUSH_GRACE_S", 0.0)
        loop = _make_loop(name="notam", push_feed="fns")
        ran = []
        # time patch OUTERMOST: mock.patch resolves its target through
        # importlib.import_module, which the inner patch replaces.
        with patch("poller.main.time.time", return_value=boot + 165):
            with patch("importlib.import_module") as imp:
                imp.return_value.run = lambda: ran.append(1)
                _run(loop.maybe_run())
        assert ran == [1]

    def test_push_grace_active_helper(self, fresh_db, monkeypatch):
        boot = 1_000_000.0
        monkeypatch.setattr(poller_main, "_boot_time", boot)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_HOLDOFF_S", 120.0)
        monkeypatch.setattr(poller_main, "POLLER_STARTUP_PUSH_GRACE_S", 120.0)
        # Never heartbeated at all: treated as pre-boot (grace applies).
        assert poller_main.push_grace_active("nws", now=boot + 100) is True
        assert poller_main.push_grace_active("nws", now=boot + 240) is False
        db.upsert_feed("push:nws", boot - 1, None)
        assert poller_main.push_grace_active("nws", now=boot + 239) is True
        db.upsert_feed("push:nws", boot + 1, None)
        assert poller_main.push_grace_active("nws", now=boot + 100) is False


# -- amtrak wiring + per-feed max age ------------------------------------------

class TestAmtrakSchedule:

    def test_amtrak_in_fetch_schedule_with_push_gate(self):
        entry = next(s for s in poller_main.FETCH_SCHEDULE if s["name"] == "amtrak")
        assert entry["module"] == "poller.fetchers.amtrak"
        assert entry["push_feed"] == "amtrak"
        assert entry["push_max_age"] >= 600, "amtrak push stamps once per 300 s poll"
        # Every schedule entry must still construct a FetchLoop.
        for s in poller_main.FETCH_SCHEDULE:
            poller_main.FetchLoop(**s)

    def test_amtrak_is_a_valid_refresh_feed_target(self, fresh_db, tmp_path):
        db.insert_trigger("t1", "refresh_feed", {"feed_name": "amtrak"})
        reactor = poller_main.TriggerReactor(tmp_path, tmp_path)
        with patch("importlib.import_module") as imp:
            imp.return_value.run = lambda: {"ok": True}
            _run(reactor._run_fetcher("amtrak", "t1"))
        row = next(t for t in db.get_triggers(outcome="success") if t["id"] == "t1")
        assert row["outcome"] == "success"

    def test_per_feed_max_age_keeps_amtrak_deferring(self, fresh_db, monkeypatch):
        boot = time.time()
        monkeypatch.setattr(poller_main, "_boot_time", boot - 3600)  # long-running poller
        loop = _make_loop(name="amtrak", push_feed="amtrak", push_max_age=660)
        ran = []
        with patch("importlib.import_module") as imp:
            imp.return_value.run = lambda: ran.append(1)
            # 4 min old: dead by the 90 s default, healthy by the amtrak gate.
            db.upsert_feed("push:amtrak", boot - 240, None)
            _run(loop.maybe_run())
            assert ran == []
            loop._last_run = 0.0
            db.upsert_feed("push:amtrak", boot - 900, None)
            _run(loop.maybe_run())
            assert ran == [1]

    def test_failover_push_last_seen(self, fresh_db):
        assert failover.push_last_seen("amtrak") is None
        db.upsert_feed("push:amtrak", 12345.0, "amtrak-tracker stopped")
        assert failover.push_last_seen("amtrak") == 12345.0  # error does not hide the stamp
        assert failover.push_is_healthy("amtrak", 10 ** 9) is False  # error still means unhealthy


# -- kickover guardrail shed awareness -----------------------------------------

class TestGuardrailShedAwareness:

    @pytest.fixture
    def g(self):
        return _load_guardrail()

    def _states(self, now, push_age, rest_age):
        rows = {}
        rows["push:nws"] = {"feed_name": "push:nws", "fetched_at": now - push_age,
                            "error": "nwws: down"}
        if rest_age is not None:
            rows["nws"] = {"feed_name": "nws", "fetched_at": now - rest_age, "error": None}
        return rows

    def _check(self, g, states, guard_state, now):
        # feed_state is read through failover.push_is_healthy too; make the
        # DB agree with the dict the script was handed.
        for r in states.values():
            db.upsert_feed(r["feed_name"], r["fetched_at"], r["error"])
        return g.check_one("nws", "nws", 300, states, dry_run=True,
                           push_max_age=90, guard_state=guard_state, now=now)

    def test_gap_forces_when_no_shed(self, fresh_db, g):
        now = time.time()
        assert self._check(g, self._states(now, 3600, 3600), {}, now) is True

    def test_no_force_during_lockdown(self, fresh_db, g):
        now = time.time()
        guard = {"tier": 2, "shed_at": now - 1800}
        assert self._check(g, self._states(now, 3600, 3600), guard, now) is False

    def test_no_force_inside_restore_grace(self, fresh_db, g):
        now = time.time()
        guard = {"tier": 0, "restored_at": now - 120, "restored_via": "guard"}
        assert self._check(g, self._states(now, 3600, 3600), guard, now) is False

    def test_shed_time_does_not_count_toward_rest_stale(self, fresh_db, g):
        now = time.time()
        # Restored 15 min ago (outside the 600 s grace); last REST run was an
        # hour ago, but 15 min < the 20 min threshold measured from restore.
        guard = {"tier": 0, "restored_at": now - 900, "restored_via": "external"}
        assert self._check(g, self._states(now, 3600, 3600), guard, now) is False
        # 25 min after restore with still no REST run: real gap, force.
        guard = {"tier": 0, "restored_at": now - 1500, "restored_via": "external"}
        assert self._check(g, self._states(now, 3600, 3600), guard, now) is True

    def test_tier1_is_not_a_shed_for_watched_feeds(self, fresh_db, g):
        now = time.time()
        guard = {"tier": 1, "shed_at": now - 1800}
        assert self._check(g, self._states(now, 3600, 3600), guard, now) is True

    def test_rest_never_ran_does_not_crash(self, fresh_db, g):
        now = time.time()
        assert self._check(g, self._states(now, 3600, None), {}, now) is True

    def test_read_guard_state_missing_or_garbage(self, g, tmp_path):
        assert g.read_guard_state(str(tmp_path / "nope.json")) == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert g.read_guard_state(str(bad)) == {}
        assert g.shed_reason({}, time.time(), 600) is None

    def test_watched_mirrors_fetch_schedule(self, g):
        sched = {s["name"]: s for s in poller_main.FETCH_SCHEDULE if s.get("push_feed")}
        watched = {w[0]: w for w in g.WATCHED}
        assert set(watched) == set(sched)
        for name, (_, push_feed, interval, max_age) in watched.items():
            assert sched[name]["push_feed"] == push_feed
            assert sched[name]["interval"] == interval
            assert max_age == sched[name].get("push_max_age", poller_main.FALLBACK_MAX_AGE)
