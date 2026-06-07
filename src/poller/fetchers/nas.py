"""
NAS status fetcher — FAA NAS Status / OIS.
Endpoint: https://nasstatus.faa.gov/api/airport-status-information

Returns XML (not JSON). Confirmed shape against live endpoint 2026-05-26:
  <AIRPORT_STATUS_INFORMATION>
    <Update_Time>...</Update_Time>
    <Delay_type>
      <Name>General Arrival/Departure Delay Info</Name>
      <Arrival_Departure_Delay_List>
        <Delay>
          <ARPT>ATL</ARPT>
          <Reason>WX:Thunderstorms</Reason>
          <Arrival_Departure Type="Departure">
            <Min>16 minutes</Min><Max>30 minutes</Max><Trend>Increasing</Trend>
          </Arrival_Departure>
        </Delay>
      </Arrival_Departure_Delay_List>
    </Delay_type>
    <Delay_type>
      <Name>Airport Closures</Name>
      <Airport_Closure_List>
        <Airport>
          <ARPT>SUN</ARPT><Reason>...</Reason><Start>...</Start><Reopen>...</Reopen>
        </Airport>
      </Airport_Closure_List>
    </Delay_type>
  </AIRPORT_STATUS_INFORMATION>

Fetches ground delays, closures, and other NAS programs.
Polled every 5 minutes by the poller scheduler.
"""

import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET

import requests

from common import db

log = logging.getLogger(__name__)

NAS_URL = "https://nasstatus.faa.gov/api/airport-status-information"
FETCH_TIMEOUT = 10


def fetch() -> list[dict]:
    """
    Fetch NAS status from FAA OIS. Returns a list of program dicts.
    Each program has: program_id, type, facility, raw.
    """
    resp = requests.get(
        NAS_URL, timeout=FETCH_TIMEOUT,
        headers={"User-Agent": "corporatetraveldc/1.0"}
    )
    resp.raise_for_status()

    body = resp.text.strip()
    if not body:
        raise ValueError("NAS feed returned empty response")

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ValueError(f"NAS XML parse error: {e}") from e

    programs: list[dict] = []

    for delay_type in root.findall("Delay_type"):
        # ── Arrival/Departure delay entries ───────────────────────────────
        ad_list = delay_type.find("Arrival_Departure_Delay_List")
        if ad_list is not None:
            for delay in ad_list.findall("Delay"):
                facility = (delay.findtext("ARPT") or "UNKN").strip()
                reason = (delay.findtext("Reason") or "").strip()
                arr_dep = delay.find("Arrival_Departure")
                ad_type = arr_dep.get("Type", "").strip() if arr_dep is not None else ""
                min_delay = (arr_dep.findtext("Min") or "") if arr_dep is not None else ""
                max_delay = (arr_dep.findtext("Max") or "") if arr_dep is not None else ""
                trend = (arr_dep.findtext("Trend") or "") if arr_dep is not None else ""

                prog_type = "DEP" if ad_type == "Departure" else "ARR" if ad_type == "Arrival" else "Delay"
                prog_id = f"{facility}-{prog_type}-{reason[:12]}"
                programs.append({
                    "program_id": prog_id,
                    "type": prog_type,
                    "facility": facility,
                    "raw": {
                        "arpt": facility, "reason": reason,
                        "ad_type": ad_type, "min": min_delay,
                        "max": max_delay, "trend": trend,
                    },
                })

        # ── Airport closure entries ────────────────────────────────────────
        closure_list = delay_type.find("Airport_Closure_List")
        if closure_list is not None:
            for airport in closure_list.findall("Airport"):
                facility = (airport.findtext("ARPT") or "UNKN").strip()
                reason = (airport.findtext("Reason") or "").strip()
                start = (airport.findtext("Start") or "").strip()
                reopen = (airport.findtext("Reopen") or "").strip()

                prog_id = f"{facility}-Closure"
                programs.append({
                    "program_id": prog_id,
                    "type": "Closure",
                    "facility": facility,
                    "raw": {
                        "arpt": facility, "reason": reason,
                        "start": start, "reopen": reopen,
                    },
                })

        # ── Ground Stop list (not yet seen in live data; parse defensively) ─
        gs_list = delay_type.find("Ground_Stop_List")
        if gs_list is not None:
            for gs in gs_list.findall("Program"):
                facility = (gs.findtext("ARPT") or gs.findtext("Airport") or "UNKN").strip()
                reason = (gs.findtext("Reason") or "").strip()
                prog_id = f"{facility}-GS-{reason[:12]}"
                programs.append({
                    "program_id": prog_id,
                    "type": "GS",
                    "facility": facility,
                    "raw": {"arpt": facility, "reason": reason},
                })

        # ── Ground Delay Program list (not yet seen in live data) ───────────
        gdp_list = delay_type.find("Ground_Delay_Program_List")
        if gdp_list is not None:
            for gdp in gdp_list.findall("Program"):
                facility = (gdp.findtext("ARPT") or gdp.findtext("Airport") or "UNKN").strip()
                reason = (gdp.findtext("Reason") or "").strip()
                prog_id = f"{facility}-GDP-{reason[:12]}"
                programs.append({
                    "program_id": prog_id,
                    "type": "GDP",
                    "facility": facility,
                    "raw": {"arpt": facility, "reason": reason},
                })

    return programs


def run() -> dict:
    feed_name = "nas"
    fetched_at = time.time()

    try:
        programs = fetch()
        active_ids = [p["program_id"] for p in programs]
        payload_hash = hashlib.sha256(
            json.dumps(sorted(active_ids)).encode()
        ).hexdigest()[:16]

        for p in programs:
            db.upsert_nas_program(
                program_id=p["program_id"],
                prog_type=p["type"],
                facility=p["facility"],
                raw_json=json.dumps(p["raw"]),
            )

        db.deactivate_absent_programs(active_ids)

        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("NAS fetch OK — %d programs active", len(programs))
        return {"count": len(programs), "program_ids": active_ids}

    except Exception as e:
        msg = str(e)
        log.error("NAS fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
