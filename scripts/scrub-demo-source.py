#!/usr/bin/env python3
"""scripts/scrub-demo-source.py -- the ONLY bridge from production to the demo
instance (2026-10-16 demo/production database split, relay 1).

Operator rule (hard, 2026-10-16): the public demo is fed ONLY by a scrub from
production into a logically and physically separate database, exactly as
push-public.sh feeds the public tree. This script is that scrub, PG -> PG:

  SOURCE  the production instance, read-only, through common.db_backend.
          pg_conn() (host-side; the socket-path -> 127.0.0.1 fallback applies):
          demo_snapshots (the recorder's private staging archive) and
          brief_archive. The LADD gate reads production's faa_ladd_aircraft
          through the SAME code path the live platform uses
          (common.db.faa_ladd_listed_batch / faa_is_ladd / faa_lookup_by_hex,
          the functions behind airplanes_live._ladd_listed) -- one definition
          of "LADD-listed", one data source, never a second mechanism.
  TARGET  the demo instance (corporatetraveldc-pgsql-demo, database
          corporatetraveldc_demo) through common.db_backend.demo_pg_conn(),
          over 127.0.0.1:5433 as demo_owner. This is the one process that
          holds both credentials, by design.

Every candidate row passes, in this order:
  1. endpoint allowlist (ENDPOINT_ALLOWLIST; board*, osint_feed and
     knowledge_graph_meta are never promoted) and brief-type allowlist;
  2. a minimum-age floor (default 24 h: nothing younger than that leaves
     production);
  3. the LADD gate on the ORIGINAL text: registration-, hex- and
     callsign-shaped tokens and the identifier fields of a JSON payload are
     batch-checked against production's LADD table; a listed identifier
     DROPS the whole row. An empty LADD table or a lookup error is FATAL
     (exit 5) -- never an empty set, never "treat as clean";
  4. src/demo/scrub_rules.scrub_text (literal + regex substitutions);
  5. the shape sweeps, second line behind the gate: tail shapes
     (N[1-9][0-9A-Z]{1,4}) and 24-bit hexes (with a digit, outside
     HEX_ALLOWLIST) are redacted;
  6. src/demo/scrub_rules.verify_scrubbed on the result -- any violation
     DROPS the row (fail closed: a gap in demo history is fine, a leak is not).

Survivors are appended (never copy-then-delete; INSERT ... ON CONFLICT DO
NOTHING makes a re-run idempotent), replay-fill covers endpoint gaps inside
the 14-day loop with already-scrubbed rows (synthetic=TRUE), retention is the
52-week tier (prune), meta.window_end advances, and ONE scrub_ledger row
(hash-chained, counts only) records the run.

Modes (one required):
    --backfill [--days N]     promote the trailing N days (default 28)
    --refresh                 promote everything since meta.window_end
    --profiles                one-time copy of demo_profiles production -> demo
    --verify-ledger           walk the target's scrub_ledger chain, exit 1 if broken
Options:
    --min-age-hours H         minimum-age floor (default 24)
    --dry-run                 do everything but write nothing (no rows, no meta,
                              no ledger row); prints the counts it would have written
Exit: 0 ok, 1 ledger broken, 2 usage/state, 5 fatal gate (manifest, LADD, identity).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pathlib
import re
import subprocess
import sys
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR / "src"))

from common import db, db_backend  # noqa: E402
from demo.db import RETENTION_TIERS  # noqa: E402
from demo.scrub_rules import scrub_text, verify_scrubbed  # noqa: E402

# ── Policy constants ────────────────────────────────────────────────────────

# The nine endpoints the public demo may replay. board / board_threads (the
# operator's own coordination board), osint_feed (monitoring output) and
# knowledge_graph_meta are recorded on the production side but NEVER
# promoted. There is no "v2" endpoint set in the recorder today
# (src/demo/recorder.py ENDPOINTS); a future public-safe endpoint is added
# here by name, never by pattern.
ENDPOINT_ALLOWLIST: frozenset[str] = frozenset({
    "tfr", "weather", "alerts", "cps", "notams", "amtrak", "opsplan", "route", "brief",
})
ENDPOINT_DENYLIST: frozenset[str] = frozenset({"board", "board_threads", "osint_feed", "knowledge_graph_meta"})
BRIEF_TYPES: frozenset[str] = frozenset({"ops", "weekly", "ep-advance"})

MIN_AGE_HOURS_DEFAULT = 24
BACKFILL_DEFAULT_DAYS = 28
REPLAY_TARGET_DAYS = 14
RETENTION_DAYS = RETENTION_TIERS["52w"]
GENESIS_HASH = "0" * 64
SOURCE_PAGE = 200

DEMO_SECRETS_FILE = pathlib.Path("/etc/corporatetraveldc/pgsql-demo-secrets.env")

# Shape sweeps (second line behind the LADD gate).
TAIL_RE = re.compile(r"\bN[1-9][0-9A-Z]{1,4}\b")
HEX24_RE = re.compile(r"\b[0-9A-Fa-f]{6}\b")
# Hex tokens that are not aircraft: the colour codes demo_api.py's
# AIRMET/SIGMET overlay and the live app's JSON carry. Extend with
# SCRUB_HEX_ALLOWLIST=aaaaaa,bbbbbb (lower-case) -- never a real aircraft.
HEX_ALLOWLIST: frozenset[str] = frozenset(
    {"ff3131", "a855f7", "4a9eff", "ffd700", "8b6f47", "ff8c00", "9ca3af"}
    | {t.strip().lower() for t in os.environ.get("SCRUB_HEX_ALLOWLIST", "").split(",") if t.strip()}
)
# LADD gate token shape: 2-8 alnum, optionally hyphenated (foreign marks).
LADD_TOKEN_RE = re.compile(r"\b[A-Z0-9]{2,8}(?:-[A-Z0-9]{1,5})?\b")
# JSON identifier fields, by kind, walked when a payload parses as JSON.
IDENT_KEYS_REG = frozenset({"registration", "reg", "tail", "tail_number", "n_number", "nnumber", "r"})
IDENT_KEYS_HEX = frozenset({"hex", "icao", "icao24", "mode_s_hex", "icao_hex"})
IDENT_KEYS_CALLSIGN = frozenset({"callsign", "call_sign", "flight", "ident", "flight_id", "acid"})

# 2026-10-17 (operator): a LADD-listed aircraft is SUBSTITUTED with a synthetic
# Q registration and the record is kept (was: the whole record dropped, which
# threw away 89% of snapshots and every brief). The Q mark is an HMAC of the
# listed identifier under a production-only key, so it is stable across runs
# but cannot be rebuilt from the LADD list; the key never leaves this box and
# the map is never stored. A listed hex becomes [hex-redacted].
Q_KEY_FILE = pathlib.Path(os.environ.get("DEMO_Q_KEY_FILE", str(pathlib.Path.home() / ".secrets" / "demo-q-registry.key")))
Q_ALPHABET = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"   # no I, O, Q
SUB_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]{1,5})?(?![A-Za-z0-9])")
SUB_HEX_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{6}(?![0-9A-Fa-f])")


class ScrubFatal(SystemExit):
    """Exit 5: a gate this script must not run past (manifest, LADD, identity)."""

    def __init__(self, msg: str):
        print(f"XX FATAL -- {msg}", file=sys.stderr)
        super().__init__(5)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── Gates ───────────────────────────────────────────────────────────────────

def verify_self() -> None:
    """Self-verifying pattern _verify_before_inference() already applies
    in src/common/llm.py before any inference -- a privileged live-DB
    reader that feeds a public surface is precisely the kind of process
    that discipline exists for. Refuses to proceed on failure."""
    result = subprocess.run(
        [str(REPO_DIR / "scripts" / "verify-manifest.sh")],
        cwd=str(REPO_DIR), capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise ScrubFatal("INTEGRITY CHECK FAILED -- refusing to run scrub-demo-source.py")


def read_env_value(path: pathlib.Path, key: str) -> str:
    """One KEY from a podman-style env file, verbatim; never printed."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line[len(key) + 1:]
    except OSError:
        pass
    return ""


