"""TFR area geometry (2026-10-18, operator: TFR widening + ACARS TFR flags).

The FAA TFR list feed carries no geometry; the per-NOTAM detail record does
(poller/fetchers/tfr.py fetches it for VIP and SECURITY TFRs, once per FAA mod
time). Each TFRAreaGroup holds:

  abdMergedArea/Avx      the merged outline, one geoLat/geoLong vertex per Avx
                         ("26.69365945N", "080.03777778W"); a circle comes
                         through as its ~36-72 point polygon approximation
  aseShapes/Abd/Avx      the defining shapes: codeType CIR (centre +
                         valRadiusArc/uomRadiusArc), GRC (great-circle vertex),
                         CCA/CWA (arc) ...

parse_areas() reduces each group to {"name", "lat", "lon", "radius_nm",
"polygon"} -- centre and radius always when there is any coordinate, the
polygon only when the merged outline has 3+ vertices -- and the poller stores
the list in tfrs.raw_json["areas_geo"]. Some records (e.g. text-only
nationwide FDC security NOTAMs) carry no coordinates at all: [] and every
consumer falls back to text.

Pure stdlib, no I/O: imported by the poller (store), the ingest ACARS writer
(position-in-TFR flag) and tests.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

EARTH_RADIUS_NM = 3440.065

_COORD_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([NSEW])\s*$", re.IGNORECASE)

# uomRadiusArc -> nm
_UOM_TO_NM = {"NM": 1.0, "KM": 1 / 1.852, "M": 1 / 1852.0, "FT": 1 / 6076.115, "SM": 0.868976}


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return EARTH_RADIUS_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_coord(text: str | None) -> float | None:
    """'26.69365945N' -> 26.69365945, '080.03777778W' -> -80.03777778."""
    m = _COORD_RE.match(text or "")
    if not m:
        return None
    v = float(m.group(1))
    return -v if m.group(2).upper() in ("S", "W") else v


def _avx_point(avx: ET.Element) -> tuple[float, float] | None:
    lat, lon = parse_coord(avx.findtext("geoLat")), parse_coord(avx.findtext("geoLong"))
    if lat is None or lon is None:
        return None
    return lat, lon


def _area_from_group(g: ET.Element) -> dict | None:
    name = (g.findtext("aseTFRArea/txtName") or "").strip() or None
    merged = g.find("abdMergedArea")
    poly = []
    if merged is not None:
        for avx in merged.findall("Avx"):
            p = _avx_point(avx)
            if p:
                poly.append(p)
    circles, shape_pts = [], []
    shapes = g.find("aseShapes")
    if shapes is not None:
        for avx in shapes.iter("Avx"):
            p = _avx_point(avx)
            if not p:
                continue
            shape_pts.append(p)
            if (avx.findtext("codeType") or "").strip().upper() == "CIR":
                try:
                    r = float(avx.findtext("valRadiusArc") or "")
                except ValueError:
                    continue
                uom = (avx.findtext("uomRadiusArc") or "NM").strip().upper()
                circles.append((p, r * _UOM_TO_NM.get(uom, 1.0)))
    if len(poly) >= 3:
        # Vertex mean: TFR areas are a few nm to ~30 nm across, where the mean of
        # the outline is within a fraction of a mile of the true centroid.
        lat = sum(p[0] for p in poly) / len(poly)
        lon = sum(p[1] for p in poly) / len(poly)
        radius = max(haversine_nm(lat, lon, a, b) for a, b in poly)
    elif circles:
        (lat, lon), radius = circles[0]
    elif shape_pts or poly:
        pts = shape_pts or poly
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        radius = None
    else:
        return None
    out = {"name": name, "lat": round(lat, 5), "lon": round(lon, 5),
           "radius_nm": round(radius, 2) if radius is not None else None}
    if len(poly) >= 3:
        out["polygon"] = [[round(a, 5), round(b, 5)] for a, b in poly]
    return out


def parse_areas(root: ET.Element) -> list[dict]:
    """Every TFRAreaGroup with any usable coordinate, in document order."""
    out = []
    for g in root.iter("TFRAreaGroup"):
        a = _area_from_group(g)
        if a:
            out.append(a)
    return out


def _in_polygon(lat: float, lon: float, poly: list) -> bool:
    """Ray casting on lat/lon; fine at TFR sizes (no antimeridian TFRs here)."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i][0], poly[i][1]
        yj, xj = poly[j][0], poly[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = xi + (lat - yi) * (xj - xi) / ((yj - yi) or 1e-12)
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_area(lat: float, lon: float, area: dict, buffer_nm: float = 0.0) -> bool:
    """Lateral test only (TFR floors/ceilings are not checked: an ACARS position
    report rarely carries a usable altitude). With buffer_nm > 0 the test is
    'within buffer_nm of the area', using the centre + radius envelope."""
    try:
        clat, clon = float(area["lat"]), float(area["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    radius = area.get("radius_nm")
    if buffer_nm > 0:
        if radius is None:
            return False
        return haversine_nm(lat, lon, clat, clon) <= float(radius) + buffer_nm
    poly = area.get("polygon")
    if poly and len(poly) >= 3:
        return _in_polygon(lat, lon, poly)
    if radius is None:
        return False
    return haversine_nm(lat, lon, clat, clon) <= float(radius)


def point_in_any(lat: float, lon: float, areas: list[dict], buffer_nm: float = 0.0) -> bool:
    return any(point_in_area(lat, lon, a, buffer_nm) for a in areas or [])
