import { useState, useEffect } from 'react'

export default function BriefView() {
  const [brief, setBrief] = useState(null)

  useEffect(() => {
    fetch('/api/dispatch/api/v1/brief')
      .then(r => r.json()).then(setBrief).catch(() => {})
  }, [])

  return (
    <div className="panel-view">
      <h2>Daily Brief</h2>
      {brief ? (
        <div className="brief-card">
          {brief.generated_at && (
            <p className="brief-meta">Generated: {brief.generated_at}</p>
          )}
          <pre className="brief-text">{brief.narrative || brief.content || JSON.stringify(brief, null, 2)}</pre>
        </div>
      ) : <p className="muted">Loading brief...</p>}
    </div>
  )
}
