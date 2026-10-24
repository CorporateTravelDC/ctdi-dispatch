/**
 * AircraftList -- sortable list of what the map is drawing (2026-10-24).
 * Desktop: right-hand column. Phone (<= 720 px): bottom sheet, ~40 vh, rows >= 40 px tall.
 * Click/tap a row -> onFocus(aircraft). Collapsed state lives in AircraftMap (it also places the
 * re-open pill over the map); the sort choice persists per device.
 */
import { useMemo, useState, useCallback } from 'react'
import { fmtAge, fmtAlt, displayName, isStale } from './markers.js'

const COLS = [
  { key: 'callsign', label: 'CALLSIGN' },
  { key: 'hex',      label: 'HEX' },
  { key: 'alt',      label: 'ALT' },
  { key: 'gs',       label: 'GS' },
  { key: 'src',      label: 'SRC' },
  { key: 'age',      label: 'AGE' },
]
const SRC_SHORT = { matched: 'R+S', local: 'RCV', swim: 'SWM' }
const SRC_RANK  = { matched: 0, local: 1, swim: 2 }

function sortValue(ac, key) {
  switch (key) {
    case 'callsign': return displayName(ac)
    case 'hex':      return ac.hex || '~'
    case 'alt':      return ac.alt_baro === 'ground' ? -1 : (Number.isFinite(Number(ac.alt_baro)) ? Number(ac.alt_baro) : -2)
    case 'gs':       return Number.isFinite(Number(ac.gs)) ? Number(ac.gs) : -1
    case 'src':      return SRC_RANK[ac.src] ?? 9
    case 'age':      return Number.isFinite(Number(ac.pos_age_s)) ? Number(ac.pos_age_s) : 1e9
    default:         return 0
  }
}

function loadSort() {
  try { const v = localStorage.getItem('acmap_list_sort'); if (v) return JSON.parse(v) } catch {}
  return { key: 'callsign', dir: 1 }
}

export default function AircraftList({ aircraft, selectedKey, keyOf, isTracked, onFocus, onCollapse }) {
  const [sort, setSort] = useState(loadSort)
  const [filter, setFilter] = useState('')

  const onSort = useCallback((key) => setSort(s => {
    const next = s.key === key ? { key, dir: -s.dir } : { key, dir: key === 'alt' || key === 'gs' ? -1 : 1 }
    try { localStorage.setItem('acmap_list_sort', JSON.stringify(next)) } catch {}
    return next
  }), [])

  const rows = useMemo(() => {
    const q = filter.trim().toUpperCase()
    const list = q
      ? aircraft.filter(ac => displayName(ac).toUpperCase().includes(q) || (ac.hex || '').toUpperCase().includes(q) || (ac.r || '').toUpperCase().includes(q))
      : aircraft.slice()
    list.sort((a, b) => {
      const va = sortValue(a, sort.key), vb = sortValue(b, sort.key)
      const c = typeof va === 'string' ? va.localeCompare(vb) : va - vb
      if (c !== 0) return c * sort.dir
      return displayName(a).localeCompare(displayName(b))
    })
    return list
  }, [aircraft, sort, filter])

  return (
    <section className="acls" id="acmap-list" aria-label="Aircraft list">
      <div className="acls-head">
        <span className="acls-title">AIRCRAFT ({aircraft.length})</span>
        <button className="acl-close" onClick={onCollapse} aria-label="Collapse aircraft list" aria-expanded="true">&times;</button>
      </div>
      <input
        className="acls-filter"
        type="search"
        value={filter}
        onChange={e => setFilter(e.target.value)}
        placeholder="Filter callsign / hex / reg"
        aria-label="Filter aircraft list"
        autoComplete="off" autoCorrect="off" autoCapitalize="characters" spellCheck={false}
      />
      <div className="acls-cols" role="row">
        {COLS.map(c => (
          <button key={c.key} className={`acls-col${sort.key === c.key ? ' on' : ''}`} onClick={() => onSort(c.key)}
            aria-sort={sort.key === c.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}
            title={`Sort by ${c.label.toLowerCase()}`}>
            {c.label}{sort.key === c.key ? (sort.dir > 0 ? ' ▴' : ' ▾') : ''}
          </button>
        ))}
      </div>
      <div className="acls-body">
        {rows.length === 0 && <div className="acls-empty">No aircraft in view.</div>}
        {rows.map(ac => {
          const k = keyOf(ac)
          const sel = k === selectedKey
          const tr = isTracked(ac)
          return (
            <button key={k}
              className={`acls-row${sel ? ' sel' : ''}${tr ? ' tracked' : ''}${ac.conflict ? ' conflict' : ''}${isStale(ac) ? ' stale' : ''}`}
              onClick={() => onFocus(ac)}
              aria-pressed={sel}
              title={ac.conflict ? (ac.conflict_note || 'callsign/registration conflict') : undefined}
            >
              <span className="acls-c callsign">{tr ? '★ ' : ''}{displayName(ac)}</span>
              <span className="acls-c hex">{ac.hex || '--'}</span>
              <span className="acls-c alt">{fmtAlt(ac.alt_baro)}</span>
              <span className="acls-c gs">{ac.gs != null ? Math.round(ac.gs) : '--'}</span>
              <span className={`acls-c src src-${ac.src}`}>{SRC_SHORT[ac.src] || ac.src}{ac.conflict ? ' !' : ''}</span>
              <span className="acls-c age">{fmtAge(ac.pos_age_s)}</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
