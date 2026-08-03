// AirmetHazardMap.jsx -- added 2026-08-03.
//
// A self-contained, CONUS-framed Leaflet view of the same FAA AIRMET/SIGMET
// hazard polygons already drawn on the tactical map (MapView.jsx's
// refreshAirmets/airmetLayerRef) -- deliberately duplicated here rather than
// shared, per Corey's direction: the WX tab is meant to become an
// all-travel-hazards "look at everything" page (prog / maritime / hazards),
// separate from the tactical map, which stays a live-traffic view for
// flights (and later AIS). Read-only -- no aircraft, no TFRs, no DC-airspace
// rings here, just the hazard polygons plus a hazard-type filter row in the
// same visual idiom as the PROG/MARITIME forecast-hour rows.
//
// Same backend as the tactical layer: GET /api/v1/airmets (5-min server
// cache). No new backend work -- this is a frontend-only addition.

import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'

const HAZARD_FILTERS = [
  { key: 'ALL',        label: 'ALL' },
  { key: 'CONVECTIVE', label: 'CONVECTIVE' },
  { key: 'TURB',       label: 'TURB' },
  { key: 'ICE',        label: 'ICE' },
  { key: 'IFR',        label: 'IFR' },
  { key: 'MTN_OBSCN',  label: 'MTN OBSCN' },
  { key: 'LLWS',       label: 'LLWS' },
]

const LEGEND = [
  { hazard: 'CONVECTIVE', color: '#ff3131', label: 'Convective' },
  { hazard: 'TURB',       color: '#a855f7', label: 'Turbulence' },
  { hazard: 'ICE',        color: '#4a9eff', label: 'Icing' },
  { hazard: 'IFR',        color: '#ffd700', label: 'IFR' },
  { hazard: 'MTN_OBSCN',  color: '#8b6f47', label: 'Mtn Obscuration' },
  { hazard: 'LLWS',       color: '#ff8c00', label: 'LLWS / Sfc Wind' },
]

const AIRMETS_POLL = 300_000  // matches the server-side 5-min cache TTL

export default function AirmetHazardMap() {
  const mapDivRef    = useRef(null)
  const leafletRef   = useRef(null)
  const layerRef     = useRef(null)
  const dataRef      = useRef([])
  const filterRef    = useRef('ALL')
  const [filter, setFilterState] = useState('ALL')
  const [count,  setCount]       = useState(0)
  const [updated, setUpdated]    = useState(null)

  // Map init -- CONUS-wide view, not DC-focused, since hazards are
  // national in scope and this page is meant to be read like a chart.
  useEffect(() => {
    if (!mapDivRef.current || leafletRef.current) return
    const map = L.map(mapDivRef.current, {
      center: [39.5, -96],
      zoom: 4,
      zoomControl: true,
    })
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 10,
    }).addTo(map)
    layerRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
    // Flex-container mount can report a zero-height box on first paint;
    // re-measure once layout settles (same fix used by the tactical map).
    setTimeout(() => map.invalidateSize(), 150)
    return () => { map.remove(); leafletRef.current = null }
  }, [])

  const draw = useCallback(() => {
    if (!layerRef.current) return
    layerRef.current.clearLayers()
    const f = filterRef.current
    const data = f === 'ALL' ? dataRef.current : dataRef.current.filter(a => a.hazard === f)
    data.forEach(a => {
      if (!a.coords || a.coords.length < 3) return
      const isSigmet = a.type === 'SIGMET'
      L.polygon(a.coords, {
        color: a.color || '#9ca3af',
        weight: isSigmet ? 2 : 1,
        fill: true,
        fillOpacity: isSigmet ? 0.16 : 0.09,
        dashArray: isSigmet ? null : '4 4',
      })
        .addTo(layerRef.current)
        .bindTooltip(
          `<b>${a.type}: ${a.hazard.replace(/_/g, ' ')}</b><br/>` +
          `${a.altitude_low ?? 'SFC'}–${a.altitude_high ?? '?'}ft` +
          (a.severity ? ` · sev ${a.severity}` : ''),
          { className: 'tfr-tooltip' }
        )
    })
    setCount(data.length)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/dispatch/api/v1/airmets')
      if (!r.ok) return
      const json = await r.json()
      dataRef.current = json.airmets || []
      draw()
      setUpdated(new Date())
    } catch (_) {}
  }, [draw])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, AIRMETS_POLL)
    return () => clearInterval(id)
  }, [refresh])

  const setFilter = (key) => {
    filterRef.current = key
    setFilterState(key)
    draw()
  }

  return (
    <div className="wx-hazard-panel">
      <div className="wx-prog-hours">
        {HAZARD_FILTERS.map(f => (
          <button
            key={f.key}
            className={`train-mode-btn wx-prog-hour-btn${filter === f.key ? ' active' : ''}`}
            onClick={() => setFilter(f.key)}
          >{f.label}</button>
        ))}
        <span className="stat" style={{ marginLeft: 'auto' }}>
          {count} active{updated ? ` · upd ${updated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
        </span>
      </div>
      <div className="wx-hazard-map" ref={mapDivRef} />
      <div className="wx-hazard-legend">
        {LEGEND.map(l => (
          <span key={l.hazard} className="wx-hazard-legend-item">
            <span className="wx-hazard-swatch" style={{ background: l.color }} />
            {l.label}
          </span>
        ))}
      </div>
    </div>
  )
}
