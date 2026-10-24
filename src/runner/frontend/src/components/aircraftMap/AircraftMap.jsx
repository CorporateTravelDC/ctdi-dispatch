/**
 * AircraftMap -- THE aircraft map (2026-10-24, operator-approved map rework, aviation wave 1).
 *
 * Grew out of MapView.jsx's TacticalMap. The Globe (aggregator iframe) mode and the GLOBE/TACTICAL
 * toggle are gone: everything the deployment has -- local receiver + SWIM, merged server-side by the
 * runner's /api/map/aircraft -- is drawn on this one Leaflet map. Marker fill = altitude (tar1090
 * ramp), outline = source, red ring = identity conflict; see markers.js / altitudeColor.js.
 *
 * Data: /api/map/scope once (pan limit, min zoom, receiver centre, SWIM area, tar1090 link), then
 * /api/map/aircraft for the current viewport every 5 s while visible and 600 ms after each pan/zoom.
 * Click-to-focus from markers, the list panel, the watchlist badge and the search box all go through
 * focusIdent/focusAircraft; off-screen or landed targets resolve through /api/map/focus.
 */
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import AriaCompassRegion from '../AriaCompassRegion.jsx'
import AccessibleTable   from '../AccessibleTable.jsx'
import LayerSidebar      from '../LayerSidebar.jsx'
import { useCompassSummary } from '../../hooks/useCompassSummary.js'
import { useGlobalLayerConfig } from '../../App.jsx'
import { useWatchlist } from '../../hooks/useWatchlist.js'
import { useReceiverLocation } from '../../hooks/useReceiverLocation.js'
import { useVisibilityAwareInterval } from '../../hooks/useVisibilityAwareInterval.js'
import { OSM_TILE_URL, osmTileOptions } from '../../mapTiles.js'
import { aircraftIcon, lastSeenIcon, tooltipHtml, displayName, fmtAge, fmtAlt, SRC_LABEL } from './markers.js'
import { TrailsStore, TrailsRenderer } from './trailsStore.js'
import { fetchFocus, drawLeg, flyTo, matchesIdent, airportCoordTable } from './focus.js'
import Legend from './Legend.jsx'
import AircraftList from './AircraftList.jsx'
import WatchlistBadge from './WatchlistBadge.jsx'

// DC-area static airspace (approximate), centred on the receiver like the range rings.
const AIRSPACE = {
  FRZ:  { radius: 9260,   color: '#ff3131', label: 'FRZ 5nm'   },
  SFRA: { radius: 27780,  color: '#4a9eff', label: 'SFRA 15nm' },
}
const RANGE_RINGS_NM = [50, 100, 150, 250]
const NM_TO_M = 1852
const POLL_MS = 5_000
const MOVE_DEBOUNCE_MS = 600

const finite = v => Number.isFinite(Number(v))

/** Stable identity for marker/trail bookkeeping: hex first (SWIM-only rows may have none). */
export function keyOf(ac) {
  if (ac.hex) return String(ac.hex).toLowerCase()
  const cs = (ac.flight || '').trim().toUpperCase()
  if (cs) return `cs:${cs}`
  if (ac.r) return `r:${String(ac.r).toUpperCase()}`
  return `p:${Number(ac.lat).toFixed(3)},${Number(ac.lon).toFixed(3)}`
}

function tar1090Link(base, hex) {
  if (!base) return null
  if (hex && /^[0-9a-f]{6}$/i.test(hex)) {
    return `${base}${base.includes('?') ? '&' : '?'}icao=${hex.toLowerCase()}`
  }
  return base
}

