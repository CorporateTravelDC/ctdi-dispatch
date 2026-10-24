// src/runner/frontend/src/components/mapKit/trails.js -- generic client-side position history (2026-10-24).
//
// Extracted copy of aircraftMap/trailsStore.js with the aviation specifics made parameters: every
// vertical's API is a snapshot (no history), so trails are built from successive polls. Differences
// from the aviation store: the keep/expire windows are per instance (a vessel at 8 kt polled every
// 60 s needs an hour of history to show anything; a train position is refreshed by dispatch every
// ~5 min), each point carries an opaque value `v` (speed, altitude, delay...) and the renderer takes
// the colour function, so segment colouring stays the vertical's decision. The store also remembers
// when a key last MOVED (not just when it was last seen) -- AIS/UTM payloads carry no timestamp, so
// "position age" on those maps is the age of the last position change seen by this client.
import L from 'leaflet'

const toRad = d => d * Math.PI / 180
const toDeg = r => r * 180 / Math.PI

/** Initial bearing (deg, 0..360) from point 1 to point 2. */
export function bearing(lat1, lon1, lat2, lon2) {
  const p1 = toRad(lat1), p2 = toRad(lat2), dl = toRad(lon2 - lon1)
  const y = Math.sin(dl) * Math.cos(p2)
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)
  return (toDeg(Math.atan2(y, x)) + 360) % 360
}

export class TrailsStore {
  /**
   * @param {object} o  keepMs: trail depth; expireMs: drop a key unseen this long;
   *                    minMove: lat/lon delta (deg) under which a poll is "same position".
   */
  constructor({ keepMs = 10 * 60_000, expireMs = 5 * 60_000, minMove = 1e-5 } = {}) {
    this.keepMs = keepMs
    this.expireMs = expireMs
    this.minMove = minMove
    this.tracks = new Map()   // key -> { pts: [{lat, lon, v, t}], lastSeen, lastMoved }
  }

  /** Record one observation. Returns true when a new point was appended (position changed). */
  push(key, lat, lon, v = null, t = Date.now()) {
    if (!key || !Number.isFinite(lat) || !Number.isFinite(lon)) return false
    let tr = this.tracks.get(key)
    if (!tr) { tr = { pts: [], lastSeen: t, lastMoved: t }; this.tracks.set(key, tr) }
    tr.lastSeen = t
    const last = tr.pts[tr.pts.length - 1]
    if (last && Math.abs(last.lat - lat) < this.minMove && Math.abs(last.lon - lon) < this.minMove) return false
    tr.pts.push({ lat, lon, v, t })
    tr.lastMoved = t
    return true
  }

  /** Drop old points and expired keys. Returns the keys removed entirely. */
  prune(now = Date.now()) {
    const removed = []
    for (const [key, tr] of this.tracks) {
      if (now - tr.lastSeen > this.expireMs) { this.tracks.delete(key); removed.push(key); continue }
      const cutoff = now - this.keepMs
      let i = 0
      while (i < tr.pts.length && tr.pts[i].t < cutoff) i++
      if (i > 0) tr.pts.splice(0, i)
    }
    return removed
  }

  has(key) { return this.tracks.has(key) }
  points(key) { return this.tracks.get(key)?.pts ?? [] }
  keys() { return [...this.tracks.keys()] }
  lastMoved(key) { return this.tracks.get(key)?.lastMoved ?? null }
  firstSeen(key) { const p = this.points(key); return p.length ? p[0].t : null }

  /** Course made good from the last two distinct points (deg), or null when there is no movement yet. */
  heading(key) {
    const pts = this.points(key)
    if (pts.length < 2) return null
    const a = pts[pts.length - 2], b = pts[pts.length - 1]
    return bearing(a.lat, a.lon, b.lat, b.lon)
  }

  /** [{ color, latlngs }] -- consecutive same-colour runs merged (phone + Pi friendly). */
  segments(key, colorOf = () => '#00d4ff') {
    const pts = this.points(key)
    if (pts.length < 2) return []
    const out = []
    let cur = null
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i]
      const color = colorOf(a.v, a)
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

/** Keeps a Leaflet layerGroup in sync with a TrailsStore for the keys `want(key)` says to draw. */
export class TrailsRenderer {
  constructor(store, layerGroup, colorOf) {
    this.store = store
    this.group = layerGroup
    this.colorOf = colorOf
    this.drawn = new Map()   // key -> L.layerGroup
  }

  sync(want, keys = null, { emphasis = () => false } = {}) {
    const list = keys ?? this.store.keys()
    for (const key of list) {
      const show = this.store.has(key) && want(key)
      if (!show) { this.remove(key); continue }
      const segs = this.store.segments(key, this.colorOf)
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
