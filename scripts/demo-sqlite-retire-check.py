#!/usr/bin/env python3
"""scripts/demo-sqlite-retire-check.py -- relay 4 PREPARATION for the
demo/production database split (2026-10-19): may the old demo SQLite files go?

It never deletes, overwrites or moves anything. It:

  (a) verifies the public demo has served from the demo Postgres instance for
      at least --hours N continuously: demo-api's /healthz says source=postgres,
      ok=true, profiles_store=postgres, uptime_s >= N h, and requests answered
      from sqlite == 0 (postgres > 0) since that start; both demo containers
      were started >= N h ago and carry DEMO_SOURCE / DISPATCH_DB_BACKEND =
      postgres (those two values only are read from their environment);
  (b) lists the old demo SQLite files that exist, with sizes, and whether any
      process on this host holds one open (names from /proc/*/fd, no command
      line is read or printed);
  (c) ONLY when (a) passes and nothing holds a file open, prints the exact
      shred command for the OPERATOR to run by hand -- never from a relay,
      never from here;
  (d) records sha256 + size + mtime of each file as the guardrail-proof G26
      record (JSON, --out; default under ~/.local/state/corporatetraveldc/
      proofs/), so the vault proof entry can show what was destroyed.

Hashing reads several GB: it waits for load headroom first
(scripts/relay-wait-load.sh) and reads at nice 19 / idle I/O.

Exit: 0 verified (command printed), 1 not yet verified or a file in use
(listing and hashes still produced), 2 usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parent.parent
HEALTHZ = os.environ.get("DEMO_HEALTHZ_URL", "http://127.0.0.1:8004/healthz")
CONTAINERS = ("systemd-corporatetraveldc-demo-api", "systemd-corporatetraveldc-runner-demo")
# The old demo SQLite stores (the rollback path of relay 2 ends here):
CANDIDATES = (
    "/var/lib/corporatetraveldc-demo-source/demo-source.db",     # the sovereign playback file (DEMO_SOURCE=sqlite)
    "/var/lib/corporatetraveldc-demo-state/demo_access.db",      # profiles under the sqlite backend
    "/var/lib/corporatetraveldc-demo/corporatetraveldc.db",      # runner-demo's local sqlite (chat under sqlite)
    "/var/lib/corporatetraveldc-demo/dispatch-chat.db",          # runner-demo's pre-2026-09-20 chat file
    "/var/lib/corporatetraveldc/demo.db",                        # the recorder's pre-Postgres archive
)
SIDECARS = ("-wal", "-shm", "-journal")
TAG = "[demo-sqlite-retire]"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fetch_healthz(url: str = HEALTHZ) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 -- loopback only
        return json.loads(r.read().decode("utf-8"))


def container_facts(name: str) -> dict:
    """StartedAt and the two switch values only -- never any other env value."""
    r = subprocess.run(["podman", "inspect", name, "--format",
                        "{{.State.StartedAt}}\n{{range .Config.Env}}{{println .}}{{end}}"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"running": False}
    lines = r.stdout.splitlines()
    facts: dict = {"running": True, "started_at": lines[0].strip() if lines else ""}
    for l in lines[1:]:
        k, _, v = l.partition("=")
        if k in ("DEMO_SOURCE", "DISPATCH_DB_BACKEND", "DISPATCH_ROLE"):
            facts[k] = v
    return facts


def _parse_started(s: str) -> datetime | None:
    # podman: "2026-10-19 03:12:45.123456789 +0000 UTC"
    try:
        date, time_, off = s.split()[:3]
        return datetime.strptime(f"{date} {time_.split('.')[0]} {off}", "%Y-%m-%d %H:%M:%S %z")
    except (ValueError, IndexError):
        return None


def verify(hours: float, healthz: dict | None, facts: dict[str, dict], now: datetime) -> list[str]:
    """Problems that block retirement; empty = verified."""
    need = hours * 3600
    p: list[str] = []
    if healthz is None:
        return ["demo-api /healthz unreachable"]
    if healthz.get("source") != "postgres":
        p.append(f"demo-api source is {healthz.get('source')!r}, not postgres")
    if healthz.get("ok") is not True:
        p.append("demo-api /healthz ok is not true")
    if healthz.get("profiles_store") != "postgres":
        p.append(f"profile store is {healthz.get('profiles_store')!r}, not postgres")
    req = healthz.get("requests") or {}
    if int(req.get("sqlite", -1)) != 0:
        p.append(f"requests answered from sqlite since start: {req.get('sqlite')}")
    if int(req.get("postgres", 0)) <= 0:
        p.append("no request answered from postgres yet (no traffic observed)")
    if int(healthz.get("uptime_s", 0)) < need:
        p.append(f"demo-api uptime {int(healthz.get('uptime_s', 0)) // 3600} h < {hours:g} h")
    for name in CONTAINERS:
        f = facts.get(name, {})
        if not f.get("running"):
            p.append(f"{name} not running")
            continue
        started = _parse_started(f.get("started_at", ""))
        if started is None or (now - started).total_seconds() < need:
            p.append(f"{name} started less than {hours:g} h ago")
        for k in ("DEMO_SOURCE", "DISPATCH_DB_BACKEND"):
            if f.get(k) != "postgres":
                p.append(f"{name} {k}={f.get(k)!r}")
        if f.get("DISPATCH_ROLE") != "demo":
            p.append(f"{name} is not DISPATCH_ROLE=demo")
    return p


def existing_files(candidates=CANDIDATES) -> list[pathlib.Path]:
    out = []
    for c in candidates:
        for suffix in ("",) + SIDECARS:
            path = pathlib.Path(c + suffix)
            if path.is_file():
                out.append(path)
    return out


def open_holders(paths: list[pathlib.Path], proc: pathlib.Path = pathlib.Path("/proc")) -> dict[str, int]:
    """path -> number of processes holding it open (fd links only; no
    cmdline, no comm is read). Unreadable /proc entries are skipped."""
    want = {str(p.resolve()): 0 for p in paths}
    for pid in proc.glob("[0-9]*"):
        seen: set[str] = set()
        try:
            for fd in (pid / "fd").iterdir():
                try:
                    t = os.readlink(fd)
                except OSError:
                    continue
                if t in want and t not in seen:
                    seen.add(t)
                    want[t] += 1
        except OSError:
            continue
    return {k: v for k, v in want.items() if v}


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def shred_command(paths: list[pathlib.Path]) -> str:
    return "shred -n 1 -z -u -- " + " ".join(str(p) for p in paths)


def _be_gentle() -> None:
    try:
        os.nice(19)
    except OSError:
        pass
    try:
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str] | None = None, *, healthz_fn=fetch_healthz, facts_fn=container_facts,
         candidates=CANDIDATES, proc: pathlib.Path = pathlib.Path("/proc"), wait_load: bool = True) -> int:
    ap = argparse.ArgumentParser(description="relay-4 preparation: may the old demo SQLite files go?")
    ap.add_argument("--hours", type=float, default=48.0, help="continuous hours on postgres required (default 48)")
    ap.add_argument("--out", default=None, help="G26 proof record path (JSON)")
    ap.add_argument("--no-hash", action="store_true", help="skip hashing (listing + verification only)")
    args = ap.parse_args(argv)
    now = utcnow()

    try:
        hz = healthz_fn()
    except Exception as e:  # noqa: BLE001 -- reported, not raised
        print(f"{TAG} /healthz: {type(e).__name__}")
        hz = None
    facts = {n: facts_fn(n) for n in CONTAINERS}
    problems = verify(args.hours, hz, facts, now)
    print(f"{TAG} (a) served from postgres for >= {args.hours:g} h: {'YES' if not problems else 'NO'}")
    for p in problems:
        print(f"{TAG}     - {p}")
    if hz:
        print(f"{TAG}     healthz: source={hz.get('source')} started_at={hz.get('started_at')} "
              f"requests={json.dumps(hz.get('requests'), sort_keys=True)} profiles_store={hz.get('profiles_store')}")

    files = existing_files(candidates)
    holders = open_holders(files, proc)
    print(f"{TAG} (b) old demo SQLite files present: {len(files)}")
    for f in files:
        st = f.stat()
        busy = holders.get(str(f.resolve()), 0)
        print(f"{TAG}     {st.st_size:>14,d}  {f}" + (f"   IN USE by {busy} process(es)" if busy else ""))

    record = {"guardrail": "G26", "what": "demo/production split relay 4: old demo SQLite files before destruction",
              "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "hours_required": args.hours,
              "verified": not problems and not holders, "problems": problems,
              "healthz": {k: (hz or {}).get(k) for k in ("source", "ok", "started_at", "uptime_s", "requests", "profiles_store")},
              "files": []}
    if not args.no_hash and files:
        if wait_load:   # tests pass False: never renice or wait the test runner
            subprocess.run(["bash", str(REPO / "scripts/relay-wait-load.sh")], stdin=subprocess.DEVNULL, check=False)
            _be_gentle()
    for f in files:
        st = f.stat()
        entry = {"path": str(f), "size": st.st_size,
                 "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if not args.no_hash:
            entry["sha256"] = sha256_file(f)
        record["files"].append(entry)
    if not args.no_hash:
        out = pathlib.Path(args.out) if args.out else (
            pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local/state"))
            / "corporatetraveldc/proofs" / f"G26-demo-sqlite-retire-{now.strftime('%Y%m%dT%H%M%SZ')}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(f"{TAG} (d) G26 record (sha256 + size + mtime per file): {out}")
        for e in record["files"]:
            print(f"{TAG}     {e['sha256']}  {e['path']}")

    if problems or holders or not files:
        why = "not verified" if problems else ("a file is in use" if holders else "nothing to retire")
        print(f"{TAG} (c) no shred command: {why}")
        return 0 if not files and not problems else 1
    print(f"{TAG} (c) VERIFIED. For the OPERATOR to run BY HAND (never from a relay), only AFTER the quadlet")
    print(f"{TAG}     change that removes the sovereign mount and DEMO_SOURCE=sqlite is live (this ends the relay-2 rollback):")
    print("")
    print("    " + shred_command(files))
    print("")
    print(f"{TAG}     btrfs note: copy-on-write means shred overwrites new extents and frees the old ones; it is")
    print(f"{TAG}     best-effort here. Check for snapshots holding these files before relying on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
