"""NET_FAILOVER_CHAIN (operator 2026-11-27): the watchdog chains the wired switch (end0), the WiFi (wld0)
and the hotspot (enu1). The default goes to the best tier that answers; failing down needs FAIL_STRIKES bad
probes on the tier in use, moving back up needs RESTORE_STRIKES good ones on the better tier. Stubbed
nmcli / ip / ping / curl keep a tiny fake NetworkManager (profile metrics, active profile per device) so the
route the stub reports follows the metrics the watchdog sets. Never touches networking."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/net-failover-watchdog.sh"
CHAIN = "Auto Ethernet@end0,jorransgateway 1@wld0,Hockey Puck@enu1"
METRICS = {"Auto Ethernet": 100, "jorransgateway 1": 600, "Hockey Puck": 1100}

NMCLI = r'''#!/bin/bash
echo "nmcli $*" >> "$CALLS"
case "$1 $2 $3" in
  "-g GENERAL.STATE device") [[ -f "$NM/$5.active" ]] && echo "100 (connected)" || echo "30 (disconnected)"; exit 0 ;;
  "-g GENERAL.CON-UUID device") [[ -f "$NM/$5.active" ]] && echo "u-$(cat "$NM/$5.active")"; exit 0 ;;
  "-g connection.uuid connection") echo "u-$5"; exit 0 ;;
  "-g ipv4.route-metric connection") cat "$NM/$5.metric"; exit 0 ;;
esac
if [[ "$1 $2" == "connection modify" ]]; then echo "$5" > "$NM/$3.metric"; exit 0; fi
if [[ "$1 $2" == "device reapply" ]]; then exit 0; fi
if [[ "$1 $2" == "connection up" ]]; then exit 4; fi
exit 0
'''

IP = r'''#!/bin/bash
[[ "$1" == "-6" && "$2" == "addr" ]] && exit 0
best=""; bm=999999
for f in "$NM"/*.active; do
  i=$(basename "$f" .active); c=$(cat "$f"); m=$(cat "$NM/$c.metric")
  (( m < bm )) && { bm=$m; best=$i; }
done
echo "1.1.1.1 via 192.0.2.1 dev $best src 192.0.2.2"
'''

PING = r'''#!/bin/bash
for a in "$@"; do for u in $UP; do [[ "$a" == "$u" ]] && exit 0; done; done
exit 1
'''

CURL = r'''#!/bin/bash
for a in "$@"; do [[ "$a" == "--interface" ]] && exit 7; done
echo "$@" >> "$SENT"; exit 0
'''


def run(tmp_path, *, up, state="", metrics=None, active=None, chain=CHAIN):
    b = tmp_path / "bin"; b.mkdir()
    nm = tmp_path / "nm"; nm.mkdir()
    sysnet = tmp_path / "net"
    for name, body in (("nmcli", NMCLI), ("ip", IP), ("ping", PING), ("curl", CURL),
                       ("sleep", "#!/bin/sh\nexit 0\n")):
        (b / name).write_text(body); (b / name).chmod(0o755)
    for con, m in (metrics or METRICS).items():
        (nm / f"{con}.metric").write_text(f"{m}\n")
    for dev, con in (active or {"end0": "Auto Ethernet", "wld0": "jorransgateway 1", "enu1": "Hockey Puck"}).items():
        (nm / f"{dev}.active").write_text(con)
        (sysnet / dev).mkdir(parents=True); (sysnet / dev / "carrier").write_text("1")
    st = tmp_path / "wd-state"; st.write_text(state + "last_alert_epoch=0\n")
    (tmp_path / "run").mkdir()
    calls, sent = tmp_path / "calls", tmp_path / "sent"
    calls.write_text(""); sent.write_text("")
    env = dict(os.environ, PATH=f"{b}{os.pathsep}{os.environ['PATH']}", NM=str(nm), CALLS=str(calls), SENT=str(sent),
               UP=" ".join(up), XDG_RUNTIME_DIR=str(tmp_path / "run"), NET_FAILOVER_STATE_FILE=str(st),
               NET_FAILOVER_LOG_FILE=str(tmp_path / "log"), NET_FAILOVER_SYS_NET_DIR=str(sysnet),
               NET_FAILOVER_CHAIN=chain, NET_FAILOVER_PARKED="false", NET_METERED_IFACES="enu1",
               NET_EXITNODE_CAP_FILE=str(tmp_path / "cap.nft"))
    p = subprocess.run(["bash", str(SCRIPT)], text=True, capture_output=True, env=env, timeout=60)
    metric = {c: int((nm / f"{c}.metric").read_text()) for c in (metrics or METRICS)}
    kv = dict(l.split("=", 1) for l in st.read_text().splitlines() if "=" in l)
    return p, metric, kv, calls.read_text(), sent.read_text()


def test_all_tiers_healthy_changes_nothing(tmp_path):
    p, metric, kv, calls, sent = run(tmp_path, up=["end0", "wld0", "enu1"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric == METRICS and kv["boosted"] == "-1"
    assert "connection modify" not in calls and sent == ""


def test_one_bad_probe_on_the_wired_primary_does_not_move(tmp_path):
    p, metric, kv, calls, _ = run(tmp_path, up=["wld0", "enu1"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric == METRICS and kv["fail_streak_0"] == "1" and "connection modify" not in calls


def test_dead_wired_primary_fails_over_to_the_wifi_not_the_hotspot(tmp_path):
    p, metric, kv, _, sent = run(tmp_path, up=["wld0", "enu1"], state="boosted=-1\nfail_streak_0=2\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric["jorransgateway 1"] == 50 and metric["Hockey Puck"] == 1100
    assert kv["boosted"] == "1" and kv["saved_metric_1"] == "600"
    assert "failed over to wld0" in sent and "CAPPED" not in sent and "UNCAPPED" not in sent


def test_wifi_dying_too_moves_on_to_the_hotspot_and_puts_the_wifi_back(tmp_path):
    p, metric, kv, _, sent = run(tmp_path, up=["enu1"], metrics=dict(METRICS, **{"jorransgateway 1": 50}),
                                 state="boosted=1\nsaved_metric_1=600\nfail_streak_0=9\nfail_streak_1=2\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric == dict(METRICS, **{"Hockey Puck": 40})                 # 10 under the WiFi's 50: no ECMP tie
    assert kv["boosted"] == "2" and kv["saved_metric_2"] == "1100"
    assert "failed over to enu1" in sent and "UNCAPPED" in sent          # the metered tier names the exit-node cap


def test_wifi_recovering_while_on_the_hotspot_moves_back_up_to_the_wifi(tmp_path):
    p, metric, kv, _, sent = run(tmp_path, up=["wld0", "enu1"], metrics=dict(METRICS, **{"Hockey Puck": 50}),
                                 state="boosted=2\nsaved_metric_2=1100\nfail_streak_0=9\nok_streak_1=2\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric == dict(METRICS, **{"jorransgateway 1": 40}) and kv["boosted"] == "1"
    assert "back up to wld0" in sent


def test_wired_recovery_restores_every_profile_metric(tmp_path):
    p, metric, kv, _, sent = run(tmp_path, up=["end0", "wld0", "enu1"], metrics=dict(METRICS, **{"Hockey Puck": 50}),
                                 state="boosted=2\nsaved_metric_2=1100\nok_streak_0=2\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric == METRICS and kv["boosted"] == "-1"
    assert "restored to end0" in sent


def test_recovery_waits_for_restore_strikes(tmp_path):
    p, metric, kv, calls, _ = run(tmp_path, up=["end0", "wld0", "enu1"], metrics=dict(METRICS, **{"jorransgateway 1": 50}),
                                  state="boosted=1\nsaved_metric_1=600\nok_streak_0=1\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric["jorransgateway 1"] == 50 and kv["boosted"] == "1" and "connection modify" not in calls


def test_every_tier_down_alerts_and_moves_nothing(tmp_path):
    p, metric, kv, calls, sent = run(tmp_path, up=[], state="boosted=-1\nfail_streak_0=5\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric == METRICS and "connection modify" not in calls and "ALL uplinks down" in sent


def test_wifi_on_the_hotspots_own_profile_is_skipped(tmp_path):
    # wld0 is up, but on "Corporate Traveler WiFi": moving "jorransgateway 1" would change nothing live.
    m = dict(METRICS, **{"Corporate Traveler WiFi": 1600})
    p, metric, kv, _, _ = run(tmp_path, up=["wld0", "enu1"], metrics=m, state="boosted=-1\nfail_streak_0=2\n",
                              active={"end0": "Auto Ethernet", "wld0": "Corporate Traveler WiFi", "enu1": "Hockey Puck"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric["Hockey Puck"] == 50 and metric["jorransgateway 1"] == 600 and kv["boosted"] == "2"


def test_two_tier_state_is_carried_over(tmp_path):
    # the old watchdog left failed_over=1 on the hotspot: that tier counts as in use and gets its metric back
    p, metric, kv, _, _ = run(tmp_path, up=["end0", "wld0", "enu1"], metrics=dict(METRICS, **{"Hockey Puck": 50}),
                              state="failed_over=1\nsaved_backup_metric=1100\nok_streak_0=2\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert metric == METRICS and kv["boosted"] == "-1" and kv["failed_over"] == "0"


def test_a_malformed_chain_refuses_to_run(tmp_path):
    p, metric, _, calls, _ = run(tmp_path, up=["end0"], chain="Auto Ethernet,jorransgateway 1@wld0")
    assert p.returncode == 2 and "connection@device" in p.stderr and "connection modify" not in calls


def test_default_chain_is_the_operator_order():
    text = SCRIPT.read_text()
    assert f'CHAIN_DEFAULT="{CHAIN}"' in text
