import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import AriaCompassRegion from './AriaCompassRegion.jsx'
import AccessibleTable   from './AccessibleTable.jsx'
import { useCompassSummary } from '../hooks/useCompassSummary.js'
import LayerSidebar from './LayerSidebar.jsx'
import { useGlobalLayerConfig } from '../App.jsx'
import { useWatchlist, airlineLogoUrl, FALLBACK_PLANE_SVG } from '../hooks/useWatchlist.js'

// DC-area static airspace GeoJSON (approximate)
const AIRSPACE = {
  FRZ:  { radius: 9260,   color: '#ff3131', label: 'FRZ 5nm'   },
  SFRA: { radius: 27780,  color: '#4a9eff', label: 'SFRA 15nm' },
}

const KDCA = [38.8521, -77.0377]
const RANGE_RINGS_NM = [50, 100, 150, 250]
const NM_TO_M = 1852

// Base globe URL — centred on KDCA, zoom 8, native controls visible
const GLOBE_BASE = `https://globe.airplanes.live/?centerlat=${KDCA[0]}&centerlon=${KDCA[1]}&zoom=8&hideSidebar`

// Detect search type from user input
function detectSearchType(raw) {
  const q = raw.trim().toUpperCase()
  if (!q) return null
  if (/^[0-9A-F]{6}$/.test(q))      return { type: 'icao',   param: 'icao',   value: q.toLowerCase(), label: 'HEX'      }
  if (/^[A-Z]-?[0-9]/.test(q) || /^N[0-9]/.test(q)) return { type: 'reg', param: 'reg', value: q, label: 'REG' }
  if (/^[A-Z]{2,3}[0-9]/.test(q))  return { type: 'flight', param: 'flight', value: q, label: 'CALLSIGN' }
  return { type: 'flight', param: 'flight', value: q, label: 'CALLSIGN' }
}

function buildGlobeUrl(searchResult) {
  if (!searchResult) return GLOBE_BASE
  return `https://globe.airplanes.live/?${searchResult.param}=${encodeURIComponent(searchResult.value)}`
}

// ── Marker factories ───────────────────────────────────────────────────────

function localFeedIcon(heading) {
  const h = heading || 0
  return window.L.divIcon({
    className: '',
    html: `<div class="aircraft-marker local-feed" style="transform:rotate(${h}deg)">
             <svg width="14" height="20" viewBox="0 0 14 20">
               <polygon points="7,0 14,20 7,15 0,20" fill="#39ff14" opacity="0.95" stroke="#1a4a1a" stroke-width="0.5"/>
             </svg>
           </div>`,
    iconSize: [14, 20],
    iconAnchor: [7, 10],
  })
}

function headingIcon(heading) {
  const h = heading || 0
  return window.L.divIcon({
    className: '',
    html: `<div class="aircraft-marker" style="transform:rotate(${h}deg)">
             <svg width="14" height="20" viewBox="0 0 14 20">
               <polygon points="7,0 14,20 7,15 0,20" fill="#00d4ff" opacity="0.9"/>
             </svg>
           </div>`,
    iconSize: [14, 20],
    iconAnchor: [7, 10],
  })
}

// Cyan filled icon for watchlist-tracked aircraft — larger for visual prominence
function trackedIcon(heading) {
  const h = heading || 0
  return window.L.divIcon({
    className: '',
    html: `<div class="aircraft-marker tracked" style="transform:rotate(${h}deg)">
             <svg width="18" height="26" viewBox="0 0 14 20">
               <polygon points="7,0 14,20 7,15 0,20" fill="#00d4ff" stroke="#003a4a" stroke-width="1.5"/>
               <polygon points="7,0 14,20 7,15 0,20" fill="none" stroke="#ffffff" stroke-width="0.5" opacity="0.6"/>
             </svg>
           </div>`,
    iconSize: [18, 26],
    iconAnchor: [9, 13],
  })
}

