"""
ops-brief — unified operational briefing, now running hourly.

Model: ollama/mistral (corporatetraveldc-pi5-osint:latest, mistral-nemo 12B; Local LLM)
MCP: https://github.com/CorporateTravelDC/corporatetravel-dispatch-mcp
Schedule: every hour :00 ET (corporatetraveldc-ops-brief.timer)
  — standard brief every hour
  — 6-hour trend analysis appended at 00:00, 06:00, 12:00, 18:00 ET
SR-1: log_usage() in finally block
SR-2: Exempt — time-bounded input, inputs always new.

Covers:
- DC-area airports (DCA/IAD/BWI)
- Northeast corridor (JFK/EWR/LGA/BOS/PHL)
- Transcontinental hubs (LAX/SFO/SEA/ORD/DFW/ATL/DEN)
- FAA NAS XML (direct pull, not just DB cache)
- NWS alerts for DC + Northeast
- Amtrak NEC status
- 6h trend analysis (CPS history + brief archive delta) at 6h intervals

Writes to both ops-brief.txt and daily-brief.txt so /api/v1/brief keeps working.

Pushes to:
  dispatch-debriefs  — full narrative (priority 3) — click → /brief?tab=ops
  dispatch           — concise bottom line (priority 3)
Both fire simultaneously.
"""

import os
import argparse
import json
import logging
import pathlib
import re
import sqlite3
import time as _time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import httpx
from common.ollama_lock import ollama_slot, OllamaBusyError
import requests

from common import config, db, ntfy_push as _ntfy
from common.aam_watch import get_aam_watch_section
from common.disruption_weather_watch import get_disruption_weather_capsule
from common.llm import generate as llm_generate
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "ops-brief"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_OPS_BRIEF_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "corporatetraveldc-pi5-ops-brief:latest")
OLLAMA_TREND_MODEL = (os.getenv("OLLAMA_OPS_BRIEF_TREND_MODEL")
                      or "corporatetraveldc-pi5-ops-brief-trend:latest")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"
# 2026-08-06: was 900s (+ an automatic same-prompt retry on top) -- root
# cause of the missing 8:00/3:00/4:00/5:00/6:00 ops-brief runs on
# 2026-08-06. That combination let a single slow generate() call
# consume more wall-clock than the container's own outer
# TimeoutStartSec, so systemd killed the whole run before either the
# primary attempt or the retry could finish OR fall back --
# see docs/COMPLIANCE_SECURITY.md and the 2026-08-06 incident notes.
# Fail-fast redesign, per operator directive: no cloud (Anthropic)
# fallback for this skill, and no retry against Ollama with the same
# slow prompt -- see the llm_generate() calls below (allow_anthropic=
# False, max_retries=0). On timeout, this now goes straight to
# _build_fallback_brief()'s deterministic template, which already
# existed and was already wired into main() -- it just used to be
# reachable only after also waiting out a doomed Anthropic attempt.
# 150s = observed healthy baseline for this call (real narrative
# generate() calls completed in well under 60s on 2026-08-06's clean
# runs, e.g. 01:00/02:00) plus a 90s margin, same shape as the
# 60-90s-past-baseline guidance used for ep-advance's identical fix.
OLLAMA_TIMEOUT    = 240  # 2026-08-07: 150->240, cold-launch coverage (operator directive)

HUB_AIRPORTS = "KDCA,KIAD,KBWI,KJFK,KEWR,KLGA,KBOS,KPHL,KORD,KATL,KLAX,KSFO,KSEA,KDEN,KDFW"
AVIATIONWX_METAR = f"https://aviationweather.gov/api/data/metar?ids={HUB_AIRPORTS}&format=raw&hours=1"
FAA_NAS_URL = "https://nasstatus.faa.gov/api/airport-status-information"
NWS_ALERTS_URL = (
    "https://api.weather.gov/alerts/active"
    "?area=VA,MD,DC,NY,NJ,CT,MA,PA,DE,RI&status=actual&severity=Extreme,Severe,Moderate"
)
AMTRAKER_URL = "https://api.amtraker.com/v3/trains"

# ATCSCC daily "Operations Plan" advisory — FAA's own forward-looking
# forecast (planned/possible/probable GDPs, ground stops, airspace
# constraints), distinct from currently-active NAS programs. Not a
# documented/versioned API — legacy fly.faa.gov advisories database,
# still live underneath the nasstatus.faa.gov SPA. Parsed defensively.
ATCSCC_ADV_LIST_URL   = "https://www.fly.faa.gov/adv/adv_list"
ATCSCC_ADV_DETAIL_URL = "https://www.fly.faa.gov/adv/adv_otherdis"

NEC_ROUTES = [
    "Acela", "Northeast Regional", "Palmetto", "Carolinian",
    "Vermonter", "Keystone", "Empire",
]

# Amtrak station code → full name.
# These are RAIL station codes, NOT airport ICAO codes.
# Mapping prevents the LLM from conflating e.g. "WAS" (Union Station) with DCA.
AMTRAK_STATIONS: dict[str, str] = {
    "WAS": "Washington Union Station",
    "NYP": "New York Penn Station",
    "BOS": "Boston South Station",
    "PHL": "Philadelphia 30th St",
    "BAL": "Baltimore Penn Station",
    "NWK": "Newark Penn Station",
    "STM": "Stamford",
    "NHV": "New Haven",
    "PVD": "Providence",
    "RTE": "Route 128",
    "BBY": "Bridgeport",
    "NLC": "New London",
    "MYS": "Mystic",
    "WIL": "Wilmington",
    "ABE": "Aberdeen",
    "HAV": "Havre de Grace",
    "NPN": "Newport News",
    "RVR": "Providence",
    "BWI": "BWI Rail Station",
    "NCR": "New Carrollton",
    "ALX": "Alexandria",
    "RMT": "Richmond Staples Mill",
    "NFK": "Norfolk",
}

