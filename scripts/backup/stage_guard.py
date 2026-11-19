#!/usr/bin/env python3
"""scripts/backup/stage_guard.py -- never let a backup dump fill a disk (2026-11-18).

Why: on 2026-11-18 13:17-13:54Z the uncompressed dispatch dump (-Z 0, kept uncompressed so Borg can
deduplicate it) reached 52 GB on the USB stage -- the "~7 GB" of October had become a 38 GB database,
27 GB of it the TOAST of demo_snapshots, which pg_dump writes out decompressed -- and the run died with
the stage filesystem full. Nothing stopped it before the disk ran out.

  stage_guard.py preflight DIR NEED_BYTES [MIN_FREE_BYTES]
      exit 0 if DIR's filesystem has NEED_BYTES + MIN_FREE_BYTES free, else print the shortfall, exit 75.
  stage_guard.py limit OUT MAX_BYTES
      copy stdin to OUT; past MAX_BYTES, stop reading (the writer gets EPIPE), delete OUT, exit 75.
  stage_guard.py free DIR
      print DIR's free bytes.
Sizes only; never file contents.
"""
from __future__ import annotations

import os
import shutil
import sys

CHUNK = 1 << 20
EX_TEMPFAIL = 75


def gib(n: int) -> str:
    return f"{n / 2**30:.1f} GiB"


def free(path: str) -> int:
    return shutil.disk_usage(path).free


def preflight(path: str, need: int, min_free: int) -> int:
    have = free(path)
    if have >= need + min_free:
        print(f"stage-guard: {gib(have)} free on {path}; need ~{gib(need)} + {gib(min_free)} reserve -- ok")
        return 0
    print(f"stage-guard: NOT ENOUGH SPACE on {path}: {gib(have)} free, need ~{gib(need)} + {gib(min_free)} "
          f"reserve (short by {gib(need + min_free - have)})", file=sys.stderr)
    return EX_TEMPFAIL


def limit(out: str, max_bytes: int) -> int:
    written = 0
    src = sys.stdin.buffer
    try:
        with open(out, "wb") as f:
            while True:
                buf = src.read(CHUNK)
                if not buf:
                    break
                written += len(buf)
                if written > max_bytes:
                    raise OverflowError
                f.write(buf)
    except OverflowError:
        try:
            os.unlink(out)
        except OSError:
            pass
        print(f"stage-guard: dump passed its {gib(max_bytes)} limit; stopped and removed {out}", file=sys.stderr)
        return EX_TEMPFAIL
    except OSError as e:
        try:
            os.unlink(out)
        except OSError:
            pass
        print(f"stage-guard: write failed ({e.strerror}); removed {out}", file=sys.stderr)
        return EX_TEMPFAIL
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "preflight":
        return preflight(argv[1], int(argv[2]), int(argv[3]) if len(argv) > 3 else 0)
    if len(argv) == 3 and argv[0] == "limit":
        return limit(argv[1], int(argv[2]))
    if len(argv) == 2 and argv[0] == "free":
        print(free(argv[1]))
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
