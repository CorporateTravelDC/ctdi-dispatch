#!/usr/bin/env python3
"""scripts/leak-guard/transcript_leak_scan.py -- find, and provably redact, secret
values that leaked into on-box transcripts (operator rule, 2026-10-09).

Why: agent session transcripts and tool outputs are files on this box. Anything
that can read them sees whatever leaked into them, session URL or not. So a leak
is redacted ON the box, before anything leaves it (private push, public push,
content that cloud agents read). See memory/vault rule "python3 -c, leak
redaction, push order".

What it looks for
  * every live secret value in /etc/corporatetraveldc/dispatch-secrets.env (the
    same parser the public leak gate uses; values >= 12 chars), raw and in its
    JSON-escaped form (transcripts are JSONL);
  * token shapes that are secrets wherever they appear: platform API tokens
    (ctdc_<user>_<32>), gateway access/refresh tokens (dga_/dgr_), and
    "Authorization: Bearer <token>" / "X-Board-Key: <token>" header values.

Default roots: ~/.claude/projects and /tmp/claude-<uid> (session transcripts and
tool outputs). Agent accounts' homes are only readable by root; the root-owned
installed copy scans them (scripts/install-root-copies.sh).

Output names the file, the secret's NAME and a count -- never a value.

  scan    [--root DIR ...]            exit 1 if a CLOSED file carries a leak; a file
          written in the last LIVE_MINUTES (a running session) is a warning only --
          it cannot be redacted safely while it is being written
  redact  [--root DIR ...] [--include-live]
          replace each hit with [REDACTED:<NAME>], atomically (same mode), skip
          files written in the last LIVE_MINUTES (a running session's transcript)
          unless --include-live, then rescan and print the proof line:
          "PROOF: <n> file(s) redacted; rescan hits = 0". Exit 1 if the rescan
          still finds anything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

SECRETS_ENV = Path(os.environ.get("LEAK_GUARD_SECRETS_ENV", "/etc/corporatetraveldc/dispatch-secrets.env"))
MIN_LEN = 12
LIVE_MINUTES = 15
TEXT_SUFFIXES = {".jsonl", ".json", ".txt", ".log", ".output", ".md", ".out", ""}
MAX_FILE_BYTES = 512 * 1024 * 1024
# Values that are configuration, not secrets, even though they sit in the secrets file.
NOT_SECRET = {b"tcps://ems1.swim.faa.gov:55443", b"tcps://ems2.swim.faa.gov:55443"}
TOKEN_PATTERNS = [
    ("api-token", re.compile(rb"ctdc_[a-z0-9-]{2,40}_[A-Za-z0-9]{32}")),
    ("gateway-token", re.compile(rb"\bdg[ar]_[A-Za-z0-9_-]{40,}")),
    ("bearer-header", re.compile(rb"(?i)(?<=authorization: bearer )[A-Za-z0-9._~+/=-]{20,}")),
    ("board-key-header", re.compile(rb"(?i)(?<=x-board-key: )[A-Za-z0-9._~+/=-]{20,}")),
]
PLACEHOLDER = re.compile(rb"^(?:0+|[xX]+|REDACTED.*)$")


def load_secret_values(path: Path = SECRETS_ENV) -> dict[str, list[bytes]]:
    """{NAME: [raw value, JSON-escaped value]} -- never printed."""
    out: dict[str, list[bytes]] = {}
    try:
        raw = path.read_bytes()
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(b"#") or b"=" not in line:
            continue
        k, _, v = line.partition(b"=")
        v = v.strip().strip(b"'\"")
        if len(v) < MIN_LEN or v in NOT_SECRET:
            continue
        forms = [v]
        esc = json.dumps(v.decode("utf-8", "replace"))[1:-1].encode()
        if esc != v:
            forms.append(esc)
        out[k.decode("utf-8", "replace")] = forms
    return out


def default_roots() -> list[Path]:
    return [Path.home() / ".claude" / "projects", Path(f"/tmp/claude-{os.getuid()}")]


def iter_files(roots: list[Path]):
    for r in roots:
        if r.is_file():
            yield r
            continue
        if not r.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(r, followlinks=False):
            for f in files:
                p = Path(dirpath) / f
                if p.suffix in TEXT_SUFFIXES and not p.is_symlink():
                    yield p


def find(blob: bytes, secrets: dict[str, list[bytes]]) -> dict[str, int]:
    hits: dict[str, int] = {}
    for name, forms in secrets.items():
        n = sum(blob.count(f) for f in forms)
        if n:
            hits[name] = n
    for label, rx in TOKEN_PATTERNS:
        n = sum(1 for m in rx.finditer(blob) if not PLACEHOLDER.match(m.group(0).split(b"_")[-1]))
        if n:
            hits[label] = hits.get(label, 0) + n
    return hits


def redact_blob(blob: bytes, secrets: dict[str, list[bytes]]) -> bytes:
    for name, forms in sorted(secrets.items(), key=lambda kv: -max(len(f) for f in kv[1])):
        for f in forms:
            blob = blob.replace(f, f"[REDACTED:{name}]".encode())
    for label, rx in TOKEN_PATTERNS:
        blob = rx.sub(lambda m: m.group(0) if PLACEHOLDER.match(m.group(0).split(b"_")[-1])
                      else f"[REDACTED:{label}]".encode(), blob)
    return blob


def scan(roots: list[Path], secrets: dict[str, list[bytes]]) -> dict[Path, dict[str, int]]:
    found = {}
    for p in iter_files(roots):
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            h = find(p.read_bytes(), secrets)
        except OSError:
            continue
        if h:
            found[p] = h
    return found


def _report(found: dict[Path, dict[str, int]]) -> None:
    for p, h in sorted(found.items()):
        print(f"  {p}: " + ", ".join(f"{k} x{v}" for k, v in sorted(h.items())))


def redact(roots: list[Path], secrets: dict[str, list[bytes]], include_live: bool) -> int:
    found = scan(roots, secrets)
    now, done, skipped = time.time(), 0, []
    for p in found:
        st = p.stat()
        if not include_live and now - st.st_mtime < LIVE_MINUTES * 60:
            skipped.append(p)
            continue
        new = redact_blob(p.read_bytes(), secrets)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".redact-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(new)
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, p)
        done += 1
    after = {p: h for p, h in scan(roots, secrets).items() if p not in skipped}
    for p in skipped:
        print(f"  SKIPPED (written in the last {LIVE_MINUTES} min, a live session?): {p}")
    if after:
        print("  STILL PRESENT after redaction:")
        _report(after)
    print(f"PROOF: {done} file(s) redacted; rescan hits = {sum(sum(h.values()) for h in after.values())}"
          + (f"; {len(skipped)} live file(s) left for after the session ends" if skipped else ""))
    return 1 if after else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("scan", "redact"))
    ap.add_argument("--root", action="append", type=Path, default=[])
    ap.add_argument("--include-live", action="store_true")
    a = ap.parse_args(argv)
    roots = a.root or default_roots()
    secrets = load_secret_values()
    if not secrets:
        print("transcript-leak-scan: WARNING no live secret values loaded (secrets file unreadable); token shapes only")
    if a.cmd == "redact":
        return redact(roots, secrets, a.include_live)
    found = scan(roots, secrets)
    now = time.time()
    live = {p: h for p, h in found.items() if now - p.stat().st_mtime < LIVE_MINUTES * 60}
    closed = {p: h for p, h in found.items() if p not in live}
    if live:
        print(f"transcript-leak-scan: WARNING {len(live)} LIVE file(s) (written in the last {LIVE_MINUTES} min) "
              "carry a leak; redact when the session ends (the sweep re-checks):")
        _report(live)
    if closed:
        print(f"transcript-leak-scan: LEAK in {len(closed)} file(s) (names and counts only):")
        _report(closed)
        print("  fix: scripts/leak-guard/transcript_leak_scan.py redact   (then rerun scan)")
        return 1
    if live:
        return 0
    print(f"transcript-leak-scan: clean ({len(secrets)} secret value(s) + {len(TOKEN_PATTERNS)} token shapes checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
