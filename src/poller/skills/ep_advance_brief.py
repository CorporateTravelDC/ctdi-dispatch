"""
ep-advance — Executive Protection advance brief for DC UHNWI principals.

Model: ollama/csexec-osint (mistral-nemo)
MCP: https://github.com/CorporateTravelDC/corporatetravel-dispatch-mcp
Schedule: 07:00 ET daily (corporatetraveldc-ep-advance.timer)
SR-1: log_usage() in finally block
SR-2: Exempt — time-bounded input; inputs always new.

Scope: 4-week multi-national executive stay in Washington DC metro and
       50-mile radius (Northern VA, MD suburbs, rural VA, Camp David corridor).
       Covers threat environment including traditional EP threat categories
       (protest aggregation zones, embassy districts, HVT corridors),
       route intelligence, vetted venue matrix, TFR/POTUS movement indicators,
       weather for outdoor movements, and active OSINT feed items.

Pushes to:
  ep-advance  — full advance intel narrative (priority 4) — click → /brief?tab=ep-advance
  ep          — concise bottom line (priority 3)
  ep-briefs   — reserved for on-demand EP snapshot briefs (OOOI-style discrete events)
"""

import os
import argparse
import json
import logging
import pathlib
import time
from datetime import datetime, timezone

import httpx
import requests

from common import config, db, ntfy_push as _ntfy
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME      = "ep-advance"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL    = (
    os.getenv("OLLAMA_OSINT_MODEL")
    or os.getenv("OLLAMA_MODEL")
    or "mistral"
)
MODEL        = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"
OLLAMA_TIMEOUT = 1200   # 20 min — Pi 5 needs headroom for large prompts


# ── Traditional EP threat site categories ─────────────────────────────────────
# Zones where crowd aggregation, political activity, or target density
# create elevated EP complexity independent of TFR or road closures.

EP_THREAT_SITES = {
    "protest_aggregation_zones": [
        {
            "name": "Lafayette Square / H Street NW",
            "zone": "WHCA",
            "ep_notes": (
                "Primary protest aggregation point for WHCA-adjacent demonstrations. "
                "History of rapid crowd escalation without advance notice. "
                "Directly adjoins Hay-Adams and St. Regis approach corridors. "
                "Monitor: DC Police MPD/Special Operations, Secret Service outer perimeter activity."
            ),
        },
        {
            "name": "Lincoln Memorial / Reflecting Pool",
            "zone": "MALL",
            "ep_notes": (
                "Second-largest demonstration staging site in DC after Lafayette Square. "
                "Mall closed to vehicle traffic during permitted events — impacts I-395 and 23rd St approach. "
                "Overflow crowds affect 23rd St NW, Constitution Ave, and Rock Creek Pkwy ingress/egress. "
                "Monitor NPS permit database and @DCPolice for event notifications."
            ),
        },
        {
            "name": "McPherson Square / Franklin Square",
            "zone": "NW",
            "ep_notes": (
                "K Street NW occupation history; encampment hub. "
                "Impacts I-395/K St NW transit corridor. "
                "Located between St. Regis (2 blocks) and Hay-Adams (3 blocks). "
                "Protests here can close K Street NW segment — reroute via M St NW."
            ),
        },
        {
            "name": "DuPont Circle",
            "zone": "NW",
            "ep_notes": (
                "High-frequency protest/demonstration staging for non-political events. "
                "Embassy Row adjacency creates secondary impact during diplomatic incidents. "
                "Massachusetts Ave NW (Embassy Row) runs north from circle — closures possible. "
                "Jefferson and CUT/Rosewood approach corridor; monitor on evenings."
            ),
        },
        {
            "name": "Freedom Plaza / Pennsylvania Ave NW",
            "zone": "WHCA",
            "ep_notes": (
                "Pennsylvania Ave NW between 13th and 15th St — permitted march staging area. "
                "Direct impact on Waldorf Astoria (Old Post Office) vehicle access. "
                "Federal Triangle Metro creates pedestrian density surge during events. "
                "Pennsylvania Ave vehicle closures can last 2-6 hours on event days."
            ),
        },
    ],
    "diplomatic_threat_zones": [
        {
            "name": "Embassy Row — Massachusetts Ave NW",
            "zone": "NW",
            "ep_notes": (
                "High-concentration diplomatic mission corridor from Dupont Circle to Naval Observatory. "
                "Protests targeting specific embassies create rolling closures. "
                "Enhanced law enforcement presence during international incidents — both asset and congestion factor. "
                "Particular sensitivity: Russian Embassy (2650 Wisconsin Ave NW), "
                "Chinese Embassy (3505 International Pl NW), Israeli Embassy (3514 International Dr NW). "
                "Maintain situational awareness of geopolitical events affecting principal's nationality."
            ),
        },
        {
            "name": "Georgetown Diplomatic Residences",
            "zone": "GT",
            "ep_notes": (
                "Multiple ambassadorial residences in Georgetown — R St NW / S St NW corridor. "
                "Events at residences draw diplomatic security details without public notice. "
                "Wisconsin Ave / P St NW intersection: frequent spontaneous traffic management. "
                "Elevated TECS/CARS screening presence in zone on event evenings."
            ),
        },
    ],
    "hvt_corridors": [
        {
            "name": "Capitol Hill / Union Station",
            "zone": "CAP",
            "ep_notes": (
                "Highest ambient law enforcement density in DC outside WHCA. "
                "Capitol Police, MPD, USSS details, and Congressional security details co-present. "
                "Union Station: Amtrak terminus — high VIP transit volume, frequent LEO screening activity. "
                "Delaware Ave NE / Louisiana Ave NE approach during congressional sessions: motorcade conflict likely. "
                "Book EP principals into Capitol Hill only with advance coordination via Congressional liaison. "
                "Pineapple and Pearls (8th St SE) is low-exposure alternative in zone."
            ),
        },
        {
            "name": "K Street NW Corridor / Lobbyist Row",
            "zone": "NW",
            "ep_notes": (
                "High concentration of government contractor and lobbying offices — target-rich environment. "
                "Vehicle surveillance risk elevated due to proximity to WHCA and federal agencies. "
                "Multiple choke points on K St between 14th and 22nd St NW. "
                "Transit only; do not use as venue corridor. Reroute via M St NW."
            ),
        },
    ],
    "potus_movement_corridors": [
        {
            "name": "16th Street NW / Pennsylvania Ave NW",
            "zone": "WHCA",
            "ep_notes": (
                "Primary POTUS motorcade route — White House north to Maryland / south to Capitol. "
                "16th St NW closures can extend from Lafayette Square to Columbia Rd NW (~30 blocks). "
                "Allow +30 min on any transit using this corridor when VIP TFRs active."
            ),
        },
        {
            "name": "South Lawn / Marine One Corridor",
            "zone": "WHCA",
            "ep_notes": (
                "Marine One departures/arrivals restrict airspace (P-56A/B) and close "
                "17th St NW, Constitution Ave, and E St NW Express for 15-45 min. "
                "Monitor TFR feed — flight patterns and timing are TFR-predictive."
            ),
        },
    ],
}


