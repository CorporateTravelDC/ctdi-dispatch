import { useState, useEffect } from 'react'

function fmtTs(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
      timeZone: 'UTC', hour12: false,
    }) + 'Z'
  } catch (_) { return iso }
}

function BriefTab({ type }) {
  const isWeekly   = type === 'weekly'
  const currentUrl = isWeekly ? '/api/dispatch/api/v1/brief/weekly' : '/api/dispatch/api/v1/brief'
  const historyUrl = isWeekly
    ? '/api/dispatch/api/v1/brief/history?limit=8&type=weekly'
    : '/api/dispatch/api/v1/brief/history?limit=8&type=ops'

  const [brief,       setBrief]       = useState(null)
  const [loading,     setLoading]     = useState(true)
  const [history,     setHistory]     = useState([])
  const [selected,    setSelected]    = useState(null)
  const [archText,    setArchText]    = useState(null)
  const [archLoading, setArchLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    setBrief(null)
    setHistory([])
    setSelected(null)
    setArchText(null)
    Promise.all([
      fetch(currentUrl).then(r => r.ok ? r.text() : null),
      fetch(historyUrl).then(r => r.json()).catch(() => []),
    ]).then(([text, hist]) => {
      setBrief(text || null)
      setHistory(Array.isArray(hist) ? hist : [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [type])

  useEffect(() => {
    if (selected === null) { setArchText(null); return }
    setArchLoading(true)
    fetch(`/api/dispatch/api/v1/brief/${selected}`)
      .then(r => r.ok ? r.text() : Promise.reject(r.status))
      .then(t => { setArchText(t); setArchLoading(false) })
      .catch(() => { setArchText('Failed to load brief.'); setArchLoading(false) })
  }, [selected])

  const displayText    = selected === null ? brief    : archText
  const displayLoading = selected === null ? loading  : archLoading
  const emptyMsg       = isWeekly
    ? 'No weekly summary yet. Runs Sunday 18:00 ET via weekly-summary skill.'
    : 'No brief available yet. Briefs generate at 00:00, 06:00, 12:00, 18:00 ET via the ops-brief skill.'

  return (
    <>
      {history.length > 0 && (
        <div className="brief-history-nav">
          <span className="brief-hist-label">HISTORY:</span>
          <button
            className={`brief-hist-btn${selected === null ? ' active' : ''}`}
            onClick={() => setSelected(null)}
          >CURRENT</button>
          {history.map(h => (
            <button
              key={h.id}
              className={`brief-hist-btn${selected === h.id ? ' active' : ''}`}
              onClick={() => setSelected(h.id)}
              title={`${h.brief_type.toUpperCase()} — ${fmtTs(h.generated_at)}`}
            >{fmtTs(h.generated_at)}</button>
          ))}
        </div>
      )}

      {displayLoading ? (
        <p className="muted">Loading...</p>
      ) : displayText ? (
        <div className="brief-card">
          {selected !== null && (() => {
            const h = history.find(x => x.id === selected)
            return h ? (
              <p className="brief-meta">
                Archived: {fmtTs(h.generated_at)} — {h.brief_type.toUpperCase()} / {h.source}
              </p>
            ) : null
          })()}
          <pre className="brief-text">{displayText}</pre>
        </div>
      ) : (
        <div className="brief-card">
          <p className="muted brief-text">{emptyMsg}</p>
        </div>
      )}
    </>
  )
}

export default function BriefView() {
  const [tab, setTab] = useState('ops')

  return (
    <div className="panel-view">
      <div className="brief-header-row">
        <div className="brief-tab-row">
          <h2>Operational Brief</h2>
          <div className="brief-type-tabs">
            <button
              className={`brief-type-btn${tab === 'ops' ? ' active' : ''}`}
              onClick={() => setTab('ops')}
            >OPS BRIEF</button>
            <button
              className={`brief-type-btn${tab === 'weekly' ? ' active' : ''}`}
              onClick={() => setTab('weekly')}
            >WEEKLY</button>
          </div>
        </div>
      </div>

      <BriefTab key={tab} type={tab} />
    </div>
  )
}