def prepare_identity() -> None:
    """This process is production-side (reads production as `dispatch`) and
    writes the demo instance as demo_owner. Pin both before any conninfo is
    built; refuse any other shape."""
    if db_backend.is_demo_role():
        raise ScrubFatal("DISPATCH_ROLE=demo -- the scrub is a production-side process, never a demo one")
    os.environ.setdefault("DEMO_PG_USER", db_backend.DEMO_PG_OWNER)
    if os.environ.get("DEMO_PG_USER") != db_backend.DEMO_PG_OWNER:
        raise ScrubFatal(f"DEMO_PG_USER must be {db_backend.DEMO_PG_OWNER!r} (the scrub writes as the instance owner)")
    if not os.environ.get("DEMO_PG_PASSWORD"):
        pw = read_env_value(DEMO_SECRETS_FILE, "POSTGRES_PASSWORD")
        if pw:
            os.environ["DEMO_PG_PASSWORD"] = pw   # the instance's bootstrap password IS demo_owner's


class LaddGate:
    """Per-row LADD check through the production filter (common.db), with a
    per-run cache so a token is looked up once. open() is fatal on an
    unreadable or EMPTY table; every lookup error is fatal (fail closed)."""

    def __init__(self):
        self.count: int = 0
        self._cache: dict[tuple[str, str], bool] = {}
        self.queries = 0
        self._extracted: dict[str, tuple[set[str], set[str], set[str]]] = {}

    def _extract(self, text: str) -> tuple[set[str], set[str], set[str]]:
        """extract() memoised for the current page (2026-10-17 profile: each
        row was tokenised three times -- prime, listed, violations)."""
        hit = self._extracted.get(text)
        if hit is None:
            hit = self.extract(text)
            if len(self._extracted) >= 2 * SOURCE_PAGE:
                self._extracted.clear()
            self._extracted[text] = hit
        return hit

    def open(self) -> None:
        try:
            n = db.faa_ladd_count()
        except Exception as e:  # noqa: BLE001 -- any failure here is fatal
            raise ScrubFatal(f"LADD table unreadable ({type(e).__name__}) -- no scrub without the filter")
        if not n or n <= 0:
            raise ScrubFatal("LADD table is EMPTY -- no scrub without the filter (import it first)")
        self.count = int(n)

    @staticmethod
    def extract(text: str) -> tuple[set[str], set[str], set[str]]:
        """(registration/callsign-shaped tokens, hex tokens, keyed-field
        tokens) from the ORIGINAL text. Text sweep plus a JSON field walk."""
        upper = text.upper()
        tokens = set(LADD_TOKEN_RE.findall(upper))
        hexes = {h.lower() for h in HEX24_RE.findall(text)}
        keyed: set[str] = set()
        parsed = None
        stripped = text.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        if parsed is not None:
            stack: list[Any] = [parsed]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    for k, v in node.items():
                        kl = str(k).lower()
                        if isinstance(v, str) and v.strip():
                            if kl in IDENT_KEYS_HEX:
                                hexes.add(v.strip().lower())
                            elif kl in IDENT_KEYS_REG or kl in IDENT_KEYS_CALLSIGN:
                                keyed.add(v.strip().upper().replace(" ", ""))
                        if isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(node, list):
                    stack.extend(node)
        return tokens | keyed, hexes, keyed

    def prime(self, texts: Iterable[str]) -> None:
        """Look up every identifier and hex of a whole page in one batch each
        (2026-10-17: per-row/per-hex lookups made the backfill ~1 row/s)."""
        idents: set[str] = set()
        hexes: set[str] = set()
        for t in texts:
            i, h, _ = self._extract(t)
            idents |= i
            hexes |= h
        self._lookup(idents, hexes)

    def _lookup(self, idents: set[str], hexes: set[str]) -> None:
        unknown = [t for t in idents if ("id", t) not in self._cache]
        unknown_hex = [h for h in hexes if ("hex", h) not in self._cache]
        try:
            if unknown:
                hits = db.faa_ladd_listed_batch(unknown)
                self.queries += 1
                for t in unknown:
                    self._cache[("id", t)] = t in hits
            if unknown_hex:
                hhits = db.faa_ladd_hex_batch(unknown_hex)
                self.queries += 1
                for h in unknown_hex:
                    self._cache[("hex", h)] = h in hhits
        except Exception as e:  # noqa: BLE001 -- a failed lookup never passes a row
            raise ScrubFatal(f"LADD lookup failed ({type(e).__name__}) -- fail closed")

    def listed(self, text: str) -> tuple[set[str], set[str]]:
        """(listed identifiers, listed hexes) in one row -- for substitution only, never logged."""
        idents, hexes, _keyed = self._extract(text)
        self._lookup(idents, hexes)
        return ({t for t in idents if self._cache.get(("id", t))},
                {h for h in hexes if self._cache.get(("hex", h))})

    def violations(self, text: str) -> list[str]:
        """Violation classes (never values) for one row; [] = clean."""
        listed, listed_hex = self.listed(text)
        out: list[str] = []
        if any(TAIL_RE.fullmatch(t) or "-" in t for t in listed):
            out.append("ladd-listed registration")
        if any(not (TAIL_RE.fullmatch(t) or "-" in t) for t in listed):
            out.append("ladd-listed callsign")
        if listed_hex:
            out.append("ladd-listed hex")
        return out


