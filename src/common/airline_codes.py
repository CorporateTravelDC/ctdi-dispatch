"""
common.airline_codes -- the ONE IATA <-> ICAO airline-prefix table.

2026-10-03: three divergent private copies existed (flight_resolver.
IATA_TO_ICAO_CARRIER, web/routes/webhooks._IATA_TO_ICAO_CARRIER,
poller/main._ICAO_TO_IATA_CARRIER) and one of them mapped ASH -> YX
(ASH is Mesa = YV; Republic is RPA = YX). They now all import from here.
Motivating case: ACARS/VDL2 `flight` fields are IATA-style (WN4060,
UA1988, AA2251, AC7252) while watchlist identifiers are ICAO (SWA4060,
UAL1988 ...), so ingest.local_airspace's watchlist match never fired on
a real ACARS message.

Extend here, nowhere else. Keep both directions derivable from one dict.
"""
from __future__ import annotations

import re
from typing import Optional

# IATA 2-char carrier -> ICAO 3-letter callsign prefix (FAA ACID form).
# US majors / LCCs / regionals seen at DCA-IAD-BWI, Canadian majors, and the
# long-haul internationals the FIDS path already knew. Union of the three
# former tables plus AC (seen on VDL2 at KDCA 2026-10-03).
IATA_TO_ICAO: dict[str, str] = {
    # US majors
    "AA": "AAL", "UA": "UAL", "DL": "DAL", "WN": "SWA", "AS": "ASA",
    "B6": "JBU", "NK": "NKS", "F9": "FFT", "G4": "AAY", "HA": "HAL",
    "SY": "SCX",
    # US regionals / feeders
    "OO": "SKW", "MQ": "ENY", "9E": "EDV", "YX": "RPA", "OH": "JIA",
    "YV": "ASH", "QX": "QXE", "C5": "UCA", "PT": "PDT", "ZW": "AWI",
    "G7": "GJS", "CP": "CPZ",
    # Canada
    "AC": "ACA", "WS": "WJA", "PD": "POE",
    "QK": "JZA",   # Jazz Aviation (Air Canada Express) -- 2026-10-05
    # Internationals commonly at IAD/BWI
    "BA": "BAW", "KL": "KLM", "AF": "AFR", "LH": "DLH", "VS": "VIR",
    "QR": "QTR", "EK": "UAE", "EY": "ETD", "TK": "THY", "EI": "EIN",
    "IB": "IBE", "LX": "SWR", "OS": "AUA", "SK": "SAS", "AY": "FIN",
    "ET": "ETH", "SV": "SVA", "KE": "KAL", "NH": "ANA", "JL": "JAL",
    # Cargo
    "FX": "FDX", "5X": "UPS",
}

ICAO_TO_IATA: dict[str, str] = {v: k for k, v in IATA_TO_ICAO.items()}

# <carrier><number><optional suffix letter>; carrier is 2 alnum (IATA) or
# 3 alpha (ICAO). Numbers are 1-4 digits. Accepts an optional space.
_CALLSIGN_RE = re.compile(r"^([A-Za-z0-9]{2}|[A-Za-z]{3})\s?(\d{1,4}[A-Za-z]?)$")


def split_callsign(raw: str | None) -> Optional[tuple[str, str]]:
    """('WN', '4060') / ('SWA', '4060'); None for anything not callsign-shaped
    (tail numbers, blanks, free text)."""
    if not raw:
        return None
    m = _CALLSIGN_RE.match(raw.strip())
    if not m:
        return None
    # 2026-10-04 (duel M11): ACARS/VDL2 flight fields are commonly
    # zero-padded (AA0123, UA0007) while watchlist identifiers are not
    # (AAL123, UAL7). Strip leading zeros from the numeric part so both sides
    # normalise the same way; a lone "0" stays "0".
    num = m.group(2).upper()
    digits = num.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    suffix = num[len(digits):]
    return m.group(1).upper(), (digits.lstrip("0") or "0") + suffix


def to_icao_callsign(raw: str | None) -> Optional[str]:
    """Normalise a flight id to ICAO form: WN4060 -> SWA4060, SWA4060 ->
    SWA4060. Returns None when the carrier is a 2-char code we do not know
    -- never a guess, so an unknown carrier cannot false-match."""
    parts = split_callsign(raw)
    if not parts:
        return None
    prefix, num = parts
    if len(prefix) == 3 and prefix.isalpha():
        return f"{prefix}{num}"
    icao = IATA_TO_ICAO.get(prefix)
    return f"{icao}{num}" if icao else None


def to_iata_carrier(icao_prefix: str | None) -> Optional[str]:
    """ICAO prefix -> IATA carrier (AAL -> AA); None if unknown."""
    if not icao_prefix:
        return None
    return ICAO_TO_IATA.get(icao_prefix.upper())


def same_flight(a: str | None, b: str | None) -> bool:
    """True when two flight ids denote the same flight after ICAO
    normalisation (WN4060 == SWA4060). Unknown carriers never match."""
    na, nb = to_icao_callsign(a), to_icao_callsign(b)
    return bool(na and nb and na == nb)
