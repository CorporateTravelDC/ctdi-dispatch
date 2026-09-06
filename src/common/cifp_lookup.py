"""
common.cifp_lookup -- read-only query API for the CIFP tables
(cifp_fixes / cifp_procedure_legs / cifp_holds, see db.py's SCHEMA_V45).

Every other module that needs a DC-area fix's coordinates, a procedure's
leg sequence, or a published hold should go through this module rather
than querying those tables directly -- keeps the query shape (region-
aware fix resolution, transition grouping, arrival-route joining) in one
place instead of re-derived per caller.

Built 2026-09-05 alongside src/poller/skills/faa_cifp_parse.py. See
docs/CIFP_DATA.md for the data model and known limits (holds are only
what's charted on a published procedure -- Potomac TRACON's verbal
airborne-holding assignments are NOT in here, see that doc).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from common import db

# Typical transit speed from a meter fix through descent/approach to the
# runway threshold. Deliberately a single rough constant, not a real
# performance model (no aircraft-type/wind/vectoring awareness) -- this
# is a static-geometry ESTIMATE, not a live schedule computation. Chosen
# as a conservative middle ground between typical descent groundspeed
# (~250-280kt at the meter fix) and final-approach speed (~140-160kt);
# every caller must label output from estimate_runway_eta() as an
# estimate, never as an authoritative time.
_TYPICAL_APPROACH_TRANSIT_KT = 180.0


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R_NM = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimate_runway_eta(meter_fix_lat: float, meter_fix_lon: float,
                         dest_airport: str, meter_fix_eta_iso: str
                         ) -> dict | None:
    """Estimate a runway-threshold arrival time from a TBFM meter-fix ETA,
    using CIFP's actual runway-threshold coordinates for `dest_airport` --
    NOT a live schedule, a static-geometry estimate (great-circle distance
    from the meter fix to the CLOSEST charted runway threshold, divided by
    a fixed typical transit speed). Callers (see tbfm_parser.py,
    fdps_parser.py) must present this as an estimate, never as an
    authoritative time -- same reasoning docs/CIFP_DATA.md already applies
    to expand_arrival_route()'s runway-transition ambiguity: which runway
    an aircraft actually lands on is genuinely unknown without a cleared-
    runway signal, so this picks the closest one rather than guessing
    which specific runway.

    Returns None (never a guess) if `dest_airport` has no charted runway
    thresholds in cifp_fixes, or if `meter_fix_eta_iso` doesn't parse."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT lat, lon FROM cifp_fixes WHERE type='RUNWAY_THRESHOLD' AND parent_airport=?",
            (dest_airport.strip().upper(),),
        ).fetchall()
    if not rows:
        return None
    try:
        eta_dt = datetime.fromisoformat(meter_fix_eta_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

    dist_nm = min(_haversine_nm(meter_fix_lat, meter_fix_lon, r["lat"], r["lon"])
                  for r in rows)
    transit_minutes = (dist_nm / _TYPICAL_APPROACH_TRANSIT_KT) * 60
    runway_eta = eta_dt + timedelta(minutes=transit_minutes)
    return {
        "runway_eta": runway_eta.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "distance_nm": round(dist_nm, 1),
        "transit_minutes": round(transit_minutes, 1),
    }


def resolve_fix(ident: str, icao_region: str | None = None) -> dict | None:
    """Resolve a fix ident to its CIFP record. Duplicate idents across
    regions are real (see docs/CIFP_DATA.md) -- pass icao_region when you
    have it (e.g. from an FDPS leg's fix_region field). Without a region,
    returns the fix only if the ident is unambiguous across every stored
    region; returns None (never a guess) if it's ambiguous."""
    ident = (ident or "").strip().upper()
    if not ident:
        return None
    with db.conn() as c:
        if icao_region:
            row = c.execute(
                "SELECT * FROM cifp_fixes WHERE ident=? AND icao_region=?",
                (ident, icao_region.strip().upper()),
            ).fetchone()
            return dict(row) if row else None
        rows = c.execute(
            "SELECT * FROM cifp_fixes WHERE ident=?", (ident,)
        ).fetchall()
    if len(rows) == 1:
        return dict(rows[0])
    return None


def get_procedure_transitions(airport: str, proc_type: str, procedure: str
                               ) -> dict[str, list[dict]]:
    """Every leg of one procedure, grouped by transition ident (or
    '(common)' for the shared body), each transition's legs in seq order."""
    with db.conn() as c:
        rows = c.execute(
            """SELECT * FROM cifp_procedure_legs
               WHERE airport=? AND proc_type=? AND procedure=?
               ORDER BY transition, seq""",
            (airport.upper(), proc_type.upper(), procedure.upper()),
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["transition"], []).append(dict(r))
    return out


def get_holds(airport: str | None = None, fix: str | None = None) -> list[dict]:
    """Published holds (HA/HF/HM legs), optionally filtered by airport
    and/or fix ident. HM rows are the closest published analogue to an
    ATC-discretion metering hold -- see docs/CIFP_DATA.md's hard limits
    before treating absence of an HM leg at a fix as "ATC won't hold you
    there.\""""
    sql = "SELECT * FROM cifp_holds WHERE 1=1"
    params: list = []
    if airport:
        sql += " AND airport=?"
        params.append(airport.upper())
    if fix:
        sql += " AND fix=?"
        params.append(fix.upper())
    with db.conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def expand_arrival_route(dest_airport: str, arr_proc: str, entry_fix: str | None,
                          proc_type: str = "STAR") -> list[dict] | None:
    """Join an entry transition -> common body -> (best-effort) runway
    transition into one ordered leg list with resolved coordinates -- the
    "filed route string to coordinate polyline" capability
    CIFP_PARSING_PROMPT.md's prompt envisioned.

    `arr_proc`/`entry_fix` are exactly the arr_proc/arr_entry_fix fields
    ingest.parsers.fdps_parser._parse_nas_route() already extracts from a
    filed NAS route string -- this function is deliberately NOT wired into
    that ingest path yet (see its own module comment); it's a tested,
    ready-to-use utility for whatever calls it next (a future hole-
    detection/route-visualization feature), not a live write-path change.

    Returns None if the procedure isn't one CIFP has for this airport
    (common for non-RNAV-charted or out-of-scope airports/procedures --
    never guess a fallback route). Runway transition is picked only when
    exactly one exists with legs (an approach/STAR commonly fans out to
    several runway transitions -- without a cleared runway, which one the
    aircraft will actually fly is genuinely unknown, so an ambiguous case
    returns the entry+common legs only, with runway explicitly omitted
    rather than guessed)."""
    transitions = get_procedure_transitions(dest_airport.upper(), proc_type,
                                             arr_proc.upper())
    if not transitions:
        return None

    legs: list[dict] = []
    if entry_fix:
        entry_key = entry_fix.strip().upper()
        if entry_key in transitions:
            legs.extend(transitions[entry_key])
    legs.extend(transitions.get("(common)", []))

    rw_transitions = [t for t in transitions if t.startswith("RW") and transitions[t]]
    if len(rw_transitions) == 1:
        legs.extend(transitions[rw_transitions[0]])

    return legs


def runway_eta_epoch(callsign: str, dest_icao: str) -> float | None:
    """Best available runway-threshold arrival estimate for one flight, as a
    UNIX EPOCH float (what flight_events.arrival_time is declared to hold).
    None when no defensible answer exists -- never a guess.

    2026-09-05: extracted as the SINGLE implementation shared by both
    writers of flight_events.arrival_time, because having two of them is
    exactly how their semantics silently diverged:

      - ingest/parsers/fdps_parser.write_flight_event() (inline, on every
        FDPS write)
      - poller/skills/tbfm_arrival_enrichment.py (batch, every 15 min)

    The batch skill took tbfm_sequences.eta verbatim as the arrival time
    regardless of which arc it was, so an "mfx" row -- an ETA to the METER
    FIX, still tens of miles out -- landed in the column as if it were a
    runway arrival, systematically early. Meanwhile the inline path added
    CIFP transit unconditionally, double-counting when the row was already
    a runway ETA. They then overwrote each other every 15 minutes.

    Arc handling (tbfm_sequences.eta_kind, added in db.init_db_v46):
      rwy      TBFM's own runway-threshold ETA -- authoritative, use as-is.
               Strictly better than our static-geometry estimate.
      mfx      ETA to the meter fix -- the ONLY arc the CIFP fix geometry
               matches. Add fix->runway transit via estimate_runway_eta().
      dfx/sfx  ETA to a descent/speed fix, a different point than meter_fix
               -- transit measured from the meter fix would be wrong. Skip.
      None     Pre-v46 row, arc unknown. Skip; never assume "mfx". Self-heals
               as TBFM re-upserts (every consumer gates on freshness anyway).

    Sequences are walked freshest-first and the first usable one wins: a
    flight carries many concurrent rows (42 observed for one flight), and
    TBFM interleaves real fixes with facility/meter-point pseudo-labels
    (ZDC, DC_MET, IAD_MP, ...) that resolve to no coordinates at all.
    """
    if not callsign or not dest_icao:
        return None
    dest_icao = dest_icao.strip().upper()

    def _resolve(meter_fix):
        fix = resolve_fix(meter_fix)
        if fix:
            return (fix["lat"], fix["lon"])
        from ingest.parsers.tbfm_parser import DC_METER_FIXES
        return DC_METER_FIXES.get(meter_fix)

    return select_arrival_epoch(
        db.get_tbfm_sequences_for_flight(callsign), dest_icao, _resolve)


def select_arrival_epoch(seqs, dest_icao: str, resolve) -> float | None:
    """Pure arc-selection logic, shared by every consumer.

    `seqs`   : sequence dicts, FRESHEST FIRST.
    `resolve`: callable(meter_fix) -> (lat, lon) | None.

    Split out from runway_eta_epoch() 2026-09-05 so the batch consumer can
    supply a preloaded in-memory resolver instead of hitting the DB once
    per fix. Keeping the decision table in ONE place is the whole point --
    three consumers had already drifted apart when each held its own copy.
    """
    for seq in seqs:
        kind = seq.get("eta_kind")
        if kind == "rwy":
            try:
                return datetime.strptime(
                    seq["eta"], "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc).timestamp()
            except (ValueError, TypeError):
                continue
        if kind != "mfx":
            continue
        coords = resolve(seq["meter_fix"])
        if not coords:
            continue
        est = estimate_runway_eta(coords[0], coords[1], dest_icao, seq["eta"])
        if not est:
            continue
        try:
            return datetime.strptime(
                est["runway_eta"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, TypeError):
            continue
    return None


def load_fix_coords() -> dict:
    """All CIFP fix idents -> (lat, lon), unambiguous ones only (an ident in
    several ICAO regions is skipped, same never-guess rule resolve_fix()
    uses). One query; for batch callers that would otherwise do thousands."""
    out, dupes = {}, set()
    with db.conn() as c:
        for row in c.execute("SELECT ident, lat, lon FROM cifp_fixes"):
            ident = row["ident"]
            if ident in out:
                dupes.add(ident)
                continue
            out[ident] = (row["lat"], row["lon"])
    for d in dupes:
        out.pop(d, None)
    return out