class QRegistry:
    """LADD-listed identifier -> stable synthetic Q mark (Q + 5 characters).

    Sized to the list (2026-10-17: ~73.5k LADD entries): 33^5 = 39.1M marks,
    so even the whole list gives ~69 expected collisions and the few thousand
    listed aircraft a demo window actually sees give well under one. Q + 4
    (1.19M) would give ~2,300 over the whole list."""

    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ScrubFatal("Q registry key shorter than 32 bytes")
        self._key = key

    @classmethod
    def load(cls, path: "pathlib.Path | None" = None, create: bool = True) -> "QRegistry":
        """Read the key; create it (0600, never printed) on first real run.
        A dry run without a key uses a throwaway one and writes nothing."""
        path = path or Q_KEY_FILE
        try:
            return cls(path.read_bytes())
        except FileNotFoundError:
            if not create:
                return cls(os.urandom(32))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
        return cls(path.read_bytes())

    def mark(self, ident: str) -> str:
        n = int.from_bytes(hmac.new(self._key, ident.strip().upper().encode(), hashlib.sha256).digest()[:8], "big")
        out = []
        for _ in range(5):
            n, r = divmod(n, len(Q_ALPHABET))
            out.append(Q_ALPHABET[r])
        return "Q" + "".join(out)


def substitute_ladd(text: str, gate: LaddGate, qreg: QRegistry) -> tuple[str, Counter]:
    """Replace every LADD-listed identifier with its Q mark and every listed
    hex with [hex-redacted]. Counts by class only."""
    listed, listed_hex = gate.listed(text)
    counts: Counter = Counter()
    # One pass per kind over the text, not one regex per listed identifier
    # (2026-10-17 profile: 0.1 s per subn on large payloads, ~60% of the run).
    if listed:
        marks = {t: qreg.mark(t) for t in listed}

        def _ident(m: re.Match) -> str:
            tok = m.group(0)
            up = tok.upper()
            if up not in marks:
                return tok
            counts["ladd registration substituted" if (TAIL_RE.fullmatch(up) or "-" in up)
                   else "ladd callsign substituted"] += 1
            return marks[up]
        text = SUB_TOKEN_RE.sub(_ident, text)
    if listed_hex:
        def _hex(m: re.Match) -> str:
            if m.group(0).lower() not in listed_hex:
                return m.group(0)
            counts["ladd hex redacted"] += 1
            return "[hex-redacted]"
        text = SUB_HEX_RE.sub(_hex, text)
    return text, counts


