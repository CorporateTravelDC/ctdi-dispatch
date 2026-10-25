// src/runner/frontend/src/components/aircraftMap/focus.js -- click-to-focus + /api/map/focus (2026-10-24).
//
// Focus = fly the map to one aircraft, open its tooltip, highlight it and draw its current leg
// (origin -> position -> destination) when coordinates are known. The list panel, the watchlist badge
// chips, the search box and the markers all funnel through the same `focusIdent` in AircraftMap.jsx;
// this module holds the stateless pieces so that component stays readable.
import L from 'leaflet'

export const FOCUS_ZOOM = 10

/** GET /api/map/focus?ident=<hex|flight|registration|serial>. 2026-10-24: the backend normalises an IATA
 *  flight number (UA919 -> UAL919), follows a codeshare to the operating callsign, and resolves a tail or
 *  serial (with or without dash) to its hex; when receiver + SWIM have nothing live it may answer with an
 *  airplanes.live fix (aircraft.src === 'aggregator', aircraft.aggregator = 'airplanes.live'). Resolves to:
 *  { ok: true,  live: true,  aircraft }      -- live anywhere in scope (same shape as /api/map/aircraft)
 *  { ok: true,  live: false, last }          -- most recent SWIM leg
 *  { ok: false, status }                     -- 404 / network / other
 */
export async function fetchFocus(ident) {
  const q = String(ident || '').trim()
  if (!q) return { ok: false, status: 0 }
  try {
    const r = await fetch(`/api/map/focus?ident=${encodeURIComponent(q)}`, { cache: 'no-store' })
    if (!r.ok) return { ok: false, status: r.status }
    const data = await r.json()
    if (data && data.live === false) return { ok: true, live: false, last: normaliseLast(data.last || data) }
    // Contract: the live case is the aircraft object itself; tolerate a {live:true, aircraft:{...}} wrapper too.
    const ac = data?.aircraft && typeof data.aircraft === 'object' && !Array.isArray(data.aircraft) ? data.aircraft : data
    if (!ac || !Number.isFinite(Number(ac.lat)) || !Number.isFinite(Number(ac.lon))) return { ok: false, status: 204 }
    return { ok: true, live: true, aircraft: ac }
  } catch {
    return { ok: false, status: -1 }
  }
}

/** Normalise a last-leg row to the /api/map/aircraft field names so the same marker/tooltip code can
 *  draw it. The spec leaves the `last` shape to the backend ("position, origin, destination, origin/
 *  destination coordinates if available, status"), so several spellings are accepted here. */
export function normaliseLast(raw) {
  if (!raw || typeof raw !== 'object') return null
  const pick = (...ks) => { for (const k of ks) { if (raw[k] != null && raw[k] !== '') return raw[k] } return null }
  const lat = Number(pick('lat', 'position_lat', 'last_lat'))
  const lon = Number(pick('lon', 'position_lon', 'last_lon'))
  const updated = Number(pick('updated_at', 'last_seen', 'timestamp'))
  let pos_age_s = Number(pick('pos_age_s', 'age_s'))
  if (!Number.isFinite(pos_age_s) && Number.isFinite(updated)) {
    pos_age_s = Date.now() / 1000 - (updated > 1e11 ? updated / 1000 : updated)
  }
  return {
    ...raw,
    hex: (pick('hex') || '').toString().toLowerCase() || null,
    flight: pick('flight', 'callsign'),
    r: pick('r', 'registration'),
    t: pick('t', 'aircraft_type'),
    lat: Number.isFinite(lat) ? lat : null,
    lon: Number.isFinite(lon) ? lon : null,
    alt_baro: pick('alt_baro', 'altitude_ft'),
    gs: pick('gs', 'ground_speed_kt'),
    track: pick('track', 'track_deg'),
    origin: pick('origin'),
    destination: pick('destination'),
    status: pick('status'),
    pos_age_s: Number.isFinite(pos_age_s) ? pos_age_s : null,
    src: 'swim',
    live: false,
  }
}

