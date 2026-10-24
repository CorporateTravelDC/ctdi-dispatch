// src/runner/frontend/src/components/mapKit/focus.js -- flyTo + ident matching (2026-10-24).
// Copied from aircraftMap/focus.js minus the /api/map/focus call: AIS / trains / UTM have no
// server-side "last leg" lookup yet, so an identifier that is not live on the map just reports so.
import L from 'leaflet'

export const DEFAULT_FOCUS_ZOOM = 11

export function flyTo(map, lat, lon, zoom = DEFAULT_FOCUS_ZOOM) {
  if (!map || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))) return
  const target = L.latLng(Number(lat), Number(lon))
  map.flyTo(target, Math.max(map.getZoom(), zoom), { duration: 0.6 })
}

/** Case/space-insensitive equality for identifiers (MMSI, UAS id, train number...). */
export function sameIdent(a, b) {
  const na = String(a ?? '').trim().toUpperCase(), nb = String(b ?? '').trim().toUpperCase()
  return !!na && na === nb
}
