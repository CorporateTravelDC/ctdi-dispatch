import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'

// ── Tile sources ──────────────────────────────────────────────────
const OSM_URL   = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTR  = '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors'
const ORM_URL   = 'https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png'
const ORM_ATTR  = '&copy; <a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> CC-BY-SA'

const ORM_LINK    = 'https://www.openrailwaymap.org/'
const KDCA        = [38.8521, -77.0377]
const DEFAULT_ZOOM = 6
const TRAIN_POLL  = 30_000

// ── Train icon factory ────────────────────────────────────────────
function trainIcon(isVip, isWatched) {
  const color = isVip ? '#ff3131' : isWatched ? '#ffd700' : '#00d4ff'
  return L.divIcon({
    className: '',
    html: `<div style="
      width:10px;height:10px;border-radius:50%;
      background:${color};border:2px solid #000;
      box-shadow:0 0 6px ${color};
    "></div>`,
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  })
}

// ── Nominatim geocode ─────────────────────────────────────────────
async function geocode(query) {
  const p = new URLSearchParams({ q: query, format: 'json', limit: '1', countrycodes: 'us' })
  try {
    let r = await fetch(`https://nominatim.openstreetmap.org/search?${p}`, { headers: { 'Accept-Language': 'en' } })
    let res = r.ok ? await r.json() : []
    if (!res.length) {
      const p2 = new URLSearchParams({ q: query, format: 'json', limit: '1' })
      r = await fetch(`https://nominatim.openstreetmap.org/search?${p2}`, { headers: { 'Accept-Language': 'en' } })
      res = r.ok ? await r.json() : []
    }
    const h = res[0]
    return h ? { lat: parseFloat(h.lat), lon: parseFloat(h.lon), name: h.display_name } : null
  } catch { return null }
}

// ── Fetch watchlist from dispatch ──────────────────────────────────
async function fetchWatchedTrains() {
  const watched = new Set()
  try {
    // Session watchlist — Tier 0
    const r = await fetch('/api/dispatch/api/v1/watchlist')
    if (r.ok) {
      const d = await r.json()
      const entries = Array.isArray(d) ? d : (d.entries || d.watchlist || [])
      entries.forEach(e => {
        const val = (typeof e === 'string' ? e : e.entry || e.value || '').toUpperCase().trim()
        // Train numbers: pure digits or Amtrak train number patterns
        if (/^\d{1,4}$/.test(val)) watched.add(val)
      })
    }
  } catch {}
  try {
    // VIP list — try without token (will 401 if not Tailscale, safe to ignore)
    const r = await fetch('/api/dispatch/admin/vip')
    if (r.ok) {
      const d = await r.json()
      const entries = Array.isArray(d) ? d : (d.vip || d.entries || [])
      entries.forEach(e => {
        const val = (typeof e === 'string' ? e : e.entry || e.value || '').toUpperCase().trim()
        if (/^\d{1,4}$/.test(val)) watched.add(val)
      })
    }
  } catch {}
  return watched
}

// ── Fetch Amtrak positions ────────────────────────────────────────
// api.amtraker.com returns { "trainNum": [{lat, lon, speed, heading, eventCode, trainNum, ...}] }
async function fetchTrainPositions() {
  try {
    const r = await fetch('https://api.amtraker.com/v3/trains', {
      headers: { Accept: 'application/json' },
    })
    if (!r.ok) return []
    const data = await r.json()
    const trains = []
    Object.values(data).forEach(arr => {
      if (Array.isArray(arr)) arr.forEach(t => { if (t.lat && t.lon) trains.push(t) })
    })
    return trains
  } catch { return [] }
}

// ── Heading arrow svg ─────────────────────────────────────────────
function headingDeg(h) {
  const map = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 }
  return map[h] ?? 0
}

