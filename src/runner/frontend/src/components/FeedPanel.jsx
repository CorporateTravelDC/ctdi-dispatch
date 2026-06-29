/**
 * FeedPanel — collapsible per-feed panel with full ARIA support.
 *
 * - aria-expanded header button controlling body region
 * - aria-labelledby linking header to content
 * - Keyboard: Enter/Space toggle; standard tab/focus management
 * - Optional badge (count / status)
 * - Slot for AccessibleTable alongside visual content
 *
 * Props:
 *   id             string     unique panel id (required for ARIA wiring)
 *   title          string     panel heading
 *   children       ReactNode  visual content
 *   defaultOpen    boolean    initial state (default: true)
 *   badge          string|number  badge text (null to omit)
 *   badgeVariant   'go'|'nogo'|'warn'|'cyan'|'muted'
 *   accessibleTable ReactNode  AccessibleTable instance
 *   className      string
 */
import { useState } from 'react'

export default function FeedPanel({
  id,
  title,
  children,
  defaultOpen   = true,
  badge         = null,
  badgeVariant  = 'muted',
  accessibleTable,
  className     = '',
}) {
  const [open, setOpen] = useState(defaultOpen)
  const headingId = `${id}-heading`
  const bodyId    = `${id}-body`

  return (
    <section
      className={['feed-panel', open ? 'fp-open' : 'fp-closed', className].filter(Boolean).join(' ')}
      aria-labelledby={headingId}
    >
      <h3 className="fp-header" id={headingId}>
        <button
          type="button"
          className="fp-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen(o => !o)}
        >
          <span className="fp-chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
          <span className="fp-title">{title}</span>
          {badge != null && (
            <span className={`fp-badge fp-badge-${badgeVariant}`} aria-label={`${badge} items`}>
              {badge}
            </span>
          )}
        </button>
      </h3>
      <div id={bodyId} className="fp-body" role="region" aria-labelledby={headingId} hidden={!open}>
        <div className="fp-content">{children}</div>
        {accessibleTable && <div className="fp-table-slot">{accessibleTable}</div>}
      </div>
    </section>
  )
}
