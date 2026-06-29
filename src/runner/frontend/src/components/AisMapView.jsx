import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'
import AriaCompassRegion from './AriaCompassRegion.jsx'
import AccessibleTable   from './AccessibleTable.jsx'
import { useCompassSummary } from '../hooks/useCompassSummary.js'
import { useWatchlist, FALLBACK_PLANE_SVG } from '../hooks/useWatchlist.js'

const OSM_URL          = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTR         = '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors'
const OSM_NAUTICAL_URL = 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png'
const OSM_NAUTICAL_ATTR = '&copy; <a href="https://www.openseamap.org">OpenSeaMap</a>'

const DEFAULT_CENTER   = [38.9, -76.8]
const DEFAULT_ZOOM     = 8
const VESSEL_POLL      = 60_000
const MARINETRAFFIC_URL = 'https://www.marinetraffic.com/en/ais/home/centerx:-76.8/centery:38.9/zoom:8'
const TRACKER_LINKS = [
  { label: 'MarineTraffic ↗', url: MARINETRAFFIC_URL },
  { label: 'VesselFinder ↗',  url: 'https://www.vesselfinder.com/?lat=38.9&lng=-76.8&zoom=8' },
  { label: 'AIS Marine ↗',    url: 'https://www.aismarine.com/ais-map/' },
]

function vesselColor(nav_status) {
  const s = nav_status ?? 15
  if (s === 1 || s === 5) return '#ffd700'
  if (s === 0 || s === 8) return '#4a9eff'
  if (s >= 2 && s <= 4)   return '#ff9100'
  return '#888'
}

function vesselIcon(nav_status, cog, isTracked) {
  const color  = isTracked ? '#00d4ff' : vesselColor(nav_status)
  const stroke = isTracked ? '#003a4a' : '#111'
  const glow   = isTracked ? 'filter:drop-shadow(0 0 4px #00d4ff);' : ''
  const deg    = (cog != null && cog >= 0) ? cog : 0
  return L.divIcon({
    className: '',
    html: `<div style="transform:rotate(${deg}deg);width:12px;height:18px;${glow}">
      <svg viewBox="0 0 12 18" width="12" height="18">
        <polygon points="6,0 12,14 6,18 0,14"
          fill="${color}" stroke="${stroke}" stroke-width="1.2" opacity="0.95"/>
        ${isTracked ? '<polygon points="6,0 12,14 6,18 0,14" fill="none" stroke="#fff" stroke-width="0.4" opacity="0.6"/>' : ''}
      </svg>
    </div>`,
    iconSize: [12, 18],
    iconAnchor: [6, 9],
  })
}

function sourceLabel(source) {
  if (source === 'local')             return 'AIS-catcher (local)'
  if (source === 'marinetraffic.com') return 'MarineTraffic API'
  if (source === 'aishub.net')        return 'AISHub'
  return null
}

