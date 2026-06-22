"""
ingest.nwws — NOAA Weather Wire (NWWS-OI) subscriber over XMPP.

Joins the NWWS-OI MUC, receives products pushed as group-chat messages, filters
by WFO, and writes alerts via db.upsert_nws_alert — the push twin of the
poller's REST `nws` fetcher.

PARSING SEAM:  NWWS-OI delivers each product as a MUC message carrying an
<x xmlns="nwws-oi"> element; the body is the raw NWS product text (large
products may be compressed/base64 in the payload). parse_product() turns one
product into zero or more upsert_nws_alert kwargs. CAP/VTEC parsing for your
specific product set goes there.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from common import db
from ingest import failover
from ingest.config import NwwsConfig

log = logging.getLogger("ingest.nwws")

# ---------------------------------------------------------------------------
# VTEC phenomenon + significance → (event_type, severity, certainty)
# Covers the product set LWX, AKQ, CTP, PHI push for DC metro.
# ---------------------------------------------------------------------------
_VTEC_MAP: dict[tuple[str, str], tuple[str, str, str]] = {
    ("TO", "W"): ("Tornado Warning",               "Extreme",  "Observed"),
    ("TO", "A"): ("Tornado Watch",                 "Extreme",  "Possible"),
    ("SV", "W"): ("Severe Thunderstorm Warning",   "Severe",   "Observed"),
    ("SV", "A"): ("Severe Thunderstorm Watch",     "Severe",   "Possible"),
    ("FF", "W"): ("Flash Flood Warning",           "Severe",   "Likely"),
    ("FF", "A"): ("Flash Flood Watch",             "Moderate", "Possible"),
    ("FF", "Y"): ("Flash Flood Advisory",          "Minor",    "Likely"),
    ("FL", "W"): ("Flood Warning",                "Severe",   "Likely"),
    ("FL", "Y"): ("Flood Advisory",               "Moderate", "Likely"),
    ("FL", "A"): ("Flood Watch",                  "Moderate", "Possible"),
    ("WS", "W"): ("Winter Storm Warning",          "Severe",   "Likely"),
    ("WS", "A"): ("Winter Storm Watch",            "Moderate", "Possible"),
    ("WW", "Y"): ("Winter Weather Advisory",       "Minor",    "Likely"),
    ("IS", "W"): ("Ice Storm Warning",             "Extreme",  "Likely"),
    ("BZ", "W"): ("Blizzard Warning",              "Extreme",  "Likely"),
    ("BZ", "A"): ("Blizzard Watch",               "Extreme",  "Possible"),
    ("LE", "W"): ("Lake Effect Snow Warning",      "Severe",   "Likely"),
    ("LE", "Y"): ("Lake Effect Snow Advisory",     "Minor",    "Likely"),
    ("LE", "A"): ("Lake Effect Snow Watch",        "Moderate", "Possible"),
    ("WC", "W"): ("Wind Chill Warning",            "Severe",   "Likely"),
    ("WC", "Y"): ("Wind Chill Advisory",           "Minor",    "Likely"),
    ("WC", "A"): ("Wind Chill Watch",              "Moderate", "Possible"),
    ("HW", "W"): ("High Wind Warning",             "Severe",   "Likely"),
    ("HW", "A"): ("High Wind Watch",               "Moderate", "Possible"),
    ("WI", "Y"): ("Wind Advisory",                "Minor",    "Likely"),
    ("EH", "W"): ("Excessive Heat Warning",        "Extreme",  "Likely"),
    ("EH", "A"): ("Excessive Heat Watch",          "Extreme",  "Possible"),
    ("HT", "Y"): ("Heat Advisory",                "Minor",    "Likely"),
    ("EC", "W"): ("Extreme Cold Warning",          "Extreme",  "Likely"),
    ("FZ", "W"): ("Freeze Warning",               "Moderate", "Likely"),
    ("FZ", "A"): ("Freeze Watch",                 "Moderate", "Possible"),
    ("FR", "Y"): ("Frost Advisory",               "Minor",    "Likely"),
    ("HZ", "W"): ("Hard Freeze Warning",          "Severe",   "Likely"),
    ("HZ", "A"): ("Hard Freeze Watch",            "Severe",   "Possible"),
    ("FG", "Y"): ("Dense Fog Advisory",           "Minor",    "Likely"),
    ("SM", "Y"): ("Dense Smoke Advisory",         "Minor",    "Likely"),
    ("CF", "W"): ("Coastal Flood Warning",        "Severe",   "Likely"),
    ("CF", "Y"): ("Coastal Flood Advisory",       "Minor",    "Likely"),
    ("CF", "A"): ("Coastal Flood Watch",          "Moderate", "Possible"),
    ("LS", "W"): ("Lakeshore Flood Warning",      "Severe",   "Likely"),
    ("LS", "Y"): ("Lakeshore Flood Advisory",     "Minor",    "Likely"),
    ("LS", "A"): ("Lakeshore Flood Watch",        "Moderate", "Possible"),
    ("SU", "W"): ("High Surf Warning",            "Severe",   "Likely"),
    ("SU", "Y"): ("High Surf Advisory",           "Minor",    "Likely"),
    ("RP", "S"): ("Rip Current Statement",        "Moderate", "Possible"),
    ("SC", "Y"): ("Small Craft Advisory",         "Minor",    "Likely"),
    ("SE", "W"): ("Hazardous Seas Warning",       "Severe",   "Likely"),
    ("SE", "A"): ("Hazardous Seas Watch",         "Moderate", "Possible"),
    ("SW", "W"): ("Storm Warning",                "Severe",   "Likely"),
    ("MH", "W"): ("Marine Weather Statement",     "Moderate", "Likely"),
    ("AF", "Y"): ("Ashfall Advisory",             "Minor",    "Likely"),
    ("AF", "W"): ("Ashfall Warning",              "Severe",   "Likely"),
    ("AQ", "Y"): ("Air Quality Alert",            "Minor",    "Likely"),
    ("DU", "W"): ("Blowing Dust Warning",         "Severe",   "Likely"),
    ("DU", "Y"): ("Blowing Dust Advisory",        "Minor",    "Likely"),
    ("DS", "W"): ("Dust Storm Warning",           "Severe",   "Likely"),
    ("LO", "Y"): ("Low Water Advisory",           "Minor",    "Likely"),
    ("UP", "W"): ("Ice Accretion Warning",        "Severe",   "Likely"),
    ("UP", "Y"): ("Freezing Spray Advisory",      "Minor",    "Likely"),
}

# Non-VTEC text products worth storing; None = explicitly skip
_TEXT_PRODUCTS: dict[str, tuple[str, str, str] | None] = {
    "SPS": ("Special Weather Statement", "Minor",    "Possible"),
    "SMW": ("Special Marine Warning",    "Severe",   "Likely"),
    "MWS": ("Marine Weather Statement",  "Moderate", "Likely"),
    # skip noisy products
    "RWR": None,  # Regional Weather Roundup
    "RTP": None,  # Regional Temp/Precip summary
    "HWO": None,  # Hazardous Weather Outlook (narrative, no VTEC)
    "PNS": None,  # Public Information Statement
    "ESF": None,  # Hydrological outlook
    "HYD": None,  # Hydrological statement
    "ADM": None,  # Administrative message
}



# WPC national discussion products -- handled via KWNO branch in _on_msg()
_WPC_PRODUCTS: dict[str, str] = {
    "FXUS02": "Short Range Forecast Discussion",
    "FXUS06": "Medium Range Forecast Discussion",
    "FXUS07": "Extended Forecast Discussion",
    "FXUS05": "Short Range QPF Discussion",
}

_WPC_TIME_RE = re.compile(
    r"(\d{3,4})\s+(AM|PM)\s+([A-Z]{2,4})\s+\w+\s+(\w+)\s+(\d{1,2})\s+(\d{4})",
    re.MULTILINE,
)

_WPC_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5,  "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_TZ_OFFSETS: dict[str, int] = {
    "EST": -5, "EDT": -4,
    "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7,
    "UTC":  0, "Z":    0,
}

# VTEC regex: /O.NEW.KLWX.TO.W.0001.260615T0200Z-260615T0300Z/
_VTEC_RE = re.compile(
    r"/[A-Z]\."
    r"(?P<action>[A-Z]{3})\."
    r"K[A-Z]{3}\."
    r"(?P<phenom>[A-Z]{2})\."
    r"(?P<sig>[A-YWOX])\."
    r"\d{4}\."
    r"(?P<t_start>\d{6}T\d{4}Z)-"
    r"(?P<t_end>\d{6}T\d{4}Z)/"
)

_HEADLINE_RE = re.compile(r"\.\.\.(.*?)\.\.\.", re.DOTALL)


def _vtec_time(s: str) -> float:
    """Parse VTEC time string 'YYMMDDTHHMM Z' → unix timestamp."""
    try:
        return datetime.strptime(s, "%y%m%dT%H%MZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return time.time() + 3600


def _extract_headline(body: str) -> str:
    m = _HEADLINE_RE.search(body)
    if m:
        return " ".join(m.group(1).split())[:300]
    # Fall back to first substantive line after preamble
    for line in body.splitlines():
        line = line.strip()
        if len(line) > 20 and not line.startswith("/") and not line[:3].isupper():
            return line[:300]
    return ""


def _extract_areas(body: str) -> str:
    """Extract affected area description from product text."""
    # NWS products list counties/zones in plain English after the UGC block
    areas: list[str] = []
    capture = False
    for line in body.splitlines():
        s = line.strip()
        if re.match(r"^[A-Z]{2}[CZ]\d{3}", s):
            capture = True
            continue
        if capture:
            if not s:
                break
            if s.startswith("/"):
                continue  # VTEC line, skip
            areas.append(s)
    return (" ".join(areas))[:500] if areas else ""


def parse_product(awips_id: str, source_wfo: str, body: str) -> list[dict]:
    """
    Parse an NWWS-OI raw NWS product into zero or more upsert_nws_alert kwargs.

    Handles:
    - VTEC-bearing products (warnings, watches, advisories) for all phenomena
      in the DC-metro product set (LWX, AKQ, CTP, PHI)
    - Non-VTEC text products: SPS, SMW
    - Skips: RWR, RTP, HWO, PNS, ESF, and any unknown product code

    Returns empty list for products that should not be stored.
    """
    now = time.time()
    results: list[dict] = []

    # --- VTEC products ---
    vtec_matches = list(_VTEC_RE.finditer(body))
    for m in vtec_matches:
        action  = m.group("action")
        phenom  = m.group("phenom")
        sig     = m.group("sig")
        t_start = m.group("t_start")
        t_end   = m.group("t_end")

        # Skip cancellations, expirations, and routine continuations
        if action in ("CAN", "EXP", "ROU"):
            continue

        key = (phenom, sig)
        if key not in _VTEC_MAP:
            log.debug("Unknown VTEC phenomenon %s.%s from %s — skipping", phenom, sig, source_wfo)
            continue

        event_type, severity, certainty = _VTEC_MAP[key]

        effective = _vtec_time(t_start) if t_start != "000000T0000Z" else now
        expires   = _vtec_time(t_end)   if t_end   != "000000T0000Z" else now + 3600

        if expires < now:
            log.debug("VTEC %s.%s from %s already expired — skipping", phenom, sig, source_wfo)
            continue

        headline  = _extract_headline(body)
        area_desc = _extract_areas(body) or source_wfo

        # Stable alert_id: same event through EXT/UPD actions keeps same ID
        alert_id = f"nwws:{source_wfo}:{phenom}.{sig}:{t_end}"

        results.append({
            "alert_id":    alert_id,
            "event_type":  event_type,
            "area_desc":   area_desc,
            "severity":    severity,
            "certainty":   certainty,
            "effective":   effective,
            "expires":     expires,
            "headline":    headline or event_type,
            "description": body[:2000],
        })
        log.info("NWWS VTEC: %s (%s.%s) from %s expires %s",
                 event_type, phenom, sig, source_wfo,
                 datetime.fromtimestamp(expires, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))

    if vtec_matches:
        return results

    # --- Non-VTEC text products ---
    product_code = awips_id[:3].upper() if awips_id else ""
    entry = _TEXT_PRODUCTS.get(product_code)
    if entry is None:
        return []  # Either explicitly skipped or unknown
    event_type, severity, certainty = entry

    headline = _extract_headline(body)
    if not headline:
        return []  # No useful content

    # Text products expire in ~1 hour; no reliable VTEC expiry to parse
    alert_id = f"nwws:{source_wfo}:{product_code}:{int(now)}"
    results.append({
        "alert_id":    alert_id,
        "event_type":  event_type,
        "area_desc":   source_wfo,
        "severity":    severity,
        "certainty":   certainty,
        "effective":   now,
        "expires":     now + 3600,
        "headline":    headline,
        "description": body[:2000],
    })
    log.info("NWWS text: %s (%s) from %s", event_type, product_code, source_wfo)

    return results



def _parse_wpc_issuance(body: str) -> float:
    """Parse WPC product header issuance time to unix epoch. Falls back to now."""
    m = _WPC_TIME_RE.search(body[:500])
    if not m:
        return time.time()
    try:
        hhmm_raw, ampm, tz_str, mon_str, day_str, year_str = m.groups()
        hhmm = hhmm_raw.zfill(4)
        hour = int(hhmm[:2])
        minute = int(hhmm[2:])
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        month = _WPC_MONTH_MAP.get(mon_str.upper(), 0)
        day   = int(day_str)
        year  = int(year_str)
        if month == 0:
            return time.time()
        import calendar
        utc_offset = _TZ_OFFSETS.get(tz_str.upper(), -4)
        local_epoch = calendar.timegm((year, month, day, hour, minute, 0, 0, 0, 0))
        return float(local_epoch - utc_offset * 3600)
    except Exception:
        return time.time()


def parse_wpc_product(awips_id: str, body: str) -> dict | None:
    """
    Parse a WPC national discussion product into upsert_wpc_discussion kwargs.
    Returns None for unknown AWIPS IDs.
    """
    label = _WPC_PRODUCTS.get(awips_id.upper())
    if not label or not body:
        return None
    return {
        "awips_id":      awips_id.upper(),
        "product_label": label,
        "issued_at":     _parse_wpc_issuance(body),
        "body":          body[:8000],
    }


async def run(cfg: NwwsConfig, stop: asyncio.Event, heartbeat: int) -> None:
    """Stay joined to the NWWS-OI MUC until stop is set, heartbeating health."""
    import slixmpp  # lazy import

    class _Client(slixmpp.ClientXMPP):
        def __init__(self):
            super().__init__(cfg.jid, cfg.password)
            self.register_plugin("xep_0045")  # MUC
            self.register_plugin("xep_0199")  # ping / keepalive
            self.add_event_handler("session_start", self._start)
            self.add_event_handler("groupchat_message", self._on_msg)

        async def _start(self, _):
            self.send_presence()
            await self.get_roster()
            self.plugin["xep_0045"].join_muc(cfg.muc, cfg.nick)
            log.info("NWWS-OI joined MUC %s as %s", cfg.muc, cfg.nick)

        def _on_msg(self, msg):
            if msg["mucnick"] == cfg.nick:
                return
            x = msg.xml.find("{nwws-oi}x")
            if x is None:
                return
            awips = x.get("awipsid", "") or x.get("ttaaii", "")
            wfo   = x.get("cccc", "")
            body  = (x.text or "").strip()

            # WPC national products (source KWNO) -- bypass local WFO filter
            if wfo == "KWNO":
                kw = parse_wpc_product(awips, body)
                if kw:
                    try:
                        db.upsert_wpc_discussion(**kw)
                        log.info("NWWS WPC: %s (%s) issued %.0f",
                                 kw["awips_id"], kw["product_label"], kw["issued_at"])
                    except Exception as e:
                        log.error("NWWS WPC handler error (%s): %s", awips, e)
                return

            # Local WFO products
            if cfg.wfo_filter and wfo not in cfg.wfo_filter:
                return
            try:
                for kw in parse_product(awips, wfo, body):
                    db.upsert_nws_alert(**kw)
            except Exception as e:
                log.error("NWWS product handler error (%s %s): %s", awips, wfo, e)

    backoff = 5
    while not stop.is_set():
        client = _Client()
        beat: asyncio.Task | None = None
        try:
            client.connect((cfg.server, cfg.port))

            async def _beat():
                while not stop.is_set():
                    failover.mark_push_healthy("nws")
                    await asyncio.sleep(heartbeat)

            beat = asyncio.create_task(_beat())
            # slixmpp runs on the same asyncio loop; wait until stop or disconnect.
            disconnected = asyncio.Event()
            client.add_event_handler("disconnected", lambda _e: disconnected.set())
            await _wait_any(stop, disconnected)
            client.disconnect()
            if stop.is_set():
                return
            raise ConnectionError("NWWS-OI disconnected")
        except asyncio.CancelledError:
            client.disconnect()
            raise
        except Exception as e:
            failover.mark_push_down("nws", f"nwws: {e}")
            log.error("NWWS-OI lost (%s); reconnecting in %ds", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
        finally:
            if beat:
                beat.cancel()


async def _wait_any(*events: asyncio.Event) -> None:
    await asyncio.wait(
        [asyncio.create_task(e.wait()) for e in events],
        return_when=asyncio.FIRST_COMPLETED,
    )
