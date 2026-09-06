// 2026-08-31: AIS and UTM both silently rendered an empty map with no
// live source configured -- the only indication was a tiny (0.62rem)
// bottom-left stat badge in muted gray, which the operator reported as
// invisible on mobile and in desktop site mode ("not even as a
// watermark"). That badge stays (it's still useful once real data
// starts flowing intermittently), but "no source configured" now also
// gets this prominent, permanent, dead-center watermark -- shown any
// time dataSource === 'none', independent of demo mode (the existing
// demo-only placeholder is a SEPARATE, already-correct thing: it hides
// live data from untrusted public visitors even when a source exists).
// A pointerEvents:none overlay so the map underneath (and its
// accessible table) stay fully usable once this is dismissed by real
// data showing up.
export default function UpcomingFeatureWatermark({ label, detail }) {
  return (
    <div className="upcoming-feature-watermark" role="status" aria-live="polite">
      <div className="ufw-inner">
        <span className="ufw-icon" aria-hidden="true">🚧</span>
        <span className="ufw-label">{label}</span>
        <span className="ufw-sub">Upcoming feature — not yet live</span>
        {detail && <span className="ufw-detail">{detail}</span>}
      </div>
    </div>
  )
}
