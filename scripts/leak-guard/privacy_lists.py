#!/usr/bin/env python3
"""scripts/leak-guard/privacy_lists.py -- industry-aware privacy lists (2026-10-23).

Operator rule (2026-10-23): "the LADD / IMO checks should be industry aware". An
aviation privacy list matches only in aviation identifier context, a maritime list
only in vessel fields / context; never cross-match; every hit reports its industry.
Trigger: the public tripwire refused a push because a sample VesselName in an AIS CSV
test fixture equalled a letters-only LADD entry.

Shared by the push-time tripwire (ladd_public_check.py), the transcript scanner
(transcript_leak_scan.py) and the history scan. Stdlib only and kept next to them in
scripts/leak-guard/: the hourly ROOT unit runs the installed copies in
/usr/local/libexec/ctdc and never imports src/ (scripts/install-root-copies.sh
installs this file with them).

Industries
  aviation  'ladd-identifier' (class name kept for compatibility). The production
            filter's list, faa_ladd_aircraft UNION faa_ladd_removals, loaded by the
            callers as before (database, or piped on stdin). FAIL CLOSED under
            AVIATION_MIN_IDENTIFIERS. Matching (the 2026-10-22 transcript rule, moved
            here): N-forms (N7QZK), hyphenated marks and letters-first forms carrying
            a digit (QQZ901) match anywhere OUTSIDE another vertical's fields. SHORT
            forms -- the registry form without the N (7QZK) and letters-only entries --
            match only in aviation identifier context (tail / reg / registration /
            aircraft / callsign / flight / r= / hex / icao ...), never inside an
            ISO-8601 timestamp, and never inside a maritime record (an AIS CSV block,
            or a record carrying an mmsi key).
            Strictly no cross-vertical matching (operator correction 2026-10-23): NO
            aviation form, N-forms included, matches inside another vertical's field
            -- an AIS CSV block, the value of a maritime key (mmsi / imo / vessel /
            vessel_name / ship ...), an AIS CallSign (a callsign key in a record that
            carries an mmsi key), an IMO1234567 token, or the value of a ground / rail
            key. An N-shaped VesselName is a vessel name, not an aircraft.
  maritime  'maritime-identifier'. Operator-designated list file
            /etc/corporatetraveldc/privacy-maritime.list: one entry per line,
            `mmsi:<9 digits>`, `imo:<7 digits>`, `name:<vessel name>`, # comments.
            Matching only in maritime context: an mmsi key right before an MMSI (a
            bare 9-digit number elsewhere never matches), an imo key or the IMO1234567
            token form for an IMO, a vessel / vessel_name / VesselName / ship /
            ship_name key for a name, a CallSign key inside a record that carries an
            mmsi key, and the MMSI / IMO / VesselName / CallSign columns of an AIS CSV
            block (a header line naming MMSI, then its rows).
  ground    'ground-identifier', /etc/corporatetraveldc/privacy-ground.list,
            `id:<identifier>` entries, matched only after a ground key (plate,
            license_plate, vehicle, vehicle_id, vin; not fleet / unit, which aviation
            prose uses).
  rail      'rail-identifier', /etc/corporatetraveldc/privacy-rail.list, `id:` entries,
            matched only after a rail key (train, train_id, trainset, consist,
            locomotive, loco, railcar).
  A MATCH in any vertical blocks (the tripwire refuses, the transcript scan reports a
  leak), each vertical only for its own fields. A missing or empty maritime / ground /
  rail file is "no entries": reported, never a failure. An unreadable one is "NOT CHECKED (<error type>)": reported loudly, still
  not a failure (only aviation is fail-closed). Values are never printed; malformed
  lines are counted, not echoed. CTDC_PRIVACY_LIST_DIR overrides the directory
  (tests: synthetic lists only).
"""
from __future__ import annotations

import hashlib
import os
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

AVIATION, MARITIME, GROUND, RAIL = "aviation", "maritime", "ground", "rail"
INDUSTRIES = (AVIATION, MARITIME, GROUND, RAIL)
KEYED_INDUSTRIES = (MARITIME, GROUND, RAIL)
LABELS = {AVIATION: "ladd-identifier", MARITIME: "maritime-identifier",
          GROUND: "ground-identifier", RAIL: "rail-identifier"}
