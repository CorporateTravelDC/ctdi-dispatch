#!/usr/bin/env python3
"""scripts/lib/populate_secrets.py -- the body of scripts/populate-secrets.sh
(rewritten 2026-10-19, operator, Gate-1b).

Reads ~/.secrets/{name}.{token,key} and sets the matching NAME=value lines in
/etc/corporatetraveldc/dispatch-secrets.env. Every other line of the file is kept
byte for byte; a known name already present is replaced where it stands (every
occurrence), a missing one is appended.

Why the rewrite: the old shell version put each value on a command line
(`sudo sed -i "s|^K=.*|K=<value>|"`, visible in the process table to every user
for the life of the call) and ran sed -i under sudo on /etc/corporatetraveldc.
Now:
  * values are read inside this process from the files and never appear in an
    argument vector, an environment variable of a child, or any output;
  * no sudo and no sed. The target is corporatetraveldc-owned 0600 inside a
    root:corporatetraveldc 0750 directory (checked 2026-10-19), so the user can
    write the FILE but cannot create a file in the DIRECTORY: a temp file +
    rename is used only when the directory is writable by this user and the
    target is this user's own file (owner and mode kept); otherwise the file is
    rewritten in place, opened for write without following a symlink, exactly
    as the operator's hand steps do (owner and mode untouched);
  * a value holding a control or escape character (any byte < 0x20, or 0x7f:
    ESC, CR, TAB, LF inside the value ...) is refused with the FILE's name, never
    the value, and nothing is written (fail closed). Surrounding whitespace (a
    trailing newline) is stripped first;
  * --dry-run prints names and lengths only.

Two names are written from one file where consumers read either:
  flightaware.key -> FLIGHTAWARE_API_KEY (common.gate_aeroapi) and
                     FLIGHTAWARE_AEROAPI_KEY (older name)
  jumpseat.token  -> JUMPSEAT_API_KEY and ACARSDRAMA_JUMPSEAT_TOKEN
                     (common.acars, acars_watcher read both)

Usage: scripts/populate-secrets.sh [--dry-run] [--target FILE]
Exit: 0 ok, 2 a value refused (nothing written), 3 the target cannot be written.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_TARGET = "/etc/corporatetraveldc/dispatch-secrets.env"

# (env name, secret file stem, extension or None = .token then .key)
MAPPING: list[tuple[str, str, str | None]] = [
    ("ACARSDRAMA_FEEDER_KEY", "acarsdrama", "token"),
    ("ACARSDRAMA_JUMPSEAT_TOKEN", "jumpseat", "token"),
    ("JUMPSEAT_API_KEY", "jumpseat", "token"),
    ("AIRFRAMES_TOKEN", "airframes", None),
    ("KPLER_MARITIME_API_TOKEN", "kpler", "key"),
    ("NTFY_TOKEN", "ntfy", None),
    ("ANTHROPIC_API_KEY", "anthropic", None),
    ("FLIGHTAWARE_FEEDER_KEY", "flightaware", "token"),
    ("FLIGHTAWARE_AEROAPI_KEY", "flightaware", "key"),
    ("FLIGHTAWARE_API_KEY", "flightaware", "key"),
    ("BOARD_KEY", "board", "key"),
    ("AIRNAVRADAR_SHARING_KEY", "airnavradar", None),
    ("FR24_SHARING_KEY", "flightradar24", None),
    ("PLANEFINDER_SHARING_KEY", "planefinder", None),
    ("AIS_AISHUB_ID", "aishub", None),
    ("AIS_MARINETRAFFIC_KEY", "marinetraffic", None),
    ("AIS_VESSELFI_KEY", "vesselfi", None),
    ("LIMOANYWHERE_WEBHOOK_SECRET", "limoanywhere_webhook", "key"),
    ("RINGCENTRAL_WEBHOOK_SECRET", "ringcentral_webhook", "key"),
    ("THREECX_WEBHOOK_SECRET", "threecx_webhook", "key"),
]

RESTART_HINT = """Restart containers to pick up changes (not done here):
  systemctl --user restart corporatetraveldc-runner.service
  systemctl --user restart corporatetraveldc-web.service
  systemctl --user restart corporatetraveldc-poller.service
  bash scripts/ingest-feed-ctl.sh restart all
  systemctl --user restart corporatetraveldc-fr24feed.service
  systemctl --user restart corporatetraveldc-airnavradar.service
  systemctl --user restart corporatetraveldc-planefinder.service"""


class Refused(Exception):
    """A secret file's value cannot go into an env file. Message names the file only."""


def bad_byte(value: bytes) -> bool:
    return any(b < 0x20 or b == 0x7F for b in value)


def secret_file(secrets_dir: Path, stem: str, ext: str | None) -> Path | None:
    for e in ((ext,) if ext else ("token", "key")):
        f = secrets_dir / f"{stem}.{e}"
        if f.is_file():
            return f
    return None


