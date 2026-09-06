/**
 * useCompassSummary — Tableau Azimuth method compass-quadrant descriptions.
 * Returns a human-readable compass-sector summary string for an ARIA live region.
 * Ref: "Azimuth: Designing Accessible Dashboards for Screen Reader Users" — Tableau, 2021.
 */
import { useMemo } from 'react'

// Fallback only, used when a caller doesn't supply a real `center` -- see
// useReceiverLocation.js. Never hardcode a GPS literal as the *only* source
// of truth again; CLAUDE.md's 2026-08-24 GPS-coordinate-confusion writeup
// covers why.
const DEFAULT_CENTER = [38.8521, -77.0377]

function bearingTo(lat, lon, centerLat, centerLon) {
  const dLon  = (lon - centerLon) * Math.PI / 180
  const lat1  = centerLat * Math.PI / 180
  const lat2  = lat * Math.PI / 180
  const y = Math.sin(dLon) * Math.cos(lat2)
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon)
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360
}

const QUADRANT_NAMES = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

function toQuadrant(bearing) {
  return QUADRANT_NAMES[Math.round(bearing / 45) % 8]
}

/**
 * @param {Array<{lat, lon, label, tracked?}>} items  positioned entities
 * @param {string[]}                           extra  extra text fragments appended verbatim
 * @param {[number, number]}                   center reference point for bearings; pass the
 *                                                     real value from useReceiverLocation()
 * @returns {string}  ARIA-ready compass summary
 */
export function useCompassSummary(items = [], extra = [], center = DEFAULT_CENTER) {
  const [centerLat, centerLon] = center
  return useMemo(() => {
    if (!items.length && !extra.length) return 'No items in range.'
    const buckets = {}
    for (const item of items) {
      if (item.lat == null || item.lon == null) continue
      const q = toQuadrant(bearingTo(item.lat, item.lon, centerLat, centerLon))
      if (!buckets[q]) buckets[q] = []
      buckets[q].push(item)
    }
    const parts = []
    for (const q of QUADRANT_NAMES) {
      const group = buckets[q]
      if (!group?.length) continue
      const tracked = group.filter(i => i.tracked)
      if (tracked.length) {
        const names = tracked.map(i => i.label).join(', ')
        parts.push(`${q}: ${group.length} total, ${tracked.length} tracked — ${names}`)
      } else {
        parts.push(`${q}: ${group.length}`)
      }
    }
    const allParts = [...parts, ...extra]
    return allParts.length ? allParts.join(' · ') : 'No items in range.'
  }, [items, extra, centerLat, centerLon])
}