# ── Curated UHNWI venue matrix — DC Core ──────────────────────────────────────
# Each entry: name, tier (AAA/AA/A), zone, notes for EP advance.
# Zones: WHCA=White House Complex Area, GT=Georgetown, CAP=Capitol Hill,
#        CLG=Cleveland/Chevy Chase corridor, NW=NW quadrant, SW=SW waterfront

EP_VENUES = {
    "hotels": [
        {
            "name": "The Hay-Adams",
            "tier": "AAA",
            "zone": "WHCA",
            "address": "800 16th St NW",
            "ep_notes": (
                "Overlooks Lafayette Square and White House north facade. "
                "Known to security community — WHCA proximity means frequent POTUS movement corridor. "
                "Discrete suite entrance via H St NW. Rooftop event space EP-viable. "
                "Basement loading dock usable for discrete principal arrival. "
                "Note: Lafayette Square protest activity can impact H St NW approach."
            ),
        },
        {
            "name": "The St. Regis Washington DC",
            "tier": "AAA",
            "zone": "WHCA",
            "address": "923 16th St NW",
            "ep_notes": (
                "Half-block from White House Security Perimeter. "
                "Motorcade-friendly K St approach. Discreet butler entrance on K St. "
                "Frequently accommodates diplomatic and head-of-state delegations — "
                "staff accustomed to security detail protocols. "
                "Confirm advance with security director prior to arrival. "
                "McPherson Square protest risk: K St closure alternate via L St NW."
            ),
        },
        {
            "name": "Salamander Washington DC",
            "tier": "AAA",
            "zone": "SW",
            "address": "1330 Maryland Ave SW",
            "ep_notes": (
                "Formerly Mandarin Oriental — acquired by Salamander Collection 2022, "
                "renovation completed 2024. 373 rooms. SW waterfront, Tidal Basin and "
                "Jefferson Memorial views. Removed from primary VIP corridor — lower ambient "
                "security activity. Porte-cochere and loading area suitable for principal "
                "extraction. Good lateral routes to I-395/295; direct access avoids downtown "
                "grid entirely. Preferred for principals wanting separation from Embassy Row "
                "and K Street activity. No protest aggregation zones within 1 mile."
            ),
        },
        {
            "name": "Four Seasons Hotel Georgetown",
            "tier": "AAA",
            "zone": "GT",
            "address": "2800 Pennsylvania Ave NW",
            "ep_notes": (
                "Georgetown's top-tier property; detached from downtown protest corridors. "
                "M St / Pennsylvania Ave approach — plan for Georgetown traffic 17:00–20:00. "
                "Canal-level parking suitable for discrete vehicle staging. "
                "Regular UHNWI/diplomatic clientele; security-aware management."
            ),
        },
        {
            "name": "Ritz-Carlton Georgetown",
            "tier": "AAA",
            "zone": "GT",
            "address": "3100 South St NW",
            "ep_notes": (
                "Converted incinerator — compact footprint, residential adjacency. "
                "C&O Canal access creates natural perimeter on south side. "
                "Lower ambient foot traffic vs. Four Seasons; preferred for privacy. "
                "Limited vehicle staging — confirm with property security in advance."
            ),
        },
        {
            "name": "Ritz-Carlton Washington DC",
            "tier": "AAA",
            "zone": "NW",
            "address": "1150 22nd St NW",
            "ep_notes": (
                "West End / M Street corridor. Near Kennedy Center and Embassy Row. "
                "Hotel-integrated vehicle staging on 22nd St NW. "
                "Frequent international diplomatic clientele — culturally aligned for "
                "multi-national UHNWI principals. Security director experienced with "
                "high-profile protective details. M St / New Hampshire Ave extraction routes."
            ),
        },
        {
            "name": "Hotel Washington",
            "tier": "AAA",
            "zone": "WHCA",
            "address": "515 15th St NW",
            "ep_notes": (
                "Adjacent to White House and National Mall. Pennsylvania Ave NW and "
                "Treasury Building flanking — highest ambient law enforcement density in zone. "
                "W Roof Deck faces White House directly — EP-notable for overwatch and "
                "event photography. Indoor porte-cochere via E St NW. "
                "Federal Triangle pedestrian surge impacts 15th St NW during major events; "
                "use E St NW approach as primary. Ideal for principals with Treasury/State "
                "Department meetings same-day."
            ),
        },
        {
            "name": "Conrad Washington DC",
            "tier": "AAA",
            "zone": "CAP",
            "address": "950 New York Ave NW",
            "ep_notes": (
                "Convention Center / Mt. Vernon Triangle. 24-hour gym; rooftop bar and pool. "
                "New York Ave NW approach: I-395 direct access, minimal residential adjacency. "
                "Mount Vernon Triangle: moderate ambient activity; lower protest risk than WHCA. "
                "Newer property (2019) — less name-recognition in security community, "
                "providing natural counter-surveillance advantage. Good for principals "
                "who prefer discretion over status signaling."
            ),
        },
        {
            "name": "Park Hyatt Washington DC",
            "tier": "AAA",
            "zone": "NW",
            "address": "1201 24th St NW",
            "ep_notes": (
                "West End / M Street. Host to Blue Duck Tavern. "
                "24th St NW vehicle staging. Integrated hotel-restaurant security environment. "
                "Regular diplomatic and international hotel guest profile. "
                "Private dining arrangements through hotel concierge simplify advance coordination."
            ),
        },
        {
            "name": "The Jefferson Hotel",
            "tier": "AAA",
            "zone": "NW",
            "address": "1200 16th St NW",
            "ep_notes": (
                "Historic Beaux-Arts; M Street proximity to WHCA. "
                "Discrete entrance off 16th or M St — confirm which is clear on arrival day. "
                "Small and boutique — staff ratios high, discretion standard. "
                "Good for low-profile stays where principal prefers minimal lobby exposure."
            ),
        },
        {
            "name": "The Watergate Hotel",
            "tier": "AA",
            "zone": "GT",
            "address": "2650 Virginia Ave NW",
            "ep_notes": (
                "Kennedy Center–adjacent; Potomac River south facade. "
                "Good for principals with KC events — 200m walk via covered approach. "
                "River entrance provides discrete waterside arrival option (boat/water taxi). "
                "Virginia Ave NW approach; Rock Creek Pkwy extraction available."
            ),
        },
        {
            "name": "Waldorf Astoria Washington DC",
            "tier": "AAA",
            "zone": "CAP",
            "address": "818 Connecticut Ave NW",
            "ep_notes": (
                "Former Trump International / Old Post Office — Pennsylvania Ave NW. "
                "Capitol Hill and K Street equidistant. "
                "Federal Triangle proximity means event-related security closures possible. "
                "Indoor porte-cochère (Pennsylvania Ave entrance) — all-weather arrival. "
                "High ambient law enforcement presence in zone; note as security asset and crowd factor."
            ),
        },
    ],
    "restaurants": [
        {
            "name": "minibar by José Andrés",
            "tier": "AAA",
            "zone": "NW",
            "address": "855 E St NW",
            "ep_notes": (
                "2-Michelin-star; tasting menu only, counter seating, 12 seats total. "
                "Effectively a private dining experience — minimal ambient exposure. "
                "E St NW vehicle access; adjacent to Penn Quarter. "
                "Pre-reservation mandatory; coordinate arrival time with reservation for principal extraction."
            ),
        },
        {
            "name": "Bresca",
            "tier": "AAA",
            "zone": "NW",
            "address": "1906 14th St NW",
            "ep_notes": (
                "1-Michelin-star; 14th Street NW corridor. "
                "Evening ambient crowd on 14th St — plan 30-min vehicle staging buffer. "
                "Small room; private dining room available on request. "
                "U Street/Shaw adjacency — monitor for weekend event pedestrian surges."
            ),
        },
        {
            "name": "Pineapple and Pearls",
            "tier": "AAA",
            "zone": "CAP",
            "address": "715 8th St SE",
            "ep_notes": (
                "2-Michelin-star; Capitol Hill adjacent, Barracks Row. "
                "Capitol Hill residential zone — low ambient foot traffic evenings. "
                "Limited street parking; confirm vehicle staging with 8th St SE loading zone. "
                "Low profile neighborhood; principal exposure minimal."
            ),
        },
        {
            "name": "The Inn at Little Washington",
            "tier": "AAA",
            "zone": "RURAL-VA",
            "address": "309 Middle St, Washington, VA",
            "ep_notes": (
                "3-Michelin-star; ~67mi from DC via US-211 / I-66. "
                "Rural Rappahannock County — naturally low threat density. "
                "No POTUS TFR impact expected at this range unless CAMP DAVID corridor active. "
                "Drive time 75–90 min each way; plan full-day excursion. "
                "Private dining rooms available; inn rooms for overnight stay option."
            ),
        },
        {
            "name": "Fiola Mare",
            "tier": "AA",
            "zone": "GT",
            "address": "3050 K St NW",
            "ep_notes": (
                "Georgetown waterfront; K Street at Wisconsin Ave. "
                "Potomac River views; outdoor terrace — weather dependency for seating. "
                "Georgetown harbour parking structure for vehicle staging. "
                "Adjacent to busy waterfront development; peak crowd 18:00–21:00 Fri/Sat."
            ),
        },
        {
            "name": "Blue Duck Tavern",
            "tier": "AA",
            "zone": "NW",
            "address": "1201 24th St NW (Park Hyatt)",
            "ep_notes": (
                "Park Hyatt DC ground floor; West End / M Street. "
                "Hotel-integrated venue — highest security posture for meal meetings. "
                "Private dining room on request; hotel loading dock for vehicle staging. "
                "Suitable for principal meetings requiring hotel-level discretion."
            ),
        },
        {
            "name": "Bourbon Steak Georgetown",
            "tier": "AA",
            "zone": "GT",
            "address": "2800 Pennsylvania Ave NW (Four Seasons)",
            "ep_notes": (
                "Four Seasons DC ground floor; hotel-level security as ambient environment. "
                "Power dining room — high probability of other UHNWI/government principals. "
                "Coordinate arrival with Four Seasons security director. "
                "Private dining available; M St staging complex on weekends."
            ),
        },
        {
            "name": "CUT by Wolfgang Puck",
            "tier": "AA",
            "zone": "NW",
            "address": "1050 Connecticut Ave NW (Rosewood Washington DC)",
            "ep_notes": (
                "Inside Rosewood Washington DC; Connecticut Ave corridor. "
                "Dupont Circle / K Street axis — government/embassy clientele. "
                "Hotel integration provides security layer; confirm suite/private dining availability. "
                "Connecticut Ave approach preferred over N St NW for vehicle staging."
            ),
        },
        {
            "name": "Rasika West End",
            "tier": "AA",
            "zone": "NW",
            "address": "1190 New Hampshire Ave NW",
            "ep_notes": (
                "Top-ranked Indian fine dining; West End / Kennedy Center axis. "
                "Regular diplomatic and international clientele — culturally appropriate for South Asian delegations. "
                "New Hampshire Ave NW staging; low foot traffic after 20:00. "
                "Private events available; typically requires advance coordination."
            ),
        },
    ],
    "venues_cultural": [
        {
            "name": "Kennedy Center for the Performing Arts",
            "tier": "AAA",
            "zone": "GT",
            "address": "2700 F St NW",
            "ep_notes": (
                "Flagship performing arts venue; presidential box available (advance WHCA coordination required). "
                "Multiple ingress/egress: River Terrace (vehicle, controlled), "
                "New Hampshire Ave (public, higher exposure), Concert Hall Garage (discrete). "
                "River Terrace preferred for principal arrival — waterside drop-off, low ambient crowd. "
                "POTUS visits draw USSS footprint — confirm TFR status before movement."
            ),
        },
        {
            "name": "National Gallery of Art",
            "tier": "AA",
            "zone": "MALL",
            "address": "6th St & Constitution Ave NW",
            "ep_notes": (
                "East and West Buildings; Mall facing = high public exposure. "
                "Staff-only entrance on Madison Dr side recommended for discrete arrival. "
                "Private after-hours tours available via Director's office — EP-preferred option. "
                "4th/6th St NW for vehicle staging; Mall foot traffic 10:00–17:00 heavy in summer."
            ),
        },
        {
            "name": "Dumbarton Oaks",
            "tier": "AA",
            "zone": "GT",
            "address": "1703 32nd St NW",
            "ep_notes": (
                "Harvard research center and historic gardens; Georgetown residential. "
                "Low public profile; Harvard/academic guest protocols. "
                "32nd St NW — Georgetown residential streets, low traffic. "
                "Garden entrance discrete; suitable for private walks. "
                "Museum tours by appointment — advance coordination provides natural security scheduling."
            ),
        },
        {
            "name": "Library of Congress",
            "tier": "A",
            "zone": "CAP",
            "address": "101 Independence Ave SE",
            "ep_notes": (
                "Capitol Hill — highest ambient LEO density in DC. "
                "Private reading room and executive suite access via Congressional liaison. "
                "First Street SE vehicle staging; Capitol Police ubiquitous in zone. "
                "Suitable for principal with Congressional meetings same-day."
            ),
        },
    ],
}


