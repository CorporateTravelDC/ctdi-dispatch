/**
 * TrainMapView -- rail: ONE Leaflet map + the roster/list panel (2026-10-24, operator-approved map
 * rework, parity with aviation).
 *
 * Until now this tab was list-only (the 2026-07-21 split-screen replaced an earlier globe view).
 * The dispatch feed it already consumed, /api/dispatch/api/v1/amtrak, carries a position per train
 * (ingest.amtrak writes amtraker's lat/lon into every trains[] row -- verified live 2026-10-24, 29/29
 * rows had both), so the map is built from that: marker per train (fill = delay state, same colours
 * as the list badges), client-side trail per train number (dispatch refreshes positions every ~5 min,
 * we poll every 60 s, 3 h kept), direction derived from successive positions (the feed has no
 * heading/speed field), click-to-focus from markers, list rows and watchlist chips. The existing
 * Amtrak / regional-roster lists stay as the list panel; roster rows (VRE/MARC permanent entries)
 * have no live feed and are not focusable. Map centre/zoom come from /train-config (operator's
 * primary station -- a public Amtrak station coordinate, never the receiver).
 *
 * Fields consumed per train (either writer's spelling, see the t*() normalisers): train_num |
 * train_number, route | train_name, origin | orig_code, destination | dest_code, status |
 * train_state, station_code/station_name, delay_minutes, dest_scheduled_arr/dest_estimated_arr,
 * lat, lon, train_id. Not in the feed today: heading, velocity, per-train position timestamp,
 * station coordinates -- see the handback report.
 */
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import AriaCompassRegion from './AriaCompassRegion.jsx'
import AccessibleTable   from './AccessibleTable.jsx'
import { useCompassSummary } from '../hooks/useCompassSummary.js'
import { useWatchlist } from '../hooks/useWatchlist.js'
import { useReceiverLocation } from '../hooks/useReceiverLocation.js'
import { useVisibilityAwareInterval } from '../hooks/useVisibilityAwareInterval.js'
import { OSM_TILE_URL, TILE_PRIVACY, osmTileOptions } from '../mapTiles.js'
import { TrailsStore } from './mapKit/trails.js'
import { useEntityLayer } from './mapKit/useEntityLayer.js'
import { esc, fmtAge, finite, loadBool, saveBool, isPhone } from './mapKit/format.js'
import MapLegend, { Swatch } from './mapKit/MapLegend.jsx'
import WatchChips from './mapKit/WatchChips.jsx'
import { TRAIN_SVG } from './mapKit/glyphs.js'

const PANEL_POLL = 60_000
const FOCUS_ZOOM = 11
// Fallback camera until /train-config answers: Washington Union Station (public), same as the
// dispatch API's own default for an unset AMTRAK_PRIMARY_STATION.
const FALLBACK_CENTER = [38.897, -77.006]
const FALLBACK_ZOOM   = 7

// OpenRailwayMap infrastructure overlay (third-party tiles -> TILE_PRIVACY like every other layer).
const ORM_TILE_URL = 'https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png'
const ORM_ATTR     = 'Rail overlay &copy; <a href="https://www.openrailwaymap.org">OpenRailwayMap</a> (CC-BY-SA)'

// ── Hardcoded DC fallback (used before /train-config responds) ────
const _FB_CORE_ROUTES = ['acela', 'northeast regional']

