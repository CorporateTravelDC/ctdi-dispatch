import { useEffect, useState, useCallback } from 'react'
import AirmetHazardMap from './AirmetHazardMap.jsx'
import { useVisibilityAwareInterval } from '../hooks/useVisibilityAwareInterval.js'

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
// the Signals tab (SignalsView.jsx's WeatherPanel/NwsAlertsPanel) and the operator
// wants them to stay there ONLY, not duplicated onto this page in any
// format. Don't re-add a METAR/alerts side panel to this component.
//
// 2026-08-03: added a third source, WPC national surface prog charts
// (PROG toggle + forecast-hour row). Same public-NOAA-static-image pattern
// as the NEXRAD radar_url above -- no new polling/parsing, config comes
// from the same /api/v1/wx-config the radar source already uses. Real,
// live imagery in both live and demo mode this cycle (not replay-captured,
// see wx-config's backend comments for why). Maritime (OPC) charts follow
// the same pattern.
//
// 2026-08-03 (later same day): added a fourth source, HAZARDS -- the
// AIRMET/SIGMET polygon layer already built for the tactical map
// (MapView.jsx), duplicated here as a self-contained CONUS-wide Leaflet
// view (AirmetHazardMap.jsx) instead of a static image, since AIRMET/SIGMET
// is vector polygon data with no equivalent public chart image (confirmed:
// AWC doesn't publish a static gairmet/sigmet graphic the way WPC does for
// surface prog). This is the first step toward the operator's stated direction --
// WX becomes the all-travel-hazards "look at everything" page, while the
// tactical map (and later the AIS map) stays the live-traffic view.

