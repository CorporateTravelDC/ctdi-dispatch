"""
cps-recompute — SR-1 + SR-2 compliant. API-free deterministic rule engine.

Schedule: hourly at :05 (corporatetraveldc-cps-recompute.timer)
SR-1: log_usage() in finally block
SR-2: check_gate()/commit_gate() on raw numeric METAR inputs + active NAS
      programs + ITWS hazard rows + (2026-08-30) per-runway STDDS RVR and
      the ITWS active-runway-configuration state

Produces a CPS (Critical Predictability State) traffic-light score
mapped to Part 135.609 HEMS minimums using a deterministic rule engine.
No AI API calls. Always produces a result.
Writes result to cps_scores table; pusher fires ntfy topic "cps"
(score/label CHANGE only -- push_cps_update() keys its state file on
"SCORE/LABEL", so narrative-only changes, e.g. the visibility-source
annotation added 2026-08-30, never generate a push on their own).
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from common import db
from common.sr1_log import log_usage
from common.sr2_gate import check_gate, commit_gate

log = logging.getLogger(__name__)

SKILL_NAME = "cps-recompute"
MODEL = "deterministic"   # no model; kept for SR-1 log_usage signature

# Part 135.609 HEMS VFR minimums (DC area / Class B):
#   Ceiling >= 1000 ft, Visibility >= 3 SM, Wind <= 30 kt
CPS_MINIMUMS = {"ceiling_ft": 1000, "visibility_sm": 3.0, "wind_kt": 30}

# Primary observation stations for DC-area HEMS scoring (in priority order)
PRIMARY_STATIONS = ("KDCA", "KIAD", "KBWI")

# ITWS wind-shear-relevant products -- 2026-07-21. Both Microburst and Wind
# Shear ATIS fire at severity 5 (severe, per itws_parser.py's 1-6 scale)
# when active; Gust Front ETI fires at severity 4 (moderate, advance
# warning of a pending event, not yet at the runway). METAR wind_kt alone
# is a steady-state reading -- it can miss a microburst or wind shear
# event entirely if the ATIS report lands between METAR observation
# cycles, which is exactly the gap this closes. Rows older than
# ITWS_STALE_SECONDS are ignored -- itws_alerts is a continuously
# upserted "current state" table (see db.upsert_itws_alert), so a hazard
# that cleared should not keep degrading CPS just because the ingest
# container hasn't pushed a fresher OFF/none-pending row yet.
ITWS_SEVERE_PRODUCTS = frozenset({"Wind Shear ATIS Product", "Microburst ATIS Product"})
ITWS_GUST_FRONT_PRODUCT = "Gust Front ETI Product"
ITWS_STALE_SECONDS = 1200

# Precip codes that violate or degrade minimums
PRECIP_VIOLATED = frozenset({
    "TS", "TSRA", "TSGR", "TSPL", "TSSN", "FZRA", "FZDZ", "FZFG",
    "SN", "SG", "PL", "IC", "GR", "GS",
})
PRECIP_MARGINAL = frozenset({
    "RA", "DZ", "BR", "FG", "HZ", "FU", "VA", "SHRA", "RASN",
})

# ── RVR -> visibility substitution (added 2026-08-30, SWIM-audit backlog #1) ──
#
# stdds_rvr (common/db_swim.py, fed by the STDDS APDS RVRDataUpdateMessage
# stream since 2026-08-30) carries real per-runway touchdown/midpoint/
# rollout RVR in FEET for the DC airports. RVR is a localized transmissometer
# reading in hundreds of feet; METAR visibility_sm is prevailing visibility
# in statute miles -- there is NO linear conversion between them. The FAA
# publishes an official correlation table for exactly this substitution,
# in 14 CFR 91.175(h) "Comparable values of RVR and ground visibility"
# (the same table appears in AIM 5-4-20 Table 5-4-1 and FAA Order 8900.1):
#
#       RVR (ft)   Visibility (SM)
#       1,600      1/4
#       2,400      1/2
#       3,200      5/8
#       4,000      3/4
#       4,500      7/8
#       5,000      1
#       6,000      1 1/4
#
# Those regulation values are what _RVR_TO_VIS_SM encodes, transcribed from
# 14 CFR 91.175(h) (stable regulation text; no vendored copy exists in this
# repo -- OPERATOR: verify the seven pairs against the CFR before the
# sign-off that lets this influence live scores). Readings between table
# rows are mapped DOWN to the next lower row (the conservative direction
# for a safety score: never credit more visibility than the table attests),
# and readings below 1,600 ft are "less than 1/4 SM".
#
# The table tops out at 6,000 ft = 1 1/4 SM, which is also RVR's maximum
# reportable value ("6000+" -- the sensor saturates there in good weather).
# A max-range reading therefore attests only ">= 1 1/4 SM" and can say
# NOTHING about the Part 135.609 3.0 SM minimum -- when every usable
# touchdown sensor reads >= 6,000 ft, METAR prevailing visibility governs
# and the max-range RVR is recorded in the audit trail but never scored.
# Corollary: any genuinely-measuring touchdown RVR (< 6,000 ft) converts to
# at most 1 1/4 SM, which is ALWAYS below the 3.0 SM minimum -- so an RVR
# substitution can only ever confirm/force a visibility violation, never
# upgrade a METAR-based one. That asymmetry is exactly why wiring RVR in
# cannot loosen the score.
_RVR_TO_VIS_SM: tuple[tuple[int, float, str], ...] = (
    (6000, 1.25,  "1 1/4"),
    (5000, 1.0,   "1"),
    (4500, 0.875, "7/8"),
    (4000, 0.75,  "3/4"),
    (3200, 0.625, "5/8"),
    (2400, 0.5,   "1/2"),
    (1600, 0.25,  "1/4"),
)
RVR_MAX_REPORTABLE_FT = 6000

# "Recent" for an RVR reading: 900 s. The APDS stream rebroadcasts each
# airport's RVR continuously (roughly once a minute while a sensor
# reports -- confirmed live 2026-08-30), so a row older than 15 min means
# the sensor or the STDDS feed stopped (the thermal-ingest-guard sheds
# stdds in its FIRST tier, so brief gaps are routine) and the reading must
# not be trusted for a safety score -- fall back to METAR. Matches the
# documented default in db_swim.get_stdds_rvr() for the same reason.
RVR_MAX_AGE_S = 900

# The ITWS Runway Configuration Product (itws_parser
# _handle_runway_configuration, live 2026-08-30) upserts a severity-0
# current-state row into itws_alerts whose detail carries the active
# configuration name, e.g. "Active runway configuration: IAD-19L-19C-12".
# It is an on-change/low-frequency broadcast (hours can pass between
# arrivals), so freshness here is generous -- 6 h -- and ONLY gates which
# runways' RVR we PREFER, never a scored value: with no (fresh) config we
# fall back to the worst touchdown RVR across ALL reporting runways, which
# is the conservative superset.
RUNWAY_CONFIG_PRODUCT = "Runway Configuration Product"
RUNWAY_CONFIG_MAX_AGE_S = 6 * 3600
_RUNWAY_CONFIG_DETAIL_PREFIX = "Active runway configuration: "


def _rvr_equivalent_vis_sm(rvr_ft: int) -> tuple[float, str] | None:
    """14 CFR 91.175(h) correlation (values in _RVR_TO_VIS_SM above),
    floor-mapped. Returns (visibility_sm, human_label) or None when the
    reading is at/above RVR's 6,000 ft max reportable value (attests only
    ">= 1 1/4 SM" -- not usable against a 3.0 SM minimum, caller must use
    METAR). Below the table's 1,600 ft floor -> (0.0, "<1/4")."""
    if rvr_ft >= RVR_MAX_REPORTABLE_FT:
        return None
    for ft, sm, label in _RVR_TO_VIS_SM:
        if rvr_ft >= ft:
            return sm, label
    return 0.0, "<1/4"


