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
import re
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
SAFE_UNIT_PROPS = ("Result", "NRestarts", "ExecMainStatus", "ActiveEnterTimestamp",
                   "Type", "ConditionResult", "UnitFileState", "ExecMainCode",
                   "ExecMainStartTimestamp", "ExecMainExitTimestamp",
                   "ExecMainStartTimestampMonotonic", "InvocationID",
                   "MemoryPeak", "MemoryCurrent", "MemoryHigh", "MemoryMax", "MemorySwapMax", "MemorySwapCurrent")
SAFE_UNIT_NAME = re.compile(r"corporatetraveldc-[a-zA-Z0-9_.@:-]+\.(?:service|timer|path)")
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
    rows = [r for r in rows if SAFE_UNIT_NAME.fullmatch(r.get("unit", ""))]
    # One bounded show call covers completed and condition-skipped oneshots
    # too. Trigger times and an inactive state cannot establish execution.
    details = {}
    if rows:
        try:
            text = _run(["systemctl", "--user", "show", *(r["unit"] for r in rows),
                         "-p", ",".join(("Id", *SAFE_UNIT_PROPS))])
            for block in text.strip().split("\n\n"):
                props = {}
                for line in block.splitlines():
                    key, sep, value = line.partition("=")
                    if sep and key in ("Id", *SAFE_UNIT_PROPS):
                        props[key] = value
                name = props.pop("Id", None)
                if name:
                    details[name] = props
        except (subprocess.SubprocessError, OSError):
            pass
    out = []
    for r in rows:
        u = {"unit": r["unit"], "load": r.get("load"), "active": r.get("active"), "sub": r.get("sub"),
             "description": (r.get("description") or "")[:DESC_MAX]}
        if r["unit"] in details:
            u.update(details[r["unit"]])
        else:
            u["detail"] = "unavailable"
        out.append(u)
    return out


def unit_files() -> list[dict]:
    """Expose timer/path masks and disabled entries even when never loaded."""
    rows = json.loads(_run(["systemctl", "--user", "list-unit-files", "--output=json", PREFIX + "*"]) or "[]")
    return [{"unit": r["unit_file"], "state": r.get("state"), "preset": r.get("preset")}
            for r in rows if SAFE_UNIT_NAME.fullmatch(r.get("unit_file", ""))]


def paths() -> list[dict]:
    rows = json.loads(_run(["systemctl", "--user", "list-units", "--all", "--type=path",
                            "--output=json", PREFIX + "*"]) or "[]")
    return [{k: r.get(k) for k in ("unit", "load", "active", "sub")}
            for r in rows if SAFE_UNIT_NAME.fullmatch(r.get("unit", ""))]


def timers() -> list[dict]:
    rows = json.loads(_run(["systemctl", "--user", "list-timers", "--all", "--output=json", PREFIX + "*"]) or "[]")
    out = {r["unit"]: {"unit": r["unit"], "activates": r.get("activates"),
                      "next_utc": _utc((r.get("next") or 0) / 1e6),
                      "last_utc": _utc((r.get("last") or 0) / 1e6)}
           for r in rows if SAFE_UNIT_NAME.fullmatch(r.get("unit", ""))}
    states = json.loads(_run(["systemctl", "--user", "list-units", "--all", "--type=timer",
                              "--output=json", PREFIX + "*"]) or "[]")
    for row in states:
        if SAFE_UNIT_NAME.fullmatch(row.get("unit", "")):
            entry = out.setdefault(row["unit"], {"unit": row["unit"]})
            entry.update({k: row.get(k) for k in ("load", "active", "sub")})
    return list(out.values())


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


def heavy_jobs() -> dict:
    # Publish aggregate pool state only; never copy the private state,
    # receipts, errors, paths or arbitrary keys into the shared snapshot.
    with open('/var/lib/corporatetraveldc/heavy-job-policy/state.json') as f:
        raw=f.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('bounded state exceeded')
    state=json.loads(raw)
    active=[]
    for unit,lease in state.get('leases',{}).items():
        if not SAFE_UNIT_NAME.fullmatch(unit):continue
        values={key:lease[key] for key in ('budget','reserve')
                if isinstance(lease.get(key),int) and 0<=lease[key]<=4*1024**3}
        active.append({'unit':unit,'swap_allowance_bytes':values.get('budget'),
                       'reserved_swap_bytes':values.get('reserve'),'controller_fault':lease.get('fault') is True})
    queue=sorted(u for u in state.get('queue',{}) if SAFE_UNIT_NAME.fullmatch(u))
    return {'max_parallel':2,'active':sorted(active,key=lambda row:row['unit']),
            'queued':queue,'host_swap_over_budget':state.get('host_swap_over_budget') is True}


COLLECTORS = {
    "host": host,
    "units": units,
    "timers": timers,
    "unit_files": unit_files,
    "paths": paths,
    "containers": containers,
    "journal_errors_60m": journal_error_counts,
    "failed_system_units": failed_system_units,
    "heavy_jobs": heavy_jobs,
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
