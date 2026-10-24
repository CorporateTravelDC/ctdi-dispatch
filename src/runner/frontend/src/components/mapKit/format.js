// src/runner/frontend/src/components/mapKit/format.js -- tiny shared formatters (2026-10-24).
//
// mapKit/ is the vertical-neutral copy of what components/aircraftMap/ does for aviation (operator
// 2026-10-24: "every coded vertical gets the same experience as aviation"). The aviation map is NOT
// refactored onto it -- these are extracted copies so AIS / trains / UTM can share one list panel,
// legend, trails store and focus logic without touching the aircraft code.

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

export const finite = v => v != null && v !== '' && Number.isFinite(Number(v))

/** Human age: 12s / 3m / 1h 05m */
export function fmtAge(s) {
  if (s == null || !Number.isFinite(Number(s))) return '?'
  s = Math.max(0, Math.round(Number(s)))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${String(m % 60).padStart(2, '0')}m`
}

/** Age of a client-side timestamp (ms epoch) in seconds, or null. */
export function ageSince(ms, now = Date.now()) {
  return Number.isFinite(ms) ? Math.max(0, (now - ms) / 1000) : null
}

/** Number -> fixed string or '--'. */
export function fmtNum(v, digits = 0, unit = '') {
  if (!finite(v)) return '--'
  return `${Number(v).toFixed(digits)}${unit}`
}

/** Read a persisted boolean (localStorage) with a lazy default. */
export function loadBool(key, dflt) {
  try {
    const v = localStorage.getItem(key)
    if (v === '1') return true
    if (v === '0') return false
  } catch {}
  return typeof dflt === 'function' ? dflt() : !!dflt
}
export function saveBool(key, val) {
  try { localStorage.setItem(key, val ? '1' : '0') } catch {}
}

export const isPhone = () => typeof window !== 'undefined' && window.innerWidth < 720