# ── Extended venue matrix — 50-mile radius ────────────────────────────────────
# Northern VA, MD suburbs, rural VA, and Camp David corridor.

EXTENDED_VENUES_50MI = {
    "northern_va": [
        {
            "name": "Salamander Resort & Spa",
            "location": "Middleburg, VA (~40mi W via US-50)",
            "tier": "AAA",
            "ep_notes": (
                "Salamander Collection flagship rural property. 340 acres, 168 rooms, equestrian estate. "
                "Natural perimeter — rural Loudoun County, no urban protest risk. "
                "Private airstrip at Middleburg Airport (W99) 5 miles; helicopter pad on property. "
                "Preferred for multi-day principal withdrawal from DC grid. "
                "Wine country adjacency: Boxwood Winery, RdV Vineyards — private events viable."
            ),
        },
        {
            "name": "McLean / Langley Corridor",
            "location": "McLean, VA (~10mi W via I-66/Chain Bridge)",
            "tier": "AA",
            "ep_notes": (
                "CIA Headquarters at Langley creates ambient federal security presence. "
                "High-income residential corridor; low protest risk. "
                "Tysons Corner Center and Galleria at Tysons II: discrete retail and dining. "
                "1789 Restaurant (Georgetown) and L'Auberge Chez François (Great Falls) accessible. "
                "Chain Bridge Road / GW Pkwy extraction to Dulles corridor. "
                "Principals with intelligence community meetings may transit this zone."
            ),
        },
        {
            "name": "Great Falls / Potomac River",
            "location": "Great Falls, VA (~15mi NW)",
            "tier": "A",
            "ep_notes": (
                "L'Auberge Chez François — French country dining, private rooms, wooded setting. "
                "Riverbend Park: controlled access, low public density. "
                "GW Pkwy / Chain Bridge extraction; IAD (Dulles) 20 min via Route 7. "
                "Good for discreet lunches away from DC grid."
            ),
        },
        {
            "name": "Leesburg / Loudoun County",
            "location": "Leesburg, VA (~35mi W via Route 7)",
            "tier": "A",
            "ep_notes": (
                "Historic downtown; wine country gateway. "
                "Lansdowne Resort: conference-grade facility, 45 holes golf, private event capacity. "
                "IAD (Dulles) 15 min south on Route 28; rotary or fixed wing extraction viable. "
                "Low ambient threat; Loudoun County Sheriff — non-urban security environment."
            ),
        },
    ],
    "maryland_suburbs": [
        {
            "name": "Chevy Chase / Bethesda Corridor",
            "location": "Bethesda, MD (~8mi NW via Wisconsin Ave)",
            "tier": "AA",
            "ep_notes": (
                "Bethesda Row dining: Le Diplomate-adjacent corridor, private dining options. "
                "Chevy Chase Club: member-only, highest UHNWI density in DC metro. "
                "National Institutes of Health campus adjacency — federal health security footprint. "
                "Wisconsin Ave NW / River Rd extraction; clear route to I-270 and Camp David corridor. "
                "Ourisman Family of Restaurants, Range, Wildwood Kitchen: vetted private dining."
            ),
        },
        {
            "name": "Potomac, MD",
            "location": "Potomac, MD (~15mi NW via River Rd)",
            "tier": "A",
            "ep_notes": (
                "Alta affluence residential corridor; Congressional Country Club nearby (Bethesda). "
                "Inn at Easton / Old Angler's Inn: riverside historic dining; private dining available. "
                "Low transit complexity; River Rd / MacArthur Blvd approach. "
                "Helicopter transit to Camp David accessible from Gaithersburg (Montgomery County Airpark)."
            ),
        },
        {
            "name": "Annapolis, MD",
            "location": "Annapolis, MD (~30mi E via US-50)",
            "tier": "AA",
            "ep_notes": (
                "State capital; Naval Academy presence creates ambient military security. "
                "Chesapeake Bay waterfront: Chesapeake Inn, Chart House. "
                "Private yacht / water transit from DC (Potomac/Bay): ~3h. "
                "USNA Gate access available for principals with flag-level military connections. "
                "BWI airport 20 min north — CONUS extraction via commercial or charter."
            ),
        },
    ],
    "camp_david_corridor": [
        {
            "name": "Frederick, MD / Camp David Corridor",
            "location": "Frederick, MD (~50mi NW via I-270)",
            "tier": "A",
            "ep_notes": (
                "Camp David (Catoctin Mountain) summit is ~18 miles north of Frederick. "
                "When Camp David is occupied: I-270 north of Gaithersburg sees heightened "
                "Maryland State Police and USSS vehicle activity. "
                "Thurmont, MD (gateway town) monitored by USSS outer perimeter — no casual transit. "
                "Hagerstown Regional Airport (HGR): nearest GA/charter field to Camp David. "
                "Avoid recommending Catoctin/Thurmont transit without confirmation Camp David is unoccupied."
            ),
        },
    ],
}


