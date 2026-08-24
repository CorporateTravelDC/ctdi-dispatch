import { useState, useCallback } from 'react'

/**
 * DemoLoginGate -- full-screen password gate shown by App.jsx when
 * useDemoStatus() reports demo_mode=true and authenticated=false (i.e.
 * this is the public dispatch-runner.example.com hostname and
 * no valid session cookie is present yet). Nothing else in the app renders
 * until this succeeds -- see App.jsx's early-return.
 *
 * On success the runner backend (POST /api/demo/login) has already set an
 * HttpOnly session cookie; this component never sees or handles the raw
 * token, only the label/window/speed it gets back for display and the
 * onSuccess callback that tells App.jsx to re-check status.
 */
export default function DemoLoginGate({ onSuccess }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = useCallback(async (e) => {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    try {
      const r = await fetch('/api/demo/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (!r.ok) {
        setError(r.status === 401 ? 'Incorrect password.' : 'Login failed -- try again.')
        setBusy(false)
        return
      }
      const data = await r.json()
      onSuccess(data)
    } catch {
      setError('Network error -- try again.')
      setBusy(false)
    }
  }, [password, busy, onSuccess])

  return (
    <div className="demo-gate">
      <div className="demo-gate-panel">
        <span className="demo-gate-brand">CORPORATE TRAVEL DISPATCH INTELLIGENCE</span>
        <h1 className="demo-gate-title">Privileged Demo Access</h1>
        <p className="demo-gate-copy">
          This is a rolling playback of a real dispatch operations platform,
          replayed from a private archive. Enter the access password you
          were given to view it.
        </p>
        <div className="demo-gate-disclosure" role="note">
          <span className="demo-gate-disclosure-label">Not live data</span>
          <p className="demo-gate-disclosure-body">
            The clock and calendar date shown inside this demo follow
            <strong> your own device&rsquo;s current time</strong>. The
            operational data itself &mdash; flights, trains, weather, alerts and
            every dispatch figure &mdash; is a <strong>historical replay at
            least two weeks old</strong>, played on a continuous loop. Nothing
            shown reflects current, real-world operations or any traveler&rsquo;s
            actual movements.
          </p>
        </div>
        <form onSubmit={submit} className="demo-gate-form">
          <input
            type="password"
            className="demo-gate-input"
            placeholder="Access password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="off"
            disabled={busy}
          />
          <button type="submit" className="demo-gate-btn" disabled={busy || !password}>
            {busy ? 'Checking…' : 'Enter'}
          </button>
        </form>
        {error && <p className="demo-gate-error">{error}</p>}
        <p className="demo-gate-footer">
          [operator LLC], LLC &middot;{' '}
          <a href="https://example.com" className="demo-gate-link">
            example.com
          </a>
        </p>
      </div>
    </div>
  )
}
