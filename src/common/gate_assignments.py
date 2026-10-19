"""
common/gate_assignments.py -- gate / terminal / baggage assignments per dated flight leg
(2026-10-18, gate batch; operator decisions in vault designs/20261018T180900Z and
memory project_gate_fids_decisions).

Evidence goes in (record()), a pure resolver picks the current value per
(leg, side, field) (resolve()), and every change of the resolved (value, status)
is kept as a revision. Pushes come only from resolved changes (push_watch_changes),
deduped on a hash of leg + side + field + value + status -- never a timestamp or a
source. Tables: pg_schema/0079 (Postgres), _SQLITE_DDL below (SQLite twin).

Source precedence (operator 2026-10-18), highest first -- SOURCE_TIERS:
  40 SWIM: TDES parkingGate (departure), TFDM stand later (slot 'swim_tfdm').
  30 ACARS: airframes.io = ACARS Drama Jumpseat = local decoder (one tier).
  20 AeroAPI: fallback only (common.gate_aeroapi: budgeted, ledgered).
  10 MWAA FIDS: DCA/IAD only, gate / terminal / baggage only.
A lower tier never replaces a live higher-tier value. Same-tier disagreement is
'conflicting' (no value pushed as fact), unless the newer message is itself a
gate change / clearance that postdates every disagreeing one.

Hard rules kept here:
  - FIDS never supplies a time, a status or a tail: no Observation field can carry
    one, and the FIDS observed_at is OUR fetch time (the FIDS published time is
    used only by common.airport_fids.match_leg to pick the row, never stored).
  - Placeholders (TDES 'G', 'N/A', blank, masked X runs ...) never count as a
    value. TDES's G-prefixed form ('GC33' next to 'C33') is NOT normalised: the
    2026-10-18 data check found no leg showing both forms (0 of 2,192 G-prefixed
    rows at DCA/IAD/BWI), so it is kept raw and flagged g_prefix_unverified,
    which caps its status at 'provisional'.
  - LADD-listed legs are dropped before storage (faa_ladd_listed_batch /
    faa_ladd_hex_batch); a LADD lookup failure counts as listed (fail closed).
    Only a count is logged.
  - Tails / registrations are never stored here; an ACARS envelope's registration
    and hex are used for the LADD check only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from common import db
from common import flight_instance as fi

log = logging.getLogger(__name__)

RESOLVER_VERSION = "gate-resolver-1"

SOURCE_TIERS = {
    "swim_tdes": 40, "swim_tfdm": 40,
    "acars_airframes": 30, "acars_jumpseat": 30, "acars_local": 30,
    "aeroapi": 20,
    "mwaa_fids": 10,
}
SOURCE_LABELS = {
    "swim_tdes": "SWIM TDES", "swim_tfdm": "SWIM TFDM",
    "acars_airframes": "ACARS", "acars_jumpseat": "ACARS", "acars_local": "ACARS",
    "aeroapi": "AeroAPI", "mwaa_fids": "MWAA FIDS",
}
# A display source re-shows its current value on every fetch: its LATEST
# observation wins (a blank there means "no value now") and it expires.
DISPLAY_SOURCES = frozenset({"mwaa_fids"})
LEG_MATCH_RANK = {"exact": 4, "route_date": 3, "route_watch": 2, "route_only": 1}
EVIDENCE_RANK = {"clearance": 5, "assignment_change": 4, "assignment_msg": 3, "api": 2, "display": 1}
SUPERSEDING_EVIDENCE = frozenset({"assignment_change", "clearance"})
SIDES = ("departure", "arrival")
FIELDS = ("gate", "terminal", "baggage", "stand")
# What the API reports and what pushes (terminal rides along in the gate push).
API_SLOTS = (("arrival", "gate"), ("arrival", "terminal"), ("arrival", "baggage"), ("departure", "gate"))
PUSH_SLOTS = (("arrival", "gate"), ("arrival", "baggage"), ("departure", "gate"))
DC_AIRPORTS = frozenset({"DCA", "IAD", "BWI"})
OBSERVED_MIN_CONFIDENCE = 0.8
RETENTION_DAYS = 120


def fids_max_stale_s() -> int:
    from common.airport_fids import FIDS_MAX_STALE_S
    return FIDS_MAX_STALE_S


_PLACEHOLDERS = frozenset({"", "G", "N/A", "NA", "N.A.", "NULL", "NONE", "-", "--", "TBD", "TBA",
                           "?", "UNK", "UNKNOWN", "0"})
_G_PREFIX_RE = re.compile(r"^G(?:[A-Z]\d{1,3}[A-Z]?|\d{1,3}[A-Z]?)$")


# ── time helpers ─────────────────────────────────────────────────────────────

def _iso(ep: float | None = None) -> str:
    ep = time.time() if ep is None else ep
    return datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ep(value) -> float | None:
    return fi.to_epoch(value)


def iso_utc(value) -> str | None:
    """Any parseable time (epoch, ISO with offset/Z) as 'YYYY-MM-DDTHH:MM:SSZ'."""
    ep = _ep(value)
    return _iso(ep) if ep is not None else None


# ── values ───────────────────────────────────────────────────────────────────

def normalize_value(field: str, raw, source: str) -> tuple[str | None, bool, tuple]:
    """(value_norm, is_placeholder, flags). value_norm is None for a placeholder."""
    if raw is None:
        return None, True, ()
    v = re.sub(r"\s+", " ", str(raw)).strip().upper().strip("-").strip()
    if v in _PLACEHOLDERS or (v and set(v) <= {"X"}):
        return None, True, ()
    flags: tuple = ()
    if field == "gate" and source.startswith("swim_") and _G_PREFIX_RE.match(v):
        flags = ("g_prefix_unverified",)
    return v, False, flags


def push_key(leg_key: str, side: str, field: str, value: str | None, status: str) -> str:
    """Content-only push identity: never a timestamp, an observation id or a source."""
    return hashlib.sha256(f"{leg_key}|{side}|{field}|{value or ''}|{status}".encode()).hexdigest()[:16]


# ── leg keys ─────────────────────────────────────────────────────────────────

def make_key(carrier: str, flight_num: str, origin=None, destination=None,
             sched=None) -> fi.FlightInstanceKey:
    num = (str(flight_num).lstrip("0") or "0").upper()
    return fi.FlightInstanceKey(carrier.upper(), num, fi.norm_airport(origin),
                                fi.norm_airport(destination), fi.utc_date(sched))


def watch_leg_key(entry: dict, ident: str | None = None, sched=None) -> fi.FlightInstanceKey | None:
    """The dated leg a flight watch is about, keyed by the OPERATING flight when
    codeshare_map names exactly one for the watch's route (the form TDES and ACARS
    use). None for a watch that is not a flight number (a tail watch)."""
    ident = ident or entry.get("identifier")
    ops = fi.operating_identities(ident, entry.get("origin"), entry.get("destination"))
    if not ops:
        return None
    carrier, num = ops[1] if len(ops) == 2 else ops[0]
    return make_key(carrier, num, entry.get("origin"), entry.get("destination"),
                    sched or entry.get("scheduled_departure"))


def leg_instance(key: fi.FlightInstanceKey) -> fi.FlightInstance | None:
    """The SWIM/TFMS-verified instance of a dated leg (for its times), or None."""
    if not key.sched_date:
        return None
    try:
        cands = fi.resolve(key.callsign, key.origin, key.destination, key.sched_date)
    except Exception as e:
        log.debug("gate: leg instance %s: %s", key, e)
        return None
    cands = [c for c in cands if c.key.sched_date == key.sched_date]
    return cands[0] if len(cands) == 1 else None


# ── LADD ─────────────────────────────────────────────────────────────────────

def ladd_blocked(idents: Iterable[str | None] = (), hexes: Iterable[str | None] = ()) -> bool:
    """True when any identifier/hex is LADD-listed OR the lookup failed (fail closed)."""
    ids = [i for i in idents if i]
    hx = [h for h in hexes if h]
    try:
        if ids and db.faa_ladd_listed_batch(ids):
            return True
        if hx and db.faa_ladd_hex_batch(hx):
            return True
        return False
    except Exception as e:
        log.warning("gate: LADD lookup failed (%s) -- record dropped (fail closed)", type(e).__name__)
        return True


# ── storage ──────────────────────────────────────────────────────────────────

_SQLITE_DDL = (
    """CREATE TABLE IF NOT EXISTS gate_baggage_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        leg_key TEXT NOT NULL, carrier TEXT NOT NULL, flight_num TEXT NOT NULL,
        origin TEXT, destination TEXT, sched_date TEXT, airport TEXT,
        side TEXT NOT NULL CHECK (side IN ('departure','arrival')),
        field TEXT NOT NULL CHECK (field IN ('gate','terminal','baggage','stand')),
        value_raw TEXT, value_norm TEXT, is_placeholder INTEGER NOT NULL DEFAULT 0, flags TEXT,
        source TEXT NOT NULL CHECK (source IN ('swim_tdes','swim_tfdm','acars_airframes',
            'acars_jumpseat','acars_local','aeroapi','mwaa_fids')),
        source_tier INTEGER NOT NULL, raw_table TEXT, raw_ref TEXT, parser_version TEXT NOT NULL,
        observed_at TEXT NOT NULL, effective_at TEXT, ingested_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        leg_match TEXT NOT NULL CHECK (leg_match IN ('exact','route_date','route_watch','route_only')),
        evidence_type TEXT NOT NULL CHECK (evidence_type IN ('clearance','assignment_change',
            'assignment_msg','api','display')),
        confidence REAL NOT NULL, verification TEXT NOT NULL DEFAULT 'none',
        status TEXT NOT NULL DEFAULT 'accepted' CHECK (status IN ('accepted','placeholder')),
        content_key TEXT NOT NULL UNIQUE)""",
    "CREATE INDEX IF NOT EXISTS idx_gbo_leg ON gate_baggage_observations (leg_key, side, field)",
    "CREATE INDEX IF NOT EXISTS idx_gbo_flight ON gate_baggage_observations (carrier, flight_num, sched_date)",
    """CREATE TABLE IF NOT EXISTS gate_baggage_assignments (
        leg_key TEXT NOT NULL, side TEXT NOT NULL, field TEXT NOT NULL,
        carrier TEXT NOT NULL, flight_num TEXT NOT NULL, origin TEXT, destination TEXT,
        sched_date TEXT, airport TEXT, value_norm TEXT,
        status TEXT NOT NULL CHECK (status IN ('observed','provisional','superseded','conflicting','unknown')),
        winning_obs_id INTEGER, conflicting_values TEXT, source TEXT,
        resolver_version TEXT NOT NULL, push_key TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1, resolved_at TEXT NOT NULL,
        PRIMARY KEY (leg_key, side, field))""",
    """CREATE TABLE IF NOT EXISTS gate_baggage_assignment_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        leg_key TEXT NOT NULL, side TEXT NOT NULL, field TEXT NOT NULL, revision INTEGER NOT NULL,
        prev_value TEXT, new_value TEXT, prev_status TEXT, new_status TEXT,
        winning_obs_id INTEGER, source TEXT, reason TEXT NOT NULL, resolved_at TEXT NOT NULL,
        UNIQUE (leg_key, side, field, revision))""",
    """CREATE TABLE IF NOT EXISTS aeroapi_gate_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        called_at TEXT NOT NULL, call_day TEXT NOT NULL, leg_key TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK (purpose IN ('pre_eta','post_on')),
        http_status INTEGER, outcome TEXT, UNIQUE (leg_key, purpose))""",
)


def ensure_tables(c=None) -> None:
    """SQLite only (tests, the demo's SQLite pin); Postgres gets pg_schema/0079."""
    from common import db_backend
    if db_backend.backend() == "postgres":
        return
    if c is None:
        with db.conn() as c2:
            for stmt in _SQLITE_DDL:
                c2.execute(stmt)
        return
    for stmt in _SQLITE_DDL:
        c.execute(stmt)


@dataclass
class Observation:
    """One piece of evidence. Carries NO time other than the source's own event
    time (observed_at; for FIDS and AeroAPI: our fetch time), no status, no tail."""
    key: fi.FlightInstanceKey
    side: str
    field: str
    value_raw: str | None
    source: str
    observed_at: str
    leg_match: str
    evidence_type: str
    confidence: float
    parser_version: str
    airport: str | None = None
    raw_table: str | None = None
    raw_ref: str | None = None
    effective_at: str | None = None
    event_key: str | None = None      # the source event's own stable fields

    def content_key(self, value_norm: str | None) -> str:
        if self.source in DISPLAY_SOURCES:
            ev = ""                       # one row per shown value; last_seen_at moves
        else:
            ev = self.event_key or self.observed_at
        raw = f"{self.key}|{self.side}|{self.field}|{value_norm or ''}|{self.source}|{ev}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Resolution:
    value: str | None = None
    status: str = "unknown"
    source: str | None = None
    winning_obs_id: int | None = None
    observed_at: str | None = None
    last_seen_at: str | None = None
    leg_match: str | None = None
    verification: str = "none"
    conflicting_values: list = dc_field(default_factory=list)
    airport: str | None = None
    flags: tuple = ()
    candidates: int = 0


@dataclass
class Change:
    leg_key: str
    side: str
    field: str
    prev_value: str | None
    prev_status: str | None
    resolution: Resolution
    revision: int


@dataclass
class RecordResult:
    stored: int = 0
    duplicates: int = 0
    ladd_dropped: bool = False
    changes: list = dc_field(default_factory=list)


def _row_ts(r: dict, col: str) -> float:
    return _ep(r.get(col)) or 0.0


def resolve(rows: Iterable[dict], now: float | None = None) -> Resolution:
    """Pure: the current assignment for ONE (leg, side, field) from its observation
    rows (dicts with the gate_baggage_observations columns). Not latest-write-wins:
    tier, then leg-match quality, evidence type, confidence; observed_at (the
    source's own event time) orders messages within a source."""
    now = time.time() if now is None else now
    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)

    candidates: list[dict] = []
    for src, rs in by_source.items():
        if src in DISPLAY_SOURCES:
            latest = max(rs, key=lambda r: (_row_ts(r, "last_seen_at"), r.get("id") or 0))
            if bool(latest.get("is_placeholder")) or not latest.get("value_norm"):
                continue
            if now - _row_ts(latest, "last_seen_at") > fids_max_stale_s():
                continue                  # stale display value: unknown, never an old gate
            candidates.append(latest)
        else:
            vals = [r for r in rs if not bool(r.get("is_placeholder")) and r.get("value_norm")]
            if vals:
                candidates.append(max(vals, key=lambda r: (_row_ts(r, "observed_at"), r.get("id") or 0)))
    if not candidates:
        return Resolution()

    top_tier = max(SOURCE_TIERS.get(c["source"], 0) for c in candidates)
    tops = [c for c in candidates if SOURCE_TIERS.get(c["source"], 0) == top_tier]

    def rank(c):
        return (LEG_MATCH_RANK.get(c["leg_match"], 0), EVIDENCE_RANK.get(c["evidence_type"], 0),
                float(c.get("confidence") or 0), _row_ts(c, "observed_at"))

    values = {c["value_norm"] for c in tops}
    verification = "none"
    if len(values) == 1:
        winner = max(tops, key=rank)
        agreeing = {c["source"] for c in candidates if c["value_norm"] == winner["value_norm"]}
        if len(agreeing) > 1:
            verification = "cross_source_agree"
    else:
        newest = max(tops, key=lambda c: (_row_ts(c, "observed_at"), rank(c)))
        others = [c for c in tops if c["value_norm"] != newest["value_norm"]]
        if newest["evidence_type"] in SUPERSEDING_EVIDENCE and \
                all(_row_ts(o, "observed_at") < _row_ts(newest, "observed_at") for o in others):
            winner = newest
        else:
            return Resolution(status="conflicting", conflicting_values=sorted(values),
                              candidates=len(candidates), airport=tops[0].get("airport"))

    flags = tuple(f for f in (winner.get("flags") or "").split(",") if f)
    observed = (LEG_MATCH_RANK.get(winner["leg_match"], 0) >= LEG_MATCH_RANK["route_date"]
                and float(winner.get("confidence") or 0) >= OBSERVED_MIN_CONFIDENCE and not flags)
    return Resolution(value=winner["value_norm"], status="observed" if observed else "provisional",
                      source=winner["source"], winning_obs_id=winner.get("id"),
                      observed_at=winner.get("observed_at"), last_seen_at=winner.get("last_seen_at"),
                      leg_match=winner["leg_match"], verification=verification,
                      airport=winner.get("airport"), flags=flags, candidates=len(candidates))


