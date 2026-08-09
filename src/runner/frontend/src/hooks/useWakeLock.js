import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * useWakeLock — requests a Screen Wake Lock so the display never sleeps
 * while this PWA is the foreground app, keeping the /api/stream EventSource
 * and the various setInterval polling hooks running instead of getting
 * throttled/suspended the way a backgrounded tab would. Added 2026-08-02
 * for the Pixel-mounted deployment (Chrome/Android — Wake Lock API has
 * been reliable there for a while; the well-known flakiness is specifically
 * iOS Safari standalone PWAs, not a concern on this target device).
 *
 * The Wake Lock API force-releases the lock the instant the document goes
 * hidden (tab switch, screen off, app backgrounded) — that's normal browser
 * behavior, not something to fight — so this re-requests on every
 * `visibilitychange` back to visible, not just once on mount.
 *
 * Distinct from that normal case: if the lock is released while the
 * document is STILL visible (OS-level power management overriding it,
 * some other app or system dialog stealing it, anything unexpected), that's
 * the actual "this could be silently going dark" signal the operator asked to
 * surface rather than paper over. On that path we both flag 'lost' AND
 * immediately attempt a single re-acquire — self-heals when possible,
 * still visibly flags that it happened rather than staying silent about it.
 *
 * Returns a status string for a UI indicator to render:
 *   'unsupported' — navigator.wakeLock doesn't exist (old/unsupported browser)
 *   'pending'     — request in flight, nothing confirmed yet
 *   'active'      — lock currently held, screen won't sleep
 *   'lost'        — held a lock and it was released while still visible —
 *                    the "someone should notice this" case. A re-acquire
 *                    attempt is already in flight when this is returned.
 *   'denied'      — request() threw (permissions policy, battery saver
 *                    blocking it on some Android configurations, etc.)
 */
export function useWakeLock() {
  const [status, setStatus] = useState(
    typeof navigator !== 'undefined' && 'wakeLock' in navigator ? 'pending' : 'unsupported'
  )
  const lockRef = useRef(null)

  const requestLock = useCallback(async () => {
    if (!('wakeLock' in navigator)) {
      setStatus('unsupported')
      return
    }
    try {
      const lock = await navigator.wakeLock.request('screen')
      lockRef.current = lock
      setStatus('active')

      lock.addEventListener('release', () => {
        lockRef.current = null
        // A release that happens because the document went hidden is
        // expected and silent — the visibilitychange handler below will
        // re-acquire once it's visible again. A release while STILL
        // visible is the unexpected case worth surfacing.
        if (document.visibilityState === 'visible') {
          setStatus('lost')
          requestLock()
        }
      })
    } catch {
      lockRef.current = null
      setStatus('denied')
    }
  }, [])

  useEffect(() => {
    requestLock()

    const onVisibility = () => {
      if (document.visibilityState === 'visible' && !lockRef.current) {
        requestLock()
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      lockRef.current?.release().catch(() => {})
    }
  }, [requestLock])

  return status
}
