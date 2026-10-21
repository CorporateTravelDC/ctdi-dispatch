"""common/input_gate.py -- skip a model narrative when the brief's input DATA is unchanged.

2026-10-19 (lean-out L2, F7). ep-advance (~25 min/h) and ops-brief (15-25 min/h)
held the long-runner model lane every hour with no unchanged-input check;
overnight most of their inputs do not move. Each skill now builds a key from
the stable DATA fields of its prompt sections and asks this gate whether the
last successful model narrative was made from the same key.

Operator rule (dedup never hashes timestamps, 2026-10-14): the key is built
ONLY from the data's own stable fields. Callers leave out fetch/pull stamps,
ages, counts and narrative text (CPS / route narratives); mask_stamps() strips
the volatile stamps that are embedded inside otherwise-stable lines. A change
in any data field changes the key and the model runs as before.

On a match the caller re-sends the stored narrative with a deterministic
"no change since HH:MMZ (HH:MM ET)" line. The reuse is capped at
INPUT_GATE_MAX_REUSE_S (default 6 h): after that the model runs again even if
nothing changed, so a narrative is never older than that. `--force` bypasses.
State: one small JSON file per skill in the state dir (atomic replace).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Volatile stamps embedded in data lines. Event fields (program end times,
# TFR ids, delay minutes) are NOT masked: a change there is a real change.
_STAMP_PATTERNS = [
    # METAR/SPECI issue group DDHHMMZ (the observation stamp, not the weather)
    (re.compile(r"\b\d{6}Z\b"), "<obs>"),
    # "(local-db, 12min old)" / "12 min ago" style ages
    (re.compile(r"\b\d+\s*(?:s|sec|secs|seconds|min|mins|minutes|h|hr|hrs|hours)\s+(?:old|ago)\b", re.I), "<age>"),
    # "as of <stamp>" to end of line (feed update stamps)
    (re.compile(r"\bas of [^\n]*", re.I), "as of <t>"),
]


def mask_stamps(text: str) -> str:
    out = text or ""
    for pat, rep in _STAMP_PATTERNS:
        out = pat.sub(rep, out)
    return out


def make_key(pieces: dict) -> str:
    """sha256 over the caller's stable data pieces (sorted, JSON)."""
    blob = json.dumps(pieces, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def label_utc_et(epoch: float) -> str:
    """'HH:MMZ (HH:MM ET)' -- time labels are UTC + ET, never Eastern alone."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return f"{dt:%H:%M}Z ({dt.astimezone(_ET):%H:%M} ET)"


# ── Key helpers: DC relevance and weather bands (2026-10-20) ─────────────────
# Diagnosis 2026-10-20: route-impact (every 300 s) and ep-advance (hourly)
# logged gate=new on EVERY run -- route-impact keyed every national TFR and
# NAS program (127 programs, ~33 changing per 5 min), ep-advance keyed raw
# METAR ceiling/visibility/wind and the national VIP/TFR count. Neither
# narrative is about any of that. These helpers build keys from what the
# narrative actually depends on; the brief CONTENT still shows raw values.

# Facilities that make a VIP TFR "DC-area" (the route narrative's own rule,
# operator 2026-10-14): Washington Center and Potomac TRACON.
DC_VIP_FACILITIES = frozenset({"ZDC", "KZDC", "PCT"})
# NAS programs relevant to DC ground ops: the three DC airports
# (common.gate_assignments.DC_AIRPORTS) plus Washington Center / Potomac TRACON.
DC_NAS_CENTER_FACILITIES = frozenset({"ZDC", "PCT"})


def tfr_facility(t: dict) -> str:
    try:
        return (json.loads(t.get("raw_json") or "{}").get("facility") or "").strip().upper()
    except (TypeError, ValueError, AttributeError):
        return ""


def is_dc_vip_tfr(t: dict) -> bool:
    return bool(t.get("is_vip")) and tfr_facility(t) in DC_VIP_FACILITIES


def dc_vip_tfr_ids(tfrs) -> list[str]:
    return sorted(str(t["tfr_id"]) for t in tfrs or [] if is_dc_vip_tfr(t))


def _norm_facility(fac) -> str:
    f = str(fac or "").strip().upper()
    return f[1:] if len(f) == 4 and f.startswith("K") else f


def dc_nas_key(programs) -> list[list[str]]:
    """Sorted, de-duplicated [type, facility] for DC-area NAS programs. Keys on
    the SET of program types per facility, never program ids: BWI/IAD alone
    carry 11-12 rotating miles-in-trail entries whose ids churn every cycle."""
    from common.gate_assignments import DC_AIRPORTS
    keep = set(DC_AIRPORTS) | DC_NAS_CENTER_FACILITIES
    out = {(str(p.get("type") or "").upper(), _norm_facility(p.get("facility")))
           for p in programs or []}
    return sorted([t, f] for t, f in out if f in keep)


# Flight-category thresholds (FAA AIM 7-1-7 / aviationweather.gov categories).
_CAT = (("LIFR", 500, 1.0), ("IFR", 1000, 3.0), ("MVFR", 3000, 5.0))
WIND_STRONG_KT = 20          # sustained or gust at/above: crosswind/limo-door relevant
WIND_HIGH_KT = 35            # at/above: high-wind operations
_PRECIP_TYPES = ("TS", "FZ", "RA", "SN", "DZ", "PL", "GR", "GS", "SG", "IC", "UP")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def flight_category(ceiling_ft, visibility_sm) -> str:
    c, v = _num(ceiling_ft), _num(visibility_sm)
    for name, cmax, vmax in _CAT:
        if (c is not None and c < cmax) or (v is not None and v < vmax):
            return name
    return "VFR" if (c is not None or v is not None) else "UNKNOWN"


def _band(value, edges) -> str:
    x = _num(value)
    if x is None:
        return "none"
    for e in edges:
        if x < e:
            return f"<{e:g}"
    return f">={edges[-1]:g}"


def wind_band(wind_kt, gust_kt=None) -> str:
    w = max([x for x in (_num(wind_kt), _num(gust_kt)) if x is not None], default=None)
    if w is None:
        return "unknown"
    return "high" if w >= WIND_HIGH_KT else "strong" if w >= WIND_STRONG_KT else "normal"


def precip_types(code) -> list[str]:
    """Precip TYPE only (thunder, freezing, rain, snow, ...): intensity (+/-)
    and showery/vicinity descriptors are not a change of situation."""
    s = str(code or "").upper()
    return [p for p in _PRECIP_TYPES if p in s]


def weather_key(metars, stations=("KDCA", "KIAD", "KBWI")) -> list[dict]:
    """Banded per-station weather for a gate key: flight category, ceiling and
    visibility bands, wind band, precip types. A 4500 -> 5000 ft ceiling or a
    6 -> 9 kt wind is the same situation; VFR -> MVFR or rain -> thunder is not."""
    rows = {m.get("station"): m for m in metars or [] if m.get("station") in stations}
    out = []
    for st in stations:
        m = rows.get(st)
        if not m:
            out.append({"station": st, "missing": True})
            continue
        out.append({
            "station": st,
            "cat": flight_category(m.get("ceiling_ft"), m.get("visibility_sm")),
            "ceil": _band(m.get("ceiling_ft"), (500, 1000, 3000)),
            "vis": _band(m.get("visibility_sm"), (1, 3, 5)),
            "wind": wind_band(m.get("wind_kt"), m.get("gust_kt")),
            "precip": precip_types(m.get("precip_code")),
        })
    return out


class InputGate:
    def __init__(self, name: str, state_dir: str | None = None,
                 max_reuse_s: int | None = None, now=time.time):
        if state_dir is None:
            from common import config
            state_dir = config.state_dir()
        self.path = pathlib.Path(state_dir) / f"input-gate-{name}.json"
        self.max_reuse_s = int(max_reuse_s if max_reuse_s is not None
                               else os.getenv("INPUT_GATE_MAX_REUSE_S", "21600"))
        self._now = now

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def reusable(self, key: str | None) -> dict | None:
        """The stored narrative when `key` matches the last model run and it is
        younger than max_reuse_s; else None (the caller runs the model)."""
        if not key:
            return None
        st = self._load()
        if st.get("key") != key or not st.get("narrative"):
            return None
        age = self._now() - float(st.get("model_at") or 0)
        if age < 0 or age >= self.max_reuse_s:
            return None
        return st

    def record(self, key: str | None, narrative: str, concise: str) -> None:
        """Remember a SUCCESSFUL model narrative (never a deterministic fallback)."""
        if not key or not narrative:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "narrative": narrative,
                                       "concise": concise, "model_at": self._now()}))
            os.replace(tmp, self.path)
        except Exception as exc:  # the gate is an optimisation; never fail the brief
            log.warning("input-gate: could not record %s: %s", self.path.name, exc)

    @staticmethod
    def no_change_note(stored: dict) -> str:
        return (f"[no change in input data since {label_utc_et(float(stored['model_at']))}: "
                f"model narrative from that run, not regenerated]")