SYSTEM_PROMPT = """You are producing a 6-hour operational briefing for [operator LLC],
an executive chauffeur operation based in Arlington, VA (Washington DC metro).
The operator is also a credentialed CERT/ARES/Skywarn volunteer (NoVA).

Your audience is a professional — be dense, direct, and use aviation/dispatch shorthand
where natural (VFR, IMC, GDP, G/S, kt, SM, CPS, etc.). No filler.

Produce a structured plain-text briefing with these sections in order.
Use ALL CAPS section labels — no markdown, no bullets, just clean readable paragraphs.

LEAD: Single most operationally significant item right now (one sentence max).

DC METRO: Current conditions at DCA/IAD/BWI — ceiling, vis, wind, precip.
Note any delay programs, closure NOTAMs, or significant frontal activity.

NORTHEAST: JFK/EWR/LGA/BOS/PHL conditions. Flag gusty winds, convection, or
approaching systems. Note any NAS programs.

TRANSCON HUBS: LAX/SFO/SEA/ORD/DFW/ATL/DEN — one line each unless a GDP
or ground stop is active (expand those). Flag marine layer, convection, wind events.

NAS PROGRAMS: All active ground stops, GDPs, and departure delay programs nationwide.
Include avg/max delay times and trend. If none, state that explicitly.

ATCSCC FORECAST: Planned/possible/probable ground stops, GDPs, and airspace
constraints from today's ATCSCC Operations Plan advisory — FAA's own forward-
looking outlook for later today/overnight, distinct from the currently-active
NAS PROGRAMS above. State timing (e.g. "after 1800Z") and probability language
(possible/probable/expected) exactly as FAA states it. Flag any VIP MOVEMENT(S)
noted in the advisory. If unavailable, state that explicitly.

TFRs: VIP/POTUS TFRs active or expected. Include TFR ID if known. Note any
impacts to DC-area airspace. If none active, state that.

NWS ALERTS: Any active Severe or Extreme weather alerts for DC/Northeast.
If none, one line stating that.

AMTRAK NEC: Status of Northeast Corridor trains — Acela and NE Regional.
Note any delays over 15 minutes. If feed unavailable, say so.
CRITICAL: Amtrak station names (Washington Union Station, New York Penn Station,
Boston South Station, etc.) are RAIL stations, NOT airports. NEVER list train
delays under airport sections. Airport delays come ONLY from FAA NAS PROGRAMS.
Train delays come ONLY from AMTRAK NEC. These are strictly separate modes.

ROUTE IMPACT: Any ground transportation impacts — road closures, POTUS movement
advisories, major events affecting DC metro routes. Omit if nothing notable.

OPERATIONAL NOTES: Anything a professional DC-area executive chauffeur and
CERT/ARES volunteer should know for this operational period — unusual airspace
activity, security events, weather hazards relevant to ground ops, etc.
Omit if nothing notable.

BOTTOM LINE: 1-2 sentence operational summary. What matters most right now.

Keep total brief under 550 words. Lead section first, bottom line last."""


def _fetch(url: str, timeout: int = 10) -> str | None:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.warning("fetch failed %s: %s", url, e)
        return None


def _metar_section() -> str:
    raw = _fetch(AVIATIONWX_METAR)
    if not raw:
        # Fall back to local DB for DC airports
        metars = db.get_metar_snapshot()
        primary = [m for m in metars if m["station"] in ("KDCA", "KIAD", "KBWI")]
        if not primary:
            return "Hub METARs unavailable."
        return "\n".join(
            f"{m['station']}: {m['ceiling_ft']}ft/{m['visibility_sm']}SM/{m['wind_kt']}kt"
            + (f" ({m['precip_code']})" if m.get("precip_code") else "")
            for m in primary
        )
    lines = [l.strip() for l in raw.splitlines() if l.strip().startswith(("METAR", "SPECI"))]
    # Supplement missing primary DC airports from local DB
    # (aviationweather.gov occasionally drops KDCA/KIAD from the response)
    present = {l.split()[1] for l in lines if len(l.split()) > 1}
    missing_dc = [s for s in ("KDCA", "KIAD", "KBWI") if s not in present]
    if missing_dc:
        metars = db.get_metar_snapshot()
        for m in metars:
            if m["station"] in missing_dc:
                wx = (f"METAR {m['station']} (local-db): "
                      f"{m['ceiling_ft']}ft/{m['visibility_sm']}SM/{m['wind_kt']}kt"
                      + (f" ({m['precip_code']})" if m.get("precip_code") else ""))
                lines.insert(0, wx)
                log.debug("ops-brief: supplemented %s from local DB (missing from aviationweather.gov)", m["station"])
    return "\n".join(lines) if lines else "No METAR data returned."


