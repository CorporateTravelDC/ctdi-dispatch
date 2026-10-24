/**
 * AisMapView -- THE vessel map (2026-10-24, operator-approved map rework, parity with aviation).
 *
 * The MarineTraffic iframe background and the LIVE/MAP toggle are gone: one Leaflet map (OSM base +
 * OpenSeaMap seamarks, both through mapTiles.js' Referer fix) draws OUR /api/ais/vessels layer, with
 * a sortable list panel, click-to-focus (markers, list rows, watchlist chips), client-side trails per
 * MMSI and a legend. Third-party is a link only: "Open in MarineTraffic" for the selected vessel
 * (MMSI-based details URL -- identifier-safe for vessels per the operator).
 *
 * Fields consumed (runner _norm_vessel schema, every source): mmsi, name, lat, lon, sog (kt),
 * cog (deg), hdg (deg, 511 = n/a), nav_status (AIS 0-15), ship_type (AIS 2-digit code). The payload
 * carries no per-vessel timestamp, so "position age" is the time since THIS client last saw the
 * position change (mapKit/trails.js lastMoved).
 *
 * Demo mode is untouched: no map, no fetch, the static placeholder image (see isDemo below).
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
import { OSM_TILE_URL, TILE_PRIVACY, osmTileOptions } from '../mapTiles.js'
import { TrailsStore } from './mapKit/trails.js'
import { useEntityLayer } from './mapKit/useEntityLayer.js'
import { sameIdent } from './mapKit/focus.js'
import { esc, fmtAge, fmtNum, finite, loadBool, saveBool, isPhone } from './mapKit/format.js'
import ListPanel from './mapKit/ListPanel.jsx'
import MapLegend, { Swatch } from './mapKit/MapLegend.jsx'
import WatchChips from './mapKit/WatchChips.jsx'
import { VESSEL_SVG, YACHT_SVG } from './mapKit/glyphs.js'

const OSM_NAUTICAL_URL  = 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png'
const OSM_NAUTICAL_ATTR = '&copy; <a href="https://www.openseamap.org">OpenSeaMap</a>'

// Chesapeake / Potomac default view (public water, not the receiver); the real receiver centre
// from useReceiverLocation is only used for the compass summary.
const DEFAULT_CENTER = [38.9, -76.8]
const DEFAULT_ZOOM   = 8
const VESSEL_POLL    = 60_000
const FOCUS_ZOOM     = 12

// ── Styling: fill = ship type (AIS 2-digit code, MarineTraffic-like families) ──────────────────
const TYPE_FAMILIES = [
  { id: 'cargo',     label: 'Cargo',                 color: '#4caf50', test: t => t >= 70 && t <= 79 },
  { id: 'tanker',    label: 'Tanker',                color: '#e53935', test: t => t >= 80 && t <= 89 },
  { id: 'passenger', label: 'Passenger',             color: '#1e88e5', test: t => t >= 60 && t <= 69 },
  { id: 'hsc',       label: 'High-speed craft',      color: '#ffeb3b', test: t => t >= 40 && t <= 49 },
  { id: 'service',   label: 'Tug / pilot / service', color: '#26c6da', test: t => t === 31 || t === 32 || (t >= 50 && t <= 59) },
  { id: 'fishing',   label: 'Fishing',               color: '#ff9800', test: t => t === 30 },
  { id: 'pleasure',  label: 'Sailing / pleasure',    color: '#ab47bc', test: t => t === 36 || t === 37 },
  { id: 'military',  label: 'Military',              color: '#78909c', test: t => t === 35 },
]
const UNKNOWN_TYPE = { id: 'other', label: 'Other / unknown', color: '#9aa5b1' }
export function typeFamily(ship_type) {
  const t = Number(ship_type)
  if (!Number.isFinite(t)) return UNKNOWN_TYPE
  return TYPE_FAMILIES.find(f => f.test(t)) || UNKNOWN_TYPE
}

// ── Nav status (AIS 0-15) ──────────────────────────────────────────────────────────────────────
const NAV_LABEL = {
  0: 'Under way (engine)', 1: 'At anchor', 2: 'Not under command', 3: 'Restricted manoeuvrability',
  4: 'Constrained by draught', 5: 'Moored', 6: 'Aground', 7: 'Fishing', 8: 'Under way (sailing)',
  11: 'Towing astern', 12: 'Pushing ahead', 14: 'AIS-SART', 15: 'Undefined',
}
const NAV_SHORT = { 0: 'UWY', 1: 'ANC', 2: 'NUC', 3: 'RIM', 4: 'CBD', 5: 'MOR', 6: 'AGR', 7: 'FSH', 8: 'SAIL', 15: '--' }
const navLabel = s => NAV_LABEL[Number(s)] || (s == null ? 'Undefined' : `Status ${s}`)
const navShort = s => NAV_SHORT[Number(s)] || (s == null ? '--' : String(s))
const isStopped = v => [1, 5, 6].includes(Number(v.nav_status)) || (finite(v.sog) && Number(v.sog) < 0.2 && glyphHeading(v) == null)
const isImpaired = v => [2, 3, 4, 6].includes(Number(v.nav_status))

// ── Trail colour = speed over ground ──────────────────────────────────────────────────────────
const SOG_STOPS = [
  { max: 2,        color: '#8a9bb0', label: '<2 kt' },
  { max: 8,        color: '#4a9eff', label: '2-8' },
  { max: 14,       color: '#00ff88', label: '8-14' },
  { max: 20,       color: '#ffd700', label: '14-20' },
  { max: Infinity, color: '#ff9100', label: '>20 kt' },
]
export function sogColor(sog) {
  if (!finite(sog)) return '#8a9bb0'
  return SOG_STOPS.find(s => Number(sog) < s.max).color
}
const SOG_BAR_CSS = `linear-gradient(90deg, ${SOG_STOPS.map((s, i) => `${s.color} ${i / SOG_STOPS.length * 100}% ${(i + 1) / SOG_STOPS.length * 100}%`).join(', ')})`

/** Heading for the glyph: true heading when valid (0-359, 511 = not available), else COG. */
function glyphHeading(v) {
  const h = Number(v.hdg)
  if (Number.isFinite(h) && h >= 0 && h < 360) return h
  const c = Number(v.cog)
  if (Number.isFinite(c) && c >= 0 && c < 360) return c
  return null
}

