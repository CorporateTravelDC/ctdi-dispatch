/**
 * AriaCompassRegion — Azimuth-method ARIA live region.
 * Visually-styled status bar + aria-live="polite" region for screen readers.
 * Updates on every data refresh with compass-quadrant plain-text descriptions.
 */
export default function AriaCompassRegion({ summary, entityType = 'items', count = 0, extra = '' }) {
  const prefix = count > 0 ? `${count} ${entityType}. ` : ''
  const fullText = `${prefix}${summary}${extra ? '. ' + extra : ''}`

  return (
    <div
      className="aria-compass-region"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-label={`Compass sector summary: ${fullText}`}
    >
      <span className="aria-compass-label" aria-hidden="true">◎ SECTOR</span>
      <span className="aria-compass-summary" aria-hidden="true">{summary}</span>
      {extra && <span className="aria-compass-extra" aria-hidden="true">{extra}</span>}
    </div>
  )
}