INDUSTRY_OF_LABEL = {v: k for k, v in LABELS.items()}
LIST_KINDS = {MARITIME: ("mmsi", "imo", "name"), GROUND: ("id",), RAIL: ("id",)}
AVIATION_MIN_IDENTIFIERS = 1000          # the tripwire's fail-closed floor (push-public.sh)
LADD_SQL = "SELECT n_number FROM faa_ladd_aircraft UNION SELECT n_number FROM faa_ladd_removals"
DEFAULT_LIST_DIR = "/etc/corporatetraveldc"
NO_ENTRIES = "no entries"


def list_path(industry: str) -> Path:
    return Path(os.environ.get("CTDC_PRIVACY_LIST_DIR") or DEFAULT_LIST_DIR) / f"privacy-{industry}.list"


# ── token text (shared) ──────────────────────────────────────────────────────────
# Matching (C speed: replace / translate / split / set ops; no per-position regex):
#   1. JSON escapes (backslash n r t b f, backslash-uXXXX) become spaces of the same
#      length, so "\nN7QZK" is not glued into one token;
#   2. base64 payloads (>= 200 base64 characters with no other byte: images and
#      attachments) are blanked -- random upper-case runs there are not identifiers;
#   3. every byte outside [A-Za-z0-9-] becomes a space.
# Offsets are preserved throughout, so a redaction edits the original bytes.
_ESCAPES = (b"\\n", b"\\r", b"\\t", b"\\b", b"\\f")
_UESC = re.compile(rb"\\u[0-9a-fA-F]{4}")
_B64_TABLE = bytes(98 if (chr(b).isascii() and (chr(b).isalnum() or chr(b) in "+/=")) else 32 for b in range(256))
_TOK_TABLE = bytes(b if (chr(b).isascii() and (chr(b).isalnum() or chr(b) == "-")) else 32 for b in range(256))
B64_MIN_RUN = 200


def token_text(blob: bytes) -> bytes:
    """Same length as blob: escapes and base64 runs blanked, separators -> spaces."""
    t = blob
    for esc in _ESCAPES:
        if esc in t:
            t = t.replace(esc, b"  ")
    if b"\\u" in t:
        t = _UESC.sub(b"      ", t)
    b64 = t.translate(_B64_TABLE)
    probe = b"b" * B64_MIN_RUN
    pos = b64.find(probe)
    if pos >= 0:
        parts, last = [], 0
        while pos >= 0:
            end = b64.find(b" ", pos)
            end = len(b64) if end < 0 else end
            parts += [t[last:pos], b" " * (end - pos)]
            last = end
            pos = b64.find(probe, end)
        parts.append(t[last:])
        t = b"".join(parts)
    return t.translate(_TOK_TABLE)


def _allowed(h: bytes, allow: set[str]) -> bool:
    return hashlib.sha256(h).hexdigest() in allow