// ── Fetch operator train config from dispatch ─────────────────────
async function fetchTrainConfig() {
  try {
    const r = await fetch('/api/dispatch/api/v1/train-config')
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

// ── Fetch dispatch train list (panel + map data) ──────────────────
async function fetchDispatchTrains() {
  try {
    const r = await fetch('/api/dispatch/api/v1/amtrak', { cache: 'no-store' })
    if (!r.ok) return null
    const d = await r.json()
    return Array.isArray(d.trains) ? d.trains : []
  } catch { return null }
}

// ── Train panel helpers ───────────────────────────────────────────
// Field name normaliser — handles both the local amtrak-tracker container
// format (train_num / route / origin / destination / status) and the
// amtraker.com _normalize() format (train_number / train_name / train_state /
// orig_code / dest_code).
function tNum(t)   { return t.train_number ?? t.train_num ?? null }
function tName(t)  { return t.train_name   || t.route      || null }
function tState(t) { return t.train_state  || t.status     || '' }
function tOrig(t)  { return t.orig_code    || t.origin     || '' }
function tDest(t)  { return t.dest_code    || t.destination || '' }
function tEvent(t) { return t.event_name   || (t._raw && t._raw.eventCode) || '' }
const keyOf = t => String(tNum(t) ?? t.train_id ?? `p:${Number(t.lat).toFixed(3)},${Number(t.lon).toFixed(3)}`)
const hasPos = t => finite(t.lat) && finite(t.lon)

// Final-destination ETA -- added 2026-07-29 per operator request: the panel
// only ever showed delay/status at the REFERENCE station (watchlist>
// regional>primary priority, see tCurrentStationLine below), never when the
// train actually reaches where it's actually going. dest_estimated_arr/
// dest_scheduled_arr are populated by both the primary ingest writer and the
// poller fallback writer as of 2026-07-29; older cached records (or a feed
// error mid-transition) fall back to digging the last station out of the
// raw amtraker record directly.
function tDestEta(t) {
  let sched = t.dest_scheduled_arr
  let est   = t.dest_estimated_arr
  if (!sched && !est) {
    const stations = (t._raw && Array.isArray(t._raw.stations)) ? t._raw.stations : null
    const last = stations && stations.length ? stations[stations.length - 1] : null
    if (last) { sched = last.schArr; est = last.arr }
  }
  const iso = est || sched
  if (!iso) return null
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    const dest = tDest(t)
    return dest ? `ETA ${dest} ${time}` : `ETA ${time}`
  } catch { return null }
}

// Current-station line: "Departed BAL", "At WAS", "Awaiting departure at NYP".
// Primary source: ingest.amtrak's station_code/station_name (added
// 2026-07-21 specifically for this -- it was already resolving this
// reference station internally for delay math but never surfacing it).
// Falls back to the older poller._normalize() shape (event_name/_raw with
// a nested stations[] array) for when the poller's fallback fetcher is
// serving instead of the primary ingest path.
function tCurrentStationLine(t) {
  const name = t.station_name || t.event_name || (t._raw && t._raw.eventName) || null
  if (!name) return null

  let status = (t.status || '').toLowerCase()
  if (!status && t._raw) {
    const code  = t._raw.eventCode || null
    const stations = Array.isArray(t._raw.stations) ? t._raw.stations : []
    const match = code ? stations.find(s => s.code === code) : null
    status = (match && match.status || '').toLowerCase()
  }

  let verb = 'Near'
  if (status === 'station')                                     verb = 'At'
  else if (status === 'enroute')                                verb = 'Departed'
  else if (status === 'predeparture' || status === 'scheduled')  verb = 'Awaiting departure at'
  else if (status === 'arrived' || status === 'completed')       verb = 'Arrived'
  return `${verb} ${name}`
}

// Delay state -> one of six buckets. The list badges use the theme variables (delayColor), the map
// markers and trail segments need literal colours (SVG presentation attributes cannot read var()),
// so DELAY_BUCKETS carries both and the legend is built from the same table.
const DELAY_BUCKETS = [
  { id: 'arr',    label: 'ARR', text: 'Arrived / completed',   css: 'var(--muted)',    hex: '#6b7f96' },
  { id: 'sch',    label: 'SCH', text: 'Scheduled, not departed', css: 'var(--cyan)',   hex: '#00d4ff' },
  { id: 'ot',     label: 'OT',  text: 'On time',               css: 'var(--go)',       hex: '#00ff88' },
  { id: 'minor',  label: '+1-10m',  text: 'Up to 10 min late', css: 'var(--marginal)', hex: '#ffd700' },
  { id: 'mod',    label: '+11-30m', text: '11-30 min late',    css: 'var(--orange)',   hex: '#ff9100' },
  { id: 'severe', label: '>30m',    text: 'More than 30 min late', css: 'var(--nogo)', hex: '#ff3040' },
]
function delayBucket(delay, state) {
  const s = (state || '').toLowerCase()
  if (s === 'completed' || s === 'arrived') return DELAY_BUCKETS[0]
  if (s === 'predeparture' || s === 'scheduled') return DELAY_BUCKETS[1]
  if (delay > 30)  return DELAY_BUCKETS[5]
  if (delay > 10)  return DELAY_BUCKETS[4]
  if (delay > 0)   return DELAY_BUCKETS[3]
  return DELAY_BUCKETS[2]
}
const tBucket = t => delayBucket(t.delay_minutes || 0, tState(t))
function delayColor(delay, state) { return delayBucket(delay, state).css }
function delayLabel(delay, state) {
  const b = delayBucket(delay, state)
  if (b.id === 'arr' || b.id === 'sch' || b.id === 'ot') return b.label
  return `+${delay}m`
}

const COMPASS_DEG = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 }

