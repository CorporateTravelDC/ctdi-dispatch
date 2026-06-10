"""
ops-brief — unified 6-hour operational briefing (merged from daily-brief).

Model: claude-sonnet-4-6
Schedule: 00:00, 06:00, 12:00, 18:00 ET (corporatetraveldc-ops-brief.timer)
SR-1: log_usage() in finally block
SR-2: Exempt — time-bounded input, inputs always new.

Supersedes daily-brief (05:00 ET). Covers everything daily-brief did plus:
- Northeast corridor airports (JFK/EWR/LGA/BOS/PHL)
- Transcontinental hubs (LAX/SFO/SEA/ORD/DFW/ATL/DEN)
- FAA NAS XML (direct pull, not just DB cache)
- NWS alerts for DC + Northeast
- Amtrak NEC status

Writes to both ops-brief.txt and daily-brief.txt so /api/v1/brief keeps working.

Pushes to:
  dispatch-debriefs  — full narrative (priority 3)
  dispatch           — concise bottom line (priority 3)
Both fire simultaneously.
"""

import argparse
import json
import logging
import pathlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
import anthropic

from common import config, db
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "ops-brief"
MODEL = "claude-sonnet-4-6"

HUB_AIRPORTS = "KDCA,KIAD,KBWI,KJFK,KEWR,KLGA,KBOS,KPHL,KORD,KATL,KLAX,KSFO,KSEA,KDEN,KDFW"
AVIATIONWX_METAR = f"https://aviationweather.gov/api/data/metar?ids={HUB_AIRPORTS}&format=raw&hours=1"
FAA_NAS_URL = "https://nasstatus.faa.gov/api/airport-status-information"
NWS_ALERTS_URL = (
    "https://api.weather.gov/alerts/active"
    "?area=VA,MD,DC,NY,NJ,CT,MA,PA,DE,RI&status=actual&severity=Extreme,Severe,Moderate"
)
AMTRAKER_URL = "https://api.amtraker.com/v3/trains"

NEC_ROUTES = [
    "Acela", "Northeast Regional", "Palmetto", "Carolinian",
    "Vermonter", "Keystone", "Empire",
]