// Build tooltip HTML for a tracked aircraft with airline logo
function trackedTooltipHtml(callsign, alt, spd, hdg) {
  const logoUrl = airlineLogoUrl(callsign)
  const logoTag = logoUrl
    ? `<img src="${logoUrl}" class="ac-logo" alt="${callsign.slice(0,3)} logo"
            onerror="this.src='${FALLBACK_PLANE_SVG}';this.classList.add('ac-logo-fallback')" />`
    : `<img src="${FALLBACK_PLANE_SVG}" class="ac-logo ac-logo-fallback" alt="aircraft" />`
  return `<div class="ac-tooltip-tracked">
    ${logoTag}
    <div class="ac-tooltip-tracked-info">
      <span class="ac-tracked-badge">★ TRACKED</span>
      <b class="ac-tracked-callsign">${callsign}</b>
      <span class="ac-tracked-details">${alt}ft · ${spd}kt · ${Math.round(hdg)}°</span>
    </div>
  </div>`
}

// ── Watchlist badge overlay for GlobeMap ──────────────────────────────────

function WatchlistBadge({ entries }) {
  const flights = entries.filter(e => e.entry_type === 'flight')
  if (!flights.length) return null
  return (
    <div className="globe-watchlist-badge" role="complementary" aria-label="Active watchlist">
      <span className="gwb-label">★ WATCHING</span>
      {flights.map(e => (
        <span key={e.id} className="gwb-chip" title={e.last_event_summary || e.identifier}>
          {e.identifier}
        </span>
      ))}
    </div>
  )
}

