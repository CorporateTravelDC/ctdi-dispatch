"""
airspace.py — /api/v1/airspace routes.

Returns static DC-area airspace as GeoJSON. Used by the Leaflet map UI.
Data is built once at module import (no DB, no polling).
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from geo.dc_airspace import FEATURE_IDS, get_feature_by_id, get_static_airspace

router = APIRouter(prefix="/api/v1/airspace", tags=["airspace"])


@router.get("", summary="All static DC airspace (GeoJSON FeatureCollection)")
async def get_airspace() -> JSONResponse:
    """
    Returns a GeoJSON FeatureCollection with:
      - DC_SFRA   — Special Flight Rules Area (30 NM ring)
      - DC_FRZ    — Flight Restriction Zone (15 NM ring)
      - P-56A     — Capitol Building prohibited area
      - P-56B     — White House prohibited area

    Ordered outer-to-inner so Leaflet renders smaller polygons on top.
    Each feature includes display hints (color, fillOpacity, weight, dashArray)
    in `properties.display` for the PWA map layer.
    """
    return JSONResponse(content=get_static_airspace())


@router.get(
    "/{feature_id}",
    summary="Single airspace feature by ID",
)
async def get_airspace_feature(feature_id: str) -> JSONResponse:
    """
    Returns a single GeoJSON Feature.

    Valid IDs: DC_SFRA, DC_FRZ, P-56A, P-56B
    """
    feature = get_feature_by_id(feature_id)
    if feature is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"Unknown airspace feature '{feature_id}'. "
                   f"Valid IDs: {', '.join(FEATURE_IDS)}",
        )
    return JSONResponse(content=feature)
