#!/usr/bin/env python3
"""scripts/backup/proton_sync.py -- mirror the Borg repository to Proton Drive with Proton's official Drive CLI.

2026-11-25 (operator): replaces rclone's protondrive backend for the nightly off-box copy. rclone's backend is
reverse-engineered and kept failing on Proton's draft handling (422 "already exists" -> 404 Code=2501 "file has
been removed", rclone issue #9722), crawling at 55-66 KiB/s; the official CLI (0.9.0, Proton Drive SDK) moved
the same data at 1.4-2.7 MiB/s in the 11-25 test and recovered an interrupted upload cleanly.

What it does (one pass, safe to rerun):
  1. verify the CLI binary against its pinned SHA-512 (a swapped binary never sees the Proton session)
  2. list the remote copy recursively (`filesystem list -j`) and the local repository
  3. upload every file that is missing remotely or has a different size, ONE file at a time:
       data/**            first, in segment order (Borg segments never change once written)
       everything else    last  (index.*, hints.*, integrity.*, config, nonce, README): so a pass that stops early
                          leaves the remote copy at the previous consistent state plus extra segments, never a
                          new index pointing at segments that are not there
  4. only when every upload succeeded: move remote files that no longer exist locally (Borg compaction) to the
     Proton trash -- capped (--max-deletes), and refused outright if the local listing looks truncated
Lock files (lock.exclusive, lock.roster) are never copied. Paths and sizes are logged; never a node id, an
account name or anything from the session.

Bandwidth: the CLI has no rate limit of its own, so the average is capped instead: after each file, if it went
faster than --avg-cap-mb, the next one waits until the average is back at the cap (bursts inside one file still
run at link speed). --stop-if-iface: before each file, if the default route has moved to that interface (the
metered hotspot), stop with exit 5 so the caller can finish on its metered path.

Exit: 0 complete; 4 incomplete (a stall: no file finished for --stall-min, or the --max-hours budget ran out);
5 stopped because the uplink became metered; 3 refused or failed hard (binary mismatch, listing failed, remote
root missing). 4 and 3 mean what they mean for rate_guard.py, so the backup script treats them the same way.

The Proton session comes from the environment (PROTON_DRIVE_CREDENTIALS_STORE / PROTON_DRIVE_CACHE_DIR), set up
by the caller; this script never reads or writes it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

META_LAST = ("index.", "hints.", "integrity.", "config", "nonce", "README")
SKIP_PREFIXES = ("lock.",)
MIN_UPLOAD_TIMEOUT_S = 600
_sleep = time.sleep
FLOOR_RATE_BPS = 256 * 1024         # per-file timeout = size at 256 KiB/s, at least 10 min (~32 min per 500 MB segment)


class Hard(Exception):
    """A refusal or failure that must not be retried blindly (exit 3)."""


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] [proton-sync] {msg}", flush=True)


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Cli:
    def __init__(self, path: str, dry_run: bool = False):
        self.path = path
        self.dry_run = dry_run

    def run(self, args: list[str], timeout: float = 300) -> subprocess.CompletedProcess:
        return subprocess.run([self.path, *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)

    def list(self, remote: str) -> list[dict]:
        p = self.run(["filesystem", "list", "-j", remote], timeout=300)
        if p.returncode != 0:
            raise Hard(f"listing {remote} failed (exit {p.returncode}): {p.stderr.strip()[:200]}")
        try:
            nodes = json.loads(p.stdout or "[]")
        except ValueError as e:
            raise Hard(f"listing {remote} returned no JSON ({e})") from None
        if not isinstance(nodes, list):
            raise Hard(f"listing {remote} returned {type(nodes).__name__}, not a list")
        return nodes

    def create_folder(self, parent: str, name: str) -> None:
        if self.dry_run:
            return
        p = self.run(["filesystem", "create-folder", parent, name], timeout=120)
        if p.returncode != 0 and "exist" not in (p.stdout + p.stderr).lower():
            raise Hard(f"could not create {parent}/{name} (exit {p.returncode}): {p.stderr.strip()[:200]}")

    def upload(self, local: Path, parent: str, size: int) -> bool:
        if self.dry_run:
            return True
        timeout = max(MIN_UPLOAD_TIMEOUT_S, size / FLOOR_RATE_BPS)
        try:
            p = self.run(["filesystem", "upload", "-f", "replace", "-d", "merge", "-t", str(local), parent],
                         timeout=timeout)
        except subprocess.TimeoutExpired:
            log(f"   upload timed out after {timeout:.0f} s: {local.name}")
            return False
        if p.returncode != 0:
            log(f"   upload failed (exit {p.returncode}): {local.name}: {(p.stderr or p.stdout).strip()[:200]}")
        return p.returncode == 0

    def trash(self, remote: str) -> bool:
        if self.dry_run:
            return True
        p = self.run(["filesystem", "trash", remote], timeout=120)
        return p.returncode == 0


def node_name(n: dict) -> str | None:
    nm = n.get("name")
    if isinstance(nm, dict):
        return nm.get("value") if nm.get("ok") else None
    return nm if isinstance(nm, str) else None


def node_size(n: dict) -> int | None:
    rev = n.get("activeRevision") or {}
    for v in (rev.get("claimedSize"), n.get("claimedSize"), n.get("size")):
        if isinstance(v, int):
            return v
    return None


def default_iface() -> str:
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in out.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    return ""


def walk_remote(cli: Cli, root: str) -> tuple[dict[str, int | None], set[str]]:
    files: dict[str, int | None] = {}
    folders: set[str] = {""}
    stack = [""]
    while stack:
        rel = stack.pop()
        for n in cli.list(root + ("/" + rel if rel else "")):
            name = node_name(n)
            if not name:
                continue
            child = f"{rel}/{name}" if rel else name
            if n.get("state", "active") != "active":
                continue                     # a draft or trashed node is not a copy
            if n.get("type") == "folder":
                folders.add(child)
                stack.append(child)
            elif n.get("type") == "file":
                files[child] = node_size(n)
    return files, folders


def walk_local(repo: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for dirpath, _dirs, names in os.walk(repo):
        for name in names:
            if name.startswith(SKIP_PREFIXES):
                continue
            p = Path(dirpath) / name
            out[str(p.relative_to(repo))] = p.stat().st_size
    return out


def order_key(rel: str) -> tuple:
    parts = rel.split("/")
    if parts[0] == "data":
        return (0, *[int(x) if x.isdigit() else x for x in parts[1:]])
    last = any(parts[-1].startswith(m) for m in META_LAST)
    return (2 if last else 1, rel)


def ensure_folders(cli: Cli, root: str, rel_dir: str, known: set[str]) -> None:
    if rel_dir in known:
        return
    parent = os.path.dirname(rel_dir)
    ensure_folders(cli, root, parent, known)
    cli.create_folder(root + ("/" + parent if parent else ""), os.path.basename(rel_dir))
    known.add(rel_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mirror the Borg repository to Proton Drive (official CLI).")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--remote", required=True, help="remote folder, e.g. /my-files/ctdi-backup")
    ap.add_argument("--cli", required=True)
    ap.add_argument("--cli-sha512", required=True)
    ap.add_argument("--stall-min", type=float, default=20)
    ap.add_argument("--max-hours", type=float, default=6)
    ap.add_argument("--max-deletes", type=int, default=200)
    ap.add_argument("--no-delete", action="store_true")
    ap.add_argument("--avg-cap-mb", type=float, default=0, help="average upload cap in MB/s (0 = none)")
    ap.add_argument("--stop-if-iface", action="append", default=[], help="stop (exit 5) if the default route uses it")
    ap.add_argument("--unsized", choices=("upload", "skip"), default="upload",
                    help="a remote file with no claimed size: upload it again (default) or count it as present")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    summary = {"uploaded": 0, "uploaded_bytes": 0, "skipped": 0, "failed": 0, "trashed": 0,
               "pending": 0, "complete": False}
    try:
        if sha512(Path(a.cli)) != a.cli_sha512.lower():
            raise Hard(f"{a.cli} does not match its pinned SHA-512; not running it")
        cli = Cli(a.cli, a.dry_run)
        repo = Path(a.repo)
        if not (repo / "config").is_file():
            raise Hard(f"{repo} does not look like a Borg repository (no config)")
        root_parent, root_name = os.path.dirname(a.remote), os.path.basename(a.remote)
        if not any(node_name(n) == root_name and n.get("type") == "folder" for n in cli.list(root_parent)):
            raise Hard(f"remote folder {a.remote} does not exist; create it deliberately, never by accident")
        local = walk_local(repo)
        remote, folders = walk_remote(cli, a.remote)
        unsized = sum(1 for r in local if r in remote and remote[r] is None)
        if a.unsized == "skip":
            todo = sorted((r for r, s in local.items() if r not in remote or remote[r] not in (None, s)), key=order_key)
        else:
            todo = sorted((r for r, s in local.items() if remote.get(r) != s), key=order_key)
        summary["remote_unsized"] = unsized
        summary["skipped"] = len(local) - len(todo)
        summary["pending"] = len(todo)
        log(f"local {len(local)} files, remote {len(remote)} files ({unsized} without a size); {len(todo)} to upload "
            f"({sum(local[r] for r in todo) / 1073741824:.2f} GiB)")
        last_progress = time.monotonic()
        metered = False
        for rel in todo:
            if a.stop_if_iface and default_iface() in a.stop_if_iface:
                log("the uplink is now metered; stopping so the caller finishes on its metered path")
                metered = True
                break
            now = time.monotonic()
            if now - t0 > a.max_hours * 3600:
                log(f"time budget of {a.max_hours} h used up; stopping (incomplete)")
                break
            if now - last_progress > a.stall_min * 60:
                log(f"no file finished for {a.stall_min} min; stopping (stall)")
                break
            rel_dir = os.path.dirname(rel)
            ensure_folders(cli, a.remote, rel_dir, folders)
            parent = a.remote + ("/" + rel_dir if rel_dir else "")
            started = time.monotonic()
            ok = cli.upload(repo / rel, parent, local[rel]) or cli.upload(repo / rel, parent, local[rel])
            took = time.monotonic() - started
            if ok and a.avg_cap_mb > 0 and not a.dry_run:
                wait = local[rel] / (a.avg_cap_mb * 1_000_000) - took
                if wait > 0:
                    summary["capped_s"] = summary.get("capped_s", 0) + int(wait)
                    _sleep(wait)
            if ok:
                summary["uploaded"] += 1
                summary["uploaded_bytes"] += local[rel]
                summary["pending"] -= 1
                last_progress = time.monotonic()
                if summary["uploaded"] % 25 == 0:
                    log(f"   {summary['uploaded']}/{len(todo)} uploaded, "
                        f"{summary['uploaded_bytes'] / 1073741824:.2f} GiB")
            else:
                summary["failed"] += 1
        summary["complete"] = summary["pending"] == 0
        if summary["complete"] and not a.no_delete:
            stale = sorted(r for r in remote if r not in local and not os.path.basename(r).startswith(SKIP_PREFIXES))
            if stale and len(local) < 0.9 * len(remote):
                log(f"NOT trashing {len(stale)} remote-only files: the local listing ({len(local)}) is under 90% of "
                    f"the remote ({len(remote)}), which looks like a truncated listing")
            elif len(stale) > a.max_deletes:
                log(f"NOT trashing {len(stale)} remote-only files: over the cap of {a.max_deletes}")
            else:
                for r in stale:
                    if cli.trash(f"{a.remote}/{r}"):
                        summary["trashed"] += 1
                if stale:
                    log(f"trashed {summary['trashed']}/{len(stale)} remote-only files (removed locally by compaction)")
        summary["seconds"] = int(time.monotonic() - t0)
        print(json.dumps(summary, sort_keys=True), flush=True)
        summary["metered_stop"] = metered
        return 0 if summary["complete"] else (5 if metered else 4)
    except Hard as e:
        log(f"REFUSED: {e}")
        summary["error"] = str(e)[:300]
        print(json.dumps(summary, sort_keys=True), flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
