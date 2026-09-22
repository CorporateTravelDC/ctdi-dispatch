import { useEffect, useState, useCallback, useMemo } from 'react'
import { useWatchlist } from '../hooks/useWatchlist.js'
import { useVisibilityAwareInterval } from '../hooks/useVisibilityAwareInterval.js'

const PANEL_POLL = 60_000

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

// ── Fetch dispatch train list (panel data) ────────────────────────
async function fetchDispatchTrains() {
  try {
    const r = await fetch('/api/dispatch/api/v1/amtrak')
    if (!r.ok) return []
    const d = await r.json()
    return Array.isArray(d.trains) ? d.trains : []
  } catch { return [] }
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

function delayColor(delay, state) {
  const s = (state || '').toLowerCase()
  if (s === 'completed' || s === 'arrived') return 'var(--muted)'
  if (s === 'predeparture' || s === 'scheduled') return 'var(--cyan)'
  if (delay > 30)  return 'var(--nogo)'
  if (delay > 10)  return 'var(--orange)'
  if (delay > 0)   return 'var(--marginal)'
  return 'var(--go)'
}

function delayLabel(delay, state) {
  const s = (state || '').toLowerCase()
  if (s === 'completed' || s === 'arrived')         return 'ARR'
  if (s === 'predeparture' || s === 'scheduled')    return 'SCH'
  if (delay > 0) return `+${delay}m`
  return 'OT'
}

function TrainRow({ t }) {
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

  return (
    <div className="train-row">
      <span className="train-row-num" style={{ color }}>{num}</span>
      <div className="train-row-info">
        <span className="train-row-name">{name}</span>
        {route && <span className="train-row-route">{route}</span>}
        {station && <span className="train-row-station">{station}</span>}
        {fallbackEvent && <span className="train-row-event">{fallbackEvent}</span>}
        {destEta && <span className="train-row-dest-eta">{destEta}</span>}
      </div>
      <span className="train-row-badge" style={{ color, borderColor: color }}>{label}</span>
    </div>
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
    <div className="train-row">
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

/**
 * Left column: Amtrak, split into core (Acela/Regional) vs regional-corridor
 * by live position (amtraker.com feed via the dispatch backend).
 */
function AmtrakColumn({ trains, coreRoutes, loading }) {
  const isCore = t => {
    const name = (tName(t) || '').toLowerCase()
    return coreRoutes.some(n => name.includes(n.toLowerCase()))
  }
  const core   = trains.filter(isCore)
  const others = trains.filter(t => !isCore(t))

  const coreLabel = coreRoutes.length <= 2
    ? coreRoutes.map(r => r.toUpperCase()).join(' · ')
    : 'CORE ROUTES'

  return (
    <div className="train-split-col">
      <div className="train-panel-section">
        <div className="train-panel-head">{coreLabel}</div>
        {loading ? (
          <div className="train-panel-empty">Loading…</div>
        ) : core.length ? (
          core.map(t => <TrainRow key={t.train_number} t={t} />)
        ) : (
          <div className="train-panel-empty">No scheduled service</div>
        )}
      </div>

      {others.length > 0 && (
        <div className="train-panel-section">
          <div className="train-panel-head">REGIONAL CORRIDOR</div>
          {others.map(t => <TrainRow key={t.train_number} t={t} />)}
        </div>
      )}

      {!loading && trains.length === 0 && (
        <div className="train-panel-empty" style={{ marginTop: '1rem' }}>
          No Amtrak service reported
        </div>
      )}
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

/**
 * Right column: operator-defined regional transit, grouped by whatever
 * `subsection` values are actually present in the permanent roster --
 * NOT hardcoded to VRE/MARC. Generalized 2026-07-21 so a non-DC deployment
 * (a different country's national-rail equivalent + its own regional rail
 * or local transit buses) just works by populating permanent_trains.json
 * with its own subsection tags; no code change needed per operator.
 * Plain text roster (no live position/delay feed for these yet), day-of-week
 * No-Service badges only.
 */
function RegionalRailColumn({ entries }) {
  const todayAbbr = _DOW_ABBR[new Date().getDay()]

  const groups = useMemo(() => {
    const byKey = new Map()
    entries.forEach(e => {
      const key = e.subsection || 'regional'
      if (!byKey.has(key)) byKey.set(key, [])
      byKey.get(key).push(e)
    })
    return Array.from(byKey.entries())
  }, [entries])

  return (
    <div className="train-split-col train-split-col-last">
      {groups.map(([key, group]) => (
        <div className="train-panel-section" key={key}>
          <div className="train-panel-head">{subsectionLabel(key)}</div>
          {group.map(e => <RosterRow key={e.id || e.identifier} e={e} todayAbbr={todayAbbr} />)}
        </div>
      ))}

      {groups.length === 0 && (
        <div className="train-panel-empty" style={{ marginTop: '1rem' }}>
          No regional transit roster in this view.
        </div>
      )}
    </div>
  )
}

export default function TrainMapView() {
  const [dispatchTrains, setDispatchTrains] = useState([])
  const [panelLoading,   setPanelLoading]   = useState(true)
  const [viewMode,       setViewMode]       = useState('regional')  // 'regional' | 'national'

  // Operator config (loaded once, then cached) -- only core_routes is used
  // now that the map/camera-position fields (center/zoom) have no consumer.
  const [trainConfig, setTrainConfig] = useState({ core_routes: _FB_CORE_ROUTES })

  const { entries: watchEntries } = useWatchlist()

  // Regional roster entries for the right column -- any permanent train
  // whose subsection isn't the national one ("amtrak"), grouped generically
  // in RegionalRailColumn rather than hardcoded to specific service names.
  // This is what makes the split-screen layout operator-portable: a non-DC
  // deployment populates permanent_trains.json with its own national-rail
  // equivalent (subsection="amtrak" by convention, or override if desired)
  // plus whatever regional rail/transit subsections make sense locally, and
  // this page renders them without a code change. Only populated in
  // regional mode -- these are local/regional-only, so National mode drops
  // them to keep that view to national-rail only.
  const regionalRosterEntries = useMemo(() => {
    if (viewMode !== 'regional') return []
    return watchEntries.filter(e =>
      e.entry_type === 'train' && e.tier === 'permanent' &&
      e.subsection && e.subsection !== 'amtrak'
    )
  }, [watchEntries, viewMode])

  // ── Load operator config ─────────────────────────────────────────
  useEffect(() => {
    fetchTrainConfig().then(cfg => {
      if (!cfg) return
      setTrainConfig({ core_routes: cfg.core_routes || _FB_CORE_ROUTES })
    })
  }, [])

  // ── Panel polling (dispatch API) ─────────────────────────────────
  const pollTrains = useCallback(async () => {
    const trains = await fetchDispatchTrains()
    setDispatchTrains(trains)
    setPanelLoading(false)
  }, [])

  useVisibilityAwareInterval(pollTrains, PANEL_POLL)

  return (
    <div className="train-map-view">
      <div className="train-map-subnav">
        <span className="train-map-title">EOTD</span>

        {/* ── REGIONAL / NATIONAL toggle ─────────────────────── */}
        <div className="train-view-toggle">
          <button
            className={`train-mode-btn${viewMode === 'regional' ? ' active' : ''}`}
            onClick={() => setViewMode('regional')}
          >REGIONAL</button>
          <button
            className={`train-mode-btn${viewMode === 'national' ? ' active' : ''}`}
            onClick={() => setViewMode('national')}
          >NATIONAL</button>
        </div>

        <span className="stat source-badge" style={{ color: 'var(--cyan)', marginLeft: 'auto' }}>
          amtraker.com · dispatch feed
        </span>
        <button
          className="intel-refresh-btn"
          onClick={pollTrains}
          title="Refresh train status"
        >↻</button>
      </div>

      <div className="train-split-body">
        <AmtrakColumn
          trains={dispatchTrains}
          coreRoutes={trainConfig.core_routes}
          loading={panelLoading}
        />
        <RegionalRailColumn
          entries={regionalRosterEntries}
        />
      </div>
    </div>
  )
}
