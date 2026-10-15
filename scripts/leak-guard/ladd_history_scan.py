#!/usr/bin/env python3
"""scripts/leak-guard/ladd_history_scan.py -- LADD identifiers in a repository's
WHOLE history (2026-10-14).

The push-time tripwire (ladd_public_check.py) guards the tree being published.
Three identifiers had already been published before it existed, so the public
mirror's history still carried them. This scans every blob reachable from any
ref, plus every commit and tag message, with the tripwire's own matching rule
and allow list, and can write the replacement file git-filter-repo needs to
remove them.

Same handling as the tripwire: identifiers arrive on STDIN, one per line, piped
straight from the database; they are never printed. Output is counts only.

  ... | ladd_history_scan.py --repo DIR
        exit 0 clean, 1 hits, 2 no identifiers loaded (fail closed)
  ... | ladd_history_scan.py --repo DIR --write-replacements FILE
        also writes FILE (mode 0600) in git-filter-repo --replace-text form:
        one whole-token regex per matching identifier, replaced with
        LADD-REMOVED. The file holds CUI: shred it after the rewrite.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import io  # noqa: E402
import zipfile  # noqa: E402

from ladd_public_check import MAX_BYTES, OFFICE_SUFFIXES, XML_TAG, load_allow, matching_tokens  # noqa: E402

REPLACEMENT = "LADD-REMOVED"


def _git(repo: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, **kw)


SKIPPED: dict[str, int] = {}


def _office_text(data: bytes) -> str | None:
    """XML text of an Office (zip) blob, as the push-time tripwire reads it; None if not a zip."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    return "\n".join(XML_TAG.sub(" ", z.read(n).decode("utf-8", "replace")) for n in z.namelist() if n.endswith(".xml"))


def history_texts(repo: Path, refs: list[str] | None = None):
    """(kind, text) for every reachable blob and every commit/tag message. Text
    blobs <= MAX_BYTES are read as text; Office files (by path suffix) through
    their XML parts, exactly as the push-time tripwire reads them (2026-10-14,
    swarm audit NB-06: they used to be skipped silently). Other binaries and
    oversize blobs are skipped and COUNTED in SKIPPED, never silently.
    refs: the refs to walk (default --all; pass published refs to leave out a
    local stash)."""
    SKIPPED.clear()
    objs = _git(repo, "rev-list", "--objects", *(refs or ["--all"])).stdout.decode().splitlines()
    office = {ln.split(" ", 1)[0] for ln in objs
              if " " in ln and ln.split(" ", 1)[1].lower().endswith(tuple(OFFICE_SUFFIXES))}
    shas = sorted({ln.split(" ", 1)[0] for ln in objs if ln})
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for sha in shas:
            proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) < 3:
                continue
            kind, size = header[1].decode(), int(header[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)  # trailing newline
            if kind in ("commit", "tag"):
                # header fields are hashes and identities; the message follows the blank line
                yield kind, data.split(b"\n\n", 1)[-1].decode("utf-8", "replace")
            elif kind == "blob" and sha in office:
                text = _office_text(data)
                if text is None:
                    SKIPPED["unreadable office"] = SKIPPED.get("unreadable office", 0) + 1
                else:
                    yield "office", text
            elif kind == "blob" and size <= MAX_BYTES and b"\0" not in data[:8192]:
                yield kind, data.decode("utf-8", "replace")
            elif kind == "blob":
                SKIPPED["binary or oversize"] = SKIPPED.get("binary or oversize", 0) + 1
    finally:
        proc.stdin.close()
        proc.wait()


def scan(repo: Path, ladd: set[str], allow: set[str]) -> tuple[set[str], dict[str, int]]:
    found: set[str] = set()
    counts = {"blob": 0, "office": 0, "commit": 0, "tag": 0}
    for kind, text in history_texts(repo):
        hit = False
        for line in text.splitlines():
            toks = matching_tokens(line, ladd, allow)
            if toks:
                found.update(toks)
                hit = True
        if hit:
            counts[kind] += 1
    return found, counts


def replacement_lines(tokens: set[str]) -> list[str]:
    """Whole-token regexes, the tripwire's token boundary: letters, digits and
    hyphens. An identifier that is a hyphen part (ZZ0001 in X-ZZ0001) is
    bounded by the hyphen, as the tripwire matches it."""
    return [f"regex:(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])==>{REPLACEMENT}" for t in sorted(tokens)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--min-identifiers", type=int, default=1000)
    ap.add_argument("--write-replacements", type=Path)
    a = ap.parse_args(argv)
    ladd = {ln.strip() for ln in sys.stdin if ln.strip()}
    if len(ladd) < a.min_identifiers:
        print(f"ladd-history-scan: only {len(ladd)} identifier(s) received (< {a.min_identifiers}); FAIL CLOSED")
        return 2
    found, counts = scan(a.repo, ladd, load_allow())
    print(f"ladd-history-scan: {len(found)} distinct identifier(s); objects with hits: "
          f"{counts['blob']} text blob(s), {counts['office']} office blob(s), {counts['commit']} commit message(s), "
          f"{counts['tag']} tag message(s); skipped unread: {dict(SKIPPED) or 'none'}")
    if counts["office"]:
        # --replace-text cannot rewrite inside a zip: those blobs need removal/replacement per path
        print("ladd-history-scan: identifiers inside Office files -- text replacement cannot fix these; "
              "STOP and handle them per blob (exit 3)")
        return 3
    if a.write_replacements and found:
        fd = os.open(a.write_replacements, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)          # an existing file keeps its old mode otherwise (swarm audit NB-09)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(replacement_lines(found)) + "\n")
        print(f"ladd-history-scan: {len(found)} replacement rule(s) written (0600; shred after use)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
