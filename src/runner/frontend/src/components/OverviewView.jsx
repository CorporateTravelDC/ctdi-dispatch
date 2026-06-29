import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import FeedPanel from './FeedPanel.jsx'
import AccessibleTable from './AccessibleTable.jsx'
import { useGlobalLayerConfig } from '../App.jsx'

/** Format a brief excerpt — first meaningful line, up to maxLen chars */
function briefExcerpt(text, maxLen = 220) {
  if (!text) return null
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  if (!lines.length) return null
  const first = lines.find(l => !l.startsWith('=') && l.length > 20) || lines[0]
  return first.length > maxLen ? first.slice(0, maxLen) + '…' : first
}

function MetricCard({ label, value, status, sub, href }) {
  const cls = status === 'go'       ? 'ov-card go'
            : status === 'marginal' ? 'ov-card marginal'
            : status === 'nogo'     ? 'ov-card nogo'
            : status === 'warn'     ? 'ov-card warn'
            : 'ov-card'
  const inner = (
    <div className={cls}>
      <div className="ov-card-label">{label}{href && <span className="ov-card-arrow">→</span>}</div>
      <div className="ov-card-value">{value ?? '—'}</div>
      {sub && <div className="ov-card-sub">{sub}</div>}
    </div>
  )
  return href
    ? <Link to={href} className="ov-card-link" title={`View ${label}`}>{inner}</Link>
    : inner
}

function AlertBadge({ alerts }) {
  if (!alerts?.length) return (
    <div className="ov-alert-row muted">No active NWS alerts in area.</div>
  )
  return (
    <div className="ov-alerts">
      {alerts.slice(0, 4).map((a, i) => (
        <div key={i} className={`ov-alert-badge sev-${(a.properties?.severity || 'minor').toLowerCase()}`}>
          <span className="ov-alert-event">{a.properties?.event || 'Alert'}</span>
          <span className="ov-alert-area">{(a.properties?.areaDesc || '').split(';')[0]}</span>
        </div>
      ))}
      {alerts.length > 4 && (
        <Link to="/signals#meteorology" className="ov-alert-more">+{alerts.length - 4} more →</Link>
      )}
    </div>
  )
}

function FeedSummary({ feeds }) {
  if (!feeds) return <span className="muted">loading…</span>
  const rows = Array.isArray(feeds) ? feeds : (feeds?.feeds ?? Object.values(feeds))
  const total = rows.length
  const stale = rows.filter(f => {
    const age = f.age_seconds ?? null
    const thresh = f.stale_threshold_seconds || 900
    const covered = !!f.push_covered
    const hasErr = f.error && !f.error.startsWith('pending_credentials')
    return !covered && (age === null || age > thresh) && !hasErr
  }).length
  const errored = rows.filter(f => f.error && !f.error.startsWith('pending_credentials')).length
  const cls = errored > 2 ? 'nogo' : stale > 2 ? 'warn' : 'go'
  const label = errored > 2 ? `${errored} feeds errored`
              : stale > 2   ? `${stale} feeds stale`
              : `All ${total} feeds nominal`
  return <span className={`ov-feed-status ${cls}`}>{label}</span>
}

// ── Per-feed panel content components ─────────────────────────────────────

function FlightsPanel({ liveState }) {
  const [local, setLocal] = useState(null)
  useEffect(() => {
    fetch('/api/adsb/local').then(r => r.json()).then(d => {
      const ac = d.aircraft || d.ac || []
      setLocal(ac.filter(a => a.lat && a.lon && !a.ground))
    }).catch(() => setLocal([]))
  }, [liveState])
  const count = local?.length ?? '…'
  return (
    <div className="fp-stat-row">
      <span className="fp-stat-value cyan">{count}</span>
      <span className="fp-stat-label">aircraft in range</span>
      <Link to="/map" className="fp-panel-link">ADS-B view →</Link>
    </div>
  )
}