SYSTEM_PROMPT = """You are producing a 6-hour operational briefing for CS Executive Services,
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

TFRs: VIP/POTUS TFRs active or expected. Include TFR ID if known. Note any
impacts to DC-area airspace. If none active, state that.

NWS ALERTS: Any active Severe or Extreme weather alerts for DC/Northeast.
If none, one line stating that.

AMTRAK NEC: Status of Northeast Corridor trains — Acela and NE Regional.
Note any delays over 15 minutes. If feed unavailable, say so.

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
        return "\n".join(
            f"{t.get('trainNum','?')} {t.get('routeName','?')} "
            f"{t.get('origCode','?')}->{t.get('destCode','?')} "
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


def _send_ntfy_dual(full_text: str, concise_text: str, title: str) -> None:
    # Strip label suffix from token (stored as "token:label" in secrets.env)
    token = config.ntfy_token().split(":")[0]
    base = config.ntfy_url()
    headers_base = {"Authorization": f"Bearer {token}", "Priority": "3", "Title": title}

    for topic, body, tags in [
        ("dispatch-debriefs", full_text, "airplane,partly_sunny"),
        ("dispatch", concise_text, "airplane"),
    ]:
        try:
            resp = requests.post(
                f"{base}/{topic}",
                data=body.encode("utf-8"),
                headers={**headers_base, "Tags": tags},
                timeout=10,
            )
            if resp.ok:
                log.info("ntfy %s OK (status=%d)", topic, resp.status_code)
            else:
                log.error("ntfy %s HTTP %d: %s", topic, resp.status_code, resp.text[:120])
        except Exception as e:
            log.error("ntfy %s FAILED: %s", topic, e)


def build_brief_content() -> tuple[str, str]:
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

    parts = [
        f"=== OPS BRIEF DATA PULL {now_utc} ===",
        f"CPS:\n{_cps_section()}",
        f"TFRs:\n{_tfr_section()}",
        f"METARs (hub airports):\n{raw_metar}",
        f"FAA NAS PROGRAMS:\n{raw_nas}",
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
        f"NAS STATUS:\n{raw_nas}"
    )
    return prompt_content, raw_appendix


def _build_fallback_brief(content: str) -> tuple[str, str]:
    """
    Build a plain-data brief from raw content when Claude API is unavailable
    (zero balance, invalid key, rate limit, or other API-level failure).
    Returns (full_text, concise_text) — same contract as the AI path.
    Flagged clearly so operator knows no narrative was generated.
    This is the standing fallback for all ops_brief API failures.
    """
    now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
    lines = content.splitlines()
    nas_lines, metar_lines, nws_lines, tfr_lines, amtrak_lines = [], [], [], [], []
    current = None
    for line in lines:
        u = line.upper()
        if "FAA NAS PROGRAMS" in u:       current = "nas"
        elif "METARS" in u:               current = "metar"
        elif "NWS ALERTS" in u:           current = "nws"
        elif "TFRS:" in u:                current = "tfr"
        elif "AMTRAK" in u:               current = "amtrak"
        elif line.startswith("==="):      current = None
        elif current == "nas"    and line.strip(): nas_lines.append(line.strip())
        elif current == "metar"  and line.strip().startswith(("METAR","SPECI")): metar_lines.append(line.strip())
        elif current == "nws"    and line.strip(): nws_lines.append(line.strip())
        elif current == "tfr"    and line.strip(): tfr_lines.append(line.strip())
        elif current == "amtrak" and line.strip(): amtrak_lines.append(line.strip())

    dc_ne  = [l for l in metar_lines if any(x in l for x in ("KDCA","KIAD","KBWI","KJFK","KEWR","KLGA","KBOS","KPHL"))]
    xcon   = [l for l in metar_lines if any(x in l for x in ("KLAX","KSFO","KSEA","KORD","KDFW","KATL","KDEN"))]

    full  = f"[DATA BRIEF — API FALLBACK] {now_label}\n"
    full += "Claude API unavailable (credit balance or key issue). Raw data push — no narrative.\n\n"
    full += "NAS PROGRAMS:\n" + ("\n".join(nas_lines[:12]) if nas_lines else "None active") + "\n\n"
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


def main(force: bool = False) -> None:
    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    input_tokens = output_tokens = 0
    status = "error"

    # API error types that should trigger the data fallback rather than a silent crash
    API_FALLBACK_ERRORS = (
        anthropic.BadRequestError,       # credit balance too low
        anthropic.AuthenticationError,   # invalid or revoked key
        anthropic.PermissionDeniedError, # key lacks access
        anthropic.RateLimitError,        # rate limited
    )

    try:
        content, raw_appendix = build_brief_content()

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=750,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": "Current operational data:\n\n" + content}],
            )
            input_tokens  = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            status = "ok"

            full_text = response.content[0].text
            log.info("%s: %d+%d tokens", SKILL_NAME, input_tokens, output_tokens)

            lines   = full_text.splitlines()
            bot_idx = next((i for i, l in enumerate(lines) if "BOTTOM LINE" in l.upper()), None)
            if bot_idx is not None:
                concise = " ".join(l for l in lines[bot_idx + 1:bot_idx + 4] if l.strip()).strip()[:280]
            else:
                concise = " ".join(l for l in lines[:3] if l.strip())[:280]

        except API_FALLBACK_ERRORS as api_err:
            # Standing fallback: API key issue → push raw data brief to ntfy
            # rather than silently failing. Operator sees data; can top up and
            # the next scheduled run restores full AI narrative automatically.
            log.warning("%s: API fallback triggered (%s: %s)", SKILL_NAME, type(api_err).__name__, api_err)
            status = "fallback"
            full_text, concise = _build_fallback_brief(content)  # raw_appendix not needed — fallback is already a data brief

        # Hybrid layout — append raw METAR + NAS to both AI narrative and fallback
        full_text = full_text.rstrip() + raw_appendix

        now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
        title     = f"OPS BRIEF {now_label}" if status == "ok" else f"OPS BRIEF {now_label} [FALLBACK]"

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
        log_usage(SKILL_NAME, MODEL, input_tokens, output_tokens, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
