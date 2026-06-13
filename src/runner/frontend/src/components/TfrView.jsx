import { useState, useEffect, useMemo, useCallback } from 'react'

// ── TFR type inference from NOTAM ID prefix ────────────────────────────────
// Basic /api/v1/tfr has no classification field; derive from NOTAM series prefix.
// NOTAM series: 0=misc, 1=airport/FDC, 2=enroute, 4=intl, 5=military,
//               6=events/security, 9=fire/disaster
const TFR_TYPES = {
  '6': { label: 'Events / Security', short: 'EVT',  color: 'orange'   },
  '5': { label: 'Military',          short: 'MIL',  color: 'airspace' },
  '4': { label: 'International',     short: 'INTL', color: 'cyan'     },
  '9': { label: 'Fire / Disaster',   short: 'FIRE', color: 'nogo'     },
  '1': { label: 'Airport / FDC',     short: 'FDC',  color: 'muted'    },
  '2': { label: 'Enroute',           short: 'ENR',  color: 'muted'    },
}
const DEFAULT_TYPE = { label: 'Other', short: 'OTH', color: 'muted' }

function tfrType(tfr_id) {
  const prefix = tfr_id ? String(tfr_id).split('/')[0] : null
  return TFR_TYPES[prefix] ?? DEFAULT_TYPE
}

function fmtUtc(s) {
  if (!s) return null
  try {
    const d = new Date(s)
    const mo = d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
    const dy = String(d.getUTCDate()).padStart(2, '0')
    const hh = String(d.getUTCHours()).padStart(2, '0')
    const mm = String(d.getUTCMinutes()).padStart(2, '0')
    return `${mo} ${dy} ${hh}${mm}Z`
  } catch (_) { return s }
}

// Search: TFR ID, type label, type short, date string
function tfrMatches(tfr, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  const t  = tfrType(tfr.tfr_id)
  return [
    tfr.tfr_id,
    t.label,
    t.short,
    fmtUtc(tfr.effective_start),
    fmtUtc(tfr.effective_end),
  ].some(v => v && String(v).toLowerCase().includes(lq))
}

// ── Type filter keys for General panel ────────────────────────────────────
const FILTER_KEYS = ['events', 'military', 'intl', 'disaster', 'fdc', 'enroute', 'other']
const TYPE_BY_KEY = Object.fromEntries(
  [...Object.entries(TFR_TYPES), ['0', DEFAULT_TYPE]].map(([, t]) => [t.short.toLowerCase(), t])
)
// derive key from type object
function typeKey(t) {
  return t.short.toLowerCase()
}

// ── TFR row (compact, sig-msg style) ─────────────────────────────────────
function TfrRow({ tfr, isVip }) {
  const t     = tfrType(tfr.tfr_id)
  const start = fmtUtc(tfr.effective_start)
  const end   = fmtUtc(tfr.effective_end)

  return (
    <div className={`sig-msg tfr-row${isVip ? ' tfr-row-vip' : ''}`}>
      <span className={`tfr-short-chip tfr-chip-${t.color}`}>{t.short}</span>
      <span className={`sig-msg-call${isVip ? ' tfr-id-vip' : ''}`}>{tfr.tfr_id}</span>
      {start
        ? <span className="sig-msg-text">{start}{end ? ` → ${end}` : ' → indef.'}</span>
        : <span className="sig-msg-text tfr-dates-degraded">dates unavailable (FAA feed)</span>
      }
      {isVip && <span className="tfr-vip-mini">VIP</span>}
    </div>
  )
}

// ── VIP panel ─────────────────────────────────────────────────────────────
function VipPanel({ tfrs, loading, updatedAt }) {
  const [search, setSearch] = useState('')
  const vip      = useMemo(() => tfrs?.filter(t => t.is_vip) ?? [], [tfrs])
  const filtered = useMemo(() => vip.filter(t => tfrMatches(t, search)), [vip, search])

  return (
    <div className={`sig-panel tfr-vip-panel${vip.length > 0 ? ' tfr-vip-active' : ''}`}>
      <div className="sig-panel-header">
        <span className="sig-label tfr-vip-label">VIP / POTUS</span>
        {vip.length > 0
          ? <span className="sig-count tfr-count-hot">{vip.length} active</span>
          : <span className="sig-count">clear</span>
        }
        <input
          className="sig-search"
          type="search"
          placeholder="search TFR ID…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search VIP TFRs"
        />
        {updatedAt && <span className="tfr-updated">↻ {updatedAt}</span>}
      </div>

      <div className="sig-feed">
        {loading ? (
          <div className="sig-empty">Loading…</div>
        ) : vip.length === 0 ? (
          <div className="sig-empty tfr-clear-state">
            <span className="tfr-clear-check">✓</span> Airspace clear — no VIP restrictions
          </div>
        ) : filtered.length === 0 ? (
          <div className="sig-empty">No VIP TFRs matching &ldquo;{search}&rdquo;</div>
        ) : (
          filtered.map(tfr => <TfrRow key={tfr.tfr_id} tfr={tfr} isVip />)
        )}
      </div>
    </div>
  )
}

