"""ACARS message-type flags (2026-10-18, operator: "build in filtering flags for
specific types and tie those to OOOIs as well as TFRs").

ONE FILE, TWO COPIES: src/common/acars_flags.py (ingest writer, runner API) and
src/acars_watcher/acars_flags.py (the acars-watcher image, whose build context is
src/acars_watcher alone). They must stay byte-identical;
tests/acars_watcher/test_acars_flags.py fails when they differ. Pure stdlib, no
I/O, no logging, no imports from the platform -- nothing here can write a phase.

classify(msg) -> sorted list of flags from FLAGS, deterministic: the same message
always gets the same flags, from its label, sublabel, direction and text shape
only. A flag is a FILTER and EVENT-DETAIL label, never an assertion:

  - "oooi" marks a message SHAPED like an OOOI report. It does not set a phase.
    Phases go through db.update_watchlist_oooi_phase_authoritative() with a named
    source, on the existing gated paths only (poller ACARS confirmation via
    common.acars.get_latest_phase); SWIM asserts, ADS-B never asserts ON/IN.
  - "tfr_notam_mention" / "tfr_area" need TFR context the message does not carry:
    the caller passes in_tfr / near_tfr callables (common.tfr_geo over the stored
    tfrs.raw_json["areas_geo"]). Without them only the text rule applies.

Message shapes read (extract_fields):
  dumpvdl2   {"vdl2": {"avlc": {"src": {"type": "Aircraft"}, "acars": {"reg": ".Q1ZT7",
             "flight": ..., "label": ..., "sublabel": ..., "blk_id": ..., "msg_text": ...}}}}
  acarsdec   top-level tail / flight / label / sublabel / block_id / text
  airframes  tail / flightNumber / label / text / linkDirection / latitude / longitude
  Jumpseat   registration / flightNumber / label / cleanedText|text / direction
  ingest     local_airspace._normalize_acars output (tail, flight, label, text, direction)
"""
from __future__ import annotations

import re

FLAGS = (
    "oooi", "eta", "gate_assign", "gate_request", "baggage", "weather", "atis",
    "cpdlc_adsc", "maintenance", "position_report", "free_text",
    "tfr_notam_mention", "diversion", "tfr_area",
)
FLAG_SET = frozenset(FLAGS)

# Labels that carry no operational content (link tests, squitters, "no data").
_NO_CONTENT_LABELS = frozenset({"_D", "_\x7f", "SQ", "Q0"})

# OOOI: ARINC 620 QP/QQ/QR/QS (OUT/OFF/ON/IN) plus QM-QO as the ingest label table
# has carried them since 2026-08; label 31/H1 with the /OUT /OFF /ON /IN fields and
# the Boeing WOW block ("OFF OFF", "ON ON"), the same shapes as common.acars.
_OOOI_LABELS = frozenset({"QM", "QN", "QO", "QP", "QQ", "QR", "QS"})
_OOOI_TEXT_LABELS = frozenset({"31", "H1"})
_OOOI_TEXT_RE = re.compile(r"/(?:OUT|OFF|ON|IN)\s|\bOFF OFF\b|\bON ON\b")

_CPDLC_ADSC_LABELS = frozenset({"AA", "BA", "A6", "B6", "A0", "B0", "AB"})
_CPDLC_ADSC_RE = re.compile(r"\bCPDLC\b|\bADS-?C\b|\bFANS\b|^/?[A-Z]{3,7}\.(?:AT1|CR1|CC1|DR1|ADS)\.")
_ATIS_LABELS = frozenset({"A9", "B9"})
_ATIS_RE = re.compile(r"\bD-?ATIS\b|\bATIS\b")
_POSITION_LABELS = frozenset({"15", "16", "20", "22", "26", "B6"})
_ETA_RE = re.compile(r"\bETA\b|\bEST(?:IMATED)?\s+ARR")
_GATE_RE = re.compile(r"\bGATE\b|\bGTE\b|\bSTAND\b")
_GATE_REQ_RE = re.compile(r"\bREQ(?:UEST)?\b|\?")
_BAGGAGE_RE = re.compile(r"\bBAG(?:S|GAGE)?\b|\bCAROUSEL\b|\bBAG\s*BELT\b|\bBAG\s*CLAIM\b")
_WEATHER_RE = re.compile(r"\bMETAR\b|\bTAF\b|\bSIGMET\b|\bAIRMET\b|\bPIREP\b|\bWX\b|\bTURB(?:ULENCE)?\b"
                         r"|\bCONVECTIVE\b|\bTSTMS?\b|\bWIND\s*SHEAR\b|\bICING\b")
