import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback } from 'react'
import AriaCompassRegion from './AriaCompassRegion.jsx'
import AccessibleTable   from './AccessibleTable.jsx'
import { useCompassSummary } from '../hooks/useCompassSummary.js'
import { useWatchlist, FALLBACK_PLANE_SVG } from '../hooks/useWatchlist.js'

// ── Tile sources ──────────────────────────────────────────────────
const OSM_URL          = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTR         = '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a> contributors'
const OSM_NAUTICAL_URL = 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png'
const OSM_NAUTICAL_ATTR = '&copy; <a href="https://www.openseamap.org">OpenSeaMap</a>'

// Centre: Chesapeake Bay / Potomac / Port of Baltimore
const DEFAULT_CENTER = [38.9, -76.8]
const DEFAULT_ZOOM   = 8
const VESSEL_POLL    = 60_000   // 1 min

// Tracker iframes — tried in order; blocked ones fall through to local map
const TRACKER_IFRAMES = [
  { label: 'MarineTraffic', url: 'https://www.marinetraffic.com/en/ais/home/centerx:-76.8/centery:38.9/zoom:8' },
  { label: 'VesselFinder',  url: 'https://www.vesselfinder.com/?lat=38.9&lng=-76.8&zoom=8' },
]
const TRACKER_LINKS = [
  { label: 'MarineTraffic ↗', url: 'https://www.marinetraffic.com/en/ais/home/centerx:-76.8/centery:38.9/zoom:8' },
  { label: 'VesselFinder ↗',  url: 'https://www.vesselfinder.com/?lat=38.9&lng=-76.8&zoom=8' },
  { label: 'AIS Marine ↗',    url: 'https://www.aismarine.com/ais-map/' },
]

// ── Nav-status colour ─────────────────────────────────────────────
function vesselColor(nav_status) {
  const s = nav_status ?? 15
  if (s === 1 || s === 5) return '#ffd700'   // anchored / moored
  if (s === 0 || s === 8) return '#4a9eff'   // underway engine / sailing
  if (s >= 2 && s <= 4)   return '#ff9100'   // constrained
  return '#888'
}

