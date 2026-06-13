import { useState, useEffect } from 'react'

/** Format a brief excerpt — first meaningful line, up to maxLen chars */
function briefExcerpt(text, maxLen = 220) {
  if (!text) return null
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  if (!lines.length) return null
  // skip header lines like "=== CSEX DISPATCH BRIEF ===" etc
  const first = lines.find(l => !l.startsWith('=') && l.length > 20) || lines[0]
  return first.length > maxLen ? first.slice(0, maxLen) + '…' : first
}

function MetricCard({ label, value, status, sub }) {
  const cls = status === 'go' ? 'ov-card go'
            : status === 'marginal' ? 'ov-card marginal'
            : status === 'nogo' ? 'ov-card nogo'
            : status === 'warn' ? 'ov-card warn'
            : 'ov-card'
  return (
    <div className={cls}>
      <div className="ov-card-label">{label}</div>
      <div className="ov-card-value">{value ?? '—'}</div>
      {sub && <div className="ov-card-sub">{sub}</div>}
    </div>
  )
}

function AlertBadge({ alerts }) {
  if (!alerts?.length) return (
    <div className="ov-alert-row muted">No active NWS alerts in area.</div>
  )
  return (
    <div className="ov-alerts">
      {alerts.slice(0, 4).map((a, i) => (
        <div key={i} className={`ov-alert-badge sev-${(a.properties?.severity || 'minor').toLowerCase()}`}>
          <span className="ov-alert-event">{a.properties?.event || 'Alert'}</span>
          <span className="ov-alert-area">{(a.properties?.areaDesc || '').split(';')[0]}</span>
        </div>
      ))}
      {alerts.length > 4 && (
        <div className="ov-alert-more">+{alerts.length - 4} more — see Status</div>
      )}
    </div>
  )
}

function FeedSummary({ feeds }) {
  if (!feeds) return <span className="muted">loading…</span>
  const rows = Array.isArray(feeds) ? feeds : (feeds?.feeds ?? Object.values(feeds))
  const total = rows.length
  const stale = rows.filter(f => {
    const age = f.age_seconds ?? null
    const thresh = f.stale_threshold_seconds || 900
    const covered = !!f.push_covered
    const hasErr = f.error && !f.error.startsWith('pending_credentials')
    return !covered && (age === null || age > thresh) && !hasErr
  }).length
  const errored = rows.filter(f => f.error && !f.error.startsWith('pending_credentials')).length

  const cls = errored > 2 ? 'nogo' : stale > 2 ? 'warn' : 'go'
  const label = errored > 2 ? `${errored} feeds errored`
              : stale > 2   ? `${stale} feeds stale`
              : `All ${total} feeds nominal`
  return <span className={`ov-feed-status ${cls}`}>{label}</span>
}

