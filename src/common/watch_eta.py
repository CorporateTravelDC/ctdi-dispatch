"""common.watch_eta -- the ONE arrival estimate shown for a watched flight.

Operator decision 2026-10-16: a watch ETA comes from SWIM only, in this order:

  1. TBFM, the destination center's metering (tbfm_sequences). Only when the
     watch's destination is one TBFM covers here (ingest keeps DC-area
     arrivals only, tbfm_parser.DC_AREA_APTS), so a TBFM time is always the
     destination center's own. Arc matters (tbfm_sequences.eta_kind):
       rwy      runway-threshold time, used as is
       mfx      time AT THE METER FIX, labelled with the fix; a runway time is
                added only as "estimated from meter fix X" (CIFP geometry)
       dfx/sfx  time at a descent/speed fix on that arrival, labelled as such,
                never presented as an arrival time
  2. TFMS's own estimate (flight_ooooi_times.tfms_eta, pg_schema/0083), unless
     its etaType is SCHEDULED (a schedule is not an estimate).
  3. FDPS's flight_events.arrival_time (itself a TBFM-derived runway estimate,
     written by cifp_lookup.runway_eta_epoch), labelled as such.

Nothing else can select or override it: no FIDS (never a time source), no
AeroAPI, no ADS-B, no ACARS ETA text, no airline-posted schedule. This module
imports none of them (tests/common/test_watch_eta.py holds that with an AST
check). Every result names its source, tier, the point the time refers to and,
for a fix time, the fix. Times are UTC with ET beside, never ET alone.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

TFMS_MAX_AGE_S = 6 * 3600
FDPS_MAX_AGE_S = 7200
# airline + flight number, the same split db.get_flight_plan_by_callsign uses
_ICAO_CALLSIGN_RE = re.compile(r"^([A-Z]{2,3})(\d+[A-Z]?)$")
_POINT_WORDS = {"dfx": "descent fix", "sfx": "speed fix"}


@dataclass(frozen=True)
class WatchEta:
    epoch: float                 # time at `point`
    point: str                   # runway | meter_fix | descent_fix | speed_fix
    source: str                  # tbfm | tfms | fdps
    tier: int                    # 1 tbfm, 2 tfms, 3 fdps
    label: str                   # user-facing, UTC with ET beside
    fix: str | None = None       # the meter fix the time refers to / was taken at
    facility: str | None = None  # TBFM facility (destination center)
    runway_epoch: float | None = None   # best defensible runway-threshold time
    runway_from_fix: bool = False       # runway_epoch estimated from a meter-fix time

    def as_dict(self) -> dict:
        d = asdict(self)
        d["eta_utc"] = _iso(self.epoch)
        d["runway_eta_utc"] = _iso(self.runway_epoch) if self.runway_epoch else None
        return d


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_utc_et(epoch: float) -> str:
    """'21:49Z (17:49 ET)' -- the glossary rule: UTC, or UTC with ET beside."""
    from zoneinfo import ZoneInfo
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return f"{dt:%H:%MZ} ({dt.astimezone(ZoneInfo('America/New_York')):%H:%M} ET)"


def _parse(ts) -> float | None:
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _dest3(dest: str | None) -> str:
    d = (dest or "").strip().upper()
    return d[1:] if len(d) == 4 and d.startswith("K") else d


def tbfm_covers(destination: str | None) -> bool:
    from ingest.parsers.tbfm_parser import DC_AREA_APTS
    return _dest3(destination) in DC_AREA_APTS


def label_tbfm_sequence(seq: dict, runway_epoch: float | None = None) -> str | None:
    """The label for one TBFM sequence row's ETA, by arc. None if unusable.
    Shared by the resolver and tbfm_parser's watch push, so the wording of a
    fix time can never drift between the push and the panel/API."""
    t = _parse(seq.get("eta"))
    if t is None:
        return None
    kind = seq.get("eta_kind")
    fix = (seq.get("meter_fix") or "?").upper()
    fac = seq.get("facility") or "TBFM"
    if kind == "rwy":
        return f"runway ETA {fmt_utc_et(t)} (TBFM {fac}, sequenced at meter fix {fix})"
    if kind == "mfx":
        s = f"ETA {fmt_utc_et(t)} at meter fix {fix} (TBFM {fac})"
        if runway_epoch:
            s += f"; runway ~{fmt_utc_et(runway_epoch)}, estimated from meter fix {fix}"
        return s
    if kind in _POINT_WORDS:
        return (f"ETA {fmt_utc_et(t)} at a {_POINT_WORDS[kind]} on the {fix} arrival "
                f"(TBFM {fac}) -- not a runway time")
    return None


def _from_tbfm(seqs: list[dict], destination: str, resolve) -> WatchEta | None:
    from common import cifp_lookup
    usable = [s for s in seqs if s.get("eta_kind") in ("rwy", "mfx", "dfx", "sfx") and _parse(s.get("eta"))]
    if not usable:
        return None
    rwy = [s for s in usable if s["eta_kind"] == "rwy"]
    mfx = [s for s in usable if s["eta_kind"] == "mfx"]
    real_mfx = [s for s in mfx if resolve and resolve(s.get("meter_fix"))]
    pick = (rwy or real_mfx or mfx or usable)[0]          # each list is freshest first
    t = _parse(pick["eta"])
    kind, fix, fac = pick["eta_kind"], (pick.get("meter_fix") or "").upper() or None, pick.get("facility")
    if kind == "rwy":
        return WatchEta(t, "runway", "tbfm", 1, label_tbfm_sequence(pick), fix, fac, t, False)
    if kind == "mfx":
        rwy_est = None
        if resolve:
            try:
                rwy_est = cifp_lookup.select_arrival_epoch([pick], "K" + _dest3(destination), resolve)
            except Exception as e:  # geometry is optional; the fix time stands on its own
                log.debug("watch_eta: runway estimate failed for %s: %s", fix, e)
        return WatchEta(t, "meter_fix", "tbfm", 1, label_tbfm_sequence(pick, rwy_est), fix, fac,
                        rwy_est, rwy_est is not None)
    return WatchEta(t, _POINT_WORDS[kind].replace(" ", "_"), "tbfm", 1, label_tbfm_sequence(pick),
                    fix, fac, None, False)


def resolve_for_watch(entry: dict) -> WatchEta | None:
    """The watch ETA for a flight watch entry. The public identifier (UA1742,
    EK231) is tried in its ICAO form and through every current codeshare /
    ATC-callsign link (flight_instance.operating_identities_detail); the first
    identity a SWIM tier answers for wins."""
    from common import flight_instance as fi
    if (entry.get("entry_type") or "flight") != "flight":
        return None
    dest = entry.get("destination")
    try:
        idents = fi.operating_identities_detail(entry.get("identifier"), entry.get("origin"), dest)
    except Exception as e:
        log.debug("watch_eta: identities for %s: %s", entry.get("identifier"), e)
        idents = []
    seen = []
    for carrier, num, _rel in idents:
        cs = f"{carrier}{num}"
        if cs in seen:
            continue
        seen.append(cs)
        got = resolve(cs, dest)
        if got:
            return got
    return None


def resolve(callsign: str, destination: str | None, *, now: float | None = None,
            tbfm_seqs: list[dict] | None = None, tfms_row: dict | None = None,
            fdps_row: dict | None = None, fix_resolver=None, lookup: bool = True) -> WatchEta | None:
    """The watch ETA for one flight leg, or None when no SWIM source has one.
    Pass rows to resolve from given data; with lookup=True missing ones are read
    from the database. A failed read falls through to the next tier."""
    from common import db
    now = time.time() if now is None else now
    cs = (callsign or "").strip().upper()
    if not cs:
        return None

    if tbfm_covers(destination):
        seqs = tbfm_seqs
        if seqs is None and lookup:
            try:
                seqs = db.get_tbfm_sequences_for_flight(cs)
            except Exception as e:
                log.debug("watch_eta: tbfm read failed for %s: %s", cs, e)
        if seqs:
            res = fix_resolver
            if res is None and lookup:
                from common import cifp_lookup

                def res(name):
                    f = cifp_lookup.resolve_fix(name)
                    return (f["lat"], f["lon"]) if f else None
            got = _from_tbfm(seqs, destination, res)
            if got:
                return got

    row = tfms_row
    if row is None and lookup:
        try:
            row = db.get_tfms_eta(cs, destination)
        except Exception as e:
            log.debug("watch_eta: tfms read failed for %s: %s", cs, e)
    if row and (row.get("tfms_eta_type") or "").upper() != "SCHEDULED":
        t, at = _parse(row.get("tfms_eta")), _parse(row.get("tfms_eta_at"))
        if t is not None and at is not None and now - at <= TFMS_MAX_AGE_S:
            kind = (row.get("tfms_eta_type") or "estimated").lower()
            return WatchEta(t, "runway", "tfms", 2, f"ETA {fmt_utc_et(t)} (TFMS {kind})", runway_epoch=t)

    frow = fdps_row
    # 2026-10-21: split the callsign onto the indexed (airline, flight_num) pair.
    # "airline || flight_num = ?" could not use idx_flight_events_airline_num and
    # scanned the whole table: every call hit the 30 s statement timeout (75 in
    # 90 min after the 10-21 rollout), and the poller's loop stalled ~50 min at
    # a time behind it (NAS, TFR, METAR fetchers starved).
    m = _ICAO_CALLSIGN_RE.match(cs or "")
    if frow is None and lookup and m:
        try:
            with db.conn() as c:
                r = c.execute(
                    "SELECT arrival_time, updated_at FROM flight_events WHERE airline = ? AND flight_num = ? "
                    "AND arrival_time IS NOT NULL ORDER BY updated_at DESC LIMIT 1",
                    (m.group(1), m.group(2))).fetchone()
            frow = dict(r) if r else None
        except Exception as e:
            log.debug("watch_eta: fdps read failed for %s: %s", cs, e)
    if frow:
        t, up = _parse(frow.get("arrival_time")), _parse(frow.get("updated_at"))
        if t is not None and (up is None or now - up <= FDPS_MAX_AGE_S):
            return WatchEta(t, "runway", "fdps", 3,
                            f"est. runway arrival {fmt_utc_et(t)} (FDPS record, derived from TBFM)",
                            runway_epoch=t)
    return None