def _norm_runway(rwy: str) -> str:
    """'01' -> '1', '01L' -> '1L': APDS RVR zero-pads the numeric part
    (confirmed in live stdds_rvr rows: KDCA '01'/'19') while ITWS config
    names don't ('IAD-19L-19C-12'); compare with the padding stripped."""
    rwy = rwy.strip().upper()
    return rwy.lstrip("0") or rwy


def _active_runways_from_config(config_name: str | None) -> frozenset[str]:
    """'IAD-19L-19C-12' -> {'19L', '19C', '12'} (normalized). The config
    name's first token is the airport; the rest are the active runways.
    ITWS does not label which of them are arrival vs departure runways, so
    ALL of them are treated as candidate arrival runways -- the scoring
    below takes the WORST touchdown RVR among them, which can only be
    conservative. Unparseable/empty -> empty set (caller falls back to
    all reporting runways)."""
    if not config_name:
        return frozenset()
    parts = [p for p in config_name.strip().upper().split("-") if p]
    if len(parts) < 2:
        return frozenset()
    return frozenset(_norm_runway(p) for p in parts[1:])


def build_inputs() -> dict:
    metars = db.get_metar_snapshot()
    nas = db.get_active_nas_programs()
    itws_all = db.get_active_itws_alerts()
    itws_relevant = ITWS_SEVERE_PRODUCTS | {ITWS_GUST_FRONT_PRODUCT}
    itws = [
        a for a in itws_all
        if a["airport"] in PRIMARY_STATIONS and a["product_type"] in itws_relevant
    ]

    # Per-runway RVR (2026-08-30). Freshness is resolved HERE (stale rows
    # simply drop out of the inputs) and the rows carry values only -- no
    # last_seen -- honoring check_gate()'s "hash only content-bearing
    # inputs, never timestamps" contract: an unchanged 6000+/U rebroadcast
    # must keep gating as unchanged. Defensive: stdds_rvr lives in the
    # separate db_swim module (created by ingest, not db.init_db_all()),
    # so a DB that predates the v41 schema must degrade to "no RVR", never
    # crash the score.
    rvr_rows: list[dict] = []
    try:
        from common import db_swim
        for sta in PRIMARY_STATIONS:
            for r in db_swim.get_stdds_rvr(sta, max_age_seconds=RVR_MAX_AGE_S):
                rvr_rows.append({
                    "airport":           r["airport"],
                    "runway":            r["runway"],
                    "touchdown_rvr_ft":  r["touchdown_rvr_ft"],
                    "touchdown_trend":   r["touchdown_trend"],
                    "midpoint_rvr_ft":   r["midpoint_rvr_ft"],
                    "rollout_rvr_ft":    r["rollout_rvr_ft"],
                })
    except Exception as e:
        log.warning("%s: RVR unavailable (scoring falls back to METAR): %s",
                    SKILL_NAME, e)
        rvr_rows = []

    # Active runway configuration (2026-08-30) -- same freshness-at-build,
    # values-only discipline as the RVR rows above.
    runway_config: list[dict] = []
    for a in itws_all:
        if (a["airport"] in PRIMARY_STATIONS
                and a["product_type"] == RUNWAY_CONFIG_PRODUCT
                and _row_is_fresh(a, RUNWAY_CONFIG_MAX_AGE_S)):
            detail = a.get("detail") or ""
            if detail.startswith(_RUNWAY_CONFIG_DETAIL_PREFIX):
                runway_config.append({
                    "airport": a["airport"],
                    "config":  detail[len(_RUNWAY_CONFIG_DETAIL_PREFIX):],
                })

    return {
        "metar": sorted([
            {
                "station":       m["station"],
                "ceiling_ft":    m["ceiling_ft"],
                "visibility_sm": m["visibility_sm"],
                "wind_kt":       m["wind_kt"],
                "precip":        m["precip_code"],
            }
            for m in metars
        ], key=lambda x: x["station"]),
        "nas_programs": sorted([
            {"type": p["type"], "facility": p["facility"]}
            for p in nas
        ], key=lambda x: (x["type"], x["facility"])),
        "itws": sorted([
            {
                "airport":      a["airport"],
                "product_type": a["product_type"],
                "severity":     a["severity"],
                "detail":       a["detail"],
                "last_seen":    a["last_seen"],
            }
            for a in itws
        ], key=lambda x: (x["airport"], x["product_type"])),
        # 2026-08-30: RVR + active-runway-config are part of every score's
        # audit trail (SR-2 gate hash) from the moment they exist, per the
        # audit's wiring guardrails -- see the constants block above for
        # the freshness/units/no-timestamp rules they were built under.
        "rvr": sorted(rvr_rows, key=lambda x: (x["airport"], x["runway"])),
        "runway_config": sorted(runway_config, key=lambda x: x["airport"]),
    }


