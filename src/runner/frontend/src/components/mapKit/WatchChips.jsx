/**
 * WatchChips -- draggable "WATCHING" chip strip over a map (2026-10-24), the vertical-neutral copy of
 * aircraftMap/WatchlistBadge.jsx. The caller passes the already-filtered watchlist entries for its
 * vertical and a per-view storageKey so each map remembers its own position/collapsed state.
 * Tap a chip -> onFocus(entry). Reuses the existing .map-watchlist-badge / .wlb-* styles.
 */
import { useState, useRef, useCallback } from 'react'

export default function WatchChips({ entries, onFocus, storageKey = 'mk_wl', label = 'WATCHING', unit = 'entries', chipLabel }) {
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem(`${storageKey}_collapsed`) === '1' } catch { return false } })
  const [pos, setPos] = useState(() => {
    try { return JSON.parse(localStorage.getItem(`${storageKey}_pos`)) || { x: null, y: null } }
    catch { return { x: null, y: null } }
  })
  const dragging = useRef(false)
  const moved    = useRef(false)
  const origin   = useRef(null)
  const startPos = useRef(null)
  const badgeRef = useRef(null)

  const beginDrag = useCallback((clientX, clientY) => {
    dragging.current = true
    moved.current = false
    const rect = badgeRef.current.getBoundingClientRect()
    origin.current   = { x: clientX, y: clientY }
    startPos.current = { x: pos.x ?? rect.left, y: pos.y ?? rect.top }
  }, [pos])
  const moveDrag = useCallback((clientX, clientY) => {
    if (!dragging.current) return
    if (Math.abs(clientX - origin.current.x) + Math.abs(clientY - origin.current.y) > 4) moved.current = true
    setPos({ x: startPos.current.x + (clientX - origin.current.x), y: startPos.current.y + (clientY - origin.current.y) })
  }, [])
  const endDrag = useCallback(() => {
    dragging.current = false
    setPos(p => { try { localStorage.setItem(`${storageKey}_pos`, JSON.stringify(p)) } catch {} return p })
  }, [storageKey])

  const onMouseDown = useCallback((e) => {
    if (e.button !== 0) return
    beginDrag(e.clientX, e.clientY)
    const onMove = (ev) => moveDrag(ev.clientX, ev.clientY)
    const onUp = () => { endDrag(); window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [beginDrag, moveDrag, endDrag])

  const onTouchStart = useCallback((e) => {
    const t = e.touches[0]
    beginDrag(t.clientX, t.clientY)
    const onTMove = (ev) => { const tt = ev.touches[0]; moveDrag(tt.clientX, tt.clientY) }
    const onTEnd = () => { endDrag(); window.removeEventListener('touchmove', onTMove); window.removeEventListener('touchend', onTEnd) }
    window.addEventListener('touchmove', onTMove, { passive: true })
    window.addEventListener('touchend', onTEnd)
  }, [beginDrag, moveDrag, endDrag])

  const toggle = useCallback(() => {
    setCollapsed(c => { try { localStorage.setItem(`${storageKey}_collapsed`, !c ? '1' : '0') } catch {} return !c })
  }, [storageKey])

  if (!entries?.length) return null
  const posStyle = pos.x !== null ? { position: 'fixed', left: pos.x, top: pos.y, right: 'unset' } : {}

  if (collapsed) {
    return (
      <button className="wlb-collapsed-pill" style={posStyle} onClick={toggle}
        title="Show watchlist" aria-label={`Watchlist: ${entries.length} ${unit}`}>
        &#9733; {entries.length}
      </button>
    )
  }
  return (
    <div ref={badgeRef} className="map-watchlist-badge" role="complementary" aria-label="Active watchlist"
      style={posStyle} onMouseDown={onMouseDown} onTouchStart={onTouchStart} title="Drag to reposition; tap a chip to focus">
      <span className="wlb-label">&#9733; {label}</span>
      {entries.map(e => (
        <button key={e.id ?? e.identifier} type="button" className="wlb-chip" title={e.last_event_summary || e.notes || e.identifier}
          onMouseDown={ev => ev.stopPropagation()}
          onTouchStart={ev => ev.stopPropagation()}
          onClick={() => { if (!moved.current) onFocus?.(e) }}>
          {chipLabel ? chipLabel(e) : e.identifier}
        </button>
      ))}
      <button className="wlb-collapse-btn" onClick={(e) => { e.stopPropagation(); toggle() }}
        onMouseDown={ev => ev.stopPropagation()} onTouchStart={ev => ev.stopPropagation()}
        title="Collapse" aria-label="Collapse watchlist">&times;</button>
    </div>
  )
}
