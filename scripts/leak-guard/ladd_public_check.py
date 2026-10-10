#!/usr/bin/env python3
"""scripts/leak-guard/ladd_public_check.py -- push-time LADD tripwire (2026-10-09).

Fails if any LADD identifier (CUI: faa_ladd_aircraft + faa_ladd_removals)
appears as a token anywhere in a tree about to be published. Found after three
identifiers quoted from the 2026-08-25 filter files had sat in a doc and a
docstring, public mirror included, since the first sanitised push.

The identifiers arrive on STDIN, one per line, piped straight from the
database by push-public.sh. They are never written to disk, never printed and
never committed: output is file:line and a count only.

Matching: tokens of letters, digits and hyphens, compared case-sensitively
(LADD entries are upper case), as the whole token and as each hyphen-separated
part. A token confirmed as a harmless coincidence (an ordinary word that is
also an entry) can be allowed by putting its SHA-256 in
scripts/leak-guard/ladd-public-allow.sha256 -- the hash, never the value.

Office files (.docx/.pptx/.xlsx/.odt...) are scanned through their XML text,
not their compressed bytes; other binaries (a NUL in the first 8 KiB) are skipped.

  ... | ladd_public_check.py --tree DIR [--min-identifiers N]
        exit 0 clean, 1 hits, 2 no identifiers loaded (fail closed)
  ... | ladd_public_check.py --tree DIR --review
        OPERATOR ONLY, in the operator's own terminal (never pasted to an agent,
        never screenshotted): each distinct matching token with its SHA-256, hit
        count and first location, so the operator can decide coincidence vs.
        real identifier. Coincidences: append "<sha256>  <reason>" to the allow
        file. Real identifiers: remove them from the tree.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
ALLOW = Path(__file__).with_name("ladd-public-allow.sha256")
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".asc", ".sig", ".pyc"}
OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods"}
XML_TAG = re.compile(r"<[^>]+>")
MAX_BYTES = 8 * 1024 * 1024


def load_allow(path: Path = ALLOW) -> set[str]:
    try:
        return {l.split()[0].lower() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")}
    except OSError:
        return set()


def matching_tokens(line: str, ladd: set[str], allow: set[str]) -> list[str]:
    out = []
    for m in TOKEN.finditer(line):
        for c in {m.group(0), *m.group(0).split("-")}:
            if c in ladd and hashlib.sha256(c.encode()).hexdigest() not in allow:
                out.append(c)
    return out


def hits_in_text(text: str, ladd: set[str], allow: set[str]) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if matching_tokens(line, ladd, allow)]


def file_texts(p: Path):
    """(label, text) pairs for one file: Office XML parts, or the file itself."""
    if p.suffix.lower() in OFFICE_SUFFIXES:
        try:
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.endswith(".xml"):
                        yield f"{p.name}!{n}", XML_TAG.sub(" ", z.read(n).decode("utf-8", "replace"))
        except (zipfile.BadZipFile, OSError):
            pass
        return
    if p.suffix.lower() in SKIP_SUFFIXES or p.stat().st_size > MAX_BYTES:
        return
    b = p.read_bytes()
    if b"\0" in b[:8192]:
        return
    yield p.name, b.decode("utf-8", "replace")


def walk(root: Path):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            p = Path(dirpath) / f
            if not p.is_symlink() and p.is_file():
                yield p


def scan_tree(root: Path, ladd: set[str], allow: set[str]) -> dict[str, list[int]]:
    found = {}
    for p in walk(root):
        for label, text in file_texts(p):
            ls = hits_in_text(text, ladd, allow)
            if ls:
                rel = str(p.relative_to(root)) + (label[len(p.name):] if label != p.name else "")
                found[rel] = ls
    return found


def review(root: Path, ladd: set[str], allow: set[str]) -> int:
    seen: dict[str, list] = {}
    for p in walk(root):
        for label, text in file_texts(p):
            for n, line in enumerate(text.splitlines(), 1):
                for tok in matching_tokens(line, ladd, allow):
                    e = seen.setdefault(tok, [0, f"{p.relative_to(root)}{label[len(p.name):]}:{n}"])
                    e[0] += 1
    print("OPERATOR REVIEW -- CUI membership below; do not paste or screenshot this section.")
    for tok, (count, where) in sorted(seen.items(), key=lambda kv: -kv[1][0]):
        print(f"{hashlib.sha256(tok.encode()).hexdigest()}  {tok:<12} hits {count:<4} first {where}")
    print(f"{len(seen)} distinct token(s). Coincidence -> append '<sha256>  <reason>' to {ALLOW}")
    return 1 if seen else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", required=True, type=Path)
    ap.add_argument("--min-identifiers", type=int, default=1000,
                    help="fail closed if fewer identifiers arrive (DB unreachable, empty table)")
    ap.add_argument("--review", action="store_true", help="operator-only: list matching tokens (see above)")
    a = ap.parse_args(argv)
    ladd = {l.strip() for l in sys.stdin if l.strip()}
    if len(ladd) < a.min_identifiers:
        print(f"ladd-public-check: only {len(ladd)} identifier(s) received (< {a.min_identifiers}); "
              "cannot vouch for the tree -- FAIL CLOSED")
        return 2
    if a.review:
        return review(a.tree, ladd, load_allow())
    found = scan_tree(a.tree, ladd, load_allow())
    if found:
        total = sum(len(v) for v in found.values())
        print(f"ladd-public-check: LADD identifier(s) on {total} line(s) in {len(found)} file(s) "
              "(locations only, never values):")
        for f, ls in sorted(found.items()):
            print(f"  {f}: line(s) {', '.join(map(str, ls[:20]))}{' ...' if len(ls) > 20 else ''}")
        return 1
    print(f"ladd-public-check: clean ({len(ladd)} identifiers checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
