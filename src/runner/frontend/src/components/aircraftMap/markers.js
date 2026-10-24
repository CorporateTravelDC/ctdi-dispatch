// src/runner/frontend/src/components/aircraftMap/markers.js -- Leaflet divIcon factory + tooltip HTML (2026-10-24).
//
// One glyph, three SOURCE styles layered on top of the altitude fill (altitudeColor.js), never replacing it:
//   matched  -- receiver AND SWIM: solid fill + thin white outline
//   local    -- receiver only:     solid fill, no outline (tar1090's own look)
//   swim     -- SWIM only:         fill at ~55% opacity + dashed outline in the altitude colour
//   conflict -- any source, callsign/registration disagreement: red ring around the glyph
//   stale    -- pos_age_s > 60: tar1090's lighter/desaturated variant of the same hue
// Watchlist (tracked) and selected states add a CSS glow + size only -- see main.css .aircraft-marker.*
import L from 'leaflet'
import { altitudeColor } from './altitudeColor.js'
import { airlineLogoUrl, FALLBACK_PLANE_SVG } from '../../hooks/useWatchlist.js'

export const STALE_AFTER_S = 60

const GLYPH = '7,0 14,20 7,15 0,20'

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

export function isStale(ac) {
  return Number(ac?.pos_age_s) > STALE_AFTER_S
}

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

export function fmtAlt(alt) {
  if (alt === 'ground') return 'GND'
  if (alt == null || !Number.isFinite(Number(alt))) return '--'
  return `${Math.round(Number(alt)).toLocaleString()}ft`
}

export const SRC_LABEL = { matched: 'Receiver + SWIM', local: 'Receiver', swim: 'SWIM' }

/**
 * Build the marker icon.
 * @param {object} ac       one /api/map/aircraft entry
 * @param {object} state    { tracked, selected }
 */
export function aircraftIcon(ac, { tracked = false, selected = false } = {}) {
  const stale = isStale(ac)
  const fill  = altitudeColor(ac.alt_baro, { stale, selected })
  const src   = ac.src || 'local'
  // 2026-10-24: SWIM (FDPS) carries no ground track, so a SWIM-only aircraft has track null. Drawing it at
  // rotation 0 would claim it flies north; it gets a heading-less round glyph instead.
  const hasTrack = ac.track !== null && ac.track !== undefined && ac.track !== '' && Number.isFinite(Number(ac.track))
  const track = hasTrack ? Number(ac.track) : 0

  let stroke = 'none', strokeW = 0, dash = '', fillOp = stale ? 0.85 : 0.95
  if (src === 'matched')   { stroke = '#ffffff'; strokeW = 1.2 }
  else if (src === 'swim') { stroke = fill; strokeW = 1.2; dash = 'stroke-dasharray="2.2 1.6"'; fillOp = 0.55 }

  const big  = tracked || selected
  const w = big ? 18 : 14, h = big ? 26 : 20
  const ring = ac.conflict
    ? `<circle cx="7" cy="10" r="10" fill="none" stroke="#ff3040" stroke-width="1.6" opacity="0.95"/>`
    : ''
  const cls = ['aircraft-marker', `src-${src}`, stale ? 'stale' : '', tracked ? 'tracked' : '', selected ? 'selected' : '', ac.conflict ? 'conflict' : '']
    .filter(Boolean).join(' ')

  // The ring is drawn in an un-rotated outer SVG so its stroke never looks "turned"; the glyph rotates
  // inside it by ground track.
  const html = `<div class="${cls}" style="width:${w}px;height:${h}px">
    <svg width="${w}" height="${h}" viewBox="-4 -3 22 26" overflow="visible">
      ${ring}
      ${hasTrack
        ? `<g transform="rotate(${track.toFixed(0)} 7 10)">
        <polygon points="${GLYPH}" fill="${fill}" fill-opacity="${fillOp}"
          stroke="${stroke}" stroke-width="${strokeW}" ${dash} stroke-linejoin="round"/>
      </g>`
        : `<circle cx="7" cy="10" r="${big ? 6 : 5}" fill="${fill}" fill-opacity="${fillOp}"
          stroke="${stroke === 'none' ? fill : stroke}" stroke-width="${strokeW || 1}" ${dash}/>`}
    </svg></div>`
  return L.divIcon({ className: '', html, iconSize: [w, h], iconAnchor: [w / 2, h / 2] })
}

