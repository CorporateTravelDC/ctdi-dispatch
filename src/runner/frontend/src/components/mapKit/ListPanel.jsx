/**
 * ListPanel -- generic sortable list of what a map is drawing (2026-10-24).
 * Extracted copy of aircraftMap/AircraftList.jsx with the columns supplied by the vertical.
 * Desktop: right-hand column. Phone (<= 720 px): bottom sheet, rows >= 40 px tall.
 * Tap a row -> onFocus(item). The sort choice persists per device under `storageKey`.
 *
 * columns: [{ key, label, get(item) -> display, sort(item) -> string|number, className?, numericDesc? }]
 *   numericDesc: first click sorts descending (speeds, altitudes, delays).
 * rowClass(item) -> extra classes ("stale", "conflict"...) for the row.
 */
import { useMemo, useState, useCallback } from 'react'

function loadSort(storageKey, dflt) {
  try { const v = localStorage.getItem(storageKey); if (v) return JSON.parse(v) } catch {}
  return dflt
}

export default function ListPanel({
  id, title, items, columns, keyOf, selectedKey, isTracked, onFocus, onCollapse,
  storageKey = 'mk_list_sort', defaultSort, filterText, filterPlaceholder = 'Filter', emptyMsg = 'Nothing in view.',
  rowClass, canFocus,
}) {
  const [sort, setSort] = useState(() => loadSort(storageKey, defaultSort || { key: columns[0].key, dir: 1 }))
  const [filter, setFilter] = useState('')

  const onSort = useCallback((key) => setSort(s => {
    const col = columns.find(c => c.key === key)
    const next = s.key === key ? { key, dir: -s.dir } : { key, dir: col?.numericDesc ? -1 : 1 }
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch {}
    return next
  }), [columns, storageKey])

  const rows = useMemo(() => {
    const q = filter.trim().toUpperCase()
    const list = (q && filterText)
      ? items.filter(it => String(filterText(it) || '').toUpperCase().includes(q))
      : items.slice()
    const col = columns.find(c => c.key === sort.key) || columns[0]
    const sv = it => col.sort ? col.sort(it) : col.get(it)
    list.sort((a, b) => {
      const va = sv(a), vb = sv(b)
      const c = (typeof va === 'string' || typeof vb === 'string')
        ? String(va ?? '').localeCompare(String(vb ?? ''))
        : (Number(va ?? -Infinity) - Number(vb ?? -Infinity))
      if (c !== 0) return c * sort.dir
      return String(keyOf(a)).localeCompare(String(keyOf(b)))
    })
    return list
  }, [items, sort, filter, columns, filterText, keyOf])

  const template = columns.map(c => c.width || '1fr').join(' ')

  return (
    <section className="mk-list" id={id} aria-label={title} style={{ '--mk-cols': template }}>
      <div className="mk-list-head">
        <span className="mk-list-title">{title} ({items.length})</span>
        <button className="mk-close" onClick={onCollapse} aria-label={`Collapse ${title.toLowerCase()} list`} aria-expanded="true">&times;</button>
      </div>
      {filterText && (
        <input
          className="mk-list-filter"
          type="search"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder={filterPlaceholder}
          aria-label={`Filter ${title.toLowerCase()} list`}
          autoComplete="off" autoCorrect="off" autoCapitalize="characters" spellCheck={false}
        />
      )}
      <div className="mk-list-cols" role="row">
        {columns.map(c => (
          <button key={c.key} className={`mk-list-col${sort.key === c.key ? ' on' : ''}`} onClick={() => onSort(c.key)}
            aria-sort={sort.key === c.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}
            title={`Sort by ${c.label.toLowerCase()}`}>
            {c.label}{sort.key === c.key ? (sort.dir > 0 ? ' ▴' : ' ▾') : ''}
          </button>
        ))}
      </div>
      <div className="mk-list-body">
        {rows.length === 0 && <div className="mk-list-empty">{emptyMsg}</div>}
        {rows.map(it => {
          const k = keyOf(it)
          const sel = k === selectedKey
          const tr = !!isTracked?.(it)
          const focusable = canFocus ? canFocus(it) : true
          return (
            <button key={k}
              className={`mk-row${sel ? ' sel' : ''}${tr ? ' tracked' : ''}${focusable ? '' : ' nofocus'} ${rowClass?.(it) || ''}`}
              onClick={() => focusable && onFocus(it)}
              aria-pressed={sel}
              disabled={!focusable}
              title={focusable ? undefined : 'No position for this entry'}
            >
              {columns.map((c, i) => (
                <span key={c.key} className={`mk-c ${c.className || ''}${i === 0 ? ' mk-c-name' : ''}`}>
                  {i === 0 && tr ? '★ ' : ''}{c.get(it)}
                </span>
              ))}
            </button>
          )
        })}
      </div>
    </section>
  )
}