// ── General TFRs panel ────────────────────────────────────────────────────
const ALL_FILTER_LABELS = {
  evt:  'Events / Security',
  mil:  'Military',
  intl: 'International',
  fire: 'Fire / Disaster',
  fdc:  'Airport / FDC',
  enr:  'Enroute',
  oth:  'Other',
}

function GeneralPanel({ tfrs, loading, feedDegraded }) {
  const [search,  setSearch]  = useState('')
  const [typeOn,  setTypeOn]  = useState(new Set(Object.keys(ALL_FILTER_LABELS)))

  const nonVip = useMemo(() => tfrs?.filter(t => !t.is_vip) ?? [], [tfrs])

  // counts per type short-key
  const counts = useMemo(() => {
    const c = {}
    nonVip.forEach(t => {
      const key = tfrType(t.tfr_id).short.toLowerCase()
      c[key] = (c[key] || 0) + 1
    })
    return c
  }, [nonVip])

  const presentKeys = useMemo(() => new Set(Object.keys(counts)), [counts])

  const filtered = useMemo(() => {
    return nonVip.filter(t => {
      const key = tfrType(t.tfr_id).short.toLowerCase()
      return typeOn.has(key) && tfrMatches(t, search)
    })
  }, [nonVip, typeOn, search])

  const toggleType = key => setTypeOn(prev => {
    const next = new Set(prev)
    next.has(key) ? next.delete(key) : next.add(key)
    return next
  })

  const allOn = typeOn.size === Object.keys(ALL_FILTER_LABELS).length
  const toggleAll = () => setTypeOn(allOn ? new Set() : new Set(Object.keys(ALL_FILTER_LABELS)))

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: 'var(--text-2)' }}>GENERAL</span>
        <span className="sig-count">{nonVip.length} active</span>
        <input
          className="sig-search"
          type="search"
          placeholder="TFR ID, type…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search general TFRs"
        />
      </div>

      {/* Type filter chips — only present types shown */}
      {!loading && nonVip.length > 0 && (
        <div className="tfr-filter-chips">
          {Object.entries(ALL_FILTER_LABELS)
            .filter(([k]) => presentKeys.has(k))
            .map(([k, label]) => (
              <button
                key={k}
                className={`tfr-filter-chip${typeOn.has(k) ? ' on' : ' off'}`}
                onClick={() => toggleType(k)}
              >
                {label}
                <span className="tfr-filter-count">{counts[k] || 0}</span>
              </button>
            ))}
          <button className="tfr-filter-chip tfr-filter-all" onClick={toggleAll}>
            {allOn ? 'NONE' : 'ALL'}
          </button>
        </div>
      )}

      {feedDegraded && (
        <div className="sig-hw-notice">
          ⚠ FAA TFR XML feed degraded — effective dates unavailable (upstream issue)
        </div>
      )}

      <div className="sig-feed">
        {loading ? (
          <div className="sig-empty">Loading…</div>
        ) : nonVip.length === 0 ? (
          <div className="sig-empty">No general TFRs active</div>
        ) : filtered.length === 0 ? (
          <div className="sig-empty">No TFRs match current filters</div>
        ) : (
          filtered.map(tfr => <TfrRow key={tfr.tfr_id} tfr={tfr} isVip={false} />)
        )}
      </div>

      {!loading && filtered.length > 0 && filtered.length !== nonVip.length && (
        <div className="sig-hw-notice" style={{ marginTop: '0.25rem', borderTopColor: 'transparent' }}>
          Showing {filtered.length} of {nonVip.length} — adjust filters or search to see more
        </div>
      )}
    </div>
  )
}

