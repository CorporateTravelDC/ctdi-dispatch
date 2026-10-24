"""tests/scripts/test_metered_uplink_policy.py -- 2026-10-23 (operator): the failover hotspot (Google Fi,
enu1) is metered and emergency-only. On it: SWIM + live ops data, plus the Proton backup upload capped
at 2 Mbps; every other heavy job defers. The failover watchdog fails back even when the hotspot device
is gone (it stayed stuck failed-over from 2026-10-15)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts/lib/heavy_lock.sh"
UPLINK = REPO / "scripts/lib/uplink.sh"
WATCHDOG = REPO / "scripts/net-failover-watchdog.sh"
spec = importlib.util.spec_from_file_location("rate_guard", REPO / "scripts/backup/rate_guard.py")
rg = importlib.util.module_from_spec(spec); spec.loader.exec_module(rg)
MB = 1_000_000
METERED = {"ifaces": {"enu1"}, "soft": 0.22, "hard": 0.25}


def env(tmp_path, dev):
    e = dict(os.environ, HEAVY_LOCK_DIR=str(tmp_path / "lk"), UPLINK_DEFAULT_IFACE=dev, NET_METERED_IFACES="enu1")
    e.pop("CTDC_HEAVY_LOCK_HELD", None)
    return e


def sh(script, e):
    return subprocess.run(["bash", "-c", f"set -euo pipefail; source {LIB}; {script}"], env=e,
                          capture_output=True, text=True, timeout=30)


# --- uplink detection ------------------------------------------------------------------------------

def test_metered_only_on_the_listed_iface(tmp_path):
    for dev, want in (("enu1", "M"), ("wld0", "U"), ("", "U")):
        r = subprocess.run(["bash", "-c", f"source {UPLINK}; uplink_is_metered && echo M || echo U"],
                           env=env(tmp_path, dev), capture_output=True, text=True)
        assert r.stdout.strip() == want, dev


# --- heavy jobs ------------------------------------------------------------------------------------

def test_rollout_defers_on_the_hotspot(tmp_path):
    r = sh("heavy_lock_acquire 'full-load rollout' 1 && echo GOT || echo DEFERRED", env(tmp_path, "enu1"))
    assert "DEFERRED" in r.stdout and "deferred: metered uplink" in r.stderr


def test_stack_refresh_defers_on_the_hotspot(tmp_path):
    r = sh("heavy_lock_acquire 'stack refresh' 1 && echo GOT || echo DEFERRED", env(tmp_path, "enu1"))
    assert "DEFERRED" in r.stdout


def test_backup_and_restore_test_run_on_the_hotspot(tmp_path):
    e = env(tmp_path, "enu1")
    assert "GOT" in sh("heavy_lock_acquire 'nightly backup' 1 && echo GOT", e).stdout
    assert "GOT" in sh("heavy_lock_acquire 'restore test' 1 && echo GOT", e).stdout


def test_rollout_runs_on_the_primary(tmp_path):
    assert "GOT" in sh("heavy_lock_acquire 'full-load rollout' 1 && echo GOT", env(tmp_path, "wld0")).stdout


# --- backup upload caps ----------------------------------------------------------------------------

def drive(rates_mb, routes, soft=2.5, hard=3.0):
    """rates_mb / routes: per-interval upload rate and default device. rclone obeys the latest limit."""
    st = {"b": 0, "i": 0, "limit": None, "alive": True, "set": [], "stopped": False}

    def sleeper(_):
        r = rates_mb[min(st["i"], len(rates_mb) - 1)]
        if st["limit"] is not None:
            r = min(r, st["limit"] * 1.1)
        st["b"] += int(r * MB * 10); st["i"] += 1
        if st["i"] >= len(rates_mb):
            st["alive"] = False

    def setter(mb):
        st["limit"] = mb; st["set"].append(mb); return True

    def stopper():
        st["stopped"] = True; st["alive"] = False

    res = rg.guard(1, "/x.sock", "wld0", soft, hard, interval=10, window=3, counter=lambda: st["b"],
                   setter=setter, stopper=stopper, sleeper=sleeper, is_alive=lambda: st["alive"],
                   metered=METERED, route=lambda: routes[min(st["i"], len(routes) - 1)])
    return res, st


def test_failover_mid_sync_drops_to_2_mbps():
    res, st = drive([2.6] * 4 + [2.6] * 8, ["wld0"] * 4 + ["enu1"] * 8)
    up = [a for a in res["actions"] if a["action"] == "uplink"]
    assert up and up[0]["iface"] == "enu1" and up[0]["metered"] and up[0]["soft_mb_s"] == 0.22
    assert st["set"][0] == 0.22 and not res["stopped"]


def test_failback_mid_sync_restores_primary_caps():
    res, st = drive([0.24] * 12, ["enu1"] * 4 + ["wld0"] * 8)
    up = [a for a in res["actions"] if a["action"] == "uplink"]
    assert up and up[0]["iface"] == "wld0" and not up[0]["metered"] and up[0]["soft_mb_s"] == 2.5


def test_starting_on_the_hotspot_uses_the_metered_hard_cap():
    # 0.3 MB/s on the hotspot is over the 0.25 hard cap -> lowered, never judged against the primary's 3
    res, _ = drive([0.3] * 6, ["enu1"] * 6)
    assert res["actions"] and res["actions"][0]["action"] == "lowered"


def test_without_metered_config_nothing_changes():
    res = rg.guard(1, "/x.sock", "wld0", 2.5, 3.0, interval=10, window=3, counter=iter(range(0, 10**9, 26 * MB)).__next__,
                   setter=lambda mb: True, stopper=lambda: None, sleeper=lambda _: None,
                   is_alive=iter([True] * 6 + [False]).__next__)
    assert res["actions"] == []


# --- watchdog fail-back ----------------------------------------------------------------------------

def stub(bin_dir: Path, name: str, body: str):
    p = bin_dir / name; p.write_text("#!/bin/bash\n" + body + "\n"); p.chmod(0o755)


def test_failback_with_the_hotspot_gone_clears_failed_over(tmp_path):
    b = tmp_path / "bin"; b.mkdir()
    calls = tmp_path / "nmcli.calls"
    stub(b, "ping", 'for a in "$@"; do [[ "$a" == "wld0" ]] && exit 0; done; exit 1')
    stub(b, "curl", "exit 1")
    stub(b, "nmcli", f'echo "$*" >> {calls}; [[ "$1 $2" == "connection up" ]] && exit 4; echo 600; exit 0')
    state = tmp_path / "wd-state"
    state.write_text("failed_over=1\nsaved_backup_metric=600\nok_streak=5\nlast_alert_epoch=0\n")
    e = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", NET_FAILOVER_STATE_FILE=str(state),
             NET_FAILOVER_LOG_FILE=str(tmp_path / "watch.log"), NET_FAILOVER_BACKUP_IF="nosuchdev0",
             NET_FAILOVER_PRIMARY_IF="wld0", CTDC_CONFIG_FILE="/nonexistent")
    r = subprocess.run(["bash", str(WATCHDOG)], env=e, capture_output=True, text=True, timeout=60)
    assert "failed_over=0" in state.read_text(), r.stdout + r.stderr
    made = calls.read_text()
    assert "ipv4.route-metric 600" in made and "connection up" not in made


# --- failover page carries the exit-node cap state (2026-10-24) ------------------------------------

def _failover_run(tmp_path, cap_present):
    b = tmp_path / "bin"; b.mkdir()
    sent = tmp_path / "sent.txt"
    stub(b, "ping", 'for a in "$@"; do [[ "$a" == "enu1" ]] && exit 0; done; exit 1')
    # probes (--interface) fail so only the backup's ping answers; the ntfy POST is recorded
    stub(b, "curl", f'for a in "$@"; do [[ "$a" == "--interface" ]] && exit 7; done; echo "$@" >> {sent}; exit 0')
    stub(b, "nmcli", 'echo 600; exit 0')
    stub(b, "ip", 'echo "default via 192.0.2.1 dev enu1 metric 50"')
    state = tmp_path / "wd-state"
    state.write_text("failed_over=0\nfail_streak=5\nok_streak=0\nlast_alert_epoch=0\n")
    cap = tmp_path / "ctdc-exitnode.nft"
    if cap_present:
        cap.write_text("table inet ctdc_exitnode {}\n")
    e = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", NET_FAILOVER_STATE_FILE=str(state),
             NET_FAILOVER_LOG_FILE=str(tmp_path / "watch.log"), NET_FAILOVER_BACKUP_IF="enu1",
             NET_FAILOVER_PRIMARY_IF="wld0", NET_EXITNODE_CAP_FILE=str(cap), CTDC_CONFIG_FILE="/nonexistent")
    subprocess.run(["bash", str(WATCHDOG)], env=e, capture_output=True, text=True, timeout=60)
    return sent.read_text() if sent.exists() else ""


def test_failover_page_says_exit_node_is_capped(tmp_path):
    body = _failover_run(tmp_path, cap_present=True)
    assert "failed over to enu1" in body and "CAPPED at 2 Mbps" in body


def test_failover_page_warns_when_cap_missing(tmp_path):
    body = _failover_run(tmp_path, cap_present=False)
    assert "failed over to enu1" in body and "UNCAPPED" in body