// Heading-aware ship shape: pointed bow, squared stern, rotates to COG
function vesselIcon(nav_status, cog, isTracked) {
  const color  = isTracked ? '#00d4ff' : vesselColor(nav_status)
  const stroke = isTracked ? '#003a4a' : '#111'
  const glow   = isTracked ? `filter:drop-shadow(0 0 4px #00d4ff);` : ''
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

// Source label for display
function sourceLabel(source) {
  if (source === 'local')             return 'AIS-catcher (local)'
  if (source === 'marinetraffic.com') return 'MarineTraffic API'
  if (source === 'aishub.net')        return 'AISHub'
  return null
}

// ── Iframe tracker mode ───────────────────────────────────────────
function TrackerIframe({ onFallback }) {
  const [idx, setIdx]     = useState(0)
  const [failed, setFailed] = useState(false)

  const handleError = () => {
    const next = idx + 1
    if (next < TRACKER_IFRAMES.length) {
      setIdx(next)
    } else {
      setFailed(true)
      onFallback()
    }
  }

  if (failed) return null

  return (
    <div className="ais-iframe-wrap">
      <iframe
        key={TRACKER_IFRAMES[idx].url}
        src={TRACKER_IFRAMES[idx].url}
        className="ais-tracker-iframe"
        title={TRACKER_IFRAMES[idx].label}
        referrerPolicy="no-referrer"
        onError={handleError}
        allow="fullscreen"
      />
      <div className="ais-iframe-badge">
        {TRACKER_IFRAMES[idx].label} (live)
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────
export default function AisMapView() {
  const mapRef         = useRef(null)
  const leafletRef     = useRef(null)
  const vesselLayerRef = useRef(null)
  const trackedLayerRef = useRef(null)

  const [vesselCount,   setVesselCount]   = useState(0)
  const [trackedCount,  setTrackedCount]  = useState(0)
  const [dataSource,    setDataSource]    = useState('none')
  const [loadErr,       setLoadErr]       = useState(false)
  const [mode,          setMode]          = useState('iframe')  // 'iframe' | 'local'
  const [vesselItems,   setVesselItems]   = useState([])        // for compass summary

  const { entries: watchEntries, hexSet } = useWatchlist()

  // Build MMSI set from watchlist entries (notes: "mmsi: 366123456")
  const mmsiSet = new Set()
  watchEntries.forEach(e => {
    const m = e.notes?.match(/mmsi:\s*(\d+)/i)
    if (m) mmsiSet.add(m[1])
    // Also check identifier field — some entries use MMSI directly
    if (e.identifier && /^\d{9}$/.test(e.identifier)) mmsiSet.add(e.identifier)
  })

  // ── Init Leaflet map ────────────────────────────────────────────
  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, {
      center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true,
    })
    L.tileLayer(OSM_URL, { attribution: OSM_ATTR, className: 'map-tiles' }).addTo(map)
    L.tileLayer(OSM_NAUTICAL_URL, {
      attribution: OSM_NAUTICAL_ATTR, maxZoom: 18, opacity: 0.85,
    }).addTo(map)

    vesselLayerRef.current  = L.layerGroup().addTo(map)
    trackedLayerRef.current = L.layerGroup().addTo(map)
    leafletRef.current = map
  }, [])

  // ── Refresh vessels ─────────────────────────────────────────────
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
        const name  = v.name || `MMSI ${v.mmsi || '?'}`
        const spd   = v.sog  != null ? `${v.sog} kt`  : '—'
        const cog   = v.cog  != null ? `${v.cog}°`    : '—'
        const type  = v.ship_type != null ? ` · Type ${v.ship_type}` : ''
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

        const marker = L.marker([v.lat, v.lon], {
          icon,
          interactive: true,
          zIndexOffset: isTracked ? 2000 : 0,
        }).bindTooltip(tip, {
          className: isTracked ? 'ac-tooltip tracked-tooltip' : 'ac-tooltip',
          permanent: isTracked,
          direction: 'top',
          sticky: !isTracked,
        })

        marker.addTo(isTracked ? trackedLayerRef.current : vesselLayerRef.current)
        count++
        if (isTracked) tCount++
        items.push({ lat: v.lat, lon: v.lon, label: name, tracked: isTracked })
      })

      setVesselCount(count)
      setTrackedCount(tCount)
      setDataSource(source || 'none')
      setVesselItems(items)
      setLoadErr(false)
    } catch (_) {
      setLoadErr(true)
    }
  }, [mmsiSet.size])  // re-run when watchlist changes size

  useEffect(() => {
    refreshVessels()
    const id = setInterval(refreshVessels, VESSEL_POLL)
    return () => clearInterval(id)
  }, [refreshVessels])

  const src = sourceLabel(dataSource)
  const compassSummary = useCompassSummary(vesselItems, [])
  const vesselTableRows = vesselItems.map(v => ({
    name:    v.label,
    lat:     v.lat?.toFixed(4),
    lon:     v.lon?.toFixed(4),
    tracked: v.tracked ? '★' : '',
  }))

  const localMap = (
    <div className="map-container">
      <AriaCompassRegion
        summary={compassSummary}
        entityType="vessels"
        count={vesselCount}
        extra="Potomac · Chesapeake · Port of Baltimore."
      />
      <AccessibleTable
        id="ais-vessel-table"
        caption={`Vessels in range — ${vesselCount} visible`}
        columns={[
          { key: 'name',    label: 'Vessel'    },
          { key: 'lat',     label: 'Latitude'  },
          { key: 'lon',     label: 'Longitude' },
          { key: 'tracked', label: '★'          },
        ]}
        rows={vesselTableRows}
        emptyMsg="No vessels currently visible."
      />
      <div ref={mapRef} className="leaflet-map" style={{ display: mode === 'local' ? '' : 'none' }} />

      {/* AIS-catcher scaffold banner — visible when no local feed */}
      {!src && vesselCount === 0 && !loadErr && (
        <div className="ais-no-data-overlay">
          <div className="ais-no-data-title">No live AIS feed</div>
          <div className="ais-no-data-sub">
            Deploy AIS-catcher on the Pi (port 8110), then set
            <code> AIS_CATCHER_URL=http://127.0.0.1:8110</code> in dispatch.env.
            AISHub and MarineTraffic API keys also accepted.
          </div>
          <div className="ais-no-data-links">
            {TRACKER_LINKS.map(t => (
              <a key={t.label} href={t.url} target="_blank" rel="noopener noreferrer"
                 className="ais-tracker-link">{t.label}</a>
            ))}
          </div>
        </div>
      )}

      {/* Overlay stats */}
      <div className="map-overlay-stats">
        {vesselCount > 0
          ? <span className="stat source-badge" style={{ color: '#4a9eff' }}>{vesselCount} vessels</span>
          : <span className="stat source-badge" style={{ color: 'var(--muted)' }}>No live AIS</span>
        }
        {trackedCount > 0 && (
          <span className="stat tracked-stat">★ {trackedCount} TRACKED</span>
        )}
        {src && <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>{src}</span>}
        {loadErr && <span className="stat error">AIS feed error</span>}
        <button className="intel-refresh-btn" onClick={refreshVessels} title="Refresh vessels">↻</button>
      </div>
    </div>
  )

  return (
    <div className="train-map-view">
      {/* Sub-nav */}
      <div className="train-map-subnav">
        <span className="train-map-title">AIS</span>

        {/* Mode toggle: local map ↔ tracker iframe */}
        <div className="ais-mode-toggle" role="group" aria-label="AIS display mode">
          <button
            className={`ais-mode-btn${mode === 'local' ? ' active' : ''}`}
            onClick={() => setMode('local')}
            aria-pressed={mode === 'local'}
          >⚓ MAP</button>
          <button
            className={`ais-mode-btn${mode === 'iframe' ? ' active' : ''}`}
            onClick={() => setMode('iframe')}
            aria-pressed={mode === 'iframe'}
            title="Attempt live tracker embed (may be blocked)"
          >🌐 LIVE</button>
        </div>

        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
          {TRACKER_LINKS.map(t => (
            <a key={t.label} href={t.url} target="_blank" rel="noopener noreferrer"
               className="train-map-external" title={t.label}>{t.label}</a>
          ))}
        </span>
      </div>

      {/* Content area */}
      {mode === 'iframe'
        ? <TrackerIframe onFallback={() => setMode('local')} />
        : localMap
      }

      {/* Local map DOM always mounted (even in iframe mode) so Leaflet doesn't lose state */}
      {mode === 'iframe' && (
        <div style={{ display: 'none' }}>
          <div ref={mapRef} />
        </div>
      )}
    </div>
  )
}