function vesselIcon(v, { tracked = false, selected = false } = {}) {
  const fam = typeFamily(v.ship_type)
  const big = tracked || selected
  const w = big ? 20 : 14, h = big ? 26 : 18
  const stroke = tracked ? '#003a4a' : '#111'
  const dash = isImpaired(v) ? 'stroke-dasharray="2 1.5"' : ''
  const cls = ['mk-marker', tracked ? 'tracked' : '', selected ? 'selected' : ''].filter(Boolean).join(' ')
  let body
  if (isStopped(v)) {
    body = `<circle cx="6" cy="9" r="4.5" fill="${fam.color}" stroke="${stroke}" stroke-width="1.2" ${dash} opacity="0.95"/>`
  } else {
    const deg = glyphHeading(v) ?? 0
    body = `<g transform="rotate(${deg.toFixed(0)} 6 9)"><polygon points="6,0 12,14 6,18 0,14" fill="${fam.color}" stroke="${stroke}" stroke-width="1.2" ${dash} stroke-linejoin="round" opacity="0.95"/></g>`
  }
  const html = `<div class="${cls}" style="width:${w}px;height:${h}px"><svg width="${w}" height="${h}" viewBox="-1 -1 14 20" overflow="visible">${body}</svg></div>`
  return L.divIcon({ className: '', html, iconSize: [w, h], iconAnchor: [w / 2, h / 2] })
}

const vesselName = v => (v.name || '').trim() || `MMSI ${v.mmsi || '?'}`
const keyOf = v => String(v.mmsi || '').trim() || `p:${Number(v.lat).toFixed(4)},${Number(v.lon).toFixed(4)}`

function tooltipHtml(v, { tracked = false } = {}, ageS = null) {
  const fam = typeFamily(v.ship_type)
  const rows = [
    `<span class="act-src" style="color:${fam.color}">${esc(fam.label)}${v.ship_type != null ? ' &middot; type ' + esc(v.ship_type) : ''}</span>`,
    `SOG ${fmtNum(v.sog, 1, ' kt')} &middot; COG ${fmtNum(v.cog, 0, '&deg;')} &middot; HDG ${finite(v.hdg) && Number(v.hdg) < 360 ? fmtNum(v.hdg, 0, '&deg;') : '--'}`,
    `${esc(navLabel(v.nav_status))}${v.mmsi ? ' &middot; MMSI ' + esc(v.mmsi) : ''}`,
    ageS != null ? `position unchanged ${fmtAge(ageS)}` : null,
  ].filter(Boolean)
  const name = vesselName(v)
  if (!tracked) return `<div class="act"><b class="act-name">${esc(name)}</b><br/>${rows.join('<br/>')}</div>`
  return `<div class="ac-tooltip-tracked">
    <img src="${(typeFamily(v.ship_type).id === 'pleasure' ? YACHT_SVG : VESSEL_SVG)}" class="ac-logo ac-logo-fallback" alt="vessel"/>
    <div class="ac-tooltip-tracked-info">
      <span class="ac-tracked-badge">&#9733; TRACKED</span>
      <b class="ac-tracked-callsign">${esc(name)}</b>
      <span class="ac-tracked-details">${rows.join('<br/>')}</span>
    </div></div>`
}

