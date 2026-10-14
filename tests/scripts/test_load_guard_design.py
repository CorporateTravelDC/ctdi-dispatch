"""2026-10-13 (operator directive): the guard is a LOAD guard. Only load1 >= 40 sheds (LOCKDOWN).
A resume is held only while load1 > 30 or the CPU is above 75C; there is no lower temperature
gate (the 22:05Z load trip could not self-clear at 65.0-65.6C idle against the old < 65C bar).
Temperature hysteresis applies only after the CPU has gone above 75C."""
import importlib.util
import time
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts/thermal-ingest-guard.py"


@pytest.fixture
def guard(monkeypatch):
    spec = importlib.util.spec_from_file_location("tig", PATH)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    box = {"state": {"tier": 0}, "shed": False, "started": False}
    # the live dispatch.env still sets the retired temperature knobs; they must not matter
    monkeypatch.setattr(m, "_cfg", lambda: {"THERMAL_GUARD_ENABLED": "true", "THERMAL_GUARD_TIER1_TEMP_C": "74",
                                           "THERMAL_GUARD_TIER2_TEMP_C": "79", "THERMAL_GUARD_RESUME_TEMP_C": "65.0"})
    monkeypatch.setattr(m, "get_fan_rpm", lambda: 3000)
    monkeypatch.setattr(m, "count_recent_load_fallbacks", lambda w: 0)
    monkeypatch.setattr(m, "_reconcile_stale_tier", lambda tier, a, b: tier)
    monkeypatch.setattr(m, "load_state", lambda: dict(box["state"]))
    monkeypatch.setattr(m, "save_state", lambda s: box.__setitem__("state", dict(s)))
    monkeypatch.setattr(m, "ntfy_alert", lambda *a, **k: None)
    monkeypatch.setattr(m, "feed_ctl", lambda *a, **k: box.__setitem__("shed", True))
    monkeypatch.setattr(m, "_lockdown_stop_stack", lambda: box.__setitem__("shed", True))
    monkeypatch.setattr(m, "_lockdown_start_stack", lambda cfg: box.__setitem__("started", True))
    monkeypatch.setattr(m, "_lockdown_stack_down_list", lambda: [])
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    return m, box


def cycle(m, temp, load):
    m.get_temp_c = lambda: temp
    m.get_load1 = lambda: load
    m.main()


def locked(box, since_s_ago=None):
    box["state"] = {"tier": 2, "shed_at": time.time() - 900, "guard_label": "Load Guard",
                    "below_resume_since": None if since_s_ago is None else time.time() - since_s_ago}


@pytest.mark.parametrize("temp,load,shed", [(82.0, 10.0, False), (76.0, 39.9, False), (60.0, 40.0, True)])
def test_only_load_40_sheds(guard, temp, load, shed):
    m, box = guard
    cycle(m, temp, load)
    assert box["shed"] is shed


def test_resume_at_idle_temperature_after_a_load_trip(guard):       # the 10-13 22:05Z case
    m, box = guard
    locked(box, since_s_ago=400)
    cycle(m, 65.3, 4.0)
    assert box["started"]


def test_load_above_30_holds_the_resume(guard):
    m, box = guard
    locked(box, since_s_ago=400)
    cycle(m, 60.0, 31.0)
    assert not box["started"]
    locked(box, since_s_ago=400)
    cycle(m, 60.0, 29.0)
    assert box["started"]


def test_temperature_holds_only_above_75_with_hysteresis_after(guard):
    m, box = guard
    locked(box, since_s_ago=400)
    cycle(m, 75.5, 5.0)                                                 # above 75: hold, dwell reset
    assert not box["started"] and box["state"]["temp_hold"] and box["state"]["below_resume_since"] is None
    cycle(m, 74.5, 5.0)                                                 # within hysteresis: still held
    assert box["state"]["temp_hold"]
    cycle(m, 73.9, 5.0)                                                 # below 74: hold clears, dwell starts
    assert not box["state"]["temp_hold"] and box["state"]["below_resume_since"] is not None
    box["state"]["below_resume_since"] -= 400
    cycle(m, 73.9, 5.0)
    assert box["started"]
