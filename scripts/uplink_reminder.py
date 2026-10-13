#!/usr/bin/env python3
"""scripts/uplink_reminder.py -- weekly nudge while the failover uplink is down (2026-10-13).

Operator: the Verizon-backed failover (enu1) has been down since 2026-10-06 (pricing); a prepaid SIM
replaces it before winter. Instead of carrying it in every backlog list: a daily quiet check, a nudge
once a week while it is down, and ONE nudge when it has been reachable for STABLE_DAYS consecutive
daily checks ("back and stable -- run the failover validation relay"). Then silence until it drops again.

"Reachable" means real internet through the interface (an IPv4 HTTPS probe bound to it), not link
state: the tethered phone reports "connected" with no service. State: ~/.local/state/ctdc-uplink/
  uplink_reminder.py [--iface enu1] [--weekday 0] [--stable-days 3]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(os.environ.get("UPLINK_REMINDER_STATE", Path.home() / ".local/state/ctdc-uplink/state.json"))
REPO = Path(__file__).resolve().parents[1]


def reachable(iface: str) -> bool:
    if os.environ.get("UPLINK_REMINDER_FAKE") in ("up", "down"):
        return os.environ["UPLINK_REMINDER_FAKE"] == "up"
    r = subprocess.run(["curl", "-4", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "15",
                        "--interface", iface, "https://www.gstatic.com/generate_204"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "204"


def nudge(title: str, msg: str, priority: int) -> None:
    if os.environ.get("UPLINK_REMINDER_DRY"):
        print(f"NUDGE[{priority}] {title} | {msg}")
        return
    subprocess.run([sys.executable, str(REPO / "scripts/relay-nudge.py"), title, msg, "--priority", str(priority)],
                   capture_output=True, timeout=60)


def step(state: dict, up: bool, now: datetime, iface: str, weekday: int, stable_days: int) -> dict:
    today = now.strftime("%Y-%m-%d")
    if state.get("last_check") == today:
        return state                                   # one decision per day
    state["last_check"] = today
    if up:
        state["up_days"] = state.get("up_days", 0) + 1
        if state["up_days"] >= stable_days and not state.get("announced_stable"):
            nudge("Failover uplink back and stable", f"{iface} has reached the internet on {state['up_days']} consecutive daily "
                  "checks. Next: run the failover validation relay (failover test, IPv6 off, no bulk sync on it).", 3)
            state["announced_stable"] = True
    else:
        if state.get("up_days") or state.get("announced_stable") or "down_since" not in state:
            state["down_since"] = today
        state["up_days"] = 0
        state["announced_stable"] = False
        if now.weekday() == weekday:
            nudge("Failover uplink still down", f"{iface} has had no internet since {state['down_since']}. "
                  "Reminder: prepaid SIM before winter. This nudge repeats weekly until the link is back and stable.", 2)
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="enu1")
    ap.add_argument("--weekday", type=int, default=0, help="0 = Monday")
    ap.add_argument("--stable-days", type=int, default=3)
    ap.add_argument("--down-since", default="2026-10-06")
    a = ap.parse_args(argv)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"down_since": a.down_since}
    up = reachable(a.iface)
    state = step(state, up, datetime.now(timezone.utc), a.iface, a.weekday, a.stable_days)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, sort_keys=True))
    print(f"uplink {a.iface}: {'reachable' if up else 'no internet'} (state {json.dumps(state, sort_keys=True)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
