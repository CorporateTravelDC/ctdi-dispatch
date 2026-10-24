/**
 * UtmMapView -- THE drone / Remote ID map (2026-10-24, operator-approved map rework, parity with aviation).
 *
 * Was already Leaflet-only (2026-08-30, no third-party embed exists for UAS); this pass adds the
 * same experience as the aircraft map: sortable list panel, click-to-focus (markers, list rows,
 * watchlist chips of entry_type "drone"), client-side trails per UAS id and a legend. Backend is
 * unchanged: /api/utm/drones (runner _norm_drone schema) -- uas_id, lat, lon, alt_m, speed_ms,
 * operator_lat, operator_lon. No timestamp per drone, so "age" is the time since this client last
 * saw the position change. When the selected drone carries an operator position it is drawn as a
 * square with a dashed line to the aircraft. Renders the honest empty state (source:"none") until a
 * receiver or USS API is configured.
 */
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import AriaCompassRegion from './AriaCompassRegion.jsx'
import AccessibleTable   from './AccessibleTable.jsx'
import UpcomingFeatureWatermark from './UpcomingFeatureWatermark.jsx'
import { useCompassSummary } from '../hooks/useCompassSummary.js'
import { useWatchlist } from '../hooks/useWatchlist.js'
import { useDemoStatus } from '../hooks/useDemoStatus.js'
import { useReceiverLocation } from '../hooks/useReceiverLocation.js'
import { useVisibilityAwareInterval } from '../hooks/useVisibilityAwareInterval.js'
import { OSM_TILE_URL, osmTileOptions } from '../mapTiles.js'
import { TrailsStore } from './mapKit/trails.js'
import { useEntityLayer } from './mapKit/useEntityLayer.js'
import { sameIdent } from './mapKit/focus.js'
import { esc, fmtAge, fmtNum, finite, loadBool, saveBool, isPhone } from './mapKit/format.js'
import ListPanel from './mapKit/ListPanel.jsx'
import MapLegend, { Swatch } from './mapKit/MapLegend.jsx'
import WatchChips from './mapKit/WatchChips.jsx'
import { DRONE_SVG } from './mapKit/glyphs.js'

const DEFAULT_CENTER = [38.9, -76.8]
const DEFAULT_ZOOM   = 9
const DRONE_POLL     = 15_000  // faster than AIS's 60s -- UAS positions move fast at low altitude
const FOCUS_ZOOM     = 14

// Fill = altitude band against the Part 107 ceiling (400 ft AGL = 121.9 m). alt_m is whatever the
// source reports (OpenDroneID geodetic or AGL); the band is a quick read, the tooltip has the value.
const ALT_BANDS = [
  { id: 'low',  label: '0-60 m',     color: '#00ff88', test: a => a <= 60 },
  { id: 'mid',  label: '60-120 m',   color: '#ffd700', test: a => a <= 121.9 },
  { id: 'high', label: '>120 m (above Part 107 ceiling)', color: '#ff3040', test: () => true },
]
const ALT_UNKNOWN = { id: 'na', label: 'Altitude unknown', color: '#9aa5b1' }
export function altBand(alt_m) {
  const a = Number(alt_m)
  if (!finite(alt_m)) return ALT_UNKNOWN
  return ALT_BANDS.find(b => b.test(a))
}
const altColor = alt_m => altBand(alt_m).color

function droneIcon(d, { tracked = false, selected = false } = {}) {
  const color  = altColor(d.alt_m)
  const stroke = tracked ? '#003a4a' : '#111'
  const big = tracked || selected
  const s = big ? 18 : 13
  const cls = ['mk-marker', tracked ? 'tracked' : '', selected ? 'selected' : ''].filter(Boolean).join(' ')
  // quad glyph: centre disc + four rotor dots
  const html = `<div class="${cls}" style="width:${s}px;height:${s}px"><svg width="${s}" height="${s}" viewBox="0 0 14 14" overflow="visible">
      <line x1="3" y1="3" x2="11" y2="11" stroke="${stroke}" stroke-width="1.6"/><line x1="11" y1="3" x2="3" y2="11" stroke="${stroke}" stroke-width="1.6"/>
      <circle cx="3" cy="3" r="1.6" fill="${color}"/><circle cx="11" cy="3" r="1.6" fill="${color}"/><circle cx="3" cy="11" r="1.6" fill="${color}"/><circle cx="11" cy="11" r="1.6" fill="${color}"/>
      <circle cx="7" cy="7" r="3.2" fill="${color}" stroke="${stroke}" stroke-width="1.1"/>
    </svg></div>`
  return L.divIcon({ className: '', html, iconSize: [s, s], iconAnchor: [s / 2, s / 2] })
}

