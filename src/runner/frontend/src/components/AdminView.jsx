import { useState } from 'react'

const FEEDS = ['metar', 'tfr', 'nws', 'nas', 'ops_plan']

function useToken() {
  const [token, setToken] = useState(() => localStorage.getItem('adminToken') || '')
  const save = (t) => { setToken(t); localStorage.setItem('adminToken', t) }
  return [token, save]
}

function authHeaders(token) {
  return { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }
}

function FeedRow({ name, feed }) {
  if (!feed) return null
  const age       = feed.age_seconds ?? null
  const threshold = feed.stale_threshold_seconds || 900
  const covered   = !!feed.push_covered
  const hasError  = feed.error && !feed.error.startsWith('pending_credentials')
  const stale     = !covered && (age === null || age > threshold) && !hasError
  const cls = covered ? 'push-covered'
            : age === null ? 'unknown'
            : stale || hasError ? 'stale'
            : 'fresh'
  return (
    <tr className={`feed-row ${cls}`}>
      <td className="feed-name">{name}</td>
      <td>{age !== null ? `${Math.round(age)}s` : '--'}</td>
      <td>{covered ? 'push-covered' : feed.error || 'ok'}</td>
      <td><div className={`feed-dot ${cls}`} /></td>
    </tr>
  )
}

