"""
tests/scripts/test_serial_bringup.py -- scripts/lib/serial-bringup.sh and
its two bash callers (stack-boot-ctl.sh, ingest-feed-ctl.sh), run against a
fake `systemctl` and a fake `sleep` on PATH plus a fake load source
(SERIAL_BRINGUP_LOADAVG). No real unit is ever touched: the fake systemctl
only appends to a log, and the fake sleep advances a virtual tick counter
and rewrites the fake loadavg file from a per-test schedule, so the
"10 s sample" loop runs in milliseconds.

Proves (operator directive 2026-09-06, work-order item 1):
  (a) the next unit does not start while load1 >= SERIAL_BRINGUP_LOAD_MAX,
  (b) the hard cap SERIAL_BRINGUP_MAX_WAIT_S releases the wait (never hangs),
  (c) unit order is preserved, per-unit windows respected,
  (d) "start" of an already-active unit is skipped without a window,
and that stack-boot-ctl.sh's boot order and ingest-feed-ctl.sh's target
expansion flow through the library unchanged.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
LIB = REPO / "scripts" / "lib" / "serial-bringup.sh"
STACK_BOOT = REPO / "scripts" / "stack-boot-ctl.sh"
FEED_CTL = REPO / "scripts" / "ingest-feed-ctl.sh"

FAKE_SYSTEMCTL = r"""#!/bin/bash
# fake systemctl: --user <action> <unit.service>
action="$2"; unit="${3%.service}"
case "$action" in
  is-active)
    if grep -qx "$unit" "$FAKE_ACTIVE" 2>/dev/null; then echo active; exit 0; fi
    echo inactive; exit 3 ;;
  start|restart)
    echo "$unit" >> "$FAKE_ACTIVE"
    echo "$action $unit load=$(cut -d' ' -f1 "$SERIAL_BRINGUP_LOADAVG") tick=$(cat "$FAKE_TICK" 2>/dev/null || echo 0)" >> "$FAKE_CALLS" ;;
  show) echo "running" ;;
