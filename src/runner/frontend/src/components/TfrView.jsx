import { useState, useEffect, useMemo } from 'react'

// ── TFR type inference from NOTAM ID prefix ───────────────────────────────
// /api/v1/tfr returns {tfr_id, is_vip, effective_start, effective_end} only.
// No classification field available without tier1 auth, so we derive from prefix.
const TFR_TYPE_MAP = {
  '6': { label: 'Events / Security', key: 'events',   color: 'orange' },
  '5': { label: 'Military',          key: 'military',  color: 'airspace' },
  '4': { label: 'International',     key: 'intl',      color: 'cyan' },
  '9': { label: 'Fire / Disaster',   key: 'disaster',  color: 'nogo' },
  '1': { label: 'Airport / FDC',     key: 'fdc',       color: 'muted' },
  '2': { label: 'Enroute',           key: 'enroute',   color: 'muted' },
  '0': { label: 'Other',             key: 'other',     color: 'muted' },
}

function tfrType(tfr_id) {
  if (!tfr_id) return TFR_TYPE_MAP['0']
  const prefix = String(tfr_id).split('/')[0]
  return TFR_TYPE_MAP[prefix] ?? TFR_TYPE_MAP['0']
}

function fmtTime(s) {
  if (!s) return null
  try {
    return new Date(s).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
      timeZone: 'UTC', hour12: false,
    }) + 'Z'
  } catch (_) { return s }
}

function tfrMatchesSearch(tfr, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [tfr.tfr_id, tfrType(tfr.tfr_id).label].some(
    v => v && String(v).toLowerCase().includes(lq)
  )
}

function notamMatchesSearch(n, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [n.notam_id, n.icao, n.location, n.text, n.classification, n.type].some(
    v => v && String(v).toLowerCase().includes(lq)
  )
}

// ── TFR card ─────────────────────────────────────────────────────────────
function TfrCard({ tfr, showVipBadge }) {
  const t      = tfrType(tfr.tfr_id)
  const start  = fmtTime(tfr.effective_start)
  const end    = fmtTime(tfr.effective_end)
  const noDate = !start && !end

  return (
    <div className={`tfr-card${tfr.is_vip ? ' vip' : ''}`}>
      <div className="tfr-card-header">
        <span className="tfr-id">{tfr.tfr_id}</span>
        {showVipBadge && tfr.is_vip && <span className="vip-badge">VIP / POTUS</span>}
        <span className={`tfr-type-chip tfr-type-${t.color}`}>{t.label}</span>
      </div>
      <div className="tfr-meta">
        {start && <span>Start: {start}</span>}
        {end   && <span>End: {end}</span>}
        {noDate && <span className="tfr-meta-degraded">Dates unavailable — FAA feed degraded</span>}
      </div>
    </div>
  )
}

// ── VIP / POTUS section ──────────────────────────────────────────────────
function VipSection({ tfrs, loading }) {
  const vip = useMemo(() => tfrs?.filter(t => t.is_vip) ?? [], [tfrs])

  return (
    <section className="airspace-section tfr-vip-section" aria-label="VIP and POTUS TFRs">
      <div className="airspace-section-header">
        <h3>
          VIP / POTUS Restrictions
          {!loading && <span className={`airspace-count${vip.length > 0 ? ' tfr-count-alert' : ''}`}>
            {' '}{vip.length} active
          </span>}
        </h3>
      </div>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : vip.length === 0 ? (
        <div className="tfr-vip-clear">
          <span className="tfr-vip-clear-icon">✓</span>
          <div>
            <div className="tfr-vip-clear-label">Airspace clear</div>
            <div className="tfr-vip-clear-sub">No VIP / POTUS restrictions active</div>
          </div>
        </div>
      ) : (
        <>
          <div className="tfr-vip-alert-banner">
            ⚠ {vip.length} VIP / POTUS restriction{vip.length !== 1 ? 's' : ''} active
          </div>
          {vip.map(tfr => <TfrCard key={tfr.tfr_id} tfr={tfr} showVipBadge={false} />)}
        </>
      )}
    </section>
  )
}

