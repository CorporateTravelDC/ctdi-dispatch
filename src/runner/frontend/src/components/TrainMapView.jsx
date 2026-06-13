import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState } from 'react'

// ── Map source definitions ────────────────────────────────────────────────
// trains.fyi: NEC / North America real-time positions — no key required
const TRAINS_FYI_URL = 'https://trains.fyi/'

// OpenRailwayMap: global infrastructure overlay on OSM base
// https://www.openrailwaymap.org/ tile service — no API key required
const ORM_TILES = {
  base: {
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors',
    name: 'OSM Base',
  },
  rail: {
    url: 'https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openrailwaymap.org/">OpenRailwayMap</a> CC-BY-SA',
    name: 'OpenRailwayMap',
    maxZoom: 19,
    subdomains: 'abc',
  },
}

// DC area default centre
const KDCA = [38.8521, -77.0377]

// ── trains.fyi iframe panel ───────────────────────────────────────────────
function TrainsFyiView() {
  const [err, setErr] = useState(false)
  return (
    <div className="train-map-wrap">
      {err ? (
        <div className="globe-fallback">
          <p>trains.fyi blocked cross-origin embedding.</p>
          <a href="https://trains.fyi/" target="_blank" rel="noopener noreferrer"
             className="globe-fallback-link">Open trains.fyi ↗</a>
        </div>
      ) : (
        <iframe
          src={TRAINS_FYI_URL}
          className="globe-iframe"
          title="trains.fyi — NEC / North America real-time train positions"
          referrerPolicy="no-referrer"
          onError={() => setErr(true)}
          allow="fullscreen"
        />
      )}
      <div className="map-overlay-stats globe-stats">
        <span className="stat source-badge" style={{ color: 'var(--go)' }}>
          trains.fyi — NEC / North America
        </span>
        <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>
          Acela · NE Regional · commuter rail
        </span>
      </div>
    </div>
  )
}

// ── OpenRailwayMap Leaflet panel ──────────────────────────────────────────
function OpenRailwayMapView() {
  const mapRef    = useRef(null)
  const leafletRef = useRef(null)

  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, { center: KDCA, zoom: 6, zoomControl: true })

    L.tileLayer(ORM_TILES.base.url, {
      attribution: ORM_TILES.base.attribution,
      className: 'map-tiles',
    }).addTo(map)

    L.tileLayer(ORM_TILES.rail.url, {
      attribution: ORM_TILES.rail.attribution,
      maxZoom: ORM_TILES.rail.maxZoom,
      subdomains: ORM_TILES.rail.subdomains,
      opacity: 0.85,
    }).addTo(map)

    // KDCA reference marker
    L.circleMarker(KDCA, { radius: 4, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map).bindTooltip('KDCA / DC area', { permanent: true, className: 'airport-label' })

    leafletRef.current = map
  }, [])

  return (
    <div className="map-container">
      <div ref={mapRef} className="leaflet-map" />
      <div className="map-overlay-stats">
        <span className="stat source-badge" style={{ color: 'var(--cyan)' }}>
          OpenRailwayMap — global infrastructure
        </span>
        <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>
          OSM base · ORM rail overlay · no real-time positions
        </span>
      </div>
    </div>
  )
}

// ── Root ──────────────────────────────────────────────────────────────────
const MODES = [
  { key: 'nec',    label: 'NEC / North America',   desc: 'trains.fyi — live positions' },
  { key: 'global', label: 'Global Infrastructure', desc: 'OpenRailwayMap — rail lines'  },
]

export default function TrainMapView() {
  const [mode, setMode] = useState(
    () => localStorage.getItem('trainMapMode') || 'nec'
  )

  const setAndStore = (m) => {
    localStorage.setItem('trainMapMode', m)
    setMode(m)
  }

  return (
    <div className="panel-view train-map-view">
      {/* Sub-nav */}
      <div className="train-map-subnav">
        <span className="train-map-title">TRAIN MAP</span>
        {MODES.map(m => (
          <button
            key={m.key}
            className={`train-mode-btn${mode === m.key ? ' active' : ''}`}
            onClick={() => setAndStore(m.key)}
            title={m.desc}
          >
            {m.label}
          </button>
        ))}
        {mode === 'nec' && (
          <a
            href="https://trains.fyi/"
            target="_blank"
            rel="noopener noreferrer"
            className="train-map-external"
            title="Open trains.fyi in new tab"
          >↗</a>
        )}
        {mode === 'global' && (
          <a
            href="https://www.openrailwaymap.org/"
            target="_blank"
            rel="noopener noreferrer"
            className="train-map-external"
            title="Open OpenRailwayMap in new tab"
          >↗</a>
        )}
      </div>

      {mode === 'nec'    && <TrainsFyiView />}
      {mode === 'global' && <OpenRailwayMapView />}
    </div>
  )
}