def read_value(path: Path) -> bytes:
    v = path.read_bytes().strip()
    if bad_byte(v):
        raise Refused(f"{path.name}: the value contains a control or escape character "
                      "(ESC, CR, TAB, LF or another byte < 0x20, or 0x7f) -- fix the file; nothing written")
    return v


def collect(secrets_dir: Path) -> tuple[dict[str, tuple[bytes, str]], list[str], list[str]]:
    """({name: (value, file name)}, skipped names, refusals). Values never leave this process."""
    values: dict[str, tuple[bytes, str]] = {}
    skipped, refused = [], []
    for name, stem, ext in MAPPING:
        f = secret_file(secrets_dir, stem, ext)
        if f is None:
            skipped.append(name)
            continue
        try:
            v = read_value(f)
        except Refused as e:
            if str(e) not in refused:
                refused.append(str(e))
            continue
        if not v:
            skipped.append(name)
            continue
        values[name] = (v, f.name)
    return values, skipped, refused


def render(existing: bytes, values: dict[str, bytes]) -> tuple[bytes, dict[str, str]]:
    """New file content: every known NAME= line replaced in place, missing names
    appended, every other byte kept. Returns (content, {name: 'replaced'|'appended'})."""
    lines = existing.splitlines(keepends=True)
    action: dict[str, str] = {}
    out = []
    for line in lines:
        key = line.split(b"=", 1)[0] if b"=" in line else None
        name = key.decode("utf-8", "replace") if key is not None else None
        if name in values:
            eol = b"\r\n" if line.endswith(b"\r\n") else (b"\n" if line.endswith(b"\n") else b"")
            out.append(key + b"=" + values[name] + eol)
            action[name] = "replaced"
        else:
            out.append(line)
    if out and not out[-1].endswith(b"\n"):
        out[-1] += b"\n"
    for name, v in values.items():
        if name not in action:
            out.append(name.encode() + b"=" + v + b"\n")
            action[name] = "appended"
    return b"".join(out), action


def install(target: Path, content: bytes) -> str:
    """Write content to target without sudo. Returns the method used."""
    if os.path.islink(target) or not os.path.isfile(target):
        raise OSError(f"{target} is not a regular file")
    st = os.lstat(target)
    d = target.parent
    if os.access(d, os.W_OK | os.X_OK) and st.st_uid == os.geteuid():
        fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{target.name}.")
        try:
            try:
                os.fchown(fd, -1, st.st_gid)          # same group (owner is already this user)
            except OSError:
                pass
            os.fchmod(fd, st.st_mode & 0o7777)
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return "temp file + rename (owner and mode kept)"
    fd = os.open(target, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        view, done = memoryview(content), 0
        while done < len(content):
            done += os.write(fd, view[done:])
        os.ftruncate(fd, len(content))
        os.fsync(fd)
    finally:
        os.close(fd)
    return "rewritten in place (directory not writable by this user; owner and mode untouched)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Set dispatch-secrets.env from ~/.secrets (values never printed).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target", default=os.environ.get("POPULATE_SECRETS_TARGET", DEFAULT_TARGET))
    a = ap.parse_args(argv)
    secrets_dir = Path(os.environ.get("HOME", str(Path.home()))) / ".secrets"
    target = Path(a.target)
    print("=== populate-secrets ===")
    print(f"Secrets dir: {secrets_dir}")
    print(f"Target:      {target}")
    if a.dry_run:
        print("Mode:        DRY RUN (names and lengths only)")
    print()

    values, skipped, refused = collect(secrets_dir)
    if refused:
        for r in refused:
            print(f"  [REFUSED] {r}")
        print("Nothing written.")
        return 2
    try:
        existing = target.read_bytes()
    except OSError as e:
        print(f"  [ERROR] cannot read {target} ({type(e).__name__}) -- no sudo is used; nothing written")
        return 3
    content, action = render(existing, {k: v for k, (v, _f) in values.items()})
    for name, _stem, _ext in MAPPING:
        if name in values:
            v, fname = values[name]
            tag = "DRY" if a.dry_run else "OK"
            print(f"  [{tag}]  {name} <{len(v)} chars> from {fname} ({action[name]})")
        elif name in skipped:
            print(f"  [SKIP] {name} -- secret file not found or empty in {secrets_dir}")
    if a.dry_run:
        print("\nDry run: nothing written.")
        return 0
    if not values:
        print("\nNothing to write.")
        return 0
    try:
        how = install(target, content)
    except OSError as e:
        print(f"  [ERROR] cannot write {target} ({type(e).__name__}) -- no sudo is used; nothing written")
        return 3
    print(f"\nWritten: {how}.")
    print(RESTART_HINT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