_MAINT_RE = re.compile(r"\bMAINT(?:ENANCE)?\b|\bFAULT\b|\bMEL\b|\bECAM\b|\bEICAS\b|\bCFDS\b|\bMX\b"
                       r"|\bLOGBOOK\b|\bINOP\b")
_MAINT_SUBLABEL_RE = re.compile(r"^#?(?:CF|DF|EC|EI|MD)")
_POS_WORD_RE = re.compile(r"\bPOS(?:ITION)?\b|#M\dBPOS|^POS")
_TFR_TEXT_RE = re.compile(r"\bTFR\b|\bNOTAM\b|\bRESTRICTED\s+(?:AREA|AIRSPACE)\b|\bPROHIBITED\s+AREA\b"
                          r"|\bSFRA\b|\bFRZ\b|\bP-?56\b|\bSECURITY\s+ZONE\b")
_DIVERT_RE = re.compile(r"\bDIVERT(?:ING|ED)?\b|\bDVRT\b|\bDIVERSION\b|\bRTB\b|\bRETURNING\s+TO\b")
_HOLD_RE = re.compile(r"\bHOLD(?:ING)?\b")

# Positions: ARINC ddmm[m] ("N38512W077123" = N38 51.2 W077 12.3) and decimal
# ("N 38.512, W 77.123"). Validated for range; the first match wins.
_POS_DDMM_RE = re.compile(r"(?<!\d)([NS])\s?(\d{4,5})\s?,?\s?([EW])\s?(\d{5,6})(?!\d)")
_POS_DEC_RE = re.compile(r"(?<!\d)([NS])\s?(\d{1,2}\.\d+)\s*,?\s*([EW])\s?(\d{1,3}\.\d+)")


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def extract_fields(msg: dict) -> dict:
    """Normalized {tail, flight, label, sublabel, text, direction, block_id, lat, lon}
    from any supported shape (module docstring). Registration pad dots are stripped;
    direction is 'uplink' | 'downlink' | ''. Never raises on odd input."""
    if not isinstance(msg, dict):
        msg = {}
    nested = {}
    src_type = ""
    v = msg.get("vdl2")
    if isinstance(v, dict):
        avlc = v.get("avlc") if isinstance(v.get("avlc"), dict) else {}
        nested = avlc.get("acars") if isinstance(avlc.get("acars"), dict) else {}
        src = avlc.get("src") if isinstance(avlc.get("src"), dict) else {}
        src_type = _s(src.get("type")).lower()
    acars = msg.get("acars") if isinstance(msg.get("acars"), dict) else {}

    def first(*vals):
        for x in vals:
            if isinstance(x, (str, int, float)) and str(x).strip():
                return str(x).strip()
        return ""

    tail = first(nested.get("reg"), msg.get("tail"), msg.get("registration"), msg.get("reg"),
                 msg.get("aircraft_reg"), acars.get("reg"), acars.get("tail")).lstrip(".")
    flight_raw = msg.get("flight")
    if isinstance(flight_raw, dict):     # airframes.io enrichment object
        flight_raw = flight_raw.get("flight") or flight_raw.get("flightIata") or flight_raw.get("flightIcao")
    flight = first(nested.get("flight"), flight_raw, msg.get("flightNumber"), msg.get("callsign"),
                   acars.get("flight"))
    label = first(nested.get("label"), msg.get("label"), acars.get("label")).upper()
    sublabel = first(nested.get("sublabel"), msg.get("sublabel"), acars.get("sublabel")).upper()
    text = first(nested.get("msg_text"), msg.get("cleanedText"), msg.get("text"),
                 msg.get("msg_text"), acars.get("msg_text"), msg.get("message"), msg.get("data"))
    block_id = first(nested.get("blk_id"), msg.get("block_id"), msg.get("blk_id"), acars.get("blk_id"))

    d = first(msg.get("direction"), msg.get("linkDirection"), msg.get("directionLabel")).lower()
    if d in ("uplink", "up", "ul", "ground-to-air", "to aircraft"):
        direction = "uplink"
    elif d in ("downlink", "down", "dl", "air-to-ground", "from aircraft"):
        direction = "downlink"
    elif src_type == "aircraft":
        direction = "downlink"
    elif src_type in ("ground station", "ground"):
        direction = "uplink"
    elif len(block_id) == 1 and block_id.isdigit():
        direction = "downlink"      # ARINC 618: downlink block ids are 0-9 ...
    elif len(block_id) == 1 and block_id.isalpha():
        direction = "uplink"        # ... uplink block ids are letters
    else:
        direction = ""

    lat = lon = None
    for k_lat, k_lon in (("lat", "lon"), ("latitude", "longitude")):
        try:
            if msg.get(k_lat) is not None and msg.get(k_lon) is not None:
                lat, lon = float(msg[k_lat]), float(msg[k_lon])
                break
        except (TypeError, ValueError):
            lat = lon = None
    if lat is None:
        lat, lon = parse_position(text)
    return {"tail": tail, "flight": flight, "label": label, "sublabel": sublabel, "text": text,
            "direction": direction, "block_id": block_id, "lat": lat, "lon": lon}


