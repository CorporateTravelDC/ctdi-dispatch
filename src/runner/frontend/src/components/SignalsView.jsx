import { useState, useEffect, useRef, useMemo } from 'react'
import { useGlobalLayerConfig } from '../App.jsx'

const SIGNAL_TYPES = [
  { key: 'vdl2',  label: 'VDL2',  endpoint: '/api/vdl2/messages',  color: '#00d4ff', hwNeeded: false },
  { key: 'acars', label: 'ACARS', endpoint: '/api/acars/messages', color: '#ffd700', hwNeeded: true },
  { key: 'hfdl',  label: 'HFDL',  endpoint: '/api/hfdl/messages',  color: '#39ff14', hwNeeded: true },
]

const SOURCE_LABELS = {
  'local':           'LOCAL',
  'acarsdrama.com':  'JUMPSEAT',
  'airframes.io':    'AIRFRAMES',
  'marinetraffic.com': 'MARINETRAFFIC',
  'none':            'NONE',
}

function SourceBadge({ source }) {
  const cls = source === 'local' ? 'local'
            : source === 'none'  ? 'none'
            : 'external'
  return <span className={`source-badge sig-source ${cls}`}>{SOURCE_LABELS[source] || source}</span>
}

function msgMatchesSearch(m, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [
    m.callsign, m.flight, m.registration, m.icao, m.text,
    m.cleanedText, m.location, m.direction, m.protocol,
    m.aircraft_type, m.icao_type,
  ].some(v => v && String(v).toLowerCase().includes(lq))
}

