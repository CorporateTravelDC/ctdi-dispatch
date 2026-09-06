"""
ATCSCC ops plan fetcher — daily traffic management snapshot.

Synthesizes the FAA/ATCSCC daily operations picture from live NAS data:
  - Active GDP / GS / AAR programs (from NAS OIS)
  - Active NOTAMs for DC-area airports (from NOTAM table)
  - Current METAR conditions at primary stations
  - Derived pattern tags for longitudinal analysis

One record per calendar day, kept indefinitely (never purged).
Intent: weekly/monthly cadence analysis of traffic management patterns
correlating with weather, seasons, and events.

Runs once per day at 07:00 ET via the daily-opsplan timer.
Also callable on-demand via admin REST (force-opsplan trigger).

Pattern tags derived (extensible):
  weather-gdp         GDP correlates with IFR conditions
  volume-delay        GDP without significant weather (demand-driven)
  vip-airspace        VIP/POTUS TFR active on this day
  multi-airport-gdp   GDPs at 2+ airports simultaneously
  ground-stop         Any ground stop program active
"""

import json
import logging
import time
from datetime import date as _date, datetime, timezone

from common import config, db

log = logging.getLogger(__name__)

FEED_NAME = "atcscc_opsplan"

# IFR threshold for weather-gdp tag: ceiling < 1000 ft OR vis < 3 SM
IFR_CEILING_FT = 1000
IFR_VIS_SM = 3.0


def _derive_pattern_tags(nas_programs: list, metars: list,
                         has_vip_tfr: bool) -> list[str]:
    """Derive searchable pattern tags from today's operational picture."""
    tags = []

    gdp_airports = [p["facility"] for p in nas_programs if p["type"] == "GDP"]
    gs_airports = [p["facility"] for p in nas_programs if p["type"] == "GS"]

    if gs_airports:
        tags.append("ground-stop")

    if len(gdp_airports) >= 2:
        tags.append("multi-airport-gdp")

    if gdp_airports:
        # Check if IFR conditions exist at any primary station
        primary_ifr = any(
            (m.get("ceiling_ft") or 9999) < IFR_CEILING_FT or
            (m.get("visibility_sm") or 99.0) < IFR_VIS_SM
            for m in metars
            if m.get("station") in ("KDCA", "KIAD", "KBWI")
        )
        if primary_ifr:
            tags.append("weather-gdp")
        else:
            tags.append("volume-delay")

    if has_vip_tfr:
        tags.append("vip-airspace")

    if not nas_programs and not has_vip_tfr:
        tags.append("normal-ops")

    return tags


def _metar_summary(metars: list) -> str:
    """Brief METAR summary for primary stations."""
    primaries = [m for m in metars if m.get("station") in ("KDCA", "KIAD", "KBWI")]
    if not primaries:
        return "No primary station data"
    lines = [
        f"{m['station']}: {m.get('ceiling_ft','?')}ft "
        f"{m.get('visibility_sm','?')}SM "
        f"{m.get('wind_kt','?')}kt"
        + (f" {m['precip_code']}" if m.get("precip_code") else "")
        for m in primaries
    ]
    return " | ".join(lines)


def run(force_date: str | None = None) -> dict:
    """
    Snapshot today's (or a specific date's) ATCSCC operational picture.
    force_date: override date (YYYY-MM-DD) — for backfill or testing.
    """
    fetched_at = time.time()
    plan_date = force_date or _date.today().isoformat()

    try:
        # Gather current NAS programs
        nas_programs = db.get_active_nas_programs()

        # Gather current METAR snapshot
        metars = db.get_metar_snapshot()

        # Check for active VIP TFRs today
        active_tfrs = db.get_active_tfrs()
        has_vip_tfr = any(t["is_vip"] for t in active_tfrs)

        # NOTAM count for today
        notams = db.get_active_notams()
        notam_count = len(notams)

        # Affected airports (unique across all programs)
        affected_airports = sorted(set(
            p["facility"] for p in nas_programs if p.get("facility")
        ))

        # Derive pattern tags
        pattern_tags = _derive_pattern_tags(nas_programs, metars, has_vip_tfr)

        # Weather summary
        weather_summary = _metar_summary(metars)

        db.upsert_atcscc_opsplan(
            plan_date=plan_date,
            nas_programs=nas_programs,
            notam_count=notam_count,
            active_airports=affected_airports,
            pattern_tags=pattern_tags,
            weather_summary=weather_summary,
        )

        db.upsert_feed(FEED_NAME, fetched_at, error=None,
                       payload_hash=plan_date)  # Date is the natural key
        log.info(
            "ATCSCC opsplan snapshot — date=%s programs=%d tags=%s",
            plan_date, len(nas_programs), pattern_tags
        )
        return {
            "plan_date": plan_date,
            "program_count": len(nas_programs),
            "pattern_tags": pattern_tags,
        }

    except Exception as e:
        msg = str(e)
        log.error("ATCSCC opsplan FAILED: %s", msg)
        db.upsert_feed(FEED_NAME, fetched_at, error=msg)
        return {"error": msg}


if __name__ == "__main__":
    import argparse, sys
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="ATCSCC ops plan snapshot")
    parser.add_argument("--date", default=None,
                        help="Override date (YYYY-MM-DD) for backfill")
    args = parser.parse_args()
    result = run(force_date=args.date)
    print(json.dumps(result, indent=2))
