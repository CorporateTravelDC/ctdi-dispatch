import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'
import AriaCompassRegion from './AriaCompassRegion.jsx'
import AccessibleTable   from './AccessibleTable.jsx'
import UpcomingFeatureWatermark from './UpcomingFeatureWatermark.jsx'
import { useCompassSummary } from '../hooks/useCompassSummary.js'
import { useWatchlist, FALLBACK_PLANE_SVG } from '../hooks/useWatchlist.js'
import { useDemoStatus } from '../hooks/useDemoStatus.js'
import { useReceiverLocation } from '../hooks/useReceiverLocation.js'
import { useVisibilityAwareInterval } from '../hooks/useVisibilityAwareInterval.js'

// 2026-08-30: modeled on AisMapView.jsx, deliberately simplified -- there
// is no third-party embed equivalent to MarineTraffic for drone/UAS
// tracking, so this is local-map-only, no iframe/mode toggle. Backend is
// /api/utm/drones (mirrors /api/ais/vessels' fallback-chain shape) --
// see that route's own docstring: no local OpenDroneID receiver or USS
// API exists on this box yet, so this will render the honest empty state
// (source:"none") until one is configured. The map/overlay/table
// machinery is fully real and wired now so nothing else needs touching
// once a receiver or USS API key exists.
const OSM_URL  = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTR = '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors'

const DEFAULT_CENTER = [38.9, -76.8]
const DEFAULT_ZOOM   = 9
const DRONE_POLL      = 15_000  // faster than AIS's 60s -- UAS positions move fast at low altitude