const uasId = d => String(d.uas_id || '').trim() || '?'
const keyOf = d => uasId(d) !== '?' ? uasId(d).toUpperCase() : `p:${Number(d.lat).toFixed(5)},${Number(d.lon).toFixed(5)}`
const hasOperator = d => finite(d.operator_lat) && finite(d.operator_lon)

function tooltipHtml(d, { tracked = false } = {}, ageS = null) {
  const band = altBand(d.alt_m)
  const rows = [
    `<span class="act-src" style="color:${band.color}">${esc(band.label)}</span>`,
    `Alt ${fmtNum(d.alt_m, 0, ' m')} &middot; Speed ${fmtNum(d.speed_ms, 1, ' m/s')}`,
    hasOperator(d) ? 'operator position known' : null,
    ageS != null ? `position unchanged ${fmtAge(ageS)}` : null,
  ].filter(Boolean)
  const name = uasId(d)
  if (!tracked) return `<div class="act"><b class="act-name">${esc(name)}</b><br/>${rows.join('<br/>')}</div>`
  return `<div class="ac-tooltip-tracked">
    <img src="${DRONE_SVG}" class="ac-logo ac-logo-fallback" alt="drone"/>
    <div class="ac-tooltip-tracked-info">
      <span class="ac-tracked-badge">&#9733; TRACKED</span>
      <b class="ac-tracked-callsign">${esc(name)}</b>
      <span class="ac-tracked-details">${rows.join('<br/>')}</span>
    </div></div>`
}

function sourceLabel(source) {
  if (source === 'local') return 'OpenDroneID receiver (local)'
  if (source === 'uss')   return 'USS API'
  return null
}

const OPERATOR_ICON = L.divIcon({
  className: '',
  html: '<div class="mk-marker operator" style="width:12px;height:12px"><svg width="12" height="12" viewBox="0 0 12 12"><rect x="1.5" y="1.5" width="9" height="9" rx="1.5" fill="#ffd700" stroke="#111" stroke-width="1.2"/></svg></div>',
  iconSize: [12, 12], iconAnchor: [6, 6],
})

