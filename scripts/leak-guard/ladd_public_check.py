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

2026-10-23 (operator: "the LADD / IMO checks should be industry aware"; the tripwire had
refused a push over a sample VesselName in an AIS CSV fixture equal to a letters-only
entry): matching is privacy_lists.py's, shared with transcript_leak_scan.py.
  aviation  N-forms, hyphenated marks and letters-first forms with a digit match
            anywhere outside another vertical's fields; the registry form without
            the N and letters-only entries only in aviation identifier context
            (registration: / tail / reg / callsign / flight / r= / hex / icao ...),
            never in an ISO timestamp, never inside a maritime record. No aviation
            form (N-forms included) ever matches inside an AIS CSV block or the value
            of a maritime / ground / rail key: strictly no cross-vertical matching.
  maritime / ground / rail  /etc/corporatetraveldc/privacy-<industry>.list, only in
            their own keyed context (privacy_lists.py docstring). Missing or empty =
            "no entries" (reported, exit unaffected); unreadable = NOT CHECKED
            (reported, exit unaffected). Only the aviation floor fails closed; a MATCH
            in any vertical refuses the push (exit 1), reported as that vertical.
Suspected identifiers (operator, 2026-10-23): a vertical with NO list (missing, empty or
unreadable) does not silently pass what its own fields carry (a 9-digit value in an MMSI
field / AIS column, an IMO-prefixed 7-digit number, a VesselName, an id in a ground / rail
id field). Each is a "suspected <vertical> identifier -- no list, needs review" finding:
locations only, and the push is HELD (exit 1, the same refusal path as a match). --review
shows the value, its context and a SUGGESTED list line to add by hand (nothing is ever
appended to /etc/corporatetraveldc/privacy-*.list); a value reviewed as not sensitive goes
into privacy-review-allow.sha256 by hash and stops re-triggering.
Every hit line names its industry and class ('ladd-identifier' for aviation, as before;
'maritime-identifier' ...). The allow file applies to every industry.

Office files (.docx/.pptx/.xlsx/.odt...) are scanned through their XML text,
not their compressed bytes; other binaries (a NUL in the first 8 KiB) are skipped.

  ... | ladd_public_check.py --tree DIR [--min-identifiers N]
        exit 0 clean, 1 hits, 2 no identifiers loaded (fail closed)
  ... | ladd_public_check.py --tree DIR --review
        OPERATOR ONLY, in the operator's own terminal (never pasted to an agent,
        never screenshotted): each distinct matching token with its SHA-256, hit
        count and first location, so the operator can decide coincidence vs.
        real identifier. Coincidences: append "<sha256>  <reason>" to the allow
        file. Real identifiers: remove them from the tree. Suspected identifiers
        (vertical with no list): value, context, a suggested list entry for the
        operator to add by hand, and the not-sensitive allow line.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import privacy_lists as pl  # noqa: E402

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
    """The pre-2026-10-23 whole-token rule (any token, any context). Kept for
    ladd_history_scan.py, which rewrites history and deliberately over-matches; the
    push-time check uses privacy_hits() below."""
    out = []
    for m in TOKEN.finditer(line):
        for c in {m.group(0), *m.group(0).split("-")}:
            if c in ladd and hashlib.sha256(c.encode()).hexdigest() not in allow:
                out.append(c)
    return out


def hits_in_text(text: str, ladd: set[str], allow: set[str]) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if matching_tokens(line, ladd, allow)]


def privacy_spans(text: str, lists: pl.PrivacyLists, allow: set[str]) -> tuple[bytes, dict[str, list]]:
    blob = text.encode("utf-8", "replace")
    return blob, pl.spans_by_industry(blob, lists, allow)


def privacy_hits(text: str, lists: pl.PrivacyLists, allow: set[str]) -> dict[str, list[int]]:
    """industry -> line numbers carrying a hit."""
    blob, spans = privacy_spans(text, lists, allow)
    return {ind: pl.line_numbers(blob, ss) for ind, ss in spans.items()}


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


SUSPECT_ALLOW = pl.SUSPECT_ALLOW
SUSPECT = "suspect:"          # scan_tree key prefix: suspected identifier, vertical with no list


def load_suspect_allow() -> set[str]:
    return pl.load_allow(SUSPECT_ALLOW)


def scan_tree(root: Path, lists: pl.PrivacyLists, allow: set[str],
              suspect_allow: set[str] | None = None) -> dict[str, dict[str, list[int]]]:
    """relative path -> industry (or 'suspect:<industry>') -> line numbers."""
    suspect_allow = load_suspect_allow() if suspect_allow is None else suspect_allow
    found = {}
    for p in walk(root):
        for label, text in file_texts(p):
            blob, spans = privacy_spans(text, lists, allow)
            hits = {ind: pl.line_numbers(blob, ss) for ind, ss in spans.items()}
            sus: dict[str, list] = {}
            for x in pl.suspects(blob, lists, suspect_allow):
                sus.setdefault(SUSPECT + x.industry, []).append((x.start, x.end))
            hits.update({k: pl.line_numbers(blob, v) for k, v in sus.items()})
            if hits:
                rel = str(p.relative_to(root)) + (label[len(p.name):] if label != p.name else "")
                found[rel] = hits
    return found


def _context(blob: bytes, a: int, b: int, width: int = 60) -> str:
    s = max(blob.rfind(b"\n", 0, a) + 1, a - width)
    e = blob.find(b"\n", b)
    e = min(len(blob) if e < 0 else e, b + width)
    return blob[s:e].decode("utf-8", "replace").strip()