export default function TrainMapView() {
  const mapRef       = useRef(null)
  const leafletRef   = useRef(null)
  const trainLayerRef = useRef(null)
  const searchMarkersRef = useRef(null)

  const [trainCount, setTrainCount]   = useState(0)
  const [vipCount,   setVipCount]     = useState(0)
  const [loadErr,    setLoadErr]      = useState(false)

  // Search state
  const [searchInput,  setSearchInput]  = useState('')
  const [searchState,  setSearchState]  = useState('idle')
  const [matchCount,   setMatchCount]   = useState(0)

  // ── Init map ────────────────────────────────────────────────────
  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, { center: KDCA, zoom: DEFAULT_ZOOM, zoomControl: true })

    L.tileLayer(OSM_URL, { attribution: OSM_ATTR, className: 'map-tiles' }).addTo(map)
    L.tileLayer(ORM_URL, { attribution: ORM_ATTR, maxZoom: 19, subdomains: 'abc', opacity: 0.8 }).addTo(map)

    L.circleMarker(KDCA, { radius: 4, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map).bindTooltip('KDCA / DC', { permanent: true, className: 'airport-label' })

    trainLayerRef.current    = L.layerGroup().addTo(map)
    searchMarkersRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  // ── Refresh train overlay ───────────────────────────────────────
  const refreshTrains = useCallback(async () => {
    if (!trainLayerRef.current) return
    try {
      const [trains, watched] = await Promise.all([fetchTrainPositions(), fetchWatchedTrains()])
      trainLayerRef.current.clearLayers()

      let count = 0, vips = 0
      trains.forEach(t => {
        const num  = String(t.trainNum || t.objectID || '').trim()
        const isWatched = watched.has(num)
        const isVip     = t.priority === 1 || (t.trainNum && watched.has(num) && false) // extend logic here
        // Mark as VIP if in watchlist at highest priority — for now watchlist match = isWatched
        const icon = trainIcon(false, isWatched)

        const spd  = t.speed  != null ? `${t.speed} mph` : '—'
        const hdg  = t.heading || '—'
        const sta  = t.eventCode || '—'
        const nm   = t.routeName || t.trainNum || '?'
        const delayed = t.late ? ` (${t.late > 0 ? '+' : ''}${t.late}m)` : ''

        const marker = L.marker([t.lat, t.lon], { icon, zIndexOffset: isWatched ? 1000 : 0 })
          .bindTooltip(
            `<b style="color:${isWatched ? '#ffd700' : '#00d4ff'}">${num} — ${nm}</b>` +
            `<br/>Speed: ${spd} · Hdg: ${hdg}` +
            `<br/>Status: ${sta}${delayed}` +
            (isWatched ? '<br/><b style="color:#ffd700">★ WATCHLISTED</b>' : ''),
            { className: 'ac-tooltip', sticky: true }
          )
          .addTo(trainLayerRef.current)

        if (isWatched) {
          // Pulsing ring for watchlisted trains
          L.circleMarker([t.lat, t.lon], {
            radius: 14, color: '#ffd700', weight: 1.5,
            fill: false, opacity: 0.5,
          }).addTo(trainLayerRef.current)
          vips++
        }
        count++
      })

      setTrainCount(count)
      setVipCount(vips)
      setLoadErr(false)
    } catch { setLoadErr(true) }
  }, [])

  useEffect(() => {
    refreshTrains()
    const id = setInterval(refreshTrains, TRAIN_POLL)
    return () => clearInterval(id)
  }, [refreshTrains])

  // ── Station / city search ───────────────────────────────────────
  const handleSearch = useCallback(async (e) => {
    e.preventDefault()
    const raw = searchInput.trim()
    if (!raw) {
      searchMarkersRef.current?.clearLayers()
      leafletRef.current?.flyTo(KDCA, DEFAULT_ZOOM, { duration: 1.0 })
      setSearchState('idle')
      setMatchCount(0)
      return
    }

    setSearchState('loading')
    searchMarkersRef.current?.clearLayers()

    const queries = raw.split(',').map(q => q.trim()).filter(Boolean)
    const results = []
    for (let i = 0; i < queries.length; i++) {
      if (i > 0) await new Promise(res => setTimeout(res, 1050))
      const hit = await geocode(queries[i])
      if (hit) results.push({ ...hit, query: queries[i] })
    }

    if (!results.length) { setSearchState('notfound'); setMatchCount(0); return }

    setSearchState('found')
    setMatchCount(results.length)

    results.forEach(r => {
      L.circleMarker([r.lat, r.lon], { radius: 8, color: '#00d4ff', fill: true, fillOpacity: 0.9, weight: 2 })
        .addTo(searchMarkersRef.current)
        .bindTooltip(r.query, { permanent: true, direction: 'top', className: 'airport-label' })
    })

    const map = leafletRef.current
    if (!map) return
    if (results.length === 1) {
      map.flyTo([results[0].lat, results[0].lon], 13, { duration: 1.2 })
    } else {
      const lats = results.map(r => r.lat), lons = results.map(r => r.lon)
      map.flyToBounds(
        L.latLngBounds([Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]).pad(0.25),
        { duration: 1.2, maxZoom: 13 }
      )
    }
  }, [searchInput])

  const handleClear = useCallback(() => {
    setSearchInput(''); setSearchState('idle'); setMatchCount(0)
    searchMarkersRef.current?.clearLayers()
    leafletRef.current?.flyTo(KDCA, DEFAULT_ZOOM, { duration: 1.0 })
  }, [])

  const statusLabel =
    searchState === 'loading'  ? '⟳ Geocoding…'
    : searchState === 'notfound' ? '✕ Not found'
    : searchState === 'found'    ? `✓ ${matchCount} location${matchCount !== 1 ? 's' : ''}`
    : null

  return (
    <div className="train-map-view">
      <div className="train-map-subnav">
        <span className="train-map-title">EOTD</span>
        <span className="stat source-badge" style={{ color: 'var(--cyan)' }}>
          OpenRailwayMap + Amtrak live positions
        </span>
        <a href={ORM_LINK} target="_blank" rel="noopener noreferrer"
           className="train-map-external" title="Open OpenRailwayMap">↗</a>
      </div>

      {/* ── Search bar ─────────────────────────────────────────── */}
      <form className="train-search-bar" onSubmit={handleSearch} role="search">
        <input
          className="train-search-input"
          type="search"
          placeholder="Station or city… (comma-separate for multiple)"
          value={searchInput}
          onChange={e => setSearchInput(e.target.value)}
          aria-label="Search for a rail station or city"
          autoComplete="off" spellCheck={false}
          disabled={searchState === 'loading'}
        />
        <button type="submit" className="globe-search-btn"
          disabled={searchState === 'loading'} aria-label="Search">
          {searchState === 'loading' ? '⟳' : '⌕'}
        </button>
        {statusLabel && (
          <span className={`globe-search-type-badge${searchState === 'notfound' ? ' search-badge-notfound' : ''}`}>
            {statusLabel}
          </span>
        )}
        {(searchState === 'found' || searchState === 'notfound') && (
          <button type="button" className="globe-search-clear" onClick={handleClear} aria-label="Clear search">✕</button>
        )}
      </form>

      <div className="map-container">
        <div ref={mapRef} className="leaflet-map" />
        <div className="map-overlay-stats">
          <span className="stat source-badge" style={{ color: '#00d4ff' }}>
            {trainCount} trains
          </span>
          {vipCount > 0 && (
            <span className="stat source-badge" style={{ color: '#ffd700' }}>
              ★ {vipCount} watchlisted
            </span>
          )}
          {loadErr && <span className="stat" style={{ color: 'var(--nogo)', fontSize: '0.6rem' }}>Amtrak feed error</span>}
          <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>
            OSM · ORM rail · Amtrak positions
          </span>
          <button
            className="intel-refresh-btn"
            onClick={refreshTrains}
            title="Refresh train positions"
            style={{ marginLeft: '0.5rem' }}
          >↻</button>
        </div>
      </div>
    </div>
  )
}
