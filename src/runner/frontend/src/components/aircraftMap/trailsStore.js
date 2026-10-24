// src/runner/frontend/src/components/aircraftMap/trailsStore.js -- client-side position history (2026-10-24).
//
// The runner API is a snapshot (no history), so trails are built here from successive polls: one ring
// buffer per aircraft key, ~10 min deep, dropped once the aircraft has been gone > 5 min. Segments are
// coloured by altitude with the same ramp as the markers (tar1090 style); consecutive points whose
// colour string is identical are merged into one polyline so a 10-minute trail is a handful of
// polylines, not 120 -- this runs on a phone against a Pi.
import L from 'leaflet'
import { altitudeColor } from './altitudeColor.js'

export const TRAIL_KEEP_MS   = 10 * 60_000
export const TRAIL_EXPIRE_MS =  5 * 60_000

export class TrailsStore {
  constructor() {
    this.tracks = new Map()   // key -> { pts: [{lat, lon, alt, t}], lastSeen }
  }

  /** Record one observation. Returns true when a new point was appended. */
  push(key, lat, lon, alt, t = Date.now()) {
    if (!key || !Number.isFinite(lat) || !Number.isFinite(lon)) return false
    let tr = this.tracks.get(key)
    if (!tr) { tr = { pts: [], lastSeen: t }; this.tracks.set(key, tr) }
    tr.lastSeen = t
    const last = tr.pts[tr.pts.length - 1]
    if (last && Math.abs(last.lat - lat) < 1e-5 && Math.abs(last.lon - lon) < 1e-5) return false
    tr.pts.push({ lat, lon, alt, t })
    return true
  }

  /** Drop old points and expired aircraft. Returns the keys that were removed entirely. */
  prune(now = Date.now()) {
    const removed = []
    for (const [key, tr] of this.tracks) {
      if (now - tr.lastSeen > TRAIL_EXPIRE_MS) { this.tracks.delete(key); removed.push(key); continue }
      const cutoff = now - TRAIL_KEEP_MS
      let i = 0
      while (i < tr.pts.length && tr.pts[i].t < cutoff) i++
      if (i > 0) tr.pts.splice(0, i)
    }
    return removed
  }

  has(key) { return this.tracks.has(key) }
  points(key) { return this.tracks.get(key)?.pts ?? [] }
  keys() { return [...this.tracks.keys()] }

  /** [{ color, latlngs: [[lat,lon],...] }, ...] -- consecutive same-colour runs merged. */
  segments(key) {
    const pts = this.points(key)
    if (pts.length < 2) return []
    const out = []
    let cur = null
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i]
      const color = altitudeColor(a.alt)
      if (!cur || cur.color !== color) {
        cur = { color, latlngs: [[a.lat, a.lon], [b.lat, b.lon]] }
        out.push(cur)
      } else {
        cur.latlngs.push([b.lat, b.lon])
      }
    }
    return out
  }
}

/**
 * Keeps a Leaflet layerGroup in sync with a TrailsStore for a chosen set of keys.
 * `want(key)` decides whether a key's trail is drawn (selected / tracked / all when toggled on).
 */
export class TrailsRenderer {
  constructor(store, layerGroup) {
    this.store = store
    this.group = layerGroup
    this.drawn = new Map()   // key -> L.layerGroup of polylines
  }

  /** Redraw `keys` (or all stored keys when omitted) honouring `want`. */
  sync(want, keys = null, { emphasis = () => false } = {}) {
    const list = keys ?? this.store.keys()
    for (const key of list) {
      const show = this.store.has(key) && want(key)
      if (!show) { this.remove(key); continue }
      const segs = this.store.segments(key)
      let g = this.drawn.get(key)
      if (!g) { g = L.layerGroup().addTo(this.group); this.drawn.set(key, g) }
      g.clearLayers()
      const weight = emphasis(key) ? 3 : 2
      for (const s of segs) {
        L.polyline(s.latlngs, { color: s.color, weight, opacity: 0.85, interactive: false, lineCap: 'round' }).addTo(g)
      }
    }
  }

  remove(key) {
    const g = this.drawn.get(key)
    if (g) { this.group.removeLayer(g); this.drawn.delete(key) }
  }

  clearAll() {
    for (const key of [...this.drawn.keys()]) this.remove(key)
  }
}