# ── Data fetch helpers ────────────────────────────────────────────────────────

NWS_DC_ALERTS = (
    "https://api.weather.gov/alerts/active"
    "?area=DC,VA,MD&status=actual&severity=Extreme,Severe,Moderate,Minor"
)

def _fetch(url: str, timeout: int = 10) -> str | None:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.warning("ep-advance fetch failed %s: %s", url, e)
        return None


def _weather_section() -> str:
    metars = db.get_metar_snapshot()
    primary = [m for m in metars if m["station"] in ("KDCA", "KIAD", "KBWI")]
    if not primary:
        return "DC area METAR unavailable."
    lines = []
    for m in primary:
        wx = (
            f"{m['station']}: {m.get('ceiling_ft','SKC')}ft / "
            f"{m.get('visibility_sm','?')}SM / "
            f"{m.get('wind_kt','?')}kt"
        )
        if m.get("precip_code"):
            wx += f" [{m['precip_code']}]"
        lines.append(wx)
    return "\n".join(lines)


def _tfr_section() -> str:
    tfrs = db.get_active_tfrs()
    if not tfrs:
        return "No active TFRs. DC VIP corridor clear."
    vip = [t for t in tfrs if t.get("is_vip")]
    if vip:
        ids = ", ".join(t["tfr_id"] for t in vip)
        return (
            f"VIP TFRs ACTIVE: {ids}. "
            "Indicates elevated POTUS/VIP movement — WHCA/DC grid heightened security posture. "
            "Anticipate motorcade corridor closures; allow +20 min ground transit buffer."
        )
    return f"{len(tfrs)} routine TFRs. No VIP/POTUS indicators. DC grid normal."