function TrainsPanel({ amtrak }) {
  const trains = amtrak?.trains ?? (Array.isArray(amtrak) ? amtrak : [])
  const delayed = trains.filter(t => (t.delay_minutes ?? 0) > 5)
  const tblRows = trains.slice(0, 10).map(t => ({
    num:   t.train_number || t.id || '—',
    route: t.route_name  || '—',
    delay: t.delay_minutes > 0 ? `+${t.delay_minutes}m` : 'On time',
  }))
  return (
    <>
      <div className="fp-stat-row">
        <span className={`fp-stat-value ${delayed.length > 0 ? 'warn' : 'go'}`}>
          {trains.length === 0 ? '—' : delayed.length > 0 ? `${delayed.length} delayed` : 'On time'}
        </span>
        <span className="fp-stat-label">{trains.length} trains tracked</span>
        <Link to="/trains" className="fp-panel-link">EOTD view →</Link>
      </div>
      {delayed.length > 0 && (
        <div className="fp-delayed-list">
          {delayed.slice(0, 3).map((t, i) => (
            <span key={i} className="fp-delay-chip">
              {t.train_number || t.id}: +{t.delay_minutes}m
            </span>
          ))}
        </div>
      )}
      <AccessibleTable
        caption="NEC trains at Washington Union Station"
        columns={[
          { key: 'num',   label: 'Train #' },
          { key: 'route', label: 'Route'   },
          { key: 'delay', label: 'Status'  },
        ]}
        rows={tblRows}
        emptyMsg="No train data available."
      />
    </>
  )
}

function MarinePanel() {
  const [vessels, setVessels] = useState(null)
  useEffect(() => {
    fetch('/api/ais/vessels').then(r => r.json()).then(d => {
      setVessels(Array.isArray(d) ? d : (d.vessels ?? []))
    }).catch(() => setVessels([]))
  }, [])
  const count = vessels?.length ?? null
  const degraded = vessels !== null && count === 0
  return (
    <div className="fp-stat-row">
      {count === null
        ? <span className="fp-stat-value muted">…</span>
        : degraded
          ? <span className="fp-stat-value muted">LOCAL DECODER OFFLINE</span>
          : <span className="fp-stat-value cyan">{count}</span>}
      {!degraded && count !== null && <span className="fp-stat-label">vessels in range</span>}
      <Link to="/ais" className="fp-panel-link">AIS view →</Link>
    </div>
  )
}

function WeatherPanel({ alerts, liveState }) {
  const [metar, setMetar] = useState(null)
  useEffect(() => {
    fetch('/api/dispatch/api/v1/weather').then(r => r.json()).then(d => {
      const stations = Array.isArray(d) ? d : (d.stations ?? d.metar ?? [])
      setMetar(stations[0] ?? null)
    }).catch(() => {})
  }, [liveState])
  const alertCount = alerts?.length ?? 0
  const wx = metar
  return (
    <div className="fp-weather-body">
      {wx ? (
        <div className="fp-metar-row">
          <span className="fp-metar-station">{wx.station_id || wx.station || 'KDCA'}</span>
          <span className="fp-metar-val">{wx.flight_category || wx.category || '—'}</span>
          <span className="fp-metar-detail">
            {wx.wind_speed_kt != null ? `${wx.wind_speed_kt}kt` : ''}
            {wx.visibility_statute_mi != null ? ` · ${wx.visibility_statute_mi}sm` : ''}
          </span>
        </div>
      ) : (
        <span className="muted" style={{fontSize:'0.7rem'}}>METAR loading…</span>
      )}
      <div className="fp-stat-row" style={{marginTop:'0.4rem'}}>
        <span className={`fp-stat-value ${alertCount > 0 ? 'warn' : 'go'}`}>
          {alertCount > 0 ? `${alertCount} NWS alert${alertCount !== 1 ? 's' : ''}` : 'Area clear'}
        </span>
        <Link to="/signals#meteorology" className="fp-panel-link">Details →</Link>
      </div>
    </div>
  )
}

function TFRPanel({ tfrs }) {
  const active = Array.isArray(tfrs) ? tfrs.filter(t => t.type !== 'error') : []
  const vip    = active.filter(t => /POTUS|VIP|MOVEMENT|Marine One/i.test(t.notam_text || t.tfr_id || ''))
  const tblRows = active.slice(0, 8).map(t => ({
    id:    t.tfr_id || '—',
    type:  t.is_vip ? 'VIP' : 'Standard',
    rad:   t.radius_nm ? `${t.radius_nm}nm` : '—',
  }))
  return (
    <>
      <div className="fp-stat-row">
        <span className={`fp-stat-value ${vip.length > 0 ? 'nogo' : active.length > 0 ? 'warn' : 'go'}`}>
          {active.length === 0 ? 'None' : vip.length > 0 ? `${vip.length} VIP` : active.length}
        </span>
        <span className="fp-stat-label">
          {active.length === 0 ? 'No active TFRs' : `active TFR${active.length !== 1 ? 's' : ''}`}
        </span>
        <Link to="/signals" className="fp-panel-link">TFR list →</Link>
      </div>
      {vip.length > 0 && (
        <div className="fp-vip-list">
          {vip.map((t, i) => (
            <span key={i} className="fp-vip-chip">{t.tfr_id}</span>
          ))}
        </div>
      )}
      <AccessibleTable
        caption="Active TFRs"
        columns={[
          { key: 'id',   label: 'TFR ID' },
          { key: 'type', label: 'Type'   },
          { key: 'rad',  label: 'Radius' },
        ]}
        rows={tblRows}
        emptyMsg="No active TFRs."
      />
    </>
  )
}