function MessageFeed({ sigType, color }) {
  const [data, setData]   = useState({ source: 'local', messages: [], count: 0 })
  const [search, setSearch] = useState('')
  const [sinceRef] = useState({ value: 0 })
  const feedRef = useRef(null)

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`${sigType.endpoint}?since=${sinceRef.value}`)
        if (!r.ok) return
        const json = await r.json()
        setData(json)
        if (json.messages?.length) {
          const ids = json.messages.map(m => m.id || m.msg_id || 0).filter(Boolean)
          if (ids.length) sinceRef.value = Math.max(...ids)
        }
      } catch (_) {}
    }
    poll()
    const id = setInterval(poll, 15000)
    return () => clearInterval(id)
  }, [sigType.endpoint])

  const filtered = useMemo(() => {
    const all = [...(data.messages || [])].reverse()
    return search ? all.filter(m => msgMatchesSearch(m, search)) : all.slice(0, 50)
  }, [data.messages, search])

  const isEmpty = !filtered.length
  const isPending = data.detail === 'hardware_pending'
  // hwNeeded: no local decoder; still show remote data if available
  const hwNotice = sigType.hwNeeded && data.source !== 'local'

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color }}>{sigType.label}</span>
        <SourceBadge source={data.source} />
        <span className="sig-count">{data.count} msg</span>
        <input
          className="sig-search"
          type="search"
          placeholder="search…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label={`Search ${sigType.label} messages`}
        />
      </div>
      {hwNotice && (
        <div className="sig-hw-notice">
          ⚠ No local {sigType.label} decoder — showing Jumpseat remote data
        </div>
      )}
      <div className="sig-feed" ref={feedRef}>
        {isPending ? (
          <div className="sig-pending">Hardware pending — {sigType.label} decoder not active</div>
        ) : isEmpty ? (
          <div className="sig-empty">
            {search ? `No ${sigType.label} messages matching "${search}"` : `No ${sigType.label} messages`}
          </div>
        ) : (
          filtered.map((m, i) => (
            <div key={i} className="sig-msg">
              <span className="sig-msg-time">
                {m.time || (m.timestamp ? m.timestamp.slice(11, 19) : '') || ''}
              </span>
              <span className="sig-msg-call" style={{ color }}>
                {m.callsign || m.flight || m.registration || m.icao || m.addr || '?'}
              </span>
              {m.flight && m.flight !== m.callsign && (
                <span className="sig-msg-flight">{m.flight}</span>
              )}
              {m.direction && (
                <span className="sig-msg-dir">
                  {m.direction === 'Air to Ground' ? '↑GND'
                 : m.direction === 'Ground to Air' ? '↓AIR'
                 : m.direction.slice(0, 6)}
                </span>
              )}
              <span className="sig-msg-text">
                {m.text
                  || (m.automated ? '[automated uplink]' : null)
                  || (m.aircraft_type ? `[${m.aircraft_type}]` : null)
                  || (m.icao_type ? `[${m.icao_type}]` : null)
                  || '[no text]'}
              </span>
              {m.location && (
                <span className="sig-msg-loc">{m.location}</span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function AisPanel() {
  const [data, setData]     = useState({ source: 'local', vessels: [], count: 0 })
  const [search, setSearch] = useState('')

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/ais/vessels')
        if (!r.ok) return
        setData(await r.json())
      } catch (_) {}
    }
    poll()
    const id = setInterval(poll, 30000)
    return () => clearInterval(id)
  }, [])

  const isPending = data.detail === 'hardware_pending' || (!data.vessels?.length && data.source === 'none')

  const filtered = useMemo(() => {
    const all = data.vessels || []
    if (!search) return all.slice(0, 30)
    const lq = search.toLowerCase()
    return all.filter(v =>
      [v.SHIPNAME, v.name, v.MMSI, v.mmsi, v.DESTINATION, v.SHIPTYPE]
        .some(f => f && String(f).toLowerCase().includes(lq))
    )
  }, [data.vessels, search])

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: '#4a9eff' }}>AIS</span>
        <SourceBadge source={data.source} />
        <span className="sig-count">{data.count} vessels</span>
        <input
          className="sig-search"
          type="search"
          placeholder="search…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search AIS vessels"
        />
      </div>
      <div className="sig-feed">
        {isPending ? (
          <div className="sig-pending">
            Hardware pending — AIS decoder not active. MarineTraffic fallback requires API key.
          </div>
        ) : !filtered.length ? (
          <div className="sig-empty">
            {search ? `No vessels matching "${search}"` : 'No vessels in range'}
          </div>
        ) : (
          filtered.map((v, i) => (
            <div key={i} className="sig-msg">
              <span className="sig-msg-call" style={{ color: '#4a9eff' }}>
                {v.SHIPNAME || v.name || 'MMSI: ' + (v.MMSI || '?')}
              </span>
              <span className="sig-msg-text">
                {v.SHIPTYPE ? `${v.SHIPTYPE} ` : ''}
                {v.SPEED ? `${v.SPEED}kt ` : ''}
                {v.DESTINATION ? `→ ${v.DESTINATION}` : ''}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function fmtObsTime(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts * 1000)
    const dd = String(d.getUTCDate()).padStart(2, '0')
    const hh = String(d.getUTCHours()).padStart(2, '0')
    const mm = String(d.getUTCMinutes()).padStart(2, '0')
    return `${dd}/${hh}${mm}Z`
  } catch (_) { return '' }
}

function fmtMetar(m) {
  const parts = []
  if (m.ceiling_ft != null) {
    const c = m.ceiling_ft >= 9999 ? 'UNL' : `${Math.round(m.ceiling_ft / 100) * 100}ft`
    parts.push(`CLG ${c}`)
  }
  if (m.visibility_sm != null) parts.push(`VIS ${m.visibility_sm}sm`)
  if (m.wind_kt != null)       parts.push(`WND ${m.wind_kt}kt`)
  if (m.precip_code)           parts.push(m.precip_code)
  return parts.join(' · ') || '—'
}

function wxMatchesSearch(m, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return (
    (m.station || '').toLowerCase().includes(lq) ||
    (m.precip_code || '').toLowerCase().includes(lq)
  )
}

function WeatherPanel() {
  const [data, setData]     = useState(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/dispatch/api/v1/weather')
        if (!r.ok) return
        setData(await r.json())
      } catch (_) {}
    }
    poll()
    const id = setInterval(poll, 60000)
    return () => clearInterval(id)
  }, [])

  const metars = data?.metars || []

  const filtered = useMemo(() => {
    return search ? metars.filter(m => wxMatchesSearch(m, search)) : metars
  }, [metars, search])

  return (
    <div className="sig-panel wx-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: '#87ceeb' }}>METAR</span>
        <span className="sig-count">{metars.length} stations</span>
        <input
          className="sig-search"
          type="search"
          placeholder="search stations…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search METARs"
        />
      </div>
      <div className="sig-hw-notice">
        ℹ TAF/SPECI: raw text pending — AviationWeather feed returns parsed fields only
      </div>
      <div className="sig-feed">
        {data === null ? (
          <div className="sig-empty">Loading weather…</div>
        ) : !filtered.length ? (
          <div className="sig-empty">
            {search ? `No stations matching "${search}"` : 'No METAR data'}
          </div>
        ) : (
          filtered.map((m, i) => (
            <div key={i} className="sig-msg wx-msg">
              <span className="sig-msg-call" style={{ color: '#87ceeb' }}>{m.station}</span>
              <span className="sig-msg-time">{fmtObsTime(m.obs_time)}</span>
              <span className="sig-msg-text">{fmtMetar(m)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default function SignalsView() {
  const layerCtx = useGlobalLayerConfig()
  const layers   = layerCtx?.config?.layers ?? {}

  // Count visible panels
  const visibleCount = [
    ...(SIGNAL_TYPES.map(st => layers[st.key] !== false)),
    layers.ais   !== false,
    layers.metar !== false,
  ].filter(Boolean).length

  return (
    <div className="panel-view signals-view">
      <div className="signals-header-row">
        <h2 id="signals-heading">Signals Intelligence</h2>
        <span className="sig-panel-count" aria-live="polite">
          {visibleCount} panel{visibleCount !== 1 ? 's' : ''} visible
        </span>
      </div>
      <p className="sig-subtitle">
        VDL2 / ACARS / HFDL via local decoders or airframes.io (Jumpseat) —
        AIS via local AIS-catcher or MarineTraffic — all sources within 250nm KDCA
      </p>

      <div
        className="sig-grid"
        role="region"
        aria-labelledby="signals-heading"
        aria-live="polite"
        aria-atomic="false"
      >
        {SIGNAL_TYPES.map(st =>
          layers[st.key] !== false
            ? <MessageFeed key={st.key} sigType={st} color={st.color} />
            : null
        )}
        {layers.ais   !== false && <AisPanel />}
        {layers.metar !== false && <WeatherPanel />}

        {visibleCount === 0 && (
          <div className="sig-all-hidden" role="status">
            All panels hidden — open ⚙ Settings to restore visibility.
          </div>
        )}
      </div>
    </div>
  )
}
