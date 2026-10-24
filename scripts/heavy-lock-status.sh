#!/usr/bin/env bash
# scripts/heavy-lock-status.sh -- is the heavy-work turn free right now, and will a timer-driven
# heavy job want it soon?  Read-only: it NEVER takes the lock (no flock call at all).
#
# 2026-10-23 (operator): on 2026-10-22 a "backup done" ping told the operator to run a relay, but
# the mid-week stack-refresh tripwire was queued on the same heavy lock (scripts/lib/heavy_lock.sh)
# and grabbed it first, so the relay refused. Run this before posting / starting a relay.
#
#   heavy-lock-status.sh [MINUTES]     window for "due soon" (default 30)
#   exit 0 clear; 1 lock held or a heavy timer fires within MINUTES
#
# Lock held? -- /proc/locks is the truth (the holder file is never cleared on release, so it only
#   names the LAST taker). A FLOCK entry on the lock file's dev:inode = held; the holder unit is
#   read from that pid's cgroup (unit name only -- never the command line: no raw process listings).
# Due soon? -- `systemctl --user list-timers --all -o json`, heavy timers only:
#   backup, backup-restore-test, stack-refresh-tripwire (hourly; every fire takes the turn while it
#   decides whether to refresh -- that is what beat the relay on 10-22), weekly-external-image-update
#   and any *serialized-rollout* timer. backup-freshness etc. are light and ignored.
# Times are printed in America/New_York.
#   HEAVY_LOCK_DIR          as in heavy_lock.sh (default ~/.local/state/ctdc-heavy)
#   HEAVY_LOCK_PROC_LOCKS   test hook (default /proc/locks)
#   HEAVY_TIMER_RE          override the heavy-timer pattern (python regex on the unit name)
set -uo pipefail
MIN="${1:-30}"
[[ "$MIN" =~ ^[0-9]+$ ]] || { echo "usage: heavy-lock-status.sh [MINUTES]" >&2; exit 2; }
HEAVY_LOCK_DIR="${HEAVY_LOCK_DIR:-${HOME}/.local/state/ctdc-heavy}"
TIMERS_JSON="$(systemctl --user list-timers --all --no-pager -o json 2>/dev/null </dev/null || echo '[]')"

TIMERS_JSON="$TIMERS_JSON" MIN="$MIN" HEAVY_LOCK_DIR="$HEAVY_LOCK_DIR" python3 - <<'PY'
import json, os, re, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
lock_dir = os.environ["HEAVY_LOCK_DIR"]
lock = os.path.join(lock_dir, "lock")
proc_locks = os.environ.get("HEAVY_LOCK_PROC_LOCKS", "/proc/locks")
window_s = int(os.environ["MIN"]) * 60
heavy_re = re.compile(os.environ.get("HEAVY_TIMER_RE") or
                      r"^corporatetraveldc-(backup|backup-restore-test|stack-refresh-tripwire"
                      r"|weekly-external-image-update|[A-Za-z0-9_-]*serialized-rollout[A-Za-z0-9_-]*)\.timer$")
NAME_UNIT = {"nightly backup": "corporatetraveldc-backup.service",
             "restore test": "corporatetraveldc-backup-restore-test.service",
             "stack refresh": "stack-refresh (tripwire or weekly external update)",
             "full-load rollout": "serialized-rollout"}
busy = False


def et(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%a %Y-%m-%d %H:%M %Z")


def holder_pids() -> list[int]:
    try:
        st = os.stat(lock)
    except OSError:
        return []
    dev = st.st_dev
    major, minor = os.major(dev), os.minor(dev)
    want = {f"{major:02x}:{minor:02x}:{st.st_ino}", f"{major}:{minor}:{st.st_ino}"}
    pids = []
    try:
        for ln in open(proc_locks):
            f = ln.split()
            # "1: FLOCK  ADVISORY  WRITE 1234 00:2a:5678 0 EOF" (blocked waiters carry "->" and are skipped)
            if len(f) >= 6 and f[1] == "FLOCK" and f[5] in want:
                pids.append(int(f[4]))
    except (OSError, ValueError):
        pass
    return pids


def unit_of(pid: int) -> str | None:
    try:
        cg = open(f"/proc/{pid}/cgroup").read()
    except OSError:
        return None
    units = re.findall(r"/([A-Za-z0-9@._:-]+\.(?:service|scope))(?=/|$)", cg.strip().splitlines()[-1])
    units = [u for u in units if not re.match(r"user@\d+\.service$", u)]
    return units[-1] if units else None


pids = holder_pids()
if pids:
    busy = True
    holder = ""
    try:
        holder = open(os.path.join(lock_dir, "holder")).read().strip()
    except OSError:
        pass
    name = holder.split(" pid=", 1)[0] if holder else "unknown"
    m = re.search(r"since=(\S+)", holder)
    since = ""
    if m:
        try:
            since = " since " + et(datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ")
                                   .replace(tzinfo=ZoneInfo("UTC")).timestamp())
        except ValueError:
            pass
    unit = unit_of(pids[0])
    if unit is None:                       # pid not visible here (other pid namespace / exited): name it from the holder file
        unit = NAME_UNIT.get(name, "unit unknown") + " (by holder name)"
    if unit.endswith(".scope"):
        unit = f"an interactive/transient scope ({unit})"
    print(f"heavy lock: HELD by {name} -- {unit}{since}")
else:
    print("heavy lock: free")

try:
    timers = json.loads(os.environ.get("TIMERS_JSON") or "[]")
except ValueError:
    timers = []
    print("timers: could not read systemctl --user list-timers (treating as unknown, not busy)")
now = time.time()
due = []
for t in timers:
    unit = t.get("unit") or ""
    nxt = t.get("next")
    if not heavy_re.match(unit) or not nxt:
        continue
    ts = int(nxt) / 1e6
    if ts - now <= window_s:
        due.append((ts, unit))
for ts, unit in sorted(due):
    mins = max(0, int((ts - now) // 60))
    print(f"due soon: {unit} fires {et(ts)} (in {mins} min) and will want the heavy turn")
if not due:
    print(f"no heavy timer due in the next {window_s // 60} min")
busy = busy or bool(due)
print("heavy-lock-status: " + ("BUSY -- wait for it to finish/fire before starting a relay" if busy else "clear"))
sys.exit(1 if busy else 0)
PY
