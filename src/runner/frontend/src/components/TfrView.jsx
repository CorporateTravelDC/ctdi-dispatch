import { useState, useEffect } from 'react'

export default function TfrView() {
  const [tfrs, setTfrs] = useState(null)

  useEffect(() => {
    fetch('/api/dispatch/api/v1/tfr-enriched')
      .then(r => r.json()).then(setTfrs).catch(() => setTfrs([]))
  }, [])

  if (!tfrs) return <div className="panel-view"><p className="muted">Loading TFRs...</p></div>
  if (!tfrs.length) return <div className="panel-view"><p className="muted">No active TFRs</p></div>

  return (
    <div className="panel-view">
      <h2>Active TFRs ({tfrs.length})</h2>
      {tfrs.map(tfr => (
        <div key={tfr.tfr_id} className={`tfr-card ${tfr.is_vip ? 'vip' : ''}`}>
          <div className="tfr-card-header">
            <span className="tfr-id">{tfr.tfr_id}</span>
            {tfr.is_vip && <span className="vip-badge">VIP / POTUS</span>}
          </div>
          {tfr.enriched_text && (
            <p className="tfr-narrative">{tfr.enriched_text}</p>
          )}
          <div className="tfr-meta">
            <span>Start: {tfr.effective_start || '--'}</span>
            <span>End: {tfr.effective_end || '--'}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