def _nas_section() -> str:
    raw = _fetch(FAA_NAS_URL)
    if not raw:
        nas = db.get_active_nas_programs()
        if not nas:
            return "NAS status unavailable."
        return "\n".join(f"{p['type']} {p['facility']}: {p['raw_json']}" for p in nas)
    try:
        root = ET.fromstring(raw)
        lines = [f"FAA NAS as of {root.findtext('Update_Time') or 'unknown'}"]
        for delay_type in root.findall("Delay_type"):
            for gd in delay_type.findall(".//Ground_Delay"):
                arpt = gd.findtext("ARPT")
                reason = gd.findtext("Reason")
                avg = gd.findtext("Avg")
                max_ = gd.findtext("Max")
                lines.append(f"GDP {arpt}: {reason} — avg {avg}, max {max_}")
            for delay in delay_type.findall(".//Delay"):
                arpt = delay.findtext("ARPT")
                reason = delay.findtext("Reason")
                for ad in delay.findall("Arrival_Departure"):
                    typ = ad.get("Type", "")[:3].upper()
                    mn = ad.findtext("Min")
                    mx = ad.findtext("Max")
                    trend = ad.findtext("Trend")
                    lines.append(f"{typ} delay {arpt}: {reason} {mn}–{mx} ({trend})")
            for airport in delay_type.findall(".//Airport"):
                arpt = airport.findtext("ARPT")
                reopen = airport.findtext("Reopen", "")
                reason = (airport.findtext("Reason") or "")[:80]
                lines.append(f"Closure {arpt}: reopen {reopen} — {reason}")
        return "\n".join(lines)
    except ET.ParseError as e:
        log.warning("NAS XML parse error: %s", e)
        return f"NAS XML parse error: {e}"



def _find_todays_opsplan_advisory() -> tuple[str, str] | None:
    """
    Locate today's (UTC) ATCSCC OPERATIONS PLAN advisory in the legacy
    fly.faa.gov advisories database. Returns (adv_date_MMDDYYYY, advn) or
    None if not found / fetch failed. The list page is newest-first, so the
    first DCC/OPERATIONS PLAN row is the latest (handles same-day reissues).
    """
    today = datetime.now(timezone.utc)
    params = {
        "whichAdvisories": "ATCSCC",
        "advisoryCategory": "All",
        "date": today.strftime("%Y-%m-%d"),
        "airflow": "true", "ctop": "true", "gStop": "true",
        "gDelay": "true", "route": "true", "other": "true",
    }
    try:
        r = requests.get(ATCSCC_ADV_LIST_URL, params=params, timeout=12)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning("ATCSCC adv list fetch failed: %s", e)
        return None

    for row in re.split(r"<tr[^>]*>", html, flags=re.IGNORECASE):
        if "OPERATIONS PLAN" not in row.upper():
            continue
        link = re.search(r"adv_otherdis\?adv_date=(\d{8})&advn=(\d+)", row)
        if not link:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        cell_text = [re.sub(r"<[^<]+?>", "", c).strip() for c in cells]
        # Column 2 (index 1) is CONTROL ELEMENT — the daily ops plan is issued by DCC.
        if len(cell_text) >= 2 and cell_text[1].upper() == "DCC":
            return link.group(1), link.group(2)
    return None


