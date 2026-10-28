#!/usr/bin/env python3
"""scripts/agent-status-snapshot.py -- what agents may see of the operator's box (2026-10-26).

Agent accounts cannot see the operator's user units, rootless containers or
journal (by design: the journal carries other accounts' argv and unit output,
`podman inspect` carries environments). This runs AS THE OPERATOR from
corporatetraveldc-agent-status-snapshot.timer every 5 minutes and publishes a
scrubbed summary for the ctdc-dev group (agents and humans):

    /srv/ctdc-handoff/status/status.json   (corporatetraveldc:ctdc-dev 0640, atomic replace)

Published: host load/memory/disk/temperature; every corporatetraveldc-* user
service and timer (state, result, restart count, next/last run); every
container (name, image, state, status, start time, exit code, restarts);
journal ERROR-or-worse COUNTS per user unit over the last 60 minutes, and the
names of failed system units. Never published: command lines, ExecStart,
environments, labels, mounts, ports, journal message text. An unavailable
source is recorded by name only.

    agent-status-snapshot.py [--out PATH] [--stdout]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone

OUT = "/srv/ctdc-handoff/status/status.json"
PREFIX = "corporatetraveldc-"
SAFE_UNIT_PROPS = ("Result", "NRestarts", "ExecMainStatus", "ActiveEnterTimestamp")
CONTAINER_FIELDS = {"State": "state", "Status": "status", "ExitCode": "exit_code", "Restarts": "restarts"}
DESC_MAX = 160


def _run(cmd: list[str], timeout: int = 30) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout


def _utc(ts: float | int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def host() -> dict:
    out: dict = {"hostname": socket.gethostname()}
    out["loadavg"] = list(os.getloadavg())
    with open("/proc/uptime") as f:
        out["uptime_s"] = int(float(f.read().split()[0]))
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                mem[k] = int(v.split()[0]) // 1024
    out["mem_mib"] = mem
    disks, seen = {}, set()
    for p in ("/", "/home", "/var", "/opt"):
        try:
            dev = os.stat(p).st_dev
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        u = shutil.disk_usage(p)
        disks[p] = {"used_pct": round(100 * u.used / u.total, 1), "free_gib": round(u.free / 2**30, 1)}
    out["disk"] = disks
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            out["cpu_temp_c"] = round(int(f.read().strip()) / 1000, 1)
    except OSError:
        pass
    return out


def units() -> list[dict]:
    rows = json.loads(_run(["systemctl", "--user", "list-units", "--all", "--type=service",
                            "--output=json", PREFIX + "*"]) or "[]")
    out = []
    for r in rows:
        u = {"unit": r["unit"], "load": r.get("load"), "active": r.get("active"), "sub": r.get("sub"),
             "description": (r.get("description") or "")[:DESC_MAX]}
        if r.get("active") in ("failed", "activating") or r.get("sub") in ("auto-restart",):
            try:
                props = _run(["systemctl", "--user", "show", r["unit"], "-p", ",".join(SAFE_UNIT_PROPS)])
                for line in props.splitlines():
                    k, _, v = line.partition("=")
                    if k in SAFE_UNIT_PROPS:
                        u[k] = v
            except (subprocess.SubprocessError, OSError):
                u["detail"] = "unavailable"
        out.append(u)
    return out


def timers() -> list[dict]:
    rows = json.loads(_run(["systemctl", "--user", "list-timers", "--all", "--output=json", PREFIX + "*"]) or "[]")
    return [{"unit": r["unit"], "activates": r.get("activates"),
             "next_utc": _utc((r.get("next") or 0) / 1e6), "last_utc": _utc((r.get("last") or 0) / 1e6)}
            for r in rows]


def containers() -> list[dict]:
    rows = json.loads(_run(["podman", "ps", "-a", "--format", "json"]) or "[]")
    out = []
    for r in rows:
        if r.get("IsInfra"):
            continue
        names = r.get("Names") or []
        c = {"name": names[0] if names else None, "image": r.get("Image"),
             "started_utc": _utc(r.get("StartedAt"))}
        for k, v in CONTAINER_FIELDS.items():
            c[v] = r.get(k)
        out.append(c)
    return sorted(out, key=lambda c: c["name"] or "")


def journal_error_counts() -> dict:
    raw = _run(["journalctl", "--user", "--since", "-60min", "-p", "3", "-o", "json",
                "--output-fields=_SYSTEMD_USER_UNIT,PRIORITY"], timeout=60)
    counts: Counter = Counter()
    for line in raw.splitlines():
        try:
            counts[json.loads(line).get("_SYSTEMD_USER_UNIT") or "(no unit)"] += 1
        except ValueError:
            continue
    return dict(counts.most_common())


def failed_system_units() -> list[str]:
    rows = json.loads(_run(["systemctl", "list-units", "--failed", "--output=json"]) or "[]")
    return sorted(r["unit"] for r in rows)


COLLECTORS = {
    "host": host,
    "units": units,
    "timers": timers,
    "containers": containers,
    "journal_errors_60m": journal_error_counts,
    "failed_system_units": failed_system_units,
}


def snapshot() -> dict:
    snap: dict = {"schema": 1, "generated_utc": _utc(time.time()), "unavailable": []}
    for key, fn in COLLECTORS.items():
        try:
            snap[key] = fn()
        except Exception:  # noqa: BLE001 -- record the source by name only, never its error text
            snap["unavailable"].append(key)
    return snap


def write_atomic(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        raise SystemExit(f"{d} is missing (scripts/agent-segmentation/srv-layout.sh creates it: corporatetraveldc:ctdc-dev 2750)")
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".status.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
            os.fchmod(f.fileno(), 0o640)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.environ.get("CTDC_STATUS_OUT", OUT))
    ap.add_argument("--stdout", action="store_true", help="print instead of writing")
    a = ap.parse_args(argv)
    snap = snapshot()
    if a.stdout:
        json.dump(snap, sys.stdout, indent=1, sort_keys=True)
        print()
    else:
        write_atomic(a.out, snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
