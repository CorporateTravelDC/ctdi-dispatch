import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'

// DC-area static airspace GeoJSON (approximate)
const AIRSPACE = {
  FRZ:  { radius: 9260,   color: '#ff3131', label: 'FRZ 5nm'   },
  SFRA: { radius: 27780,  color: '#4a9eff', label: 'SFRA 15nm' },
}

const KDCA = [38.8521, -77.0377]
const RANGE_RINGS_NM = [50, 100, 150, 250]
const NM_TO_M = 1852

// globe.airplanes.live — centred on KDCA, zoom 8
const GLOBE_URL = `https://globe.airplanes.live/?centerlat=${KDCA[0]}&centerlon=${KDCA[1]}&zoom=8&hideSidebar&hideButtons`

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

// ── Globe mode: iframe + local feeder overlay ─────────────────────────────
function GlobeMap({ liveState }) {
  const overlayRef    = useRef(null)
  const leafletRef    = useRef(null)
  const localLayerRef = useRef(null)
  const [localCount,  setLocalCount]  = useState(0)
  const [iframeError, setIframeError] = useState(false)

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
    // No base tiles — this sits on top of the iframe
    localLayerRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  // Sync overlay zoom/center if we ever need it — for now static
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
        const marker = L.marker([ac.lat, ac.lon], {
          icon: localFeedIcon(hdg),
          interactive: true,
        })
        marker.bindTooltip(
          `<b class="local-feed-tip">LOCAL</b> ${callsign}<br/>` +
          `Alt: ${alt}ft · ${ac.gs || '?'}kt · ${Math.round(hdg)}°`,
          { className: 'ac-tooltip local-tooltip' }
        )
        marker.addTo(localLayerRef.current)
        count++
      })
      setLocalCount(count)
    } catch (_) {}
  }, [])

  useEffect(() => {
    refreshLocal()
    const id = setInterval(refreshLocal, 10_000)
    return () => clearInterval(id)
  }, [refreshLocal])

  return (
    <div className="globe-map-wrap">
      {iframeError ? (
        <div className="globe-fallback">
          <p>globe.airplanes.live blocked cross-origin embedding.</p>
          <a href="https://globe.airplanes.live/" target="_blank" rel="noopener noreferrer"
             className="globe-fallback-link">Open globe.airplanes.live ↗</a>
        </div>
      ) : (
        <iframe
          src={GLOBE_URL}
          className="globe-iframe"
          title="globe.airplanes.live"
          referrerPolicy="no-referrer"
          onError={() => setIframeError(true)}
          allow="fullscreen"
        />
      )}

      {/* Transparent Leaflet overlay — local feeder aircraft only */}
      <div ref={overlayRef} className="globe-local-overlay" />

      <div className="map-overlay-stats globe-stats">
        <span className="stat source-badge local-feed-stat">
          ◉ {localCount} LOCAL
        </span>
        <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>
          green = local feeder · globe = airplanes.live
        </span>
      </div>
    </div>
  )
}

// ── Local Leaflet map ──────────────────────────────────────────────────────
function LocalMap({ adsbMode, liveState }) {
  const mapRef         = useRef(null)
  const leafletRef     = useRef(null)
  const aircraftLayerRef = useRef(null)
  const tfrLayerRef    = useRef(null)
  const [acCount,  setAcCount]  = useState(0)
  const [tfrCount, setTfrCount] = useState(0)
  const [error,    setError]    = useState(null)

  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, { center: KDCA, zoom: 8, zoomControl: true })

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      className: 'map-tiles',
    }).addTo(map)

    Object.values(AIRSPACE).forEach(({ radius, color, label }) => {
      L.circle(KDCA, { radius, color, weight: 1, fill: false, dashArray: '4 6', opacity: 0.5 })
        .addTo(map).bindTooltip(label, { permanent: false })
    })

    RANGE_RINGS_NM.forEach(nm => {
      L.circle(KDCA, { radius: nm * NM_TO_M, color: '#2a3f6f', weight: 1, fill: false, dashArray: '2 8', opacity: 0.4 })
        .addTo(map)
    })

    L.circleMarker(KDCA, { radius: 5, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map).bindTooltip('KDCA', { permanent: true, className: 'airport-label' })

    aircraftLayerRef.current = L.layerGroup().addTo(map)
    tfrLayerRef.current = L.layerGroup().addTo(map)
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
      let count = 0
      aircraft.forEach(ac => {
        if (!ac.lat || !ac.lon) return
        const alt = ac.alt_baro ?? ac.altitude ?? 0
        if (ac.ground || ac.gnd || alt === 'ground' || alt < 100) return
        const callsign = (ac.flight || '').trim() || ac.hex || '?'
        const hdg = ac.track || ac.heading || 0
        L.marker([ac.lat, ac.lon], { icon: headingIcon(hdg) })
          .addTo(aircraftLayerRef.current)
          .bindTooltip(`<b>${callsign}</b><br/>Alt: ${alt}ft Spd: ${ac.gs || '?'}kt Hdg: ${Math.round(hdg)}`, { className: 'ac-tooltip' })
        count++
      })
      setAcCount(count)
      setError(null)
    } catch (e) { setError(`ADS-B: ${e.message}`) }
  }, [adsbMode])

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

  return (
    <div className="map-container">
      <div ref={mapRef} className="leaflet-map" />
      <div className="map-overlay-stats">
        <span className="stat">{acCount} AC</span>
        <span className="stat">{tfrCount} TFR{tfrCount !== 1 ? 's' : ''}</span>
        {error && <span className="stat error">{error}</span>}
        <span className={`stat source-badge ${adsbMode}`}>
          {adsbMode === 'local' ? 'LOCAL' : 'LIVE (airplanes.live)'}
        </span>
      </div>
    </div>
  )
}

// ── Root: pick globe vs local ─────────────────────────────────────────────
export default function MapView({ adsbMode, liveState }) {
  if (adsbMode === 'globe') {
    return <GlobeMap liveState={liveState} />
  }
  return <LocalMap adsbMode={adsbMode} liveState={liveState} />
}