function sourceLabel(source) {
  if (source === 'local')             return 'AIS-catcher (local)'
  if (source === 'marinetraffic.com') return 'MarineTraffic API'
  if (source === 'aishub.net')        return 'AISHub'
  return null
}

/** MMSI for a watchlist entry: "mmsi: 123456789" in notes, or a bare 9-digit identifier. */
function entryMmsi(e) {
  const m = e.notes?.match(/mmsi:\s*(\d{9})/i)
  if (m) return m[1]
  if (e.identifier && /^\d{9}$/.test(e.identifier.trim())) return e.identifier.trim()
  return null
}

const LIST_COLS = [
  { key: 'name', label: 'VESSEL', width: '1.6fr', get: vesselName, sort: v => vesselName(v).toUpperCase() },
  { key: 'mmsi', label: 'MMSI',   width: '1.2fr', get: v => v.mmsi || '--', className: 'dim' },
  { key: 'sog',  label: 'SOG',    width: '0.7fr', get: v => fmtNum(v.sog, 1), sort: v => finite(v.sog) ? Number(v.sog) : -1, numericDesc: true },
  { key: 'type', label: 'TYPE',   width: '1fr',   get: v => typeFamily(v.ship_type).label.split(' ')[0], sort: v => typeFamily(v.ship_type).id },
  { key: 'nav',  label: 'NAV',    width: '0.7fr', get: v => navShort(v.nav_status), sort: v => Number(v.nav_status ?? 15), className: 'dim' },
]

