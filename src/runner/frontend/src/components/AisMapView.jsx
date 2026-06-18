import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'

// ── Tile sources ──────────────────────────────────────────────────
const OSM_URL   = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTR  = '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors'
// OpenSeaMap seamark overlay — harbours, buoys, depth contours, channels
const OSM_NAUTICAL_URL  = 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png'
const OSM_NAUTICAL_ATTR = '&copy; <a href="https://www.openseamap.org">OpenSeaMap</a> contributors'

// External trackers — open in new tab when no live feed is active
const TRACKERS = [
  { label: 'MarineTraffic', url: 'https://www.marinetraffic.com/en/ais/home/centerx:-76.8/centery:38.9/zoom:8' },
  { label: 'VesselFinder',  url: 'https://www.vesselfinder.com/?lat=38.9&lng=-76.8&zoom=8' },
  { label: 'ShipFinder',    url: 'https://shipfinder.co/?lat=38.9&lon=-76.8&zoom=8' },
]

// Default center: Chesapeake Bay / Potomac / DC waterways
const DEFAULT_CENTER = [38.9, -76.8]
const DEFAULT_ZOOM   = 8
const VESSEL_POLL    = 60_000  // 1 minute

// ── Nav-status color coding ───────────────────────────────────────
// ITU NAVSTAT values 0-15
function vesselColor(nav_status) {
  const s = nav_status ?? 15
  if (s === 1 || s === 5) return '#ffd700'   // anchored / moored — yellow
  if (s === 0 || s === 8) return '#00d4ff'   // underway engine / sailing — cyan
  if (s >= 2 && s <= 4)   return '#ff9100'   // constrained — orange
  return '#888'                               // not defined / unknown
}

function vesselIcon(nav_status) {
  const color = vesselColor(nav_status)
  return L.divIcon({
    className: '',
    html: `<div style="
      width:8px;height:8px;border-radius:2px;transform:rotate(45deg);
      background:${color};border:1.5px solid #000;
      box-shadow:0 0 5px ${color};
    "></div>`,
    iconSize: [8, 8],
    iconAnchor: [4, 4],
  })
}

// ── Vessel data fetch ─────────────────────────────────────────────
async function fetchVessels() {
  try {
    const r = await fetch('/api/ais/vessels')
    if (!r.ok) return { source: 'none', vessels: [] }
    return await r.json()
  } catch {
    return { source: 'none', vessels: [] }
  }
}

// ── Source badge label ────────────────────────────────────────────
function sourceLabel(source) {
  if (source === 'local')           return 'AIS-catcher (local)'
  if (source === 'marinetraffic.com') return 'MarineTraffic API'
  if (source === 'aishub.net')      return 'AISHub'
  return null
}

export default function AisMapView() {
  const mapRef        = useRef(null)
  const leafletRef    = useRef(null)
  const vesselLayerRef = useRef(null)

  const [vesselCount, setVesselCount] = useState(0)
  const [dataSource,  setDataSource]  = useState('none')
  const [loadErr,     setLoadErr]     = useState(false)

  // ── Init map ────────────────────────────────────────────────────
  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, {
      center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true,
    })

    L.tileLayer(OSM_URL, { attribution: OSM_ATTR, className: 'map-tiles' }).addTo(map)
    L.tileLayer(OSM_NAUTICAL_URL, {
      attribution: OSM_NAUTICAL_ATTR, maxZoom: 18, opacity: 0.9,
    }).addTo(map)

    vesselLayerRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  // ── Refresh vessel overlay ──────────────────────────────────────
  const refreshVessels = useCallback(async () => {
    if (!vesselLayerRef.current) return
    try {
      const { source, vessels } = await fetchVessels()
      vesselLayerRef.current.clearLayers()

      ;(vessels || []).forEach(v => {
        if (v.lat == null || v.lon == null) return
        const icon  = vesselIcon(v.nav_status)
        const name  = v.name || `MMSI ${v.mmsi || '?'}`
        const speed = v.sog != null ? `${v.sog} kts` : '—'
        const cog   = v.cog != null ? `${v.cog}°` : '—'
        const type  = v.ship_type != null ? ` · Type ${v.ship_type}` : ''

        L.marker([v.lat, v.lon], { icon })
          .bindTooltip(
            `<b style="color:#00d4ff">${name}</b>` +
            (v.mmsi ? `<br/>MMSI: ${v.mmsi}` : '') +
            `<br/>SOG: ${speed} · COG: ${cog}${type}`,
            { className: 'ac-tooltip', sticky: true }
          )
          .addTo(vesselLayerRef.current)
      })

      setVesselCount((vessels || []).filter(v => v.lat != null).length)
      setDataSource(source || 'none')
      setLoadErr(false)
    } catch {
      setLoadErr(true)
    }
  }, [])

  useEffect(() => {
    refreshVessels()
    const id = setInterval(refreshVessels, VESSEL_POLL)
    return () => clearInterval(id)
  }, [refreshVessels])

  const src = sourceLabel(dataSource)

  return (
    <div className="train-map-view">
      {/* Sub-nav */}
      <div className="train-map-subnav">
        <span className="train-map-title">AIS</span>
        <span className="stat source-badge" style={{ color: 'var(--cyan)' }}>
          {src ? src : 'OpenSeaMap nautical chart'}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
          {TRACKERS.map(t => (
            <a key={t.label} href={t.url} target="_blank" rel="noopener noreferrer"
               className="train-map-external" title={`Open ${t.label}`}>
              {t.label} ↗
            </a>
          ))}
        </span>
      </div>

      {/* Map */}
      <div className="map-container">
        <div ref={mapRef} className="leaflet-map" />

        {/* Overlay stats */}
        <div className="map-overlay-stats">
          {vesselCount > 0 ? (
            <span className="stat source-badge" style={{ color: '#00d4ff' }}>
              {vesselCount} vessels
            </span>
          ) : (
            <span className="stat source-badge" style={{ color: 'var(--muted)' }}>
              No live AIS feed
            </span>
          )}
          {loadErr && (
            <span className="stat" style={{ color: 'var(--nogo)', fontSize: '0.6rem' }}>
              AIS feed error
            </span>
          )}
          <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>
            Potomac · Chesapeake · Port of Baltimore
          </span>
          <button className="intel-refresh-btn" onClick={refreshVessels}
                  title="Refresh vessel positions" style={{ marginLeft: '0.5rem' }}>↻</button>
        </div>

        {/* No-data overlay with external links */}
        {!src && vesselCount === 0 && !loadErr && (
          <div className="ais-no-data-overlay">
            <div className="ais-no-data-title">No live AIS feed configured</div>
            <div className="ais-no-data-sub">
              Connect local AIS-catcher hardware, or add an API key in dispatch.env
            </div>
            <div className="ais-no-data-links">
              {TRACKERS.map(t => (
                <a key={t.label} href={t.url} target="_blank" rel="noopener noreferrer"
                   className="ais-tracker-link">
                  {t.label} ↗
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