// ── Globe mode: iframe + local feeder overlay ─────────────────────────────
function GlobeMap({ liveState }) {
  const overlayRef    = useRef(null)
  const leafletRef    = useRef(null)
  const localLayerRef = useRef(null)
  const [localCount,  setLocalCount]  = useState(0)
  const [iframeError, setIframeError] = useState(false)
  const [localItems,  setLocalItems]  = useState([])

  const [searchInput, setSearchInput] = useState('')
  const [searchResult, setSearchResult] = useState(null)
  const [iframeSrc, setIframeSrc]     = useState(GLOBE_BASE)

  const { entries: watchEntries, callsignSet, hexSet } = useWatchlist()

  const handleSearch = useCallback((e) => {
    e.preventDefault()
    const q = searchInput.trim()
    if (!q) { setSearchResult(null); setIframeSrc(GLOBE_BASE); return }
    const result = detectSearchType(q)
    setSearchResult(result)
    setIframeSrc(buildGlobeUrl(result))
  }, [searchInput])

  const handleClear = useCallback(() => {
    setSearchInput('')
    setSearchResult(null)
    setIframeSrc(GLOBE_BASE)
  }, [])

  // Init overlay Leaflet (transparent, pointer-events managed per-marker)
  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(overlayRef.current, {
      center: KDCA,
      zoom: 8,
      zoomControl: false,
      attributionControl: false,
      dragging: false,
      scrollWheelZoom: false,
      touchZoom: false,
      doubleClickZoom: false,
      keyboard: false,
      boxZoom: false,
    })
    localLayerRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  const refreshLocal = useCallback(async () => {
    if (!localLayerRef.current) return
    try {
      const r = await fetch('/api/adsb/local')
      if (!r.ok) return
      const data = await r.json()
      const aircraft = data.aircraft || data.ac || []
      localLayerRef.current.clearLayers()
      let count = 0
      aircraft.forEach(ac => {
        if (!ac.lat || !ac.lon) return
        const alt = ac.alt_baro ?? ac.altitude ?? 0
        if (ac.ground || ac.gnd || alt === 'ground' || alt < 100) return
        const callsign = (ac.flight || '').trim() || ac.hex || '?'
        const hdg = ac.track || ac.heading || 0
        const spd = ac.gs ?? ac.speed ?? '?'
        const isTracked = callsignSet.has(callsign.toUpperCase()) || hexSet.has((ac.hex || '').toLowerCase())
        const marker = L.marker([ac.lat, ac.lon], {
          icon: isTracked ? trackedIcon(hdg) : localFeedIcon(hdg),
          interactive: true,
          zIndexOffset: isTracked ? 1000 : 0,
        })
        const tip = isTracked
          ? trackedTooltipHtml(callsign, alt, spd, hdg)
          : `<b class="local-feed-tip">LOCAL</b> ${callsign}<br/>Alt: ${alt}ft · ${spd}kt · ${Math.round(hdg)}°`
        marker.bindTooltip(tip, {
          className: isTracked ? 'ac-tooltip tracked-tooltip' : 'ac-tooltip local-tooltip',
          permanent: isTracked,
        })
        marker.addTo(localLayerRef.current)
        count++
      })
      setLocalCount(count)
      setLocalItems(
        aircraft
          .filter(ac => ac.lat && ac.lon)
          .map(ac => {
            const callsign = (ac.flight || '').trim() || ac.hex || '?'
            const isTracked = callsignSet.has(callsign.toUpperCase()) || hexSet.has((ac.hex || '').toLowerCase())
            return { lat: ac.lat, lon: ac.lon, label: callsign, tracked: isTracked }
          })
      )
    } catch (_) {}
  }, [callsignSet, hexSet])

  useEffect(() => {
    refreshLocal()
    const id = setInterval(refreshLocal, 10_000)
    return () => clearInterval(id)
  }, [refreshLocal])

  const compassSummary = useCompassSummary(localItems, [])
  const localTableRows = localItems.map(ac => ({ callsign: ac.label, lat: ac.lat?.toFixed(4), lon: ac.lon?.toFixed(4), tracked: ac.tracked ? 'Yes' : '' }))

  return (
    <div className="globe-map-wrap">
      <AriaCompassRegion
        summary={compassSummary}
        entityType="local feeder aircraft"
        count={localCount}
        extra="Global traffic via globe.airplanes.live."
      />
      <AccessibleTable
        id="globe-local-table"
        caption={`Local feeder aircraft — ${localCount} visible`}
        columns={[
          { key: 'callsign', label: 'Callsign' },
          { key: 'lat',      label: 'Latitude'  },
          { key: 'lon',      label: 'Longitude' },
          { key: 'tracked',  label: 'Tracked'   },
        ]}
        rows={localTableRows}
        emptyMsg="No local feeder aircraft visible."
      />
      {/* ── Search bar overlay ─────────────────────────────────── */}
      <form className="globe-search-bar" onSubmit={handleSearch} role="search">
        <input
          className="globe-search-input"
          type="search"
          placeholder="Callsign, tail / reg, hex ID…"
          value={searchInput}
          onChange={e => setSearchInput(e.target.value)}
          aria-label="Search aircraft by callsign, registration, or ICAO hex"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="characters"
          spellCheck={false}
        />
        <button type="submit" className="globe-search-btn" aria-label="Search">⌕</button>
        {searchResult && (
          <>
            <span className="globe-search-type-badge">
              {searchResult.label}: {searchResult.value}
            </span>
            <button type="button" className="globe-search-clear" onClick={handleClear} aria-label="Clear search">✕</button>
          </>
        )}
      </form>

      <div className="globe-iframe-wrap">
        {iframeError ? (
          <div className="globe-fallback">
            <p>globe.airplanes.live blocked cross-origin embedding.</p>
            <a href="https://globe.airplanes.live/" target="_blank" rel="noopener noreferrer"
               className="globe-fallback-link">Open globe.airplanes.live ↗</a>
          </div>
        ) : (
          <iframe
            key={iframeSrc}
            src={iframeSrc}
            className="globe-iframe"
            title="globe.airplanes.live"
            referrerPolicy="no-referrer"
            onError={() => setIframeError(true)}
            allow="fullscreen"
          />
        )}
        {/* Transparent Leaflet overlay — local feeder + tracked aircraft */}
        <div ref={overlayRef} className="globe-local-overlay" />
        {/* Watchlist badge — top-right corner, above iframe */}
        <WatchlistBadge entries={watchEntries} />
      </div>

      <div className="map-overlay-stats globe-stats">
        <span className="stat source-badge local-feed-stat">◉ {localCount} LOCAL</span>
        <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>
          green = local feeder · cyan = tracked · globe = airplanes.live
        </span>
      </div>
    </div>
  )
}