export default function AircraftMap({ liveState }) {
  const { config } = useGlobalLayerConfig() ?? {}
  const layers = config?.layers ?? {}

  const mapRef        = useRef(null)
  const leafletRef    = useRef(null)
  const groups        = useRef({})          // leaflet layerGroups by name
  const markersRef    = useRef(new Map())   // key -> { marker, layer, perm }
  const trailsRef     = useRef(new TrailsStore())
  const trailsDrawRef = useRef(null)
  const selectedKeyRef = useRef(null)
  const watchRef      = useRef({ callsignSet: new Set(), hexSet: new Set() })
  const layersRef     = useRef(layers)
  const moveTimer     = useRef(null)
  const airportsRef   = useRef(new Map())

  const [scope,       setScope]       = useState(null)
  const [aircraft,    setAircraft]    = useState([])
  const [counts,      setCounts]      = useState({ matched: 0, local: 0, swim: 0, conflict: 0 })
  const [swimAvail,   setSwimAvail]   = useState(null)
  const [tfrCount,    setTfrCount]    = useState(0)
  const [airmetCount, setAirmetCount] = useState(0)
  const [tfrExtra,    setTfrExtra]    = useState([])
  const [error,       setError]       = useState(null)
  const [selectedKey, setSelectedKey] = useState(null)
  const [selectedAc,  setSelectedAc]  = useState(null)
  const [lastSeen,    setLastSeen]    = useState(null)
  const [searchInput, setSearchInput] = useState('')
  const [focusMsg,    setFocusMsg]    = useState(null)
  const [listCollapsed, setListCollapsed] = useState(() => {
    try { const v = localStorage.getItem('acmap_list_collapsed'); if (v != null) return JSON.parse(v) } catch {}
    return typeof window !== 'undefined' && window.innerWidth < 720
  })

  const { entries: watchEntries, callsignSet, hexSet } = useWatchlist()
  const receiverLoc = useReceiverLocation()
  watchRef.current = { callsignSet, hexSet }
  layersRef.current = layers

  // Receiver centre: /api/map/scope's trusted value wins, the frontend-config hook is the fallback.
  const center = useMemo(() => {
    const c = scope?.receiver?.center
    return (Array.isArray(c) && c.length === 2 && finite(c[0]) && finite(c[1])) ? [Number(c[0]), Number(c[1])] : receiverLoc
  }, [scope, receiverLoc])

  const isTracked = useCallback((ac) => {
    const { callsignSet: cs, hexSet: hx } = watchRef.current
    const flight = (ac.flight || '').trim().toUpperCase()
    const reg    = (ac.r || '').trim().toUpperCase()
    return (flight && cs.has(flight)) || (reg && cs.has(reg)) || hx.has((ac.hex || '').toLowerCase())
  }, [])

  const wantTrail = useCallback((key) => {
    if (layersRef.current.trails) return true
    if (key === selectedKeyRef.current) return true
    const rec = markersRef.current.get(key)
    return !!rec?.tracked
  }, [])

  // ── Map init ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (leafletRef.current) return
    const map = L.map(mapRef.current, { center: receiverLoc, zoom: 8, zoomControl: true, maxBoundsViscosity: 1.0 })
    L.tileLayer(OSM_TILE_URL, osmTileOptions({ className: 'map-tiles' })).addTo(map)

    map._airspaceLayers = Object.values(AIRSPACE).map(({ radius, color, label }) =>
      L.circle(receiverLoc, { radius, color, weight: 1, fill: false, dashArray: '4 6', opacity: 0.5 })
        .addTo(map).bindTooltip(label, { permanent: false }))
    map._ringLayers = RANGE_RINGS_NM.map(nm =>
      L.circle(receiverLoc, { radius: nm * NM_TO_M, color: '#2a3f6f', weight: 1, fill: false, dashArray: '2 8', opacity: 0.4 }).addTo(map))
    map._scopeLayers = []
    map._groundMarker = L.circleMarker(receiverLoc, { radius: 5, color: '#ffd700', fill: true, fillOpacity: 1 })
      .addTo(map).bindTooltip('RECEIVER', { permanent: true, className: 'airport-label' })

    const g = groups.current
    g.trails   = L.layerGroup().addTo(map)
    g.leg      = L.layerGroup().addTo(map)
    g.aircraft = L.layerGroup().addTo(map)
    g.tracked  = L.layerGroup().addTo(map)   // tracked on top
    g.lastSeen = L.layerGroup().addTo(map)
    g.tfr      = L.layerGroup().addTo(map)
    g.airmet   = L.layerGroup().addTo(map)
    trailsDrawRef.current = new TrailsRenderer(trailsRef.current, g.trails)

    map.on('moveend', () => {
      clearTimeout(moveTimer.current)
      moveTimer.current = setTimeout(() => refreshRef.current?.(), MOVE_DEBOUNCE_MS)
    })
    map.on('click', () => clearSelectionRef.current?.())
    leafletRef.current = map
    return () => clearTimeout(moveTimer.current)
  }, [])

  // Re-centre + move receiver-anchored overlays when the real centre resolves.
  useEffect(() => {
    const map = leafletRef.current
    if (!map) return
    map.setView(center, map.getZoom())
    map._airspaceLayers?.forEach(c => c.setLatLng(center))
    map._ringLayers?.forEach(r => r.setLatLng(center))
    map._groundMarker?.setLatLng(center)
  }, [center])

  // ── Scope: pan limit, min zoom, SWIM area, receiver range ───────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetch('/api/map/scope', { cache: 'no-store' })
        if (!r.ok) return
        const s = await r.json()
        if (cancelled || !s) return
        setScope(s)
        airportsRef.current = airportCoordTable(s.swim?.airports)
        const map = leafletRef.current
        if (!map) return
        const b = s.bounds
        if (Array.isArray(b) && b.length === 2) {
          const bounds = L.latLngBounds(b).pad(0.15)
          map.setMaxBounds(bounds)
          const z = map.getBoundsZoom(bounds, false)
          if (Number.isFinite(z)) map.setMinZoom(Math.max(2, Math.floor(z)))
        }
        map._scopeLayers.forEach(l => map.removeLayer(l))
        map._scopeLayers = []
        const rc = s.receiver?.center
        if (Array.isArray(rc) && finite(s.receiver?.range_nm)) {
          map._scopeLayers.push(L.circle(rc, { radius: Number(s.receiver.range_nm) * NM_TO_M, color: '#ffd700', weight: 1, fill: false, dashArray: '6 6', opacity: 0.45 })
            .bindTooltip(`Receiver range ${s.receiver.range_nm} nm`))
        }
        const sc = s.swim?.center
        if (Array.isArray(sc) && finite(sc[0]) && finite(s.swim?.radius_nm)) {
          map._scopeLayers.push(L.circle(sc, { radius: Number(s.swim.radius_nm) * NM_TO_M, color: '#9c6bff', weight: 1, fill: false, dashArray: '10 6', opacity: 0.45 })
            .bindTooltip(`SWIM area${s.swim.label ? ': ' + s.swim.label : ''} (${s.swim.radius_nm} nm)`))
        }
        if (layersRef.current.rings !== false) map._scopeLayers.forEach(l => l.addTo(map))
      } catch (_) {}
    })()
    return () => { cancelled = true }
  }, [])

  // ── Marker styling helpers ──────────────────────────────────────────────
  const applyStyle = useCallback((key, rec) => {
    const ac = rec.marker._ac
    const tracked  = isTracked(ac)
    const selected = key === selectedKeyRef.current
    const perm = tracked || selected
    rec.marker.setIcon(aircraftIcon(ac, { tracked, selected }))
    rec.marker.setZIndexOffset(selected ? 3000 : tracked ? 2000 : ac.conflict ? 500 : 0)
    const cls = `ac-tooltip${tracked ? ' tracked-tooltip' : ''}${selected ? ' selected-tooltip' : ''}`
    const html = tooltipHtml(ac, { tracked })
    if (rec.perm !== perm || rec.cls !== cls) {
      rec.marker.unbindTooltip()
      rec.marker.bindTooltip(html, { className: cls, permanent: perm, direction: 'top', interactive: false })
      rec.perm = perm; rec.cls = cls
    } else {
      rec.marker.setTooltipContent(html)
    }
    const g = groups.current
    const target = tracked ? g.tracked : g.aircraft
    if (rec.layer !== target) {
      rec.layer?.removeLayer(rec.marker)
      rec.marker.addTo(target)
      rec.layer = target
    }
    rec.tracked = tracked
  }, [isTracked])

  const redrawLeg = useCallback((ac) => {
    const g = groups.current
    if (!g.leg) return
    drawLeg(g.leg, ac, airportsRef.current)
  }, [])

  const clearSelection = useCallback(() => {
    const prev = selectedKeyRef.current
    selectedKeyRef.current = null
    setSelectedKey(null); setSelectedAc(null); setLastSeen(null)
    groups.current.leg?.clearLayers()
    groups.current.lastSeen?.clearLayers()
    const rec = prev && markersRef.current.get(prev)
    if (rec) { applyStyle(prev, rec); rec.marker.closeTooltip() }
    trailsDrawRef.current?.sync(wantTrail, prev ? [prev] : [])
  }, [applyStyle, wantTrail])
  const clearSelectionRef = useRef(clearSelection)
  clearSelectionRef.current = clearSelection

  const focusAircraft = useCallback((ac, { fly = true } = {}) => {
    if (!ac) return
    const key = keyOf(ac)
    const prev = selectedKeyRef.current
    selectedKeyRef.current = key
    setSelectedKey(key); setSelectedAc(ac); setLastSeen(null); setFocusMsg(null)
    groups.current.lastSeen?.clearLayers()
    const prevRec = prev && prev !== key && markersRef.current.get(prev)
    if (prevRec) { applyStyle(prev, prevRec); prevRec.marker.closeTooltip() }
    const rec = markersRef.current.get(key)
    if (rec) { applyStyle(key, rec); rec.marker.openTooltip() }
    redrawLeg(ac)
    trailsDrawRef.current?.sync(wantTrail, [prev, key].filter(Boolean), { emphasis: k => k === key })
    if (fly) flyTo(leafletRef.current, ac.lat, ac.lon)
  }, [applyStyle, redrawLeg, wantTrail])

  /** Resolve a hex / callsign / tail: live marker first, then /api/map/focus. */
  const focusIdent = useCallback(async (ident) => {
    const q = String(ident || '').trim()
    if (!q) return
    for (const [, rec] of markersRef.current) {
      if (matchesIdent(rec.marker._ac, q)) { focusAircraft(rec.marker._ac); return }
    }
    setFocusMsg(`Looking up ${q.toUpperCase()}...`)
    const res = await fetchFocus(q)
    if (!res.ok) {
      setFocusMsg(res.status === 404 ? `${q.toUpperCase()}: not live and no recent SWIM leg` : `${q.toUpperCase()}: lookup failed`)
      return
    }
    if (res.live) { focusAircraft(res.aircraft); return }
    // Not airborne anywhere in scope: grey "last seen" marker at its last position + most recent leg.
    const last = res.last
    const prev = selectedKeyRef.current
    selectedKeyRef.current = null
    setSelectedKey(null); setSelectedAc(null); setLastSeen(last)
    const prevRec = prev && markersRef.current.get(prev)
    if (prevRec) { applyStyle(prev, prevRec); prevRec.marker.closeTooltip() }
    const g = groups.current
    g.leg?.clearLayers(); g.lastSeen?.clearLayers()
    if (last && finite(last.lat) && finite(last.lon)) {
      const name = displayName(last)
      const line = [
        `<b class="act-name">${name}</b>`,
        `<span class="act-src act-src-swim">SWIM last leg${last.status ? ' &middot; ' + String(last.status).toUpperCase() : ''}</span>`,
        `last seen ${fmtAge(last.pos_age_s)} ago`,
        (last.origin || last.destination) ? `${last.origin || '????'} &rarr; ${last.destination || '????'}` : null,
        last.alt_baro != null ? fmtAlt(last.alt_baro) : null,
      ].filter(Boolean).join('<br/>')
      L.marker([Number(last.lat), Number(last.lon)], { icon: lastSeenIcon(last.track), zIndexOffset: 2500 })
        .addTo(g.lastSeen)
        .bindTooltip(`<div class="act last-seen">${line}</div>`, { className: 'ac-tooltip last-seen-tooltip', permanent: true, direction: 'top' })
        .openTooltip()
      drawLeg(g.leg, last, airportsRef.current, { muted: true })
      flyTo(leafletRef.current, last.lat, last.lon)
      setFocusMsg(`${name}: not airborne, last seen ${fmtAge(last.pos_age_s)} ago`)
    } else {
      drawLeg(g.leg, last, airportsRef.current, { muted: true })
      setFocusMsg(`${q.toUpperCase()}: last leg known, no position`)
    }
  }, [focusAircraft, applyStyle])

  // ── Aircraft poll ───────────────────────────────────────────────────────
  const refreshAircraft = useCallback(async () => {
    const map = leafletRef.current
    const g = groups.current
    if (!map || !g.aircraft) return
    try {
      const b = map.getBounds()
      const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
      const qs = new URLSearchParams({
        south: clamp(b.getSouth(), -90, 90).toFixed(4),
        west:  clamp(b.getWest(), -180, 180).toFixed(4),
        north: clamp(b.getNorth(), -90, 90).toFixed(4),
        east:  clamp(b.getEast(), -180, 180).toFixed(4),
      })
      const r = await fetch(`/api/map/aircraft?${qs}`, { cache: 'no-store' })
      if (!r.ok) throw new Error(r.statusText || String(r.status))
      const data = await r.json()
      const list = (data.aircraft || []).filter(ac => finite(ac.lat) && finite(ac.lon))
      const now = Date.now()
      const seen = new Set()
      const changed = []
      const markers = markersRef.current

      list.forEach(ac => {
        const key = keyOf(ac)
        if (seen.has(key)) return
        seen.add(key)
        const lat = Number(ac.lat), lon = Number(ac.lon)
        let rec = markers.get(key)
        if (!rec) {
          const marker = L.marker([lat, lon], { icon: aircraftIcon(ac), interactive: true, keyboard: false })
          marker._ac = ac
          marker.on('click', (e) => { L.DomEvent.stopPropagation(e); focusAircraftRef.current(marker._ac, { fly: false }) })
          rec = { marker, layer: null, perm: null, cls: null, tracked: false }
          markers.set(key, rec)
        } else {
          rec.marker._ac = ac
          rec.marker.setLatLng([lat, lon])
        }
        applyStyle(key, rec)
        if (trailsRef.current.push(key, lat, lon, ac.alt_baro, now)) changed.push(key)
        if (key === selectedKeyRef.current) { setSelectedAc(ac); redrawLeg(ac) }
      })

      for (const [key, rec] of markers) {
        if (seen.has(key)) continue
        rec.layer?.removeLayer(rec.marker)
        markers.delete(key)
      }
      const removed = trailsRef.current.prune(now)
      removed.forEach(k => trailsDrawRef.current?.remove(k))
      trailsDrawRef.current?.sync(wantTrail, changed, { emphasis: k => k === selectedKeyRef.current })

      setAircraft(list)
      setCounts({ matched: 0, local: 0, swim: 0, conflict: 0, ...(data.counts || {}) })
      setSwimAvail(data.swim_available !== false)
      setError(null)
    } catch (e) { setError(`AIRCRAFT: ${e.message}`) }
  }, [applyStyle, redrawLeg, wantTrail])
  const refreshRef = useRef(refreshAircraft)
  refreshRef.current = refreshAircraft
  const focusAircraftRef = useRef(focusAircraft)
  focusAircraftRef.current = focusAircraft

  const refreshTfrs = useCallback(async () => {
    const g = groups.current
    if (!g.tfr) return
    try {
      const r = await fetch('/api/dispatch/api/v1/tfr')
      if (!r.ok) return
      const tfrs = await r.json()
      if (!Array.isArray(tfrs)) return
      g.tfr.clearLayers()
      tfrs.forEach(tfr => {
        const lat = tfr.center_lat, lon = tfr.center_lon
        if (!lat || !lon) return
        const radius_m = tfr.radius_nm ? tfr.radius_nm * NM_TO_M : 9260
        const color = tfr.is_vip ? '#ff3131' : '#ff6b35'
        L.circle([lat, lon], { radius: radius_m, color, weight: tfr.is_vip ? 2 : 1, fill: true, fillOpacity: 0.12 })
          .addTo(g.tfr)
          .bindTooltip(`${tfr.is_vip ? 'VIP TFR: ' : 'TFR: '}${tfr.tfr_id}`, { className: 'tfr-tooltip' })
      })
      setTfrCount(tfrs.length)
      setTfrExtra(tfrs.filter(t => t.is_vip).map(t => `VIP TFR: ${t.tfr_id}`))
    } catch (_) {}
  }, [])

  const refreshAirmets = useCallback(async () => {
    const g = groups.current
    if (!g.airmet) return
    try {
      const r = await fetch('/api/dispatch/api/v1/airmets')
      if (!r.ok) return
      const data = await r.json()
      const airmets = data.airmets || []
      g.airmet.clearLayers()
      airmets.forEach(a => {
        if (!a.coords || a.coords.length < 3) return
        const isSigmet = a.type === 'SIGMET'
        L.polygon(a.coords, {
          color: a.color || '#9ca3af', weight: isSigmet ? 2 : 1, fill: true,
          fillOpacity: isSigmet ? 0.14 : 0.08, dashArray: isSigmet ? null : '4 4',
        })
          .addTo(g.airmet)
          .bindTooltip(
            `<b>${a.type}: ${a.hazard.replace(/_/g, ' ')}</b><br/>` +
            `${a.altitude_low ?? 'SFC'}-${a.altitude_high ?? '?'}ft` + (a.severity ? ` · sev ${a.severity}` : ''),
            { className: 'tfr-tooltip' }
          )
      })
      setAirmetCount(airmets.length)
    } catch (_) {}
  }, [])

  useVisibilityAwareInterval(refreshAircraft, POLL_MS)
  useVisibilityAwareInterval(refreshTfrs, 60_000)
  useVisibilityAwareInterval(refreshAirmets, 300_000)  // matches the 5-min server-side cache TTL

  useEffect(() => {
    if (liveState?.tfr_count !== undefined) setTfrCount(liveState.tfr_count)
  }, [liveState])

  // Watchlist membership changed -> restyle (tracked glow / layer) without waiting for the next poll.
  useEffect(() => {
    for (const [key, rec] of markersRef.current) applyStyle(key, rec)
  }, [callsignSet, hexSet, applyStyle])

  // ── Layer toggles ───────────────────────────────────────────────────────
  useEffect(() => {
    const map = leafletRef.current
    if (!map) return
    const g = groups.current
    const show = (layer, on) => { if (!layer) return; on ? map.addLayer(layer) : map.removeLayer(layer) }
    map._airspaceLayers?.forEach(c => show(c, layers.airspace !== false))
    map._ringLayers?.forEach(r => show(r, layers.rings !== false))
    map._scopeLayers?.forEach(r => show(r, layers.rings !== false))
    show(g.aircraft, layers.localFeed !== false)
    show(g.tracked,  layers.tracked !== false)
    show(g.tfr,      layers.tfr !== false)
    show(g.airmet,   layers.airmet !== false)
    show(g.trails,   layers.localFeed !== false || layers.tracked !== false)
    trailsDrawRef.current?.sync(wantTrail, null, { emphasis: k => k === selectedKeyRef.current })
  }, [layers.airspace, layers.rings, layers.localFeed, layers.tracked, layers.tfr, layers.airmet, layers.trails, wantTrail])

  // Panel open/close changes the map box -> tell Leaflet.
  useEffect(() => {
    const id = setTimeout(() => leafletRef.current?.invalidateSize(), 60)
    return () => clearTimeout(id)
  }, [listCollapsed])

  const toggleList = useCallback(() => setListCollapsed(c => {
    try { localStorage.setItem('acmap_list_collapsed', JSON.stringify(!c)) } catch {}
    return !c
  }), [])

  const onSearch = useCallback((e) => {
    e.preventDefault()
    focusIdent(searchInput)
  }, [focusIdent, searchInput])

  // ── Derived / a11y ──────────────────────────────────────────────────────
  const compassItems = useMemo(() => aircraft.map(ac => ({ lat: ac.lat, lon: ac.lon, label: displayName(ac), tracked: isTracked(ac) })),
    [aircraft, isTracked, callsignSet, hexSet])
  const compassSummary = useCompassSummary(compassItems, tfrExtra, center)
  const trackedCount = compassItems.filter(a => a.tracked).length
  const tableRows = useMemo(() => aircraft.map(ac => ({
    id: keyOf(ac), callsign: displayName(ac), hex: ac.hex || '', alt: fmtAlt(ac.alt_baro),
    src: SRC_LABEL[ac.src] || ac.src, lat: Number(ac.lat).toFixed(4), lon: Number(ac.lon).toFixed(4),
    tracked: isTracked(ac) ? 'Yes' : '', conflict: ac.conflict ? 'Yes' : '',
  })), [aircraft, isTracked, callsignSet, hexSet])

  const selectedHex = selectedAc?.hex || lastSeen?.hex || null
  const tar1090Href = tar1090Link(scope?.tar1090_url, selectedHex)
  const sourceLabel = swimAvail === false ? 'Receiver only' : 'Receiver + SWIM'

  return (
    <div className={`map-with-sidebar acmap${listCollapsed ? ' acmap-list-collapsed' : ''}`}>
      <LayerSidebar />
      <div className="map-container">
        <AriaCompassRegion
          summary={compassSummary}
          entityType="aircraft"
          count={aircraft.length}
          extra={[
            swimAvail === false ? 'SWIM unavailable, receiver only.' : '',
            tfrCount > 0 ? `${tfrCount} active TFR${tfrCount !== 1 ? 's' : ''}.` : '',
          ].filter(Boolean).join(' ')}
        />
        <AccessibleTable
          id="aircraft-map-table"
          caption={`Aircraft in view -- ${aircraft.length} (${sourceLabel})`}
          columns={[
            { key: 'callsign', label: 'Callsign' },
            { key: 'hex',      label: 'Hex' },
            { key: 'alt',      label: 'Altitude' },
            { key: 'src',      label: 'Source' },
            { key: 'lat',      label: 'Latitude' },
            { key: 'lon',      label: 'Longitude' },
            { key: 'tracked',  label: 'Tracked' },
            { key: 'conflict', label: 'Conflict' },
          ]}
          rows={tableRows}
          emptyMsg="No aircraft currently in view."
        />
        <div ref={mapRef} className="leaflet-map" />

        <form className="acmap-search" onSubmit={onSearch} role="search">
          <input
            className="acmap-search-input"
            type="search"
            placeholder="Hex, callsign or tail"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            aria-label="Focus an aircraft by hex, callsign or tail"
            autoComplete="off" autoCorrect="off" autoCapitalize="characters" spellCheck={false}
          />
          <button type="submit" className="acmap-search-btn" aria-label="Focus aircraft">GO</button>
          {(selectedAc || lastSeen) && (
            <button type="button" className="acmap-search-clear" onClick={clearSelection} aria-label="Clear selection">&times;</button>
          )}
        </form>
        {focusMsg && <div className="acmap-focus-msg" role="status">{focusMsg}</div>}

        <WatchlistBadge entries={watchEntries} onFocus={focusIdent} />

        <div className="acmap-corner-br">
          {listCollapsed && (
            <button className="acl-pill acls-pill" onClick={toggleList} aria-expanded="false" aria-controls="acmap-list" title="Show aircraft list">
              LIST ({aircraft.length})
            </button>
          )}
          <Legend />
        </div>

        <div className="map-overlay-stats">
          <span className="stat">{aircraft.length} AC</span>
          {trackedCount > 0 && <span className="stat tracked-stat">&#9733; {trackedCount} TRACKED</span>}
          {counts.conflict > 0 && <span className="stat error" title="callsign/registration disagreement between receiver and SWIM">{counts.conflict} CONFLICT</span>}
          <span className="stat">{tfrCount} TFR{tfrCount !== 1 ? 's' : ''}</span>
          <span className="stat">{airmetCount} AIRMET/SIGMET</span>
          {error && <span className="stat error">{error}</span>}
          <span className={`stat source-badge ${swimAvail === false ? 'rcv-only' : 'rcv-swim'}`}
            title={swimAvail === false ? 'Dispatch SWIM feed unreachable; local receiver only' : `matched ${counts.matched} · receiver only ${counts.local} · SWIM only ${counts.swim}`}>
            {sourceLabel}{swimAvail !== false && ` · ${counts.matched}/${counts.local}/${counts.swim}`}
          </span>
          {tar1090Href && (
            <a className="stat acmap-tar-link" href={tar1090Href} target="_blank" rel="noopener noreferrer"
              title={selectedHex ? `tar1090 on ${selectedHex}` : 'Open the receiver map'}>
              Open full receiver map
            </a>
          )}
        </div>
      </div>

      {!listCollapsed && (
        <AircraftList
          aircraft={aircraft}
          selectedKey={selectedKey}
          keyOf={keyOf}
          isTracked={isTracked}
          onFocus={focusAircraft}
          onCollapse={toggleList}
        />
      )}
    </div>
  )
}