// ── General TFRs section ─────────────────────────────────────────────────
const TYPE_FILTERS = [
  { key: 'events',   label: 'Events / Security' },
  { key: 'military', label: 'Military' },
  { key: 'intl',     label: 'International' },
  { key: 'disaster', label: 'Fire / Disaster' },
  { key: 'fdc',      label: 'Airport / FDC' },
  { key: 'enroute',  label: 'Enroute' },
  { key: 'other',    label: 'Other' },
]

function GeneralSection({ tfrs, loading, feedDegraded }) {
  const [search,      setSearch]      = useState('')
  const [activeTypes, setActiveTypes] = useState(new Set(TYPE_FILTERS.map(f => f.key)))

  const nonVip = useMemo(() => tfrs?.filter(t => !t.is_vip) ?? [], [tfrs])

  // Count per type for filter chip badges
  const typeCounts = useMemo(() => {
    const counts = {}
    nonVip.forEach(t => {
      const key = tfrType(t.tfr_id).key
      counts[key] = (counts[key] || 0) + 1
    })
    return counts
  }, [nonVip])

  // Which type keys are actually present in data?
  const presentKeys = useMemo(() => new Set(Object.keys(typeCounts)), [typeCounts])

  const filtered = useMemo(() => {
    return nonVip.filter(t => {
      const key = tfrType(t.tfr_id).key
      return activeTypes.has(key) && tfrMatchesSearch(t, search)
    })
  }, [nonVip, activeTypes, search])

  const toggleType = (key) => {
    setActiveTypes(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else               next.add(key)
      return next
    })
  }

  const allOn  = activeTypes.size === TYPE_FILTERS.length
  const toggleAll = () => {
    setActiveTypes(allOn
      ? new Set()
      : new Set(TYPE_FILTERS.map(f => f.key))
    )
  }

  return (
    <section className="airspace-section" aria-label="General TFRs">
      <div className="airspace-section-header">
        <h3>
          General Restrictions
          {!loading && <span className="airspace-count">{nonVip.length} active</span>}
        </h3>
        <input
          className="sig-search"
          type="search"
          placeholder="search TFRs…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search general TFRs"
        />
      </div>

      {/* Type filter chips — only show types that have data */}
      {!loading && nonVip.length > 0 && (
        <div className="tfr-filter-chips">
          {TYPE_FILTERS.filter(f => presentKeys.has(f.key)).map(f => (
            <button
              key={f.key}
              className={`tfr-filter-chip${activeTypes.has(f.key) ? ' on' : ' off'}`}
              onClick={() => toggleType(f.key)}
              title={`${typeCounts[f.key] ?? 0} TFRs`}
            >
              {f.label}
              <span className="tfr-filter-count">{typeCounts[f.key] ?? 0}</span>
            </button>
          ))}
          <button className="tfr-filter-chip tfr-filter-all" onClick={toggleAll}>
            {allOn ? 'NONE' : 'ALL'}
          </button>
        </div>
      )}

      {feedDegraded && nonVip.length > 0 && (
        <div className="tfr-feed-warn">
          ⚠ FAA TFR XML feed degraded — effective dates unavailable for most TFRs (upstream issue)
        </div>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : nonVip.length === 0 ? (
        <p className="muted">No general TFRs active</p>
      ) : filtered.length === 0 ? (
        <p className="muted">No TFRs match current filters</p>
      ) : (
        <>
          <p className="tfr-filter-summary muted">{filtered.length} of {nonVip.length} shown</p>
          {filtered.map(tfr => <TfrCard key={tfr.tfr_id} tfr={tfr} showVipBadge={false} />)}
        </>
      )}
    </section>
  )
}

