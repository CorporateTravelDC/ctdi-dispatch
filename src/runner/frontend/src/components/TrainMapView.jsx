import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import AriaCompassRegion from './AriaCompassRegion.jsx'
import AccessibleTable   from './AccessibleTable.jsx'
import { useCompassSummary } from '../hooks/useCompassSummary.js'
import { useWatchlist }      from '../hooks/useWatchlist.js'

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

// ── Train icon — heading-aware SVG arrow (top-down locomotive silhouette) ───
function trainIcon(isWatched, heading) {
  const color  = isWatched ? '#ffd700' : '#00d4ff'
  const stroke = isWatched ? '#3a2a00' : '#002a3a'
  const glow   = isWatched
    ? 'filter:drop-shadow(0 0 5px #ffd700) drop-shadow(0 0 2px #ff9900);'
    : 'filter:drop-shadow(0 0 3px #00d4ff);'
  const deg    = heading != null && !isNaN(heading) ? heading : 0
  const sz     = isWatched ? 18 : 14
  const half   = sz / 2
  return L.divIcon({
    className: '',
    html: `<div style="transform:rotate(${deg}deg);width:${sz}px;height:${sz}px;${glow}">
      <svg viewBox="0 0 12 18" width="${sz}" height="${sz}">
        <polygon points="6,0 12,14 6,18 0,14"
          fill="${color}" stroke="${stroke}" stroke-width="1.2" opacity="0.95"/>
        ${isWatched
          ? '<polygon points="6,0 12,14 6,18 0,14" fill="none" stroke="#fff" stroke-width="0.5" opacity="0.55"/>'
          : ''}
      </svg>
    </div>`,
    iconSize: [sz, sz],
    iconAnchor: [half, half],
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
// Field name normaliser — handles both the local amtrak-tracker container
// format (train_num / route / origin / destination / status) and the
// amtraker.com _normalize() format (train_number / train_name / train_state /
// orig_code / dest_code).
function tNum(t)   { return t.train_number ?? t.train_num ?? null }
function tName(t)  { return t.train_name   || t.route      || null }
function tState(t) { return t.train_state  || t.status     || '' }
function tOrig(t)  { return t.orig_code    || t.origin     || '' }
function tDest(t)  { return t.dest_code    || t.destination || '' }
function tEvent(t) { return t.event_name   || (t._raw && t._raw.eventCode) || '' }

function delayColor(delay, state) {
  const s = (state || '').toLowerCase()
  if (s === 'completed' || s === 'arrived') return 'var(--muted)'
  if (s === 'predeparture' || s === 'scheduled') return 'var(--cyan)'
  if (delay > 30)  return 'var(--nogo)'
  if (delay > 10)  return 'var(--orange)'
  if (delay > 0)   return 'var(--marginal)'
  return 'var(--go)'
}

function delayLabel(delay, state) {
  const s = (state || '').toLowerCase()
  if (s === 'completed' || s === 'arrived')         return 'ARR'
  if (s === 'predeparture' || s === 'scheduled')    return 'SCH'
  if (delay > 0) return `+${delay}m`
  return 'OT'
}

function TrainRow({ t }) {
  const delay  = t.delay_minutes || 0
  const state  = tState(t)
  const color  = delayColor(delay, state)
  const label  = delayLabel(delay, state)
  const num    = tNum(t) ?? '?'
  const rawName = tName(t) || `Train ${num}`
  const name   = rawName.replace(/\s+\d+$/, '').trim()
  const orig   = tOrig(t)
  const dest   = tDest(t)
  const route  = (orig && dest) ? `${orig}→${dest}` : ''
  const event  = tEvent(t)

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
  const isCore = t => {
    const name = (tName(t) || '').toLowerCase()
    return coreRoutes.some(n => name.includes(n.toLowerCase()))
  }
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

  // Map display mode: 'iframe' = asm.transitdocs.com bg + transparent overlay | 'local' = full OSM+ORM
  const osmLayerRef      = useRef(null)
  const ormLayerRef      = useRef(null)
  const [mapDisplayMode, setMapDisplayMode] = useState('iframe')
  const [iframeError,    setIframeError]    = useState(false)

  // Watchlist + train position items for compass summary
  const { entries: watchEntries } = useWatchlist()
  const watchlistSet = useMemo(() => {
    const s = new Set()
    watchEntries.forEach(e => {
      if (e.identifier) s.add(e.identifier.trim())
    })
    return s
  }, [watchEntries])
  const [trainItems, setTrainItems] = useState([])

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

    osmLayerRef.current = L.tileLayer(OSM_URL, { attribution: OSM_ATTR, className: 'map-tiles' })
    ormLayerRef.current = L.tileLayer(ORM_URL, { attribution: ORM_ATTR, maxZoom: 19, subdomains: 'abc', opacity: 0.8 })
    // Start without tiles (iframe mode is default); interaction starts disabled
    map.dragging.disable()
    map.scrollWheelZoom.disable()
    map.touchZoom.disable()

    L.circleMarker(DC_DEFAULT, { radius: 4, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map).bindTooltip('KDCA / DC', { permanent: true, className: 'airport-label' })

    trainLayerRef.current    = L.layerGroup().addTo(map)
    searchMarkersRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  // ── Toggle tile layers and interaction based on mapDisplayMode ──────────────────
  useEffect(() => {
    const map = leafletRef.current
    if (!map) return
    if (mapDisplayMode === 'local') {
      if (osmLayerRef.current && !map.hasLayer(osmLayerRef.current)) map.addLayer(osmLayerRef.current)
      if (ormLayerRef.current && !map.hasLayer(ormLayerRef.current)) map.addLayer(ormLayerRef.current)
      map.dragging.enable()
      map.scrollWheelZoom.enable()
      map.touchZoom.enable()
    } else {
      if (osmLayerRef.current && map.hasLayer(osmLayerRef.current))  map.removeLayer(osmLayerRef.current)
      if (ormLayerRef.current && map.hasLayer(ormLayerRef.current))  map.removeLayer(ormLayerRef.current)
      map.dragging.disable()
      map.scrollWheelZoom.disable()
      map.touchZoom.disable()
    }
  }, [mapDisplayMode])

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
      const [trains] = await Promise.all([
        fetchTrainPositions(filterFn),
      ])
      trainLayerRef.current.clearLayers()

      let count = 0, vips = 0
      const trainItemsArr = []
      trains.forEach(t => {
        const num       = String(t.trainNum || t.objectID || '').trim()
        const isWatched = watchlistSet.has(num)
        const hdgNum    = typeof t.heading === 'number' ? t.heading : null
        const icon      = trainIcon(isWatched, hdgNum)

        const spd     = t.speed  != null ? `${t.speed} mph` : '—'
        const hdg     = t.heading || '—'
        const sta     = t.eventCode || '—'
        const nm      = t.routeName || t.trainNum || '?'
        const delayed = t.late ? ` (${t.late > 0 ? '+' : ''}${t.late}m)` : ''

        const tip = isWatched
          ? `<div class="ac-tooltip-tracked">
               <div class="ac-tooltip-tracked-info">
                 <span class="ac-tracked-badge">★ WATCHED</span>
                 <b class="ac-tracked-callsign">${num} — ${nm}</b>
                 <span class="ac-tracked-details">Speed: ${spd} · Hdg: ${hdg} · ${sta}${delayed}</span>
               </div>
             </div>`
          : `<b style="color:#00d4ff">${num} — ${nm}</b><br/>Speed: ${spd} · Hdg: ${hdg}<br/>Status: ${sta}${delayed}`

        L.marker([t.lat, t.lon], {
          icon, interactive: true, zIndexOffset: isWatched ? 2000 : 0,
        })
          .bindTooltip(tip, {
            className: isWatched ? 'ac-tooltip tracked-tooltip' : 'ac-tooltip',
            permanent: isWatched,
            direction: 'top',
            sticky: !isWatched,
          })
          .addTo(trainLayerRef.current)

        if (isWatched) vips++
        trainItemsArr.push({ lat: t.lat, lon: t.lon, label: `${num} ${nm}`, tracked: isWatched })
        count++
      })

      setTrainCount(count)
      setVipCount(vips)
      setTrainItems(trainItemsArr)
      setLoadErr(false)
    } catch { setLoadErr(true) }
  }, [viewMode, trainConfig, makeFilter, watchlistSet])

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

  const compassSummary = useCompassSummary(trainItems, [])
  const trainTableRows = trainItems.map(t => ({
    train: t.label, lat: t.lat?.toFixed(4), lon: t.lon?.toFixed(4), watched: t.tracked ? '★' : '',
  }))

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

        <div className="ais-mode-toggle" role="group" aria-label="Map display mode">
          <button className={`ais-mode-btn${mapDisplayMode === 'iframe' ? ' active' : ''}`}
            onClick={() => setMapDisplayMode('iframe')}
            disabled={iframeError} title={iframeError ? 'Amtrak tracker blocked embed' : 'Amtrak System Map + local overlay'}>
            🌐 LIVE</button>
          <button className={`ais-mode-btn${mapDisplayMode === 'local' ? ' active' : ''}`}
            onClick={() => setMapDisplayMode('local')} title="Full OSM + OpenRailwayMap">
            🗺 MAP</button>
        </div>
        <span className="stat source-badge" style={{ color: 'var(--cyan)', marginLeft: 'auto' }}>
          {mapDisplayMode === 'iframe' ? 'asm.transitdocs.com + Amtrak overlay' : 'OpenRailwayMap + Amtrak live'}
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
          {mapDisplayMode === 'local' && (
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
          )}

          <div className="globe-iframe-wrap">
            <AriaCompassRegion
              summary={compassSummary}
              entityType="trains"
              count={trainCount}
              extra="NEC corridor · Amtrak regional and national service."
            />
            <AccessibleTable
              id="train-position-table"
              caption={`Amtrak train positions — ${trainCount} active`}
              columns={[
                { key: 'train', label: 'Train' }, { key: 'lat', label: 'Latitude' },
                { key: 'lon', label: 'Longitude' }, { key: 'watched', label: '★' },
              ]}
              rows={trainTableRows}
              emptyMsg="No active trains in range."
            />
            {/* Aggregate train tracker iframe */}
            {mapDisplayMode === 'iframe' && !iframeError && (
              <iframe
                src="https://asm.transitdocs.com/map"
                className="globe-iframe"
                title="Amtrak System Map (asm.transitdocs.com)"
                referrerPolicy="no-referrer"
                onError={() => { setIframeError(true); setMapDisplayMode('local') }}
                allow="fullscreen"
              />
            )}
            {/* Leaflet: transparent in iframe mode, tiled in local mode */}
            <div
              ref={mapRef}
              className={`ais-leaflet-layer${mapDisplayMode === 'iframe' ? ' ais-overlay-mode' : ' ais-local-mode'}`}
            />
            <div className="map-overlay-stats globe-stats">
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
