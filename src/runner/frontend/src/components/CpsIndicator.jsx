export default function CpsIndicator({ cps }) {
  if (!cps) return <span className="cps-pill unknown">CPS --</span>
  const color = { GREEN: 'go', YELLOW: 'marginal', RED: 'nogo' }[cps.score] || 'unknown'
  return (
    <span className={`cps-pill ${color}`} title={cps.narrative || ''}>
      {cps.score} / {cps.label}
    </span>
  )
}
