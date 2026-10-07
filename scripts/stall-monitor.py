#!/usr/bin/env python3
"""scripts/stall-monitor.py -- measure scheduling stalls for watchdog tuning.

Runs as root (corporatetraveldc-stall-monitor.service, installed copy). Every
second it sleeps 1s and measures how late it woke (CLOCK_MONOTONIC). A late
wake is a stall the box spent unable to schedule a normal-priority process --
the same condition that keeps PID 1 from petting the hardware watchdog.
Records gaps >= 1.5s; keeps the worst ever and the top 20 with timestamps and
load at the time, in /var/lib/corporatetraveldc/reports/stall-monitor.json
(written on every new record and every 5 minutes; survives a reset, so a
stall that ends in a reset shows up as the last heartbeat before the gap
across boots -- recorded on start as 'boot_gap').
"""
import json, os, time

OUT = "/var/lib/corporatetraveldc/reports/stall-monitor.json"
THRESH = 1.5


def load():
    try:
        with open(OUT) as fh:
            return json.load(fh)
    except Exception:
        return {"since": time.time(), "max_gap_s": 0.0, "top": [], "boot_gaps": []}


def save(st):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)


def note(st, gap, kind):
    entry = {"at": time.time(), "gap_s": round(gap, 3), "kind": kind, "load1": os.getloadavg()[0]}
    st["top"] = sorted(st["top"] + [entry], key=lambda e: -e["gap_s"])[:20]
    if kind == "stall" and gap > st["max_gap_s"]:
        st["max_gap_s"] = round(gap, 3)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    st = load()
    hb = st.get("heartbeat")
    if hb:
        # wall-clock gap between the last heartbeat and now = downtime across a
        # reset; kept separately (it is not a stall the watchdog could measure)
        st.setdefault("boot_gaps", []).append({"last_heartbeat": hb, "boot_at": time.time()})
        st["boot_gaps"] = st["boot_gaps"][-20:]
    last_save = 0.0
    while True:
        t0 = time.monotonic()
        time.sleep(1.0)
        gap = time.monotonic() - t0  # wall time a 1s sleep actually took
        if gap >= THRESH:
            note(st, gap, "stall")
            save(st)
        now = time.time()
        if now - last_save >= 300:
            st["heartbeat"] = now
            save(st)
            last_save = now


if __name__ == "__main__":
    main()