def load_allow(path: Path) -> set[str]:
    try:
        return {l.split()[0].lower() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")}
    except OSError:
        return set()


# ── maritime record context (used by both directions: maritime hits, aviation veto) ──
_MMSI_WORD = re.compile(rb"(?i)(?<![A-Za-z0-9])mmsi(?![A-Za-z0-9])")
_CSV_DELIMS = (b",", b"\t", b";", b"|")
_ROW_LEAD = re.compile(rb'[ \t"\'(]*')
RECORD_REACH = 400


def _col_kind(name: bytes) -> str | None:
    n = re.sub(rb"[^a-z0-9]", b"", name.lower())
    if n == b"mmsi":
        return "mmsi"
    if n in (b"imo", b"imonumber", b"imono"):
        return "imo"
    if n in (b"vesselname", b"shipname", b"name"):
        return "name"
    if n == b"callsign":
        return "*"
    return None


def _segment(blob: bytes, start: int, end: int) -> tuple[int, int]:
    """The CSV-ish payload of one physical line: leading blanks/quotes/parens dropped,
    cut at a literal backslash-n or a closing quote (a CSV embedded in a string)."""
    a = _ROW_LEAD.match(blob, start, end).end()
    cut = end
    for stop in (b"\\n", b'"', b"'"):
        i = blob.find(stop, a, end)
        if 0 <= i < cut:
            cut = i
    return a, cut


def _line_at(blob: bytes, pos: int) -> tuple[int, int]:
    s = blob.rfind(b"\n", 0, pos) + 1
    e = blob.find(b"\n", pos)
    return s, (len(blob) if e < 0 else e)


# a header: delimiter-separated column names (letters first), one of them MMSI
_HEADER_RX = {d: re.compile(rb"[A-Za-z_][A-Za-z0-9_]*(?:" + re.escape(d) + rb"[A-Za-z_][A-Za-z0-9_ ]*)+")
              for d in _CSV_DELIMS}


@dataclass
class AisBlock:
    start: int
    end: int
    cells: list[tuple[str, int, int]] = field(default_factory=list)   # (column kind, start, end)


HEADER_REACH = 1024      # a header is searched only around its MMSI cell (JSONL lines run to MBs)


def _header(blob: bytes, ls: int, le: int, at: int):
    lo, hi = max(ls, at - HEADER_REACH), min(le, at + HEADER_REACH)
    for d, rx in _HEADER_RX.items():
        for m in rx.finditer(blob, lo, hi):
            if m.start() <= at < m.end():
                kinds = [_col_kind(x.strip()) for x in m.group(0).split(d)]
                if "mmsi" in kinds:
                    return d, kinds
                break
    return None


def _word_hits(low: bytes, word: bytes, prefix_only: bool = False):
    """Offsets of word in a lower-cased blob (C-speed find), bounded on the left (and on
    the right unless prefix_only) by a non-alphanumeric, non-underscore byte."""
    n, w = len(low), len(word)
    i = low.find(word)
    while i >= 0:
        if (i == 0 or not _WORDCHAR[low[i - 1]]) and (prefix_only or i + w >= n or not _WORDCHAR[low[i + w]]):
            yield i
        i = low.find(word, i + 1)


_WORDCHAR = [chr(b).isascii() and (chr(b).isalnum() or chr(b) == "_") for b in range(256)]


def ais_blocks(blob: bytes) -> list[AisBlock]:
    """AIS CSV blocks: a header line whose cells are column names including MMSI, then
    the following lines carrying the same delimiter (rows of another width are skipped
    but stay inside the block). Works for a bare CSV and for one embedded line by line
    in string literals (test fixtures). Only lines naming MMSI are header candidates,
    so a large file without one costs a single regex pass."""
    out: list[AisBlock] = []
    n = len(blob)
    for at in _word_hits(blob.lower(), b"mmsi"):
        if out and at <= out[-1].end:
            continue
        ls, le = _line_at(blob, at)
        h = _header(blob, ls, le, at)
        if h is None:
            continue
        delim, cols = h
        blk = AisBlock(ls, le)
        pos = le + 1
        while pos < n:
            e = blob.find(b"\n", pos)
            e = n if e < 0 else e
            a, b = _segment(blob, pos, e)
            seg = blob[a:b]
            if delim not in seg:
                break
            parts = seg.split(delim)
            if len(parts) == len(cols):
                off = a
                for kind, cell in zip(cols, parts):
                    if kind:
                        lead = len(cell) - len(cell.lstrip())
                        v = cell.strip()
                        if v:
                            blk.cells.append((kind, off + lead, off + lead + len(v)))
                    off += len(cell) + len(delim)
            blk.end = e
            pos = e + 1
        out.append(blk)
    return out


def _record_bounds(blob: bytes, pos: int) -> tuple[int, int]:
    """A record around pos: bounded by a newline, a literal backslash-n or a JSON brace,
    within RECORD_REACH bytes either side."""
    lo = max(0, pos - RECORD_REACH)
    s = max([blob.rfind(x, lo, pos) for x in (b"\n", b"\\n", b"{")] + [lo - 1]) + 1
    hi = min(len(blob), pos + RECORD_REACH)
    ends = [i for i in (blob.find(x, pos, hi) for x in (b"\n", b"\\n", b"}")) if i >= 0]
    return s, (min(ends) if ends else hi)


def in_foreign_field(blob: bytes, text: bytes, pos: int, h: bytes, blocks: list[AisBlock]) -> bool:
    """pos sits in another vertical's field: an AIS CSV block, the value right after a
    maritime / ground / rail key, an AIS CallSign, or an IMO1234567 token."""
    if _IMO_TOKEN.fullmatch(h):
        return True
    if any(b.start <= pos <= b.end for b in blocks):
        return True
    window = text[max(0, pos - CONTEXT_WINDOW):pos]
    if any(rx.search(window) for rx in _KEY_RX.values()):
        return True
    if _CALLSIGN_KEY.search(window):
        s, e = _record_bounds(blob, pos)
        return _MMSI_WORD.search(blob, s, e) is not None
    return False


def in_maritime_record(blob: bytes, pos: int, blocks: list[AisBlock]) -> bool:
    if any(b.start <= pos <= b.end for b in blocks):
        return True
    s, e = _record_bounds(blob, pos)
    return _MMSI_WORD.search(blob, s, e) is not None


# ── aviation (LADD) ─────────────────────────────────────────────────────────────
def aviation_forms(identifiers) -> frozenset[bytes]:
    """Every whole-token form an identifier is matched in (never printed)."""
    out: set[bytes] = set()
    for ident in identifiers:
        raw = (ident or "").strip().upper()
        if not raw:
            continue
        forms = {raw}
        if len(raw) > 1 and raw[0] == "N" and raw[1].isdigit():
            forms.add(raw[1:])                      # N7QZK -> registry form 7QZK
        elif raw[0].isdigit():
            forms.add("N" + raw)                    # registry form 7QZK -> N7QZK
        for f in forms:
            if any(c.isalpha() for c in f):         # all-digit forms would hit every number
                out.add(f.encode())
    return frozenset(out)


def aviation_floor(idents: list[str], minimum: int = AVIATION_MIN_IDENTIFIERS
                   ) -> tuple[frozenset[bytes] | None, str]:
    if len(idents) < minimum:
        return None, f"LADD list has {len(idents)} identifier(s) (< {minimum}; fail closed)"
    return aviation_forms(idents), ""


# 2026-10-22 (operator): the forms without an N-prefix hit timestamps ('...:19.536Z')
# and prose ('Nextcloud MAJOR VERSION'). A SHORT form -- a registry form starting with
# a digit (7QZK) or a letters-only entry -- needs identifier context: one of these keys
# right before it, separated only by blanks (in the token text quotes, colons, '=' and
# JSON escapes are already blanks): 'registration: 7QZK', 'r=7QZK', '"reg":"7QZK"'.
AVIATION_CONTEXT = re.compile(
    rb"(?i)(?:^|[^A-Za-z0-9_])(?:tails?|reg|regs|registration|registrations|registry|aircraft|airframe|"
    rb"n-?number|callsign|call-sign|flight|ident|r|hex|icao|icao24)"
    rb"(?:\s+(?:number|no|num|id))?\s{1,12}$")
CONTEXT_WINDOW = 48
_TS_FRACTION = re.compile(rb"[0-9]+Z")


def aviation_short_form(h: bytes) -> bool:
    if b"-" in h:
        return False
    if len(h) > 1 and h[:1] == b"N" and h[1:2].isdigit():
        return False                                    # N-form: anywhere
    return h[:1].isdigit() or h.isalpha()               # registry form, or letters only


def timestamp_part(blob: bytes, pos: int, h: bytes) -> bool:
    """digits+Z right after '.' or ':' -- the fraction / seconds of an ISO-8601 time."""
    return pos > 0 and blob[pos - 1:pos] in (b".", b":") and _TS_FRACTION.fullmatch(h) is not None


def aviation_context(text: bytes, pos: int) -> bool:
    return AVIATION_CONTEXT.search(text[max(0, pos - CONTEXT_WINDOW):pos]) is not None


def _aviation_hit_strings(text: bytes, forms: frozenset[bytes], allow: set[str]) -> set[bytes]:
    toks = set(text.split())
    hits = toks & forms
    for tok in toks:
        if b"-" in tok:
            hits |= forms.intersection(tok.split(b"-"))
    return {h for h in hits if not _allowed(h, allow)}


def aviation_spans(blob: bytes, forms: frozenset[bytes] | None, allow: set[str],
                   text: bytes | None = None, blocks: list[AisBlock] | None = None) -> list[tuple[int, int]]:
    """(start, end) of every listed whole token or hyphen part, sorted, non-overlapping."""
    if not forms:
        return []
    text = token_text(blob) if text is None else text
    hits = _aviation_hit_strings(text, forms, allow)
    if not hits:
        return []
    blocks = ais_blocks(blob) if blocks is None else blocks
    n = len(text)
    spans = []
    for h in hits:
        stops = (32,) if b"-" in h else (32, 45)    # hyphenated entry: whole tokens only
        short = aviation_short_form(h)
        pos = text.find(h)
        while pos >= 0:
            end = pos + len(h)
            if ((pos == 0 or text[pos - 1] in stops) and (end == n or text[end] in stops)
                    and not timestamp_part(blob, pos, h)
                    and (not short or (aviation_context(text, pos)
                                       and not in_maritime_record(blob, pos, blocks)))
                    and not in_foreign_field(blob, text, pos, h, blocks)):
                spans.append((pos, end))
            pos = text.find(h, pos + 1)
    return _merge(spans)


# ── keyed industries (maritime, ground, rail) ───────────────────────────────────
_SUFFIX = rb"(?:\s+(?:number|no|num|id))?\s{1,12}$"
_KEYS = {
    (MARITIME, "mmsi"): rb"mmsi",
    (MARITIME, "imo"): rb"imo",
    (MARITIME, "name"): rb"vessel|vessel\s?name|vesselname|ship|ship\s?name|shipname",
    (GROUND, "id"): rb"plate|licen[cs]e\s?plate|vehicle|vin",
    (RAIL, "id"): rb"train|trainset|consist|locomotive|loco|rail\s?car|railcar",
}
_KEY_RX = {k: re.compile(rb"(?i)(?:^|[^A-Za-z0-9_])(?:" + v + rb")" + _SUFFIX) for k, v in _KEYS.items()}
_CALLSIGN_KEY = re.compile(rb"(?i)(?:^|[^A-Za-z0-9_])(?:call\s?sign|callsign)" + _SUFFIX)
_IMO_TOKEN = re.compile(rb"IMO[0-9]{7}")
_VALID = {("mmsi"): re.compile(r"[0-9]{9}"), ("imo"): re.compile(r"[0-9]{7}")}


def normalize(v: bytes) -> bytes:
    return b" ".join(v.upper().translate(_TOK_TABLE).split())


@dataclass
class KeyedList:
    industry: str
    entries: dict[str, frozenset[bytes]] | None      # kind -> normalized values; None = unreadable
    status: str
    _rx: dict | None = None
    _kinds: dict[bytes, set[str]] | None = None

    @property
    def count(self) -> int:
        return sum(len(v) for v in (self.entries or {}).values())

    def compiled(self):
        if self._rx is None and self.count:
            kinds: dict[bytes, set[str]] = {}
            for kind, vals in self.entries.items():
                for v in vals:
                    kinds.setdefault(v, set()).add(kind)
                    if kind == "imo":
                        kinds.setdefault(b"IMO" + v, set()).add("imo-token")
            # first word -> patterns of the entries starting with it (tried only where the
            # first word occurs: a token-set intersection finds those at C speed)
            by_first: dict[bytes, list[re.Pattern]] = {}
            for v in sorted(kinds, key=len, reverse=True):
                by_first.setdefault(v.split()[0], []).append(
                    re.compile(rb" +".join(re.escape(w) for w in v.split()) + rb"(?![A-Za-z0-9-])"))
            self._rx = by_first
            self._kinds = kinds
        return self._rx, self._kinds


def parse_list(text: str, industry: str) -> tuple[dict[str, frozenset[bytes]], int]:
    """(kind -> values, malformed-line count). Never echoes a value."""
    kinds = LIST_KINDS[industry]
    out: dict[str, set[bytes]] = {k: set() for k in kinds}
    bad = 0
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        kind, sep, val = line.partition(":")
        kind, val = kind.strip().lower(), val.strip()
        if not sep or kind not in kinds or not val or (kind in _VALID and not _VALID[kind].fullmatch(val)):
            bad += 1
            continue
        nv = normalize(val.encode())
        if nv:
            out[kind].add(nv)
        else:
            bad += 1
    return {k: frozenset(v) for k, v in out.items()}, bad


def load_keyed_list(industry: str, path: Path | None = None) -> KeyedList:
    path = list_path(industry) if path is None else path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return KeyedList(industry, {}, NO_ENTRIES)
    except OSError as e:
        return KeyedList(industry, None, f"NOT CHECKED (list unreadable: {type(e).__name__})")
    entries, bad = parse_list(text, industry)
    n = sum(len(v) for v in entries.values())
    status = f"{n} entr{'y' if n == 1 else 'ies'}" if n else NO_ENTRIES
    if bad:
        status += f", {bad} malformed line(s) ignored"
    return KeyedList(industry, entries, status)


def _entry_matches(text: bytes, by_first: dict[bytes, list[re.Pattern]]):
    """Matches of listed entries in token text, case-insensitive, whole tokens."""
    up = text.upper()
    firsts = set(up.split()).intersection(by_first)
    found = []
    for w in firsts:
        i = up.find(w)
        while i >= 0:
            if i == 0 or up[i - 1] == 32:
                for rx in by_first[w]:
                    m = rx.match(up, i)
                    if m:
                        found.append(m)
                        break
            i = up.find(w, i + 1)
    found.sort(key=lambda m: m.start())
    return found


def keyed_spans(blob: bytes, kl: KeyedList, allow: set[str], text: bytes | None = None,
                blocks: list[AisBlock] | None = None) -> list[tuple[int, int]]:
    rx, kinds = kl.compiled()
    if rx is None:
        return []
    text = token_text(blob) if text is None else text
    spans = []
    for m in _entry_matches(text, rx):
        v = normalize(m.group(0))
        if _allowed(v, allow):
            continue
        pos = m.start()
        window = text[max(0, pos - CONTEXT_WINDOW):pos]
        ks = kinds.get(v, set())
        ok = "imo-token" in ks and kl.industry == MARITIME
        ok = ok or any(_KEY_RX[(kl.industry, k)].search(window) for k in ks if (kl.industry, k) in _KEY_RX)
        if not ok and kl.industry == MARITIME and _CALLSIGN_KEY.search(window):
            s, e = _record_bounds(blob, pos)
            ok = _MMSI_WORD.search(blob, s, e) is not None
        if ok:
            spans.append((m.start(), m.end()))
    if kl.industry == MARITIME:
        blocks = ais_blocks(blob) if blocks is None else blocks
        for b in blocks:
            for kind, s, e in b.cells:
                v = normalize(blob[s:e])
                if kind == "imo" and v.startswith(b"IMO"):
                    v = v[3:].strip()
                vk = kinds.get(v, set()) - {"imo-token"}
                if vk and (kind == "*" or kind in vk) and not _allowed(v, allow):
                    spans.append((s, e))
    return _merge(spans)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans.sort()
    out, last = [], -1
    for a, b in spans:
        if a >= last:
            out.append((a, b))
            last = b
    return out


# ── all industries together ──────────────────────────────────────────────────────
@dataclass
class PrivacyLists:
    aviation: frozenset[bytes] | None
    aviation_status: str
    keyed: dict[str, KeyedList]

    def summary(self) -> str:
        parts = [f"{AVIATION}: " + (f"{self.aviation_status}" if self.aviation is not None
                                    else f"NOT CHECKED ({self.aviation_status})")]
        for ind in KEYED_INDUSTRIES:
            kl = self.keyed.get(ind)
            parts.append(f"{ind}: {kl.status if kl else NO_ENTRIES}")
        return "; ".join(parts)


def load_keyed_lists() -> dict[str, KeyedList]:
    return {ind: load_keyed_list(ind) for ind in KEYED_INDUSTRIES}


def spans_by_industry(blob: bytes, lists: PrivacyLists, allow: set[str]) -> dict[str, list[tuple[int, int]]]:
    """industry -> spans. One token text and one AIS-block parse per blob."""
    active = [kl for kl in lists.keyed.values() if kl.count]
    if not lists.aviation and not active:
        return {}
    text = token_text(blob)
    blocks = ais_blocks(blob)
    out = {}
    if lists.aviation:
        s = aviation_spans(blob, lists.aviation, allow, text, blocks)
        if s:
            out[AVIATION] = s
    for kl in active:
        s = keyed_spans(blob, kl, allow, text, blocks)
        if s:
            out[kl.industry] = s
    return out


def line_numbers(blob: bytes, spans: list[tuple[int, int]]) -> list[int]:
    nl = [m.start() for m in re.finditer(rb"\n", blob)] if spans else []
    return sorted({bisect_left(nl, a) + 1 for a, _ in spans})


# ── suspected identifiers in a vertical with no list (operator, 2026-10-23) ─────────
# A vertical with no list (missing, empty or unreadable) must not silently pass what
# its own FIELDS carry: a 9-digit value in an MMSI field / column, a 7-digit IMO with
# the IMO prefix or in an IMO field / column, a vessel name in a VesselName / ship_name
# field or column, an id carrying a digit in a ground / rail id field. Each is a
# "suspected <vertical> identifier -- no list, needs review" finding: location only in
# every automated output; the operator sees the value only in --review, in their own
# terminal, with a SUGGESTED list entry to add BY HAND (nothing ever appends to
# /etc/corporatetraveldc/privacy-*.list). Not sensitive: append the review's
# "<sha256>  <reason>" line to SUSPECT_ALLOW (hash of "<vertical>:<normalized value>",
# never the value), and it stops re-triggering. Fields only (key + ':' / '=' or a CSV
# column), never prose, except the IMO-prefixed number, which is self-describing.
SUSPECT_ALLOW = Path(__file__).with_name("privacy-review-allow.sha256")
_SEP = rb"[\"']?[ \t]*[:=][ \t]*[\"']?"
_PLACEHOLDER = {b"NULL", b"NONE", b"STR", b"TEXT", b"STRING", b"TRUE", b"FALSE", b"UNKNOWN", b"N A", b"NA",
                b"TBD", b"ANY", b"OPTIONAL", b"VARCHAR", b"INT", b"INTEGER"}
# (kind, where, keywords, pattern). Matching runs on blob.lower() (same offsets): each
# keyword is found with bytes.find and the pattern is tried only there -- a per-position
# regex over a 500 MB transcript was ~3 MB/s.
_SUSPECT_RULES = {
    MARITIME: [
        ("mmsi", "mmsi field", (b"mmsi",), re.compile(rb"mmsi" + _SEP + rb"([0-9]{9})(?![0-9])")),
        ("imo", "IMO prefix / field", (b"imo",), re.compile(
            rb"imo(?:[ _]?(?:number|no))?(?:" + _SEP + rb"|[ \t]?)([0-9]{7})(?![0-9])")),
        ("name", "vessel name field", (b"vessel", b"ship"), re.compile(
            rb"(?:vessel[ _]?name|ship[ _]?name)[\"']?[ \t]*[:=][ \t]*"
            rb"(?:\"([^\"\n\\]{2,60})\"|'([^'\n\\]{2,60})'|([a-z0-9][^,;\n\"'}\\]{1,59}))")),
    ],
    GROUND: [
        ("id", "ground id field", (b"licen", b"plate", b"vehicle", b"vin"), re.compile(
            rb"(?:licen[cs]e[ _]?plate|plate|vehicle[ _]?id|vin)" + _SEP
            + rb"([a-z0-9][a-z0-9-]{1,20})(?![a-z0-9-])")),
    ],
    RAIL: [
        ("id", "rail id field", (b"train", b"loco", b"rail", b"consist"), re.compile(
            rb"(?:train[ _]?(?:id|number|no|num)|trainset|locomotive|loco|railcar|rail[ _]car|consist)"
            + _SEP + rb"([a-z0-9][a-z0-9-]{0,20})(?![a-z0-9-])")),
    ],
}
_CSV_SUSPECT = {"mmsi": re.compile(rb"[0-9]{9}"), "imo": re.compile(rb"(?:IMO ?)?[0-9]{7}", re.I),
                "name": re.compile(rb".*[A-Za-z].*")}


@dataclass
class Suspect:
    industry: str
    kind: str
    where: str           # the kind of field it sat in (never the value)
    start: int
    end: int

    def value(self, blob: bytes) -> bytes:
        v = normalize(blob[self.start:self.end])
        if self.kind == "imo" and v.startswith(b"IMO"):
            v = v[3:].strip()
        return v


def suspect_hash(industry: str, value: bytes) -> str:
    return hashlib.sha256(industry.encode() + b":" + normalize(value)).hexdigest()


def _plausible(kind: str, v: bytes) -> bool:
    if not v or v in _PLACEHOLDER or v[:1] in (b"{", b"$", b"<", b"%"):
        return False
    if v.isdigit() and (len(set(v)) == 1 or v in b"0123456789"):
        return False                                    # format placeholders: 000000000, 1234567
    if kind == "name":
        return any(c.isalpha() for c in v.decode("ascii", "replace"))
    if kind == "id":
        return any(c.isdigit() for c in v.decode("ascii", "replace"))
    return True


def suspects(blob: bytes, lists: "PrivacyLists", allow: set[str],
             blocks: list[AisBlock] | None = None) -> list[Suspect]:
    """Suspected identifiers in the fields of every vertical that has NO entries
    (missing, empty or unreadable list). Sorted, one per span."""
    bare = [ind for ind in KEYED_INDUSTRIES if not (lists.keyed.get(ind) and lists.keyed[ind].count)]
    out: dict[tuple[int, int], Suspect] = {}
    if not bare:
        return []
    low = blob.lower()
    for ind in bare:
        for kind, where, words, rx in _SUSPECT_RULES[ind]:
            for w in words:
                for at in _word_hits(low, w, prefix_only=True):
                    m = rx.match(low, at)
                    if m is None:
                        continue
                    g = next(i for i in range(1, (m.lastindex or 1) + 1) if m.group(i) is not None)
                    s = Suspect(ind, kind, where, m.start(g), m.end(g))
                    v = s.value(blob)
                    if blob[s.start:s.start + 1] not in (b"{", b"$", b"<", b"%") and _plausible(kind, v) and suspect_hash(ind, v) not in allow:
                        out.setdefault((s.start, s.end), s)
    if MARITIME in bare:
        blocks = ais_blocks(blob) if blocks is None else blocks
        for b in blocks:
            for kind, a, e in b.cells:
                rx = _CSV_SUSPECT.get(kind)
                if rx is None or not rx.fullmatch(blob[a:e].strip()):
                    continue
                s = Suspect(MARITIME, kind, f"AIS CSV {kind} column", a, e)
                v = s.value(blob)
                if _plausible(kind, v) and suspect_hash(MARITIME, v) not in allow:
                    out.setdefault((a, e), s)
    return [out[k] for k in sorted(out)]


def suggested_entry(s: Suspect, blob: bytes, location: str, date: str) -> str:
    """The list line the operator may add by hand (review mode only: carries the value)."""
    kind = s.kind if s.industry == MARITIME else "id"
    return (f"{kind}:{s.value(blob).decode('utf-8', 'replace')}  # seen in {s.where} at {location}; "
            f"suggested by review {date}")