function trainIcon(t, { tracked = false, selected = false } = {}, heading = null) {
  const b = tBucket(t)
  const num = String(tNum(t) ?? '?')
  const big = tracked || selected
  const s = big ? 40 : 32
  const cls = ['mk-marker', 'train-marker', tracked ? 'tracked' : '', selected ? 'selected' : ''].filter(Boolean).join(' ')
  const pillW = Math.max(18, 6 + num.length * 6)
  const arrow = heading != null
    ? `<g transform="rotate(${heading.toFixed(0)} 16 16)"><polygon points="16,1 20.5,8 11.5,8" fill="${b.hex}" stroke="#111" stroke-width="1"/></g>`
    : ''
  const html = `<div class="${cls}" style="width:${s}px;height:${s}px"><svg width="${s}" height="${s}" viewBox="0 0 32 32" overflow="visible">
      ${arrow}
      <rect x="${16 - pillW / 2}" y="10" width="${pillW}" height="12" rx="3.5" fill="${b.hex}" stroke="${tracked ? '#003a4a' : '#111'}" stroke-width="1.2"/>
      <text x="16" y="19" text-anchor="middle" style="font-family:var(--font-data),monospace" font-size="8.5" font-weight="700" fill="#0a0e14">${esc(num)}</text>
    </svg></div>`
  return L.divIcon({ className: '', html, iconSize: [s, s], iconAnchor: [s / 2, s / 2] })
}

function tooltipHtml(t, { tracked = false } = {}, ageS = null) {
  const b = tBucket(t)
  const num = tNum(t) ?? '?'
  const rawName = tName(t) || `Train ${num}`
  const name = rawName.replace(/\s+\d+$/, '').trim()
  const orig = tOrig(t), dest = tDest(t)
  const rows = [
    `<span class="act-src" style="color:${b.hex}">${esc(delayLabel(t.delay_minutes || 0, tState(t)))} &middot; ${esc(b.text)}</span>`,
    (orig && dest) ? `${esc(orig)} &rarr; ${esc(dest)}` : null,
    tCurrentStationLine(t) ? esc(tCurrentStationLine(t)) : (tEvent(t) ? esc(tEvent(t)) : null),
    tDestEta(t) ? esc(tDestEta(t)) : null,
    ageS != null ? `position unchanged ${fmtAge(ageS)}` : null,
  ].filter(Boolean)
  const title = `${num} ${name}`
  if (!tracked) return `<div class="act"><b class="act-name">${esc(title)}</b><br/>${rows.join('<br/>')}</div>`
  return `<div class="ac-tooltip-tracked">
    <img src="${TRAIN_SVG}" class="ac-logo ac-logo-fallback" alt="train"/>
    <div class="ac-tooltip-tracked-info">
      <span class="ac-tracked-badge">&#9733; TRACKED</span>
      <b class="ac-tracked-callsign">${esc(title)}</b>
      <span class="ac-tracked-details">${rows.join('<br/>')}</span>
    </div></div>`
}

/** Train number out of a watchlist identifier ("2150", "Acela 2150", "NER 86"). */
function entryTrainNum(e) {
  const m = String(e.identifier || '').match(/(\d{1,4})\s*$/)
  return m ? m[1] : null
}

