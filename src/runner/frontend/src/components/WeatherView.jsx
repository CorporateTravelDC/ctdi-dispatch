import { useEffect, useState, useCallback } from 'react'

const CONFIG_POLL = 300_000   // wx-config barely changes -- 5 min is plenty
const RADAR_POLL   = 150_000  // radar.weather.gov loop gif regenerates every few minutes

// ── Fetchers ────────────────────────────────────────────────────────
async function fetchWxConfig() {
  try {
    const r = await fetch('/api/dispatch/api/v1/wx-config')
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

// 2026-07-21 correction: WX is strictly a full-page NEXRAD/operator radar
// map -- no METAR, no NWS alerts panel here. Those already have a home on
// the Signals tab (SignalsView.jsx's WeatherPanel/NwsAlertsPanel) and Corey
// wants them to stay there ONLY, not duplicated onto this page in any
// format. Don't re-add a METAR/alerts side panel to this component.

export default function WeatherView() {
  const [wxConfig, setWxConfig] = useState(null)
  // 'nws' | 'operator' -- operator only selectable once config.operator is populated.
  const [source,   setSource]   = useState('nws')
  const [radarNonce, setRadarNonce] = useState(0)

  // ── Config (NWS site + optional operator-defined alternate) ───────
  const loadConfig = useCallback(async () => {
    const cfg = await fetchWxConfig()
    if (cfg) setWxConfig(cfg)
  }, [])

  useEffect(() => {
    loadConfig()
    const id = setInterval(loadConfig, CONFIG_POLL)
    return () => clearInterval(id)
  }, [loadConfig])

  // If an operator source disappears from config while selected, fall back to NWS.
  useEffect(() => {
    if (source === 'operator' && !(wxConfig && wxConfig.operator)) setSource('nws')
  }, [wxConfig, source])

  // ── Radar image refresh (cache-bust the loop gif periodically) ────
  useEffect(() => {
    const id = setInterval(() => setRadarNonce(n => n + 1), RADAR_POLL)
    return () => clearInterval(id)
  }, [])

  const refreshAll = () => { loadConfig(); setRadarNonce(n => n + 1) }

  const hasOperator = !!(wxConfig && wxConfig.operator)
  const nws = wxConfig && wxConfig.nws
  const op  = wxConfig && wxConfig.operator

  const activeName  = source === 'operator' && op ? (op.name || 'OPERATOR') : (nws ? nws.name || 'NWS' : 'NWS')
  const radarSrc = source === 'operator' && op
    ? `${op.map_url}${op.map_url.includes('?') ? '&' : '?'}_r=${radarNonce}`
    : (nws ? `${nws.radar_url}?_r=${radarNonce}` : null)
  const isIframe = source === 'operator' && op && op.is_iframe

  return (
    <div className="train-map-view">
      <div className="train-map-subnav">
        <span className="train-map-title">WX</span>

        <div className="train-view-toggle">
          <button
            className={`train-mode-btn${source === 'nws' ? ' active' : ''}`}
            onClick={() => setSource('nws')}
          >{nws ? (nws.name || 'NEXRAD').toUpperCase() : 'NEXRAD'}</button>
          {hasOperator && (
            <button
              className={`train-mode-btn${source === 'operator' ? ' active' : ''}`}
              onClick={() => setSource('operator')}
            >{(op.name || 'OPERATOR').toUpperCase()}</button>
          )}
        </div>

        <span className="stat source-badge" style={{ color: 'var(--cyan)', marginLeft: 'auto' }}>
          {source === 'operator' && op
            ? (op.name || 'operator-defined source')
            : `radar.weather.gov · ${nws ? nws.wfo : ''}`}
        </span>
        <button className="intel-refresh-btn" onClick={refreshAll} title="Refresh weather data">↻</button>
      </div>

      {/*
        Full-page radar -- mirrors .globe-map-wrap/.map-container's plain
        width:100%/flex:1 treatment directly, no side panel of any kind.
        Corrected 2026-07-21: an earlier pass added a METAR/alerts side
        panel here; that was wrong -- Corey wants METAR/alerts to live ONLY
        on the Signals tab, and WX to be strictly the radar map, full page.
      */}
      <div className="wx-radar-main">
        <div className="wx-radar-head">
          {activeName.toUpperCase()} RADAR{source === 'nws' && nws ? ` — ${nws.radar_site}` : ''}
        </div>
        <div className="wx-radar-stage">
          {radarSrc ? (
            isIframe ? (
              <iframe
                src={radarSrc}
                title="Operator weather map"
                className="wx-radar-frame"
                loading="lazy"
              />
            ) : (
              <img
                src={radarSrc}
                alt={`${activeName} radar loop`}
                className="wx-radar-image"
                loading="lazy"
              />
            )
          ) : (
            <div className="train-panel-empty">No radar source configured</div>
          )}
        </div>
        {source === 'nws' && nws && nws.station_page && (
          <div className="wx-radar-caption">
            <a href={nws.station_page} target="_blank" rel="noopener noreferrer">
              Full interactive view ↗
            </a>
          </div>
        )}
      </div>
    </div>
  )
}
