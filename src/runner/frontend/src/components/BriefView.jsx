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

export default function BriefView() {
  const [brief, setBrief]         = useState(null)
  const [loading, setLoading]     = useState(true)
  const [history, setHistory]     = useState([])
  const [selected, setSelected]   = useState(null)   // null = current, id = archived
  const [archText, setArchText]   = useState(null)
  const [archLoading, setArchLoading] = useState(false)

  // Load current brief + history index on mount
  useEffect(() => {
    Promise.all([
      fetch('/api/dispatch/api/v1/brief').then(r => r.text()),
      fetch('/api/dispatch/api/v1/brief/history?limit=7').then(r => r.json()).catch(() => []),
    ]).then(([text, hist]) => {
      setBrief(text)
      setHistory(Array.isArray(hist) ? hist : [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  // Load archived brief when a history entry is selected
  useEffect(() => {
    if (selected === null) { setArchText(null); return }
    setArchLoading(true)
    fetch(`/api/dispatch/api/v1/brief/${selected}`)
      .then(r => r.text())
      .then(t => { setArchText(t); setArchLoading(false) })
      .catch(() => { setArchText('Failed to load brief.'); setArchLoading(false) })
  }, [selected])

  const displayText = selected === null ? brief : archText
  const displayLoading = selected === null ? loading : archLoading

  return (
    <div className="panel-view">
      <div className="brief-header-row">
        <h2>Operational Brief</h2>
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
      </div>

      {displayLoading ? (
        <p className="muted">Loading brief...</p>
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
          <p className="muted brief-text">
            No brief available yet. Briefs generate at 00:00, 06:00, 12:00, 18:00 ET
            via the ops-brief skill.
          </p>
        </div>
      )}
    </div>
  )
}