def sweep_shapes(text: str) -> tuple[str, Counter]:
    """Second line behind the LADD gate: redact tail shapes and 24-bit
    hexes (those with at least one digit and outside HEX_ALLOWLIST; the
    gate already checked every hex token against the registry)."""
    counts: Counter = Counter()

    def _tail(m: re.Match) -> str:
        counts["tail-shape redacted"] += 1
        return "[tail-redacted]"

    def _hex(m: re.Match) -> str:
        tok = m.group(0)
        if tok.lower() in HEX_ALLOWLIST or not any(c.isdigit() for c in tok):
            return tok
        counts["hex-shape redacted"] += 1
        return "[hex-redacted]"

    text = TAIL_RE.sub(_tail, text)
    text = HEX24_RE.sub(_hex, text)
    return text, counts


def _reason_class(v: str) -> str:
    # class only, never content -- "unrecognized email 'x@y'" -> "unrecognized email"
    return v.split(" '")[0].split(": ")[0]


def scrub_row_text(text: str, gate: LaddGate, qreg: "QRegistry | None" = None) -> tuple[str | None, Counter, Counter]:
    """(scrubbed_text_or_None, drop_reasons, redactions). None = DROP.
    With a Q registry, LADD-listed aircraft are substituted and the row is
    kept; the substituted text must then pass the gate again or it drops."""
    drops: Counter = Counter()
    redactions: Counter = Counter()
    if qreg is not None:
        text, redactions = substitute_ladd(text, gate, qreg)
        for cls in gate.violations(text):
            drops[cls + " (substitution incomplete)"] += 1
    else:
        for cls in gate.violations(text):
            drops[cls] += 1
    if drops:
        return None, drops, redactions
    scrubbed = scrub_text(text)
    scrubbed, swept = sweep_shapes(scrubbed)
    redactions.update(swept)
    violations = verify_scrubbed(scrubbed)
    if violations:
        for v in violations:
            drops[_reason_class(v)] += 1
        return None, drops, redactions
    return scrubbed, drops, redactions


def decode_payload(payload: bytes, compressed: bool) -> str | None:
    try:
        return (zlib.decompress(payload) if compressed else payload).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


def scrub_payload_bytes(payload: bytes, compressed: bool, gate: LaddGate,
                        qreg: "QRegistry | None" = None) -> tuple[bytes | None, Counter, Counter]:
    try:
        raw = zlib.decompress(payload) if compressed else payload
        text = raw.decode("utf-8")
    except Exception as e:  # noqa: BLE001 -- undecodable = drop, class only
        return None, Counter({f"decode failure ({type(e).__name__})": 1}), Counter()
    clean, drops, red = scrub_row_text(text, gate, qreg)
    if clean is None:
        return None, drops, red
    return zlib.compress(clean.encode("utf-8"), 6), drops, red


# ── Target ──────────────────────────────────────────────────────────────────