export default function AisMapView() {
  const mapRef          = useRef(null)
  const leafletRef      = useRef(null)
  const osmLayerRef     = useRef(null)
  const nautLayerRef    = useRef(null)
  const vesselLayerRef  = useRef(null)
  const trackedLayerRef = useRef(null)

  // 'iframe': MarineTraffic bg + transparent Leaflet vessel overlay
  // 'local' : full OSM + OpenSeaMap Leaflet
  const [mode,         setMode]        = useState('iframe')
  const [iframeError,  setIframeError] = useState(false)
  const [vesselCount,  setVesselCount] = useState(0)
  const [trackedCount, setTrackedCount]= useState(0)
  const [dataSource,   setDataSource]  = useState('none')
  const [loadErr,      setLoadErr]     = useState(false)
  const [vesselItems,  setVesselItems] = useState([])

  const { entries: watchEntries } = useWatchlist()

  const mmsiSet = new Set()
  watchEntries.forEach(e => {
    const m = e.notes?.match(/mmsi:\s*(\d+)/i)
    if (m) mmsiSet.add(m[1])
    if (e.identifier && /^\d{9}$/.test(e.identifier)) mmsiSet.add(e.identifier)
  })

  // Init Leaflet — single instance, tile layers toggled by mode
  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, {
      center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true,
    })
    osmLayerRef.current  = L.tileLayer(OSM_URL, { attribution: OSM_ATTR, className: 'map-tiles' })
    nautLayerRef.current = L.tileLayer(OSM_NAUTICAL_URL, {
      attribution: OSM_NAUTICAL_ATTR, maxZoom: 18, opacity: 0.85,
    })
    // Tiles start hidden — iframe mode is default
    vesselLayerRef.current  = L.layerGroup().addTo(map)
    trackedLayerRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  // Toggle interaction and tile visibility when mode changes
  useEffect(() => {
    const map = leafletRef.current
    if (!map) return
    if (mode === 'local') {
      if (!map.hasLayer(osmLayerRef.current))  map.addLayer(osmLayerRef.current)
      if (!map.hasLayer(nautLayerRef.current)) map.addLayer(nautLayerRef.current)
      map.dragging.enable()
      map.scrollWheelZoom.enable()
      map.touchZoom.enable()
    } else {
      if (map.hasLayer(osmLayerRef.current))  map.removeLayer(osmLayerRef.current)
      if (map.hasLayer(nautLayerRef.current)) map.removeLayer(nautLayerRef.current)
      map.dragging.disable()
      map.scrollWheelZoom.disable()
      map.touchZoom.disable()
    }
  }, [mode])

  const refreshVessels = useCallback(async () => {
    if (!vesselLayerRef.current || !trackedLayerRef.current) return
    try {
      const r = await fetch('/api/ais/vessels')
      if (!r.ok) { setLoadErr(true); return }
      const { source, vessels } = await r.json()

      vesselLayerRef.current.clearLayers()
      trackedLayerRef.current.clearLayers()

      let count = 0, tCount = 0
      const items = []

      ;(vessels || []).forEach(v => {
        if (v.lat == null || v.lon == null) return
        const name = v.name || `MMSI ${v.mmsi || '?'}`
        const spd  = v.sog  != null ? `${v.sog} kt` : '—'
        const cog  = v.cog  != null ? `${v.cog}°`   : '—'
        const type = v.ship_type != null ? ` · Type ${v.ship_type}` : ''
        const isTracked = mmsiSet.has(String(v.mmsi || ''))
        const icon = vesselIcon(v.nav_status, v.cog ?? v.hdg, isTracked)

        const tip = isTracked
          ? `<div class="ac-tooltip-tracked">
               <img src="${FALLBACK_PLANE_SVG}" class="ac-logo ac-logo-fallback" alt="vessel"/>
               <div class="ac-tooltip-tracked-info">
                 <span class="ac-tracked-badge">★ TRACKED</span>
                 <b class="ac-tracked-callsign">${name}</b>
                 <span class="ac-tracked-details">SOG: ${spd} · COG: ${cog}${type}</span>
               </div>
             </div>`
          : `<b style="color:#4a9eff">${name}</b>`
            + (v.mmsi ? `<br/>MMSI: ${v.mmsi}` : '')
            + `<br/>SOG: ${spd} · COG: ${cog}${type}`

        L.marker([v.lat, v.lon], {
          icon, interactive: true, zIndexOffset: isTracked ? 2000 : 0,
        })
        .bindTooltip(tip, {
          className: isTracked ? 'ac-tooltip tracked-tooltip' : 'ac-tooltip',
          permanent: isTracked, direction: 'top', sticky: !isTracked,
        })
        .addTo(isTracked ? trackedLayerRef.current : vesselLayerRef.current)

        count++
        if (isTracked) tCount++
        items.push({ lat: v.lat, lon: v.lon, label: name, tracked: isTracked })
      })

      setVesselCount(count)
      setTrackedCount(tCount)
      setDataSource(source || 'none')
      setVesselItems(items)
      setLoadErr(false)
    } catch (_) { setLoadErr(true) }
  }, [mmsiSet.size])

  useEffect(() => {
    refreshVessels()
    const id = setInterval(refreshVessels, VESSEL_POLL)
    return () => clearInterval(id)
  }, [refreshVessels])

  const handleIframeError = () => {
    setIframeError(true)
    setMode('local')
  }

  const src = sourceLabel(dataSource)
  const compassSummary = useCompassSummary(vesselItems, [])
  const vesselTableRows = vesselItems.map(v => ({
    name: v.label, lat: v.lat?.toFixed(4), lon: v.lon?.toFixed(4), tracked: v.tracked ? '★' : '',
  }))

  return (
    <div className="train-map-view">
      <div className="train-map-subnav">
        <span className="train-map-title">AIS</span>

        <div className="ais-mode-toggle" role="group" aria-label="AIS display mode">
          <button className={`ais-mode-btn${mode === 'iframe' ? ' active' : ''}`}
            onClick={() => setMode('iframe')} aria-pressed={mode === 'iframe'}
            disabled={iframeError} title={iframeError ? 'MarineTraffic blocked cross-origin embed' : ''}>
            🌐 LIVE</button>
          <button className={`ais-mode-btn${mode === 'local' ? ' active' : ''}`}
            onClick={() => setMode('local')} aria-pressed={mode === 'local'}>
            ⚓ MAP</button>
        </div>

        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
          {TRACKER_LINKS.map(t => (
            <a key={t.label} href={t.url} target="_blank" rel="noopener noreferrer"
               className="train-map-external">{t.label}</a>
          ))}
        </span>
      </div>

      {/* Map area: iframe bg + transparent Leaflet overlay (iframe mode)
                   OR full tiled Leaflet (local mode)  */}
      <div className="globe-iframe-wrap">
        <AriaCompassRegion summary={compassSummary} entityType="vessels" count={vesselCount}
          extra="Potomac · Chesapeake · Port of Baltimore." />
        <AccessibleTable
          id="ais-vessel-table"
          caption={`Vessels in range — ${vesselCount} visible`}
          columns={[
            { key: 'name', label: 'Vessel' }, { key: 'lat', label: 'Latitude' },
            { key: 'lon', label: 'Longitude' }, { key: 'tracked', label: '★' },
          ]}
          rows={vesselTableRows}
          emptyMsg="No vessels currently visible."
        />

        {/* MarineTraffic iframe background — behind transparent Leaflet */}
        {mode === 'iframe' && !iframeError && (
          <iframe
            src={MARINETRAFFIC_URL}
            className="globe-iframe"
            title="MarineTraffic live AIS"
            referrerPolicy="no-referrer"
            onError={handleIframeError}
            allow="fullscreen"
          />
        )}

        {/* Iframe blocked fallback — full links */}
        {mode === 'local' && iframeError && (
          <div className="ais-iframe-fallback-banner">
            MarineTraffic blocked embed.{' '}
            {TRACKER_LINKS.map(t => (
              <a key={t.label} href={t.url} target="_blank" rel="noopener noreferrer"
                 className="ais-tracker-link">{t.label}</a>
            ))}
          </div>
        )}

        {/* Leaflet: transparent overlay in iframe mode, full tiled in local mode */}
        <div
          ref={mapRef}
          className={`ais-leaflet-layer${mode === 'iframe' ? ' ais-overlay-mode' : ' ais-local-mode'}`}
        />

        {/* No local AIS feed notice (local mode only) */}
        {mode === 'local' && !src && vesselCount === 0 && !loadErr && (
          <div className="ais-no-data-overlay">
            <div className="ais-no-data-title">No live AIS feed</div>
            <div className="ais-no-data-sub">
              Deploy AIS-catcher on the Pi (port 8110), then set
              <code> AIS_CATCHER_URL=http://127.0.0.1:8110</code> in dispatch.env.
            </div>
            <div className="ais-no-data-links">
              {TRACKER_LINKS.map(t => (
                <a key={t.label} href={t.url} target="_blank" rel="noopener noreferrer"
                   className="ais-tracker-link">{t.label}</a>
              ))}
            </div>
          </div>
        )}

        <div className="map-overlay-stats globe-stats">
          {vesselCount > 0
            ? <span className="stat source-badge" style={{ color: '#4a9eff' }}>{vesselCount} vessels</span>
            : mode === 'iframe'
              ? <span className="stat source-badge" style={{ color: 'var(--muted)' }}>no local AIS</span>
              : <span className="stat source-badge" style={{ color: 'var(--muted)' }}>No live AIS</span>
          }
          {trackedCount > 0 && <span className="stat tracked-stat">★ {trackedCount} TRACKED</span>}
          {src && <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>{src}</span>}
          {mode === 'iframe' && !iframeError && (
            <span className="ais-iframe-badge">MarineTraffic</span>
          )}
          {loadErr && <span className="stat error">AIS feed error</span>}
          <button className="intel-refresh-btn" onClick={refreshVessels} title="Refresh vessels">↻</button>
        </div>
      </div>
    </div>
  )
}