def _insert(c, obs: Observation, now_iso: str) -> tuple[str, bool]:
    norm, placeholder, flags = normalize_value(obs.field, obs.value_raw, obs.source)
    ck = obs.content_key(norm)
    cur = c.execute(
        """INSERT INTO gate_baggage_observations
           (leg_key, carrier, flight_num, origin, destination, sched_date, airport, side, field,
            value_raw, value_norm, is_placeholder, flags, source, source_tier, raw_table, raw_ref,
            parser_version, observed_at, effective_at, ingested_at, last_seen_at, leg_match,
            evidence_type, confidence, verification, status, content_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (content_key) DO NOTHING""",
        (str(obs.key), obs.key.carrier, obs.key.flight_num, obs.key.origin, obs.key.destination,
         obs.key.sched_date, fi.norm_airport(obs.airport), obs.side, obs.field,
         (None if obs.value_raw is None else str(obs.value_raw)[:32]), norm, placeholder,
         ",".join(flags) or None, obs.source, SOURCE_TIERS[obs.source], obs.raw_table,
         (obs.raw_ref or None) and str(obs.raw_ref)[:200], obs.parser_version, obs.observed_at,
         obs.effective_at, now_iso, (now_iso if obs.source in DISPLAY_SOURCES else obs.observed_at),
         obs.leg_match, obs.evidence_type, float(obs.confidence), "none",
         "placeholder" if placeholder else "accepted", ck))
    inserted = (cur.rowcount or 0) > 0
    if not inserted and obs.source in DISPLAY_SOURCES:
        c.execute("UPDATE gate_baggage_observations SET last_seen_at = ? "
                  "WHERE content_key = ? AND last_seen_at < ?", (now_iso, ck, now_iso))
    return ck, inserted