function droneIcon(isTracked) {
  const color  = isTracked ? '#00d4ff' : '#ff9100'
  const stroke = isTracked ? '#003a4a' : '#111'
  const glow   = isTracked ? 'filter:drop-shadow(0 0 4px #00d4ff);' : ''
  return L.divIcon({
    className: '',
    html: `<div style="width:12px;height:12px;${glow}">
      <svg viewBox="0 0 12 12" width="12" height="12">
        <circle cx="6" cy="6" r="5" fill="${color}" stroke="${stroke}" stroke-width="1.2" opacity="0.95"/>
        <circle cx="6" cy="6" r="1.4" fill="#fff" opacity="0.85"/>
      </svg>
    </div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })
}

function sourceLabel(source) {
  if (source === 'local') return 'OpenDroneID receiver (local)'
  if (source === 'uss')   return 'USS API'
  return null
}

export default function UtmMapView() {
  const mapRef         = useRef(null)
  const leafletRef     = useRef(null)
  const osmLayerRef    = useRef(null)
  const droneLayerRef  = useRef(null)
  const trackedLayerRef = useRef(null)

  const [droneCount,   setDroneCount]   = useState(0)
  const [trackedCount, setTrackedCount] = useState(0)
  const [dataSource,   setDataSource]   = useState('none')
  const [loadErr,      setLoadErr]      = useState(false)
  const [droneItems,   setDroneItems]   = useState([])

  const { entries: watchEntries } = useWatchlist()
  const [demoStatus] = useDemoStatus()
  // Same isDemo posture as AisMapView -- withhold the live fetch until we
  // positively know this isn't an untrusted demo visitor.
  const isDemo = demoStatus === null ||
    (demoStatus.demo_mode === true && demoStatus.trusted_origin !== true)

  const droneIdSet = new Set()
  watchEntries
    .filter(e => e.entry_type === 'drone')
    .forEach(e => { if (e.identifier) droneIdSet.add(e.identifier.toUpperCase()) })

  useEffect(() => {
    if (isDemo) return
    if (leafletRef.current) return
    if (!mapRef.current) return
    const map = L.map(mapRef.current, {
      center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true,
    })
    osmLayerRef.current = L.tileLayer(OSM_URL, { attribution: OSM_ATTR, className: 'map-tiles' }).addTo(map)
    droneLayerRef.current   = L.layerGroup().addTo(map)
    trackedLayerRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [isDemo])

  const refreshDrones = useCallback(async () => {
    if (isDemo) return
    if (!droneLayerRef.current || !trackedLayerRef.current) return
    try {
      const r = await fetch('/api/utm/drones')
      if (!r.ok) { setLoadErr(true); return }
      const { source, drones } = await r.json()

      droneLayerRef.current.clearLayers()
      trackedLayerRef.current.clearLayers()

      let count = 0, tCount = 0
      const items = []

      ;(drones || []).forEach(d => {
        if (d.lat == null || d.lon == null) return
        const uasId = d.uas_id || '?'
        const alt   = d.alt_m    != null ? `${d.alt_m} m`   : '—'
        const spd   = d.speed_ms != null ? `${d.speed_ms} m/s` : '—'
        const isTracked = droneIdSet.has(String(uasId).toUpperCase())
        const icon = droneIcon(isTracked)

        const tip = isTracked
          ? `<div class="ac-tooltip-tracked">
               <img src="${FALLBACK_PLANE_SVG}" class="ac-logo ac-logo-fallback" alt="drone"/>
               <div class="ac-tooltip-tracked-info">
                 <span class="ac-tracked-badge">★ TRACKED</span>
                 <b class="ac-tracked-callsign">${uasId}</b>
                 <span class="ac-tracked-details">Alt: ${alt} · Speed: ${spd}</span>
               </div>
             </div>`
          : `<b style="color:#ff9100">${uasId}</b><br/>Alt: ${alt} · Speed: ${spd}`

        L.marker([d.lat, d.lon], {
          icon, interactive: true, zIndexOffset: isTracked ? 2000 : 0,
        })
        .bindTooltip(tip, {
          className: isTracked ? 'ac-tooltip tracked-tooltip' : 'ac-tooltip',
          permanent: isTracked, direction: 'top', sticky: !isTracked,
        })
        .addTo(isTracked ? trackedLayerRef.current : droneLayerRef.current)

        count++
        if (isTracked) tCount++
        items.push({ lat: d.lat, lon: d.lon, label: uasId, tracked: isTracked })
      })

      setDroneCount(count)
      setTrackedCount(tCount)
      setDataSource(source || 'none')
      setDroneItems(items)
      setLoadErr(false)
    } catch (_) { setLoadErr(true) }
  }, [droneIdSet.size])

  useVisibilityAwareInterval(refreshDrones, DRONE_POLL, !isDemo)

  const src = sourceLabel(dataSource)
  const receiverLoc = useReceiverLocation()
  const compassSummary = useCompassSummary(droneItems, [], receiverLoc)
  const droneTableRows = droneItems.map(d => ({
    id: d.label, lat: d.lat?.toFixed(4), lon: d.lon?.toFixed(4), tracked: d.tracked ? '★' : '',
  }))

  return (
    <div className="train-map-view">
      <div className="train-map-subnav">
        <span className="train-map-title">UTM</span>
      </div>

      {isDemo ? (
        <div className="globe-iframe-wrap ais-demo-placeholder-wrap">
          <div className="ais-demo-placeholder-img" style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'var(--muted)', fontSize: '0.85rem', textAlign: 'center', padding: '2rem',
          }}>
            UTM / drone Remote ID tracking — upcoming feature, shown for
            demonstration purposes only. Live drone positions are not
            shown in demo mode.
          </div>
        </div>
      ) : (
      <div className="globe-iframe-wrap">
        <AriaCompassRegion summary={compassSummary} entityType="drones" count={droneCount}
          extra="Part 107 UAS / Remote ID, DC-area coverage once a receiver is configured." />
        <AccessibleTable
          id="utm-drone-table"
          caption={`Drones in range — ${droneCount} visible`}
          columns={[
            { key: 'id', label: 'UAS ID' }, { key: 'lat', label: 'Latitude' },
            { key: 'lon', label: 'Longitude' }, { key: 'tracked', label: '★' },
          ]}
          rows={droneTableRows}
          emptyMsg={dataSource === 'none'
            ? 'No UTM source configured yet — no local receiver or USS API wired in.'
            : 'No drones currently visible.'}
        />

        <div ref={mapRef} className="ais-leaflet-layer ais-local-mode" />

        {dataSource === 'none' && (
          <UpcomingFeatureWatermark
            label="UTM / Drone Remote ID"
            detail="No local receiver or USS API configured yet"
          />
        )}

        <div className="map-overlay-stats globe-stats">
          {dataSource === 'none'
            ? <span className="stat source-badge" style={{ color: 'var(--muted)' }}>No UTM source configured</span>
            : droneCount > 0
              ? <span className="stat source-badge" style={{ color: '#ff9100' }}>{droneCount} drones</span>
              : <span className="stat source-badge" style={{ color: 'var(--muted)' }}>No live UTM data</span>}
          {trackedCount > 0 && <span className="stat tracked-stat">★ {trackedCount} TRACKED</span>}
          {src && <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>{src}</span>}
          {loadErr && <span className="stat error">UTM feed error</span>}
          <button className="intel-refresh-btn" onClick={refreshDrones} title="Refresh drones">↻</button>
        </div>
      </div>
      )}
    </div>
  )
}
