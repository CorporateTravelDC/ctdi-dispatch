/**
 * MapLegend -- generic collapsible legend (2026-10-24), extracted from aircraftMap/Legend.jsx.
 * Collapsed by default on a phone; state persists per device under `storageKey`.
 * sections: [{ title, rows: [{ swatch: ReactNode, text: ReactNode }], bar?: { css, labels: [[pct, text]] } }]
 */
import { useState, useCallback } from 'react'
import { loadBool, saveBool, isPhone } from './format.js'

/** Small inline SVG swatch: square | dot | arrow | line | pill(text). */
export function Swatch({ color = '#888', shape = 'square', dash = false, glow = false, stroke = '#111', text = '' }) {
  const sd = dash ? '2 1.5' : undefined
  const style = glow ? { filter: `drop-shadow(0 0 3px ${color})` } : undefined
  let body
  switch (shape) {
    case 'dot':   body = <circle cx="9" cy="9" r="5" fill={color} stroke={stroke} strokeWidth="1.2" strokeDasharray={sd} />; break
    case 'arrow': body = <polygon points="9,2 14,14 9,11 4,14" fill={color} stroke={stroke} strokeWidth="1.2" strokeDasharray={sd} strokeLinejoin="round" />; break
    case 'line':  body = <line x1="2" y1="9" x2="16" y2="9" stroke={color} strokeWidth="3" strokeLinecap="round" strokeDasharray={sd} />; break
    case 'pill':  body = <><rect x="1" y="4" width="16" height="10" rx="3" fill={color} stroke={stroke} strokeWidth="1" strokeDasharray={sd} /><text x="9" y="11.5" textAnchor="middle" fontSize="6.5" fontWeight="700" fill="#0a0e14">{text}</text></>; break
    default:      body = <rect x="3" y="3" width="12" height="12" rx="2" fill={color} stroke={stroke} strokeWidth="1" strokeDasharray={sd} />
  }
  return <svg className="mk-swatch" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" style={style}>{body}</svg>
}

export default function MapLegend({ id = 'mk-legend', storageKey = 'mk_legend_collapsed', title = 'LEGEND', sections = [], credit }) {
  const [collapsed, setCollapsed] = useState(() => loadBool(storageKey, isPhone))
  const toggle = useCallback(() => setCollapsed(c => { saveBool(storageKey, !c); return !c }), [storageKey])

  if (collapsed) {
    return (
      <button className="mk-pill" onClick={toggle} aria-expanded="false" aria-controls={id} title="Show legend">
        {title}
      </button>
    )
  }
  return (
    <div className="mk-legend" id={id} role="region" aria-label="Map legend">
      <div className="mk-legend-head">
        <span className="mk-legend-title">{title}</span>
        <button className="mk-close" onClick={toggle} aria-label="Collapse legend" aria-expanded="true">&times;</button>
      </div>
      {sections.map((s, i) => (
        <div className="mk-legend-sec" key={i}>
          {s.title && <div className="mk-legend-sec-title">{s.title}</div>}
          {s.bar && (
            <>
              <div className="mk-legend-bar" style={{ background: s.bar.css }} aria-hidden="true" />
              <div className="mk-legend-bar-labels" aria-hidden="true">
                {s.bar.labels.map(([pct, text]) => <span key={text} style={{ left: `${pct}%` }}>{text}</span>)}
              </div>
            </>
          )}
          {(s.rows || []).map((r, j) => (
            <div className="mk-legend-row" key={j}>{r.swatch}<span>{r.text}</span></div>
          ))}
        </div>
      ))}
      {credit && <div className="mk-legend-credit">{credit}</div>}
    </div>
  )
}