export default function WeatherView() {
  const [wxConfig, setWxConfig] = useState(null)
  // 'nws' | 'operator' | 'prog' | 'maritime' | 'hazards' -- operator only
  // selectable once config.operator is populated; prog/maritime/hazards are
  // always selectable once wxConfig loads (all three are fixed public
  // product sets, not per-deployment config choices). hazards doesn't
  // actually read wxConfig at all -- AirmetHazardMap fetches /api/v1/airmets
  // directly, same as the tactical map's AIRMET layer.
  const [source,   setSource]   = useState('nws')
  const [progHour, setProgHour] = useState('current')  // 'current' | 6|12|18|24|30|36|48|60
  const [maritimeChart, setMaritimeChart] = useState('sfc-current')  // key into maritime.charts
  const [nwsScope, setNwsScope] = useState('site')  // 'site' | 'conus' -- NEXRAD only
  const [radarNonce, setRadarNonce] = useState(0)

  // ── Config (NWS site + optional operator-defined alternate) ───────
  const loadConfig = useCallback(async () => {
    const cfg = await fetchWxConfig()
    if (cfg) setWxConfig(cfg)
  }, [])

  useVisibilityAwareInterval(loadConfig, CONFIG_POLL)

  // If an operator source disappears from config while selected, fall back to NWS.
  useEffect(() => {
    if (source === 'operator' && !(wxConfig && wxConfig.operator)) setSource('nws')
  }, [wxConfig, source])

  // ── Radar image refresh (cache-bust the loop gif periodically, and the
  // moment the app becomes visible again -- see useVisibilityAwareInterval)
  useVisibilityAwareInterval(() => setRadarNonce(n => n + 1), RADAR_POLL)

  const refreshAll = () => { loadConfig(); setRadarNonce(n => n + 1) }

  const hasOperator = !!(wxConfig && wxConfig.operator)
  const nws      = wxConfig && wxConfig.nws
  const op       = wxConfig && wxConfig.operator
  const prog     = wxConfig && wxConfig.prog
  const maritime = wxConfig && wxConfig.maritime

  const progImage = prog
    ? (progHour === 'current' ? prog.current : prog.forecasts.find(f => f.hour === progHour))
    : null
  const maritimeImage = maritime
    ? maritime.charts.find(c => c.key === maritimeChart)
    : null

  const activeName = source === 'operator' && op ? (op.name || 'OPERATOR')
    : source === 'prog' ? (prog ? prog.name || 'WPC SFC PROG' : 'WPC SFC PROG')
    : source === 'maritime' ? (maritime ? maritime.name || 'OPC' : 'OPC')
    : source === 'hazards' ? 'FAA HAZARDS'
    : (nws ? nws.name || 'NWS' : 'NWS')

  const radarSrc = source === 'operator' && op
    ? `${op.map_url}${op.map_url.includes('?') ? '&' : '?'}_r=${radarNonce}`
    : source === 'prog'
    ? (progImage ? `${progImage.url}?_r=${radarNonce}` : null)
    : source === 'maritime'
    ? (maritimeImage ? `${maritimeImage.url}?_r=${radarNonce}` : null)
    : (nws
        ? `${nwsScope === 'conus' && nws.radar_url_conus ? nws.radar_url_conus : nws.radar_url}?_r=${radarNonce}`
        : null)

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
          <button
            className={`train-mode-btn${source === 'prog' ? ' active' : ''}`}
            onClick={() => setSource('prog')}
          >PROG</button>
          <button
            className={`train-mode-btn${source === 'maritime' ? ' active' : ''}`}
            onClick={() => setSource('maritime')}
          >MARITIME</button>
          <button
            className={`train-mode-btn${source === 'hazards' ? ' active' : ''}`}
            onClick={() => setSource('hazards')}
          >HAZARDS</button>
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
            : source === 'prog'
            ? 'wpc.ncep.noaa.gov'
            : source === 'maritime'
            ? 'tgftp.nws.noaa.gov (OPC)'
            : source === 'hazards'
            ? 'aviationweather.gov (AWC Data API)'
            : `radar.weather.gov · ${nws ? nws.wfo : ''}`}
        </span>
        <button className="intel-refresh-btn" onClick={refreshAll} title="Refresh weather data">↻</button>
      </div>

      {/*
        Full-page radar -- mirrors .globe-map-wrap/.map-container's plain
        width:100%/flex:1 treatment directly, no side panel of any kind.
        Corrected 2026-07-21: an earlier pass added a METAR/alerts side
        panel here; that was wrong -- the operator wants METAR/alerts to live ONLY
        on the Signals tab, and WX to be strictly the radar map, full page.
      */}
      {source === 'prog' && prog && (
        <div className="wx-prog-hours">
          <button
            className={`train-mode-btn wx-prog-hour-btn${progHour === 'current' ? ' active' : ''}`}
            onClick={() => setProgHour('current')}
          >NOW</button>
          {prog.forecasts.map(f => (
            <button
              key={f.hour}
              className={`train-mode-btn wx-prog-hour-btn${progHour === f.hour ? ' active' : ''}`}
              onClick={() => setProgHour(f.hour)}
            >{f.label}</button>
          ))}
        </div>
      )}

      {source === 'maritime' && maritime && (
        <div className="wx-prog-hours">
          {maritime.charts.map(c => (
            <button
              key={c.key}
              className={`train-mode-btn wx-prog-hour-btn${maritimeChart === c.key ? ' active' : ''}`}
              onClick={() => setMaritimeChart(c.key)}
            >{c.label}</button>
          ))}
        </div>
      )}

      {/* 2026-08-03: site vs. national-composite loop for NEXRAD -- every
          other source on this tab (PROG, MARITIME, HAZARDS) already shows
          a wide-area view; NEXRAD was still local-site-only. Same static-
          image toggle pattern, just two options instead of a list. */}
      {source === 'nws' && nws && nws.radar_url_conus && (
        <div className="wx-prog-hours">
          <button
            className={`train-mode-btn wx-prog-hour-btn${nwsScope === 'site' ? ' active' : ''}`}
            onClick={() => setNwsScope('site')}
          >{(nws.radar_site || 'SITE').toUpperCase()}</button>
          <button
            className={`train-mode-btn wx-prog-hour-btn${nwsScope === 'conus' ? ' active' : ''}`}
            onClick={() => setNwsScope('conus')}
          >CONUS</button>
        </div>
      )}

      <div className="wx-radar-main">
        <div className="wx-radar-head">
          {source === 'prog'
            ? `${activeName.toUpperCase()}${progImage ? ` — ${progImage.label}` : ''}`
            : source === 'maritime'
            ? `${activeName.toUpperCase()}${maritimeImage ? ` — ${maritimeImage.label}` : ''}`
            : source === 'hazards'
            ? `${activeName.toUpperCase()} — AIRMET/SIGMET`
            : `${activeName.toUpperCase()} RADAR${source === 'nws' && nws ? ` — ${nwsScope === 'conus' ? 'CONUS' : nws.radar_site}` : ''}`}
        </div>
        <div className={`wx-radar-stage${source === 'hazards' ? ' wx-radar-stage-hazards' : ''}`}>
          {source === 'hazards' ? (
            <AirmetHazardMap />
          ) : radarSrc ? (
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
