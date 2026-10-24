/**
 * Legend -- altitude ramp + source styles + conflict ring, collapsible (2026-10-24).
 * Collapsed by default on a phone (the runner is used on one); state persists per device.
 */
import { useState, useCallback } from 'react'
import { altitudeGradientCss, LEGEND_STOPS, GROUND_COLOR, UNKNOWN_COLOR, altitudeColor, ALTITUDE_RAMP_CREDIT } from './altitudeColor.js'

const KEY = 'acmap_legend_collapsed'
const SAMPLE_ALT = 9000  // a mid-ramp green so the three source glyphs read as "same altitude, different source"

function Glyph({ src, conflict = false, stale = false }) {
  const fill = altitudeColor(SAMPLE_ALT, { stale })
  let stroke = 'none', sw = 0, dash = null, op = 0.95
  if (src === 'matched') { stroke = '#ffffff'; sw = 1.2 }
  if (src === 'swim')    { stroke = fill; sw = 1.2; dash = '2.2 1.6'; op = 0.55 }
  return (
    <svg width="22" height="26" viewBox="-4 -3 22 26" aria-hidden="true" className="acl-glyph">
      {conflict && <circle cx="7" cy="10" r="10" fill="none" stroke="#ff3040" strokeWidth="1.6" />}
      <polygon points="7,0 14,20 7,15 0,20" fill={fill} fillOpacity={op} stroke={stroke} strokeWidth={sw}
        strokeDasharray={dash || undefined} strokeLinejoin="round" />
    </svg>
  )
}

export default function Legend() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const v = localStorage.getItem(KEY)
      if (v === '1') return true
      if (v === '0') return false
    } catch {}
    return typeof window !== 'undefined' && window.innerWidth < 720
  })
  const toggle = useCallback(() => {
    setCollapsed(c => { try { localStorage.setItem(KEY, c ? '0' : '1') } catch {} return !c })
  }, [])

  if (collapsed) {
    return (
      <button className="acl-pill" onClick={toggle} aria-expanded="false" aria-controls="acmap-legend" title="Show legend">
        LEGEND
      </button>
    )
  }

  return (
    <div className="acl" id="acmap-legend" role="region" aria-label="Map legend">
      <div className="acl-head">
        <span className="acl-title">LEGEND</span>
        <button className="acl-close" onClick={toggle} aria-label="Collapse legend" aria-expanded="true">&times;</button>
      </div>
      <div className="acl-sec">
        <div className="acl-sec-title">Fill = altitude</div>
        <div className="acl-bar" style={{ background: altitudeGradientCss(40000) }} aria-hidden="true" />
        <div className="acl-bar-labels" aria-hidden="true">
          {LEGEND_STOPS.filter(s => [0, 4000, 8000, 10000, 20000, 30000, 40000].includes(s.alt)).map(s => (
            <span key={s.alt} style={{ left: `${(s.alt / 40000 * 100).toFixed(1)}%` }}>{s.label}</span>
          ))}
        </div>
        <ul className="acl-list sr-only">
          {LEGEND_STOPS.map(s => <li key={s.alt}>{s.alt} ft: {s.color}</li>)}
        </ul>
        <div className="acl-row"><span className="acl-swatch" style={{ background: GROUND_COLOR }} /> ground</div>
        <div className="acl-row"><span className="acl-swatch" style={{ background: UNKNOWN_COLOR }} /> altitude unknown</div>
      </div>
      <div className="acl-sec">
        <div className="acl-sec-title">Outline = source</div>
        <div className="acl-row"><Glyph src="matched" /> <span><b>Receiver + SWIM</b> solid, white outline</span></div>
        <div className="acl-row"><Glyph src="local" /> <span><b>Receiver only</b> solid, no outline</span></div>
        <div className="acl-row"><Glyph src="swim" /> <span><b>SWIM only</b> faded, dashed outline</span></div>
        <div className="acl-row"><Glyph src="matched" conflict /> <span><b>Conflict</b> red ring: callsign/reg disagree</span></div>
        <div className="acl-row"><Glyph src="local" stale /> <span><b>Stale</b> lighter: position &gt; 60 s old</span></div>
        <div className="acl-row"><span className="acl-swatch acl-swatch-tracked" aria-hidden="true" /> <span><b>Watchlist</b> cyan glow, larger</span></div>
      </div>
      <div className="acl-credit">{ALTITUDE_RAMP_CREDIT}</div>
    </div>
  )
}
