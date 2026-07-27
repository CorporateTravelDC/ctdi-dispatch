import { useState, useEffect } from 'react'

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
  const [bwState,  setBwState]      = useState(null)
  const [bwErr,    setBwErr]        = useState(null)

  const addLog = (msg) => setLog(prev => [`[${new Date().toLocaleTimeString()}] ${msg}`, ...prev.slice(0, 19)])

  useEffect(() => { loadBandwidthPriority() }, [])

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

  // Bandwidth priority (SWIM vs NEXRAD) -- Tier 0 read, admin-gated write.
  // 2026-07-21: bidirectional operator toggle Corey asked for. 'nexrad'
  // pauses ingest's fdps feed (see ingest/swim_client.py); 'swim' is a
  // documented contract for a future NEXRAD Level II puller that doesn't
  // exist yet -- flagged in the UI below, not hidden.
  const loadBandwidthPriority = async () => {
    setBwErr(null)
    try {
      const r = await fetch('/api/dispatch/api/v1/bandwidth-priority')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setBwState(await r.json())
    } catch (e) { setBwErr(e.message) }
  }

  const setBandwidthPriority = async (priority) => {
    const r = await fetch('/api/dispatch/admin/bandwidth-priority', {
      method: 'POST', headers: authHeaders(token),
      body: JSON.stringify({ priority, reason: 'set via admin panel' }),
    })
    addLog(r.ok ? `Bandwidth priority set: ${priority}` : `FAIL set priority: ${r.status}`)
    if (r.ok) loadBandwidthPriority()
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
        <h3>Bandwidth Priority <span className="admin-toggle-chevron" style={{fontSize:'0.65rem'}}>SWIM ↔ NEXRAD</span></h3>
        <div className="btn-row">
          <button
            className={`admin-btn${bwState?.priority === 'swim' && bwState?.active ? ' active' : ''}`}
            onClick={() => setBandwidthPriority('swim')}
          >SWIM Priority</button>
          <button
            className={`admin-btn${!bwState || bwState.priority === 'auto' || !bwState.active ? ' active' : ''}`}
            onClick={() => setBandwidthPriority('auto')}
          >Auto</button>
          <button
            className={`admin-btn warn${bwState?.priority === 'nexrad' && bwState?.active ? ' active' : ''}`}
            onClick={() => setBandwidthPriority('nexrad')}
          >NEXRAD Priority</button>
        </div>
        {bwErr
          ? <p className="admin-error">{bwErr}</p>
          : (
            <p className="muted" style={{fontSize:'0.75rem', marginTop:'0.4rem'}}>
              {bwState
                ? (bwState.active
                    ? `Active: ${bwState.priority.toUpperCase()} priority${bwState.reason ? ` — ${bwState.reason}` : ''}${bwState.expires_at ? ` (expires ${new Date(bwState.expires_at * 1000).toLocaleTimeString()})` : ''}`
                    : 'Auto -- no override in effect')
                : 'Loading…'}
              {bwState?.priority === 'swim' && bwState?.active && (
                ' — note: SWIM priority has nothing to defer to yet (no NEXRAD Level II puller built). NEXRAD priority is functional (pauses SWIM\'s fdps feed).'
              )}
            </p>
          )}
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
