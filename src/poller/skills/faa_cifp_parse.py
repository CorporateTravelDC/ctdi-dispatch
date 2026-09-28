"""
faa_cifp_parse -- parses the CIFP zip faa_cifp_pull.py already downloads
into queryable DB tables (cifp_fixes / cifp_procedure_legs / cifp_holds),
for the Washington/Baltimore terminal area.

Column map verified 2026-09-05 by byte-inspection of the real cycle 2609
file (CIFP_260903.zip), not from memory of the ARINC 424-18 spec --
several commonly-cited layouts are off by one in the altitude/speed
block. See docs/CIFP_DATA.md for the full column reference and the
validation assertions this parser is checked against.

Runs after faa_cifp_pull.py (same weekly timer cadence, offset a few
minutes later) -- idempotent, only re-parses when the downloaded cycle
differs from what's already in cifp_meta.

Fix-catalog scope (cifp_fixes): every navaid/waypoint/airport/runway
inside the DC-area lat/lon box below, UNION every distinct (fix,
fix_region) actually referenced by a leg of a SID/STAR/approach at one
of the 21 airports in scope -- the union is what makes an out-of-box
enroute-transition fix like HVQ (Charleston, WV -- outside the box but a
real GIBBZ6/TRUPS6 transition fix) resolvable. A box-only catalog would
silently miss it.

SR-2: exempt -- deterministic, no LLM call.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile

from common import db
from common.sr1_log import log_usage
from poller.skills.faa_cifp_pull import current_cifp_zip

log = logging.getLogger(__name__)

SKILL_NAME = "faa-cifp-parse"

# Same 21-airport DC/Baltimore terminal-area scope as the parsing spec
# this was built against -- see docs/CIFP_DATA.md.
_AIRPORTS = frozenset({
    "KIAD", "KDCA", "KBWI", "KADW", "KHEF", "KJYO", "KMTN", "KDMW", "KDAA",
    "KNYG", "KGAI", "KFDK", "KFME", "KCGS", "KVKX", "KESN", "KOKV", "KMRB",
    "KRMN", "KW32", "KAPG",
})
# latmin, latmax, lonmin, lonmax
_BOX = (37.3, 40.7, -80.5, -74.3)

_SUB = {"D": "SID", "E": "STAR", "F": "APPROACH"}
_HOLD_MEANING = {
    "HA": "hold_to_altitude",
    "HF": "hold_to_fix_one_circuit",
    "HM": "hold_manual_until_atc",
}


def _dms_lat(s: str) -> float | None:
    if not s.strip():
        return None
    h = s[0]
    v = int(s[1:3]) + int(s[3:5]) / 60 + (int(s[5:7]) + int(s[7:9]) / 100) / 3600
    return round(-v if h == "S" else v, 6)


def _dms_lon(s: str) -> float | None:
    if not s.strip():
        return None
    h = s[0]
    v = int(s[1:4]) + int(s[4:6]) / 60 + (int(s[6:8]) + int(s[8:10]) / 100) / 3600
    return round(-v if h == "W" else v, 6)


def _g(line: str, a: int, b: int) -> str:
    """1-based inclusive column slice."""
    return line[a - 1:b].strip()


def _parse_lines(lines: list[str], cycle: str) -> tuple[list[dict], list[dict], list[dict]]:
    # ---------- fix catalog (points) ----------
    points: dict[tuple[str, str], dict] = {}

    def add(ident: str, region: str, typ: str, lat, lon, name: str,
            parent_airport: str | None = None) -> None:
        if lat is None or lon is None or not ident:
            return
        k = (ident, region)
        if k in points:
            return
        points[k] = {
            "ident": ident, "icao_region": region, "type": typ,
            "lat": lat, "lon": lon, "name": name,
            "parent_airport": parent_airport, "cycle": cycle,
        }

    for l in lines:
        if len(l) < 52:
            continue
        sec = l[4]
        if sec == "D" and l[5] in (" ", "B"):
            typ = "VHF_NAVAID" if l[5] == " " else "NDB"
            add(_g(l, 14, 17), _g(l, 20, 21), typ,
                _dms_lat(_g(l, 33, 41)), _dms_lon(_g(l, 42, 51)), _g(l, 94, 123))
        elif sec == "E" and l[5] == "A":
            add(_g(l, 14, 18), _g(l, 20, 21), "ENROUTE_WPT",
                _dms_lat(_g(l, 33, 41)), _dms_lon(_g(l, 42, 51)), _g(l, 99, 123))
        elif sec == "P" and l[12] == "C":
            add(_g(l, 14, 18), _g(l, 20, 21), "TERMINAL_WPT",
                _dms_lat(_g(l, 33, 41)), _dms_lon(_g(l, 42, 51)), _g(l, 99, 123),
                parent_airport=_g(l, 7, 10))
        elif sec == "P" and l[12] == "G":
            rw, apt = _g(l, 14, 18), _g(l, 7, 10)
            add(apt + "/" + rw, _g(l, 11, 12), "RUNWAY_THRESHOLD",
                _dms_lat(_g(l, 33, 41)), _dms_lon(_g(l, 42, 51)), apt + " " + rw,
                parent_airport=apt)
        elif sec == "P" and l[12] == "A":
            add(_g(l, 7, 10), _g(l, 11, 12), "AIRPORT",
                _dms_lat(_g(l, 33, 41)), _dms_lon(_g(l, 42, 51)), _g(l, 94, 123))

    def coords(fix: str, region: str, apt: str | None) -> tuple:
        if fix.startswith("RW") and apt:
            p = points.get((apt + "/" + fix, region))
            if p:
                return p["lat"], p["lon"], p["type"]
        p = points.get((fix, region))
        if not p:
            cand = [v for (i, _r), v in points.items() if i == fix]
            p = cand[0] if len(cand) == 1 else None
        return (p["lat"], p["lon"], p["type"]) if p else (None, None, None)

    # ---------- procedure legs ----------
    legs: list[dict] = []
    for l in lines:
        if len(l) < 50 or l[4] != "P" or l[12] not in "DEF":
            continue
        apt = _g(l, 7, 10)
        if apt not in _AIRPORTS:
            continue
        if _g(l, 39, 39) not in ("0", "1"):
            continue  # skip continuation records
        fix = _g(l, 30, 34)
        fix_region = _g(l, 35, 36)
        lat, lon, fix_type = coords(fix, fix_region, apt) if fix else (None, None, None)
        legs.append({
            "airport": apt, "proc_type": _SUB[l[12]], "procedure": _g(l, 14, 19),
            "transition": _g(l, 21, 25) or "(common)", "seq": int(_g(l, 27, 29) or 0),
            "fix": fix or None, "fix_region": fix_region or None, "fix_type": fix_type,
            "path_term": _g(l, 48, 49), "turn": _g(l, 44, 44) or None,
            "lat": lat, "lon": lon,
            "alt_desc": _g(l, 83, 83) or None, "alt1": _g(l, 85, 89) or None,
            "alt2": _g(l, 90, 94) or None, "speed_limit": _g(l, 100, 102) or None,
            "cycle": cycle,
        })

    # ---------- holds (legs whose path/terminator is HA/HF/HM) ----------
    # A second pass over the raw lines, not derived from `legs` above --
    # dist_or_time (cols 75-78) isn't kept on the leg dict (not needed
    # there), and hold rows need it for leg_time_min/leg_dist_nm.
    hold_rows: list[dict] = []
    for l in lines:
        if len(l) < 50 or l[4] != "P" or l[12] not in "DEF":
            continue
        apt = _g(l, 7, 10)
        if apt not in _AIRPORTS:
            continue
        if _g(l, 39, 39) not in ("0", "1"):
            continue
        path_term = _g(l, 48, 49)
        if path_term not in _HOLD_MEANING:
            continue
        fix = _g(l, 30, 34)
        fix_region = _g(l, 35, 36)
        lat, lon, fix_type = coords(fix, fix_region, apt) if fix else (None, None, None)
        mag_course = _g(l, 71, 74)
        d = _g(l, 75, 78)
        hold_rows.append({
            "airport": apt, "proc_type": _SUB[l[12]], "procedure": _g(l, 14, 19),
            "transition": _g(l, 21, 25) or None, "seq": int(_g(l, 27, 29) or 0),
            "fix": fix, "fix_type": fix_type, "lat": lat, "lon": lon,
            "hold_type": path_term, "hold_meaning": _HOLD_MEANING[path_term],
            "turn_direction": {"L": "left", "R": "right"}.get(_g(l, 44, 44), None),
            "inbound_course_mag": (float(mag_course) / 10 if mag_course else None),
            "leg_time_min": (float(d[1:]) / 10 if d.startswith("T") else None) if d else None,
            "leg_dist_nm": (float(d) / 10 if d and not d.startswith("T") else None),
            "alt_desc": _g(l, 83, 83) or None, "alt1": _g(l, 85, 89) or None,
            "alt2": _g(l, 90, 94) or None, "speed_limit": _g(l, 100, 102) or None,
            "cycle": cycle,
        })

    # ---------- fix catalog scope: regional box UNION every leg-referenced fix ----------
    regional = [p for p in points.values()
                if p["lat"] is not None and p["lon"] is not None
                and _BOX[0] <= p["lat"] <= _BOX[1] and _BOX[2] <= p["lon"] <= _BOX[3]]
    referenced_keys = {(r["fix"], r["fix_region"]) for r in legs if r["fix"]}
    referenced = [points[k] for k in referenced_keys if k in points]
    seen = set()
    fixes: list[dict] = []
    for p in regional + referenced:
        k = (p["ident"], p["icao_region"])
        if k in seen:
            continue
        seen.add(k)
        fixes.append(p)

    return fixes, legs, hold_rows


def parse_and_store() -> dict:
    ref = current_cifp_zip()
    if ref is None:
        return {"ok": False, "reason": "no_cifp_zip_downloaded_yet"}
    path, cycle = ref

    if db.cifp_meta_get("cycle") == cycle:
        log.info("faa-cifp-parse: cycle %s already parsed, skipping", cycle)
        return {"ok": True, "cycle": cycle, "changed": False}

    started = time.time()
    with zipfile.ZipFile(path) as zf:
        with zf.open("FAACIFP18") as f:
            raw = f.read()
    lines = raw.decode("latin-1").splitlines()

    fixes, legs, holds = _parse_lines(lines, cycle)
    result = db.cifp_replace_all(cycle, fixes, legs, holds)
    result["elapsed_sec"] = round(time.time() - started, 1)
    log.info("faa-cifp-parse: %s", result)
    return result


def main() -> None:
    status = "error"
    try:
        result = parse_and_store()
        status = "success" if result.get("ok") else "error"
        log.info("faa-cifp-parse: %s", result)
    except Exception as e:
        log.error("faa-cifp-parse failed: %s", e)
        try:
            from common import ntfy_push
            ntfy_push.send(
                "ops-health", str(e), title="FAA CIFP parse FAILED",
                priority=3, tags="warning",
            )
        except Exception:
            pass
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
