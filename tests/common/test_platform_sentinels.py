"""tests/common/test_platform_sentinels.py -- 2026-11-18 platform-side sentinels (they run on the box so they
keep working through an agent-side lapse): plan coverage, ground-stop tripwire, per-centre first
messages, agent-lapse escalation with dismissal. Clock, database (sqlite twin), ntfy and board are
stubbed; nothing reaches a real service."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from common import agent_lapse as al  # noqa: E402
from common import artcc_sentinel as ac  # noqa: E402
from common import db  # noqa: E402
from common import fdps_predeparture as fp  # noqa: E402
from common import ground_stop_tripwire as gst  # noqa: E402
from common import plan_coverage as pc  # noqa: E402
from common import platform_sentinels as ps  # noqa: E402
from common import push_policy_registry as reg  # noqa: E402


def ep(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


@pytest.fixture(autouse=True)
def _reports(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(ps, "HANDOFF_DIR", tmp_path / "handoff")
    monkeypatch.setattr(ps, "load1", lambda: 1.0)


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(ps, "push", lambda kind, message, **kw: sent.append({"kind": kind, "message": message, **kw})
                        or True)
    return sent


# ------------------------------------------------------------------ shared plumbing
def test_times_are_utc_with_et_beside():
    assert ps.fmt(ep("2026-11-18T18:51:00Z")) == "2026-11-18 18:51Z (13:51 ET)"
    assert ps.fmt(ep("2026-11-01T05:30:00Z")).endswith("(01:30 ET)")   # before the 06:00Z DST change
    assert ps.fmt(None) == "unknown"


def test_heavy_runs_skip_above_the_load_line(monkeypatch, caplog):
    monkeypatch.setattr(ps, "load1", lambda: 30.0)
    assert ps.load_ok("x") is False
    assert pc.run() is None and ac.run() is None


def test_a_failing_run_exits_zero_with_its_reason(caplog):
    def boom():
        raise RuntimeError("db down")
    assert ps.run_safely("x", boom) == 0
    assert "db down" in caplog.text


def test_every_sentinel_kind_is_registered():
    for v, k in [("platform_ops", "plan_coverage_step"), ("platform_ops", "artcc_first_drop"),
                 ("platform_ops", "ground_stop_wide"), ("platform_ops", "agent_lapse_nudge"),
                 ("platform_ops", "agent_lapse_email"), ("platform_ops", "agent_lapse_escalate"),
                 ("aviation", "ground_stop_watched_leg")]:
        assert reg.resolve(v, k) is not None, (v, k)
    assert reg.resolve("platform_ops", "agent_lapse_nudge").first == 2


def test_ntfy_actions_header_is_sent(monkeypatch):
    from common import ntfy_push
    seen = {}

    class R:
        status_code = 200

        def raise_for_status(self):
            return None
    monkeypatch.setattr(ntfy_push.requests, "post", lambda url, data=None, headers=None, timeout=None:
                        seen.update(headers or {}) or R())
    assert ntfy_push.send("ops-health", "x", title="t", actions="view, Open console, https://h/console, clear=true",
                          kind="agent_lapse_escalate", vertical="platform_ops", entity="a",
                          detail={"stage": "escalate", "episode": "1"})
    assert seen["Actions"].startswith("view, Open console")


# ------------------------------------------------------------------ 1. per-watch guard wording
def test_guard_says_plan_not_yet_visible_when_tfms_has_the_schedule():
    entry = {"id": "wl-flight-ash4006-20261118", "identifier": "ASH4006", "origin": "KSAV",
             "destination": "KIAD", "oooi_phase": "pre_departure"}
    tfms = {"original_departure": "2026-11-18T18:35:00Z", "airline_out_time": None, "airline_off_time": None}
    v = fp.assess(entry, None, tfms, ep("2026-11-18T18:00:00Z"))
    assert "FDPS plan not yet visible (TFMS has the schedule" in v["summary"]


# ------------------------------------------------------------------ 2. plan coverage + first_seen
def test_first_seen_is_set_on_insert_only_with_centre_and_source():
    db.init_db_all() if hasattr(db, "init_db_all") else db.init_db()
    raw1 = '<flight centre="ZJX" source="FH" system="SLC" timestamp="2026-11-18T12:00:00Z">'
    raw2 = '<flight centre="ZDC" source="TH" system="SLC" timestamp="2026-11-18T19:57:00Z">'
    db.upsert_flight_event("g1", "ASH", "4006", "KSAV", "KIAD", None, None, None, "active",
                           None, None, None, None, raw1)
    with db.conn() as c:
        first = dict(c.execute("SELECT first_seen_at, first_seen_centre, first_seen_source FROM flight_events "
                               "WHERE flight_id='g1'").fetchone())
    db.upsert_flight_event("g1", "ASH", "4006", "KSAV", "KIAD", None, None, None, "active",
                           None, None, None, None, raw2)
    with db.conn() as c:
        again = dict(c.execute("SELECT first_seen_at, first_seen_centre, first_seen_source FROM flight_events "
                               "WHERE flight_id='g1'").fetchone())
    assert first["first_seen_centre"] == "ZJX" and first["first_seen_source"] == "FH"
    assert again == first and first["first_seen_at"] is not None


def _leg(cs, out, off, o="KSAV", d="KIAD"):
    return {"callsign": cs, "origin": o, "destination": d, "out": out, "off": off}


def test_coverage_counts_visible_before_out_and_skips_unknown_rows():
    legs = [_leg("ASH4006", "2026-11-18T18:35:00Z", "2026-11-18T18:51:00Z"),
            _leg("ASH6220", "2026-11-18T14:00:00Z", "2026-11-18T14:12:00Z"),
            _leg("ASH1", "2026-11-18T10:00:00Z", "2026-11-18T10:10:00Z"),
            _leg("UAL9", "2026-11-18T10:00:00Z", "2026-11-18T10:10:00Z")]
    events = [{"callsign": "ASH4006", "origin": "KSAV", "destination": "KIAD", "first_seen_at": ep("2026-11-18T18:55:00Z")},
              {"callsign": "ASH6220", "origin": "KSAV", "destination": "KIAD", "first_seen_at": ep("2026-11-18T12:12:00Z")},
              {"callsign": "UAL9", "origin": "KSAV", "destination": "KIAD", "first_seen_at": None}]
    out = pc.compute(legs, events)
    ash = out["ASH"]
    assert (ash["legs"], ash["measured"], ash["visible"]) == (3, 3, 1)     # 4006 late, 6220 early, ASH1 no row
    assert ash["median_lead_min"] == 108.0 and ash["group"] == "regional"
    assert out["UAL"]["measured"] == 0 and out["UAL"]["share"] is None   # pre-0088 row: unknown, not "missing"


def test_coverage_step_change_against_the_trailing_median():
    hist = [{"ASH": {"share": 0.15, "measured": 300}} for _ in range(10)]
    assert pc.steps({"ASH": {"share": 0.16, "measured": 300}}, hist) == []
    s = pc.steps({"ASH": {"share": 0.80, "measured": 300}}, hist)          # a national cut-over shows as a rise
    assert s[0]["direction"] == "rise" and s[0]["delta_pp"] == 65.0
    assert pc.steps({"ASH": {"share": 0.80, "measured": 10}}, hist) == []  # too few legs
    assert pc.steps({"ASH": {"share": 0.80, "measured": 300}}, hist[:2]) == []   # not enough history


# ------------------------------------------------------------------ 3. ground-stop tripwire
def _gs(pid, fac, start, end, src="tfms", typ="GS"):
    return {"program_id": pid, "type": typ, "facility": fac, "source": src, "active": 0,
            "fetched_at": ep(start), "raw_json": json.dumps({"start_time": ep(start), "end_time": ep(end)})}


FIXTURE_1112 = [_gs("GS-SEA", "SEA", "2026-11-12T17:01:00Z", "2026-11-12T18:30:00Z"),
                _gs("GS-EWR", "EWR", "2026-11-12T18:08:00Z", "2026-11-12T23:45:00Z"),
                _gs("GS-JFK", "JFK", "2026-11-12T18:39:00Z", "2026-11-13T00:30:00Z"),
                _gs("GS-LGA", "LGA", "2026-11-12T18:04:00Z", "2026-11-12T23:30:00Z"),
                _gs("GS-BOS", "BOS", "2026-11-12T18:40:00Z", "2026-11-12T22:40:00Z"),
                _gs("GS-PHL", "PHL", "2026-11-12T18:20:00Z", "2026-11-12T18:52:00Z"),   # dips the count to 4 at 18:52
                _gs("GS-PHL2", "PHL", "2026-11-12T19:00:00Z", "2026-11-12T22:00:00Z"),
                {"program_id": "SEA-GS-rest", "type": "GS", "facility": "SEA", "source": "rest", "active": 0,
                 "fetched_at": ep("2026-11-12T17:05:00Z"), "raw_json": json.dumps({"arpt": "SEA"})}]


def test_replay_fires_once_per_wide_episode_with_hysteresis(monkeypatch):
    monkeypatch.setattr(gst, "WIDE_N", 5)
    progs = gst.parse_rows(FIXTURE_1112)
    ev = gst.replay(progs, ep("2026-11-12T12:00:00Z"), ep("2026-11-14T12:00:00Z"))
    kinds = [e["event"] for e in ev]
    assert kinds == ["wide_on", "wide_off"]                  # the 18:52 dip to 4 does not flap it off
    assert ev[0]["at_fmt"].startswith("2026-11-12 18:40Z") and len(ev[0]["facilities"]) == 5


def test_replay_names_a_watched_leg_once(monkeypatch):
    progs = gst.parse_rows(FIXTURE_1112)
    w = [{"id": "wl-1", "identifier": "UAL123", "origin": "KDCA", "destination": "KEWR"}]
    ev = [e for e in gst.replay(progs, ep("2026-11-12T12:00:00Z"), ep("2026-11-13T12:00:00Z"), w)
          if e["event"] == "watched_leg"]
    assert len(ev) == 1 and ev[0]["facility"] == "EWR" and ev[0]["end"] == "destination"


def test_live_run_is_quiet_when_clear_and_pushes_wide_then_cleared(monkeypatch, pushes):
    now = {"t": ep("2026-11-12T12:00:00Z")}
    monkeypatch.setattr(ps, "now", lambda: now["t"])
    monkeypatch.setattr(gst, "_watches", lambda: [])
    progs = gst.parse_rows(FIXTURE_1112)
    monkeypatch.setattr(gst, "_fetch", lambda since=None: progs)
    gst.run()
    assert pushes == []                                       # quiet first run, nothing in effect
    now["t"] = ep("2026-11-12T19:30:00Z")
    gst.run()
    now["t"] = ep("2026-11-13T02:00:00Z")
    gst.run()
    assert [p["detail"]["state"] for p in pushes] == ["wide", "clear"]
    assert "(14:30 ET)" in pushes[0]["message"]


# ------------------------------------------------------------------ 4. per-centre sentinel
def test_artcc_counts_first_messages_and_tests_the_filter_hypothesis():
    t0 = ep("2026-11-18T12:00:00Z")
    events = [{"callsign": "ASH6220", "origin": "KSAV", "destination": "KIAD", "first_seen_at": t0, "centre": "ZJX",
               "source": "FH"},
              {"callsign": "ASH4006", "origin": "KSAV", "destination": "KIAD", "first_seen_at": ep("2026-11-18T18:55:00Z"),
               "centre": "ZDC", "source": "TH"}]
    legs = [_leg("ASH6220", "2026-11-18T14:00:00Z", "2026-11-18T14:12:00Z"),
            _leg("ASH4006", "2026-11-18T18:35:00Z", "2026-11-18T18:51:00Z")]
    today = ac.compute(events, legs)
    assert today["ZJX"] == {"firsts": 1, "firsts_fh": 1, "predep": 1, "matched": 1}
    assert today["ZDC"]["predep"] == 0
    hist = [{"ZDC": {"firsts": 30, "matched": 30, "predep": 0}, "ZJX": {"firsts": 30, "matched": 30, "predep": 20}}
            for _ in range(5)]
    h = ac.hypothesis(hist)
    assert h["never_predeparture"] == ["ZDC"] and "ZJX" in h["some_predeparture"]
    assert ac.drops({"ZJX": {"firsts": 5}}, hist)[0]["centre"] in ("ZDC", "ZJX")
    found = {d["centre"]: d for d in ac.drops({"ZJX": {"firsts": 5}}, hist)}
    assert found["ZDC"]["firsts"] == 0 and found["ZJX"]["drop_pct"] > 60


# ------------------------------------------------------------------ 5. agent-lapse sentinel
@pytest.fixture
def lapse(monkeypatch, pushes):
    clock = {"t": ep("2026-11-18T12:00:00Z")}
    acts = {"ctdc-agent-anthropic-claude-ea": {"board": ep("2026-11-18T11:00:00Z"), "tools": None, "signed": None},
            "ctdc-agent-anthropic-cowork": {"board": ep("2026-11-18T11:00:00Z"), "tools": None, "signed": None},
            "ctdc-agent-openai-chatgpt": {"board": ep("2026-11-18T11:30:00Z"), "tools": None, "signed": None}}
    live = {n: {"inert": False, "kill_orders": 0, "signer_active": True, "tokens_revoked_24h": 0,
                "login_refresh": "root-only"} for n in acts}
    board = []
    monkeypatch.setattr(al, "accounts", lambda: sorted(acts))
    monkeypatch.setattr(al, "last_activity", lambda names, now: {n: dict(acts[n]) for n in names})
    monkeypatch.setattr(al, "liveness", lambda n, now: dict(live[n]))
    monkeypatch.setattr(db, "board_insert", lambda **kw: board.append(kw) or {"id": "brd-x", "seq": 1})
    return {"clock": clock, "acts": acts, "live": live, "board": board, "pushes": pushes}


def _at(lapse, iso):
    lapse["clock"]["t"] = ep(iso)
    return al.run(now=lapse["clock"]["t"])


def test_escalation_runs_6_12_24_once_each_per_episode(lapse):
    p = lapse["pushes"]
    _at(lapse, "2026-11-18T16:00:00Z")
    assert p == []
    _at(lapse, "2026-11-18T17:05:00Z")                      # 6 h after 11:00 (both anthropic accounts)
    kinds = sorted(x["kind"] for x in p)
    assert kinds == ["agent_lapse_nudge", "agent_lapse_nudge"] and all(x["priority"] == 2 for x in p)
    assert {b["to_side"] for b in lapse["board"]} == {"ctdc-agent-anthropic-claude-ea", "ctdc-agent-anthropic-cowork"}
    assert all(b["from_side"] == "dispatch" and b["thread"] == "coord" for b in lapse["board"])
    _at(lapse, "2026-11-18T17:35:00Z")
    assert len(p) == 3 and p[-1]["kind"] == "agent_lapse_nudge"       # chatgpt reaches 6 h; no repeats
    _at(lapse, "2026-11-18T23:05:00Z")
    emails = [x for x in p if x["kind"] == "agent_lapse_email"]
    assert len(emails) == 2 and all(x["email"] for x in emails)
    _at(lapse, "2026-11-19T11:05:00Z")
    esc = [x for x in p if x["kind"] == "agent_lapse_escalate"]
    assert len(esc) == 2
    e = esc[0]
    assert e["priority"] == 4 and e["email"] and e["actions"].startswith("view, Open console (dismiss), https://")
    for needed in ("Last board post: 2026-11-18 11:00Z (06:00 ET)", "team-liveness.sh --status --user",
                   "--reactivate", "ctdc-agent-lapse.sh dismiss", "signer active=True"):
        assert needed in e["message"], needed
    n = len(p)
    _at(lapse, "2026-11-19T11:35:00Z")
    assert len([x for x in p if x["kind"] == "agent_lapse_escalate"]) == 3   # chatgpt's own 24 h
    assert len(p) == n + 1


def test_a_whole_vendor_silent_together_reads_as_a_subscription_lapse(lapse):
    _at(lapse, "2026-11-19T11:05:00Z")
    esc = [x for x in lapse["pushes"] if x["kind"] == "agent_lapse_escalate"]
    assert esc and all("subscription or login lapse" in x["message"] for x in esc
                       if "anthropic" in x["entity"])


def test_activity_resuming_ends_the_episode_and_a_new_silence_starts_over(lapse):
    _at(lapse, "2026-11-18T17:05:00Z")
    lapse["acts"]["ctdc-agent-anthropic-cowork"]["board"] = ep("2026-11-18T17:10:00Z")
    _at(lapse, "2026-11-18T17:20:00Z")
    _at(lapse, "2026-11-18T23:15:00Z")                     # cowork: a new 6 h silence -> a new nudge
    cow = [x for x in lapse["pushes"] if x["entity"] == "ctdc-agent-anthropic-cowork"]
    assert [x["kind"] for x in cow] == ["agent_lapse_nudge", "agent_lapse_nudge"]


def test_dismissal_holds_until_it_ends_or_activity_resumes(lapse):
    t = ep("2026-11-18T17:00:00Z")
    al.dismiss("ctdc-agent-anthropic-claude-ea", al.parse_for("24h"), "known: subscription renewing", by="corporatetraveldc",
               via="ssh", now=t)
    _at(lapse, "2026-11-19T11:05:00Z")
    ea = [x for x in lapse["pushes"] if x["entity"] == "ctdc-agent-anthropic-claude-ea"]
    assert ea == []
    rows = {r["account"]: r for r in al.current()}
    assert rows["ctdc-agent-anthropic-claude-ea"]["stage"] == "dismissed"
    _at(lapse, "2026-11-19T17:30:00Z")                    # the 24 h window ended -> escalation resumes
    assert [x["kind"] for x in lapse["pushes"] if x["entity"] == "ctdc-agent-anthropic-claude-ea"] == ["agent_lapse_escalate"]
    with db.conn() as c:
        al.ensure(c)
        rec = dict(c.execute("SELECT by_who, via, reason FROM agent_lapse_dismissals").fetchone())
    assert rec == {"by_who": "corporatetraveldc", "via": "ssh", "reason": "known: subscription renewing"}


def test_an_inert_account_is_listed_not_escalated(lapse):
    lapse["live"]["ctdc-agent-openai-chatgpt"]["inert"] = "2026-11-01T02:05:19+00:00 inert: login stale"
    _at(lapse, "2026-11-19T12:00:00Z")
    assert not [x for x in lapse["pushes"] if x["entity"] == "ctdc-agent-openai-chatgpt"]
    assert {r["account"]: r["stage"] for r in al.current()}["ctdc-agent-openai-chatgpt"] == "inert"


def test_dismiss_validates_its_inputs():
    with pytest.raises(ValueError):
        al.dismiss("ctdc-agent-x", 3600, "", by="op", via="ssh")
    with pytest.raises(ValueError):
        al.dismiss("Bad Name!", 3600, "why", by="op", via="ssh")
    with pytest.raises(ValueError):
        al.parse_for("45d")
    with pytest.raises(ValueError):
        al.parse_for("soon")
    assert al.parse_for("90m") == 5400 and al.parse_for(None) == 86400


def test_ssh_dismiss_refuses_any_account_but_the_operator():
    env = {**os.environ, "CTDC_OPERATOR_ACCOUNT": "corporatetraveldc-not-me"}
    r = subprocess.run(["bash", str(ROOT / "scripts/ctdc-agent-lapse.sh"), "dismiss", "--account", "x", "--reason", "y"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 77 and "nothing changed" in r.stderr


def test_units_are_timers_without_requires_and_call_the_sentinel_script():
    udir = ROOT / ".config/systemd/user"
    for name, arg in [("plan-coverage-sentinel", "plan-coverage"), ("ground-stop-tripwire", "ground-stop"),
                      ("artcc-sentinel", "artcc"), ("agent-lapse-sentinel", "agent-lapse run")]:
        svc = (udir / f"corporatetraveldc-{name}.service").read_text()
        tmr = (udir / f"corporatetraveldc-{name}.timer").read_text()
        assert f"scripts/platform-sentinel.py {arg}" in svc
        assert "Persistent=true" in tmr and "RandomizedDelaySec=" in tmr
        assert "Requires=" not in tmr.replace("# no Requires=", "")