/** [lat, lon] for the origin/destination of an aircraft or last-leg row, from any of the accepted
 *  spellings, falling back to an airport lookup table (code -> [lat, lon]) when only the code is known. */
export function legEndpoints(ac, airportCoords = null) {
  if (!ac) return { origin: null, destination: null }
  const coord = (prefix, code) => {
    const a = ac[`${prefix}_coords`] || ac[`${prefix}_latlon`]
    if (Array.isArray(a) && a.length === 2 && a.every(v => Number.isFinite(Number(v)))) return [Number(a[0]), Number(a[1])]
    const la = Number(ac[`${prefix}_lat`]), lo = Number(ac[`${prefix}_lon`])
    if (Number.isFinite(la) && Number.isFinite(lo)) return [la, lo]
    const obj = ac.leg?.[prefix]
    if (obj && Number.isFinite(Number(obj.lat)) && Number.isFinite(Number(obj.lon))) return [Number(obj.lat), Number(obj.lon)]
    if (code && airportCoords) {
      const hit = airportCoords.get(String(code).toUpperCase())
      if (hit) return hit
    }
    return null
  }
  return { origin: coord('origin', ac.origin), destination: coord('destination', ac.destination) }
}

/** Build a code -> [lat, lon] map from /api/map/scope's swim.airports when the entries carry coordinates
 *  (plain code strings are accepted and simply yield no coordinates). */
export function airportCoordTable(airports) {
  const m = new Map()
  if (!Array.isArray(airports)) return m
  for (const a of airports) {
    if (!a || typeof a !== 'object') continue
    const code = a.icao || a.code || a.id || a.ident
    const lat = Number(a.lat ?? a.latitude), lon = Number(a.lon ?? a.lng ?? a.longitude)
    if (code && Number.isFinite(lat) && Number.isFinite(lon)) m.set(String(code).toUpperCase(), [lat, lon])
  }
  return m
}

/** Draw origin -> position (solid) and position -> destination (dashed) into `group`. */
export function drawLeg(group, ac, airportCoords, { muted = false } = {}) {
  group.clearLayers()
  if (!ac || !Number.isFinite(Number(ac.lat))) return false
  const here = [Number(ac.lat), Number(ac.lon)]
  const { origin, destination } = legEndpoints(ac, airportCoords)
  const color = muted ? '#8a9bb0' : '#00d4ff'
  let drew = false
  if (origin) {
    L.polyline([origin, here], { color, weight: 2, opacity: 0.75, interactive: false }).addTo(group)
    L.circleMarker(origin, { radius: 4, color, weight: 1.5, fill: true, fillOpacity: 0.9, interactive: false })
      .addTo(group).bindTooltip(String(ac.origin || 'origin'), { className: 'airport-label', permanent: true, direction: 'top' })
    drew = true
  }
  if (destination) {
    L.polyline([here, destination], { color, weight: 2, opacity: 0.75, dashArray: '6 6', interactive: false }).addTo(group)
    L.circleMarker(destination, { radius: 4, color, weight: 1.5, fill: false, interactive: false })
      .addTo(group).bindTooltip(String(ac.destination || 'destination'), { className: 'airport-label', permanent: true, direction: 'top' })
    drew = true
  }
  return drew
}

export function flyTo(map, lat, lon) {
  if (!map || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))) return
  const target = L.latLng(Number(lat), Number(lon))
  const zoom = Math.max(map.getZoom(), FOCUS_ZOOM)
  map.flyTo(target, zoom, { duration: 0.6 })
}

/** Does this aircraft answer to `ident` (hex or callsign, case-insensitive)? */
export function matchesIdent(ac, ident) {
  const q = String(ident || '').trim().toUpperCase()
  if (!q || !ac) return false
  if ((ac.hex || '').toUpperCase() === q) return true
  if ((ac.flight || '').trim().toUpperCase() === q) return true
  if ((ac.r || '').trim().toUpperCase().replace(/-/g, '') === q.replace(/-/g, '')) return true
  return false
}