class Target:
    """Writes to the demo instance behind one flag: dry_run=True turns every
    write into a counted no-op. SQL is portable between the demo instance
    (through the translating demo_pg_conn) and the sqlite stand-ins the
    tests use; rows are read by name on both."""

    def __init__(self, factory: Callable[[], Any], dry_run: bool):
        self.factory = factory
        self.dry_run = dry_run
        self.would_write = 0

    def get_meta(self, key: str) -> str | None:
        with self.factory() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        if self.dry_run:
            self.would_write += 1
            return
        with self.factory() as c:
            c.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def insert_snapshots(self, rows: list[tuple]) -> None:
        if not rows:
            return
        if self.dry_run:
            self.would_write += len(rows)
            return
        with self.factory() as c:
            for endpoint, captured_at, payload, payload_hash, synthetic in rows:
                c.execute(
                    "INSERT INTO snapshots (endpoint, captured_at, payload, payload_hash, compressed, synthetic) "
                    "VALUES (?, ?, ?, ?, TRUE, ?) ON CONFLICT DO NOTHING",
                    (endpoint, captured_at, payload, payload_hash, bool(synthetic)),
                )

    def insert_briefs(self, rows: list[tuple]) -> None:
        if not rows:
            return
        if self.dry_run:
            self.would_write += len(rows)
            return
        with self.factory() as c:
            for generated_at, brief_type, content, source in rows:
                c.execute(
                    "INSERT INTO brief_archive (generated_at, brief_type, content, source) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (generated_at, brief_type, content, source),
                )

    def prune(self, retention_days: int) -> int:
        cutoff = iso(utcnow() - timedelta(days=retention_days))
        if self.dry_run:
            with self.factory() as c:
                n = c.execute("SELECT COUNT(*) AS n FROM snapshots WHERE captured_at < ?", (cutoff,)).fetchone()["n"]
                n += c.execute("SELECT COUNT(*) AS n FROM brief_archive WHERE generated_at < ?", (cutoff,)).fetchone()["n"]
            return int(n)
        with self.factory() as c:
            a = c.execute("DELETE FROM snapshots WHERE captured_at < ?", (cutoff,)).rowcount
            b = c.execute("DELETE FROM brief_archive WHERE generated_at < ?", (cutoff,)).rowcount
        return int(a or 0) + int(b or 0)

    def replay_fill_gaps(self, target_days: int = REPLAY_TARGET_DAYS) -> Counter:
        """2026-08-14 mechanism, carried over: for each endpoint without
        target_days of real coverage inside the trailing window, re-date the
        OLDEST already-scrubbed real row into each missing day (synthetic=
        TRUE, 06:00Z). Never fabricates content; real rows always win."""
        window_end_s = self.get_meta("window_end")
        if not window_end_s:
            return Counter()
        window_end = datetime.fromisoformat(window_end_s)
        window_start = window_end - timedelta(days=target_days)
        filled: Counter = Counter()
        with self.factory() as c:
            endpoints = [r["endpoint"] for r in c.execute("SELECT DISTINCT endpoint FROM snapshots").fetchall()]
            for ep in endpoints:
                real_days = {
                    r["captured_at"][:10] for r in c.execute(
                        "SELECT captured_at FROM snapshots WHERE endpoint=? AND synthetic = FALSE "
                        "AND captured_at>=? AND captured_at<?",
                        (ep, window_start.isoformat(), window_end.isoformat()),
                    ).fetchall()
                }
                if len(real_days) >= target_days:
                    continue
                src = c.execute(
                    "SELECT payload, payload_hash FROM snapshots WHERE endpoint=? AND synthetic = FALSE "
                    "ORDER BY captured_at ASC LIMIT 1", (ep,),
                ).fetchone()
                if src is None:
                    continue
                cursor = window_start
                while cursor < window_end:
                    if cursor.date().isoformat() not in real_days:
                        ts = cursor.replace(hour=6, minute=0, second=0, microsecond=0).isoformat()
                        if not self.dry_run:
                            c.execute(
                                "INSERT INTO snapshots (endpoint, captured_at, payload, payload_hash, compressed, synthetic) "
                                "VALUES (?, ?, ?, ?, TRUE, TRUE) ON CONFLICT DO NOTHING",
                                (ep, ts, bytes(src["payload"]), src["payload_hash"]),
                            )
                        filled[ep] += 1
                    cursor += timedelta(days=1)
        if self.dry_run:
            self.would_write += sum(filled.values())
        return filled

    # -- ledger --------------------------------------------------------------

    @staticmethod
    def entry_hash(prev_hash: str, body: dict) -> str:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{prev_hash}|{canonical}".encode("utf-8")).hexdigest()

    def ledger_append(self, body: dict) -> str | None:
        """ONE row per run. Returns the new entry hash (None on dry run)."""
        if self.dry_run:
            self.would_write += 1
            return None
        with self.factory() as c:
            last = c.execute("SELECT seq, entry_hash FROM scrub_ledger ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev = last["entry_hash"] if last else GENESIS_HASH
            body = dict(body, seq=seq)
            h = self.entry_hash(prev, body)
            c.execute(
                "INSERT INTO scrub_ledger (seq, run_at, mode, window_start, window_end, snapshots_promoted, "
                "snapshots_dropped, briefs_promoted, briefs_dropped, dropped_by_class, synthetic_filled, pruned, "
                "ladd_count, prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, body["run_at"], body["mode"], body["window_start"], body["window_end"],
                 body["snapshots_promoted"], body["snapshots_dropped"], body["briefs_promoted"],
                 body["briefs_dropped"], json.dumps(body["dropped_by_class"], sort_keys=True),
                 body["synthetic_filled"], body["pruned"], body["ladd_count"], prev, h),
            )
        return h

    def ledger_verify(self) -> tuple[bool, int]:
        """(intact, rows). Recomputes every entry hash from its stored
        fields and the previous hash."""
        with self.factory() as c:
            rows = c.execute(
                "SELECT seq, run_at, mode, window_start, window_end, snapshots_promoted, snapshots_dropped, "
                "briefs_promoted, briefs_dropped, dropped_by_class, synthetic_filled, pruned, ladd_count, "
                "prev_hash, entry_hash FROM scrub_ledger ORDER BY seq"
            ).fetchall()
        prev = GENESIS_HASH
        expected_seq = 1
        for r in rows:
            if r["seq"] != expected_seq or r["prev_hash"] != prev:
                return False, len(rows)
            body = {
                "seq": r["seq"], "run_at": r["run_at"], "mode": r["mode"],
                "window_start": r["window_start"], "window_end": r["window_end"],
                "snapshots_promoted": r["snapshots_promoted"], "snapshots_dropped": r["snapshots_dropped"],
                "briefs_promoted": r["briefs_promoted"], "briefs_dropped": r["briefs_dropped"],
                "dropped_by_class": json.loads(r["dropped_by_class"]),
                "synthetic_filled": r["synthetic_filled"], "pruned": r["pruned"], "ladd_count": r["ladd_count"],
            }
            if self.entry_hash(prev, body) != r["entry_hash"]:
                return False, len(rows)
            prev = r["entry_hash"]
            expected_seq += 1
        return True, len(rows)