/** Grey "last seen" marker for a /api/map/focus live:false result. */
export function lastSeenIcon(track) {
  const t = Number.isFinite(Number(track)) ? Number(track) : 0
  const html = `<div class="aircraft-marker last-seen" style="width:18px;height:26px">
    <svg width="18" height="26" viewBox="-4 -3 22 26" overflow="visible">
      <g transform="rotate(${t.toFixed(0)} 7 10)">
        <polygon points="${GLYPH}" fill="#8a9bb0" fill-opacity="0.6" stroke="#ddeeff" stroke-width="1" stroke-dasharray="2 2"/>
      </g>
    </svg></div>`
  return L.divIcon({ className: '', html, iconSize: [18, 26], iconAnchor: [9, 13] })
}

export function displayName(ac) {
  return (ac?.flight || '').trim() || (ac?.r || '').trim() || (ac?.hex || '').toUpperCase() || '?'
}

/** Tooltip body shared by every source; tracked aircraft get the logo header on top. */
export function tooltipHtml(ac, { tracked = false } = {}) {
  const name  = displayName(ac)
  const stale = isStale(ac)
  const srcTxt = SRC_LABEL[ac.src] || esc(ac.src || '?')
  const rows = []
  rows.push(`<span class="act-src act-src-${esc(ac.src || 'local')}">${srcTxt}${stale ? ' &middot; STALE' : ''}</span>`)
  rows.push(`${fmtAlt(ac.alt_baro)} &middot; ${ac.gs != null ? Math.round(ac.gs) + 'kt' : '--'} &middot; ${ac.track != null ? Math.round(ac.track) + '&deg;' : '--'}`)
  const ident = [ac.hex ? `hex ${esc(ac.hex)}` : null, ac.r ? `reg ${esc(ac.r)}` : null, ac.t ? esc(ac.t) : null].filter(Boolean).join(' &middot; ')
  if (ident) rows.push(ident)
  if (ac.origin || ac.destination) rows.push(`${esc(ac.origin || '????')} &rarr; ${esc(ac.destination || '????')}`)
  if (ac.src === 'swim' || stale) rows.push(`pos age ${fmtAge(ac.pos_age_s)}`)
  if (ac.conflict) rows.push(`<span class="act-conflict">CONFLICT: ${esc(ac.conflict_note || 'callsign/registration disagree (SWIM is tail authority)')}</span>`)

  if (!tracked) {
    return `<div class="act"><b class="act-name">${esc(name)}</b><br/>${rows.join('<br/>')}</div>`
  }
  const logoUrl = airlineLogoUrl(name)
  const logoTag = logoUrl
    ? `<img src="${logoUrl}" class="ac-logo" alt="${esc(name.slice(0, 3))} logo"
            onerror="this.src='${FALLBACK_PLANE_SVG}';this.classList.add('ac-logo-fallback')" />`
    : `<img src="${FALLBACK_PLANE_SVG}" class="ac-logo ac-logo-fallback" alt="aircraft" />`
  return `<div class="ac-tooltip-tracked">
    ${logoTag}
    <div class="ac-tooltip-tracked-info">
      <span class="ac-tracked-badge">&#9733; TRACKED</span>
      <b class="ac-tracked-callsign">${esc(name)}</b>
      <span class="ac-tracked-details">${rows.join('<br/>')}</span>
    </div>
  </div>`
}