// ── Local Leaflet map ──────────────────────────────────────────────────────
function LocalMap({ adsbMode, liveState }) {
  const { config } = useGlobalLayerConfig() ?? {}
  const layers = config?.layers ?? {}
  const mapRef           = useRef(null)
  const leafletRef       = useRef(null)
  const aircraftLayerRef = useRef(null)
  const trackedLayerRef  = useRef(null)
  const tfrLayerRef      = useRef(null)
  const [acCount,  setAcCount]  = useState(0)
  const [tfrCount, setTfrCount] = useState(0)
  const [error,    setError]    = useState(null)
  const [acItems,  setAcItems]  = useState([])
  const [tfrExtra, setTfrExtra] = useState([])

  const { entries: watchEntries, callsignSet, hexSet } = useWatchlist()

  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, { center: KDCA, zoom: 8, zoomControl: true })

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      className: 'map-tiles',
    }).addTo(map)

    map._airspaceLayers = []
    Object.values(AIRSPACE).forEach(({ radius, color, label }) => {
      const c = L.circle(KDCA, { radius, color, weight: 1, fill: false, dashArray: '4 6', opacity: 0.5 })
        .addTo(map).bindTooltip(label, { permanent: false })
      map._airspaceLayers.push(c)
    })

    map._ringLayers = []
    RANGE_RINGS_NM.forEach(nm => {
      const r = L.circle(KDCA, { radius: nm * NM_TO_M, color: '#2a3f6f', weight: 1, fill: false, dashArray: '2 8', opacity: 0.4 })
        .addTo(map)
      map._ringLayers.push(r)
    })

    L.circleMarker(KDCA, { radius: 5, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map).bindTooltip('KDCA', { permanent: true, className: 'airport-label' })

    aircraftLayerRef.current = L.layerGroup().addTo(map)
    trackedLayerRef.current  = L.layerGroup().addTo(map)   // tracked on top
    tfrLayerRef.current      = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  const refreshAircraft = useCallback(async () => {
    if (!aircraftLayerRef.current) return
    try {
      const url = adsbMode === 'local' ? '/api/adsb/local' : '/api/adsb/live'
      const r = await fetch(url)
      if (!r.ok) throw new Error(r.statusText)
      const data = await r.json()
      const aircraft = data.aircraft || data.ac || []
      aircraftLayerRef.current.clearLayers()
      trackedLayerRef.current.clearLayers()
      let count = 0
      aircraft.forEach(ac => {
        if (!ac.lat || !ac.lon) return
        const alt = ac.alt_baro ?? ac.altitude ?? 0
        if (ac.ground || ac.gnd || alt === 'ground' || alt < 100) return
        const callsign = (ac.flight || '').trim() || ac.hex || '?'
        const hdg = ac.track || ac.heading || 0
        const spd = ac.gs ?? ac.speed ?? '?'
        const isTracked = callsignSet.has(callsign.toUpperCase()) || hexSet.has((ac.hex || '').toLowerCase())

        if (isTracked) {
          // Tracked aircraft go in their own layer (rendered above all others)
          L.marker([ac.lat, ac.lon], { icon: trackedIcon(hdg), interactive: true, zIndexOffset: 2000 })
            .addTo(trackedLayerRef.current)
            .bindTooltip(trackedTooltipHtml(callsign, alt, spd, hdg), {
              className: 'ac-tooltip tracked-tooltip',
              permanent: true,
              direction: 'top',
            })
        } else {
          L.marker([ac.lat, ac.lon], { icon: headingIcon(hdg), interactive: true })
            .addTo(aircraftLayerRef.current)
            .bindTooltip(
              `<b>${callsign}</b><br/>Alt: ${alt}ft Spd: ${spd}kt Hdg: ${Math.round(hdg)}°`,
              { className: 'ac-tooltip' }
            )
        }
        count++
      })
      setAcCount(count)
      const compassItems = aircraft
        .filter(ac => ac.lat && ac.lon)
        .map(ac => {
          const callsign = (ac.flight || '').trim() || ac.hex || '?'
          const isTracked = callsignSet.has(callsign.toUpperCase()) || hexSet.has((ac.hex || '').toLowerCase())
          return { lat: ac.lat, lon: ac.lon, label: callsign, tracked: isTracked }
        })
      setAcItems(compassItems)
      setError(null)
    } catch (e) { setError(`ADS-B: ${e.message}`) }
  }, [adsbMode, callsignSet, hexSet])

  const refreshTfrs = useCallback(async () => {
    if (!tfrLayerRef.current) return
    try {
      const r = await fetch('/api/dispatch/api/v1/tfr')
      if (!r.ok) return
      const tfrs = await r.json()
      if (!Array.isArray(tfrs)) return
      tfrLayerRef.current.clearLayers()
      tfrs.forEach(tfr => {
        const lat = tfr.center_lat, lon = tfr.center_lon
        if (!lat || !lon) return
        const radius_m = tfr.radius_nm ? tfr.radius_nm * NM_TO_M : 9260
        const color = tfr.is_vip ? '#ff3131' : '#ff6b35'
        L.circle([lat, lon], { radius: radius_m, color, weight: tfr.is_vip ? 2 : 1, fill: true, fillOpacity: 0.12 })
          .addTo(tfrLayerRef.current)
          .bindTooltip(`${tfr.is_vip ? 'VIP TFR: ' : 'TFR: '}${tfr.tfr_id}`, { className: 'tfr-tooltip' })
      })
      setTfrCount(tfrs.length)
      const extras = tfrs.filter(t => t.is_vip).map(t => `VIP TFR: ${t.tfr_id}`)
      setTfrExtra(extras)
    } catch (_) {}
  }, [])

  useEffect(() => {
    refreshAircraft()
    const id = setInterval(refreshAircraft, 10_000)
    return () => clearInterval(id)
  }, [refreshAircraft])

  useEffect(() => {
    refreshTfrs()
    const id = setInterval(refreshTfrs, 60_000)
    return () => clearInterval(id)
  }, [refreshTfrs])

  useEffect(() => {
    if (liveState?.tfr_count !== undefined) setTfrCount(liveState.tfr_count)
  }, [liveState])

  // Sync Leaflet layer visibility to config toggles
  useEffect(() => {
    const map = leafletRef.current
    if (!map) return
    map._airspaceLayers?.forEach(c =>
      layers.airspace !== false ? map.addLayer(c) : map.removeLayer(c)
    )
    map._ringLayers?.forEach(r =>
      layers.rings !== false ? map.addLayer(r) : map.removeLayer(r)
    )
    if (aircraftLayerRef.current) {
      layers.localFeed !== false
        ? map.addLayer(aircraftLayerRef.current)
        : map.removeLayer(aircraftLayerRef.current)
    }
    // "tracked" layer follows the localFeed toggle (they're part of the same feed)
    if (trackedLayerRef.current) {
      layers.localFeed !== false
        ? map.addLayer(trackedLayerRef.current)
        : map.removeLayer(trackedLayerRef.current)
    }
    if (tfrLayerRef.current) {
      layers.tfr !== false
        ? map.addLayer(tfrLayerRef.current)
        : map.removeLayer(tfrLayerRef.current)
    }
  }, [layers.airspace, layers.rings, layers.localFeed, layers.tfr])

  const compassSummary = useCompassSummary(acItems, tfrExtra)

  const acTableRows = acItems.map(ac => ({
    callsign: ac.label,
    lat:      ac.lat?.toFixed(4),
    lon:      ac.lon?.toFixed(4),
    tracked:  ac.tracked ? '★' : '',
  }))

  const trackedCount = acItems.filter(a => a.tracked).length

  return (
    <div className="map-with-sidebar">
      <LayerSidebar />
      <div className="map-container">
        <AriaCompassRegion
          summary={compassSummary}
          entityType="aircraft"
          count={acCount}
          extra={tfrCount > 0 ? `${tfrCount} active TFR${tfrCount !== 1 ? 's' : ''}.` : ''}
        />
        <AccessibleTable
          id="local-ac-table"
          caption={`Aircraft within range — ${acCount} visible`}
          columns={[
            { key: 'callsign', label: 'Callsign' },
            { key: 'lat',      label: 'Latitude'  },
            { key: 'lon',      label: 'Longitude' },
            { key: 'tracked',  label: '★'          },
          ]}
          rows={acTableRows}
          emptyMsg="No aircraft currently visible."
        />
        <div ref={mapRef} className="leaflet-map" />
        <div className="map-overlay-stats">
          <span className="stat">{acCount} AC</span>
          {trackedCount > 0 && (
            <span className="stat tracked-stat">★ {trackedCount} TRACKED</span>
          )}
          <span className="stat">{tfrCount} TFR{tfrCount !== 1 ? 's' : ''}</span>
          {error && <span className="stat error">{error}</span>}
          <span className={`stat source-badge ${adsbMode}`}>
            {adsbMode === 'local' ? 'LOCAL' : 'LIVE (airplanes.live)'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ── Root: pick globe vs local ─────────────────────────────────────────────
export default function MapView({ adsbMode, liveState }) {
  return (
    <div className="train-map-view">
      {adsbMode === 'globe'
        ? <GlobeMap liveState={liveState} />
        : <LocalMap adsbMode={adsbMode} liveState={liveState} />}
    </div>
  )
}
