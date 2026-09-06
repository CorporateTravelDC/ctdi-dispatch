import { useEffect, useRef } from 'react'

/**
 * useVisibilityAwareInterval — setInterval(callback, delayMs) that ALSO
 * fires an immediate extra call the moment the tab/PWA becomes visible or
 * focused again, instead of waiting up to a full `delayMs` for the next
 * natural tick.
 *
 * Added 2026-08-24: the operator reported "the timer pauses" on a
 * GrapheneOS Pixel, both in the installed home-screen PWA and in-browser --
 * i.e. NOT only the well-known iOS Safari standalone-PWA case documented
 * in useWakeLock.js. A backgrounded/suspended tab genuinely can throttle
 * or fully pause a raw setInterval regardless of platform -- that's
 * deliberate browser/OS battery behavior (GrapheneOS's hardening makes it
 * more aggressive than stock Android by design, not a bug to code around),
 * and nothing in JS can force a timer to keep ticking while truly
 * backgrounded. What IS fixable, and what this hook fixes: the stale
 * display for however long is left until the next natural tick once you
 * DO look back at the app. This closes that gap for every interval-based
 * data poll in the runner frontend. See useWakeLock.js for the separate,
 * complementary screen-sleep-prevention half of this fix.
 *
 * Resets the interval's phase on every visibility-triggered fire so the
 * next natural tick is spaced `delayMs` from the just-forced refresh, not
 * from whenever the interval happened to be re-armed pre-background --
 * avoids a redundant near-duplicate fetch moments later.
 *
 * @param {Function} callback   called with no args on each tick; may be async
 * @param {number}   delayMs    interval period
 * @param {boolean}  enabled    pass false to disable entirely (default true)
 */
export function useVisibilityAwareInterval(callback, delayMs, enabled = true) {
  const callbackRef = useRef(callback)
  callbackRef.current = callback

  useEffect(() => {
    if (!enabled) return undefined

    let intervalId = null
    const tick = () => callbackRef.current()

    const arm = () => {
      if (intervalId) clearInterval(intervalId)
      intervalId = setInterval(tick, delayMs)
    }

    tick()
    arm()

    // visibilitychange covers tab switches and screen lock/unlock on
    // desktop and most mobile browsers. pageshow additionally covers
    // bfcache restores (back/forward navigation) some browsers don't
    // fire visibilitychange for. focus covers window/app-switcher
    // returns some mobile browsers fire instead of (or without)
    // visibilitychange. All three funnel through the same handler,
    // which re-checks document.visibilityState itself, so a stray
    // 'focus'/'pageshow' while already hidden is a harmless no-op.
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      tick()
      arm()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('pageshow', onVisible)
    window.addEventListener('focus', onVisible)

    return () => {
      clearInterval(intervalId)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('pageshow', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [delayMs, enabled])
}