export default function UtmMapView() {
  const mapRef     = useRef(null)
  const leafletRef = useRef(null)
  const groupsRef  = useRef({})
  const trailsRef  = useRef(new TrailsStore({ keepMs: 10 * 60_000, expireMs: 2 * 60_000, minMove: 5e-6 }))
  const idSetRef   = useRef(new Set())

  const [drones,       setDrones]       = useState([])
  const [dataSource,   setDataSource]   = useState('none')
  const [loadErr,      setLoadErr]      = useState(false)
  const [focusMsg,     setFocusMsg]     = useState(null)
  const [allTrails,    setAllTrails]    = useState(() => loadBool('utmmap_all_trails', false))
  const [listCollapsed, setListCollapsed] = useState(() => loadBool('utmmap_list_collapsed', isPhone))

  const { entries: watchEntries } = useWatchlist()
  const [demoStatus] = useDemoStatus()
  // Same isDemo posture as AisMapView -- withhold the live fetch until we positively know this
  // isn't an untrusted demo visitor.
  const isDemo = demoStatus === null ||
    (demoStatus.demo_mode === true && demoStatus.trusted_origin !== true)

  const watchDrones = useMemo(() => watchEntries.filter(e => e.entry_type === 'drone' && e.identifier), [watchEntries])
  const droneIdSet = useMemo(() => new Set(watchDrones.map(e => e.identifier.trim().toUpperCase())), [watchDrones])
  idSetRef.current = droneIdSet

  const isTracked = useCallback(d => idSetRef.current.has(uasId(d).toUpperCase()), [])
  const posAge = useCallback(d => {
    const t = trailsRef.current.lastMoved(keyOf(d))
    return t == null ? null : (Date.now() - t) / 1000
  }, [])

  // Operator position for the selected drone (square + dashed line), redrawn on every refresh.
  const drawOperator = useCallback((d) => {
    const g = groupsRef.current.leg
    if (!g) return
    g.clearLayers()
    if (!d || !hasOperator(d)) return
    const op = [Number(d.operator_lat), Number(d.operator_lon)]
    L.polyline([op, [Number(d.lat), Number(d.lon)]], { color: '#ffd700', weight: 2, opacity: 0.75, dashArray: '6 6', interactive: false }).addTo(g)
    L.marker(op, { icon: OPERATOR_ICON, interactive: false }).addTo(g)
      .bindTooltip('OPERATOR', { className: 'airport-label', permanent: true, direction: 'bottom' })
  }, [])

  const layer = useEntityLayer({
    getMap: () => leafletRef.current,
    groupsRef,
    keyOf,
    icon: droneIcon,
    tooltip: (d, st) => tooltipHtml(d, st, posAge(d)),
    isTracked,
    trails: trailsRef.current,
    trailValue: d => d.alt_m,
    trailColor: altColor,
    allTrails,
    focusZoom: FOCUS_ZOOM,
    onSelect: drawOperator,
  })
  const clearRef = useRef(layer.clearSelection)
  clearRef.current = layer.clearSelection

  useEffect(() => {
    if (isDemo) return
    if (leafletRef.current) return
    if (!mapRef.current) return
    const map = L.map(mapRef.current, { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true })
    L.tileLayer(OSM_TILE_URL, osmTileOptions({ className: 'map-tiles' })).addTo(map)
    const g = groupsRef.current
    g.trails  = L.layerGroup().addTo(map)
    g.leg     = L.layerGroup().addTo(map)
    g.main    = L.layerGroup().addTo(map)
    g.tracked = L.layerGroup().addTo(map)
    map.on('click', () => clearRef.current?.())
    leafletRef.current = map
  }, [isDemo])

  const refreshDrones = useCallback(async () => {
    if (isDemo) return
    if (!groupsRef.current.main) return
    try {
      const r = await fetch('/api/utm/drones', { cache: 'no-store' })
      if (!r.ok) { setLoadErr(true); return }
      const { source, drones: raw } = await r.json()
      const list = (raw || []).filter(d => finite(d.lat) && finite(d.lon))
      layer.sync(list)
      setDrones(list)
      setDataSource(source || 'none')
      setLoadErr(false)
    } catch (_) { setLoadErr(true) }
  }, [isDemo, layer.sync])

  useVisibilityAwareInterval(refreshDrones, DRONE_POLL, !isDemo)

  useEffect(() => { layer.restyleAll() }, [droneIdSet, allTrails, layer.restyleAll])
  useEffect(() => { const id = setTimeout(() => leafletRef.current?.invalidateSize(), 60); return () => clearTimeout(id) }, [listCollapsed])

  const toggleList   = useCallback(() => setListCollapsed(c => { saveBool('utmmap_list_collapsed', !c); return !c }), [])
  const toggleTrails = useCallback(() => setAllTrails(c => { saveBool('utmmap_all_trails', !c); return !c }), [])

  const focusEntry = useCallback((e) => {
    setFocusMsg(null)
    if (!layer.focusWhere(d => sameIdent(uasId(d), e.identifier))) setFocusMsg(`${e.identifier}: not in the current Remote ID picture`)
  }, [layer.focusWhere])

  const receiverLoc = useReceiverLocation()
  const compassItems = useMemo(() => drones.map(d => ({ lat: d.lat, lon: d.lon, label: uasId(d), tracked: isTracked(d) })), [drones, isTracked, droneIdSet])
  const compassSummary = useCompassSummary(compassItems, [], receiverLoc)
  const trackedCount = compassItems.filter(d => d.tracked).length
  const tableRows = useMemo(() => drones.map(d => ({
    id: uasId(d), alt: fmtNum(d.alt_m, 0), spd: fmtNum(d.speed_ms, 1),
    lat: Number(d.lat).toFixed(4), lon: Number(d.lon).toFixed(4), tracked: isTracked(d) ? 'Yes' : '',
  })), [drones, isTracked, droneIdSet])

  const src = sourceLabel(dataSource)
  const sel = layer.selected

  const listCols = useMemo(() => [
    { key: 'id',  label: 'UAS ID', width: '1.8fr', get: uasId, sort: d => uasId(d).toUpperCase() },
    { key: 'alt', label: 'ALT m',  width: '0.8fr', get: d => fmtNum(d.alt_m, 0), sort: d => finite(d.alt_m) ? Number(d.alt_m) : -1, numericDesc: true },
    { key: 'spd', label: 'M/S',    width: '0.8fr', get: d => fmtNum(d.speed_ms, 1), sort: d => finite(d.speed_ms) ? Number(d.speed_ms) : -1, numericDesc: true },
    { key: 'age', label: 'AGE',    width: '0.7fr', get: d => fmtAge(posAge(d)), sort: d => posAge(d) ?? 1e9, className: 'dim' },
    { key: 'op',  label: 'OP',     width: '0.5fr', get: d => hasOperator(d) ? 'Y' : '--', sort: d => hasOperator(d) ? 0 : 1, className: 'dim' },
  ], [posAge])

  const legendSections = [
    { title: 'Fill = altitude', rows: [...ALT_BANDS, ALT_UNKNOWN].map(b => ({ swatch: <Swatch color={b.color} shape="dot" />, text: b.label })) },
    { title: 'Marks', rows: [
      { swatch: <Swatch color="#ffd700" />, text: <><b>Operator</b> square + dashed line, for the selected drone when reported</> },
      { swatch: <Swatch color="#00d4ff" shape="dot" glow />, text: <><b>Watchlist</b> cyan glow, larger</> },
      { swatch: <Swatch color="#ffd700" shape="line" />, text: <><b>Trail</b> coloured by altitude band; selected + watchlist, TRAILS draws all (10 min, this screen's own history)</> },
    ] },
  ]

  return (
    <div className="mk-view utm-view">
      <div className="mk-subnav">
        <span className="mk-title">UTM</span>
        {sel && <span className="mk-sel-name">{uasId(sel)}</span>}
        <span className="mk-subnav-right">
          {sel && <button className="mk-tool-btn" onClick={layer.clearSelection} aria-label="Clear selection">&times;</button>}
        </span>
      </div>

      {isDemo ? (
        <div className="mk-stage ais-demo-placeholder-wrap">
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
      <div className={`mk-stage${listCollapsed ? ' mk-list-collapsed' : ''}`}>
        <div className="mk-map-wrap">
          <AriaCompassRegion summary={compassSummary} entityType="drones" count={drones.length}
            extra="Part 107 UAS / Remote ID, DC-area coverage once a receiver is configured." />
          <AccessibleTable
            id="utm-drone-table"
            caption={`Drones in range -- ${drones.length} visible`}
            columns={[
              { key: 'id', label: 'UAS ID' }, { key: 'alt', label: 'Altitude m' }, { key: 'spd', label: 'Speed m/s' },
              { key: 'lat', label: 'Latitude' }, { key: 'lon', label: 'Longitude' }, { key: 'tracked', label: 'Tracked' },
            ]}
            rows={tableRows}
            emptyMsg={dataSource === 'none'
              ? 'No UTM source configured yet — no local receiver or USS API wired in.'
              : 'No drones currently visible.'}
          />

          <div ref={mapRef} className="mk-map" />

          <div className="mk-tools">
            <button className={`mk-tool-btn${allTrails ? ' on' : ''}`} onClick={toggleTrails} aria-pressed={allTrails} title="Draw every drone's trail">TRAILS</button>
          </div>
          {focusMsg && <div className="mk-focus-msg" role="status">{focusMsg}</div>}

          <WatchChips entries={watchDrones} onFocus={focusEntry} storageKey="utmmap_wl" unit="drones" />

          {dataSource === 'none' && (
            <UpcomingFeatureWatermark
              label="UTM / Drone Remote ID"
              detail="No local receiver or USS API configured yet"
            />
          )}

          <div className="mk-corner-br">
            {listCollapsed && (
              <button className="mk-pill" onClick={toggleList} aria-expanded="false" aria-controls="utmmap-list" title="Show drone list">
                LIST ({drones.length})
              </button>
            )}
            <MapLegend id="utmmap-legend" storageKey="utmmap_legend_collapsed" sections={legendSections} />
          </div>

          <div className="map-overlay-stats mk-stats">
            {dataSource === 'none'
              ? <span className="stat source-badge" style={{ color: 'var(--muted)' }}>No UTM source configured</span>
              : drones.length > 0
                ? <span className="stat source-badge" style={{ color: '#ff9100' }}>{drones.length} drones</span>
                : <span className="stat source-badge" style={{ color: 'var(--muted)' }}>No live UTM data</span>}
            {trackedCount > 0 && <span className="stat tracked-stat">&#9733; {trackedCount} TRACKED</span>}
            {src && <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>{src}</span>}
            {loadErr && <span className="stat error">UTM feed error</span>}
            <button className="intel-refresh-btn" onClick={refreshDrones} title="Refresh drones">&#8635;</button>
          </div>
        </div>

        {!listCollapsed && (
          <ListPanel
            id="utmmap-list"
            title="DRONES"
            items={drones}
            columns={listCols}
            keyOf={keyOf}
            selectedKey={layer.selectedKey}
            isTracked={isTracked}
            onFocus={layer.focus}
            onCollapse={toggleList}
            storageKey="utmmap_list_sort"
            defaultSort={{ key: 'id', dir: 1 }}
            filterText={uasId}
            filterPlaceholder="Filter UAS ID"
            emptyMsg={dataSource === 'none' ? 'No UTM source configured yet.' : 'No drones in the current picture.'}
          />
        )}
      </div>
      )}
    </div>
  )
}
