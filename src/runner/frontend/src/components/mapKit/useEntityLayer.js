// src/runner/frontend/src/components/mapKit/useEntityLayer.js -- markers + trails + selection (2026-10-24).
//
// The part of aircraftMap/AircraftMap.jsx that is not aviation-specific, as a hook: one Leaflet marker
// per entity (keyed), restyled in place on every poll, tracked (watchlist) entities on their own
// group so they stay on top, a trail per key drawn when it is selected / tracked / "all trails" is on,
// and click-to-focus that flies the map, opens the tooltip and highlights the marker. The vertical
// supplies keyOf / icon / tooltip / isTracked and the trail value+colour; the hook never knows what
// the entity is. Marker click, list row, watchlist chip all end in focus().
import L from 'leaflet'
import { useRef, useState, useCallback } from 'react'
import { TrailsRenderer } from './trails.js'
import { flyTo } from './focus.js'

/**
 * @param {object} opts
 *   getMap      () => L.Map|null
 *   groupsRef   ref -> { main, tracked?, trails? }   (layerGroups created by the view's map init)
 *   keyOf       (item) => string
 *   icon        (item, {tracked, selected}) => L.DivIcon
 *   tooltip     (item, {tracked, selected}) => html string
 *   isTracked   (item) => bool
 *   trails      TrailsStore|null, trailValue (item) => any, trailColor (v, pt) => css colour
 *   allTrails   bool -- draw every trail, not just selected/tracked
 *   focusZoom   number
 *   onSelect    (item|null, key|null) => void   (also fires when a selected item refreshes)
 */
export function useEntityLayer(opts) {
  const o = useRef(opts)
  o.current = opts   // latest closures without re-creating the stable callbacks below

  const markersRef     = useRef(new Map())   // key -> { marker, layer, perm, cls, tracked }
  const rendererRef    = useRef(null)
  const selectedKeyRef = useRef(null)
  const [selectedKey, setSelectedKey] = useState(null)
  const [selected,    setSelected]    = useState(null)

  const renderer = () => {
    const g = o.current.groupsRef.current
    if (!rendererRef.current && g?.trails && o.current.trails) {
      rendererRef.current = new TrailsRenderer(o.current.trails, g.trails, (v, pt) => o.current.trailColor?.(v, pt) ?? '#00d4ff')
    }
    return rendererRef.current
  }
  const wantTrail = (key) => {
    if (o.current.allTrails) return true
    if (key === selectedKeyRef.current) return true
    return !!markersRef.current.get(key)?.tracked
  }

  const applyStyle = useCallback((key, rec) => {
    const item = rec.marker._item
    const tracked  = !!o.current.isTracked?.(item)
    const sel      = key === selectedKeyRef.current
    rec.marker.setIcon(o.current.icon(item, { tracked, selected: sel }))
    rec.marker.setZIndexOffset(sel ? 3000 : tracked ? 2000 : 0)
    const perm = tracked || sel
    const cls  = `ac-tooltip${tracked ? ' tracked-tooltip' : ''}${sel ? ' mk-sel-tooltip' : ''}`
    const html = o.current.tooltip(item, { tracked, selected: sel })
    if (rec.perm !== perm || rec.cls !== cls) {
      rec.marker.unbindTooltip()
      rec.marker.bindTooltip(html, { className: cls, permanent: perm, direction: 'top', interactive: false })
      rec.perm = perm; rec.cls = cls
    } else {
      rec.marker.setTooltipContent(html)
    }
    const g = o.current.groupsRef.current
    const target = (tracked && g.tracked) ? g.tracked : g.main
    if (rec.layer !== target) {
      rec.layer?.removeLayer(rec.marker)
      rec.marker.addTo(target)
      rec.layer = target
    }
    rec.tracked = tracked
  }, [])

  const focus = useCallback((item, { fly = true } = {}) => {
    if (!item) return
    const key  = o.current.keyOf(item)
    const prev = selectedKeyRef.current
    selectedKeyRef.current = key
    setSelectedKey(key); setSelected(item)
    const m = markersRef.current
    const prevRec = prev && prev !== key && m.get(prev)
    if (prevRec) { applyStyle(prev, prevRec); prevRec.marker.closeTooltip() }
    const rec = m.get(key)
    if (rec) { applyStyle(key, rec); rec.marker.openTooltip() }
    renderer()?.sync(wantTrail, [prev, key].filter(Boolean), { emphasis: k => k === key })
    o.current.onSelect?.(item, key)
    if (fly) flyTo(o.current.getMap(), item.lat, item.lon, o.current.focusZoom)
  }, [applyStyle])
  const focusRef = useRef(focus)
  focusRef.current = focus

  const clearSelection = useCallback(() => {
    const prev = selectedKeyRef.current
    selectedKeyRef.current = null
    setSelectedKey(null); setSelected(null)
    const rec = prev && markersRef.current.get(prev)
    if (rec) { applyStyle(prev, rec); rec.marker.closeTooltip() }
    renderer()?.sync(wantTrail, prev ? [prev] : [])
    o.current.onSelect?.(null, null)
  }, [applyStyle])

  /** Focus the live item matching `pred`; false when nothing on the map matches. */
  const focusWhere = useCallback((pred, flyOpts) => {
    for (const [, rec] of markersRef.current) {
      if (pred(rec.marker._item)) { focus(rec.marker._item, flyOpts); return true }
    }
    return false
  }, [focus])

  /** Reconcile markers/trails with a fresh snapshot. Items without a finite lat/lon are skipped. */
  const sync = useCallback((items, now = Date.now()) => {
    const g = o.current.groupsRef.current
    if (!g?.main) return
    const m = markersRef.current
    const seen = new Set()
    const changed = []
    const store = o.current.trails
    for (const item of items || []) {
      const lat = Number(item?.lat), lon = Number(item?.lon)
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue
      const key = o.current.keyOf(item)
      if (!key || seen.has(key)) continue
      seen.add(key)
      let rec = m.get(key)
      if (!rec) {
        const marker = L.marker([lat, lon], { icon: o.current.icon(item, {}), interactive: true, keyboard: false })
        marker._item = item
        marker.on('click', (e) => { L.DomEvent.stopPropagation(e); focusRef.current(marker._item, { fly: false }) })
        rec = { marker, layer: null, perm: null, cls: null, tracked: false }
        m.set(key, rec)
      } else {
        rec.marker._item = item
        rec.marker.setLatLng([lat, lon])
      }
      // push BEFORE styling so an icon can use the store's derived heading / last-moved age
      if (store && store.push(key, lat, lon, o.current.trailValue?.(item), now)) changed.push(key)
      applyStyle(key, rec)
      if (key === selectedKeyRef.current) { setSelected(item); o.current.onSelect?.(item, key) }
    }
    for (const [key, rec] of m) {
      if (seen.has(key)) continue
      rec.layer?.removeLayer(rec.marker)
      m.delete(key)
    }
    if (store) store.prune(now).forEach(k => renderer()?.remove(k))
    renderer()?.sync(wantTrail, changed, { emphasis: k => k === selectedKeyRef.current })
  }, [applyStyle])

  /** Restyle every marker (watchlist membership changed) and redraw trails (allTrails toggled). */
  const restyleAll = useCallback(() => {
    for (const [key, rec] of markersRef.current) applyStyle(key, rec)
    renderer()?.sync(wantTrail, null, { emphasis: k => k === selectedKeyRef.current })
  }, [applyStyle])

  return { sync, focus, focusWhere, clearSelection, restyleAll, selectedKey, selected, selectedKeyRef, markersRef }
}
