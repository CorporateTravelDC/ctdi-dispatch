import { useEffect, useRef, useState, useCallback } from 'react'

// DC-area static airspace GeoJSON (approximate)
const AIRSPACE = {
  FRZ: { radius: 9260,   color: '#ff3131', label: 'FRZ 5nm' },   // 5nm in metres
  SFRA: { radius: 27780, color: '#4a9eff', label: 'SFRA 15nm' },
}

const KDCA = [38.8521, -77.0377]
const RANGE_RINGS_NM = [50, 100, 150, 250]
const NM_TO_M = 1852

function adsbUrl(mode) {
  return mode === 'local' ? '/api/adsb/local' : '/api/adsb/live'
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

export default function MapView({ adsbMode, liveState }) {
  const mapRef = useRef(null)
  const leafletRef = useRef(null)
  const aircraftLayerRef = useRef(null)
  const tfrLayerRef = useRef(null)
  const [acCount, setAcCount] = useState(0)
  const [tfrCount, setTfrCount] = useState(0)
  const [error, setError] = useState(null)

  // Initialise map once
  useEffect(() => {
    if (leafletRef.current) return
    const L = window.L
    const map = L.map(mapRef.current, {
      center: KDCA,
      zoom: 8,
      zoomControl: true,
    })

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      className: 'map-tiles',
    }).addTo(map)

    // Static airspace rings
    Object.values(AIRSPACE).forEach(({ radius, color, label }) => {
      L.circle(KDCA, {
        radius,
        color,
        weight: 1,
        fill: false,
        dashArray: '4 6',
        opacity: 0.5,
      }).addTo(map).bindTooltip(label, { permanent: false })
    })

    // Range rings
    RANGE_RINGS_NM.forEach(nm => {
      L.circle(KDCA, {
        radius: nm * NM_TO_M,
        color: '#2a3f6f',
        weight: 1,
        fill: false,
        dashArray: '2 8',
        opacity: 0.4,
      }).addTo(map)
    })

    // KDCA marker
    L.circleMarker(KDCA, { radius: 5, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map)
      .bindTooltip('KDCA', { permanent: true, className: 'airport-label' })

    // Layer groups for dynamic data
    aircraftLayerRef.current = L.layerGroup().addTo(map)
    tfrLayerRef.current = L.layerGroup().addTo(map)

    leafletRef.current = map
  }, [])

  // Fetch and render aircraft
  const refreshAircraft = useCallback(async () => {
    if (!aircraftLayerRef.current) return
    try {
      const r = await fetch(adsbUrl(adsbMode))
      if (!r.ok) throw new Error(r.statusText)
      const data = await r.json()
      const aircraft = data.aircraft || data.ac || []
      const L = window.L
      aircraftLayerRef.current.clearLayers()
      let count = 0
      aircraft.forEach(ac => {
        const lat = ac.lat
        const lon = ac.lon
        if (!lat || !lon) return
        const callsign = (ac.flight || ac.callsign || '').trim() || ac.hex || '?'
        const alt = ac.alt_baro || ac.altitude || '?'
        const spd = ac.gs || ac.speed || '?'
        const hdg = ac.track || ac.heading || 0
        const isGround = ac.ground || ac.gnd || alt === 'ground' || alt < 100
        if (isGround) return
        L.marker([lat, lon], { icon: headingIcon(hdg) })
          .addTo(aircraftLayerRef.current)
          .bindTooltip(
            `<b>${callsign}</b><br/>Alt: ${alt}ft Spd: ${spd}kt Hdg: ${Math.round(hdg)}`,
            { className: 'ac-tooltip' }
          )
        count++
      })
      setAcCount(count)
      setError(null)
    } catch (e) {
      setError(`ADS-B: ${e.message}`)
    }
  }, [adsbMode])

  // Fetch and render TFRs
  const refreshTfrs = useCallback(async () => {
    if (!tfrLayerRef.current) return
    try {
      const r = await fetch('/api/dispatch/api/v1/tfr')
      if (!r.ok) return
      const tfrs = await r.json()
      if (!Array.isArray(tfrs)) return
      const L = window.L
      tfrLayerRef.current.clearLayers()
      tfrs.forEach(tfr => {
        // TFRs from the dispatch API include a center point and radius
        const lat = tfr.center_lat
        const lon = tfr.center_lon
        const radius_m = tfr.radius_nm ? tfr.radius_nm * NM_TO_M : 9260
        if (!lat || !lon) return
        const isVip = tfr.is_vip
        const color = isVip ? '#ff3131' : '#ff6b35'
        L.circle([lat, lon], {
          radius: radius_m,
          color,
          weight: isVip ? 2 : 1,
          fill: true,
          fillOpacity: 0.12,
          className: isVip ? 'tfr-vip' : 'tfr-normal',
        })
          .addTo(tfrLayerRef.current)
          .bindTooltip(
            `${isVip ? 'VIP TFR: ' : 'TFR: '}${tfr.tfr_id}`,
            { className: 'tfr-tooltip' }
          )
      })
      setTfrCount(tfrs.length)
    } catch (_) {}
  }, [])

  // Poll ADS-B every 10s; refresh immediately on mode change
  useEffect(() => {
    refreshAircraft()
    const id = setInterval(refreshAircraft, 10000)
    return () => clearInterval(id)
  }, [refreshAircraft])

  // Poll TFRs every 60s
  useEffect(() => {
    refreshTfrs()
    const id = setInterval(refreshTfrs, 60000)
    return () => clearInterval(id)
  }, [refreshTfrs])

  // Update TFR count from SSE state
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
