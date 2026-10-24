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
  * (2026-10-20) every other credential file the platform holds (SOURCES below):
    the other /etc/corporatetraveldc/*secrets*.env files (Postgres superuser
    passwords for both instances, the demo session secret, ntfy, the SDR feeder
    keys), demo.env's role password, /etc/corporatetraveldc/*.token (ntfy root,
    Cloudflare honeypot) and every file in the operator's ~/.secrets (Cloudflare,
    ntfy FCM, Proton Bridge, Tailscale node key, GitHub PAT, the demo Q-registry
    key, SSH private keys ...) plus the tailnet TLS private keys (~/*.ts.net.key).
    Env files other than dispatch-secrets.env contribute only secret-NAMED keys
    (SECRET_KEY_NAME: no coordinates, hosts or model names). A binary key file is
    matched in its hex and base64 forms; a multi-line key file (PEM / OpenSSH) by
    up to KEY_BODY_LINES of its base64 body lines. Public material is skipped
    (SOURCE_SKIP: *.pub, *.pub.asc, *.crt, the signing fingerprint files). Values
    are read only inside this process, by path; a file this account cannot read
    is reported by NAME as "not checked" (the hourly root run reads root-only
    files), never silently passed;
  * token shapes that are secrets wherever they appear: platform API tokens
    (ctdc_<user>_<32>), gateway access/refresh tokens (dga_/dgr_), and
    "Authorization: Bearer <token>" / "X-Board-Key: <token>" header values;
  * (2026-10-20) vendor credential shapes: private-key blocks (PEM, OpenSSH, PGP;
    header plus a base64 body, redacted through the END line), GitHub tokens
    (ghp_/gho_/ghu_/ghs_/ghr_, github_pat_), Anthropic sk-ant-, Tailscale tskey-
    and private node keys (privkey:/nlpriv:), ntfy access tokens (tk_);
  * registration-bearing log-line shapes (2026-10-18): the identifier in the old
    FDPS "fdps: wrote flight_event for <id> (" journal line;
  * LADD identifiers (2026-10-19, Gate-1b), pattern class 'ladd-identifier':
    the production filter's own list (faa_ladd_aircraft UNION faa_ladd_removals,
    the same union push-public.sh pipes into the tripwire; one class, no tier),
    loaded in-process through common.db.conn() ONLY when run with the dispatch
    env (scripts/with-dispatch-env.sh). Never printed, logged or written.
    2026-10-20: "with the dispatch env" means the Postgres connection settings
    common.db.conn() uses are set (DISPATCH_PG_HOST / _DB / _USER; DISPATCH_ENV_KEYS),
    no longer DISPATCH_DB_BACKEND / DISPATCH_DB (the SQLite path is retired; dropping
    DISPATCH_DB would have switched the class off silently). The operator-side
    callers (pre-push, push-public.sh, relay-preflight.sh) run it through
    scripts/with-dispatch-env.sh with --require-ladd, so an unreadable list fails
    the push. The hourly ROOT unit cannot use common.db (the installed copy has no
    src/, and root never imports operator-writable code) and root's python has no
    Postgres driver: scripts/leak-guard/agent-leak-scan.sh pipes the same union out
    of the operator's corporatetraveldc-pgsql container (the push-public.sh tripwire
    pattern, runuser as the operator) into --ladd-stdin, also with --require-ladd. Whole
    upper-case tokens (letters, digits, hyphens; each hyphen part too, as the
    tripwire matches), JSON escapes (backslash-n, -t, -uXXXX) treated as separators;
    each entry in its N-form and its registry form without the N (N7QZK / 7QZK),
    callsign / flight-ID strings as listed. An all-digit registry form is not
    matched (it would hit every number). Coincidences allowed by hash in
    ladd-public-allow.sha256, as the tripwire. Redacted to [ladd-redacted].
    With the dispatch env and the list unreadable (DB down, table empty or under
    LADD_MIN_IDENTIFIERS), the class is NOT CHECKED: said loudly, exit 3 on scan
    and redact (never a silent pass). Without the dispatch env it is NOT CHECKED
    with a loud warning, exit 3 only under --require-ladd.
    2026-10-22 (operator): an identifier WITHOUT the N-prefix (registry form 7QZK,
    or a letters-only entry) matches only in identifier context (tail / reg /
    registration / registry / aircraft / callsign / flight / r= / hex / icao, or a
    JSON value of such a key), and a digits+Z token after '.' or ':' (the fraction
    of an ISO-8601 timestamp, 13:02:19.536Z) never matches. False positives were
    timestamps and capitalised prose words. N-forms (N7QZK) and letters-first forms
    carrying a digit (callsigns QQZ901, marks Q1ZT7) match as before.
    2026-10-23 (operator, industry aware): the matching lives in privacy_lists.py
    (shared with the public tripwire). A short form inside a maritime record (an AIS
    CSV block, a record with an mmsi key) never matches as aviation.
  * maritime / ground / rail privacy lists (2026-10-23), classes 'maritime-identifier',
    'ground-identifier', 'rail-identifier': /etc/corporatetraveldc/privacy-<industry>.list,
    matched only in their own keyed context (privacy_lists.py). Redacted to
    [<industry>-redacted]. Absent or empty = "no entries", never a failure.
  * receiver coordinates (2026-10-22, operator), class 'receiver-coords': the feeder
    receiver's latitude/longitude, read in-process (never printed) from the feeder
    env files (COORD_SOURCES: ultrafeeder READSB_LAT/READSB_LON and the map centre,
    piaware / opensky / planefinder / airnavradar LAT/LONG where readable). Matched
    BY VALUE, not by a generic lat/lon shape: a decimal with 3+ places whose integer
    part is the receiver's and whose value, rounded or truncated to 3 or 4 places,
    equals it, with or without sign / leading + / trailing degree sign (--lat x,
    READSB_LAT=x, +x°, a bare "lat, lon" pair). Redacted to [coords-redacted]. No value
    readable: the class is NOT CHECKED, said loudly; exit 3 only under --require-coords.
  * Feeder UUIDs (ultrafeeder UUID, piaware FEEDER_ID) are PUBLIC identifiers, not
    secrets (operator decision 2026-10-22): NOT_SECRET_KEYS keeps them out of the
    value set from every env file. Sharing keys, FR24KEY, OPENSKY_SERIAL stay values.

Default roots: ~/.claude/projects and /tmp/claude-<uid> (session transcripts and
tool outputs). Agent accounts' homes are only readable by root: --agents scans every
ctdc-agent-* account's Claude Code and Codex transcripts, run hourly as root from the
installed copy (corporatetraveldc-agent-leak-scan.timer; scripts/install-root-copies.sh).

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
  --require-ladd   exit 3 whenever the LADD class could not be checked
  --ladd-stdin     read the LADD list from stdin (one per line) instead of the database
                   (2026-10-20, the root unit; implies --require-ladd)
  --require-coords exit 3 whenever the receiver-coords class could not be checked
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
# 2026-10-15: single-value secret files outside the env file (one value per file,
# named by the file). The healthchecks.io ping URL lives here: anyone holding it can
# fake "alive". Its last path segment (the check id) is matched on its own too, so a
# copy without the scheme is caught. Colon-separated override for tests.
# The default names the OPERATOR's home by account, not "~": the hourly root run
# (corporatetraveldc-agent-leak-scan) would otherwise look in /root and never load it
# (parity audit 2026-10-15).
def _operator_home() -> str:
    try:
        import pwd
        return pwd.getpwnam("corporatetraveldc").pw_dir
    except (KeyError, ImportError):
        return os.path.expanduser("~")


EXTRA_FILES = [Path(p).expanduser() for p in os.environ.get(
    "LEAK_GUARD_EXTRA_FILES", f"{_operator_home()}/.secrets/healthchecks_ping.url").split(":") if p]
MIN_LEN = 12

# ── Every other credential file (2026-10-20, freeze bug fix: coverage) ─────────
# Colon-separated globs; LEAK_GUARD_SOURCES overrides (tests set it empty: a test
# never loads a real secret file). Files already loaded above are skipped.
def _default_sources() -> str:
    home = _operator_home()
    return ":".join(("/etc/corporatetraveldc/*secrets*.env", "/etc/corporatetraveldc/demo.env",
                     "/etc/corporatetraveldc/*.token", f"{home}/.secrets/*", f"{home}/*.ts.net.key"))


SOURCES = [p for p in os.environ.get("LEAK_GUARD_SOURCES", _default_sources()).split(":") if p]
# Public material and flag files are never values (fingerprints and public keys are tracked in the repo).
SOURCE_SKIP = re.compile(r"(?:\.pub|\.pub\.asc|\.crt|signing\.env|signing-pin|tailnet-disable|\.example|\.template|\.bak[-.\w]*)$")
# In env files other than dispatch-secrets.env only secret-NAMED keys are values (a feeder
# file's LAT/LONG, demo.env's model name or URLs are configuration).
SECRET_KEY_NAME = re.compile(r"(?i)(PASS|SECRET|TOKEN|KEY|SHARECODE|SERIAL|ACCOUNT|SID|VERIFY_SERVICE)")
# 2026-10-22 (operator): feeder UUIDs are PUBLIC identifiers (they appear on the
# aggregators' public stats pages), never values -- in ANY env file, dispatch-secrets.env
# included. Exact key names; every other key keeps its treatment.
NOT_SECRET_KEYS = frozenset({"UUID", "ULTRAFEEDER_UUID", "FEEDER_ID", "PIAWARE_FEEDER_ID"})
# Secret-named keys whose values are shorter than MIN_LEN but are still passwords: the
# OpenSky feeder serial is the feeder's credential (11 characters). {name: minimum length}.
SHORT_SECRET_KEYS = {"OPENSKY_SERIAL": 8}
KEY_BODY_LINES = 3          # base64 body lines taken per multi-line key file (scan cost stays bounded)
KEY_BODY_MIN = 32
NOT_CHECKED: list[str] = []  # source files this account could not read (names only); set by load_secret_values
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
    # 2026-10-20 (credential coverage): vendor shapes the git hooks knew and the transcripts did not.
    # A private-key block needs a real base64 body after its header (a header named in prose is not
    # key material) and is matched -- and redacted -- through its END line (or the end of the body run).
    ("private-key-block", re.compile(
        rb"-----BEGIN (?P<k>(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----(?:\s|\\[rn])*"
        rb"(?:[A-Za-z-]+: [^\r\n\\]*(?:\s|\\[rn])+){0,4}[A-Za-z0-9+/=]{40,}"
        rb"[A-Za-z0-9+/=\s\\]*?(?:-----END (?P=k)-----|(?![A-Za-z0-9+/=\s\\]))")),
    ("github-token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("github-pat", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{50,}")),
    ("anthropic-key", re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("tailscale-key", re.compile(rb"\btskey-[A-Za-z0-9-]{20,}")),
    ("tailscale-private-key", re.compile(rb"\b(?:privkey|nlpriv):[0-9a-f]{64}\b")),
    ("ntfy-token", re.compile(rb"\btk_[a-z0-9]{29}\b")),
] + [
    # 2026-10-18 (leak hygiene): registration-bearing log-line shapes. Only the
    # identifier is matched (and redacted); the rest of the line stays readable.
    # fdps_parser logged "fdps: wrote flight_event for <callsign or GUFI> (<o> -> <d>,
    # source=..)" at INFO on every message until 2026-10-18 -- for GA flights the
    # callsign IS the tail. The slot is redacted whatever it holds (an airline callsign
    # cannot be told from a registration by shape alone).
    (label, re.compile(rx)) for label, rx in (
        ("fdps-log-registration", rb"(?<=fdps: wrote flight_event for )[A-Za-z0-9.:-]{2,64}(?= \()"),
    )
]
PLACEHOLDER = re.compile(rb"^(?:0+|[xX]+|REDACTED.*)$")

# ── LADD identifiers (2026-10-19, Gate-1b) and the other industries (2026-10-23) ──
# 2026-10-23 (operator: "the LADD / IMO checks should be industry aware"): the matching
# lives in privacy_lists.py next to this file (shared with the public tripwire; the root
# unit's installed copy sits beside it in /usr/local/libexec/ctdc). Aviation keeps the
# 2026-10-22 context rule and adds one veto: a short form inside a maritime record (an
# AIS CSV block, a record carrying an mmsi key) is not an aircraft. Maritime / ground /
# rail lists (/etc/corporatetraveldc/privacy-<industry>.list) match only in their own
# keyed context, report as '<industry>-identifier', and never fail the scan when absent.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import privacy_lists as pl  # noqa: E402

LADD_LABEL = pl.LABELS[pl.AVIATION]
LADD_REPLACEMENT = b"[ladd-redacted]"
LADD_MIN_IDENTIFIERS = pl.AVIATION_MIN_IDENTIFIERS
LADD_ALLOW = Path(__file__).with_name("ladd-public-allow.sha256")
LADD_SQL = pl.LADD_SQL
B64_MIN_RUN = pl.B64_MIN_RUN
# keyed industries (maritime, ground, rail): loaded by main(); empty = "no entries"
KEYED: dict[str, "pl.KeyedList"] = {}
# 2026-10-23 (operator): suspected identifiers in the fields of a vertical with NO list
# (privacy_lists.suspects): path -> "suspected-<industry>-identifier" -> count, filled by
# scan(). Reported loudly by main() as needing review, by path and count only (never a
# value); not redacted and not an exit status of their own -- the review happens in the
# public tripwire's --review, and the transcript is not the place to decide it.
REVIEW: dict[Path, dict[str, int]] = {}


def _industry_note(label: str) -> str:
    return f" [{pl.INDUSTRY_OF_LABEL[label]}]" if label in pl.INDUSTRY_OF_LABEL else ""


# 2026-10-20: the settings common.db.conn() / _pg_conninfo() actually connect with
# (config/dispatch.env). Keyed on these, not DISPATCH_DB_BACKEND / DISPATCH_DB: the SQLite
# path is retired, and removing DISPATCH_DB must not switch the LADD class off silently.
DISPATCH_ENV_KEYS = ("DISPATCH_PG_HOST", "DISPATCH_PG_DB", "DISPATCH_PG_USER")


def _dispatch_env() -> bool:
    return any(os.environ.get(k) for k in DISPATCH_ENV_KEYS)


ladd_forms = pl.aviation_forms


def _ladd_from_db() -> list[str]:
    """The production filter's list through common.db.conn(). Raises on any failure."""
    src = os.environ.get("LEAK_GUARD_SRC") or str(Path(__file__).resolve().parents[2] / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from common import db  # noqa: E402 -- only with the dispatch env
    with db.conn() as c:
        return [r["n_number"] for r in c.execute(LADD_SQL).fetchall()]


def load_ladd_identifiers() -> tuple[frozenset[bytes] | None, str]:
    """(match set, "") or (None, reason). The reason never carries a value: a DB
    error is named by its type only."""
    if not _dispatch_env():
        return None, "no dispatch env (run through scripts/with-dispatch-env.sh)"
    try:
        idents = _ladd_from_db()
    except Exception as e:  # noqa: BLE001 -- any failure: the class is not checked
        return None, f"LADD list unreadable ({type(e).__name__})"
    return _ladd_floor(idents)


def _ladd_floor(idents: list[str]) -> tuple[frozenset[bytes] | None, str]:
    return pl.aviation_floor(idents, LADD_MIN_IDENTIFIERS)


def load_ladd_from_stream(stream) -> tuple[frozenset[bytes] | None, str]:
    """2026-10-20 (--ladd-stdin, the root unit): one identifier per line on stdin, piped
    from the database by agent-leak-scan.sh; never written anywhere. Same floor."""
    try:
        raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
    except Exception as e:  # noqa: BLE001
        return None, f"LADD stream unreadable ({type(e).__name__})"
    if isinstance(raw, str):
        raw = raw.encode()
    idents = [l.decode("utf-8", "replace").strip() for l in raw.splitlines()]
    return _ladd_floor([i for i in idents if i])


def _ladd_allow() -> set[str]:
    return pl.load_allow(LADD_ALLOW)


def ladd_spans(blob: bytes, ladd: frozenset[bytes] | None, allow: set[str] | None = None) -> list[tuple[int, int]]:
    """(start, end) of every aviation hit, sorted, non-overlapping (privacy_lists.aviation_spans)."""
    if not ladd:
        return []
    return pl.aviation_spans(blob, ladd, _ladd_allow() if allow is None else allow)


def keyed_spans(blob: bytes, keyed: dict | None = None, allow: set[str] | None = None
                ) -> dict[str, list[tuple[int, int]]]:
    """label -> spans for the maritime / ground / rail lists (empty lists: nothing)."""
    keyed = KEYED if keyed is None else keyed
    active = [kl for kl in keyed.values() if kl.count]
    if not active:
        return {}
    allow = _ladd_allow() if allow is None else allow
    text, blocks = pl.token_text(blob), pl.ais_blocks(blob)
    out = {}
    for kl in active:
        s = pl.keyed_spans(blob, kl, allow, text, blocks)
        if s:
            out[pl.LABELS[kl.industry]] = s
    return out


def _replace_spans(blob: bytes, spans: list[tuple[int, int, bytes]]) -> bytes:
    if not spans:
        return blob
    out, last = [], 0
    for a, b, rep in sorted(spans):
        if a < last:
            continue
        out += [blob[last:a], rep]
        last = b
    out.append(blob[last:])
    return b"".join(out)


def redact_ladd(blob: bytes, ladd: frozenset[bytes] | None, keyed: dict | None = None) -> bytes:
    spans = [(a, b, LADD_REPLACEMENT) for a, b in ladd_spans(blob, ladd)]
    for label, ss in keyed_spans(blob, keyed).items():
        rep = f"[{pl.INDUSTRY_OF_LABEL[label]}-redacted]".encode()
        spans += [(a, b, rep) for a, b in ss]
    return _replace_spans(blob, spans)


# ── Receiver coordinates (2026-10-22, operator) ────────────────────────────────
COORD_LABEL = "receiver-coords"
COORD_REPLACEMENT = b"[coords-redacted]"
COORD_PLACES = (3, 4)          # matched at 3 and 4 decimal places (rounded or truncated)
_ETC = "/etc/corporatetraveldc"
# "path|KEY,KEY;path|KEY,KEY". LEAK_GUARD_COORD_SOURCES overrides (tests: synthetic file,
# or empty = class NOT CHECKED). The first file is the receiver's own (ultrafeeder); the
# others are the same receiver as each feeder knows it, loaded when readable.
_COORD_DEFAULT = ";".join((
    f"{_ETC}/ultrafeeder-secrets.env|READSB_LAT,READSB_LON,TAR1090_DEFAULTCENTERLAT,TAR1090_DEFAULTCENTERLON",
    f"{_ETC}/piaware-secrets.env|LAT,LONG",
    f"{_ETC}/opensky-secrets.env|LAT,LONG",
    f"{_ETC}/planefinder-secrets.env|LAT,LONG",
    f"{_ETC}/airnavradar-secrets.env|LAT,LONG",
))


def _coord_sources() -> list[tuple[Path, set[str]]]:
    out = []
    for item in os.environ.get("LEAK_GUARD_COORD_SOURCES", _COORD_DEFAULT).split(";"):
        path, _, keys = item.strip().partition("|")
        if path:
            out.append((Path(path), {k.strip() for k in keys.split(",") if k.strip()}))
    return out


def _decimal(v: bytes):
    from decimal import Decimal, InvalidOperation
    try:
        d = abs(Decimal(v.decode("ascii").strip().lstrip("+")))
    except (InvalidOperation, UnicodeDecodeError, ValueError):
        return None
    return d if d.is_finite() and d <= 180 else None


def _quant(d, places: int):
    """(rounded half-up, truncated) to places."""
    from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
    q = Decimal(1).scaleb(-places)
    return d.quantize(q, ROUND_HALF_UP), d.quantize(q, ROUND_DOWN)


def load_receiver_coords():
    """(frozenset of absolute coordinate Decimals, "") or (None, reason). Never printed;
    the reason names files and key names only."""
    vals, unreadable = set(), []
    for path, keys in _coord_sources():
        try:
            raw = path.read_bytes()
        except OSError as e:
            unreadable.append(f"{path.name} ({type(e).__name__})")
            continue
        for line in raw.splitlines():
            k, sep, v = line.strip().partition(b"=")
            if not sep or _env_key(k) not in keys:
                continue
            d = _decimal(v.strip().strip(b"'\""))
            if d is not None and d != 0 and -d.as_tuple().exponent >= 3:
                vals.add(d)
    if not vals:
        why = "no receiver coordinate readable" + (f": {', '.join(unreadable)}" if unreadable
                                                    else " (no coordinate source configured)")
        return None, why
    return frozenset(vals), ""


def _coord_regex(coords) -> re.Pattern:
    ints = sorted({str(int(d)).encode() for d in coords}, key=len, reverse=True)
    # sign / leading +, the integer part of a receiver coordinate, 3+ places, an optional
    # degree sign (raw UTF-8, Latin-1 or JSON-escaped). Not glued to a longer number.
    return re.compile(rb"(?<![0-9.])[+-]?(?:" + b"|".join(re.escape(i) for i in ints)
                      + rb")\.[0-9]{3,}(?![0-9]|\.[0-9])(?:\xc2\xb0|\xb0|\\u00[bB]0)?")


def coord_spans(blob: bytes, coords) -> list[tuple[int, int]]:
    if not coords:
        return []
    targets = {p: set() for p in COORD_PLACES}
    for d in coords:
        for p in COORD_PLACES:
            targets[p].update(_quant(d, p))
    spans = []
    for m in _coord_regex(coords).finditer(blob):
        num = re.match(rb"[+-]?[0-9]+\.[0-9]+", m.group(0)).group(0)
        t = _decimal(num)
        if t is None:
            continue
        places = len(num.split(b".")[1])
        if any(p <= places and targets[p].intersection(_quant(t, p)) for p in COORD_PLACES):
            spans.append(m.span())
    return spans


def redact_coords(blob: bytes, coords) -> bytes:
    spans = coord_spans(blob, coords)
    if not spans:
        return blob
    out, last = [], 0
    for a, b in spans:
        out += [blob[last:a], COORD_REPLACEMENT]
        last = b
    out.append(blob[last:])
    return b"".join(out)


def load_secret_values(path: Path = SECRETS_ENV) -> dict[str, list[bytes]]:
    """{NAME: [raw value, JSON-escaped value]} -- never printed."""
    out: dict[str, list[bytes]] = {}
    try:
        raw = path.read_bytes()
    except OSError:
        raw = b""                      # still load the single-value files below
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(b"#") or b"=" not in line:
            continue
        k, _, v = line.partition(b"=")
        v = v.strip().strip(b"'\"")
        if len(v) < MIN_LEN or v in NOT_SECRET or _env_key(k) in NOT_SECRET_KEYS:
            continue
        forms = [v]
        esc = json.dumps(v.decode("utf-8", "replace"))[1:-1].encode()
        if esc != v:
            forms.append(esc)
        out[k.decode("utf-8", "replace")] = forms
    for f in EXTRA_FILES:
        try:
            v = f.read_bytes().strip()
        except OSError:
            continue
        if len(v) < MIN_LEN or b"\n" in v:
            continue
        forms = [v]
        tail = v.rstrip(b"/").rsplit(b"/", 1)[-1]
        if tail != v and len(tail) >= MIN_LEN:
            forms.append(tail)
        out[f"file:{f.name}"] = forms
    seen = {_real(path)} | {_real(f) for f in EXTRA_FILES}
    NOT_CHECKED[:] = []
    for name, forms in _load_sources(SOURCES, seen, NOT_CHECKED):
        have = {x for fs in out.values() for x in fs}
        forms = [x for x in forms if x not in have]      # one value, one name (counts stay honest)
        if forms:
            out[name] = forms
    return out


def _real(p: Path) -> str:
    return os.path.realpath(p)


def _forms(v: bytes) -> list[bytes]:
    forms = [v]
    esc = json.dumps(v.decode("utf-8", "replace"))[1:-1].encode()
    if esc != v:
        forms.append(esc)
    return forms


def _value_forms(raw: bytes) -> list[bytes]:
    """Forms a single-value or key file is matched in (never printed)."""
    import base64
    v = raw.strip()
    if not v:
        return []
    try:
        printable = all(c.isprintable() or c in "\r\n\t" for c in v.decode("ascii"))
    except UnicodeDecodeError:
        printable = False
    if not printable:                                 # a raw binary key (the demo Q-registry HMAC key)
        if len(raw) < 16:
            return []
        b64 = base64.b64encode(raw)
        return list(dict.fromkeys([raw.hex().encode(), raw.hex().upper().encode(), b64, b64.rstrip(b"="),
                                   base64.urlsafe_b64encode(raw).rstrip(b"=")]))
    lines = [l.strip() for l in v.splitlines() if l.strip()]
    if len(lines) == 1:
        one = lines[0]
        return _forms(one) if len(one) >= MIN_LEN and one not in NOT_SECRET and not PLACEHOLDER.match(one) else []
    # multi-line: a PEM / OpenSSH private key -> a few of its base64 body lines (first, middle, last)
    body = [l for l in lines if not l.startswith(b"-----") and b": " not in l and len(l) >= KEY_BODY_MIN]
    if len(body) > KEY_BODY_LINES:
        idx = sorted({round(i * (len(body) - 1) / (KEY_BODY_LINES - 1)) for i in range(KEY_BODY_LINES)})
        body = [body[i] for i in idx]
    return list(dict.fromkeys(body))


def _env_key(k: bytes) -> str:
    k = k.strip().decode("utf-8", "replace")
    return k[7:].strip() if k.startswith("export ") else k


def _env_values(raw: bytes, label: str):
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(b"#") or b"=" not in line:
            continue
        k, _, v = line.partition(b"=")
        k = k.strip().decode("utf-8", "replace")
        if k.startswith("export "):
            k = k[7:].strip()
        v = v.strip().strip(b"'\"")
        if (not SECRET_KEY_NAME.search(k) or k in NOT_SECRET_KEYS or len(v) < SHORT_SECRET_KEYS.get(k, MIN_LEN)
                or v in NOT_SECRET or PLACEHOLDER.match(v) or v == b"CHANGE_ME"):
            continue
        yield f"{label}:{k}", _forms(v)


def _load_sources(patterns, seen: set[str], not_checked: list[str]):
    """(NAME, forms) for every credential file the globs name. Unreadable -> not_checked, by name."""
    import glob
    for pat in patterns:
        parent = Path(pat).parent
        if parent.exists() and not os.access(parent, os.R_OK | os.X_OK):
            not_checked.append(f"{pat} (directory not listable)")
            continue
        for fp in sorted(glob.glob(pat)):
            p = Path(fp)
            rp = _real(p)
            if rp in seen or SOURCE_SKIP.search(p.name) or SOURCE_SKIP.search(rp) or not os.path.isfile(rp):
                continue
            seen.add(rp)
            try:
                if os.path.getsize(rp) > 1024 * 1024:  # not a credential file
                    continue
                raw = p.read_bytes()
            except PermissionError:
                not_checked.append(str(p))
                continue
            except OSError:
                continue
            if p.name.endswith(".env"):
                yield from _env_values(raw, p.name)
            else:
                forms = _value_forms(raw)
                if forms:
                    yield f"file:{p.name}", forms


def default_roots() -> list[Path]:
    return [Path.home() / ".claude" / "projects", Path(f"/tmp/claude-{os.getuid()}")]


def agent_roots() -> list[Path]:
    """2026-10-12 (docs-parity audit): the docstring said a root-owned copy scanned the agent accounts'
    homes; nothing did. Every ctdc-agent-* account: Claude Code projects + /tmp/claude-<uid>, and the
    Codex CLI's session logs (~/.codex/sessions, ~/.codex/history.jsonl). Only root can read them."""
    import pwd
    roots = []
    operator = os.environ.get("CTDC_OPERATOR_ACCOUNT", "corporatetraveldc")   # the operator's own sessions too:
    for u in pwd.getpwall():                                                  # a transcript that leaked while live
        if u.pw_name.startswith("ctdc-agent-") or u.pw_name == operator:      # is redacted once it closes
            h = Path(u.pw_dir)
            roots += [h / ".claude" / "projects", Path(f"/tmp/claude-{u.pw_uid}"),
                      h / ".codex" / "sessions", h / ".codex" / "history.jsonl"]
    return roots


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


def find(blob: bytes, secrets: dict[str, list[bytes]], ladd: frozenset[bytes] | None = None,
         coords=None) -> dict[str, int]:
    hits: dict[str, int] = {}
    for name, forms in secrets.items():
        n = sum(blob.count(f) for f in forms)
        if n:
            hits[name] = n
    for label, rx in TOKEN_PATTERNS:
        n = sum(1 for m in rx.finditer(blob) if not PLACEHOLDER.match(m.group(0).split(b"_")[-1]))
        if n:
            hits[label] = hits.get(label, 0) + n
    n = len(coord_spans(blob, coords))
    if n:
        hits[COORD_LABEL] = n
    n = len(ladd_spans(blob, ladd))
    if n:
        hits[LADD_LABEL] = n
    for label, spans in keyed_spans(blob).items():
        hits[label] = len(spans)
    return hits


def redact_blob(blob: bytes, secrets: dict[str, list[bytes]], ladd: frozenset[bytes] | None = None,
                coords=None) -> bytes:
    for name, forms in sorted(secrets.items(), key=lambda kv: -max(len(f) for f in kv[1])):
        for f in forms:
            blob = blob.replace(f, f"[REDACTED:{name}]".encode())
    for label, rx in TOKEN_PATTERNS:
        blob = rx.sub(lambda m: m.group(0) if PLACEHOLDER.match(m.group(0).split(b"_")[-1])
                      else f"[REDACTED:{label}]".encode(), blob)
    return redact_ladd(redact_coords(blob, coords), ladd)


def scan(roots: list[Path], secrets: dict[str, list[bytes]],
         ladd: frozenset[bytes] | None = None, coords=None) -> dict[Path, dict[str, int]]:
    found = {}
    REVIEW.clear()
    lists = pl.PrivacyLists(None, "", KEYED)
    sallow = pl.load_allow(pl.SUSPECT_ALLOW)
    for p in iter_files(roots):
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            blob = p.read_bytes()
            h = find(blob, secrets, ladd, coords)
        except OSError:
            continue
        if h:
            found[p] = h
        sus: dict[str, int] = {}
        for x in pl.suspects(blob, lists, sallow):
            k = f"suspected-{x.industry}-identifier"
            sus[k] = sus.get(k, 0) + 1
        if sus:
            REVIEW[p] = sus
    return found


def _report(found: dict[Path, dict[str, int]]) -> None:
    for p, h in sorted(found.items()):
        print(f"  {p}: " + ", ".join(f"{k} x{v}{_industry_note(k)}" for k, v in sorted(h.items())))


def redact(roots: list[Path], secrets: dict[str, list[bytes]], include_live: bool,
           ladd: frozenset[bytes] | None = None, coords=None) -> int:
    found = scan(roots, secrets, ladd, coords)
    now, done, skipped = time.time(), 0, []
    for p in found:
        st = p.stat()
        if not include_live and now - st.st_mtime < LIVE_MINUTES * 60:
            skipped.append(p)
            continue
        new = redact_blob(p.read_bytes(), secrets, ladd, coords)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".redact-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(new)
        os.chmod(tmp, st.st_mode & 0o7777)
        if os.geteuid() == 0:                 # root redacting an agent's file: keep the agent as its owner
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, p)
        done += 1
    # 2026-10-13: a file that leaked DURING this run (after the first scan passed it) was neither
    # redacted nor skipped, so the rescan failed the unit on a live session (16:04Z run, 4.5 min under
    # load). The rescan applies the same live rule: such a file joins the skipped list for a later run.
    after = {}
    for p, h in scan(roots, secrets, ladd, coords).items():
        if p in skipped:
            continue
        if not include_live and time.time() - p.stat().st_mtime < LIVE_MINUTES * 60:
            skipped.append(p)
            continue
        after[p] = h
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
    ap.add_argument("--agents", action="store_true", help="scan every ctdc-agent-* account (root only)")
    ap.add_argument("--include-live", action="store_true")
    ap.add_argument("--require-ladd", action="store_true",
                    help="exit 3 whenever the LADD class could not be checked (no dispatch env included)")
    ap.add_argument("--ladd-stdin", action="store_true",
                    help="read the LADD list from stdin, one per line (root unit; implies --require-ladd)")
    ap.add_argument("--require-coords", action="store_true",
                    help="exit 3 whenever the receiver-coords class could not be checked")
    a = ap.parse_args(argv)
    roots = a.root or (agent_roots() if a.agents else default_roots())
    secrets = load_secret_values()
    if not secrets:
        print("transcript-leak-scan: WARNING no live secret values loaded (secrets file unreadable); token shapes only")
    if NOT_CHECKED:
        # names only; the hourly root run (corporatetraveldc-agent-leak-scan) reads the root-only files
        print(f"transcript-leak-scan: NOTE {len(NOT_CHECKED)} credential file(s) NOT CHECKED (unreadable to "
              "this account): " + ", ".join(NOT_CHECKED))
    ladd, why = load_ladd_from_stream(sys.stdin) if a.ladd_stdin else load_ladd_identifiers()
    # 2026-10-23: maritime / ground / rail lists. Absent or empty = "no entries" and an
    # unreadable one is said loudly; neither ever changes the exit status (only aviation
    # is fail-closed).
    KEYED.clear()
    KEYED.update(pl.load_keyed_lists())
    print("transcript-leak-scan: privacy lists -- "
          + "; ".join(f"{ind}: {kl.status}" for ind, kl in KEYED.items()))
    # 3 = the LADD class was not checked where it had to be (dispatch env present,
    # --require-ladd or --ladd-stdin): never a silent pass.
    ladd_rc = 3 if ladd is None and (a.require_ladd or a.ladd_stdin or _dispatch_env()) else 0
    if ladd is None:
        print(f"transcript-leak-scan: {'ERROR' if ladd_rc else 'WARNING'} LADD class '{LADD_LABEL}' NOT CHECKED: {why}")
    coords, cwhy = load_receiver_coords()
    coords_rc = 3 if coords is None and a.require_coords else 0
    if coords is None:
        print(f"transcript-leak-scan: {'ERROR' if coords_rc else 'WARNING'} class '{COORD_LABEL}' NOT CHECKED: {cwhy}")
    ladd_rc = ladd_rc or coords_rc
    if a.cmd == "redact":
        rc = redact(roots, secrets, a.include_live, ladd, coords)
        if ladd is None:
            print(f"  LADD class '{LADD_LABEL}' NOT CHECKED in this redact: {why}")
        if coords is None:
            print(f"  class '{COORD_LABEL}' NOT CHECKED in this redact: {cwhy}")
        return rc or ladd_rc
    found = scan(roots, secrets, ladd, coords)
    if REVIEW:
        print(f"transcript-leak-scan: REVIEW {len(REVIEW)} file(s) carry suspected identifier(s) in a vertical "
              "with no list -- needs review (paths and counts only):")
        _report(REVIEW)
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
        return ladd_rc
    if ladd is None or coords is None:
        skipped = ([f"LADD class NOT CHECKED: {why}"] if ladd is None else []) + \
                  ([f"{COORD_LABEL} class NOT CHECKED: {cwhy}"] if coords is None else [])
        checked = (" + LADD class checked" if ladd is not None else "") + \
                  (f" + {COORD_LABEL} class checked" if coords is not None else "")
        print(f"transcript-leak-scan: no leak found by the classes checked ({len(secrets)} secret value(s) + "
              f"{len(TOKEN_PATTERNS)} token shapes{checked}) -- " + "; ".join(skipped))
        return ladd_rc
    print(f"transcript-leak-scan: clean ({len(secrets)} secret value(s) + {len(TOKEN_PATTERNS)} token shapes "
          f"+ LADD class checked + {COORD_LABEL} class checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
