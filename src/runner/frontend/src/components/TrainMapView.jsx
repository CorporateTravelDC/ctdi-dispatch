import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'

// ── Tile sources ──────────────────────────────────────────────────
const OSM_URL   = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTR  = '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors'
const ORM_URL   = 'https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png'
const ORM_ATTR  = '&copy; <a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> CC-BY-SA'

const ORM_LINK      = 'https://www.openrailwaymap.org/'
const DC_DEFAULT    = [38.8521, -77.0377]    // KDCA — used until config loads
const US_CENTER     = [39.5, -98.35]          // continental US center
const US_ZOOM       = 4
const DEFAULT_ZOOM  = 7
const TRAIN_POLL    = 30_000
const PANEL_POLL    = 60_000

// ── Hardcoded DC fallback (used before /train-config responds) ────
const _FB_STATIONS    = new Set(['WAS', 'BWI', 'NCR', 'ALX', 'BAL', 'ABE', 'WIL', 'NPN'])
const _FB_ROUTES      = ['acela', 'northeast regional', 'palmetto', 'carolinian',
                          'vermonter', 'keystone', 'empire service', 'empire state',
                          'silver star', 'silver meteor']
const _FB_CORE_ROUTES = ['acela', 'northeast regional']

// ── Train icon factory ────────────────────────────────────────────
function trainIcon(isWatched) {
  const color = isWatched ? '#ffd700' : '#00d4ff'
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
    const r = await fetch('/api/dispatch/api/v1/watchlist')
    if (r.ok) {
      const d = await r.json()
      const entries = Array.isArray(d) ? d : (d.entries || d.watchlist || [])
      entries.forEach(e => {
        const val = (typeof e === 'string' ? e : e.entry || e.value || '').toUpperCase().trim()
        if (/^\d{1,4}$/.test(val)) watched.add(val)
      })
    }
  } catch {}
  try {
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
// filterFn: null for national mode (all trains), or isRelevant(t) for regional.
async function fetchTrainPositions(filterFn) {
  try {
    const r = await fetch('https://api.amtraker.com/v3/trains', {
      headers: { Accept: 'application/json' },
    })
    if (!r.ok) return []
    const data = await r.json()
    const trains = []
    Object.values(data).forEach(arr => {
      if (Array.isArray(arr)) arr.forEach(t => {
        if (t.lat && t.lon && (!filterFn || filterFn(t))) trains.push(t)
      })
    })
    return trains
  } catch { return [] }
}

// ── Fetch operator train config from dispatch ─────────────────────
async function fetchTrainConfig() {
  try {
    const r = await fetch('/api/dispatch/api/v1/train-config')
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

// ── Fetch dispatch train list (panel data) ────────────────────────
async function fetchDispatchTrains() {
  try {
    const r = await fetch('/api/dispatch/api/v1/amtrak')
    if (!r.ok) return []
    const d = await r.json()
    return Array.isArray(d.trains) ? d.trains : []
  } catch { return [] }
}

// ── Train panel helpers ───────────────────────────────────────────
function delayColor(delay, state) {
  const s = (state || '').toLowerCase()
  if (s === 'completed')    return 'var(--muted)'
  if (s === 'predeparture') return 'var(--cyan)'
  if (delay > 30)  return 'var(--nogo)'
  if (delay > 10)  return 'var(--orange)'
  if (delay > 0)   return 'var(--marginal)'
  return 'var(--go)'
}

function delayLabel(delay, state) {
  const s = (state || '').toLowerCase()
  if (s === 'completed')    return 'DONE'
  if (s === 'predeparture') return 'PRE'
  if (delay > 0) return `+${delay}m`
  return 'OT'
}

function TrainRow({ t }) {
  const delay = t.delay_minutes || 0
  const color = delayColor(delay, t.train_state)
  const label = delayLabel(delay, t.train_state)
  const num   = t.train_number || '?'
  const name  = (t.train_name || `Train ${num}`).replace(/\s+\d+$/, '')
  const route = (t.orig_code && t.dest_code) ? `${t.orig_code}→${t.dest_code}` : ''
  const event = t.event_name || (t._raw && t._raw.eventCode) || ''

  return (
    <div className="train-row">
      <span className="train-row-num" style={{ color }}>{num}</span>
      <div className="train-row-info">
        <span className="train-row-name">{name}</span>
        {route && <span className="train-row-route">{route}</span>}
        {event && <span className="train-row-event">{event}</span>}
      </div>
      <span className="train-row-badge" style={{ color, borderColor: color }}>{label}</span>
    </div>
  )
}

function TrainPanel({ trains, coreRoutes, loading }) {
  const isCore = t => coreRoutes.some(n => (t.train_name || '').toLowerCase().includes(n.toLowerCase()))
  const core   = trains.filter(isCore)
  const others = trains.filter(t => !isCore(t))

  const coreLabel = coreRoutes.length <= 2
    ? coreRoutes.map(r => r.toUpperCase()).join(' · ')
    : 'CORE ROUTES'

  return (
    <div className="train-side-panel">
      <div className="train-panel-section">
        <div className="train-panel-head">{coreLabel}</div>
        {loading ? (
          <div className="train-panel-empty">Loading…</div>
        ) : core.length ? (
          core.map(t => <TrainRow key={t.train_number} t={t} />)
        ) : (
          <div className="train-panel-empty">No scheduled service</div>
        )}
      </div>

      {others.length > 0 && (
        <div className="train-panel-section">
          <div className="train-panel-head">REGIONAL CORRIDOR</div>
          {others.map(t => <TrainRow key={t.train_number} t={t} />)}
        </div>
      )}

      {!loading && trains.length === 0 && (
        <div className="train-panel-empty" style={{ marginTop: '1rem' }}>
          No regional trains reported
        </div>
      )}
    </div>
  )
}

export default function TrainMapView() {
  const mapRef           = useRef(null)
  const leafletRef       = useRef(null)
  const trainLayerRef    = useRef(null)
  const searchMarkersRef = useRef(null)

  const [trainCount,     setTrainCount]     = useState(0)
  const [vipCount,       setVipCount]       = useState(0)
  const [loadErr,        setLoadErr]        = useState(false)
  const [dispatchTrains, setDispatchTrains] = useState([])
  const [panelLoading,   setPanelLoading]   = useState(true)
  const [viewMode,       setViewMode]       = useState('regional')  // 'regional' | 'national'

  // Operator config (loaded once, then cached)
  const [trainConfig, setTrainConfig] = useState({
    stations:    _FB_STATIONS,
    routes:      _FB_ROUTES,
    core_routes: _FB_CORE_ROUTES,
    center:      DC_DEFAULT,
    zoom:        DEFAULT_ZOOM,
  })

  // Search state
  const [searchInput, setSearchInput] = useState('')
  const [searchState, setSearchState] = useState('idle')
  const [matchCount,  setMatchCount]  = useState(0)

  // ── Load operator config ─────────────────────────────────────────
  useEffect(() => {
    fetchTrainConfig().then(cfg => {
      if (!cfg) return
      const stations  = new Set((cfg.stations  || []).map(s => s.toUpperCase()))
      const routes    = (cfg.routes      || []).map(r => r.toLowerCase())
      const coreRts   = cfg.core_routes  || _FB_CORE_ROUTES
      const center    = cfg.center       || DC_DEFAULT
      const zoom      = cfg.zoom         || DEFAULT_ZOOM
      setTrainConfig({ stations, routes, core_routes: coreRts, center, zoom })
      // Fly to operator's hub on first load
      if (leafletRef.current) {
        leafletRef.current.flyTo(center, zoom, { duration: 1.2 })
      }
    })
  }, [])

  // ── Build filter function from current config ────────────────────
  const makeFilter = useCallback((cfg) => {
    return (train) => {
      const route = (train.routeName || '').toLowerCase()
      if (cfg.routes.some(n => route.includes(n))) return true
      const stns = Array.isArray(train.stations) ? train.stations : []
      return stns.some(s => cfg.stations.has(s.code || s.stationCode || ''))
    }
  }, [])

  // ── Init map ────────────────────────────────────────────────────
  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, { center: DC_DEFAULT, zoom: DEFAULT_ZOOM, zoomControl: true })

    L.tileLayer(OSM_URL, { attribution: OSM_ATTR, className: 'map-tiles' }).addTo(map)
    L.tileLayer(ORM_URL, { attribution: ORM_ATTR, maxZoom: 19, subdomains: 'abc', opacity: 0.8 }).addTo(map)

    L.circleMarker(DC_DEFAULT, { radius: 4, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map).bindTooltip('KDCA / DC', { permanent: true, className: 'airport-label' })

    trainLayerRef.current    = L.layerGroup().addTo(map)
    searchMarkersRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  // ── Handle REGIONAL/NATIONAL toggle ─────────────────────────────
  useEffect(() => {
    if (!leafletRef.current) return
    if (viewMode === 'national') {
      leafletRef.current.flyTo(US_CENTER, US_ZOOM, { duration: 1.0 })
    } else {
      leafletRef.current.flyTo(trainConfig.center, trainConfig.zoom, { duration: 1.0 })
    }
  }, [viewMode, trainConfig])

  // ── Refresh train overlay (map) ─────────────────────────────────
  const refreshTrains = useCallback(async () => {
    if (!trainLayerRef.current) return
    const filterFn = viewMode === 'national' ? null : makeFilter(trainConfig)
    try {
      const [trains, watched] = await Promise.all([
        fetchTrainPositions(filterFn),
        fetchWatchedTrains(),
      ])
      trainLayerRef.current.clearLayers()

      let count = 0, vips = 0
      trains.forEach(t => {
        const num       = String(t.trainNum || t.objectID || '').trim()
        const isWatched = watched.has(num)
        const icon      = trainIcon(isWatched)

        const spd     = t.speed  != null ? `${t.speed} mph` : '—'
        const hdg     = t.heading || '—'
        const sta     = t.eventCode || '—'
        const nm      = t.routeName || t.trainNum || '?'
        const delayed = t.late ? ` (${t.late > 0 ? '+' : ''}${t.late}m)` : ''

        L.marker([t.lat, t.lon], { icon, zIndexOffset: isWatched ? 1000 : 0 })
          .bindTooltip(
            `<b style="color:${isWatched ? '#ffd700' : '#00d4ff'}">${num} — ${nm}</b>` +
            `<br/>Speed: ${spd} · Hdg: ${hdg}` +
            `<br/>Status: ${sta}${delayed}` +
            (isWatched ? '<br/><b style="color:#ffd700">★ WATCHLISTED</b>' : ''),
            { className: 'ac-tooltip', sticky: true }
          )
          .addTo(trainLayerRef.current)

        if (isWatched) {
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
  }, [viewMode, trainConfig, makeFilter])

  useEffect(() => {
    refreshTrains()
    const id = setInterval(refreshTrains, TRAIN_POLL)
    return () => clearInterval(id)
  }, [refreshTrains])

  // ── Panel polling (dispatch API, always regional) ───────────────
  useEffect(() => {
    const poll = async () => {
      const trains = await fetchDispatchTrains()
      setDispatchTrains(trains)
      setPanelLoading(false)
    }
    poll()
    const id = setInterval(poll, PANEL_POLL)
    return () => clearInterval(id)
  }, [])

  // ── Station / city search ───────────────────────────────────────
  const handleSearch = useCallback(async (e) => {
    e.preventDefault()
    const raw = searchInput.trim()
    if (!raw) {
      searchMarkersRef.current?.clearLayers()
      leafletRef.current?.flyTo(
        viewMode === 'national' ? US_CENTER : trainConfig.center,
        viewMode === 'national' ? US_ZOOM   : trainConfig.zoom,
        { duration: 1.0 }
      )
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
  }, [searchInput, viewMode, trainConfig])

  const handleClear = useCallback(() => {
    setSearchInput(''); setSearchState('idle'); setMatchCount(0)
    searchMarkersRef.current?.clearLayers()
    leafletRef.current?.flyTo(
      viewMode === 'national' ? US_CENTER : trainConfig.center,
      viewMode === 'national' ? US_ZOOM   : trainConfig.zoom,
      { duration: 1.0 }
    )
  }, [viewMode, trainConfig])

  const statusLabel =
    searchState === 'loading'  ? '⟳ Geocoding…'
    : searchState === 'notfound' ? '✕ Not found'
    : searchState === 'found'    ? `✓ ${matchCount} location${matchCount !== 1 ? 's' : ''}`
    : null

  const countLabel = viewMode === 'national'
    ? `${trainCount} trains nationwide`
    : `${trainCount} regional trains`

  return (
    <div className="train-map-view">
      <div className="train-map-subnav">
        <span className="train-map-title">EOTD</span>

        {/* ── REGIONAL / NATIONAL toggle ─────────────────────── */}
        <div className="train-view-toggle">
          <button
            className={`train-mode-btn${viewMode === 'regional' ? ' active' : ''}`}
            onClick={() => setViewMode('regional')}
          >REGIONAL</button>
          <button
            className={`train-mode-btn${viewMode === 'national' ? ' active' : ''}`}
            onClick={() => setViewMode('national')}
          >NATIONAL</button>
        </div>

        <span className="stat source-badge" style={{ color: 'var(--cyan)', marginLeft: 'auto' }}>
          OpenRailwayMap + Amtrak live positions
        </span>
        <a href={ORM_LINK} target="_blank" rel="noopener noreferrer"
           className="train-map-external" title="Open OpenRailwayMap">↗</a>
      </div>

      <div className="train-page-body">
        {/* ── Schedule panel (always regional/configured) ────── */}
        <TrainPanel
          trains={dispatchTrains}
          coreRoutes={trainConfig.core_routes}
          loading={panelLoading}
        />

        {/* ── Map + search ─────────────────────────────────── */}
        <div className="train-map-section">
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
                {countLabel}
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
      </div>
    </div>
  )
}