function TrainRow({ t, selected, tracked, onFocus }) {
  const delay    = t.delay_minutes || 0
  const state    = tState(t)
  const color    = delayColor(delay, state)
  const label    = delayLabel(delay, state)
  const num      = tNum(t) ?? '?'
  const rawName  = tName(t) || `Train ${num}`
  const name     = rawName.replace(/\s+\d+$/, '').trim()
  const orig     = tOrig(t)
  const dest     = tDest(t)
  const route    = (orig && dest) ? `${orig}→${dest}` : ''
  const station  = tCurrentStationLine(t)
  const fallbackEvent = !station ? tEvent(t) : ''
  const destEta  = tDestEta(t)
  const focusable = hasPos(t)

  return (
    <button type="button"
      className={`train-row mk-train-row${selected ? ' sel' : ''}${tracked ? ' tracked' : ''}${focusable ? '' : ' nofocus'}`}
      onClick={() => focusable && onFocus(t)} disabled={!focusable} aria-pressed={selected}
      title={focusable ? 'Show on map' : 'No position reported for this train'}>
      <span className="train-row-num" style={{ color }}>{tracked ? '★' : ''}{num}</span>
      <div className="train-row-info">
        <span className="train-row-name">{name}</span>
        {route && <span className="train-row-route">{route}</span>}
        {station && <span className="train-row-station">{station}</span>}
        {fallbackEvent && <span className="train-row-event">{fallbackEvent}</span>}
        {destEta && <span className="train-row-dest-eta">{destEta}</span>}
      </div>
      <span className="train-row-badge" style={{ color, borderColor: color }}>{label}</span>
    </button>
  )
}

const _DOW_ABBR = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/**
 * Static roster row — for VRE/MARC entries, which have no live position/
 * delay feed (unlike Amtrak via amtraker.com). Shows a No Service badge
 * when today's day-of-week isn't in the entry's days_active pattern.
 */
function RosterRow({ e, todayAbbr }) {
  const runsToday = (e.days_active || []).includes(todayAbbr)
  return (
    <div className="train-row" title="Roster entry: no live position feed">
      <span className="train-row-num" style={{ color: runsToday ? 'var(--cyan)' : 'var(--muted)' }}>
        {e.identifier}
      </span>
      <div className="train-row-info">
        <span className="train-row-name">{e.route_name || ''}</span>
        {e.destination && <span className="train-row-route">→{e.destination}</span>}
      </div>
      <span
        className="train-row-badge"
        style={{
          color:       runsToday ? 'var(--go)' : 'var(--muted)',
          borderColor: runsToday ? 'var(--go)' : 'var(--muted)',
        }}
      >
        {runsToday ? 'M-F' : 'No Svc'}
      </span>
    </div>
  )
}

// Best-effort display label for a subsection code. Known DC-area codes get
// their proper acronym casing; anything else (a different operator's own
// subsection tags -- e.g. "metra", "sbahn", "citybus") falls back to
// upper-casing the raw string so a new operator's roster just works without
// a frontend change.
const _SUBSECTION_LABELS = { vre: 'VRE', marc: 'MARC' }
function subsectionLabel(code) {
  return _SUBSECTION_LABELS[code] || String(code || '').toUpperCase()
}

const SORTS = [
  { key: 'num',   label: 'NUM',   value: t => Number(tNum(t)) || 0 },
  { key: 'delay', label: 'DELAY', value: t => -(Number(t.delay_minutes) || 0) },
  { key: 'route', label: 'ROUTE', value: t => (tName(t) || '').toLowerCase() },
]
function loadSort() {
  try { const v = localStorage.getItem('trainmap_list_sort'); if (v) return JSON.parse(v) } catch {}
  return { key: 'num', dir: 1 }
}

/**
 * List panel: Amtrak (core routes / regional corridor, sortable, rows focus the map) plus the
 * operator's regional roster grouped by subsection (regional mode only, no positions).
 */