# ── Promotion ───────────────────────────────────────────────────────────────

def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _scrub_page(source_factory: Callable[[], Any], page: list, gate: LaddGate,
                qreg: "QRegistry | None") -> tuple[list[tuple], int, int, Counter, Counter, str | None]:
    """Fetch and scrub one page of source ids. Pure: returns the rows to insert."""
    with source_factory() as s:
        rows = s.execute(
            "SELECT endpoint, captured_at, payload, payload_hash, compressed FROM demo_snapshots "
            "WHERE id IN (" + ",".join("?" * len(page)) + ") ORDER BY captured_at, id",
            tuple(page),
        ).fetchall()
    promoted = dropped = 0
    reasons: Counter = Counter()
    redactions: Counter = Counter()
    max_at: str | None = None
    out: list[tuple] = []
    gate.prime(t for t in (decode_payload(bytes(x["payload"]), bool(x["compressed"])) for x in rows) if t)
    for r in rows:
        if r["endpoint"] not in ENDPOINT_ALLOWLIST or r["endpoint"] in ENDPOINT_DENYLIST:
            dropped += 1
            reasons["endpoint not allowlisted"] += 1
            continue
        clean, drops, red = scrub_payload_bytes(bytes(r["payload"]), bool(r["compressed"]), gate, qreg)
        redactions.update(red)
        if clean is None:
            dropped += 1
            reasons.update(drops)
            continue
        out.append((r["endpoint"], r["captured_at"], clean, r["payload_hash"], False))
        promoted += 1
        if max_at is None or r["captured_at"] > max_at:
            max_at = r["captured_at"]
    return out, promoted, dropped, reasons, redactions, max_at


# 2026-10-17: the scrub is CPU-bound on large payloads (~57k tokens per
# snapshot), so pages are scrubbed in separate worker processes and only the
# parent writes to the demo instance. Workers inherit nothing open: the
# forked connection pools are dropped (never closed -- they belong to the
# parent) and each opens its own LADD gate (fail closed, exit 5 as before).
_WORK: dict[str, Any] = {}

# 2026-10-17: the backfill ignored the box's load and tripped the
# thermal-ingest-guard LOCKDOWN (load1 >= 40, whole stack shed) twice; the
# baseline here already sits at 20-30, and the lockdown sheds the stack, not
# this job. So the job steps aside on its own: before each page it waits while
# load1 >= DEMO_SCRUB_MAX_LOAD (default 24), and at most `workers` pages are
# in flight.
MAX_LOAD = float(os.environ.get("DEMO_SCRUB_MAX_LOAD", "24"))
LOAD_POLL_S = float(os.environ.get("DEMO_SCRUB_LOAD_POLL_S", "15"))


def _wait_for_headroom(sleep: Callable[[float], None] | None = None,
                       loadavg: Callable[[], tuple] = os.getloadavg) -> float:
    """Block while load1 >= MAX_LOAD. Returns seconds waited."""
    import time
    sleep = sleep or time.sleep
    waited = 0.0
    announced = False
    while (load1 := loadavg()[0]) >= MAX_LOAD:
        if not announced:
            print(f"[scrub-demo-source] load1 {load1:.1f} >= {MAX_LOAD:g} -- pausing", flush=True)
            announced = True
        sleep(LOAD_POLL_S)
        waited += LOAD_POLL_S
    return waited


def _worker_init() -> None:
    db_backend._pool = None
    db_backend._demo_pool = None
    gate = LaddGate()
    gate.open()
    _WORK["gate"] = gate


def _worker_page(page: list):
    return _scrub_page(_WORK["source_factory"], page, _WORK["gate"], _WORK["qreg"])


