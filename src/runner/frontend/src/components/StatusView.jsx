import { useState, useEffect } from 'react'

function FeedRow({ name, feed }) {
  if (!feed) return null
  const age = feed.age_seconds ?? null
  const stale = age !== null && age > (feed.stale_threshold_seconds || 900)
  const cls = age === null ? 'unknown' : stale ? 'stale' : 'fresh'
  return (
    <tr className={`feed-row ${cls}`}>
      <td className="feed-name">{name}</td>
      <td>{age !== null ? `${Math.round(age)}s` : '--'}</td>
      <td>{feed.error || 'ok'}</td>
      <td className={`feed-dot ${cls}`} />
    </tr>
  )
}

export default function StatusView({ liveState }) {
  const [feeds, setFeeds] = useState(null)
  const cps = liveState?.cps

  useEffect(() => {
    fetch('/api/dispatch/api/v1/feeds')
      .then(r => r.json()).then(setFeeds).catch(() => {})
  }, [liveState])

  const factorColor = v =>
    v === 'ok' ? '#39ff14' : v === 'marginal' ? '#ffd700' : '#ff3131'

  return (
    <div className="panel-view">
      <h2>System Status</h2>

      <section className="status-section">
        <h3>Critical Predictability State</h3>
        {cps ? (
          <div className="cps-card">
            <div className={`cps-score-large ${cps.score?.toLowerCase()}`}>
              {cps.score} / {cps.label}
            </div>
            <p className="cps-narrative">{cps.narrative}</p>
            {cps.factors && (
              <div className="cps-factors">
                {Object.entries(cps.factors).map(([k, v]) => (
                  <span key={k} className="factor-chip"
                        style={{ borderColor: factorColor(v) }}>
                    {k}: {v}
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : <p className="muted">CPS data unavailable</p>}
      </section>

      <section className="status-section">
        <h3>Feed Freshness</h3>
        {feeds ? (
          <table className="feed-table">
            <thead>
              <tr><th>Feed</th><th>Age</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {Object.entries(feeds).map(([name, feed]) => (
                <FeedRow key={name} name={name} feed={feed} />
              ))}
            </tbody>
          </table>
        ) : <p className="muted">Loading feeds...</p>}
      </section>

      {liveState?.healthz && (
        <section className="status-section">
          <h3>Dispatch Health</h3>
          <div className="health-row">
            <span>Status: <b>{liveState.healthz.status}</b></span>
            <span>Snapshot: {liveState.healthz.snapshot_age_seconds}s</span>
            <span>Audits 24h: {liveState.healthz.audit_count_24h}</span>
          </div>
        </section>
      )}
    </div>
  )
}