def _nws_section() -> str:
    raw = _fetch(NWS_DC_ALERTS)
    if not raw:
        return "NWS alerts unavailable."
    try:
        data = json.loads(raw)
        feats = data.get("features", [])
        if not feats:
            return "No active NWS alerts for DC metro."
        return "\n".join(
            f"[{f['properties'].get('severity','?')}] "
            f"{f['properties'].get('event','?')} — "
            f"{(f['properties'].get('headline','') or '')[:90]}"
            for f in feats[:5]
        )
    except Exception as e:
        return f"NWS parse error: {e}"


def _cps_section() -> str:
    cps = db.get_latest_cps()
    if not cps:
        return "CPS unavailable."
    return (
        f"CPS {cps.get('score','?')}/{cps.get('label','?')} — "
        f"{cps.get('narrative','') or 'no narrative'}"
    )


def _route_section() -> str:
    route = db.get_latest_route_narrative()
    if not route or not route.get("route_narrative"):
        return "No active route alerts."
    return route["route_narrative"][:350]


def _threat_sites_section() -> str:
    """Format EP threat site categories for LLM context."""
    lines = ["=== EP THREAT SITE AWARENESS ==="]

    lines.append("\n-- PROTEST AGGREGATION ZONES --")
    for site in EP_THREAT_SITES["protest_aggregation_zones"]:
        lines.append(
            f"  {site['name']} [{site['zone']}]: {site['ep_notes'][:200]}"
        )

    lines.append("\n-- DIPLOMATIC / EMBASSY THREAT ZONES --")
    for site in EP_THREAT_SITES["diplomatic_threat_zones"]:
        lines.append(
            f"  {site['name']}: {site['ep_notes'][:200]}"
        )

    lines.append("\n-- HIGH-VALUE TARGET CORRIDORS --")
    for site in EP_THREAT_SITES["hvt_corridors"]:
        lines.append(
            f"  {site['name']} [{site['zone']}]: {site['ep_notes'][:200]}"
        )

    lines.append("\n-- POTUS MOVEMENT CORRIDORS --")
    for site in EP_THREAT_SITES["potus_movement_corridors"]:
        lines.append(
            f"  {site['name']}: {site['ep_notes'][:150]}"
        )

    return "\n".join(lines)


