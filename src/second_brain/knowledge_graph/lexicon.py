"""
second_brain.knowledge_graph.lexicon -- shared entity lexicon for the
wikilink retrofit (retrofit_links.py) and the graph builder (build_graph.py).

Each entry: (canonical label, subtype, compiled regex). The canonical label
is used VERBATIM as the entity hub note's basename (03-Entities/<label>.md)
and as the [[wikilink]] target, so labels must be filesystem- and
wikilink-safe: no `/ \\ : # | [ ]`.

Matching rules:
- ALL-CAPS acronyms are case-sensitive (a prose "cps" or "aim" must not
  false-positive).
- Deliberately curated to entities that actually recur in THIS vault
  (platform systems, DC-area aviation/rail, the business's verticals) --
  deterministic and reviewable, no LLM in the loop.
"""
import re


def _ci(pat: str) -> re.Pattern:
    return re.compile(rf"\b(?:{pat})\b", re.IGNORECASE)


def _cs(pat: str) -> re.Pattern:
    return re.compile(rf"\b(?:{pat})\b")


LEXICON: list[tuple[str, str, re.Pattern]] = [
    # people / agents
    ("the operator", "person", _cs("the operator")),
    ("Claude", "agent", _cs("Claude")),
    ("Ollama", "agent", _ci("ollama")),
    # FAA SWIM + aviation data systems
    ("FAA", "org", _cs("FAA")),
    ("SWIM", "system", _cs("SWIM")),
    ("FDPS", "system", _cs("FDPS")),
    ("TFMS", "system", _cs("TFMS")),
    ("STDDS", "system", _cs("STDDS|ASDE-X|ASDEX")),
    ("ITWS", "system", _cs("ITWS")),
    ("TBFM", "system", _cs("TBFM")),
    ("AIM-FNS", "system", _cs("AIM-FNS|AIM/FNS|FNS")),
    ("NOTAM", "topic", _cs("NOTAMs?")),
    ("TFR", "topic", _cs("TFRs?")),
    ("METAR", "topic", _cs("METARs?|TAFs?")),
    ("ACARS", "system", _cs("ACARS")),
    ("ADS-B", "system", _ci("ADS-B|adsb|UltraFeeder|tar1090|dump1090")),
    ("FIDS", "system", _cs("FIDS")),
    ("CPS", "system", _cs("CPS")),
    ("NWS", "org", _cs("NWS|NWWS")),
    # airports / places
    ("DCA", "place", _cs("DCA|Reagan National")),
    ("IAD", "place", _cs("IAD|Dulles")),
    ("BWI", "place", _cs("BWI")),
    ("Washington DC", "place", _ci("Washington,? D\\.?C\\.?")),
    ("Potomac TRACON", "place", _cs("PCT|Potomac TRACON")),
    # rail / ground
    ("Amtrak", "org", _ci("Amtrak|Amtraker")),
    ("Northeast Corridor", "topic", _ci("Northeast Regional|Acela|NEC corridor")),
    ("WMATA", "org", _ci("WMATA|Metrorail")),
    ("Limo Anywhere", "system", _ci("Limo ?Anywhere")),
    ("RingCentral", "org", _ci("RingCentral")),
    ("3CX", "system", _cs("3CX")),
    ("Uber", "org", _cs("Uber")),
    ("Lyft", "org", _cs("Lyft")),
    # AAM / aviation industry
    ("Advanced Air Mobility", "topic",
     _ci("AAM|UAM|eVTOL|advanced air mobility|urban air mobility")),
    ("UTM", "topic", _cs("UTM")),
    ("Boeing", "org", _cs("Boeing")),
    ("Archer Aviation", "org", _cs("Archer")),
    ("Joby Aviation", "org", _cs("Joby")),
    ("United Airlines", "org", _ci("United Airlines")),
    ("American Airlines", "org", _ci("American Airlines")),
    ("Delta Air Lines", "org", _ci("Delta Air Lines|Delta flight")),
    ("Marriott", "org", _ci("Marriott")),
    ("Hilton", "org", _ci("Hilton")),
    # infra / stack
    ("Raspberry Pi 5", "system", _ci("Raspberry Pi|Pi ?5")),
    ("Nextcloud", "system", _ci("Nextcloud")),
    ("WebDAV", "system", _ci("WebDAV|PROPFIND")),
    ("Tailscale", "system", _ci("Tailscale|tailnet")),
    ("ntfy", "system", _ci("ntfy")),
    ("Podman", "system", _ci("Podman|Quadlet")),
    ("systemd", "system", _ci("systemd")),
    ("SELinux", "system", _ci("SELinux")),
    ("nginx", "system", _ci("nginx")),
    ("RTL-SDR", "system", _ci("RTL-?SDR")),
    ("ProtonMail Bridge", "system", _ci("Proton ?Mail|protonbridge")),
    ("MCP", "system", _cs("MCP")),
    ("dispatch-mcp", "project", _ci("dispatch-mcp")),
    ("ctdi-dispatch", "project", _ci("ctdi-dispatch(?:-internal)?")),
    ("demo-archiver", "project", _ci("demo-?archiver?|demo\\.db|demo-api")),
    ("Second Brain", "project", _ci("second[- ]brain")),
    ("Scrub Gate", "system", _ci("scrub[- _]gate")),
    ("Watchlist", "system", _ci("watchlists?")),
    ("Thermal Management", "topic",
     _ci("thermal|heatsink|radiator|cgroup|swap[- ]thrash")),
    # business threads
    ("Investor Materials", "topic",
     _ci("investor materials|pitch deck|executive summary|due.diligence")),
    ("ISO 42001", "topic", _ci("ISO[- ]?42001")),
    ("Executive Protection", "topic", _ci("executive protection")),
    ("Gig Economy", "topic",
     _ci("gig economy|gig platform|DoorDash|Uber Eats|Grubhub")),
    ("Corporate Travel", "topic", _ci("corporate travel|business travel")),
    ("Concierge", "topic", _ci("concierge")),
    ("Hotels and Lodging", "topic", _ci("hotel|lodging|resort")),
]

SUBTYPE_OF: dict[str, str] = {label: st for label, st, _ in LEXICON}