def review(root: Path, lists: pl.PrivacyLists, allow: set[str], suspect_allow: set[str] | None = None) -> int:
    import time
    suspect_allow = load_suspect_allow() if suspect_allow is None else suspect_allow
    today = time.strftime("%Y-%m-%d")
    seen: dict[tuple[str, str], list] = {}
    sus: dict[tuple[str, str], list] = {}
    for p in walk(root):
        for label, text in file_texts(p):
            blob, spans = privacy_spans(text, lists, allow)
            loc = f"{p.relative_to(root)}{label[len(p.name):]}"
            for ind, ss in spans.items():
                for a, b in ss:
                    tok = pl.normalize(blob[a:b]).decode("utf-8", "replace") if ind != pl.AVIATION \
                        else blob[a:b].decode("utf-8", "replace")
                    n = pl.line_numbers(blob, [(a, b)])[0]
                    e = seen.setdefault((ind, tok), [0, f"{loc}:{n}"])
                    e[0] += 1
            for x in pl.suspects(blob, lists, suspect_allow):
                v = x.value(blob).decode("utf-8", "replace")
                where = f"{loc}:{pl.line_numbers(blob, [(x.start, x.end)])[0]}"
                e = sus.setdefault((x.industry, v), [0, where, x, _context(blob, x.start, x.end),
                                                     pl.suggested_entry(x, blob, where, today)])
                e[0] += 1
    print("OPERATOR REVIEW -- CUI membership below; do not paste or screenshot this section.")
    for (ind, tok), (count, where) in sorted(seen.items(), key=lambda kv: -kv[1][0]):
        print(f"{hashlib.sha256(tok.encode()).hexdigest()}  {ind:<9} {tok:<12} hits {count:<4} first {where}")
    print(f"{len(seen)} distinct token(s). Coincidence -> append '<sha256>  <reason>' to {ALLOW}")
    if sus:
        print("\nSUSPECTED identifiers in a vertical with NO list -- needs review:")
        for (ind, v), (count, where, x, ctx, entry) in sorted(sus.items(), key=lambda kv: -kv[1][0]):
            print(f"  [{ind}] {v!r}  hits {count}  first {where}  ({x.where})")
            print(f"    context:  {ctx}")
            print(f"    sensitive -> add BY HAND to /etc/corporatetraveldc/privacy-{ind}.list:")
            print(f"      {entry}")
            print(f"    not sensitive -> append to {SUSPECT_ALLOW}:")
            print(f"      {pl.suspect_hash(ind, v.encode())}  not sensitive: {x.where}, reviewed {today}")
        print(f"{len(sus)} distinct suspected value(s). Nothing is appended automatically.")
    return 1 if (seen or sus) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", required=True, type=Path)
    ap.add_argument("--min-identifiers", type=int, default=1000,
                    help="fail closed if fewer identifiers arrive (DB unreachable, empty table)")
    ap.add_argument("--review", action="store_true",
                    help="operator-only: list matching tokens and suspected identifiers (see above)")
    a = ap.parse_args(argv)
    ladd = {l.strip() for l in sys.stdin if l.strip()}
    if len(ladd) < a.min_identifiers:
        print(f"ladd-public-check: only {len(ladd)} identifier(s) received (< {a.min_identifiers}); "
              "cannot vouch for the tree -- FAIL CLOSED")
        return 2
    lists = pl.PrivacyLists(pl.aviation_forms(ladd), f"{len(ladd)} identifiers", pl.load_keyed_lists())
    print(f"ladd-public-check: privacy lists -- {lists.summary()}")
    if a.review:
        return review(a.tree, lists, load_allow())
    found = scan_tree(a.tree, lists, load_allow())
    matches = {f: {k: v for k, v in h.items() if not k.startswith(SUSPECT)} for f, h in found.items()}
    matches = {f: h for f, h in matches.items() if h}
    held = {f: {k[len(SUSPECT):]: v for k, v in h.items() if k.startswith(SUSPECT)} for f, h in found.items()}
    held = {f: h for f, h in held.items() if h}
    if matches:
        total = sum(len(ls) for v in matches.values() for ls in v.values())
        per = {ind: sum(1 for v in matches.values() if ind in v) for ind in pl.INDUSTRIES}
        print(f"ladd-public-check: privacy identifier(s) on {total} line(s) in {len(matches)} file(s) ("
              + ", ".join(f"{ind} {n} file(s)" for ind, n in per.items() if n)
              + "; locations only, never values):")
        for f, hits in sorted(matches.items()):
            for ind in pl.INDUSTRIES:
                ls = hits.get(ind)
                if ls:
                    print(f"  {f}: line(s) {', '.join(map(str, ls[:20]))}{' ...' if len(ls) > 20 else ''}"
                          f" [{ind}: {pl.LABELS[ind]}]")
    if held:
        total = sum(len(ls) for v in held.values() for ls in v.values())
        print(f"ladd-public-check: HOLD -- suspected identifier(s) on {total} line(s) in {len(held)} file(s) "
              "in a vertical with no list, needs review (locations only, never values):")
        for f, hits in sorted(held.items()):
            for ind in pl.KEYED_INDUSTRIES:
                ls = hits.get(ind)
                if ls:
                    print(f"  {f}: line(s) {', '.join(map(str, ls[:20]))}{' ...' if len(ls) > 20 else ''}"
                          f" [suspected {ind} identifier -- no list, needs review]")
        print("  review in YOUR terminal only: scripts/leak-guard/ladd_public_check.py --tree DIR --review "
              "(list on stdin, as push-public.sh pipes it); add real ones to the vertical's list by hand, "
              f"not-sensitive ones by hash to {SUSPECT_ALLOW.name}")
    if matches or held:
        return 1
    print(f"ladd-public-check: clean ({len(ladd)} identifiers checked; {lists.summary()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