def parse_position(text: str) -> tuple[float | None, float | None]:
    """(lat, lon) from an ACARS position text, or (None, None)."""
    t = (text or "").upper()
    m = _POS_DEC_RE.search(t)
    if m:
        lat, lon = float(m.group(2)), float(m.group(4))
    else:
        m = _POS_DDMM_RE.search(t)
        if not m:
            return None, None
        la, lo = m.group(2), m.group(4)
        lat_min = float(la[2:4] + ("." + la[4:] if len(la) > 4 else ""))
        lon_min = float(lo[3:5] + ("." + lo[5:] if len(lo) > 5 else ""))
        if lat_min >= 60 or lon_min >= 60:
            return None, None
        lat, lon = int(la[:2]) + lat_min / 60, int(lo[:3]) + lon_min / 60
    if lat > 90 or lon > 180:
        return None, None
    if m.group(1) == "S":
        lat = -lat
    if m.group(3) == "W":
        lon = -lon
    return round(lat, 4), round(lon, 4)


def classify(msg: dict, in_tfr=None, near_tfr=None) -> list[str]:
    """Flags for one message (sorted). in_tfr(lat, lon) / near_tfr(lat, lon) are
    optional callables answering 'inside an active TFR area' / 'near one'."""
    f = extract_fields(msg)
    label, sub, direction = f["label"], f["sublabel"], f["direction"]
    text = f["text"].upper()
    if label in _NO_CONTENT_LABELS or (not text and label not in _OOOI_LABELS):
        return []
    flags: set[str] = set()
    if label in _OOOI_LABELS or (label in _OOOI_TEXT_LABELS and _OOOI_TEXT_RE.search(text)):
        flags.add("oooi")
    if _ETA_RE.search(text):
        flags.add("eta")
    if _GATE_RE.search(text):
        if direction == "downlink" and _GATE_REQ_RE.search(text):
            flags.add("gate_request")
        elif direction != "downlink":
            flags.add("gate_assign")
    if _BAGGAGE_RE.search(text):
        flags.add("baggage")
    if label in _ATIS_LABELS or _ATIS_RE.search(text):
        flags.add("atis")
    elif _WEATHER_RE.search(text):
        flags.add("weather")
    if label in _CPDLC_ADSC_LABELS or _CPDLC_ADSC_RE.search(text):
        flags.add("cpdlc_adsc")
    # H1 sublabels (#CF, #DF, #EC, #EI, #MD: fault/maintenance downlinks), from the
    # sublabel field or a "#.." text prefix -- never a bare word ("MDW", "ECHO").
    if _MAINT_RE.search(text) or (sub and _MAINT_SUBLABEL_RE.match(sub)) or \
            (text.startswith("#") and _MAINT_SUBLABEL_RE.match(text)):
        flags.add("maintenance")
    has_pos = f["lat"] is not None and f["lon"] is not None
    if has_pos and (label in _POSITION_LABELS or _POS_WORD_RE.search(text)):
        flags.add("position_report")
    if _DIVERT_RE.search(text):
        flags.add("diversion")
    if _TFR_TEXT_RE.search(text):
        flags.add("tfr_notam_mention")
    elif (_DIVERT_RE.search(text) or _HOLD_RE.search(text)) and has_pos and near_tfr is not None:
        try:
            if near_tfr(f["lat"], f["lon"]):
                flags.add("tfr_notam_mention")
        except Exception:
            pass
    if has_pos and in_tfr is not None:
        try:
            if in_tfr(f["lat"], f["lon"]):
                flags.add("tfr_area")
        except Exception:
            pass
    if not flags:
        flags.add("free_text")
    return sorted(flags)


def to_column(flags: list[str]) -> str | None:
    """acars_messages.acars_flags value: 'eta,oooi' (sorted, comma-joined) or None."""
    return ",".join(sorted(set(flags))) if flags else None


def parse_filter(value: str | None) -> list[str]:
    """'oooi, eta' -> ['eta', 'oooi']; raises ValueError naming any unknown flag."""
    want = sorted({p.strip().lower() for p in (value or "").split(",") if p.strip()})
    bad = [w for w in want if w not in FLAG_SET]
    if bad:
        raise ValueError(f"unknown ACARS flag(s): {', '.join(bad)}; known: {', '.join(FLAGS)}")
    return want
