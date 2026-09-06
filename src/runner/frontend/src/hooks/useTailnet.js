import { useState, useEffect, useRef, useCallback } from 'react'

// Re-check every 15s so a genuine network-path change (e.g. actually
// leaving the tailnet) is picked up in reasonable time without polling
// aggressively.
const CHECK_INTERVAL_MS = 15_000

// A changed result must repeat this many times in a row before it's
// actually applied. Added 2026-07-21: on a device that's on both cellular
// and Tailscale (or switching between them), a single check can catch the
// request mid-handoff and answer honestly-but-transiently from whichever
// path was live at that instant -- which flickered the ADMIN tab/search bar
// for a moment even on the trusted side. This isn't a caching bug, the
// backend's per-request answer is correct every time; it's the UI reacting
// to real but momentary network noise. Requiring 2 consecutive matching
// checks (~15-30s) before flipping an already-confirmed value smooths that
// out while still reflecting a real, sustained change quickly.
const DEBOUNCE_CONFIRMATIONS = 2

/**
 * useTailnet — asks the runner's own /api/whoami whether THIS request
 * arrived over a trusted (Tailscale/LAN) origin. Added 2026-07-21 so the
 * frontend can hide admin-adjacent UI (the ADMIN nav tab, the Settings
 * panel's cross-device-sync/admin-token section, the /admin route itself,
 * the ADS-B search bar) entirely on Ops (public hostname) rather than show
 * it present-but-broken. Mirrors the backend's tailscale_gate middleware's
 * own _is_trusted check -- see runner/main.py.
 *
 * Polls periodically (not just once on mount) and debounces flips away from
 * the currently-confirmed value -- see DEBOUNCE_CONFIRMATIONS above.
 *
 * Returns `null` while the first check is in flight (first paint), then
 * `true`/`false`. Callers should treat `null` as "not yet known" and
 * default to hiding admin UI until it resolves, so there's no flash of
 * admin controls before the check completes.
 */
export function useTailnet() {
  const [tailnet, setTailnet] = useState(null)
  // Tracks a candidate value that differs from the currently-confirmed
  // `tailnet` state, and how many consecutive checks have agreed with it.
  const pendingRef = useRef({ value: null, count: 0 })

  const check = useCallback(async () => {
    let result
    try {
      const r = await fetch('/api/whoami', { cache: 'no-store' })
      const d = r.ok ? await r.json() : { tailnet: false }
      result = !!d.tailnet
    } catch {
      result = false
    }

    setTailnet(prev => {
      if (prev === null) {
        // First resolution ever -- apply immediately, nothing to debounce
        // against yet.
        pendingRef.current = { value: result, count: 0 }
        return result
      }
      if (result === prev) {
        // Matches the confirmed state -- clear any pending flip.
        pendingRef.current = { value: prev, count: 0 }
        return prev
      }
      // Differs from the confirmed state. Only flip after this same
      // differing value has repeated DEBOUNCE_CONFIRMATIONS times in a row.
      if (pendingRef.current.value === result) {
        pendingRef.current.count += 1
      } else {
        pendingRef.current = { value: result, count: 1 }
      }
      if (pendingRef.current.count >= DEBOUNCE_CONFIRMATIONS) {
        pendingRef.current = { value: result, count: 0 }
        return result
      }
      return prev
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    const run = () => { if (!cancelled) check() }
    run()
    const id = setInterval(run, CHECK_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [check])

  return tailnet
}