def _validate(obs: Observation) -> None:
    if obs.source not in SOURCE_TIERS:
        raise ValueError(f"unknown gate source {obs.source!r}")
    if obs.side not in SIDES or obs.field not in FIELDS:
        raise ValueError(f"bad slot {obs.side}/{obs.field}")
    if obs.leg_match not in LEG_MATCH_RANK or obs.evidence_type not in EVIDENCE_RANK:
        raise ValueError("bad leg_match / evidence_type")
    if obs.source == "mwaa_fids" and fi.norm_airport(obs.airport) not in DC_AIRPORTS:
        raise ValueError("FIDS observations are DC airports only")


def record(observations: list[Observation], *, ladd_idents: Iterable[str | None] = (),
           ladd_hexes: Iterable[str | None] = (), now: float | None = None) -> RecordResult:
    """Store evidence for ONE leg (all observations share obs.key) and re-resolve the
    touched slots. LADD-listed (or LADD-unknown) legs are dropped before anything is
    written. Never raises on a LADD or store error; the caller's own path goes on."""
    res = RecordResult()
    if not observations:
        return res
    key = observations[0].key
    for o in observations:
        _validate(o)
        if str(o.key) != str(key):
            raise ValueError("record(): one leg per call")
    if ladd_blocked([key.callsign, *ladd_idents], ladd_hexes):
        res.ladd_dropped = True
        log.info("gate: 1 leg dropped before storage (LADD-listed or LADD lookup unavailable)")
        return res
    now = time.time() if now is None else now
    now_iso = _iso(now)
    touched: set[tuple[str, str]] = set()
    with db.conn() as c:
        ensure_tables(c)
        for o in observations:
            _ck, inserted = _insert(c, o, now_iso)
            if inserted:
                res.stored += 1
            else:
                res.duplicates += 1
            touched.add((o.side, o.field))
    for side, field in sorted(touched):
        ch = refresh_slot(key, side, field, now=now, reason=f"record:{observations[0].source}")
        if ch:
            res.changes.append(ch)
    return res


