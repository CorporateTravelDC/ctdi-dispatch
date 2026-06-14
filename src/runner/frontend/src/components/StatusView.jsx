export default function StatusView({ liveState }) {
  const cps = liveState?.cps

  const factorColor = v =>
    v === 'ok' ? '#39ff14' : v === 'marginal' ? '#ffd700' : '#ff3131'

  return (
    <div className="panel-view">
      <h2>CPS Status</h2>

      <section className="status-section">
        <h3>Critical Predictability State</h3>
        {cps ? (
          <div className="cps-card">
            <div className={`cps-score-large ${cps.score?.toLowerCase()}`}>
              {cps.score} / {cps.label}
            </div>
            <p className="cps-narrative">{cps.narrative}</p>
            {cps.factors && (
              <div className="cps-factors">
                {Object.entries(cps.factors).map(([k, v]) => (
                  <span key={k} className="factor-chip"
                        style={{ borderColor: factorColor(v) }}>
                    {k}: {v}
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : <p className="muted">CPS data unavailable</p>}
      </section>
    </div>
  )
}
