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

export default function AdminView() {
  const [token, setToken] = useToken()
  const [log, setLog] = useState([])
  const [vip, setVip] = useState('')

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