def _fetch_opsplan_text(adv_date: str, advn: str) -> str | None:
    """Fetch and lightly clean the raw <PRE> advisory text for a given advisory."""
    try:
        r = requests.get(
            ATCSCC_ADV_DETAIL_URL,
            params={"adv_date": adv_date, "advn": advn},
            timeout=12,
        )
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning("ATCSCC adv detail fetch failed: %s", e)
        return None
    m = re.search(r"<PRE>(.*?)</PRE>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
        raw = raw.replace(a, b)
    return raw.strip()


_OPSPLAN_HEADER_RE = re.compile(r"^([A-Z][A-Z0-9 /()_\-]{2,60}:)\s*$", re.MULTILINE)


def _split_opsplan_sections(raw: str) -> dict[str, str]:
    """Split the raw ATCSCC advisory text into {SECTION NAME: body} on ALL-CAPS
    header lines (e.g. 'TERMINAL PLANNED:'). Free text before the first header
    (the human-written forecast paragraph) is stored under '_intro'."""
    headers = list(_OPSPLAN_HEADER_RE.finditer(raw))
    if not headers:
        return {"_intro": raw.strip()}
    sections: dict[str, str] = {}
    intro = raw[: headers[0].start()].strip()
    if intro:
        sections["_intro"] = intro
    for i, h in enumerate(headers):
        key = h.group(1).rstrip(":").strip()
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(raw)
        sections[key] = raw[start:end].strip()
    return sections


def _fetch_atcscc_opsplan_sections() -> tuple[str, dict] | None:
    """Fetch + split today's ATCSCC OPERATIONS PLAN advisory ONCE. Returns
    (advn, sections) or None. Shared by the forecast formatter and the
    webinar-defer check so a single ops-brief run only hits the legacy
    fly.faa.gov advisories database twice total (list + detail), not four
    times."""
    found = _find_todays_opsplan_advisory()
    if not found:
        return None
    adv_date, advn = found
    raw = _fetch_opsplan_text(adv_date, advn)
    if not raw:
        return None
    return advn, _split_opsplan_sections(raw)


_WEBINAR_RE = re.compile(r"NEXT PLANNING WEBINAR:\s*(\d{3,4})Z?", re.IGNORECASE)


def _next_webinar_utc(sections: dict, now: "datetime | None" = None) -> "datetime | None":
    """Parse 'NEXT PLANNING WEBINAR: HHMMZ' out of the advisory (it lands as
    trailing text in whichever section happens to be last -- currently VIP
    MOVEMENT(S) -- since it has inline content and isn't its own header).
    Resolves to the next UTC occurrence: today if still ahead, else assumed
    to mean tomorrow (advisories are same-calendar-day documents)."""
    now = now or datetime.now(timezone.utc)
    text = " ".join(sections.values())
    m = _WEBINAR_RE.search(text)
    if not m:
        return None
    hhmm = m.group(1).zfill(4)
    try:
        hour, minute = int(hhmm[:2]), int(hhmm[2:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
    except ValueError:
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < now - timedelta(hours=1):
        candidate += timedelta(days=1)
    return candidate


def _defer_for_webinar(webinar_dt: datetime) -> None:
    """Push a defer notice and skip generation this cycle.

    --- FIXED 2026-07-20 ---
    This used to try to schedule a one-shot systemd-run --user timer to
    rerun ~10 minutes after the webinar. That never actually worked: this
    code runs inside the corporatetraveldc-poller container (a plain Python
    image, no systemd tooling at all), so subprocess.run(["systemd-run",
    ...]) always failed with FileNotFoundError ([Errno 2] No such file or
    directory: 'systemd-run'). Confirmed today via journalctl -- every
    defer this session (11:00 and 13:00 ET) hit that error and silently
    dropped the whole hour's brief instead of rerunning. The referenced
    target unit (corporatetraveldc-ops-brief-deferred.service) doesn't
    even exist on the host either, so this was never fully wired up.

    Real fix: don't try to self-reschedule at all. ops-brief already runs
    hourly via corporatetraveldc-ops-brief.timer. Webinars only trigger a
    defer when they land within 30 min of a scheduled run, and observed
    spacing between webinars is ~2 hours -- so the NEXT regular hourly run
    (30-60 min later) is always well past the post-webinar update window
    on its own. No special rerun is needed; just skip this cycle cleanly
    and let the existing timer catch it next hour. Simpler and it can't
    silently eat a cycle the way the broken subprocess call did.
    """
    now = datetime.now(timezone.utc)
    next_hourly_utc = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))

    msg = (
        f"Ops brief deferred: ATCSCC planning webinar at {webinar_dt.strftime('%H:%MZ')} "
        f"is within 30 min. Skipping this cycle so we don't publish on stale "
        f"pre-webinar data -- next hourly run at {next_hourly_utc.strftime('%H:%MZ')} "
        f"will pick up the post-webinar Operations Plan update."
    )
    log.info("%s", msg)
    try:
        # topic_brief override added 2026-08-02: dispatch-ops is now
        # weekly-summary-only (see _send_ntfy_dual below) -- this and every
        # other ops-brief-family push moves to "ops-brief", the topic name
        # that was already documented everywhere but never actually used
        # until now (see ntfy_topic_audit_20260802 memory for how that was
        # found).
        _ntfy.send_dual(msg, msg, title="OPS BRIEF DEFERRED", topic_brief="ops-brief")
    except Exception as e:
        log.warning("defer notice push failed: %s", e)


def _atcscc_forecast_section(prefetched: "tuple[str, dict] | None" = None) -> str:
    """
    ATCSCC's own daily Operations Plan advisory — the FAA's forward-looking
    forecast of planned/possible/probable ground stops, GDPs, and airspace
    constraints for later today/overnight. Complements _nas_section(), which
    only reflects programs that are CURRENTLY active.
    pass prefetched=(advn, sections) from _fetch_atcscc_opsplan_sections()
    to avoid a redundant fetch when the caller already pulled it (e.g. the
    webinar-defer check in main()).
    """
    try:
        data = prefetched if prefetched is not None else _fetch_atcscc_opsplan_sections()
        if not data:
            return "ATCSCC Operations Plan advisory not yet posted or unavailable for today."
        advn, sections = data

        def _clean(body: str, max_lines: int = 14) -> str:
            lines = [
                l.strip() for l in body.splitlines()
                if l.strip() and not set(l.strip()) <= {"_"}
            ]
            return "\n".join(lines[:max_lines]) if lines else "None."

        intro            = _clean(sections.get("_intro", ""), max_lines=10)
        terminal_planned = _clean(sections.get("TERMINAL PLANNED", ""))
        enroute_planned  = _clean(sections.get("EN ROUTE PLANNED", ""))
        vip_raw = sections.get("VIP MOVEMENT(S)", "")
        # VIP MOVEMENT(S) is the last labeled section in the advisory, so its
        # body runs to end-of-text and picks up the trailing signature block
        # (planning webinar time, sequence code, sender tag) -- trim those off.
        vip_raw = vip_raw.split("NEXT PLANNING WEBINAR")[0]
        vip              = _clean(vip_raw, max_lines=4)

        parts = [f"ATCSCC ADVZY {advn} DCC OPERATIONS PLAN (advisory text, forward-looking):"]
        if intro:
            parts.append(f"SUMMARY: {intro}")
        parts.append(f"TERMINAL PLANNED (later today/overnight):\n{terminal_planned}")
        parts.append(f"EN ROUTE PLANNED (later today/overnight):\n{enroute_planned}")
        if vip and vip != "None.":
            parts.append(f"VIP MOVEMENT(S) NOTED IN OPS PLAN:\n{vip}")
        return "\n\n".join(parts)
    except Exception as e:
        log.warning("ATCSCC forecast section failed: %s", e)
        return f"ATCSCC Operations Plan forecast unavailable ({e})."


def _nws_alerts_section() -> str:
    raw = _fetch(NWS_ALERTS_URL)
    if not raw:
        return "NWS alerts unavailable."
    try:
        data = json.loads(raw)
        features = data.get("features", [])
        if not features:
            return "No active NWS alerts for DC/Northeast."
        return "\n".join(
            f"[{f['properties'].get('severity','?')}] "
            f"{f['properties'].get('event','?')} — "
            f"{f['properties'].get('areaDesc','')[:60]} — "
            f"{(f['properties'].get('headline','') or '')[:80]}"
            for f in features[:6]
        )
    except Exception as e:
        return f"NWS alerts parse error: {e}"


def _amtrak_section() -> str:
    raw = _fetch(AMTRAKER_URL, timeout=12)
    if not raw:
        return "Amtrak feed unavailable (timeout)."
    try:
        data = json.loads(raw)
        nec = []
        for k, v in data.items():
            trains = v if isinstance(v, list) else [v]
            for t in trains:
                rn = t.get("routeName", "")
                if any(r.lower() in rn.lower() for r in NEC_ROUTES):
                    delay = 0
                    for s in t.get("stations", []):
                        if s.get("status") == "Enroute":
                            sch = s.get("schArr", "")
                            act = s.get("arr", "")
                            if sch and act and sch != act:
                                try:
                                    ds = datetime.fromisoformat(sch.replace("Z", "+00:00"))
                                    da = datetime.fromisoformat(act.replace("Z", "+00:00"))
                                    delay = int((da - ds).total_seconds() / 60)
                                except Exception:
                                    pass
                            break
                    nec.append((delay, t))
        if not nec:
            return "No Amtrak NEC trains in feed (feed may be returning non-Amtrak data only)."
        nec.sort(key=lambda x: abs(x[0]), reverse=True)
        def _stn(code: str) -> str:
            return AMTRAK_STATIONS.get(code, code)

        return "\n".join(
            f"Train {t.get('trainNum','?')} ({t.get('routeName','?')}) "
            f"{_stn(t.get('origCode','?'))} → {_stn(t.get('destCode','?'))} "
            f"{'+'if d>0 else ''}{d}min {t.get('trainState','?')} at {t.get('eventName','?')}"
            for d, t in nec[:12]
        )
    except Exception as e:
        return f"Amtrak parse error: {e}"


def _tfr_section() -> str:
    tfrs = db.get_active_tfrs()
    vip = [t for t in tfrs if t.get("is_vip")]
    total = len(tfrs)
    if vip:
        ids = ", ".join(t["tfr_id"] for t in vip)
        return f"VIP TFRs ACTIVE: {ids}. Total active TFRs: {total}."
    return f"No VIP TFRs. {total} routine TFRs active. DC airspace normal."


def _cps_section() -> str:
    cps = db.get_latest_cps()
    if not cps:
        return "CPS not yet computed."
    return (
        f"CPS: {cps.get('score','?')}/{cps.get('label','?')} — "
        f"{cps.get('narrative','') or 'No narrative'}"
    )


def _route_section() -> str:
    route = db.get_latest_route_narrative()
    if not route or not route.get("route_narrative"):
        return ""
    return route["route_narrative"][:400]


def _cps_history_6h() -> list[dict]:
    """Return CPS score entries from the last 6 hours, oldest first."""
    cutoff = _time.time() - 6 * 3600
    try:
        with db.conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT score, label, narrative, computed_at FROM cps_scores "
                "WHERE computed_at >= ? ORDER BY computed_at ASC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("ops-brief: cps_history query failed: %s", e)
        return []


def _brief_history_6h() -> list[dict]:
    """Return ops brief archive entries from the last 6 hours, oldest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")  # ISO-8601 string -- matches
    # brief_archive.generated_at's stored TEXT format so the WHERE
    # clause actually compares correctly (was float vs TEXT before).
    try:
        with db.conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT id, generated_at, brief_type, content FROM brief_archive "
                "WHERE generated_at >= ? AND brief_type='ops' ORDER BY generated_at ASC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("ops-brief: brief_history query failed: %s", e)
        return []


def _trend_analysis_prompt() -> str:
    """
    Build a trend context block from the last 6h of CPS scores and brief archives.
    Used at 6h boundaries (00, 06, 12, 18 ET) to drive a trend narrative section.
    """
    cps_hist = _cps_history_6h()
    brief_hist = _brief_history_6h()

    lines = ["=== 6-HOUR TREND DATA ==="]

    # CPS trend
    if cps_hist:
        lines.append(f"\nCPS history (last 6h, {len(cps_hist)} readings):")
        for entry in cps_hist:
            ts = datetime.fromtimestamp(entry["computed_at"], tz=timezone.utc).strftime("%H:%MZ")
            lines.append(
                f"  {ts}: {entry.get('score','?')}/{entry.get('label','?')} — "
                f"{(entry.get('narrative') or '')[:100]}"
            )
        # Summarize direction
        if len(cps_hist) >= 2:
            first_label = cps_hist[0].get("label", "?")
            last_label  = cps_hist[-1].get("label", "?")
            if first_label != last_label:
                lines.append(f"  TREND DIRECTION: {first_label} → {last_label}")
            else:
                lines.append(f"  TREND DIRECTION: STABLE ({last_label})")
    else:
        lines.append("\nCPS history: No data in last 6h.")

    # Brief archive snapshot comparison
    if len(brief_hist) >= 2:
        lines.append(f"\nBrief archive (last 6h, {len(brief_hist)} briefs):")
        for b in brief_hist:
            ts = datetime.fromisoformat(b["generated_at"].replace("Z", "+00:00")).strftime("%H:%MZ")
            # Extract first 150 chars of content for trend context
            snippet = (b.get("content") or "").replace("\n", " ").strip()[:150]
            lines.append(f"  {ts}: {snippet}…")
    elif len(brief_hist) == 1:
        lines.append(f"\nBrief archive: 1 prior brief in 6h window.")
    else:
        lines.append("\nBrief archive: No prior briefs in 6h window (first run of interval).")

    return "\n".join(lines)


TREND_SYSTEM_PROMPT = (
    "You are the dispatch intelligence officer for [operator LLC]. "
    "You have just received a 6-hour data trend package showing CPS scores and brief snapshots. "
    "Produce exactly two labeled paragraphs, in this order, each 2-3 dense sentences:\n\n"
    "RETROSPECTIVE (LAST 6H): whether conditions improved, degraded, or stayed stable over "
    "the past 6 hours, and the single most significant change, if any.\n\n"
    "PREDICTIVE (NEXT 6H): the outlook for the next 6 hours based on current trajectory, "
    "active NAS programs, weather systems in motion, and any known TFR/schedule changes.\n\n"
    "Aviation/dispatch shorthand is expected. No filler. Use exactly those two labels, nothing else."
)


def _generate_trend_narrative(trend_prompt: str) -> str:
    """Generate trend analysis via local Ollama only. Returns empty string on failure.

    2026-08-06: allow_anthropic=False (never call the cloud API for this
    skill, operator directive) and max_retries=0 (a slow call gets ONE
    attempt at the tight OLLAMA_TIMEOUT above, then straight to the
    deterministic fallback the caller already builds on empty string --
    no retry against Ollama with the same slow prompt, which is what let
    a single call consume more time than the container's own timeout).
    """
    return llm_generate(
        system=None,  # dedicated Modelfile carries this now
        prompt=trend_prompt,
        ollama_model=OLLAMA_TREND_MODEL,
        max_tokens=200,
        temperature=0.15,
        timeout=OLLAMA_TIMEOUT,
        allow_anthropic=False,
        max_retries=0,
    ) or ""


def _send_ntfy_dual(full_text: str, concise_text: str, title: str) -> None:
    """Delegates to common.ntfy_push.send_dual — click URLs set per-topic.

    topic_brief="ops-brief" added 2026-08-02: send_dual()'s default
    ("dispatch-ops") used to be shared, undocumented, with weekly_summary.py
    -- nobody was actually subscribed to "ops-brief" (the topic every
    click-map entry and docstring claimed was real) because nothing ever
    published there. the operator's direction: dispatch-ops becomes
    weekly-summary-only; ops-brief's concise push moves to match its own
    documented name instead. topic_full stays on the shared default
    ("dispatch-debriefs") -- that one's fine as a general full-narrative
    bucket, only the concise/"brief" side had the collision.
    """
    _ntfy.send_dual(full_text, concise_text, title=title, topic_brief="ops-brief")


def _ollama_generate(model: str, system: str, prompt: str) -> str | None:
    """
    Single Ollama /api/generate call. Returns response text or None on any error.
    Raises httpx.HTTPStatusError so callers can inspect status codes.
    """
    # priority="report" (2026-07-26): see common/ollama_lock.py. Raises
    # OllamaBusyError up to the caller (same as any other httpx failure this
    # function already lets propagate) if a hot alert is pending.
    with ollama_slot(priority="report", timeout=OLLAMA_TIMEOUT):
        resp = httpx.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model":  model,
                "system": system,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 500, "temperature": 0.2},
            },
            timeout=OLLAMA_TIMEOUT,
        )
    resp.raise_for_status()
    return resp.json().get("response", "").strip() or None


def _call_ollama(prompt_content: str) -> tuple[str, str] | None:
    """
    Generate ops brief via local Ollama only (2026-08-06: no Anthropic
    fallback, operator directive -- see llm_generate() call below).
    Returns (full_text, concise_text) or None on any Ollama failure --
    caller (main()) already builds a deterministic template on None.
    """
    # System prompt now lives in the corporatetraveldc-pi5-ops-brief
    # Modelfile itself (2026-08-02) -- system=None below lets Ollama use
    # that baked-in default instead of resending it every call.
    system = None

    model_used = OLLAMA_MODEL
    # 2026-08-06: allow_anthropic=False, max_retries=0 -- see
    # _generate_trend_narrative above for the full explanation. Same
    # fail-fast redesign, same root cause it fixes.
    narrative = llm_generate(
        system=system,
        prompt=prompt_content,
        ollama_model=OLLAMA_MODEL,
        max_tokens=500,
        temperature=0.2,
        timeout=OLLAMA_TIMEOUT,
        allow_anthropic=False,
        max_retries=0,
    )

    if not narrative:
        return None

    # Extract BOTTOM LINE as concise push (last sentence or last line after "BOTTOM LINE:")
    concise = narrative
    for marker in ("BOTTOM LINE:", "Bottom line:", "BOTTOM LINE —"):
        if marker in narrative:
            concise = narrative.split(marker, 1)[1].strip().splitlines()[0].strip()
            break
    else:
        # Fall back to last non-empty sentence
        sentences = [s.strip() for s in narrative.replace("\n", " ").split(".") if s.strip()]
        if sentences:
            concise = sentences[-1] + "."

    now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
    full_text = f"OPS BRIEF {now_label} (Ollama/{model_used})\n\n{narrative}"
    return full_text, concise[:200]


def build_brief_content(prefetched_atcscc: "tuple[str, dict] | None" = None) -> tuple[str, str]:
    """
    Returns (prompt_content, raw_appendix).
    prompt_content — fed to Claude for narrative generation.
    raw_appendix   — METAR + NAS raw data block, appended to BOTH the AI
                     narrative and the fallback brief for hybrid layout.
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    route = _route_section()

    # Raw sections stored separately so they can be appended to final brief
    raw_metar = _metar_section()
    raw_nas   = _nas_section()
    raw_atcscc = _atcscc_forecast_section(prefetched_atcscc)

    parts = [
        f"=== OPS BRIEF DATA PULL {now_utc} ===",
        f"CPS:\n{_cps_section()}",
        f"TFRs:\n{_tfr_section()}",
        f"METARs (hub airports):\n{raw_metar}",
        f"FAA NAS PROGRAMS:\n{raw_nas}",
        f"ATCSCC OPERATIONS PLAN FORECAST:\n{raw_atcscc}",
        f"NWS ALERTS (DC/Northeast):\n{_nws_alerts_section()}",
        f"AMTRAK NEC:\n{_amtrak_section()}",
    ]
    if route:
        parts.append(f"ROUTE NARRATIVE (local DB):\n{route}")

    prompt_content = "\n\n".join(parts)

    # Raw appendix — appended verbatim to the bottom of every push
    raw_appendix = (
        f"\n\n--- RAW DATA ({now_utc}) ---\n"
        f"METARs:\n{raw_metar}\n\n"
        f"NAS STATUS:\n{raw_nas}\n\n"
        f"ATCSCC OPS PLAN FORECAST:\n{raw_atcscc}"
    )
    return prompt_content, raw_appendix


def _build_fallback_brief(content: str) -> tuple[str, str]:
    """
    Build a plain-data brief from raw content when Ollama is unavailable
    (not configured, unreachable, or returned empty response).
    Returns (full_text, concise_text) — same contract as the Ollama path.
    Flagged clearly so operator knows no narrative was generated.
    """
    now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
    lines = content.splitlines()
    nas_lines, metar_lines, nws_lines, tfr_lines, amtrak_lines, atcscc_lines = [], [], [], [], [], []
    current = None
    for line in lines:
        u = line.upper()
        if "FAA NAS PROGRAMS" in u:       current = "nas"
        elif "ATCSCC OPERATIONS PLAN FORECAST" in u: current = "atcscc"
        elif "METARS" in u:               current = "metar"
        elif "NWS ALERTS" in u:           current = "nws"
        elif "TFRS:" in u:                current = "tfr"
        elif "AMTRAK" in u:               current = "amtrak"
        elif line.startswith("==="):      current = None
        elif current == "nas"    and line.strip(): nas_lines.append(line.strip())
        elif current == "atcscc" and line.strip(): atcscc_lines.append(line.strip())
        elif current == "metar"  and line.strip().startswith(("METAR","SPECI")): metar_lines.append(line.strip())
        elif current == "nws"    and line.strip(): nws_lines.append(line.strip())
        elif current == "tfr"    and line.strip(): tfr_lines.append(line.strip())
        elif current == "amtrak" and line.strip(): amtrak_lines.append(line.strip())

    dc_ne  = [l for l in metar_lines if any(x in l for x in ("KDCA","KIAD","KBWI","KJFK","KEWR","KLGA","KBOS","KPHL"))]
    xcon   = [l for l in metar_lines if any(x in l for x in ("KLAX","KSFO","KSEA","KORD","KDFW","KATL","KDEN"))]

    full  = f"[DATA BRIEF — DETERMINISTIC FALLBACK] {now_label}\n"
    full += "Ollama unavailable or not configured. Raw data push — no narrative.\n\n"
    full += "NAS PROGRAMS:\n" + ("\n".join(nas_lines[:12]) if nas_lines else "None active") + "\n\n"
    full += "ATCSCC FORECAST (PLANNED):\n" + ("\n".join(atcscc_lines[:20]) if atcscc_lines else "Unavailable") + "\n\n"
    full += "DC/NORTHEAST METARs:\n" + ("\n".join(dc_ne) if dc_ne else "Unavailable") + "\n\n"
    full += "TRANSCON METARs:\n"  + ("\n".join(xcon)  if xcon  else "Unavailable") + "\n\n"
    full += "TFRs:\n"  + ("\n".join(tfr_lines[:3])    if tfr_lines    else "No VIP TFRs") + "\n\n"
    full += "NWS ALERTS:\n" + ("\n".join(nws_lines[:4]) if nws_lines else "None active") + "\n\n"
    full += "AMTRAK NEC:\n" + ("\n".join(amtrak_lines[:6]) if amtrak_lines else "Feed unavailable")

    gdp    = next((l for l in nas_lines if "GDP" in l), None)
    delay  = next((l for l in nas_lines if "DEP" in l or "delay" in l.lower()), None)
    lead   = gdp or delay or "No active NAS programs"
    concise = f"[FALLBACK] {now_label} — {lead[:180]}. Full data in dispatch-debriefs."
    return full, concise


def main(force: bool = False, run_trend: bool = False, deferred_rerun: bool = False) -> None:
    """
    run_trend: force a 6h trend analysis section regardless of current hour.
               Auto-true when the current ET hour is 0, 6, 12, or 18.
    """
    status = "error"

    # Determine whether to append a 6h trend analysis.
    # At 6h boundaries (00, 06, 12, 18 ET) or when --trend flag passed.
    now_et_hour = datetime.now(timezone.utc).hour - 4  # rough ET offset (no DST correction)
    # DST: ET is UTC-5 (Nov–Mar) or UTC-4 (Mar–Nov). Use a pragmatic check.
    import time as _t
    _lt = _t.localtime()
    now_local_hour = _lt.tm_hour
    is_6h_boundary = (now_local_hour % 6 == 0) or run_trend

    try:
        # Webinar-aware defer: if the ATCSCC planning webinar is under 30 min
        # out, skip this cycle's brief, push a defer notice, and schedule a
        # one-shot rerun 10 min after the webinar so we pick up the updated
        # Operations Plan instead of stale pre-webinar data. Skipped on the
        # deferred rerun itself so this can never chain indefinitely.
        atcscc_prefetch = None
        if not deferred_rerun:
            atcscc_prefetch = _fetch_atcscc_opsplan_sections()
            if atcscc_prefetch:
                _, _sections = atcscc_prefetch
                webinar_dt = _next_webinar_utc(_sections)
                if webinar_dt:
                    mins_until = (webinar_dt - datetime.now(timezone.utc)).total_seconds() / 60
                    if 0 <= mins_until < 30:
                        _defer_for_webinar(webinar_dt)
                        status = "deferred"
                        log_usage(SKILL_NAME, MODEL, 0, 0, status, "new")
                        return

        content, raw_appendix = build_brief_content(prefetched_atcscc=atcscc_prefetch)

        # Try Ollama first; fall back to deterministic if unavailable / not configured.
        ollama_result = _call_ollama(content)
        if ollama_result:
            full_text, concise = ollama_result
            status = "ok"
            log.info("%s: brief generated (Ollama/%s)", SKILL_NAME, OLLAMA_MODEL)
        else:
            # 2026-08-06: narrow safety net around the fallback ITSELF --
            # same pattern applied identically across every skill with an
            # Ollama fallback. See route_impact.py for the full note.
            try:
                full_text, concise = _build_fallback_brief(content)
                status = "ok"
                log.info("%s: brief generated (deterministic)", SKILL_NAME)
            except Exception as fallback_err:
                log.error("%s: deterministic fallback also failed — %s", SKILL_NAME, fallback_err)
                full_text = (
                    f"[{SKILL_NAME.upper()}] Generation failed -- both Ollama and the "
                    f"deterministic fallback errored. See logs."
                )
                concise = full_text
                status = "fallback_error"

        # Append raw METAR + NAS appendix to the traditional brief body
        full_text = full_text.rstrip() + raw_appendix

        # Operator directive 2026-07-23: fold in the weekly AAM (vertiport/
        # eVTOL/Part 108) watch section if a fresh one exists -- the scrape
        # + synthesis runs weekly (aam_weekly_watch.py), this just reads
        # the cached result, no extra Ollama call on every hourly run.
        aam_section = get_aam_watch_section("ops")
        if aam_section:
            full_text = full_text.rstrip() + "\n\n=== " + aam_section

        # 2026-08-10 catch-up session: short truncated capsule of the daily
        # disruption/weather digest (poller/skills/disruption_weather_digest.py,
        # daily 04:35 ET) -- the "leadership standup"-style short version
        # requested for the daily brief, distinct from that skill's full
        # note in the second-brain vault. Same cached-read, no-extra-
        # Ollama-call pattern as the AAM section above.
        disruption_capsule = get_disruption_weather_capsule()
        if disruption_capsule:
            full_text = full_text.rstrip() + "\n\n=== DISRUPTION/WEATHER (30d) ===\n" + disruption_capsule

        now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
        brief_label = "OPS BRIEF+TREND" if is_6h_boundary else "OPS BRIEF"
        title     = f"{brief_label} {now_label}"

        # At 6h boundaries (00/06/12/18 ET): lead the brief with a retrospective +
        # predictive trend package, THEN the traditional brief body underneath.
        if is_6h_boundary:
            log.info("%s: 6h boundary — generating trend analysis", SKILL_NAME)
            trend_prompt = _trend_analysis_prompt()
            trend_narrative = _generate_trend_narrative(trend_prompt)
            if not trend_narrative:
                # Deterministic fallback: raw trend data stands in for the narrative
                trend_narrative = (
                    "RETROSPECTIVE (LAST 6H): narrative unavailable (Ollama offline) — raw data below.\n\n"
                    "PREDICTIVE (NEXT 6H): narrative unavailable (Ollama offline).\n\n"
                    f"{trend_prompt}"
                )
            full_text = (
                f"{title}\n\n"
                f"=== 6-HOUR TREND ===\n{trend_narrative}\n\n"
                f"=== TRADITIONAL BRIEF ===\n{full_text}"
            )
            log.info("%s: trend section prepended", SKILL_NAME)

        state = pathlib.Path(config.state_dir())
        state.mkdir(parents=True, exist_ok=True)
        (state / "ops-brief.txt").write_text(full_text)
        (state / "daily-brief.txt").write_text(full_text)

        # Archive to DB for brief history (BriefView 7-day history)
        try:
            db.archive_brief(full_text, brief_type="ops", source="skill")
        except Exception as arch_err:
            log.warning("brief archive failed: %s", arch_err)

        _send_ntfy_dual(full_text, concise, title)

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--trend", action="store_true",
                        help="Force a 6h trend analysis section regardless of current hour")
    parser.add_argument("--deferred-rerun", action="store_true",
                        help="This run IS a webinar-deferred rerun -- skip the defer check itself")
    args = parser.parse_args()
    main(force=args.force, run_trend=args.trend, deferred_rerun=args.deferred_rerun)