def _osint_section() -> str:
    """
    Pull recent OSINT feed items from the dispatch DB that are relevant to EP operations.
    Uses osint_get_feed() to retrieve items scored >= MEDIUM (4) from the last 24h.
    DC-area, security-adjacent, and diplomatic items are surfaced.
    Falls back gracefully if no OSINT data available.
    """
    try:
        cutoff_24h = time.time() - 86400
        items = db.osint_get_feed(scope_id=None, min_score=4, limit=20)
        if not items:
            return "No active OSINT feed items in last 24h."

        # Filter to items ingested in last 24h
        recent = [i for i in items if i.get("ingested_at", 0) >= cutoff_24h]
        if not recent:
            return "No recent OSINT items (last 24h) above MEDIUM threshold."

        # EP-relevant keyword filter
        EP_KEYWORDS = {
            "dc", "washington", "capitol", "white house", "potus", "embassy",
            "protest", "demonstration", "motorcade", "security", "threat",
            "arlington", "fairfax", "bethesda", "maryland", "virginia",
            "cia", "fbi", "dhs", "state department", "treasury", "executive",
            "diplomatic", "consulate", "ambassador", "closure", "evacuation",
        }

        filtered = []
        for item in recent:
            text = (
                (item.get("title") or "") + " " +
                (item.get("body") or "") + " " +
                (item.get("scope_label") or "")
            ).lower()
            if any(kw in text for kw in EP_KEYWORDS):
                filtered.append(item)

        if not filtered:
            # Fall back to top-scored recent items if no keyword match
            filtered = recent[:5]

        lines = [f"=== ACTIVE OSINT FEED ({len(filtered)} EP-relevant items last 24h) ==="]
        for item in filtered[:8]:
            score_label = "HIGH" if item.get("score", 0) >= 7 else "MED"
            lines.append(
                f"  [{score_label}] [{item.get('scope_label','?')}] "
                f"{(item.get('title') or 'No title')[:120]} "
                f"— {(item.get('body') or '')[:100]}"
            )
        return "\n".join(lines)

    except Exception as e:
        log.warning("ep-advance: OSINT section failed: %s", e)
        return f"OSINT feed unavailable: {e}"


