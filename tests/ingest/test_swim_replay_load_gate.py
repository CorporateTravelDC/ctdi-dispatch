"""Backlog replay after a LOCKDOWN restore is gated by load (lean-out L2 / F15, 2026-10-19).

Each 30-min LOCKDOWN outage was replayed from the 8 h durable-queue window as fast as possible
on restore, pushing load1 back toward the 40 trip line. While a feed is behind (last message
older than the replay lag) it now stops pulling whenever load1 >= SWIM_REPLAY_MAX_LOAD, resumes
below that minus the gap, never gates caught-up traffic, never stalls on an unreadable load,
and stops gating after its per-connection pause budget."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from ingest import swim_client as sc  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def gate(loads, **kw):
    it = iter(loads)
    clock = Clock()
    g = sc._ReplayGate(max_load=30, resume_gap=4, lag_s=120, max_pause_s=kw.get("max_pause_s", 900),
                       check_every_s=0, load_fn=lambda: next(it), clock=clock)
    return g


def test_live_traffic_is_never_gated():
    g = gate([99.0])
    g.observe(5.0)                     # a current message
    assert g.should_pause() is False


def test_replay_pauses_at_max_load_and_resumes_below_the_gap():
    g = gate([31.0, 28.0, 25.9])
    g.observe(1800.0)                  # 30 min behind: replaying
    assert g.should_pause() is True    # 31 >= 30
    assert g.should_pause() is True    # 28 >= 26: still paused (hysteresis)
    assert g.should_pause() is False   # 25.9 < 26: resume


def test_replay_below_max_load_runs():
    g = gate([20.0])
    g.observe(1800.0)
    assert g.should_pause() is False


def test_unknown_load_never_stalls():
    g = gate([None])
    g.observe(1800.0)
    assert g.should_pause() is False


def test_catching_up_clears_the_pause():
    g = gate([35.0])
    g.observe(1800.0)
    assert g.should_pause() is True
    g.observe(3.0)
    assert g.is_paused is False and g.should_pause() is False


def test_pause_budget_then_ungated():
    g = gate([35.0] * 10, max_pause_s=10)
    g.observe(1800.0)
    assert g.should_pause() is True
    g.paused(5.0)
    assert g.should_pause() is True
    g.paused(5.0)                      # budget used
    assert g.exhausted and g.should_pause() is False


def test_disabled_with_zero():
    g = sc._ReplayGate(max_load=0, load_fn=lambda: 99.0)
    g.observe(1800.0)
    assert g.should_pause() is False


def test_load_read_is_rate_limited():
    calls = []
    clock = Clock()
    g = sc._ReplayGate(max_load=30, check_every_s=2.0, load_fn=lambda: calls.append(1) or 10.0, clock=clock)
    g.observe(1800.0)
    for _ in range(5):
        g.should_pause()
    assert len(calls) == 1
    clock.t = 2.0
    g.should_pause()
    assert len(calls) == 2


def test_read_load1_parses_proc_format(tmp_path):
    p = tmp_path / "loadavg"
    p.write_text("23.45 20.10 19.00 5/900 12345\n")
    assert sc._read_load1(str(p)) == 23.45
    assert sc._read_load1(str(tmp_path / "missing")) is None


def test_defaults_sit_between_relay_wait_and_lockdown():
    assert 24 <= sc.REPLAY_MAX_LOAD < 40
    assert sc.REPLAY_MAX_PAUSE_SECONDS < 1800       # feed-db-integrity silence threshold