// ── NOTAMs panel ──────────────────────────────────────────────────────────
function notamMatches(n, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [n.notam_id, n.icao, n.location, n.text, n.classification, n.type]
    .some(v => v && String(v).toLowerCase().includes(lq))
}

function NotamPanel({ notams, loading }) {
  const [search, setSearch] = useState('')
  const filtered = useMemo(() => {
    const all = notams || []
    return search ? all.filter(n => notamMatches(n, search)) : all
  }, [notams, search])

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: 'var(--muted)' }}>NOTAM</span>
        <span className="sig-count">{notams?.length ?? 0} active</span>
        <input
          className="sig-search"
          type="search"
          placeholder="NOTAM ID, ICAO, location…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search NOTAMs"
        />
      </div>
      <div className="sig-feed">
        {loading ? (
          <div className="sig-empty">Loading…</div>
        ) : !notams?.length ? (
          <div className="sig-pending">
            FAA NOTAM API key required — set <code>FAA_NOTAM_API_KEY</code> in dispatch-secrets.env
          </div>
        ) : !filtered.length ? (
          <div className="sig-empty">No NOTAMs matching &ldquo;{search}&rdquo;</div>
        ) : (
          filtered.map((n, i) => (
            <div key={n.notam_id || i} className="sig-msg">
              <span className="sig-msg-call" style={{ color: 'var(--text-2)' }}>
                {n.notam_id || '—'}
              </span>
              {n.icao && <span className="sig-msg-flight">{n.icao}</span>}
              <span className="sig-msg-text">
                {n.text || n.raw_text || (n.classification && `[${n.classification}]`) || '—'}
              </span>
              {n.location && <span className="sig-msg-loc">{n.location}</span>}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ── Root view ─────────────────────────────────────────────────────────────
const POLL_MS = 30_000

export default function TfrView() {
  const [tfrs,    setTfrs]    = useState(null)
  const [notams,  setNotams]  = useState(null)
  const [feedErr, setFeedErr] = useState(false)
  const [updatedAt, setUpdatedAt] = useState(null)

  const fmtNow = () => {
    const d = new Date()
    return `${String(d.getUTCHours()).padStart(2,'0')}${String(d.getUTCMinutes()).padStart(2,'0')}Z`
  }

  const loadTfrs = useCallback(async () => {
    try {
      const r = await fetch('/api/dispatch/api/v1/tfr')
      if (!r.ok) throw new Error(r.status)
      const d = await r.json()
      const list = Array.isArray(d) ? d : (d.tfrs || [])
      setTfrs(list)
      const nullDates = list.filter(t => !t.effective_start && !t.effective_end).length
      setFeedErr(list.length > 0 && nullDates / list.length > 0.1)
      setUpdatedAt(fmtNow())
    } catch { setTfrs([]) }
  }, [])

  const loadNotams = useCallback(async () => {
    try {
      const r = await fetch('/api/dispatch/api/v1/notams')
      const d = await r.json()
      setNotams(Array.isArray(d) ? d : (d.notams || []))
    } catch { setNotams([]) }
  }, [])

  useEffect(() => {
    loadTfrs()
    loadNotams()
    const id = setInterval(loadTfrs, POLL_MS)
    return () => clearInterval(id)
  }, [loadTfrs, loadNotams])

  return (
    <div className="panel-view signals-view">
      <div className="signals-header-row">
        <h2>Airspace Restrictions — TFRs &amp; NOTAMs</h2>
        <button
          className="intel-refresh-btn"
          onClick={() => { loadTfrs(); loadNotams() }}
          disabled={tfrs === null}
          title="Refresh now"
        >
          {tfrs === null ? '⟳' : '↻'}
        </button>
        <span className="sig-panel-count" style={{ marginLeft: 'auto' }}>
          Polls every {POLL_MS / 1000}s
        </span>
      </div>
      <p className="sig-subtitle">
        FAA TFR feed · Search by TFR ID or type · Airport / ARTCC data requires enriched endpoint
      </p>

      <div className="sig-grid tfr-grid">
        <VipPanel     tfrs={tfrs}   loading={tfrs === null}   updatedAt={updatedAt} />
        <GeneralPanel tfrs={tfrs}   loading={tfrs === null}   feedDegraded={feedErr} />
        <NotamPanel   notams={notams} loading={notams === null} />
      </div>
    </div>
  )
}