def promote_snapshots(source_factory: Callable[[], Any], target: Target, since: str, until: str,
                      gate: LaddGate, qreg: "QRegistry | None" = None,
                      workers: int = 1) -> tuple[int, int, Counter, Counter, str | None]:
    """(promoted, dropped, drop_reasons, redactions, max_captured_at)."""
    allow = sorted(ENDPOINT_ALLOWLIST - ENDPOINT_DENYLIST)
    with source_factory() as s:
        ids = [r["id"] for r in s.execute(
            "SELECT id FROM demo_snapshots WHERE captured_at > ? AND captured_at <= ? "
            "AND endpoint IN (" + ",".join("?" * len(allow)) + ") ORDER BY captured_at, id",
            (since, until, *allow),
        ).fetchall()]
    promoted = dropped = 0
    reasons: Counter = Counter()
    redactions: Counter = Counter()
    max_at: str | None = None
    pages = list(_chunks(ids, SOURCE_PAGE))

    def _take(res) -> None:
        nonlocal promoted, dropped, max_at
        out, p, d, rs, red, m = res
        target.insert_snapshots(out)
        promoted += p
        dropped += d
        reasons.update(rs)
        redactions.update(red)
        if m and (max_at is None or m > max_at):
            max_at = m

    if workers <= 1 or len(pages) <= 1:
        for page in pages:
            _wait_for_headroom()
            _take(_scrub_page(source_factory, page, gate, qreg))
    else:
        import concurrent.futures as cf
        import multiprocessing as mp
        _WORK.update(source_factory=source_factory, qreg=qreg)
        done = 0
        with cf.ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"),
                                    initializer=_worker_init) as ex:
            pending: set = set()
            queue = list(pages)
            while queue or pending:
                while queue and len(pending) < workers:
                    _wait_for_headroom()
                    pending.add(ex.submit(_worker_page, queue.pop(0)))
                finished, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
                for fut in finished:
                    try:
                        _take(fut.result())
                    except ScrubFatal:
                        raise
                    except Exception as e:  # noqa: BLE001 -- a worker failure never passes rows
                        raise ScrubFatal(f"scrub worker failed ({type(e).__name__}) -- fail closed")
                    done += 1
                    if done % 20 == 0 or done == len(pages):
                        print(f"[scrub-demo-source] pages {done}/{len(pages)}", flush=True)
    return promoted, dropped, reasons, redactions, max_at


def promote_briefs(source_factory: Callable[[], Any], target: Target, since: str, until: str,
                   gate: LaddGate, qreg: "QRegistry | None" = None) -> tuple[int, int, Counter, Counter, str | None]:
    types = sorted(BRIEF_TYPES)
    with source_factory() as s:
        rows = s.execute(
            "SELECT generated_at, brief_type, content, source FROM brief_archive "
            "WHERE generated_at > ? AND generated_at <= ? AND brief_type IN (" + ",".join("?" * len(types)) + ") "
            "ORDER BY generated_at",
            (since, until, *types),
        ).fetchall()
    promoted = dropped = 0
    reasons: Counter = Counter()
    redactions: Counter = Counter()
    max_at: str | None = None
    out: list[tuple] = []
    gate.prime(r["content"] for r in rows if r["content"])
    for r in rows:
        if r["brief_type"] not in BRIEF_TYPES:
            dropped += 1
            reasons["brief type not allowlisted"] += 1
            continue
        clean, drops, red = scrub_row_text(r["content"], gate, qreg)
        redactions.update(red)
        if clean is None:
            dropped += 1
            reasons.update(drops)
            continue
        out.append((r["generated_at"], r["brief_type"], clean, r["source"]))
        promoted += 1
        if max_at is None or r["generated_at"] > max_at:
            max_at = r["generated_at"]
    target.insert_briefs(out)
    return promoted, dropped, reasons, redactions, max_at


def copy_profiles(source_factory: Callable[[], Any], target: Target) -> tuple[int, int]:
    """One-time copy of demo_profiles production -> demo (hashes and salts,
    never a plaintext; the shape is identical). A row whose (label,
    created_at) already exists on the target is skipped. (copied, skipped)."""
    with source_factory() as s:
        rows = s.execute(
            "SELECT label, password_hash, salt, window_days, speed, auto_scale, created_at, active "
            "FROM demo_profiles ORDER BY id"
        ).fetchall()
    copied = skipped = 0
    with target.factory() as t:
        existing = {(r["label"], r["created_at"]) for r in t.execute("SELECT label, created_at FROM demo_profiles").fetchall()}
        for r in rows:
            if (r["label"], r["created_at"]) in existing:
                skipped += 1
                continue
            if not target.dry_run:
                t.execute(
                    "INSERT INTO demo_profiles (label, password_hash, salt, window_days, speed, auto_scale, created_at, active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["label"], r["password_hash"], r["salt"], r["window_days"], r["speed"],
                     bool(r["auto_scale"]), r["created_at"], bool(r["active"])),
                )
            copied += 1
    if target.dry_run:
        target.would_write += copied
    return copied, skipped


