"""
METAR fetcher — AviationWeather.gov ADDS API.
Fetches METARs for DC-area stations. Parses ceiling, visibility, wind, precip.
Polled every 5 minutes by the poller scheduler.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass

import requests

from common import db

log = logging.getLogger(__name__)

ADDS_URL = (
    "https://aviationweather.gov/api/data/metar"
    "?ids={stations}&format=raw&taf=false&hours=1"
)

# DC-area stations for CPS scoring.
# DCA, IAD, BWI are primary; KFDK, KHEF, KJYO are secondary.
DC_STATIONS = ["KDCA", "KIAD", "KBWI", "KFDK", "KHEF", "KJYO", "KGAI"]

FETCH_TIMEOUT = 10


@dataclass
class MetarRecord:
    station: str
    raw_metar: str
    ceiling_ft: int | None
    visibility_sm: float | None
    wind_kt: int | None
    precip_code: str | None
    obs_time: float | None


def parse_metar(raw: str) -> MetarRecord:
    """
    Parse a raw METAR string into structured fields.
    Returns None values for fields that cannot be parsed.
    """
    parts = raw.split()
    if not parts:
        return MetarRecord(
            station="UNKN", raw_metar=raw, ceiling_ft=None,
            visibility_sm=None, wind_kt=None, precip_code=None, obs_time=None
        )

    station = parts[0] if parts[0].startswith("K") else "UNKN"
    ceiling_ft: int | None = None
    visibility_sm: float | None = None
    wind_kt: int | None = None
    precip_code: str | None = None
    obs_time: float | None = None

    for part in parts[1:]:
        # Observation time: 6 digits + Z
        if re.match(r"^\d{6}Z$", part) and obs_time is None:
            try:
                import calendar
                from datetime import datetime, timezone
                day = int(part[:2])
                hour = int(part[2:4])
                minute = int(part[4:6])
                now = datetime.now(timezone.utc)
                obs = now.replace(day=day, hour=hour, minute=minute, second=0,
                                  microsecond=0)
                obs_time = obs.timestamp()
            except (ValueError, OverflowError):
                pass

        # Wind: dddssKT or dddssGggKT
        m = re.match(r"^(\d{3}|VRB)(\d{2,3})(G\d{2,3})?KT$", part)
        if m and wind_kt is None:
            try:
                wind_kt = int(m.group(2))
            except ValueError:
                pass

        # Visibility: digits followed by SM
        m = re.match(r"^(\d+(?:/\d+)?)SM$", part)
        if m and visibility_sm is None:
            try:
                frac = m.group(1)
                if "/" in frac:
                    num, denom = frac.split("/")
                    visibility_sm = int(num) / int(denom)
                else:
                    visibility_sm = float(frac)
            except (ValueError, ZeroDivisionError):
                pass

        # Clouds/ceiling: BKN|OVC followed by 3-digit hundred-feet
        m = re.match(r"^(BKN|OVC)(\d{3})", part)
        if m and ceiling_ft is None:
            try:
                ceiling_ft = int(m.group(2)) * 100
            except ValueError:
                pass

        # Precip: RA, SN, TS, TSRA, RASN, etc.
        if re.match(r"^[-+]?(RA|SN|TS|TSRA|RASN|DZ|FZ|SG|GR|PL)", part):
            precip_code = part

    return MetarRecord(
        station=station,
        raw_metar=raw,
        ceiling_ft=ceiling_ft,
        visibility_sm=visibility_sm,
        wind_kt=wind_kt,
        precip_code=precip_code,
        obs_time=obs_time or time.time(),
    )



def parse_wind_dir(raw_metar: str) -> int | None:
    """
    Extract wind direction (degrees true) from a raw METAR string.
    Returns None for variable winds (VRB) or unparseable input.
    Exported for use by pusher wind-change detection without DB schema changes.
    """
    import re
    for part in raw_metar.split():
        m = re.match(r"^(\d{3}|VRB)(\d{2,3})(G\d{2,3})?KT$", part)
        if m:
            dir_str = m.group(1)
            if dir_str == "VRB":
                return None
            try:
                return int(dir_str)
            except ValueError:
                return None
    return None


def fetch(stations: list[str] = DC_STATIONS) -> list[MetarRecord]:
    """Fetch METARs for the given station list."""
    url = ADDS_URL.format(stations=",".join(stations))
    resp = requests.get(url, timeout=FETCH_TIMEOUT,
                        headers={"User-Agent": "corporatetraveldc/1.0"})
    resp.raise_for_status()

    records = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("No METAR"):
            continue
        # API prefixes lines with "METAR " or "SPECI " — strip before parsing.
        if line.startswith(("METAR ", "SPECI ")):
            line = line.split(" ", 1)[1]
        rec = parse_metar(line)
        if rec.station != "UNKN":
            records.append(rec)

    return records


def run() -> dict:
    feed_name = "metar"
    fetched_at = time.time()

    try:
        records = fetch()
        payload_hash = hashlib.sha256(
            "".join(r.raw_metar for r in records).encode()
        ).hexdigest()[:16]

        for rec in records:
            db.upsert_metar(
                station=rec.station,
                raw_metar=rec.raw_metar,
                ceiling_ft=rec.ceiling_ft,
                visibility_sm=rec.visibility_sm,
                wind_kt=rec.wind_kt,
                precip_code=rec.precip_code,
                obs_time=rec.obs_time or fetched_at,
            )

        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("METAR fetch OK — %d stations", len(records))
        return {"count": len(records)}

    except Exception as e:
        msg = str(e)
        log.error("METAR fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
