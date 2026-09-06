/**
 * AccessibleTable — Azimuth-method tabular fallback for every visualization.
 * Visually hidden (sr-only) by default; pass visible={true} to surface on screen.
 *
 * Props:
 *   caption  string                     table description
 *   columns  Array<{key, label, tip?}>  column definitions
 *   rows     Array<object>              data rows
 *   visible  boolean                    show table visually (default: false)
 *   maxRows  number                     truncate to N rows (default: 50)
 *   emptyMsg string
 *   id       string                     for aria-controls wiring
 */
export default function AccessibleTable({
  caption,
  columns  = [],
  rows     = [],
  visible  = false,
  maxRows  = 50,
  emptyMsg = 'No data available.',
  id,
}) {
  const shown = rows.slice(0, maxRows)
  const cls   = ['accessible-table-wrap', visible ? 'table-visible' : 'sr-only'].join(' ')

  return (
    <div className={cls} id={id}>
      <table className="accessible-table" role="table">
        <caption className="table-caption">{caption}</caption>
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col.key} scope="col" title={col.tip}>{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="table-empty">{emptyMsg}</td>
            </tr>
          ) : (
            shown.map((row, i) => (
              <tr key={row.id ?? row.hex ?? row.callsign ?? i}>
                {columns.map(col => (
                  <td key={col.key} data-label={col.label}>{row[col.key] ?? '—'}</td>
                ))}
              </tr>
            ))
          )}
          {rows.length > maxRows && (
            <tr>
              <td colSpan={columns.length} className="table-truncated">
                {rows.length - maxRows} additional rows not shown.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