export default function OverviewView({ liveState }) {
  const [brief,   setBrief]   = useState(null)
  const [alerts,  setAlerts]  = useState(null)
  const [tfrs,    setTfrs]    = useState(null)
  const [amtrak,  setAmtrak]  = useState(null)
  const [feeds,   setFeeds]   = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetch('/api/dispatch/api/v1/brief').then(r => r.ok ? r.text() : null).catch(() => null),
      fetch('/api/dispatch/api/v1/alerts').then(r => r.json()).catch(() => null),
      fetch('/api/dispatch/api/v1/tfr').then(r => r.json()).catch(() => null),
      fetch('/api/dispatch/api/v1/amtrak').then(r => r.json()).catch(() => null),
      fetch('/api/dispatch/api/v1/feeds').then(r => r.json()).catch(() => null),
    ]).then(([b, a, t, am, f]) => {
      setBrief(b)
      setAlerts(Array.isArray(a) ? a : (a?.features ?? a?.alerts ?? []))
      setTfrs(Array.isArray(t) ? t : (t?.tfrs ?? []))
      setAmtrak(am)
      setFeeds(f)
      setLoading(false)
    })
  }, [liveState?.healthz?.status]) // re-fetch on health state change

  const cps = liveState?.cps
  const cpsStatus = cps?.score?.toLowerCase() === 'go' ? 'go'
                  : cps?.score?.toLowerCase() === 'marginal' ? 'marginal'
                  : cps?.score?.toLowerCase() === 'no-go' ? 'nogo'
                  : null

  // Amtrak status
  const trains = amtrak?.trains ?? (Array.isArray(amtrak) ? amtrak : [])
  const delayedTrains = trains.filter(t => (t.delay_minutes ?? 0) > 5)
  const amtrakStatus = trains.length === 0 ? null
    : delayedTrains.length > 0 ? `${delayedTrains.length} delayed` : 'On time'
  const amtrakSub = delayedTrains.length > 0
    ? delayedTrains.slice(0,2).map(t => `${t.train_number || t.id}: +${t.delay_minutes}m`).join(' · ')
    : `${trains.length} trains tracked`

  const activeTfrs = Array.isArray(tfrs) ? tfrs.filter(t => t.type !== 'error') : []
  const vipTfrs    = activeTfrs.filter(t =>
    /POTUS|VIP|MOVEMENT|Marine One/i.test(t.notam_text || t.tfr_id || ''))

  return (
    <div className="panel-view ov-view">
      <div className="ov-header">
        <h2>Situational Overview</h2>
        <span className="ov-ts">
          {new Date().toLocaleString('en-US', {
            weekday: 'short', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
          })}
        </span>
      </div>

      {/* Top metric row */}
      <div className="ov-metrics">
        <MetricCard
          label="Operational Readiness"
          value={cps ? `${cps.score} — ${cps.label}` : 'Loading…'}
          status={cpsStatus}
          sub={cps?.narrative?.slice(0, 80)}
        />
        <MetricCard
          label="Environmental Alerts"
          value={alerts === null ? '…' : alerts.length || 'None'}
          status={alerts?.length > 2 ? 'warn' : alerts?.length > 0 ? 'marginal' : 'go'}
          sub={alerts?.length > 0 ? (alerts[0]?.properties?.event || '') : 'Area clear'}
        />
        <MetricCard
          label="Airspace Restrictions"
          value={activeTfrs.length || 'None'}
          status={vipTfrs.length > 0 ? 'nogo' : activeTfrs.length > 0 ? 'marginal' : 'go'}
          sub={vipTfrs.length > 0 ? `${vipTfrs.length} VIP/POTUS TFR active` : activeTfrs.length > 0 ? 'See TFR page' : 'No restrictions'}
        />
        <MetricCard
          label="Rail — Union Station"
          value={trains.length === 0 ? 'N/A' : amtrakStatus}
          status={delayedTrains.length > 0 ? 'warn' : trains.length > 0 ? 'go' : null}
          sub={amtrakSub || 'No data'}
        />
      </div>

      {/* Feed health row */}
      <div className="ov-row">
        <div className="ov-row-label">Feed Health</div>
        <FeedSummary feeds={feeds} />
      </div>

      {/* NWS alerts */}
      <section className="ov-section">
        <div className="ov-section-header">
          <span className="ov-section-title">Active NWS Alerts — DC Metro</span>
        </div>
        {alerts === null
          ? <div className="muted ov-loading">Loading alerts…</div>
          : <AlertBadge alerts={alerts} />}
      </section>

      {/* Ops brief excerpt */}
      <section className="ov-section">
        <div className="ov-section-header">
          <span className="ov-section-title">Current Ops Brief</span>
          <a href="/brief" className="ov-section-link">Full brief →</a>
        </div>
        {loading
          ? <div className="muted ov-loading">Loading…</div>
          : brief
            ? <div className="ov-brief-excerpt">{briefExcerpt(brief) || '(No content parsed)'}</div>
            : <div className="muted ov-loading">No brief available yet.</div>}
      </section>
    </div>
  )
}