function SignalsPanel({ liveState }) {
  const [counts, setCounts] = useState(null)
  const load = useCallback(() => {
    const since = Math.floor(Date.now() / 1000) - 3600
    Promise.all([
      fetch(`/api/vdl2/messages?limit=1&since=${since}`).then(r => r.json()).catch(() => ({ messages: [] })),
      fetch(`/api/acars/messages?limit=1&since=${since}`).then(r => r.json()).catch(() => ({ messages: [] })),
    ]).then(([v, a]) => setCounts({
      vdl2:  v.total_count ?? v.messages?.length ?? 0,
      acars: a.total_count ?? a.messages?.length ?? 0,
    }))
  }, [])
  useEffect(() => { load() }, [liveState, load])
  return (
    <div className="fp-signals-body">
      <div className="fp-sig-row">
        <span className="fp-sig-label">VDL2</span>
        <span className="fp-sig-count">{counts?.vdl2 ?? '…'}</span>
        <span className="fp-sig-unit">msgs / hr</span>
      </div>
      <div className="fp-sig-row">
        <span className="fp-sig-label">ACARS</span>
        <span className="fp-sig-count">{counts?.acars ?? '…'}</span>
        <span className="fp-sig-unit">msgs / hr</span>
      </div>
      <Link to="/signals" className="fp-panel-link" style={{marginTop:'0.4rem',display:'block'}}>Signal viewer →</Link>
    </div>
  )
}

