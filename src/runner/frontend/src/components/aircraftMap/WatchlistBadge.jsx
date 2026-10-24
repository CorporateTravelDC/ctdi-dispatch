/**
 * WatchlistBadge -- draggable "WATCHING" chip strip over the map (moved out of MapView.jsx 2026-10-24).
 * Chips are now buttons: tap one -> onFocus(identifier), which resolves via the live list or
 * /api/map/focus?ident= (see AircraftMap.focusIdent). Position/collapsed state persist per device.
 */
import { useState, useRef, useCallback } from 'react'

export default function WatchlistBadge({ entries, onFocus }) {
  const flights = entries.filter(e => e.entry_type === 'flight')
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('gwb_collapsed') === '1')
  const [pos, setPos] = useState(() => {
    try { return JSON.parse(localStorage.getItem('gwb_pos')) || { x: null, y: null } }
    catch { return { x: null, y: null } }
  })
  const dragging  = useRef(false)
  const moved     = useRef(false)
  const origin    = useRef(null)
  const startPos  = useRef(null)
  const badgeRef  = useRef(null)

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
    setPos(p => { try { localStorage.setItem('gwb_pos', JSON.stringify(p)) } catch {} return p })
  }, [])

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
    setCollapsed(c => {
      const next = !c
      try { localStorage.setItem('gwb_collapsed', next ? '1' : '0') } catch {}
      return next
    })
  }, [])

  if (!flights.length) return null

  const posStyle = pos.x !== null ? { position: 'fixed', left: pos.x, top: pos.y, right: 'unset' } : {}

  if (collapsed) {
    return (
      <button className="wlb-collapsed-pill" style={posStyle} onClick={toggle}
        title="Show watchlist" aria-label={`Watchlist: ${flights.length} aircraft`}>
        &#9733; {flights.length}
      </button>
    )
  }

  return (
    <div ref={badgeRef} className="map-watchlist-badge" role="complementary" aria-label="Active watchlist"
      style={posStyle} onMouseDown={onMouseDown} onTouchStart={onTouchStart} title="Drag to reposition; tap a chip to focus">
      <span className="wlb-label">&#9733; WATCHING</span>
      {flights.map(e => (
        <button key={e.id} type="button" className="wlb-chip" title={e.last_event_summary || e.identifier}
          onMouseDown={ev => ev.stopPropagation()}
          onTouchStart={ev => ev.stopPropagation()}
          onClick={() => { if (!moved.current) onFocus?.(e.identifier) }}>
          {e.identifier}
        </button>
      ))}
      <button className="wlb-collapse-btn" onClick={(e) => { e.stopPropagation(); toggle() }}
        onMouseDown={ev => ev.stopPropagation()} onTouchStart={ev => ev.stopPropagation()}
        title="Collapse" aria-label="Collapse watchlist">&times;</button>
    </div>
  )
}
