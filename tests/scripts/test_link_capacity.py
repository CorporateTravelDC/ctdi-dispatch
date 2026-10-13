"""tests/test_link_capacity.py -- the ramp and the assessment, driven by a simulated
link (no real traffic): a fake byte counter and a fake transfer process."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/lib"))

import link_capacity as lc  # noqa: E402

MB = lc.MB


class FakeLink:
    """A link with a ceiling; the transfer moves min(cap, ceiling) per tick. Optional
    policer (fast first ticks), jitter, and a 'feed' that degrades above a cap."""

    def __init__(self, ceiling, total_mb=10_000, police_after=None, police_rate=None, jitter=None, degrade_above=None):
        self.ceiling, self.left = ceiling, total_mb * MB
        self.police_after, self.police_rate, self.jitter = police_after, police_rate, jitter or []
        self.degrade_above, self.bytes, self.cap, self.ticks, self.running = degrade_above, 0, None, 0, False

    def counter(self):
        return self.bytes

    def launcher(self, cmd):
        self.cap = float(cmd.split("--cap ")[1]); self.ticks = 0; self.running = True
        link = self

        class P:
            returncode = None

            def poll(self_):
                if not link.running:
                    return self_.returncode
                rate = min(link.cap or link.ceiling, link.ceiling)
                if link.police_after is not None and link.ticks >= link.police_after:
                    rate = min(rate, link.police_rate)
                if link.jitter:
                    rate *= link.jitter[link.ticks % len(link.jitter)]
                step = min(link.left, rate * MB)
                link.bytes += int(step); link.left -= step; link.ticks += 1
                if link.left <= 0:
                    link.running = False; self_.returncode = 0
                    return 0
                return None

            def terminate(self_):
                link.running = False; self_.returncode = -15

            def wait(self_, timeout=None):
                return self_.returncode

            def kill(self_):
                self.terminate()
        return P()

    def feed_probe(self):
        def p():
            bad = self.degrade_above is not None and (self.cap or 0) > self.degrade_above and self.running
            return {"ok": not bad, "value": 300 if bad else 5, "detail": ""}
        p.name = "feed-age"
        return p


def cfg(tmp_path, **kw):
    base = dict(command="xfer --cap {cap}", direction="up", iface="lo", caps_mb_s=[1, 2, 4, 8, 16],
                phase_s=8, sample_s=0, ledger=str(tmp_path / "led.jsonl"))
    base.update(kw)
    return lc.RampConfig(**base)


def run(link, c):
    return lc.ramp(c, [link.feed_probe()], counter=link.counter, launcher=link.launcher, log=lambda *_: None)


def test_link_bound_stops_raising_and_finishes_at_last_good(tmp_path, monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 1.0), t[0])[1])
    link = FakeLink(ceiling=5, total_mb=150)
    res = run(link, cfg(tmp_path))
    verdicts = [p.get("verdict") for p in res["phases"][:-1]]
    assert verdicts[:3] == ["ok", "ok", "ok"] and verdicts[3] == "link-bound"
    assert res["safe_cap_mb_s"] == 4 and res["completed"]


def test_degraded_health_drops_back(tmp_path, monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 1.0), t[0])[1])
    link = FakeLink(ceiling=50, total_mb=200, degrade_above=3)
    res = run(link, cfg(tmp_path))
    assert [p["verdict"] for p in res["phases"][:3]] == ["ok", "ok", "degraded"]
    assert res["safe_cap_mb_s"] == 2 and res["completed"]


def test_degraded_at_first_cap_goes_below_it(tmp_path, monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 1.0), t[0])[1])
    link = FakeLink(ceiling=50, total_mb=40, degrade_above=0.5)
    res = run(link, cfg(tmp_path))
    assert res["phases"][0]["verdict"] == "degraded" and res["safe_cap_mb_s"] == 0.5


def _clock(monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 1.0), t[0])[1])


def test_assessment_flags_a_policer_and_recommends_headroom(tmp_path, monkeypatch):
    _clock(monkeypatch)
    link = FakeLink(ceiling=50, total_mb=400, police_after=2, police_rate=3)   # fast start, then 3 MB/s flat
    run(link, cfg(tmp_path, caps_mb_s=[8]))
    a = lc.assess(str(tmp_path / "led.jsonl"))["directions"]["up"]
    assert "burst-then-police" in a["flags"] and a["needs_shaping"]
    assert a["recommended_cap_mb_s"] == round(min(a["safe_caps_mb_s"]) * 0.8, 3)


def test_assessment_flags_a_shared_link(tmp_path, monkeypatch):
    _clock(monkeypatch)
    link = FakeLink(ceiling=50, total_mb=300, jitter=[1.0, 0.3, 1.0, 0.4])      # big swings at a fixed cap
    run(link, cfg(tmp_path, caps_mb_s=[2, 4]))
    a = lc.assess(str(tmp_path / "led.jsonl"))["directions"]["up"]
    assert "variable-throughput" in a["flags"] and a["shared_link_suspected"]


def test_assessment_flags_latency_inflation(tmp_path):
    led = tmp_path / "led.jsonl"
    rows = [{"event": "start", "direction": "up", "baseline": {"tcp:gw:443": {"ok": True, "value": 12.0}}},
            {"event": "phase", "direction": "up", "verdict": "ok", "probe_medians": {"tcp:gw:443": 140.0}},
            {"event": "end", "direction": "up", "safe_cap_mb_s": 4}]
    led.write_text("".join(json.dumps(r) + "\n" for r in rows))
    a = lc.assess(str(led))["directions"]["up"]
    assert "latency-inflation" in a["flags"] and a["needs_shaping"] and a["recommended_cap_mb_s"] == 3.2


def test_download_direction_is_recorded_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 1.0), t[0])[1])
    run(FakeLink(ceiling=5, total_mb=80), cfg(tmp_path, direction="down"))
    a = lc.assess(str(tmp_path / "led.jsonl"))["directions"]
    assert "down" in a and "up" not in a and a["down"]["recommended_cap_mbit_s"] == round(a["down"]["recommended_cap_mb_s"] * 8, 1)


def test_ledger_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    t = [0.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 1.0), t[0])[1])
    run(FakeLink(ceiling=5, total_mb=20), cfg(tmp_path))
    led = tmp_path / "led.jsonl"
    assert (led.stat().st_mode & 0o777) == 0o600
    assert all(json.loads(l)["event"] in ("start", "phase", "end") for l in led.read_text().splitlines())


def test_mcp_run_is_gated_and_needs_a_health_probe():
    r = lc.link_ramp_run("rclone sync a b --bwlimit {cap}M")
    assert r["blocked"] and "health probe" in r["reason"]
    r = lc.link_ramp_run("rclone sync a b --bwlimit {cap}M", latency_host="example.invalid")
    assert r["blocked"] and "confirmed=True" in r["reason"] and r["plan"]["probes"] == ["tcp:example.invalid:443"]


# -- CTDI wrapper (scripts/backup/uplink_ramp.py) --

def test_ctdi_wrapper_feed_probe_and_command(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("uplink_ramp", Path(__file__).resolve().parents[2] / "scripts/backup/uplink_ramp.py")
    ur = importlib.util.module_from_spec(spec); spec.loader.exec_module(ur)
    monkeypatch.setenv("UPLINK_RAMP_FAKE_AGES", json.dumps({"push:fdps": 5, "push:tbfm": 900, "push:itws": 12}))
    r = ur.swim_feed_probe(90)()
    assert r["ok"] and r["value"] == 12                       # TBFM is quiet at night by nature: not counted
    monkeypatch.setenv("UPLINK_RAMP_FAKE_AGES", json.dumps({"push:fdps": 300}))
    assert ur.swim_feed_probe(90)()["ok"] is False


def test_vendored_copy_matches_the_toolkit_original():
    toolkit = Path("/opt/corporatetraveldc/public/agentic-management-tooling-mcp/agentic/link_capacity.py")
    if not toolkit.exists():
        import pytest
        pytest.skip("toolkit checkout not present")
    vendored = (Path(__file__).resolve().parents[2] / "scripts/lib/link_capacity.py").read_text().split("\n", 2)[2]
    assert vendored == toolkit.read_text()
