"""
dc_airspace.py — Static DC-area airspace GeoJSON definitions.

Drawn once at module import; never polled. These boundaries are fixed
by FAA regulation and only change via NOTAM or permanent rulemaking.

Sources:
  - FAR 91.161 (DC SFRA / FRZ boundaries)
  - FAAO 7400.11 (P-56A, P-56B)

DO NOT include TFRs here — those are polled via the TFR feed.
"""

import math
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────
# DCA (Ronald Reagan Washington National) — centre of SFRA/FRZ
DCA_LAT = 38.8521
DCA_LON = -77.0377

NM_TO_DEG_LAT = 1 / 60.0  # 1 NM ≈ 1/60 degree latitude


def _nm_to_deg_lon(lat_deg: float) -> float:
    """Convert nautical miles to degrees longitude at a given latitude."""
    return 1 / (60.0 * math.cos(math.radians(lat_deg)))


def _circle_coords(
    center_lat: float,
    center_lon: float,
    radius_nm: float,
    n_points: int = 64,
) -> list[list[float]]:
    """Generate polygon coordinates for a circle of radius_nm centred at (lat, lon)."""
    coords = []
    for i in range(n_points + 1):
        angle = math.radians(360 * i / n_points)
        dlat = radius_nm * NM_TO_DEG_LAT * math.cos(angle)
        dlon = radius_nm * _nm_to_deg_lon(center_lat) * math.sin(angle)
        coords.append([center_lon + dlon, center_lat + dlat])
    return coords


# ── Static airspace features ─────────────────────────────────────────────────

def _frz() -> dict[str, Any]:
    """DC Flight Restriction Zone — 15 NM radius centred on DCA, SFC–18,000 ft."""
    return {
        "type": "Feature",
        "id": "DC_FRZ",
        "properties": {
            "id": "DC_FRZ",
            "name": "DC Flight Restriction Zone",
            "short": "FRZ",
            "class": "frz",
            "radius_nm": 15,
            "center_lat": DCA_LAT,
            "center_lon": DCA_LON,
            "floor_ft": 0,
            "ceiling_ft": 18000,
            "description": (
                "No flight permitted without ATC clearance and TSA waiver. "
                "Applies SFC–18,000 ft MSL within 15 NM of DCA."
            ),
            "reference": "FAR 91.161(b)",
            "display": {
                "color": "#cc0000",
                "fillOpacity": 0.08,
                "weight": 2,
                "dashArray": None,
            },
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [_circle_coords(DCA_LAT, DCA_LON, 15)],
        },
    }


def _sfra() -> dict[str, Any]:
    """DC Special Flight Rules Area — 30 NM radius, SFC–18,000 ft."""
    return {
        "type": "Feature",
        "id": "DC_SFRA",
        "properties": {
            "id": "DC_SFRA",
            "name": "DC Special Flight Rules Area",
            "short": "SFRA",
            "class": "sfra",
            "radius_nm": 30,
            "center_lat": DCA_LAT,
            "center_lon": DCA_LON,
            "floor_ft": 0,
            "ceiling_ft": 18000,
            "description": (
                "Flight plan required, transponder squawk, ATC contact. "
                "No flight without authorization within 30 NM of DCA."
            ),
            "reference": "FAR 91.161(a)",
            "display": {
                "color": "#ff6600",
                "fillOpacity": 0.05,
                "weight": 1.5,
                "dashArray": "8 4",
            },
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [_circle_coords(DCA_LAT, DCA_LON, 30)],
        },
    }


def _p56a() -> dict[str, Any]:
    """P-56A — Capitol Building restricted area, SFC–unlimited."""
    # Approximate octagon centred on Capitol (38.8899°N, 77.0091°W), ~1 NM radius
    cap_lat, cap_lon = 38.8899, -77.0091
    return {
        "type": "Feature",
        "id": "P-56A",
        "properties": {
            "id": "P-56A",
            "name": "Prohibited Area P-56A",
            "short": "P-56A",
            "class": "prohibited",
            "center_lat": cap_lat,
            "center_lon": cap_lon,
            "floor_ft": 0,
            "ceiling_ft": 99999,
            "description": (
                "Prohibited airspace over the Capitol Building. "
                "No flight permitted at any altitude. Violations may result in "
                "use of deadly force."
            ),
            "reference": "FAR 73 / FAAO 7400.11",
            "display": {
                "color": "#990099",
                "fillOpacity": 0.20,
                "weight": 2.5,
                "dashArray": None,
            },
        },
        "geometry": {
            "type": "Polygon",
            # Approximate circle ~1 NM radius
            "coordinates": [_circle_coords(cap_lat, cap_lon, 1.0, n_points=32)],
        },
    }


def _p56b() -> dict[str, Any]:
    """P-56B — White House restricted area, SFC–unlimited."""
    wh_lat, wh_lon = 38.8977, -77.0366
    return {
        "type": "Feature",
        "id": "P-56B",
        "properties": {
            "id": "P-56B",
            "name": "Prohibited Area P-56B",
            "short": "P-56B",
            "class": "prohibited",
            "center_lat": wh_lat,
            "center_lon": wh_lon,
            "floor_ft": 0,
            "ceiling_ft": 99999,
            "description": (
                "Prohibited airspace over the White House complex. "
                "No flight permitted at any altitude."
            ),
            "reference": "FAR 73 / FAAO 7400.11",
            "display": {
                "color": "#990099",
                "fillOpacity": 0.20,
                "weight": 2.5,
                "dashArray": None,
            },
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [_circle_coords(wh_lat, wh_lon, 1.0, n_points=32)],
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

# Singleton — built once at import time
_STATIC_AIRSPACE: dict[str, Any] | None = None


def get_static_airspace() -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of all static DC airspace features.

    Ordered outer → inner so Leaflet renders smaller polygons on top.
    """
    global _STATIC_AIRSPACE
    if _STATIC_AIRSPACE is None:
        _STATIC_AIRSPACE = {
            "type": "FeatureCollection",
            "features": [
                _sfra(),    # 30 NM — largest, render first (bottom)
                _frz(),     # 15 NM
                _p56a(),    # ~1 NM
                _p56b(),    # ~1 NM
            ],
        }
    return _STATIC_AIRSPACE


def get_feature_by_id(feature_id: str) -> dict[str, Any] | None:
    """Return a single airspace feature by ID, or None if not found."""
    fc = get_static_airspace()
    for f in fc["features"]:
        if f["id"] == feature_id:
            return f
    return None


FEATURE_IDS = ["DC_SFRA", "DC_FRZ", "P-56A", "P-56B"]