// ── NOTAMs section ────────────────────────────────────────────────────────
function NotamSection({ notams, loading }) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!notams) return []
    return search ? notams.filter(n => notamMatchesSearch(n, search)) : notams
  }, [notams, search])

  return (
    <section className="airspace-section" aria-label="Active NOTAMs">
      <div className="airspace-section-header">
        <h3>
          NOTAMs
          {!loading && <span className="airspace-count">{notams?.length ?? 0} active</span>}
        </h3>
        <input
          className="sig-search"
          type="search"
          placeholder="search NOTAMs…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search NOTAMs"
        />
      </div>

      {loading ? (
        <p className="muted">Loading NOTAMs…</p>
      ) : !notams?.length ? (
        <p className="muted">
          No active NOTAMs — FAA NOTAM API key required (<code>~/.secrets/faa-notam.key</code>)
        </p>
      ) : !filtered.length ? (
        <p className="muted">No NOTAMs matching &ldquo;{search}&rdquo;</p>
      ) : (
        filtered.map((n, i) => (
          <div key={n.notam_id || i} className="tfr-card">
            <div className="tfr-card-header">
              <span className="tfr-id">{n.notam_id || '—'}</span>
              {n.icao           && <span className="notam-icao">{n.icao}</span>}
              {n.classification && <span className="notam-class">{n.classification}</span>}
            </div>
            <p className="tfr-narrative">{n.text || n.raw_text || JSON.stringify(n)}</p>
            <div className="tfr-meta">
              {n.effective_start && <span>From: {fmtTime(n.effective_start)}</span>}
              {n.effective_end   && <span>To: {fmtTime(n.effective_end)}</span>}
              {n.location        && <span>Loc: {n.location}</span>}
            </div>
          </div>
        ))
      )}
    </section>
  )
}

// ── Root view ─────────────────────────────────────────────────────────────
export default function TfrView() {
  const [tfrs,    setTfrs]    = useState(null)
  const [notams,  setNotams]  = useState(null)
  const [feedErr, setFeedErr] = useState(false)

  useEffect(() => {
    fetch('/api/dispatch/api/v1/tfr')
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json() })
      .then(d => {
        const list = Array.isArray(d) ? d : (d.tfrs || [])
        setTfrs(list)
        // Detect FAA feed degradation: if >10% of TFRs have no dates, flag it
        const nullDates = list.filter(t => !t.effective_start && !t.effective_end).length
        if (list.length > 0 && nullDates / list.length > 0.1) setFeedErr(true)
      })
      .catch(() => setTfrs([]))

    fetch('/api/dispatch/api/v1/notams')
      .then(r => r.json())
      .then(d => setNotams(Array.isArray(d) ? d : (d.notams || [])))
      .catch(() => setNotams([]))
  }, [])

  const refresh = () => {
    setTfrs(null); setNotams(null); setFeedErr(false)
    fetch('/api/dispatch/api/v1/tfr')
      .then(r => r.json())
      .then(d => {
        const list = Array.isArray(d) ? d : (d.tfrs || [])
        setTfrs(list)
        const nullDates = list.filter(t => !t.effective_start && !t.effective_end).length
        if (list.length > 0 && nullDates / list.length > 0.1) setFeedErr(true)
      })
      .catch(() => setTfrs([]))

    fetch('/api/dispatch/api/v1/notams')
      .then(r => r.json())
      .then(d => setNotams(Array.isArray(d) ? d : (d.notams || [])))
      .catch(() => setNotams([]))
  }

  return (
    <div className="panel-view">
      <div className="tfr-view-header">
        <h2>Airspace Restrictions</h2>
        <button className="intel-refresh-btn" onClick={refresh}
                disabled={tfrs === null} title="Refresh">
          {tfrs === null ? '⟳' : '↻'}
        </button>
      </div>

      <VipSection     tfrs={tfrs}   loading={tfrs === null} />
      <GeneralSection tfrs={tfrs}   loading={tfrs === null} feedDegraded={feedErr} />
      <NotamSection   notams={notams} loading={notams === null} />
    </div>
  )
}