esac
exit 0
"""

FAKE_SLEEP = r"""#!/bin/bash
# fake sleep: advance the virtual clock one tick and replay the load schedule
# (one load1 value per line; the last line repeats forever).
n=$(cat "$FAKE_TICK" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_TICK"
total=$(wc -l < "$FAKE_SCHED"); [[ $n -gt $total ]] && n=$total
echo "$(sed -n "${n}p" "$FAKE_SCHED") 0.00 0.00 1/1 1" > "$SERIAL_BRINGUP_LOADAVG"
"""


@pytest.fixture
def harness(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("systemctl", FAKE_SYSTEMCTL), ("sleep", FAKE_SLEEP)):
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)
    loadavg = tmp_path / "loadavg"
    sched = tmp_path / "schedule"
    calls = tmp_path / "calls"
    active = tmp_path / "active"
    tick = tmp_path / "tick"
    thermal = tmp_path / "thermal"   # millidegrees, like thermal_zone0/temp
    calls.touch()
    active.touch()

    def run(args, schedule, initial_load="3.00", extra_env=None, script=None, temp_mc=50000):
        sched.write_text("\n".join(str(v) for v in schedule) + "\n")
        loadavg.write_text(f"{initial_load} 0.00 0.00 1/1 1\n")
        thermal.write_text(f"{temp_mc}\n")
        calls.write_text("")
        tick.write_text("0")
        env = dict(os.environ)
        env.update({
            "PATH": f"{bindir}:{env['PATH']}",
            "SERIAL_BRINGUP_LOADAVG": str(loadavg),
            "SERIAL_BRINGUP_THERMAL": str(thermal),
            "SERIAL_BRINGUP_TEMP_WARN_C": "79",
            "SERIAL_BRINGUP_LOAD_MAX": "12",
            "SERIAL_BRINGUP_MIN_WINDOW_S": "20",
            "SERIAL_BRINGUP_MAX_WAIT_S": "60",
            "SERIAL_BRINGUP_SAMPLE_S": "10",
            "FAKE_SCHED": str(sched), "FAKE_CALLS": str(calls),
            "FAKE_ACTIVE": str(active), "FAKE_TICK": str(tick),
        })
        env.update(extra_env or {})
        r = subprocess.run(
            ["bash", str(script or LIB)] + list(args),
            env=env, capture_output=True, text=True, timeout=60,
        )
        recorded = [line.split() for line in calls.read_text().splitlines()]
        # [(action, unit, load, tick), ...]
        parsed = [(a, u, float(l.split("=")[1]), int(t.split("=")[1])) for a, u, l, t in recorded]
        return r, parsed

    run.active = active
    return run


def test_next_unit_waits_for_load_below_threshold(harness):
    # unit a spikes to 20 for four ticks (window is 2 ticks), then 8.
    r, calls = harness(["restart", "a", "b"], schedule=[20, 20, 20, 20, 8, 5])
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c[:2] for c in calls] == [("restart", "a"), ("restart", "b")]
    # b started only once load was under 12 -- at tick 5, not at the end of
    # a's 2-tick minimum window.
    assert calls[1][2] < 12
    assert calls[1][3] == 5
    assert "holding before next unit" in r.stdout
    assert "WARNING" not in r.stdout


def test_thermal_monitor_is_observe_only(harness):
    # Operator decision 2026-09-06: the temp is logged at every sample and a
    # WARNING is raised at/above the tier-2 threshold, but the sequence is
    # never paused, aborted or reordered on temperature -- same units, same
    # ticks as a cool run.
    cool, cool_calls = harness(["restart", "a", "b"], schedule=[8, 5])
    hot, hot_calls = harness(["restart", "a", "b"], schedule=[8, 5], temp_mc=85000)
    assert hot.returncode == 0, hot.stdout + hot.stderr
    assert [c[:2] for c in hot_calls] == [("restart", "a"), ("restart", "b")]
    assert [c[3] for c in hot_calls] == [c[3] for c in cool_calls]
    assert "WARNING: temp 85C >= 79C" in hot.stdout and "observe only" in hot.stdout
    assert "TEMP-WARN (observed, not acted on)" in hot.stdout
    assert "temp 50C" in cool.stdout and "WARNING" not in cool.stdout
    # an unreadable sensor is not an error either
    gone, gone_calls = harness(["restart", "a"], schedule=[5],
                               extra_env={"SERIAL_BRINGUP_THERMAL": "/nonexistent/temp"})
    assert gone.returncode == 0 and len(gone_calls) == 1 and "temp -C" in gone.stdout


def test_hard_cap_releases_the_wait(harness):
    # load never recovers: every hold must end at the cap, never hang.
    r, calls = harness(["restart", "a", "b"], schedule=[20], initial_load="20.00")
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c[:2] for c in calls] == [("restart", "a"), ("restart", "b")]
    assert "after 60s cap" in r.stdout
    assert r.stdout.count("WARNING") >= 2   # a's post-window and b's pre-start hold
    # cap=60, step=10: pre-start hold 6 ticks + window 6 ticks per unit.
    assert calls[0][3] == 6
    assert calls[1][3] == 18
    assert "restart b" in r.stdout


def test_order_and_per_unit_windows_preserved(harness):
    r, calls = harness(["restart", "c:30", "a", "b:40"], schedule=[3])
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c[1] for c in calls] == ["c", "a", "b"]
    ticks = [c[3] for c in calls]
    # c: 30 s window = 3 ticks; a: default 20 s = 2 ticks; b last.
    assert ticks[1] - ticks[0] == 3
    assert ticks[2] - ticks[1] == 2
    assert ">>> c: baseline 3.00 -> peak 3 at +10s -> end 3  [active]" in r.stdout


def test_start_skips_already_active_unit(harness):
    harness.active.write_text("b\n")
    r, calls = harness(["start", "a", "b", "c"], schedule=[3])
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c[:2] for c in calls] == [("start", "a"), ("start", "c")]
    assert "b.service already active -- skip" in r.stdout
    # no window spent on b: c starts exactly one window after a.
    assert calls[1][3] - calls[0][3] == 2


def test_restart_never_skips_active_unit(harness):
    harness.active.write_text("a\n")
    r, calls = harness(["restart", "a"], schedule=[3])
    assert [c[:2] for c in calls] == [("restart", "a")]
    assert "skip" not in r.stdout


def test_cli_usage_rejects_bad_action(harness):
    r, calls = harness(["stop", "a"], schedule=[3])
    assert r.returncode == 2
    assert calls == []


def test_stack_boot_ctl_order_is_serial_and_load_gated(harness):
    # ntfy, pgsql, then the six SWIM feeds lightest-first, ingest-core,
    # poller, pusher, web, runner, then the rest -- one at a time.
    r, calls = harness(["start", "--stagger=10"], schedule=[3], script=STACK_BOOT)
    assert r.returncode == 0, r.stdout + r.stderr
    units = [c[1] for c in calls]
    assert units[:13] == [
        "ntfy", "corporatetraveldc-pgsql",
        "corporatetraveldc-ingest-notam", "corporatetraveldc-ingest-itws",
        "corporatetraveldc-ingest-tbfm", "corporatetraveldc-ingest-tfms",
        "corporatetraveldc-ingest-stdds", "corporatetraveldc-ingest-fdps",
        "corporatetraveldc-ingest-core",
        "corporatetraveldc-poller", "corporatetraveldc-pusher",
        "corporatetraveldc-web", "corporatetraveldc-runner",
    ]
    assert len(units) == len(set(units))
    # The four ADS-B feeders come right after ultrafeeder (their Wants=).
    uf = units.index("corporatetraveldc-ultrafeeder")
    assert set(units[uf + 1:uf + 5]) == {
        "corporatetraveldc-piaware", "corporatetraveldc-fr24feed",
        "corporatetraveldc-planefinder", "corporatetraveldc-airnavradar",
    }
    assert "amtrak-tracker" in units
    # 2026-09-06: a unit this sequence owns must NOT also carry its own
    # WantedBy=default.target, or default.target starts it flat at login
    # a minute before this sequence runs (the Sep 5 boot: all seven ingest
    # units up at 17:49:07, stack-boot-stagger began 17:50:32). Re-adding
    # WantedBy to any ORDER unit fails here.
    double = set(units) & _tracked_units_wanted_by_default_target()
    assert not double, sorted(double)


def _tracked_units_wanted_by_default_target():
    """Names of tracked quadlet .container units with an active (uncommented)
    WantedBy=default.target line -- the ones systemd starts flat at login."""
    out = set()
    for p in (REPO / ".config" / "containers" / "systemd").glob("*.container"):
        if any(line.strip() == "WantedBy=default.target" for line in p.read_text().splitlines()):
            out.add(p.stem)
    return out
    assert all(c[0] == "start" for c in calls)
    # per-unit windows from serialized-rollout.sh: poller 180 s = 18 ticks.
    poller = units.index("corporatetraveldc-poller")
    assert calls[poller + 1][3] - calls[poller][3] == 18
    # --stagger is the default minimum window (10 s = 1 tick here).
    pusher = units.index("corporatetraveldc-pusher")
    assert calls[pusher + 1][3] - calls[pusher][3] == 1


def test_stack_boot_ctl_holds_on_load(harness):
    r, calls = harness(["start", "--stagger=10"], schedule=[30, 30, 30, 5], script=STACK_BOOT)
    assert r.returncode == 0, r.stdout + r.stderr
    # second unit could not start until the load fell under 12 (tick 4).
    assert calls[1][3] == 4 and calls[1][2] < 12


def test_ingest_feed_ctl_restart_all_goes_through_library(harness):
    r, calls = harness(["restart", "all", "--stagger=20"], schedule=[3], script=FEED_CTL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c[1] for c in calls] == [
        "corporatetraveldc-ingest-core",
        "corporatetraveldc-ingest-notam", "corporatetraveldc-ingest-itws",
        "corporatetraveldc-ingest-tbfm", "corporatetraveldc-ingest-tfms",
        "corporatetraveldc-ingest-stdds", "corporatetraveldc-ingest-fdps",
    ]
    assert all(c[0] == "restart" for c in calls)
    ticks = [c[3] for c in calls]
    assert all(b - a == 2 for a, b in zip(ticks, ticks[1:]))
    assert "load gate < 12" in r.stdout


def test_ingest_feed_ctl_explicit_list_holds_on_load(harness):
    r, calls = harness(["restart", "tfms,stdds"], schedule=[25, 25, 25, 25, 25, 4], script=FEED_CTL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c[1] for c in calls] == ["corporatetraveldc-ingest-tfms", "corporatetraveldc-ingest-stdds"]
    assert calls[1][2] < 12 and calls[1][3] == 6


def test_ingest_feed_ctl_stop_is_unchanged(harness):
    # stop never goes through the library: one systemctl call, no ticks.
    r, calls = harness(["stop", "tfms,stdds"], schedule=[3], script=FEED_CTL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == []   # fake systemctl only logs start/restart
    assert "all at once" in r.stdout