// ── Main OverviewView ──────────────────────────────────────────────────────
export default function OverviewView({ liveState }) {
  const { config } = useGlobalLayerConfig() ?? {}
  const panels = config?.panels ?? {}

  const [brief,   setBrief]   = useState(null)
  const [alerts,  setAlerts]  = useState(null)
  const [tfrs,    setTfrs]    = useState(null)
  const [amtrak,  setAmtrak]  = useState(null)
  const [feeds,   setFeeds]   = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetch('/api/dispatch/api/v1/brief').then(r => r.ok ? r.text() : null).catch(() => null),
      fetch('/api/dispatch/api/v1/alerts').then(r => r.json()).catch(() => null),
      fetch('/api/dispatch/api/v1/tfr').then(r => r.json()).catch(() => null),
      fetch('/api/dispatch/api/v1/amtrak').then(r => r.json()).catch(() => null),
      fetch('/api/dispatch/api/v1/feeds').then(r => r.json()).catch(() => null),
    ]).then(([b, a, t, am, f]) => {
      setBrief(b)
      setAlerts(Array.isArray(a) ? a : (a?.features ?? a?.alerts ?? []))
      setTfrs(Array.isArray(t) ? t : (t?.tfrs ?? []))
      setAmtrak(am)
      setFeeds(f)
      setLoading(false)
    })
  }, [liveState?.healthz?.status])

  const cps = liveState?.cps
  const cpsStatus = cps?.score?.toLowerCase() === 'go'       ? 'go'
                  : cps?.score?.toLowerCase() === 'marginal' ? 'marginal'
                  : cps?.score?.toLowerCase() === 'no-go'    ? 'nogo'
                  : null

  const trains        = amtrak?.trains ?? (Array.isArray(amtrak) ? amtrak : [])
  const delayedTrains = trains.filter(t => (t.delay_minutes ?? 0) > 5)
  const activeTfrs    = Array.isArray(tfrs) ? tfrs.filter(t => t.type !== 'error') : []
  const vipTfrs       = activeTfrs.filter(t =>
    /POTUS|VIP|MOVEMENT|Marine One/i.test(t.notam_text || t.tfr_id || ''))

  return (
    <div className="panel-view ov-view">
      <div className="ov-header">
        <h2>Situational Overview</h2>
        <span className="ov-ts">
          {new Date().toLocaleString('en-US', {
            weekday: 'short', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
          })}
        </span>
      </div>

      {/* Top metric row */}
      <div className="ov-metrics">
        <MetricCard
          label="Operational Readiness"
          value={cps ? `${cps.score} — ${cps.label}` : 'Loading…'}
          status={cpsStatus}
          sub={cps?.narrative?.slice(0, 80)}
          href="/brief"
        />
        <MetricCard
          label="Environmental Alerts"
          value={alerts === null ? '…' : alerts.length || 'None'}
          status={alerts?.length > 2 ? 'warn' : alerts?.length > 0 ? 'marginal' : 'go'}
          sub={alerts?.length > 0 ? (alerts[0]?.properties?.event || alerts[0]?.event_type || '') : 'Area clear'}
          href="/signals#meteorology"
        />
        <MetricCard
          label="Airspace Restrictions"
          value={activeTfrs.length || 'None'}
          status={vipTfrs.length > 0 ? 'nogo' : activeTfrs.length > 0 ? 'marginal' : 'go'}
          sub={vipTfrs.length > 0 ? `${vipTfrs.length} VIP/POTUS TFR active` : activeTfrs.length > 0 ? 'See TFR page' : 'No restrictions'}
          href="/signals"
        />
        <MetricCard
          label="Rail — Union Station"
          value={trains.length === 0 ? 'N/A' : delayedTrains.length > 0 ? `${delayedTrains.length} delayed` : 'On time'}
          status={delayedTrains.length > 0 ? 'warn' : trains.length > 0 ? 'go' : null}
          sub={delayedTrains.length > 0
            ? delayedTrains.slice(0,2).map(t => `${t.train_number || t.id}: +${t.delay_minutes}m`).join(' · ')
            : `${trains.length} trains tracked`}
          href="/trains"
        />
      </div>

      {/* Feed health row */}
      <div className="ov-row">
        <div className="ov-row-label">Feed Health</div>
        <FeedSummary feeds={feeds} />
        <Link to="/status" className="ov-section-link" style={{ marginLeft: 'auto' }}>View feeds →</Link>
      </div>

      {/* ── Panel grid ── */}
      <div className="ov-panel-grid" role="region" aria-label="Operational feed panels">

        {(panels.flights !== false) && (
          <FeedPanel
            id="ov-flights"
            title="Flights"
            badge={null}
            badgeVariant="cyan"
            className="ov-panel"
          >
            <FlightsPanel liveState={liveState} />
          </FeedPanel>
        )}

        {(panels.trains !== false) && (
          <FeedPanel
            id="ov-trains"
            title="Trains / EOTD"
            badge={delayedTrains.length > 0 ? delayedTrains.length : null}
            badgeVariant={delayedTrains.length > 0 ? 'warn' : 'go'}
            className="ov-panel"
          >
            <TrainsPanel amtrak={amtrak} />
          </FeedPanel>
        )}

        {(panels.marine !== false) && (
          <FeedPanel
            id="ov-marine"
            title="Marine / AIS"
            badgeVariant="muted"
            className="ov-panel"
          >
            <MarinePanel />
          </FeedPanel>
        )}

        {(panels.weather !== false) && (
          <FeedPanel
            id="ov-weather"
            title="Weather"
            badge={alerts?.length > 0 ? alerts.length : null}
            badgeVariant="warn"
            className="ov-panel"
          >
            <WeatherPanel alerts={alerts} liveState={liveState} />
          </FeedPanel>
        )}

        {(panels.tfr !== false) && (
          <FeedPanel
            id="ov-tfr"
            title="TFRs"
            badge={activeTfrs.length > 0 ? activeTfrs.length : null}
            badgeVariant={vipTfrs.length > 0 ? 'nogo' : 'warn'}
            className="ov-panel"
          >
            <TFRPanel tfrs={tfrs} />
          </FeedPanel>
        )}

        {(panels.signals !== false) && (
          <FeedPanel
            id="ov-signals"
            title="Signals"
            className="ov-panel"
          >
            <SignalsPanel liveState={liveState} />
          </FeedPanel>
        )}

      </div>

      {/* NWS alerts section */}
      <FeedPanel id="ov-nws" title="Active NWS Alerts — DC Metro" defaultOpen={true} className="ov-section-panel">
        {alerts === null
          ? <div className="muted ov-loading">Loading alerts…</div>
          : <AlertBadge alerts={alerts} />}
      </FeedPanel>

      {/* Ops brief excerpt */}
      <FeedPanel id="ov-brief" title="Current Ops Brief" defaultOpen={true} className="ov-section-panel">
        {loading
          ? <div className="muted ov-loading">Loading…</div>
          : brief
            ? <>
                <div className="ov-brief-excerpt">{briefExcerpt(brief) || '(No content parsed)'}</div>
                <Link to="/brief" className="fp-panel-link" style={{display:'block',marginTop:'0.5rem'}}>Full brief →</Link>
              </>
            : <div className="muted ov-loading">No brief available yet.</div>}
      </FeedPanel>
    </div>
  )
}