# ── Main ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="production -> demo instance scrub (the only bridge)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--profiles", action="store_true")
    mode.add_argument("--verify-ledger", action="store_true")
    parser.add_argument("--days", type=int, default=BACKFILL_DEFAULT_DAYS,
                        help=f"backfill window in days (default {BACKFILL_DEFAULT_DAYS})")
    parser.add_argument("--min-age-hours", type=int, default=MIN_AGE_HOURS_DEFAULT,
                        help=f"minimum age of anything promoted (default {MIN_AGE_HOURS_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true", help="count everything, write nothing")
    parser.add_argument("--workers", type=int, default=1,
                        help="scrub pages in N worker processes (the parent alone writes); 2026-10-17")
    return parser


def main(argv: list[str] | None = None, *, source_factory: Callable[[], Any] | None = None,
         target_factory: Callable[[], Any] | None = None, skip_self_verify: bool = False) -> int:
    args = build_parser().parse_args(argv)

    if not skip_self_verify:
        verify_self()
    prepare_identity()

    src = source_factory or db_backend.pg_conn
    tgt_factory = target_factory or db_backend.demo_pg_conn
    target = Target(tgt_factory, dry_run=args.dry_run)
    tag = "[scrub-demo-source]"
    if args.dry_run:
        print(f"{tag} DRY RUN -- nothing will be written")

    if args.verify_ledger:
        intact, n = target.ledger_verify()
        print(f"{tag} scrub_ledger: {n} row(s), {'intact' if intact else 'BROKEN'}")
        return 0 if intact else 1

    if args.profiles:
        copied, skipped = copy_profiles(src, target)
        print(f"{tag} demo_profiles: {copied} copied, {skipped} already present"
              + (" (dry run)" if args.dry_run else ""))
        return 0

    now = utcnow()
    until = iso(now - timedelta(hours=max(0, args.min_age_hours)))
    if args.backfill:
        since = iso(now - timedelta(days=args.days))
        mode = "backfill"
        print(f"{tag} BACKFILL -- trailing {args.days} days (since {since}, min age {args.min_age_hours} h, until {until})")
    else:
        since = target.get_meta("window_end") or ""
        if not since:
            print(f"XX --refresh requested but no prior window_end in meta -- run --backfill first.", file=sys.stderr)
            return 2
        mode = "refresh"
        print(f"{tag} REFRESH -- since window_end={since} (min age {args.min_age_hours} h, until {until})")

    gate = LaddGate()
    gate.open()
    qreg = QRegistry.load(create=not args.dry_run)
    print(f"{tag} LADD gate open through the production filter ({gate.count} identifiers on the list)")

    s_prom, s_drop, s_reasons, s_red, s_max = promote_snapshots(src, target, since, until, gate, qreg, workers=max(1, args.workers))
    b_prom, b_drop, b_reasons, b_red, b_max = promote_briefs(src, target, since, until, gate, qreg)

    candidates = [x for x in (s_max, b_max) if x is not None]
    new_window_end = max(candidates) if candidates else since
    target.set_meta("window_end", new_window_end)
    target.set_meta("promoted_at", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    filled = target.replay_fill_gaps(REPLAY_TARGET_DAYS)
    pruned = target.prune(RETENTION_DAYS)

    dropped_by_class = {k: v for k, v in (s_reasons + b_reasons).items()}
    body = {
        "run_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": mode,
        "window_start": since, "window_end": new_window_end,
        "snapshots_promoted": s_prom, "snapshots_dropped": s_drop,
        "briefs_promoted": b_prom, "briefs_dropped": b_drop,
        "dropped_by_class": dropped_by_class,
        "synthetic_filled": int(sum(filled.values())), "pruned": int(pruned),
        "ladd_count": gate.count,
    }
    entry = target.ledger_append(body)

    print("")
    print(f"{tag} === REPORT ({mode}{', dry run' if args.dry_run else ''}) ===")
    print(f"  snapshots:     {s_prom} promoted, {s_drop} dropped")
    for reason, count in s_reasons.most_common():
        print(f"    dropped ({reason}): {count}")
    print(f"  brief_archive: {b_prom} promoted, {b_drop} dropped")
    for reason, count in b_reasons.most_common():
        print(f"    dropped ({reason}): {count}")
    for cls, count in (s_red + b_red).most_common():
        print(f"  redacted ({cls}): {count}")
    if filled:
        print(f"  replay-fill (synthetic, real-data-derived, {REPLAY_TARGET_DAYS}d target):")
        for ep, count in filled.most_common():
            print(f"    {ep}: {count} day(s)")
    print(f"  pruned (> {RETENTION_DAYS} d): {pruned}")
    print(f"  LADD lookups: {gate.queries} batch queries")
    print(f"  new window_end: {new_window_end}")
    if args.dry_run:
        print(f"  would have written {target.would_write} row(s) -- nothing written")
    else:
        print(f"  scrub_ledger entry: {entry}")
    return 0


if __name__ == "__main__":
    # 2026-08-14: self-renice -- a --backfill run holds ~90%+ of a core for
    # an hour-plus and once starved a production ops-brief into a timeout
    # at load ~6 on this 4-core box. Costs nothing when the box is idle.
    # Here, not in main(): a test that imports main() must not renice pytest.
    try:
        os.nice(15)
    except OSError:
        pass
    sys.exit(main())
