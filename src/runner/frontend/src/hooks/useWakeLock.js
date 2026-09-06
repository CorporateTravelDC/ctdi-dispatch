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
 * Hardened 2026-08-24: reported still pausing on a GrapheneOS Pixel (both
 * the installed PWA and in-browser), not just the iOS Safari case this
 * hook already documented. GrapheneOS's stricter-than-stock background/
 * power management is a deliberate hardening tradeoff, not a bug to code
 * around, but the re-acquire path only listened for `visibilitychange` --
 * some mobile browsers fire `focus`/`pageshow` on an app-switcher return
 * without a matching `visibilitychange`, or fire it later than expected,
 * so the lock could sit un-reacquired longer than necessary. Now also
 * re-acquires on `focus`/`pageshow`, and a 30s health-check re-requests if
 * `lockRef.current` has gone unexpectedly null while still visible without
 * the lock's own 'release' listener having fired -- belt-and-suspenders
 * against a platform silently dropping the lock without the event firing
 * reliably. None of this can force a screen to stay on once the OS fully
 * backgrounds/suspends the whole browser process (switching apps, screen
 * physically powered off) -- that's a real platform limit, not something
 * client-side JS can override on any platform. See
 * useVisibilityAwareInterval.js for the complementary fix: closing the
 * stale-data gap for whenever the app WAS backgrounded, regardless of
 * whether the wake lock held.
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
    // focus/pageshow: some mobile browsers return from an app-switcher
    // without a timely (or any) matching visibilitychange -- see the
    // 2026-08-24 GrapheneOS hardening note above. onVisibility itself
    // re-checks document.visibilityState, so a stray fire while still
    // hidden is a harmless no-op.
    window.addEventListener('pageshow', onVisibility)
    window.addEventListener('focus', onVisibility)

    // Periodic health-check: re-request if the lock has gone unexpectedly
    // null while still visible without the 'release' listener having
    // fired. Belt-and-suspenders against a platform silently dropping the
    // lock without a reliable event -- observed-as-possible on hardened
    // Android builds, not confirmed root cause, cheap enough to run
    // regardless.
    const healthCheck = setInterval(() => {
      if (document.visibilityState === 'visible' && !lockRef.current) {
        requestLock()
      }
    }, 30_000)

    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pageshow', onVisibility)
      window.removeEventListener('focus', onVisibility)
      clearInterval(healthCheck)
      lockRef.current?.release().catch(() => {})
    }
  }, [requestLock])

  return status
}
