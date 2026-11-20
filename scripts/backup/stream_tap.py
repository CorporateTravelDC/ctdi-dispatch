#!/usr/bin/env python3
"""scripts/backup/stream_tap.py -- pass a byte stream through and record its size and hashes (2026-11-19).

  stream_tap.py META_JSON      stdin -> stdout unchanged; on EOF write {"bytes", "sha256", "sha1"} to META_JSON
  stream_tap.py --file PATH    print {"bytes", "sha256", "sha1"} of PATH (the re-read check of a staged object)
  stream_tap.py --same A B     exit 0 when two such JSON strings agree on sha256 and bytes
  stream_tap.py --get JSON KEY print one field

Used by ctdi-backup-direct.sh so every object's ciphertext hash is computed as it is written, then the
staged file is re-read and checked against it before upload, and the remote copy is checked after.
Sizes and hashes only; it never looks inside the stream (the stream is already encrypted).
"""
from __future__ import annotations

import hashlib
import json
import sys

CHUNK = 1 << 20


def _digest(read) -> dict:
    s256, s1, n = hashlib.sha256(), hashlib.sha1(), 0
    while True:
        b = read()
        if not b:
            break
        s256.update(b); s1.update(b); n += len(b)
        yield b
    yield {"bytes": n, "sha256": s256.hexdigest(), "sha1": s1.hexdigest()}


def tap(meta_path: str, src=None, dst=None) -> int:
    src = src or sys.stdin.buffer
    dst = dst or sys.stdout.buffer
    meta = None
    try:
        for item in _digest(lambda: src.read(CHUNK)):
            if isinstance(item, dict):
                meta = item
            else:
                dst.write(item)
        dst.flush()
    except BrokenPipeError:
        # the reader stopped (e.g. stage_guard hit its limit): record nothing, fail the pipeline
        return 75
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    return 0


def file_meta(path: str) -> dict:
    with open(path, "rb") as f:
        meta = None
        for item in _digest(lambda: f.read(CHUNK)):
            if isinstance(item, dict):
                meta = item
        return meta


def same(a: str, b: str) -> bool:
    x, y = json.loads(a), json.loads(b)
    return x["sha256"] == y["sha256"] and x["bytes"] == y["bytes"]


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--file":
        print(json.dumps(file_meta(argv[1])))
        return 0
    if len(argv) == 3 and argv[0] == "--same":      # exit 0 when two metas agree on sha256 + bytes
        return 0 if same(argv[1], argv[2]) else 1
    if len(argv) == 3 and argv[0] == "--get":       # print one field of a meta JSON
        print(json.loads(argv[1])[argv[2]])
        return 0
    if len(argv) == 1:
        return tap(argv[0])
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