function TrainList({ trains, coreRoutes, loading, rosterEntries, selectedKey, isTracked, onFocus, onCollapse }) {
  const [sort, setSort] = useState(loadSort)
  const onSort = useCallback((key) => setSort(s => {
    const next = s.key === key ? { key, dir: -s.dir } : { key, dir: 1 }
    try { localStorage.setItem('trainmap_list_sort', JSON.stringify(next)) } catch {}
    return next
  }), [])

  const sorted = useMemo(() => {
    const s = SORTS.find(x => x.key === sort.key) || SORTS[0]
    return trains.slice().sort((a, b) => {
      const va = s.value(a), vb = s.value(b)
      const c = typeof va === 'string' ? va.localeCompare(vb) : va - vb
      return (c || (Number(tNum(a)) || 0) - (Number(tNum(b)) || 0)) * sort.dir
    })
  }, [trains, sort])

  const isCore = t => {
    const name = (tName(t) || '').toLowerCase()
    return coreRoutes.some(n => name.includes(n.toLowerCase()))
  }
  const core   = sorted.filter(isCore)
  const others = sorted.filter(t => !isCore(t))
  const coreLabel = coreRoutes.length <= 2 ? coreRoutes.map(r => r.toUpperCase()).join(' · ') : 'CORE ROUTES'

  const todayAbbr = _DOW_ABBR[new Date().getDay()]
  const groups = useMemo(() => {
    const byKey = new Map()
    rosterEntries.forEach(e => {
      const key = e.subsection || 'regional'
      if (!byKey.has(key)) byKey.set(key, [])
      byKey.get(key).push(e)
    })
    return Array.from(byKey.entries())
  }, [rosterEntries])

  const row = t => <TrainRow key={keyOf(t)} t={t} selected={keyOf(t) === selectedKey} tracked={isTracked(t)} onFocus={onFocus} />

  return (
    <section className="mk-list train-list" id="trainmap-list" aria-label="Train list">
      <div className="mk-list-head">
        <span className="mk-list-title">TRAINS ({trains.length})</span>
        <button className="mk-close" onClick={onCollapse} aria-label="Collapse train list" aria-expanded="true">&times;</button>
      </div>
      <div className="mk-list-cols train-sort" role="row">
        {SORTS.map(s => (
          <button key={s.key} className={`mk-list-col${sort.key === s.key ? ' on' : ''}`} onClick={() => onSort(s.key)}
            aria-sort={sort.key === s.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'} title={`Sort by ${s.label.toLowerCase()}`}>
            {s.label}{sort.key === s.key ? (sort.dir > 0 ? ' ▴' : ' ▾') : ''}
          </button>
        ))}
      </div>
      <div className="mk-list-body">
        <div className="train-panel-section">
          <div className="train-panel-head">{coreLabel}</div>
          {loading ? <div className="train-panel-empty">Loading…</div>
            : core.length ? core.map(row)
            : <div className="train-panel-empty">No scheduled service</div>}
        </div>
        {others.length > 0 && (
          <div className="train-panel-section">
            <div className="train-panel-head">REGIONAL CORRIDOR</div>
            {others.map(row)}
          </div>
        )}
        {!loading && trains.length === 0 && (
          <div className="train-panel-empty" style={{ marginTop: '1rem' }}>No Amtrak service reported</div>
        )}
        {groups.map(([key, group]) => (
          <div className="train-panel-section" key={key}>
            <div className="train-panel-head">{subsectionLabel(key)} <span className="train-panel-head-note">roster, no live position</span></div>
            {group.map(e => <RosterRow key={e.id || e.identifier} e={e} todayAbbr={todayAbbr} />)}
          </div>
        ))}
      </div>
    </section>
  )
}

export default function TrainMapView() {
  const mapRef     = useRef(null)
  const leafletRef = useRef(null)
  const groupsRef  = useRef({})
  const ormRef     = useRef(null)
  const trailsRef  = useRef(new TrailsStore({ keepMs: 3 * 60 * 60_000, expireMs: 20 * 60_000, minMove: 1e-4 }))
  const numSetRef  = useRef(new Set())
  const fittedRef  = useRef(false)

  const [dispatchTrains, setDispatchTrains] = useState([])
  const [panelLoading,   setPanelLoading]   = useState(true)
  const [feedErr,        setFeedErr]        = useState(false)
  const [viewMode,       setViewMode]       = useState('regional')  // 'regional' | 'national'
  const [focusMsg,       setFocusMsg]       = useState(null)
  const [railOverlay,    setRailOverlay]    = useState(() => loadBool('trainmap_rail_overlay', false))
  const [allTrails,      setAllTrails]      = useState(() => loadBool('trainmap_all_trails', false))
  const [listCollapsed,  setListCollapsed]  = useState(() => loadBool('trainmap_list_collapsed', isPhone))

  // Operator config: core_routes for the list split, center/zoom for the map camera (these two
  // fields have a consumer again as of 2026-10-24).
  const [trainConfig, setTrainConfig] = useState({ core_routes: _FB_CORE_ROUTES, center: null, zoom: null })

  const { entries: watchEntries } = useWatchlist()

  // Regional roster entries for the list -- any permanent train whose subsection isn't the
  // national one ("amtrak"), grouped generically rather than hardcoded to specific service names.
  // Only in regional mode -- National mode drops them to keep that view to national-rail only.
  const regionalRosterEntries = useMemo(() => {
    if (viewMode !== 'regional') return []
    return watchEntries.filter(e =>
      e.entry_type === 'train' && e.tier === 'permanent' &&
      e.subsection && e.subsection !== 'amtrak'
    )
  }, [watchEntries, viewMode])
  // Watch chips: train entries that are NOT the regional roster (those have no position to focus).
  const watchTrains = useMemo(() => watchEntries.filter(e =>
    e.entry_type === 'train' && entryTrainNum(e) && !(e.tier === 'permanent' && e.subsection && e.subsection !== 'amtrak')
  ), [watchEntries])
  const numSet = useMemo(() => new Set(watchTrains.map(entryTrainNum)), [watchTrains])
  numSetRef.current = numSet

  const isTracked = useCallback(t => numSetRef.current.has(String(tNum(t) ?? '')), [])
  // 2026-10-24: the ingest now passes amtraker's own fix time (position_updated) and compass heading;
  // prefer them, and fall back to this screen's trail history when an older record lacks them.
  const posAge = useCallback(t => {
    const fix = t.position_updated ? Date.parse(t.position_updated) : NaN
    if (Number.isFinite(fix)) return Math.max(0, (Date.now() - fix) / 1000)
    const ts = trailsRef.current.lastMoved(keyOf(t))
    return ts == null ? null : (Date.now() - ts) / 1000
  }, [])
  const headingOf = useCallback(t => {
    const deg = COMPASS_DEG[String(t.heading || '').toUpperCase()]
    return deg != null ? deg : trailsRef.current.heading(keyOf(t))
  }, [])

  const layer = useEntityLayer({
    getMap: () => leafletRef.current,
    groupsRef,
    keyOf,
    icon: (t, st) => trainIcon(t, st, headingOf(t)),
    tooltip: (t, st) => tooltipHtml(t, st, posAge(t)),
    isTracked,
    trails: trailsRef.current,
    trailValue: t => tBucket(t).hex,
    trailColor: v => v || '#6b7f96',
    allTrails,
    focusZoom: FOCUS_ZOOM,
  })
  const clearRef = useRef(layer.clearSelection)
  clearRef.current = layer.clearSelection

  // ── Map init ───────────────────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (leafletRef.current || !mapRef.current) return
    const map = L.map(mapRef.current, { center: FALLBACK_CENTER, zoom: FALLBACK_ZOOM, zoomControl: true })
    L.tileLayer(OSM_TILE_URL, osmTileOptions({ className: 'map-tiles' })).addTo(map)
    ormRef.current = L.tileLayer(ORM_TILE_URL, { subdomains: 'abc', attribution: ORM_ATTR, maxZoom: 19, opacity: 0.7, ...TILE_PRIVACY })
    const g = groupsRef.current
    g.trails  = L.layerGroup().addTo(map)
    g.main    = L.layerGroup().addTo(map)
    g.tracked = L.layerGroup().addTo(map)
    map.on('click', () => clearRef.current?.())
    leafletRef.current = map
  }, [])

  useEffect(() => {
    const map = leafletRef.current, orm = ormRef.current
    if (!map || !orm) return
    if (railOverlay) { if (!map.hasLayer(orm)) orm.addTo(map) } else if (map.hasLayer(orm)) map.removeLayer(orm)
  }, [railOverlay])

  // ── Load operator config (camera + core routes) ────────────────────────────────────────────
  useEffect(() => {
    fetchTrainConfig().then(cfg => {
      if (!cfg) return
      const c = cfg.center
      const center = (Array.isArray(c) && c.length === 2 && finite(c[0]) && finite(c[1])) ? [Number(c[0]), Number(c[1])] : null
      const zoom = finite(cfg.zoom) ? Number(cfg.zoom) : null
      setTrainConfig({ core_routes: cfg.core_routes || _FB_CORE_ROUTES, center, zoom })
      // Only move the camera if the first data load has not already framed the trains.
      if (center && !fittedRef.current) leafletRef.current?.setView(center, zoom ?? FALLBACK_ZOOM)
    })
  }, [])

  // ── Poll (dispatch API): list + map from the same payload ─────────────────────────────────
  const pollTrains = useCallback(async () => {
    const trains = await fetchDispatchTrains()
    if (trains === null) { setFeedErr(true); setPanelLoading(false); return }
    setFeedErr(false)
    setDispatchTrains(trains)
    setPanelLoading(false)
    const positioned = trains.filter(hasPos)
    layer.sync(positioned)
    const map = leafletRef.current
    if (map && !fittedRef.current && positioned.length) {
      fittedRef.current = true
      map.fitBounds(L.latLngBounds(positioned.map(t => [Number(t.lat), Number(t.lon)])).pad(0.1), { maxZoom: 9 })
    }
  }, [layer.sync])

  useVisibilityAwareInterval(pollTrains, PANEL_POLL)

  useEffect(() => { layer.restyleAll() }, [numSet, allTrails, layer.restyleAll])
  useEffect(() => { const id = setTimeout(() => leafletRef.current?.invalidateSize(), 60); return () => clearTimeout(id) }, [listCollapsed])

  const toggleList   = useCallback(() => setListCollapsed(c => { saveBool('trainmap_list_collapsed', !c); return !c }), [])
  const toggleTrails = useCallback(() => setAllTrails(c => { saveBool('trainmap_all_trails', !c); return !c }), [])
  const toggleRail   = useCallback(() => setRailOverlay(c => { saveBool('trainmap_rail_overlay', !c); return !c }), [])

  const focusEntry = useCallback((e) => {
    const num = entryTrainNum(e)
    setFocusMsg(null)
    if (!layer.focusWhere(t => String(tNum(t) ?? '') === num && hasPos(t))) setFocusMsg(`${e.identifier}: no live position in the dispatch feed`)
  }, [layer.focusWhere])

  // ── Derived / a11y ─────────────────────────────────────────────────────────────────────────
  const positioned = useMemo(() => dispatchTrains.filter(hasPos), [dispatchTrains])
  const receiverLoc = useReceiverLocation()
  const compassItems = useMemo(() => positioned.map(t => ({ lat: t.lat, lon: t.lon, label: `${tNum(t) ?? '?'} ${tName(t) || ''}`.trim(), tracked: isTracked(t) })), [positioned, isTracked, numSet])
  const compassSummary = useCompassSummary(compassItems, [], trainConfig.center || receiverLoc)
  const trackedCount = compassItems.filter(t => t.tracked).length
  const delayedCount = dispatchTrains.filter(t => (Number(t.delay_minutes) || 0) >= 15).length
  const tableRows = useMemo(() => dispatchTrains.map(t => ({
    num: tNum(t) ?? '', name: tName(t) || '', status: delayLabel(t.delay_minutes || 0, tState(t)),
    station: tCurrentStationLine(t) || '', lat: hasPos(t) ? Number(t.lat).toFixed(4) : '', lon: hasPos(t) ? Number(t.lon).toFixed(4) : '',
    tracked: isTracked(t) ? 'Yes' : '',
  })), [dispatchTrains, isTracked, numSet])

  const sel = layer.selected
  const legendSections = [
    { title: 'Fill = delay state', rows: DELAY_BUCKETS.map(b => ({ swatch: <Swatch color={b.hex} shape="pill" text={b.label === 'ARR' || b.label === 'SCH' || b.label === 'OT' ? b.label : '+'} />, text: <><b>{b.label}</b> {b.text}</> })) },
    { title: 'Marks', rows: [
      { swatch: <Swatch color="#9aa5b1" shape="arrow" />, text: <><b>Arrow</b> direction of travel, from the feed's heading (or successive positions when a record lacks it)</> },
      { swatch: <Swatch color="#00ff88" shape="line" />, text: <><b>Trail</b> coloured by delay state at the time; selected + watchlist, TRAILS draws all (3 h, this screen's own history; dispatch refreshes positions about every 5 min)</> },
      { swatch: <Swatch color="#00d4ff" shape="dot" glow />, text: <><b>Watchlist</b> cyan glow, larger</> },
      { swatch: <Swatch color="#6b7f96" dash />, text: <><b>Roster</b> VRE / MARC entries are list-only (no live feed)</> },
    ] },
  ]

  return (
    <div className="mk-view train-view">
      <div className="mk-subnav">
        <span className="mk-title">EOTD</span>

        {/* ── REGIONAL / NATIONAL toggle ─────────────────────── */}
        <div className="train-view-toggle">
          <button className={`train-mode-btn${viewMode === 'regional' ? ' active' : ''}`} onClick={() => setViewMode('regional')}>REGIONAL</button>
          <button className={`train-mode-btn${viewMode === 'national' ? ' active' : ''}`} onClick={() => setViewMode('national')}>NATIONAL</button>
        </div>
        {sel && <span className="mk-sel-name">{tNum(sel)} {(tName(sel) || '').replace(/\s+\d+$/, '')}</span>}

        <span className="mk-subnav-right">
          <span className="stat source-badge" style={{ color: 'var(--cyan)' }}>amtraker.com · dispatch feed</span>
          {sel && <button className="mk-tool-btn" onClick={layer.clearSelection} aria-label="Clear selection">&times;</button>}
          <button className="intel-refresh-btn" onClick={pollTrains} title="Refresh train status">&#8635;</button>
        </span>
      </div>

      <div className={`mk-stage${listCollapsed ? ' mk-list-collapsed' : ''}`}>
        <div className="mk-map-wrap">
          <AriaCompassRegion summary={compassSummary} entityType="trains" count={positioned.length}
            extra={`${dispatchTrains.length} trains reported, ${positioned.length} with a position.${delayedCount ? ` ${delayedCount} delayed 15 min or more.` : ''}`} />
          <AccessibleTable
            id="train-map-table"
            caption={`Trains -- ${dispatchTrains.length} reported`}
            columns={[
              { key: 'num', label: 'Train' }, { key: 'name', label: 'Route' }, { key: 'status', label: 'Status' },
              { key: 'station', label: 'Where' }, { key: 'lat', label: 'Latitude' }, { key: 'lon', label: 'Longitude' },
              { key: 'tracked', label: 'Tracked' },
            ]}
            rows={tableRows}
            emptyMsg="No Amtrak service reported."
          />

          <div ref={mapRef} className="mk-map" />

          <div className="mk-tools">
            <button className={`mk-tool-btn${railOverlay ? ' on' : ''}`} onClick={toggleRail} aria-pressed={railOverlay} title="OpenRailwayMap track overlay">RAIL</button>
            <button className={`mk-tool-btn${allTrails ? ' on' : ''}`} onClick={toggleTrails} aria-pressed={allTrails} title="Draw every train's trail">TRAILS</button>
          </div>
          {focusMsg && <div className="mk-focus-msg" role="status">{focusMsg}</div>}

          <WatchChips entries={watchTrains} onFocus={focusEntry} storageKey="trainmap_wl" unit="trains" />

          <div className="mk-corner-br">
            {listCollapsed && (
              <button className="mk-pill" onClick={toggleList} aria-expanded="false" aria-controls="trainmap-list" title="Show train list">
                LIST ({dispatchTrains.length})
              </button>
            )}
            <MapLegend id="trainmap-legend" storageKey="trainmap_legend_collapsed" sections={legendSections} credit="Positions: Amtrak via amtraker.com (dispatch feed)" />
          </div>

          <div className="map-overlay-stats mk-stats">
            <span className="stat source-badge" style={{ color: 'var(--cyan)' }}>{positioned.length} trains on map</span>
            {delayedCount > 0 && <span className="stat" style={{ color: 'var(--orange)' }}>{delayedCount} delayed 15m+</span>}
            {trackedCount > 0 && <span className="stat tracked-stat">&#9733; {trackedCount} TRACKED</span>}
            {feedErr && <span className="stat error">Train feed error</span>}
          </div>
        </div>

        {!listCollapsed && (
          <TrainList
            trains={dispatchTrains}
            coreRoutes={trainConfig.core_routes}
            loading={panelLoading}
            rosterEntries={regionalRosterEntries}
            selectedKey={layer.selectedKey}
            isTracked={isTracked}
            onFocus={layer.focus}
            onCollapse={toggleList}
          />
        )}
      </div>
    </div>
  )
}