def _obs_rows(c, leg_key: str, side: str | None = None, field: str | None = None) -> list[dict]:
    if side and field:
        rows = c.execute("SELECT * FROM gate_baggage_observations WHERE leg_key = ? AND side = ? AND field = ?",
                         (leg_key, side, field)).fetchall()
    else:
        rows = c.execute("SELECT * FROM gate_baggage_observations WHERE leg_key = ?", (leg_key,)).fetchall()
    return [dict(r) for r in rows]


def refresh_slot(key: fi.FlightInstanceKey, side: str, field: str, *, now: float | None = None,
                 reason: str = "refresh") -> Change | None:
    """Re-resolve one slot and persist a revision when (value, status) changed.
    Returns the Change, or None when nothing changed. A concurrent writer taking
    the same revision number rolls this transaction back; it is retried once."""
    now = time.time() if now is None else now
    for attempt in (1, 2):
        try:
            return _refresh_once(key, side, field, now, reason)
        except Exception as e:
            if attempt == 2:
                raise
            log.debug("gate: refresh race on %s %s/%s (%s) -- retrying", key, side, field, type(e).__name__)
    return None


def _refresh_once(key: fi.FlightInstanceKey, side: str, field: str, now: float,
                  reason: str) -> Change | None:
    lk = str(key)
    with db.conn() as c:
        ensure_tables(c)
        r = resolve(_obs_rows(c, lk, side, field), now)
        cur = c.execute("SELECT * FROM gate_baggage_assignments WHERE leg_key = ? AND side = ? AND field = ?",
                        (lk, side, field)).fetchone()
        cur = dict(cur) if cur else None
        if cur is None and r.status == "unknown":
            return None
        pk = push_key(lk, side, field, r.value, r.status)
        conflicts = json.dumps(r.conflicting_values) if r.conflicting_values else None
        if cur is not None and (cur.get("value_norm"), cur.get("status")) == (r.value, r.status):
            if (cur.get("winning_obs_id"), cur.get("source")) != (r.winning_obs_id, r.source):
                c.execute("UPDATE gate_baggage_assignments SET winning_obs_id = ?, source = ?, "
                          "conflicting_values = ?, resolved_at = ? WHERE leg_key = ? AND side = ? AND field = ?",
                          (r.winning_obs_id, r.source, conflicts, _iso(now), lk, side, field))
            return None
        rev = (int(cur["revision"]) + 1) if cur else 1
        c.execute("""INSERT INTO gate_baggage_assignment_history
                     (leg_key, side, field, revision, prev_value, new_value, prev_status, new_status,
                      winning_obs_id, source, reason, resolved_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (lk, side, field, rev, cur.get("value_norm") if cur else None, r.value,
                   cur.get("status") if cur else None, r.status, r.winning_obs_id, r.source,
                   reason[:80], _iso(now)))
        c.execute("""INSERT INTO gate_baggage_assignments
                     (leg_key, side, field, carrier, flight_num, origin, destination, sched_date, airport,
                      value_norm, status, winning_obs_id, conflicting_values, source, resolver_version,
                      push_key, revision, resolved_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT (leg_key, side, field) DO UPDATE SET
                       airport = excluded.airport, value_norm = excluded.value_norm,
                       status = excluded.status, winning_obs_id = excluded.winning_obs_id,
                       conflicting_values = excluded.conflicting_values, source = excluded.source,
                       resolver_version = excluded.resolver_version, push_key = excluded.push_key,
                       revision = excluded.revision, resolved_at = excluded.resolved_at""",
                  (lk, side, field, key.carrier, key.flight_num, key.origin, key.destination,
                   key.sched_date, fi.norm_airport(r.airport), r.value, r.status, r.winning_obs_id,
                   conflicts, r.source, RESOLVER_VERSION, pk, rev, _iso(now)))
        return Change(lk, side, field, cur.get("value_norm") if cur else None,
                      cur.get("status") if cur else None, r, rev)


# ── reads (API) ──────────────────────────────────────────────────────────────

def _field_view(r: Resolution, now: float, history: list[dict]) -> dict:
    ts = r.last_seen_at or r.observed_at
    age = int(now - _ep(ts)) if ts and _ep(ts) is not None else None
    fresh = None
    if r.status in ("observed", "provisional"):
        fresh = True if r.source not in DISPLAY_SOURCES else (age is not None and age <= fids_max_stale_s())
    return {
        "value": r.value,
        "status": r.status,
        "source": SOURCE_LABELS.get(r.source) if r.source else None,
        "source_id": r.source,
        "observed_at": iso_utc(r.observed_at) if r.observed_at else None,
        "freshness": {"age_seconds": age, "fresh": fresh},
        "verification": r.verification,
        "conflicting_values": r.conflicting_values or [],
        "superseded": [{"value": h.get("prev_value"), "status": "superseded",
                        "until": h.get("resolved_at")}
                       for h in history if h.get("prev_value") and h.get("prev_value") != r.value],
    }


def leg_assignments(key: fi.FlightInstanceKey | str, now: float | None = None) -> dict:
    """{'arrival_gate': {...}, 'arrival_terminal': ..., 'arrival_baggage': ...,
    'departure_gate': ...} computed live from the evidence (read-only)."""
    now = time.time() if now is None else now
    lk = str(key)
    with db.conn() as c:
        ensure_tables(c)
        rows = _obs_rows(c, lk)
        hist = [dict(h) for h in c.execute(
            "SELECT side, field, prev_value, new_value, resolved_at FROM gate_baggage_assignment_history "
            "WHERE leg_key = ? ORDER BY revision", (lk,)).fetchall()]
    out = {}
    for side, field in API_SLOTS:
        slot_rows = [r for r in rows if r["side"] == side and r["field"] == field]
        res = resolve(slot_rows, now)
        out[f"{side}_{field}"] = _field_view(res, now, [h for h in hist
                                                        if h["side"] == side and h["field"] == field])
    return out


def unknown_fields() -> dict:
    return {f"{s}_{f}": _field_view(Resolution(), time.time(), []) for s, f in API_SLOTS}


def legs_for(identifier: str, origin: str | None = None, destination: str | None = None,
             sched_date: str | None = None, now: float | None = None) -> list[dict]:
    """Stored legs for a flight (marketing or operating number), newest first.
    Without a date: legs scheduled from yesterday to tomorrow (UTC), plus undated ones."""
    now = time.time() if now is None else now
    idents = fi.operating_identities(identifier, origin, destination)
    if not idents:
        return []
    days = None
    if not sched_date:
        d0 = datetime.fromtimestamp(now, tz=timezone.utc).date()
        days = {(d0 + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
    seen: dict[str, dict] = {}
    with db.conn() as c:
        ensure_tables(c)
        for carrier, num in idents:
            num = num.lstrip("0") or "0"
            if sched_date:
                rows = c.execute("SELECT DISTINCT leg_key, carrier, flight_num, origin, destination, sched_date "
                                 "FROM gate_baggage_observations WHERE carrier = ? AND flight_num = ? "
                                 "AND sched_date = ?", (carrier, num, sched_date)).fetchall()
            else:
                rows = c.execute("SELECT DISTINCT leg_key, carrier, flight_num, origin, destination, sched_date "
                                 "FROM gate_baggage_observations WHERE carrier = ? AND flight_num = ?",
                                 (carrier, num)).fetchall()
            for r in rows:
                r = dict(r)
                if fi.airports_agree(r.get("origin"), origin) is False or \
                        fi.airports_agree(r.get("destination"), destination) is False:
                    continue
                if days is not None and r.get("sched_date") and r["sched_date"] not in days:
                    continue
                seen[r["leg_key"]] = r
    return sorted(seen.values(), key=lambda r: (r.get("sched_date") or "", r["leg_key"]), reverse=True)


# ── pushes ───────────────────────────────────────────────────────────────────

def _push_dedup():
    """Shared, cross-process, forward-only state (DISPATCH_STATE_DIR), per watch slot."""
    from common.push_dedup import PushDedup
    return PushDedup("gate_assignment")


def dedup_slot(entry_id: str, side: str, field: str) -> str:
    return f"{entry_id}:{side}:{field}"


def mark_pushed(entry_id: str, key: fi.FlightInstanceKey | str, side: str, field: str,
                value: str | None, status: str) -> None:
    """Record a push made by another path (the TDES clearance push names the
    departure gate) so the resolved-change push does not repeat it."""
    _push_dedup().record(dedup_slot(entry_id, side, field), push_key(str(key), side, field, value, status))


def push_text(ident: str | None, side: str, field: str, value: str, status: str, airport: str | None,
              source: str | None, terminal: str | None = None) -> str:
    """No status word from any source, no time: the value, our resolution status
    and which source tier supplied it.

    2026-10-19 (operator, Gate-1b): ident=None leaves the flight out -- the
    watchlist push already puts the watch's identifier in the title ("FLT X: ")
    and both bodies, so the summary repeated it ("FLT UAL1627: UAL1627 arrival
    gate ..."). The word "provisional" is unchanged (operator has not decided)."""
    what = {"gate": "gate", "baggage": "baggage claim", "terminal": "terminal"}[field]
    where = f" at {airport}" if airport else ""
    term = f" (terminal {terminal})" if terminal and field == "gate" else ""
    label = SOURCE_LABELS.get(source or "", source or "?")
    qual = "" if status == "observed" else " (provisional)"
    who = f"{ident} " if ident else ""
    return f"{who}{side} {what} {value}{term}{where}{qual} [{label}]"


def _shown_ident(ident: str | None, entry: dict) -> str | None:
    """The flight to name in the summary: None when it is the watch's own
    identifier (same string, or the same flight in IATA/ICAO form), which the
    push title and bodies already carry."""
    from common.airline_codes import same_flight
    watch = (entry.get("identifier") or "").strip().upper()
    mine = (ident or "").strip().upper()
    if not mine or mine == watch or same_flight(mine, watch):
        return None
    return ident


def push_watch_changes(entry: dict, ident: str, key: fi.FlightInstanceKey,
                       now: float | None = None) -> int:
    """Push each changed, KNOWN resolved assignment for this watch's leg through the
    existing watchlist path. No push for unknown or conflicting. Returns pushes made."""
    now = time.time() if now is None else now
    pushed = 0
    view = None
    for side, field in PUSH_SLOTS:
        try:
            refresh_slot(key, side, field, now=now, reason="sweep")
        except Exception as e:
            log.debug("gate: refresh %s %s/%s: %s", key, side, field, e)
    for side, field in PUSH_SLOTS:
        if view is None:
            view = leg_assignments(key, now)
        f = view[f"{side}_{field}"]
        if f["status"] not in ("observed", "provisional") or not f["value"]:
            continue
        pk = push_key(str(key), side, field, f["value"], f["status"])
        slot = dedup_slot(entry["id"], side, field)
        if not _push_dedup().should_push(slot, pk):
            continue
        airport = key.destination if side == "arrival" else key.origin
        terminal = view.get("arrival_terminal", {}).get("value") if side == "arrival" else None
        summary = push_text(_shown_ident(ident, entry), side, field, f["value"], f["status"], airport,
                            f["source_id"], terminal)
        from shared.watchlist import watchlist_event_hit
        watchlist_event_hit(entry["id"], summary,
                            {"watchlist_trigger": "gate_assignment", "slot": f"{side}:{field}",
                             "side": side, "field": field, "value": f["value"],
                             "assignment_status": f["status"],      # OUR resolution status, never a FIDS one
                             "airport": airport, "leg_key": str(key), "source": f["source"]},
                            priority=3)
        _push_dedup().record(slot, pk)
        pushed += 1
    return pushed


# ── retention ────────────────────────────────────────────────────────────────

def prune(days: int = RETENTION_DAYS) -> int:
    cutoff = _iso(time.time() - days * 86400)
    n = 0
    with db.conn() as c:
        ensure_tables(c)
        n += c.execute("DELETE FROM gate_baggage_observations WHERE ingested_at < ?", (cutoff,)).rowcount or 0
        n += c.execute("DELETE FROM gate_baggage_assignments WHERE resolved_at < ?", (cutoff,)).rowcount or 0
        n += c.execute("DELETE FROM gate_baggage_assignment_history WHERE resolved_at < ?", (cutoff,)).rowcount or 0
        n += c.execute("DELETE FROM aeroapi_gate_calls WHERE called_at < ?", (cutoff,)).rowcount or 0
    return n


# ── ACARS (airframes.io / Jumpseat / local) ──────────────────────────────────

ACARS_SOURCES = {"AIRFRAMES": "acars_airframes", "JUMPSEAT": "acars_jumpseat", "LOCAL": "acars_local"}
UPLINK_LABELS = frozenset({"RA", "C1", "40", "41", "42", "21", "3T"})
_ACARS_WINDOW_BEFORE_DEP_S = 3 * 3600
_ACARS_WINDOW_AFTER_ARR_S = 15 * 60
_ACARS_DEFAULT_BLOCK_S = 16 * 3600
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def _direction(env: dict) -> str | None:
    d = (env.get("direction") or "").strip().lower()
    if d in ("up", "uplink", "ground to air", "ground-to-air", "g2a"):
        return "up"
    if d in ("down", "downlink", "air to ground", "air-to-ground", "a2g"):
        return "down"
    return None


def _allowed_days(inst: fi.FlightInstance) -> set[int]:
    """Day-of-month values a message may carry for this leg: the UTC scheduled-
    departure date and the date 10 h earlier (US local dates west of UTC)."""
    days = set()
    if inst.key.sched_date:
        days.add(int(inst.key.sched_date[8:10]))
    if inst.sched_dep:
        for off in (0, -10 * 3600):
            days.add(datetime.fromtimestamp(inst.sched_dep + off, tz=timezone.utc).day)
    return days


def _qk_date(s: str) -> str | None:
    try:
        return f"20{s[5:7]}-{_MONTHS[s[2:5]]:02d}-{int(s[0:2]):02d}"
    except Exception:
        return None


def bind_acars(hit: dict, env: dict) -> tuple[fi.FlightInstance | None, dict]:
    """Bind one parsed ACARS hit to exactly one SWIM/TFMS-verified dated leg.
    Returns (instance, info) -- info carries the grade or the drop reason. Never a
    guess: no flight id, no unique leg, a wrong date or destination drops it."""
    from common import acars_gate_parser as agp
    from common.airline_codes import IATA_TO_ICAO
    info: dict = {"fmt": hit.get("fmt")}
    grade = agp.format_grade(hit)
    if grade in ("request", "ack"):
        info["drop"] = "signal_only"
        return None, info
    t_ep = _ep(env.get("timestamp"))
    if t_ep is None:
        info["drop"] = "no_time"
        return None, info

    flt = hit.get("flt")
    ident = None
    if flt and not agp.masked(flt):
        al = hit.get("al") or agp.FORMAT_CARRIER.get(hit.get("fmt") or "")
        if al:
            ident = f"{IATA_TO_ICAO.get(al, al) if len(al) == 2 else al}{flt}"
    in_text_flight = ident is not None
    if ident is None:
        ef = (env.get("flight") or "").strip().upper().replace(" ", "")
        ident = ef or None
    if not ident or not fi.parse_flight_ident(ident):
        info["drop"] = "no_flight_id"
        return None, info

    dst = hit.get("dst") if not agp.masked(hit.get("dst")) else None
    endpoints = [x for x in (hit.get("org"), hit.get("dst"), hit.get("station")) if x and not agp.masked(x)]
    if hit.get("fmt") == "AA_REGIONAL_ARRINFO":
        dst = None                         # route meaning varies: the leg decides
    elif not dst:
        dst = hit.get("station")

    cands = []
    for inst in fi.resolve(ident):
        dep = inst.best_dep
        if dep is None:
            continue
        arr = inst.best_arr or dep + _ACARS_DEFAULT_BLOCK_S
        if not (dep - _ACARS_WINDOW_BEFORE_DEP_S <= t_ep <= arr + _ACARS_WINDOW_AFTER_ARR_S):
            continue
        if dst:
            if fi.airports_agree(inst.key.destination, dst) is not True:
                continue
        elif hit.get("fmt") == "AA_REGIONAL_ARRINFO" and endpoints:
            if not any(fi.airports_agree(inst.key.destination, e) is True for e in endpoints):
                continue
        cands.append(inst)
    if not cands:
        info["drop"] = "no_leg"
        return None, info
    if len(cands) > 1:
        info["drop"] = "ambiguous_leg"
        return None, info
    inst = cands[0]

    day_ok = None
    if hit.get("day") and not agp.masked(hit.get("day")):
        day_ok = int(hit["day"]) in _allowed_days(inst)
    elif hit.get("date"):
        allowed = {inst.key.sched_date}
        if inst.sched_dep:
            allowed.add(datetime.fromtimestamp(inst.sched_dep - 10 * 3600, tz=timezone.utc).date().isoformat())
        day_ok = _qk_date(hit["date"]) in allowed
    if day_ok is False:
        info["drop"] = "wrong_date"
        return None, info

    has_dst = bool(dst) or (hit.get("fmt") == "AA_REGIONAL_ARRINFO" and bool(endpoints))
    if in_text_flight and has_dst and (day_ok or hit.get("fmt") == "YX_ARR_GATE_INFO"):
        leg_match = "exact"
    elif has_dst:
        leg_match = "route_date"
    else:
        leg_match = "route_only"
    direction = _direction(env)
    uplink = direction == "up" or (direction is None and (env.get("label") or "").upper() in UPLINK_LABELS)
    confirmed = (grade == "confirmed" and uplink and in_text_flight and bool(dst)
                 and leg_match == "exact")
    info.update(grade="confirmed" if confirmed else "probable", leg_match=leg_match,
                uplink=uplink)
    return inst, info


def acars_observations(hit: dict, env: dict, inst: fi.FlightInstance, info: dict,
                       source: str) -> list[Observation]:
    from common import acars_gate_parser as agp
    key = inst.key
    conf = 0.9 if info.get("grade") == "confirmed" else 0.6
    evidence = "assignment_change" if hit.get("kind") == "GATE_CHANGE" else "assignment_msg"
    observed = iso_utc(env.get("timestamp"))
    event_key = "|".join(str(hit.get(k) or "") for k in ("fmt", "al", "flt", "day", "dst", "gate", "sent"))
    if not hit.get("sent"):
        ep = _ep(env.get("timestamp")) or 0
        event_key += "|" + _iso(ep - ep % 600)          # 10-min bucket: feeder copies collapse
    common_kw = dict(key=key, source=source, observed_at=observed, leg_match=info["leg_match"],
                     evidence_type=evidence, confidence=conf, parser_version=agp.PARSER_VERSION,
                     airport=key.destination, raw_table=f"acars_watcher:{source}",   # raw text is not stored
                     raw_ref=(str(env.get("msg_id")) if env.get("msg_id") else None), event_key=event_key)
    out = []
    if hit.get("gate") and hit.get("kind") in agp.ASSIGNMENT_KINDS:
        out.append(Observation(side="arrival", field="gate", value_raw=hit["gate"], **common_kw))
    if hit.get("terminal") and hit.get("kind") in agp.ASSIGNMENT_KINDS:
        out.append(Observation(side="arrival", field="terminal", value_raw=hit["terminal"], **common_kw))
    if agp.baggage_counts(hit):
        out.append(Observation(side="arrival", field="baggage", value_raw=hit["bag"], **common_kw))
    return out


def _watched_leg(inst: fi.FlightInstance, entries: list[dict]) -> bool:
    try:
        return fi.match_watch_entry(entries, inst.key.callsign, origin=inst.key.origin,
                                    destination=inst.key.destination) is not None
    except Exception:
        return False


_HIT_VALUE_RE = re.compile(r"^[A-Z0-9 /:-]{1,16}$")


def _clean_hit(hit: dict) -> dict | None:
    """A forwarded hit (acars_gate_forward) re-validated: known format, the kind
    that format has, short plain values only."""
    from common import acars_gate_parser as agp
    kinds = {name: kind for name, kind, _rx in agp.FORMATS}
    fmt = str(hit.get("fmt") or "")
    if fmt not in kinds:
        return None
    kind = str(hit.get("kind") or "")
    if kind != kinds[fmt] and not (fmt == "UA_GATE_ASSIGN" and kind == "GATE_CHANGE"):
        return None
    out = {"fmt": fmt, "kind": kind}
    for k in ("gate", "terminal", "bag", "flt", "day", "date", "al", "org", "dst", "station", "sent", "chg"):
        v = hit.get(k)
        if v is None or v == "":
            continue
        v = str(v).upper().strip()
        if not _HIT_VALUE_RE.match(v):
            return None
        out[k] = v
    return out


def ingest_acars(items: list[dict]) -> dict:
    """Bind, LADD-check and store gate/baggage announcements from ACARS. Each item
    is an envelope {source, label, timestamp, direction, flight, msg_id} plus either
    `hit` (fields already parsed by acars_gate_forward -- the watcher sends no
    text, no registration, no hex) or `text` (parsed here). Stores only legs
    touching DCA/IAD/BWI or on the watchlist. LADD runs on the bound leg's own
    identifiers (callsign, SWIM registration and hex) and the envelope flight id.
    Returns counts only -- never an identifier."""
    from common import acars_gate_parser as agp
    counts: dict[str, int] = {"received": 0, "parsed": 0, "stored": 0, "signals": 0, "ladd_dropped": 0}

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    entries = None
    for env in items:
        counts["received"] += 1
        source = ACARS_SOURCES.get((env.get("source") or "").upper())
        if not source:
            bump("bad_source")
            continue
        if isinstance(env.get("hit"), dict):
            hit = _clean_hit(env["hit"])
            if hit is None:
                bump("bad_hit")
                continue
        else:
            text = env.get("text") or ""
            hits = agp.parse(text) if agp.is_candidate(text) else []
            if not hits:
                bump("noise")
                continue
            hit = hits[0]
        counts["parsed"] += 1
        if hit.get("kind") in agp.SIGNAL_KINDS:
            counts["signals"] += 1
            continue
        try:
            inst, info = bind_acars(hit, env)
        except Exception as e:
            log.debug("gate: acars bind failed: %s", type(e).__name__)
            bump("bind_error")
            continue
        if inst is None:
            bump("drop_" + info.get("drop", "unknown"))
            continue
        relevant = bool({inst.key.origin, inst.key.destination} & DC_AIRPORTS)
        if not relevant:
            if entries is None:
                try:
                    from shared.watchlist import get_active_entries
                    entries = get_active_entries(entry_type="flight", cached=True)
                except Exception:
                    entries = []
            relevant = _watched_leg(inst, entries)
        if not relevant:
            bump("out_of_scope")
            continue
        obs = acars_observations(hit, env, inst, info, source)
        if not obs:
            bump("no_fields")
            continue
        try:
            r = record(obs, ladd_idents=[env.get("flight"), inst.registration],
                       ladd_hexes=[inst.hex])
        except Exception as e:
            log.warning("gate: acars store failed: %s", type(e).__name__)
            bump("store_error")
            continue
        if r.ladd_dropped:
            counts["ladd_dropped"] += 1
        counts["stored"] += r.stored
    return counts


# ── TDES (SWIM, departure gate) ──────────────────────────────────────────────

def tdes_leg(callsign: str, airport: str | None, destination: str | None,
             event_time) -> tuple[fi.FlightInstanceKey | None, str]:
    """The dated leg a TDES clearance belongs to: the one instance of this callsign
    from this airport (to this destination) scheduled within [-2 h, +6 h] of the
    clearance -> 'exact'; otherwise the clearance's own UTC date -> 'route_date'."""
    parsed = fi.parse_flight_ident(callsign)
    if not parsed:
        return None, ""
    t = _ep(event_time)
    try:
        cands = [i for i in fi.resolve(callsign, airport, destination)
                 if i.sched_dep is not None and t is not None
                 and t - 2 * 3600 <= i.sched_dep <= t + 6 * 3600]
    except Exception:
        cands = []
    if len(cands) == 1:
        k = cands[0].key
        return make_key(k.carrier, k.flight_num, k.origin or airport, k.destination or destination,
                        k.sched_date), "exact"
    return make_key(parsed[0], parsed[1], airport, destination, t), "route_date"


def record_tdes(rec: dict) -> tuple[fi.FlightInstanceKey | None, RecordResult | None]:
    """Departure-gate evidence from one stored TDES clearance record."""
    gate = rec.get("parking_gate")
    if gate is None:
        return None, None
    key, match = tdes_leg(rec.get("callsign"), rec.get("airport"), rec.get("destination_airport"),
                          rec.get("event_time"))
    if key is None:
        return None, None
    ev = f"{rec.get('airport')}|{rec.get('callsign')}|{rec.get('event_time')}"
    obs = Observation(key=key, side="departure", field="gate", value_raw=gate, source="swim_tdes",
                      observed_at=iso_utc(rec.get("event_time")) or _iso(), leg_match=match,
                      evidence_type="clearance", confidence=0.95, parser_version="tdes-1",
                      airport=rec.get("airport"), raw_table="tdes_departure_events", raw_ref=ev, event_key=ev)
    return key, record([obs])
