import { useState, useEffect, useMemo } from 'react'

function fmtTime(s) {
  if (!s) return '--'
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
  return [
    tfr.tfr_id, tfr.enriched_text, tfr.raw_text, tfr.notam_id,
    tfr.facility, tfr.classification, tfr.type,
  ].some(v => v && String(v).toLowerCase().includes(lq))
}

function notamMatchesSearch(n, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [
    n.notam_id, n.icao, n.location, n.text, n.classification, n.type,
  ].some(v => v && String(v).toLowerCase().includes(lq))
}

function TfrPanel({ tfrs, loading }) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!tfrs) return []
    return search ? tfrs.filter(t => tfrMatchesSearch(t, search)) : tfrs
  }, [tfrs, search])

  return (
    <section className="airspace-section" aria-label="Active TFRs">
      <div className="airspace-section-header">
        <h3>TFRs <span className="airspace-count">{tfrs?.length ?? 0}</span></h3>
        <input
          className="sig-search"
          type="search"
          placeholder="search TFRs…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search TFRs"
        />
      </div>

      {loading ? (
        <p className="muted">Loading TFRs...</p>
      ) : !tfrs?.length ? (
        <p className="muted">No active TFRs</p>
      ) : !filtered.length ? (
        <p className="muted">No TFRs matching &ldquo;{search}&rdquo;</p>
      ) : (
        filtered.map(tfr => (
          <div key={tfr.tfr_id} className={`tfr-card ${tfr.is_vip ? 'vip' : ''}`}>
            <div className="tfr-card-header">
              <span className="tfr-id">{tfr.tfr_id}</span>
              {tfr.is_vip && <span className="vip-badge">VIP / POTUS</span>}
            </div>
            {tfr.enriched_text && (
              <p className="tfr-narrative">{tfr.enriched_text}</p>
            )}
            <div className="tfr-meta">
              <span>Start: {fmtTime(tfr.effective_start)}</span>
              <span>End: {fmtTime(tfr.effective_end)}</span>
              {tfr.altitude_floor != null && <span>Floor: {tfr.altitude_floor} ft</span>}
              {tfr.altitude_ceiling != null && <span>Ceil: {tfr.altitude_ceiling} ft</span>}
            </div>
          </div>
        ))
      )}
    </section>
  )
}

function NotamPanel({ notams, loading }) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!notams) return []
    return search ? notams.filter(n => notamMatchesSearch(n, search)) : notams
  }, [notams, search])

  return (
    <section className="airspace-section" aria-label="Active NOTAMs">
      <div className="airspace-section-header">
        <h3>NOTAMs <span className="airspace-count">{notams?.length ?? 0}</span></h3>
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
        <p className="muted">Loading NOTAMs...</p>
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
              {n.icao && <span className="notam-icao">{n.icao}</span>}
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

export default function TfrView() {
  const [tfrs, setTfrs]     = useState(null)
  const [notams, setNotams] = useState(null)

  useEffect(() => {
    fetch('/api/dispatch/api/v1/tfr-enriched')
      .then(r => r.json())
      .then(d => setTfrs(Array.isArray(d) ? d : (d.tfrs || [])))
      .catch(() => setTfrs([]))

    fetch('/api/dispatch/api/v1/notams')
      .then(r => r.json())
      .then(d => setNotams(Array.isArray(d) ? d : (d.notams || [])))
      .catch(() => setNotams([]))
  }, [])

  return (
    <div className="panel-view">
      <h2>Airspace — TFRs &amp; NOTAMs</h2>
      <TfrPanel   tfrs={tfrs}     loading={tfrs === null} />
      <NotamPanel notams={notams} loading={notams === null} />
    </div>
  )
}
