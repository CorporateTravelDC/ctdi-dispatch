"""common.privacy_jurisdictions -- non-US aircraft follow their own country's privacy default
(2026-11-02, operator directive: "default to what each country requires or does itself for
non-US aircraft that are private"; opt-out requests are honoured on top, in any country,
through the operator-kept aircraft list in common.withheld).

Two decisions per country, from the global privacy-parity research of 2026-11-02:
  private_display  "withhold": a private (non-commercial) aircraft's identity is withheld
                   from everyone without an aircraft pass or master token.
                   "show": shown like any other aircraft (LADD / opt-outs still apply).
  owner_public     whether the country itself publishes owner names; when False the
                   platform never returns an owner name for that country's aircraft.

"Private" = not flying under an ICAO operator designator: no call sign, a call sign equal
to the registration, or anything that is not three letters + a flight number. A PIA
third-party call sign is withheld already (db.PIA_CALLSIGN_PREFIXES). Business-aviation
charters under an operator designator count as commercial and are shown, matching the
European tracker practice this mirrors.

The nationality comes from the registration mark (longest known prefix) or, failing that,
the ICAO 24-bit address block (ICAO Annex 10 Vol III allocations, largest blocks only).
US aircraft (N, A00000-AFFFFF) are governed by LADD / PIA and never by this table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Policy:
    country: str
    private_display: str      # "withhold" | "show"
    owner_public: bool
    basis: str


_EUROPE = ("European practice: non-commercial aircraft are blocked from public tracking by default "
           "(owner opt-in), per FlightAware's European coverage statement; no EASA/EUROCONTROL rule found")
POLICIES: dict[str, Policy] = {
    # Europe (EU/EEA, Switzerland, UK and Crown dependencies, Monaco): withhold private.
    **{c: Policy(c, "withhold", False, _EUROPE) for c in (
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "GR", "HU", "IE", "IS", "IT", "LV", "LT",
        "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "CH", "MC", "SM")},
    "DE": Policy("DE", "withhold", False, _EUROPE + "; LuftVG s.64(6): owner published only with consent"),
    "GB": Policy("GB", "withhold", True, _EUROPE + "; ANO 2016 art. 27: the register (owner) is public"),
    "IM": Policy("IM", "withhold", True, _EUROPE + " (Isle of Man register)"),
    "GG": Policy("GG", "withhold", True, _EUROPE + " (Guernsey register)"),
    "AE": Policy("AE", "withhold", False, "GCAA CAR-RCA: the register is not public; the owner is notified "
                                          "before any disclosure"),
    "NZ": Policy("NZ", "show", False, "CAA NZ: individual owners may have name and address withheld"),
    "CA": Policy("CA", "show", True, "CAR 202.69(2): owner public; NAV CANADA sensitive call signs arrive via LADD"),
    "AU": Policy("AU", "show", True, "CASA register public; Airservices exclusions are internal only"),
    "JP": Policy("JP", "show", True, "JCAB register open to inspection"),
    "BR": Policy("BR", "show", False, "ANAC RAB: owner tax IDs masked online under LGPD"),
}
# A country not in the table: show the aircraft, never the owner (no default to follow).
UNLISTED = Policy("??", "show", False, "no published national default found")

# Registration nationality marks -> country. Longest prefix wins.
_REG_PREFIXES: dict[str, str] = {
    "OE": "AT", "OO": "BE", "LZ": "BG", "9A": "HR", "5B": "CY", "OK": "CZ", "OY": "DK", "ES": "EE", "OH": "FI",
    "F": "FR", "D": "DE", "SX": "GR", "HA": "HU", "EI": "IE", "EJ": "IE", "TF": "IS", "I": "IT", "YL": "LV",
    "LY": "LT", "LX": "LU", "9H": "MT", "PH": "NL", "LN": "NO", "SP": "PL", "CS": "PT", "YR": "RO", "OM": "SK",
    "S5": "SI", "EC": "ES", "EM": "ES", "SE": "SE", "HB": "CH", "3A": "MC", "T7": "SM", "G": "GB", "M": "IM",
    "2": "GG", "A6": "AE", "ZK": "NZ", "C": "CA", "VH": "AU", "JA": "JP", "PP": "BR", "PR": "BR", "PS": "BR",
    "PT": "BR", "PU": "BR",
}
_REG_SORTED = sorted(_REG_PREFIXES, key=len, reverse=True)

# ICAO 24-bit address blocks (inclusive) -> country; the larger, well-established blocks only.
_HEX_BLOCKS: list[tuple[int, int, str]] = [
    (0x300000, 0x33FFFF, "IT"), (0x340000, 0x37FFFF, "ES"), (0x380000, 0x3BFFFF, "FR"),
    (0x3C0000, 0x3FFFFF, "DE"), (0x400000, 0x43FFFF, "GB"), (0x440000, 0x447FFF, "AT"),
    (0x448000, 0x44FFFF, "BE"), (0x450000, 0x457FFF, "BG"), (0x458000, 0x45FFFF, "DK"),
    (0x460000, 0x467FFF, "FI"), (0x468000, 0x46FFFF, "GR"), (0x470000, 0x477FFF, "HU"),
    (0x478000, 0x47FFFF, "NO"), (0x480000, 0x487FFF, "NL"), (0x488000, 0x48FFFF, "PL"),
    (0x490000, 0x497FFF, "PT"), (0x498000, 0x49FFFF, "CZ"), (0x4A0000, 0x4A7FFF, "RO"),
    (0x4A8000, 0x4AFFFF, "SE"), (0x4B0000, 0x4B7FFF, "CH"), (0x4CA000, 0x4CAFFF, "IE"),
    (0x4CC000, 0x4CCFFF, "IS"), (0x4D0000, 0x4D03FF, "LU"), (0x4D2000, 0x4D23FF, "MT"),
    (0x4D4000, 0x4D43FF, "MC"), (0x7C0000, 0x7FFFFF, "AU"), (0x840000, 0x87FFFF, "JP"),
    (0x896000, 0x896FFF, "AE"), (0xC00000, 0xC3FFFF, "CA"), (0xC80000, 0xC87FFF, "NZ"),
    (0xE40000, 0xE7FFFF, "BR"),
]
_US_HEX = (0xA00000, 0xAFFFFF)
_OPERATOR_CALLSIGN = re.compile(r"^[A-Z]{3}[0-9][0-9A-Z]{0,3}$")


def _norm_reg(reg: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9-]", "", (reg or "").strip().upper())


def country_of(registration: Optional[str] = None, hex_id: Optional[str] = None) -> Optional[str]:
    """ISO-style country code, 'US' for a US aircraft, or None when unknown."""
    reg = _norm_reg(registration)
    if reg:
        if reg.startswith("N") and (len(reg) == 1 or reg[1].isdigit()):
            return "US"
        bare = reg.replace("-", "")
        head = reg.split("-", 1)[0] if "-" in reg else None
        if head is not None and head in _REG_PREFIXES:
            return _REG_PREFIXES[head]
        for p in _REG_SORTED:
            if bare.startswith(p) and len(bare) > len(p):
                return _REG_PREFIXES[p]
        if not hex_id:
            return "??"                       # a non-US mark we have no national default for
    try:
        h = int((hex_id or "").strip(), 16)
    except ValueError:
        return None
    if _US_HEX[0] <= h <= _US_HEX[1]:
        return "US"
    for lo, hi, c in _HEX_BLOCKS:
        if lo <= h <= hi:
            return c
    return None


def policy_for(country: Optional[str]) -> Optional[Policy]:
    if country in (None, "US"):
        return None
    return POLICIES.get(country, UNLISTED)


def is_private(callsign: Optional[str], registration: Optional[str] = None) -> bool:
    cs = re.sub(r"[^A-Z0-9]", "", (callsign or "").upper())
    if not cs:
        return True
    if cs == _norm_reg(registration).replace("-", ""):
        return True
    return not _OPERATOR_CALLSIGN.match(cs)


def withheld_by_country(item: dict) -> bool:
    """True when the aircraft's own country withholds private aircraft and this one is private."""
    reg = item.get("registration") or item.get("r")
    hx = item.get("hex") or item.get("hex_id")
    p = policy_for(country_of(reg, hx))
    if p is None or p.private_display != "withhold":
        return False
    cs = item.get("callsign") or item.get("flight") or item.get("identifier")
    return is_private(cs, reg)


def owner_public(registration: Optional[str] = None, hex_id: Optional[str] = None) -> bool:
    """May an owner name be shown? US: yes (FAA public record, LADD handled elsewhere)."""
    p = policy_for(country_of(registration, hex_id))
    return True if p is None else p.owner_public
