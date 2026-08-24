import { useEffect, useState } from 'react'

// Fallback only -- the pre-2026-08-24 hardcoded KDCA/CENTER_LAT/LON placeholder
// every map component used to carry directly. Real value comes from
// ULTRAFEEDER_LAT/LON via runner's /api/v1/frontend-config -- never hardcode
// a GPS literal back into a tracked file, see CLAUDE.md's 2026-08-24
// GPS-coordinate-confusion writeup.
const FALLBACK = [38.8521, -77.0377]

let cached = null // module-level -- one fetch serves every mounted consumer

/**
 * useReceiverLocation — the real feeder GPS location, fetched once from
 * runner's own /api/v1/frontend-config (receiver_lat/receiver_lon, sourced
 * server-side from ULTRAFEEDER_LAT/LON). Returns the FALLBACK placeholder
 * until the fetch resolves (or if it fails), then the real value.
 */
export function useReceiverLocation() {
  const [loc, setLoc] = useState(cached || FALLBACK)

  useEffect(() => {
    if (cached) return
    let cancelled = false
    fetch('/api/v1/frontend-config')
      .then(r => (r.ok ? r.json() : null))
      .then(cfg => {
        if (cancelled || !cfg) return
        const lat = Number(cfg.receiver_lat)
        const lon = Number(cfg.receiver_lon)
        if (Number.isFinite(lat) && Number.isFinite(lon)) {
          cached = [lat, lon]
          setLoc(cached)
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  return loc
}