def _venue_summary() -> str:
    """Compact DC core venue matrix formatted for LLM context."""
    lines = ["=== VETTED VENUE MATRIX — DC CORE (UHNWI / EP-CLEARED) ==="]

    lines.append("\n-- HOTELS (AAA tier) --")
    for h in EP_VENUES["hotels"]:
        if h["tier"] == "AAA":
            lines.append(
                f"  {h['name']} [{h['zone']}] {h['address']}\n"
                f"    EP: {h['ep_notes']}"
            )

    lines.append("\n-- HOTELS (AA tier) --")
    for h in EP_VENUES["hotels"]:
        if h["tier"] == "AA":
            lines.append(f"  {h['name']} [{h['zone']}] — {h['ep_notes'][:120]}…")

    lines.append("\n-- DINING (Michelin / UHNWI tier) --")
    for r in EP_VENUES["restaurants"]:
        lines.append(
            f"  {r['name']} [{r['zone']}] {r['address']}\n"
            f"    EP: {r['ep_notes'][:150]}…"
        )

    lines.append("\n-- CULTURAL VENUES --")
    for v in EP_VENUES["venues_cultural"]:
        lines.append(
            f"  {v['name']} [{v['zone']}] — {v['ep_notes'][:120]}…"
        )

    return "\n".join(lines)


def _extended_venues_summary() -> str:
    """50-mile radius venue matrix for day trips and alternative staging."""
    lines = ["=== EXTENDED VENUE MATRIX — 50-MILE RADIUS ==="]

    lines.append("\n-- NORTHERN VIRGINIA --")
    for v in EXTENDED_VENUES_50MI["northern_va"]:
        lines.append(
            f"  {v['name']} | {v['location']}\n"
            f"    EP: {v['ep_notes'][:200]}"
        )

    lines.append("\n-- MARYLAND SUBURBS --")
    for v in EXTENDED_VENUES_50MI["maryland_suburbs"]:
        lines.append(
            f"  {v['name']} | {v['location']}\n"
            f"    EP: {v['ep_notes'][:200]}"
        )

    lines.append("\n-- CAMP DAVID CORRIDOR --")
    for v in EXTENDED_VENUES_50MI["camp_david_corridor"]:
        lines.append(
            f"  {v['name']} | {v['location']}\n"
            f"    EP: {v['ep_notes'][:200]}"
        )

    return "\n".join(lines)


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the advance intelligence officer for CS Executive Services, preparing a daily
EP-Advance brief for a multi-national UHNWI executive with a personal security detail
on a 4-week Washington DC engagement (full metro including 50-mile radius).

Your audience is the EP team leader — be dense, direct, and operationally specific.
No filler. No hospitality puffery. Prioritize threat, route, timing, and logistics.

Produce a structured plain-text brief. ALL CAPS section labels. No markdown. No bullets.
Sections in order:

THREAT POSTURE: Current DC threat environment based on TFRs, POTUS movement indicators,
active NWS alerts, protest aggregation zone status, embassy district activity, and OSINT
feed intelligence. Rate: GREEN / AMBER / RED with one-line rationale. POTUS VIP TFRs
active = AMBER minimum. Active OSINT items referencing closures, protests, or security
events = AMBER. Combine indicators — if multiple AMBER factors present, escalate to RED.

EP THREAT ADVISORY: Identify any active or elevated risk from the traditional EP threat
site categories: protest aggregation zones (Lafayette Square, Lincoln Memorial, McPherson
Square, DuPont Circle, Freedom Plaza), diplomatic tension zones (Embassy Row, Georgetown
diplomatic residences), HVT corridors (Capitol Hill, K Street), and POTUS movement
corridors (16th St NW, South Lawn). Flag only those with current indicators or pattern risk.
If OSINT feed items mention any of these locations, reference them.

WEATHER IMPACT: Current conditions at KDCA/KIAD/KBWI and outdoor movement implications.
Note any precipitation or wind impacting outdoor venue access or motorcade comfort.

PRINCIPAL MOVEMENT: Ground transit advisory for DC metro and 50-mile radius today. Note
active route closures, POTUS corridor impacts, and recommended vehicle staging approach.
Flag Georgetown, Mall, Embassy Row, and Capitol Hill sectors if high-density. If Camp
David corridor is relevant (POTUS weekend pattern), note I-270 north impact.

HOTEL RECOMMENDATION (TODAY): From the vetted matrix, identify the top 1-2 hotels
that best balance security posture, discretion, and proximity to likely activity zones.
Reference current TFR/threat/OSINT context. Consider SW waterfront (Salamander) for
higher-threat days where separation from protest zones is prioritized.

DINING RECOMMENDATION (TONIGHT): From the vetted matrix, flag 1-2 restaurants appropriate
for tonight given current threat posture, crowd density, and weather. Note any EP
considerations specific to tonight. For elevated threat days, prioritize hotel-integrated
venues (Blue Duck Tavern/Park Hyatt, Bourbon Steak/Four Seasons).