def _row_is_fresh(row: dict, max_age_s: float) -> bool:
    last_seen = row.get("last_seen")
    if not last_seen:
        return False
    try:
        seen_at = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - seen_at).total_seconds()
    return age <= max_age_s


def _itws_row_is_fresh(row: dict) -> bool:
    return _row_is_fresh(row, ITWS_STALE_SECONDS)


def _compute_cps(inputs: dict) -> dict:
    """
    Deterministic Part 135.609 HEMS go/no-go rule engine.
    Returns dict with score, label, factors, narrative.
    """
    primaries = {m["station"]: m for m in inputs["metar"]
                 if m["station"] in PRIMARY_STATIONS}
    nas = inputs["nas_programs"]
    itws_by_station: dict[str, list[dict]] = {}
    for row in inputs.get("itws", []):
        itws_by_station.setdefault(row["airport"], []).append(row)
    rvr_by_station: dict[str, list[dict]] = {}
    for row in inputs.get("rvr", []):
        rvr_by_station.setdefault(row["airport"], []).append(row)
    config_by_station = {r["airport"]: r["config"]
                         for r in inputs.get("runway_config", [])}
    # Per-station record of WHICH source produced the visibility judgment
    # (rvr vs metar, and why) -- surfaced in the narrative below so the
    # substitution is visible in the score's own output, not just a log
    # line (2026-08-30 audit guardrail).
    vis_sources: dict[str, str] = {}

    factors = {
        "ceiling":    "ok",
        "visibility": "ok",
        "wind":       "ok",
        "precip":     "ok",
        "airspace":   "ok",
        "gdp":        "ok",
    }

    worst = "ok"
    limiting: list[str] = []

    def degrade(factor: str, level: str, msg: str) -> None:
        nonlocal worst
        prev = factors[factor]
        # Never downgrade severity
        if level == "violated" or (level == "marginal" and prev == "ok"):
            factors[factor] = level
        if level == "violated":
            if worst != "violated":
                worst = "violated"
            if msg not in limiting:
                limiting.append(msg)
        elif level == "marginal":
            if worst == "ok":
                worst = "marginal"
            if msg not in limiting:
                limiting.append(msg)

    for sta in PRIMARY_STATIONS:
        m = primaries.get(sta)
        if not m:
            continue

        # Ceiling check
        c = m.get("ceiling_ft")
        if c is not None:
            if c < CPS_MINIMUMS["ceiling_ft"]:
                degrade("ceiling", "violated",
                        f"{sta} ceiling {c}ft (min {CPS_MINIMUMS['ceiling_ft']}ft)")
            elif c < 1500:
                degrade("ceiling", "marginal",
                        f"{sta} ceiling {c}ft marginal (1000–1500ft range)")

        # Visibility check. 2026-08-30 (SWIM-audit backlog #1): touchdown
        # RVR on the active arrival runway is preferred over METAR
        # prevailing visibility when a recent reading exists -- RVR is the
        # localized instrument measurement at the runway itself, and Part
        # 91.175(h) is the FAA's own authority for substituting one for
        # the other. Selection rules (each choice is the conservative one):
        #   - touchdown value only (the guardrail's preferred sensor;
        #     midpoint/rollout stay audit-trail-only);
        #   - restricted to the ITWS active-config runways when that state
        #     is known AND the names intersect (a naming mismatch or a
        #     config listing only non-RVR-equipped runways falls back to
        #     ALL reporting runways rather than silently discarding data);
        #   - WORST (minimum) reading among the selected runways;
        #   - a None touchdown value is a non-reporting sensor (see
        #     smes_parser._rvr_value_ft: blank/0 normalize to None, never
        #     to zero visibility) and contributes nothing;
        #   - a max-range reading (>= 6000 ft) cannot attest 3.0 SM, so
        #     METAR governs (recorded in vis_sources either way).
        v = m.get("visibility_sm")
        rvr_scored = False
        reporting = [r for r in rvr_by_station.get(sta, [])
                     if r.get("touchdown_rvr_ft") is not None]
        if reporting:
            active = _active_runways_from_config(config_by_station.get(sta))
            on_active = [r for r in reporting
                         if _norm_runway(r["runway"]) in active]
            selected = on_active or reporting
            scope = "active-config rwys" if on_active else "all reporting rwys"
            worst_row = min(selected, key=lambda r: r["touchdown_rvr_ft"])
            ft = worst_row["touchdown_rvr_ft"]
            equiv = _rvr_equivalent_vis_sm(ft)
            if equiv is None:
                vis_sources[sta] = (f"metar (touchdown RVR at max range "
                                    f"{ft}ft+ across {scope})")
            else:
                eq_sm, eq_label = equiv
                rvr_scored = True
                vis_sources[sta] = (f"rvr rwy {worst_row['runway']} "
                                    f"{ft}ft ({scope})")
                # By table construction eq_sm <= 1.25 SM < the 3.0 SM
                # minimum -- a measuring RVR sensor is always a violation.
                degrade("visibility", "violated",
                        f"{sta} touchdown RVR {ft}ft rwy {worst_row['runway']}"
                        f" = {eq_label}SM per 91.175(h)"
                        f" (min {CPS_MINIMUMS['visibility_sm']}SM)")
        if not rvr_scored and v is not None:
            vis_sources.setdefault(sta, "metar")
            if v < CPS_MINIMUMS["visibility_sm"]:
                degrade("visibility", "violated",
                        f"{sta} vis {v}SM (min {CPS_MINIMUMS['visibility_sm']}SM)")
            elif v < 5.0:
                degrade("visibility", "marginal",
                        f"{sta} vis {v}SM marginal (3–5SM range)")

        # Wind check
        w = m.get("wind_kt")
        if w is not None:
            if w > CPS_MINIMUMS["wind_kt"]:
                degrade("wind", "violated",
                        f"{sta} wind {w}kt (max {CPS_MINIMUMS['wind_kt']}kt)")
            elif w >= 25:
                degrade("wind", "marginal",
                        f"{sta} wind {w}kt marginal (25–30kt range)")

        # ITWS wind shear / microburst / gust front check -- catches
        # short-duration events a steady-state METAR reading can miss
        # entirely between observation cycles. See ITWS_SEVERE_PRODUCTS
        # comment above for the severity mapping.
        for itws_row in itws_by_station.get(sta, []):
            if not _itws_row_is_fresh(itws_row):
                continue
            severity = itws_row.get("severity") or 0
            product = itws_row["product_type"]
            detail = itws_row.get("detail") or product
            if product in ITWS_SEVERE_PRODUCTS and severity >= 5:
                degrade("wind", "violated", f"{sta} {detail} (ITWS)")
            elif product == ITWS_GUST_FRONT_PRODUCT and severity >= 4:
                degrade("wind", "marginal", f"{sta} {detail} (ITWS)")

        # Precipitation check
        p = str(m.get("precip") or "").upper()
        precip_tokens = set(p.split())
        if precip_tokens & PRECIP_VIOLATED:
            codes = " ".join(sorted(precip_tokens & PRECIP_VIOLATED))
            degrade("precip", "violated", f"{sta} {codes}")
        elif precip_tokens & PRECIP_MARGINAL:
            codes = " ".join(sorted(precip_tokens & PRECIP_MARGINAL))
            degrade("precip", "marginal", f"{sta} {codes}")

    # NAS ground programs
    gsps = [p for p in nas
            if "GSP" in p.get("type", "").upper()
            or "GROUND_STOP" in p.get("type", "").upper()]
    gdps = [p for p in nas
            if "GDP" in p.get("type", "").upper()
            and p not in gsps]

    if gsps:
        facilities = ", ".join(p["facility"] for p in gsps[:3])
        degrade("gdp", "violated", f"Ground Stop at {facilities}")
    elif gdps:
        facilities = ", ".join(p["facility"] for p in gdps[:3])
        degrade("gdp", "marginal", f"GDP at {facilities}")

    # Score
    if worst == "violated":
        score, label = "RED", "NO-GO"
    elif worst == "marginal":
        score, label = "YELLOW", "MARGINAL"
    else:
        score, label = "GREEN", "GO"

    # Narrative
    if limiting:
        narrative = limiting[0]
        if len(limiting) > 1:
            extra = len(limiting) - 1
            narrative += f" (+{extra} other factor{'s' if extra > 1 else ''})"
    elif not primaries:
        narrative = "No primary station METAR data — CPS based on NAS programs only"
    else:
        narrative = "All factors within Part 135.609 HEMS minimums"

    # 2026-08-30: make the visibility source auditable in the stored score
    # itself whenever RVR data was in play at any primary station --
    # whether it drove the number (rvr ...) or was present but at max
    # range and deferred to METAR. Stations with plain METAR-only scoring
    # are omitted, so the narrative is byte-identical to the pre-RVR
    # engine whenever no RVR data exists (the common case, and the
    # explicit don't-silently-change-the-output guardrail). Pushes are
    # unaffected either way -- pusher keys on score/label only.
    rvr_notes = {s: src for s, src in vis_sources.items() if src != "metar"}
    if rvr_notes:
        narrative += " [vis src: " + "; ".join(
            f"{s}={rvr_notes[s]}" for s in sorted(rvr_notes)) + "]"

    return {
        "score":     score,
        "label":     label,
        "factors":   factors,
        "narrative": narrative,
    }


def main(force: bool = False) -> None:
    inputs = build_inputs()
    gate_result, _gate_hash = check_gate(SKILL_NAME, inputs, force=force)

    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping recompute", SKILL_NAME)
        sys.exit(0)

    status = "error"
    try:
        data = _compute_cps(inputs)

        db.insert_cps(
            score=data["score"],
            label=data["label"],
            factors=data["factors"],
            narrative=data["narrative"],
        )
        status = "ok"
        log.info("%s: %s/%s — %s", SKILL_NAME, data["score"], data["label"], data["narrative"])

    except Exception as e:
        log.error("%s: compute error: %s", SKILL_NAME, e)
        status = "error"
        raise
    finally:
        # SR-1: log with 0 tokens (deterministic — no API usage)
        # 2026-08-25 fix (Opus blind review C-7): only commit the gate
        # hash once we know this run didn't crash -- see
        # sr2_gate.commit_gate()'s docstring for why the write is
        # deferred until after the guarded work actually completes.
        if status != "error":
            commit_gate(SKILL_NAME, _gate_hash)
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true",
                        help="Bypass hash gate; recompute regardless of input state")
    args = parser.parse_args()
    main(force=args.force)