export default function AdminView() {
  const [token, setToken]           = useToken()
  const [log, setLog]               = useState([])
  const [vip, setVip]               = useState('')
  const [feedOpen, setFeedOpen]     = useState(false)
  const [feeds, setFeeds]           = useState(null)
  const [feedErr, setFeedErr]       = useState(null)
  const [healthOpen, setHealthOpen] = useState(false)
  const [health, setHealth]         = useState(null)
  const [healthErr, setHealthErr]   = useState(null)

  const addLog = (msg) => setLog(prev => [`[${new Date().toLocaleTimeString()}] ${msg}`, ...prev.slice(0, 19)])

  const refreshFeed = async (feed) => {
    const r = await fetch(`/api/dispatch/admin/refresh-feed/${feed}`,
      { method: 'POST', headers: authHeaders(token) })
    addLog(r.ok ? `Feed refresh queued: ${feed}` : `FAIL refresh ${feed}: ${r.status}`)
  }

  const forceCps = async () => {
    const r = await fetch('/api/dispatch/admin/force-recompute-cps',
      { method: 'POST', headers: authHeaders(token) })
    addLog(r.ok ? 'CPS recompute queued' : `FAIL CPS recompute: ${r.status}`)
  }

  const testAlert = async () => {
    const r = await fetch('/api/dispatch/admin/push-test-alert',
      { method: 'POST', headers: authHeaders(token),
        body: JSON.stringify({ message: 'dispatch-runner test' }) })
    addLog(r.ok ? 'Test alert sent' : `FAIL test alert: ${r.status}`)
  }

  const addVip = async () => {
    if (!vip.trim()) return
    const r = await fetch('/api/dispatch/admin/vip',
      { method: 'POST', headers: authHeaders(token),
        body: JSON.stringify({ entry: vip.trim() }) })
    addLog(r.ok ? `VIP added: ${vip}` : `FAIL add VIP: ${r.status}`)
    if (r.ok) setVip('')
  }

  const loadFeeds = async () => {
    setFeedErr(null)
    try {
      const r = await fetch('/api/dispatch/admin/feeds', { headers: authHeaders(token) })
      if (r.status === 401 || r.status === 403) throw new Error('Unauthorized — check bearer token')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      const list = Array.isArray(data) ? data : (data?.feeds ?? [])
      const keyed = {}
      list.forEach(f => { if (f.feed_name) keyed[f.feed_name] = f })
      setFeeds(keyed)
    } catch (e) { setFeedErr(e.message) }
  }

  const loadHealth = async () => {
    setHealthErr(null)
    try {
      const r = await fetch('/api/dispatch/admin/healthz', { headers: authHeaders(token) })
      if (r.status === 401 || r.status === 403) throw new Error('Unauthorized — check bearer token')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setHealth(await r.json())
    } catch (e) { setHealthErr(e.message) }
  }

  const toggleFeeds = () => {
    const next = !feedOpen
    setFeedOpen(next)
    if (next && !feeds) loadFeeds()
  }

  const toggleHealth = () => {
    const next = !healthOpen
    setHealthOpen(next)
    if (next && !health) loadHealth()
  }

  return (
    <div className="panel-view">
      <h2>Admin</h2>

      <section className="admin-section">
        <h3>Token</h3>
        <input className="token-input" type="password" placeholder="Bearer token"
          value={token} onChange={e => setToken(e.target.value)}
          onBlur={e => { localStorage.setItem('adminToken', e.target.value) }} />
      </section>

      <section className="admin-section">
        <h3>Feed Refresh</h3>
        <div className="btn-row">
          {FEEDS.map(f => (
            <button key={f} className="admin-btn" onClick={() => refreshFeed(f)}>{f}</button>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h3>Actions</h3>
        <div className="btn-row">
          <button className="admin-btn" onClick={forceCps}>Force CPS</button>
          <button className="admin-btn warn" onClick={testAlert}>Test Alert</button>
        </div>
      </section>

      <section className="admin-section">
        <h3>VIP Watchlist</h3>
        <div className="vip-add-row">
          <input className="vip-input" placeholder="Callsign or tail number"
            value={vip} onChange={e => setVip(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addVip()} />
          <button className="admin-btn" onClick={addVip}>Add</button>
        </div>
      </section>

      {/* Feed Status — token-gated, collapsible */}
      {token && (
        <section className="admin-section">
          <div className="admin-toggle-row" onClick={toggleFeeds}>
            <h3 style={{ margin: 0, cursor: 'pointer' }}>
              Feed Status <span className="admin-toggle-chevron">{feedOpen ? '▾' : '▸'}</span>
            </h3>
            {feedOpen && (
              <button className="admin-btn admin-btn-sm" onClick={e => { e.stopPropagation(); loadFeeds() }}>
                Refresh
              </button>
            )}
          </div>
          {feedOpen && (
            feedErr
              ? <p className="admin-error">{feedErr}</p>
              : feeds
                ? (
                  <table className="feed-table">
                    <thead><tr><th>Feed</th><th>Age</th><th>Status</th><th></th></tr></thead>
                    <tbody>
                      {Object.entries(feeds).map(([name, feed]) => (
                        <FeedRow key={name} name={name} feed={feed} />
                      ))}
                    </tbody>
                  </table>
                )
                : <p className="muted">Loading...</p>
          )}
        </section>
      )}

      {/* Dispatch Health — token-gated, collapsible */}
      {token && (
        <section className="admin-section">
          <div className="admin-toggle-row" onClick={toggleHealth}>
            <h3 style={{ margin: 0, cursor: 'pointer' }}>
              Dispatch Health <span className="admin-toggle-chevron">{healthOpen ? '▾' : '▸'}</span>
            </h3>
            {healthOpen && (
              <button className="admin-btn admin-btn-sm" onClick={e => { e.stopPropagation(); loadHealth() }}>
                Refresh
              </button>
            )}
          </div>
          {healthOpen && (
            healthErr
              ? <p className="admin-error">{healthErr}</p>
              : health
                ? (
                  <div className="health-row">
                    <span>Status: <b>{health.status}</b></span>
                    {health.snapshot_age_seconds != null && (
                      <span>Snapshot: {health.snapshot_age_seconds}s</span>
                    )}
                    {health.audit_count_24h != null && (
                      <span>Audits 24h: {health.audit_count_24h}</span>
                    )}
                  </div>
                )
                : <p className="muted">Loading...</p>
          )}
        </section>
      )}

      {log.length > 0 && (
        <section className="admin-section">
          <h3>Log</h3>
          <div className="admin-log">
            {log.map((l, i) => <div key={i} className="log-line">{l}</div>)}
          </div>
        </section>
      )}
    </div>
  )
}