export default function AisMapView() {
  const mapRef      = useRef(null)
  const leafletRef  = useRef(null)
  const groupsRef   = useRef({})
  const trailsRef   = useRef(new TrailsStore({ keepMs: 60 * 60_000, expireMs: 10 * 60_000, minMove: 2e-5 }))
  const mmsiSetRef  = useRef(new Set())

  const [vessels,      setVessels]      = useState([])
  const [dataSource,   setDataSource]   = useState('none')
  const [loadErr,      setLoadErr]      = useState(false)
  const [focusMsg,     setFocusMsg]     = useState(null)
  const [allTrails,    setAllTrails]    = useState(() => loadBool('aismap_all_trails', false))
  const [listCollapsed, setListCollapsed] = useState(() => loadBool('aismap_list_collapsed', isPhone))

  const { entries: watchEntries } = useWatchlist()
  const [demoStatus] = useDemoStatus()
  // Treat "not yet known" (null, still loading) the same as demo mode -- withhold the live fetch
  // until we positively know this ISN'T a demo instance. trusted_origin mirrors App.jsx's demoBanner
  // logic: a Tailscale/trusted visitor to the demo-runner instance still gets the live AIS picture;
  // the placeholder is for untrusted/public demo visitors only. (Unchanged from the iframe era.)
  const isDemo = demoStatus === null ||
    (demoStatus.demo_mode === true && demoStatus.trusted_origin !== true)

  const watchVessels = useMemo(() => watchEntries.filter(e => entryMmsi(e)), [watchEntries])
  const mmsiSet = useMemo(() => new Set(watchVessels.map(entryMmsi)), [watchVessels])
  mmsiSetRef.current = mmsiSet

  const isTracked = useCallback(v => mmsiSetRef.current.has(String(v.mmsi || '').trim()), [])
  const posAge = useCallback(v => {
    const t = trailsRef.current.lastMoved(keyOf(v))
    return t == null ? null : (Date.now() - t) / 1000
  }, [])

  const layer = useEntityLayer({
    getMap: () => leafletRef.current,
    groupsRef,
    keyOf,
    icon: vesselIcon,
    tooltip: (v, st) => tooltipHtml(v, st, posAge(v)),
    isTracked,
    trails: trailsRef.current,
    trailValue: v => v.sog,
    trailColor: sogColor,
    allTrails,
    focusZoom: FOCUS_ZOOM,
  })
  const clearRef = useRef(layer.clearSelection)
  clearRef.current = layer.clearSelection

  // ── Map init: only in the live branch (the ref'd div does not exist in demo) ────────────────
  useEffect(() => {
    if (isDemo) return
    if (leafletRef.current) return
    if (!mapRef.current) return
    const map = L.map(mapRef.current, { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true })
    L.tileLayer(OSM_TILE_URL, osmTileOptions({ className: 'map-tiles' })).addTo(map)
    L.tileLayer(OSM_NAUTICAL_URL, { attribution: OSM_NAUTICAL_ATTR, maxZoom: 18, opacity: 0.85, ...TILE_PRIVACY }).addTo(map)
    const g = groupsRef.current
    g.trails  = L.layerGroup().addTo(map)
    g.main    = L.layerGroup().addTo(map)
    g.tracked = L.layerGroup().addTo(map)
    map.on('click', () => clearRef.current?.())
    leafletRef.current = map
  }, [isDemo])

  const refreshVessels = useCallback(async () => {
    if (isDemo) return  // never fetch live vessel positions in demo mode
    if (!groupsRef.current.main) return
    try {
      const r = await fetch('/api/ais/vessels', { cache: 'no-store' })
      if (!r.ok) { setLoadErr(true); return }
      const { source, vessels: raw } = await r.json()
      const list = (raw || []).filter(v => finite(v.lat) && finite(v.lon))
      layer.sync(list)
      setVessels(list)
      setDataSource(source || 'none')
      setLoadErr(false)
    } catch (_) { setLoadErr(true) }
  }, [isDemo, layer.sync])

  useVisibilityAwareInterval(refreshVessels, VESSEL_POLL, !isDemo)  // no polling loop in demo mode either

  // Watchlist membership / trails toggle -> restyle without waiting for the next poll.
  useEffect(() => { layer.restyleAll() }, [mmsiSet, allTrails, layer.restyleAll])
  useEffect(() => { const id = setTimeout(() => leafletRef.current?.invalidateSize(), 60); return () => clearTimeout(id) }, [listCollapsed])

  const toggleList   = useCallback(() => setListCollapsed(c => { saveBool('aismap_list_collapsed', !c); return !c }), [])
  const toggleTrails = useCallback(() => setAllTrails(c => { saveBool('aismap_all_trails', !c); return !c }), [])

  const focusEntry = useCallback((e) => {
    const mmsi = entryMmsi(e)
    setFocusMsg(null)
    if (!layer.focusWhere(v => sameIdent(v.mmsi, mmsi))) setFocusMsg(`${e.identifier || mmsi}: not in the current AIS picture`)
  }, [layer.focusWhere])

  // ── Derived / a11y ──────────────────────────────────────────────────────────────────────────
  const receiverLoc = useReceiverLocation()
  const compassItems = useMemo(() => vessels.map(v => ({ lat: v.lat, lon: v.lon, label: vesselName(v), tracked: isTracked(v) })), [vessels, isTracked, mmsiSet])
  const compassSummary = useCompassSummary(compassItems, [], receiverLoc)
  const trackedCount = compassItems.filter(v => v.tracked).length
  const tableRows = useMemo(() => vessels.map(v => ({
    name: vesselName(v), mmsi: v.mmsi || '', type: typeFamily(v.ship_type).label, sog: fmtNum(v.sog, 1),
    lat: Number(v.lat).toFixed(4), lon: Number(v.lon).toFixed(4), tracked: isTracked(v) ? 'Yes' : '',
  })), [vessels, isTracked, mmsiSet])

  const src = sourceLabel(dataSource)
  const sel = layer.selected
  const mtHref = sel?.mmsi && /^\d{9}$/.test(String(sel.mmsi)) ? `https://www.marinetraffic.com/en/ais/details/ships/mmsi:${sel.mmsi}` : null

  const legendSections = [
    { title: 'Fill = ship type', rows: [...TYPE_FAMILIES, UNKNOWN_TYPE].map(f => ({ swatch: <Swatch color={f.color} />, text: f.label })) },
    { title: 'Shape = status', rows: [
      { swatch: <Swatch color="#9aa5b1" shape="arrow" />, text: <><b>Arrow</b> under way, points along heading (COG if no heading)</> },
      { swatch: <Swatch color="#9aa5b1" shape="dot" />, text: <><b>Circle</b> anchored / moored / aground / stopped</> },
      { swatch: <Swatch color="#9aa5b1" shape="arrow" dash />, text: <><b>Dashed</b> not under command / restricted / aground</> },
      { swatch: <Swatch color="#00d4ff" shape="dot" glow />, text: <><b>Watchlist</b> cyan glow, larger</> },
    ] },
    { title: 'Trail = speed over ground', bar: { css: SOG_BAR_CSS, labels: SOG_STOPS.map((s, i) => [(i + 0.5) / SOG_STOPS.length * 100, s.label]) },
      rows: [{ swatch: <Swatch color="#00d4ff" shape="line" />, text: 'Selected + watchlist vessels; TRAILS draws all. History is this screen\'s own (up to 60 min).' }] },
  ]

  return (
    <div className="mk-view ais-view">
      <div className="mk-subnav">
        <span className="mk-title">AIS</span>
        {sel && <span className="mk-sel-name" title={sel.mmsi ? `MMSI ${sel.mmsi}` : undefined}>{vesselName(sel)}</span>}
        <span className="mk-subnav-right">
          {mtHref && (
            <a href={mtHref} target="_blank" rel="noopener noreferrer" className="mk-ext-link" title="Vessel details on MarineTraffic (opens in a new tab)">
              Open in MarineTraffic
            </a>
          )}
          {sel && <button className="mk-tool-btn" onClick={layer.clearSelection} aria-label="Clear selection">&times;</button>}
        </span>
      </div>

      {isDemo ? (
        <div className="mk-stage ais-demo-placeholder-wrap">
          <img
            src="/ais-demo-placeholder.jpg"
            alt="AIS / vessel tracking preview -- upcoming feature, shown for demonstration purposes only. Live vessel positions are not shown in demo mode."
            className="ais-demo-placeholder-img"
          />
        </div>
      ) : (
      <div className={`mk-stage${listCollapsed ? ' mk-list-collapsed' : ''}`}>
        <div className="mk-map-wrap">
          <AriaCompassRegion summary={compassSummary} entityType="vessels" count={vessels.length}
            extra="Potomac · Chesapeake · Port of Baltimore." />
          <AccessibleTable
            id="ais-vessel-table"
            caption={`Vessels in range -- ${vessels.length} visible`}
            columns={[
              { key: 'name', label: 'Vessel' }, { key: 'mmsi', label: 'MMSI' }, { key: 'type', label: 'Type' },
              { key: 'sog', label: 'SOG kt' }, { key: 'lat', label: 'Latitude' }, { key: 'lon', label: 'Longitude' },
              { key: 'tracked', label: 'Tracked' },
            ]}
            rows={tableRows}
            emptyMsg="No vessels currently visible."
          />

          <div ref={mapRef} className="mk-map" />

          <div className="mk-tools">
            <button className={`mk-tool-btn${allTrails ? ' on' : ''}`} onClick={toggleTrails} aria-pressed={allTrails} title="Draw every vessel's trail">TRAILS</button>
          </div>
          {focusMsg && <div className="mk-focus-msg" role="status">{focusMsg}</div>}

          <WatchChips entries={watchVessels} onFocus={focusEntry} storageKey="aismap_wl" unit="vessels" />

          {dataSource === 'none' && (
            <UpcomingFeatureWatermark
              label="AIS / Vessel Tracking"
              detail="No local AIS-catcher or AISHub source configured yet"
            />
          )}

          <div className="mk-corner-br">
            {listCollapsed && (
              <button className="mk-pill" onClick={toggleList} aria-expanded="false" aria-controls="aismap-list" title="Show vessel list">
                LIST ({vessels.length})
              </button>
            )}
            <MapLegend id="aismap-legend" storageKey="aismap_legend_collapsed" sections={legendSections} credit="OpenSeaMap seamarks (CC-BY-SA) over OpenStreetMap" />
          </div>

          <div className="map-overlay-stats mk-stats">
            {vessels.length > 0
              ? <span className="stat source-badge" style={{ color: '#4a9eff' }}>{vessels.length} vessels</span>
              : <span className="stat source-badge" style={{ color: 'var(--muted)' }}>No live AIS</span>}
            {trackedCount > 0 && <span className="stat tracked-stat">&#9733; {trackedCount} TRACKED</span>}
            {src && <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>{src}</span>}
            {loadErr && <span className="stat error">AIS feed error</span>}
            <button className="intel-refresh-btn" onClick={refreshVessels} title="Refresh vessels">&#8635;</button>
          </div>
        </div>

        {!listCollapsed && (
          <ListPanel
            id="aismap-list"
            title="VESSELS"
            items={vessels}
            columns={LIST_COLS}
            keyOf={keyOf}
            selectedKey={layer.selectedKey}
            isTracked={isTracked}
            onFocus={layer.focus}
            onCollapse={toggleList}
            storageKey="aismap_list_sort"
            defaultSort={{ key: 'name', dir: 1 }}
            filterText={v => `${vesselName(v)} ${v.mmsi || ''}`}
            filterPlaceholder="Filter name / MMSI"
            emptyMsg={dataSource === 'none' ? 'No AIS source configured yet.' : 'No vessels in the current picture.'}
          />
        )}
      </div>
      )}
    </div>
  )
}