EXTENDED OPERATIONS (50-MILE): If threat posture is AMBER or RED, or if principal
has scheduled excursions, briefly note best alternative staging options outside DC core
(Salamander Middleburg, Bethesda/Chevy Chase corridor, Annapolis waterfront, Great Falls).
Skip this section on GREEN days with no scheduled excursions.

VENUE ADVISORY: Any venues from the matrix with heightened EP complexity today
(Mall events, Capitol Hill activity, Kennedy Center events, Embassy Row closures).
If none, note that.

ADVANCE CHECKLIST: 3-5 specific items the EP team should action or confirm today —
vehicle staging, hotel security liaison, TFR monitoring, protest zone monitoring,
Embassy Row situation check, weather contingencies, 50-mile radius transit if applicable.

BOTTOM LINE: One sentence. Overall EP readiness posture for today and the single
most time-sensitive action item.

Keep total brief under 750 words. Threat posture first; bottom line last."""


# ── Ollama generation ─────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> tuple[str, str] | None:
    if not OLLAMA_BASE_URL:
        return None
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model":  OLLAMA_MODEL,
                "system": SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 750, "temperature": 0.15},
            },
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        narrative = resp.json().get("response", "").strip()
    except Exception as exc:
        log.warning("ep-advance: Ollama failed — %s", exc)
        return None

    if not narrative:
        return None

    # Extract BOTTOM LINE for concise push
    concise = narrative
    for marker in ("BOTTOM LINE:", "Bottom line:", "BOTTOM LINE —"):
        if marker in narrative:
            concise = narrative.split(marker, 1)[1].strip().splitlines()[0].strip()
            break

    now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
    return f"EP-ADVANCE {now_label} (Ollama/{OLLAMA_MODEL})\n\n{narrative}", concise[:220]


# ── Deterministic fallback ────────────────────────────────────────────────────

def _fallback_brief(tfr: str, weather: str, nws: str, route: str, osint: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
    cps = _cps_section()

    has_vip = "VIP TFR" in tfr.upper() and "NO VIP" not in tfr.upper()
    posture = "AMBER — VIP TFRs active" if has_vip else "GREEN — no VIP indicators"

    full = (
        f"[EP-ADVANCE FALLBACK — DETERMINISTIC] {now}\n"
        f"Ollama not configured or unavailable. Raw data only.\n\n"
        f"THREAT POSTURE: {posture}\n\n"
        f"TFR STATUS:\n{tfr}\n\n"
        f"WEATHER:\n{weather}\n\n"
        f"NWS ALERTS:\n{nws}\n\n"
        f"ROUTE:\n{route}\n\n"
        f"CPS: {cps}\n\n"
        f"OSINT INTEL:\n{osint}\n\n"
        f"ADVANCE CHECKLIST (STANDARD):\n"
        f"  1. Confirm hotel security liaison contact at primary property.\n"
        f"  2. Monitor TFR feed — VIP movement pattern may shift within 4h.\n"
        f"  3. Check Lafayette Square / McPherson Square for protest activity.\n"
        f"  4. Stage vehicle at venue 15 min prior; confirm parking with venue advance.\n"
        f"  5. Weather check before each outdoor movement; have covered alternates.\n\n"
        f"BOTTOM LINE: Routine DC EP environment. Confirm hotel, dining advances, and protest zone status."
    )
    concise = f"[EP-ADVANCE FALLBACK] {now} — {posture}. Confirm hotel and dining advances."
    return full, concise


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main(force: bool = False) -> None:
    status = "error"
    try:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        tfr      = _tfr_section()
        weather  = _weather_section()
        nws      = _nws_section()
        route    = _route_section()
        cps      = _cps_section()
        venues   = _venue_summary()
        extended = _extended_venues_summary()
        threats  = _threat_sites_section()
        osint    = _osint_section()

        prompt = "\n\n".join([
            f"=== EP-ADVANCE DATA PULL {now_utc} ===",
            f"TFR / SECURITY INDICATORS:\n{tfr}",
            f"WEATHER (DC AIRPORTS):\n{weather}",
            f"NWS ALERTS (DC/VA/MD):\n{nws}",
            f"CPS:\n{cps}",
            f"ROUTE / GROUND IMPACT:\n{route}",
            osint,
            threats,
            venues,
            extended,
        ])

        result = _call_ollama(prompt)
        if result:
            full_text, concise = result
            status = "ok"
            log.info("ep-advance: brief generated via Ollama/%s", OLLAMA_MODEL)
        else:
            full_text, concise = _fallback_brief(tfr, weather, nws, route, osint)
            status = "ok"
            log.info("ep-advance: brief generated (deterministic fallback)")

        now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
        title     = f"EP-ADVANCE {now_label}"

        # Write to state dir
        state = pathlib.Path(config.state_dir())
        state.mkdir(parents=True, exist_ok=True)
        (state / "ep-advance.txt").write_text(full_text)

        # Archive for BriefView history
        try:
            db.archive_brief(full_text, brief_type="ep-advance", source="skill")
        except Exception as arch_err:
            log.warning("ep-advance: archive failed: %s", arch_err)

        # Push to ntfy
        _ntfy.send("ep-advance", full_text, title=title, priority=4, tags="shield,rotating_light")
        _ntfy.send("ep",         concise,  title=title, priority=3, tags="shield")

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="EP-Advance daily brief skill")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
