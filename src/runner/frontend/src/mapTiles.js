// src/runner/frontend/src/mapTiles.js -- one place for every Leaflet basemap layer (2026-10-23).
//
// OpenStreetMap's tile servers have no API key; their usage policy requires a Referer, and a
// request without one is answered HTTP 200 with an "Access blocked" placeholder tile
// (osm.wiki/Blocked). The public runner domain sends `Referrer-Policy: no-referrer` from nginx
// (kept on purpose for everything else), so every map there showed the blocked tile while the
// tailnet port, which sends no policy header, worked. Each tile layer now asks for
// `strict-origin`: OSM sees only the site origin, never a page path or query.
//
// Also: the {s}.tile.openstreetmap.org subdomains are deprecated; tile.openstreetmap.org is the
// one host. Attribution is required by the policy and stays on every layer.
export const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
export const OSM_ATTRIBUTION = '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors'

// Options every third-party tile layer gets (Leaflet >= 1.9 sets referrerpolicy on each tile <img>).
export const TILE_PRIVACY = { referrerPolicy: 'strict-origin' }

export function osmTileOptions(extra = {}) {
  return { attribution: OSM_ATTRIBUTION, maxZoom: 19, ...TILE_PRIVACY, ...extra }
}
