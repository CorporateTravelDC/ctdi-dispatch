import { useState, useEffect, useCallback } from 'react'

// Re-check periodically so a session that expires mid-visit (8h TTL) is
// caught and the gate re-shown, without polling aggressively -- logging in
// again after 8h of a demo sitting open in a browser tab is a fine outcome.
const CHECK_INTERVAL_MS = 5 * 60_000

/**
 * useDemoStatus -- asks the runner's own /api/demo/status whether this is
 * a demo-mode instance at all, whether THIS request is already
 * authenticated (trusted Tailscale origin, or a valid session cookie), and
 * the active profile's label/window/speed. Mirrors useTailnet.js's shape.
 *
 * Returns `null` while the first check is in flight, then the status
 * object. Callers should treat `null` as "not yet known" and avoid
 * rendering the app shell until it resolves, so there's no flash of live
 * content before the gate check completes on a genuinely gated visit.
 *
 * Call `recheck()` right after a successful login to pick up the new
 * cookie immediately rather than waiting for the next poll interval.
 */
export function useDemoStatus() {
  const [status, setStatus] = useState(null)

  const check = useCallback(async () => {
    try {
      const r = await fetch('/api/demo/status', { cache: 'no-store' })
      const d = r.ok ? await r.json() : { demo_mode: false, authenticated: true }
      setStatus(d)
    } catch {
      // Network hiccup -- keep whatever we last knew rather than flashing
      // the gate open/closed on a transient fetch failure.
      setStatus(prev => prev ?? { demo_mode: false, authenticated: true })
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const run = () => { if (!cancelled) check() }
    run()
    const id = setInterval(run, CHECK_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [check])

  return [status, check]
}
