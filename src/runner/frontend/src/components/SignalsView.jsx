import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useGlobalLayerConfig } from '../App.jsx'

// ═══════════════════════════════════════════════════════════════════
//  AIRSPACE — TFR + NOTAM (merged from TfrView)
// ═══════════════════════════════════════════════════════════════════

const TFR_TYPES = {
  '6': { label: 'Events / Security', short: 'EVT',  color: 'orange'   },
  '5': { label: 'Military',          short: 'MIL',  color: 'airspace' },
  '4': { label: 'International',     short: 'INTL', color: 'cyan'     },
  '9': { label: 'Fire / Disaster',   short: 'FIRE', color: 'nogo'     },
  '1': { label: 'Airport / FDC',     short: 'FDC',  color: 'muted'    },
  '2': { label: 'Enroute',           short: 'ENR',  color: 'muted'    },
}
const DEFAULT_TYPE = { label: 'Other', short: 'OTH', color: 'muted' }

function tfrType(tfr_id) {
  const prefix = tfr_id ? String(tfr_id).split('/')[0] : null
  return TFR_TYPES[prefix] ?? DEFAULT_TYPE
}

function fmtUtc(s) {
  if (!s) return null
  try {
    const d  = new Date(s)
    const mo = d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
    const dy = String(d.getUTCDate()).padStart(2, '0')
    const hh = String(d.getUTCHours()).padStart(2, '0')
    const mm = String(d.getUTCMinutes()).padStart(2, '0')
    return `${mo} ${dy} ${hh}${mm}Z`
  } catch (_) { return s }
}

function tfrMatches(tfr, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  const t  = tfrType(tfr.tfr_id)
  return [tfr.tfr_id, t.label, t.short, fmtUtc(tfr.effective_start), fmtUtc(tfr.effective_end), tfr.enriched_text]
    .some(v => v && String(v).toLowerCase().includes(lq))
}

function TfrRow({ tfr, isVip }) {
  const t     = tfrType(tfr.tfr_id)
  const start = fmtUtc(tfr.effective_start)
  const end   = fmtUtc(tfr.effective_end)
  return (
    <div className={`sig-msg tfr-row${isVip ? ' tfr-row-vip' : ''}`}>
      <span className={`tfr-short-chip tfr-chip-${t.color}`}>{t.short}</span>
      <span className={`sig-msg-call${isVip ? ' tfr-id-vip' : ''}`}>{tfr.tfr_id}</span>
      {tfr.enriched_text
        ? <span className="sig-msg-text tfr-enriched">{tfr.enriched_text}</span>
        : start
          ? <span className="sig-msg-text">{start}{end ? ` → ${end}` : ' → indef.'}</span>
          : <span className="sig-msg-text tfr-dates-degraded">dates unavailable (FAA feed)</span>
      }
      {isVip && <span className="tfr-vip-mini">VIP</span>}
    </div>
  )
}

function VipPanel({ tfrs, loading, updatedAt }) {
  const [search, setSearch] = useState('')
  const vip      = useMemo(() => tfrs?.filter(t => t.is_vip) ?? [], [tfrs])
  const filtered = useMemo(() => vip.filter(t => tfrMatches(t, search)), [vip, search])
  return (
    <div className={`sig-panel tfr-vip-panel${vip.length > 0 ? ' tfr-vip-active' : ''}`}>
      <div className="sig-panel-header">
        <span className="sig-label tfr-vip-label">VIP / POTUS</span>
        {vip.length > 0
          ? <span className="sig-count tfr-count-hot">{vip.length} active</span>
          : <span className="sig-count">clear</span>
        }
        <input className="sig-search" type="search" placeholder="search TFR ID…"
          value={search} onChange={e => setSearch(e.target.value)} aria-label="Search VIP TFRs" />
        {updatedAt && <span className="tfr-updated">↻ {updatedAt}</span>}
      </div>
      <div className="sig-feed">
        {loading ? (
          <div className="sig-empty">Loading…</div>
        ) : vip.length === 0 ? (
          <div className="sig-empty tfr-clear-state">
            <span className="tfr-clear-check">✓</span> Airspace clear — no VIP restrictions
          </div>
        ) : filtered.length === 0 ? (
          <div className="sig-empty">No VIP TFRs matching "{search}"</div>
        ) : (
          filtered.map(tfr => <TfrRow key={tfr.tfr_id} tfr={tfr} isVip />)
        )}
      </div>
    </div>
  )
}

const ALL_FILTER_LABELS = {
  evt: 'Events / Security', mil: 'Military', intl: 'International',
  fire: 'Fire / Disaster',  fdc: 'Airport / FDC', enr: 'Enroute', oth: 'Other',
}

function GeneralPanel({ tfrs, loading, feedDegraded }) {
  const [search, setSearch] = useState('')
  const [typeOn, setTypeOn] = useState(new Set(Object.keys(ALL_FILTER_LABELS)))
  const nonVip = useMemo(() => tfrs?.filter(t => !t.is_vip) ?? [], [tfrs])
  const counts = useMemo(() => {
    const c = {}
    nonVip.forEach(t => { const k = tfrType(t.tfr_id).short.toLowerCase(); c[k] = (c[k] || 0) + 1 })
    return c
  }, [nonVip])
  const presentKeys = useMemo(() => new Set(Object.keys(counts)), [counts])
  const filtered = useMemo(() =>
    nonVip.filter(t => {
      const k = tfrType(t.tfr_id).short.toLowerCase()
      return typeOn.has(k) && tfrMatches(t, search)
    }), [nonVip, typeOn, search])
  const allOn    = typeOn.size === Object.keys(ALL_FILTER_LABELS).length
  const toggleAll = () => setTypeOn(allOn ? new Set() : new Set(Object.keys(ALL_FILTER_LABELS)))
  const toggleType = k => setTypeOn(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })
  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: 'var(--text-2)' }}>GENERAL</span>
        <span className="sig-count">{nonVip.length} active</span>
        <input className="sig-search" type="search" placeholder="TFR ID, type…"
          value={search} onChange={e => setSearch(e.target.value)} aria-label="Search general TFRs" />
      </div>
      {!loading && nonVip.length > 0 && (
        <div className="tfr-filter-chips">
          {Object.entries(ALL_FILTER_LABELS).filter(([k]) => presentKeys.has(k)).map(([k, label]) => (
            <button key={k} className={`tfr-filter-chip${typeOn.has(k) ? ' on' : ' off'}`}
              onClick={() => toggleType(k)}>
              {label}<span className="tfr-filter-count">{counts[k] || 0}</span>
            </button>
          ))}
          <button className="tfr-filter-chip tfr-filter-all" onClick={toggleAll}>{allOn ? 'NONE' : 'ALL'}</button>
        </div>
      )}
      {feedDegraded && (
        <div className="sig-hw-notice">⚠ FAA TFR XML feed degraded — effective dates unavailable</div>
      )}
      <div className="sig-feed">
        {loading ? <div className="sig-empty">Loading…</div>
          : nonVip.length === 0 ? <div className="sig-empty">No general TFRs active</div>
          : filtered.length === 0 ? <div className="sig-empty">No TFRs match current filters</div>
          : filtered.map(tfr => <TfrRow key={tfr.tfr_id} tfr={tfr} isVip={false} />)
        }
      </div>
    </div>
  )
}

function notamMatches(n, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [n.notam_id, n.icao, n.location, n.text, n.classification, n.type]
    .some(v => v && String(v).toLowerCase().includes(lq))
}

function NotamPanel({ notams, loading }) {
  const [search, setSearch] = useState('')
  const filtered = useMemo(() => {
    const all = notams || []
    return search ? all.filter(n => notamMatches(n, search)) : all
  }, [notams, search])
  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: 'var(--muted)' }}>NOTAM</span>
        <span className="sig-count">{notams?.length ?? 0} active</span>
        <input className="sig-search" type="search" placeholder="NOTAM ID, ICAO, location…"
          value={search} onChange={e => setSearch(e.target.value)} aria-label="Search NOTAMs" />
      </div>
      <div className="sig-feed">
        {loading ? <div className="sig-empty">Loading…</div>
          : !notams?.length ? (
            <div className="sig-pending">
              FAA NOTAM API key required — set <code>FAA_NOTAM_API_KEY</code> in dispatch-secrets.env
            </div>
          ) : !filtered.length ? <div className="sig-empty">No NOTAMs matching "{search}"</div>
          : filtered.map((n, i) => (
            <div key={n.notam_id || i} className="sig-msg">
              <span className="sig-msg-call" style={{ color: 'var(--text-2)' }}>{n.notam_id || '—'}</span>
              {n.icao && <span className="sig-msg-flight">{n.icao}</span>}
              <span className="sig-msg-text">
                {n.text || n.raw_text || (n.classification && `[${n.classification}]`) || '—'}
              </span>
              {n.location && <span className="sig-msg-loc">{n.location}</span>}
            </div>
          ))
        }
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════
//  SIGNALS — VDL2 / ACARS / HFDL / AIS / METAR
// ═══════════════════════════════════════════════════════════════════

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
  const cls = source === 'local' ? 'local' : source === 'none' ? 'none' : 'external'
  return <span className={`source-badge sig-source ${cls}`}>{SOURCE_LABELS[source] || source}</span>
}

function msgMatchesSearch(m, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [m.callsign, m.flight, m.registration, m.icao, m.text, m.cleanedText, m.location, m.direction, m.protocol, m.aircraft_type, m.icao_type]
    .some(v => v && String(v).toLowerCase().includes(lq))
}

function MessageFeed({ sigType, color }) {
  const [data, setData]   = useState({ source: 'local', messages: [], count: 0 })
  const [search, setSearch] = useState('')
  const [sinceRef]          = useState({ value: 0 })
  const feedRef             = useRef(null)

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

  const isPending = data.detail === 'hardware_pending'
  const hwNotice  = sigType.hwNeeded && data.source !== 'local'

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color }}>{sigType.label}</span>
        <SourceBadge source={data.source} />
        <span className="sig-count">{data.count} msg</span>
        <input className="sig-search" type="search" placeholder="search…"
          value={search} onChange={e => setSearch(e.target.value)} aria-label={`Search ${sigType.label} messages`} />
      </div>
      {hwNotice && <div className="sig-hw-notice">⚠ No local {sigType.label} decoder — showing Jumpseat remote data</div>}
      <div className="sig-feed" ref={feedRef}>
        {isPending ? (
          <div className="sig-pending">Hardware pending — {sigType.label} decoder not active</div>
        ) : !filtered.length ? (
          <div className="sig-empty">
            {search ? `No ${sigType.label} messages matching "${search}"` : `No ${sigType.label} messages`}
          </div>
        ) : filtered.map((m, i) => (
          <div key={i} className="sig-msg">
            <span className="sig-msg-time">{m.time || (m.timestamp ? m.timestamp.slice(11, 19) : '') || ''}</span>
            <span className="sig-msg-call" style={{ color }}>{m.callsign || m.flight || m.registration || m.icao || m.addr || '?'}</span>
            {m.flight && m.flight !== m.callsign && <span className="sig-msg-flight">{m.flight}</span>}
            {m.direction && (
              <span className="sig-msg-dir">
                {m.direction === 'Air to Ground' ? '↑GND' : m.direction === 'Ground to Air' ? '↓AIR' : m.direction.slice(0, 6)}
              </span>
            )}
            <span className="sig-msg-text">
              {m.text || (m.automated ? '[automated uplink]' : null) || (m.aircraft_type ? `[${m.aircraft_type}]` : null) || (m.icao_type ? `[${m.icao_type}]` : null) || '[no text]'}
            </span>
            {m.location && <span className="sig-msg-loc">{m.location}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

function AisPanel() {
  const [data, setData]   = useState({ source: 'local', vessels: [], count: 0 })
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
  const filtered  = useMemo(() => {
    const all = data.vessels || []
    if (!search) return all.slice(0, 30)
    const lq = search.toLowerCase()
    return all.filter(v => [v.SHIPNAME, v.name, v.MMSI, v.mmsi, v.DESTINATION, v.SHIPTYPE]
      .some(f => f && String(f).toLowerCase().includes(lq)))
  }, [data.vessels, search])

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: '#4a9eff' }}>AIS</span>
        <SourceBadge source={data.source} />
        <span className="sig-count">{data.count} vessels</span>
        <input className="sig-search" type="search" placeholder="search…"
          value={search} onChange={e => setSearch(e.target.value)} aria-label="Search AIS vessels" />
      </div>
      <div className="sig-feed">
        {isPending ? (
          <div className="sig-pending">Hardware pending — AIS decoder not active. MarineTraffic fallback requires API key.</div>
        ) : !filtered.length ? (
          <div className="sig-empty">{search ? `No vessels matching "${search}"` : 'No vessels in range'}</div>
        ) : filtered.map((v, i) => (
          <div key={i} className="sig-msg">
            <span className="sig-msg-call" style={{ color: '#4a9eff' }}>{v.SHIPNAME || v.name || 'MMSI: ' + (v.MMSI || '?')}</span>
            <span className="sig-msg-text">{v.SHIPTYPE ? `${v.SHIPTYPE} ` : ''}{v.SPEED ? `${v.SPEED}kt ` : ''}{v.DESTINATION ? `→ ${v.DESTINATION}` : ''}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function fmtObsTime(ts) {
  if (!ts) return ''
  try {
    const d  = new Date(ts * 1000)
    const dd = String(d.getUTCDate()).padStart(2, '0')
    const hh = String(d.getUTCHours()).padStart(2, '0')
    const mm = String(d.getUTCMinutes()).padStart(2, '0')
    return `${dd}/${hh}${mm}Z`
  } catch (_) { return '' }
}

function fmtMetar(m) {
  const parts = []
  if (m.ceiling_ft != null)   parts.push(`CLG ${m.ceiling_ft >= 9999 ? 'UNL' : `${Math.round(m.ceiling_ft / 100) * 100}ft`}`)
  if (m.visibility_sm != null) parts.push(`VIS ${m.visibility_sm}sm`)
  if (m.wind_kt != null)       parts.push(`WND ${m.wind_kt}kt`)
  if (m.precip_code)           parts.push(m.precip_code)
  return parts.join(' · ') || '—'
}

// ═══════════════════════════════════════════════════════════════════
//  METEOROLOGY — NWS Alerts + METAR
// ═══════════════════════════════════════════════════════════════════

const SEV_COLORS = {
  extreme:  '#ff2929',
  severe:   '#ff6b35',
  moderate: '#ffd700',
  minor:    '#87ceeb',
  unknown:  'var(--muted)',
}

function fmtAlertTime(s) {
  if (!s) return ''
  try {
    const d = new Date(s)
    return `${String(d.getUTCMonth()+1).padStart(2,'0')}/${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}${String(d.getUTCMinutes()).padStart(2,'0')}Z`
  } catch(_) { return s }
}

function alertMatches(a, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [a.event_type, a.area_desc, a.severity, a.headline, a.alert_id]
    .some(v => v && String(v).toLowerCase().includes(lq))
}

function NwsAlertsPanel() {
  const [data, setData]     = useState(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/dispatch/api/v1/alerts')
        if (!r.ok) return
        setData(await r.json())
      } catch (_) {}
    }
    poll()
    const id = setInterval(poll, 60000)
    return () => clearInterval(id)
  }, [])

  const alerts   = data?.alerts ?? (Array.isArray(data) ? data : [])
  const filtered = useMemo(() => {
    if (!search) return alerts
    return alerts.filter(a => alertMatches(a, search))
  }, [alerts, search])

  return (
    <div className="sig-panel wx-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: '#ff8c00' }}>NWS ALERTS</span>
        <span className={`sig-count${alerts.length > 0 ? ' sig-count-hot' : ''}`}>
          {alerts.length > 0 ? `${alerts.length} active` : 'clear'}
        </span>
        <input className="sig-search" type="search" placeholder="search alerts…"
          value={search} onChange={e => setSearch(e.target.value)} aria-label="Search NWS alerts" />
      </div>
      <div className="sig-feed">
        {data === null ? (
          <div className="sig-empty">Loading NWS alerts…</div>
        ) : !filtered.length ? (
          <div className="sig-empty">
            {search ? `No alerts matching "${search}"` : '✓ No active NWS hazardous weather alerts'}
          </div>
        ) : filtered.map((a, i) => {
          const sev   = (a.severity || 'unknown').toLowerCase()
          const color = SEV_COLORS[sev] ?? SEV_COLORS.unknown
          return (
            <div key={a.alert_id || i} className="sig-msg wx-alert-msg">
              <span className="sig-msg-call" style={{ color }}>{a.event_type || 'Alert'}</span>
              <span className="sig-msg-flight" style={{ color: 'var(--text-2)' }}>{sev.toUpperCase()}</span>
              <span className="sig-msg-text">{a.headline || a.area_desc || '—'}</span>
              {a.expires && <span className="sig-msg-time">exp {fmtAlertTime(a.expires)}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function WeatherPanel() {
  const [data, setData]   = useState(null)
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

  const metars   = data?.metars || []
  const filtered = useMemo(() => {
    if (!search) return metars
    const lq = search.toLowerCase()
    return metars.filter(m => (m.station || '').toLowerCase().includes(lq) || (m.precip_code || '').toLowerCase().includes(lq))
  }, [metars, search])

  return (
    <div className="sig-panel wx-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: '#87ceeb' }}>METAR</span>
        <span className="sig-count">{metars.length} stations</span>
        <input className="sig-search" type="search" placeholder="search stations…"
          value={search} onChange={e => setSearch(e.target.value)} aria-label="Search METARs" />
      </div>
      <div className="sig-hw-notice">ℹ TAF/SPECI: raw text pending — AviationWeather feed returns parsed fields only</div>
      <div className="sig-feed">
        {data === null ? <div className="sig-empty">Loading weather…</div>
          : !filtered.length ? <div className="sig-empty">{search ? `No stations matching "${search}"` : 'No METAR data'}</div>
          : filtered.map((m, i) => (
            <div key={i} className="sig-msg wx-msg">
              <span className="sig-msg-call" style={{ color: '#87ceeb' }}>{m.station}</span>
              <span className="sig-msg-time">{fmtObsTime(m.obs_time)}</span>
              <span className="sig-msg-text">{fmtMetar(m)}</span>
            </div>
          ))
        }
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════
//  Root
// ═══════════════════════════════════════════════════════════════════

const TFR_POLL_MS = 30_000

export default function SignalsView() {
  const layerCtx = useGlobalLayerConfig()
  const layers   = layerCtx?.config?.layers ?? {}

  // ── TFR + NOTAM state ──────────────────────────────────────────
  const [tfrs,      setTfrs]      = useState(null)
  const [notams,    setNotams]    = useState(null)
  const [feedErr,   setFeedErr]   = useState(false)
  const [updatedAt, setUpdatedAt] = useState(null)

  const fmtNow = () => {
    const d = new Date()
    return `${String(d.getUTCHours()).padStart(2, '0')}${String(d.getUTCMinutes()).padStart(2, '0')}Z`
  }

  const loadTfrs = useCallback(async () => {
    try {
      const r = await fetch('/api/dispatch/api/v1/tfr-enriched')
      if (!r.ok) throw new Error(r.status)
      const d    = await r.json()
      const list = Array.isArray(d) ? d : (d.tfrs || [])
      setTfrs(list)
      const nullDates = list.filter(t => !t.effective_start && !t.effective_end).length
      setFeedErr(list.length > 0 && nullDates / list.length > 0.1)
      setUpdatedAt(fmtNow())
    } catch {
      try {
        const r2   = await fetch('/api/dispatch/api/v1/tfr')
        const d2   = await r2.json()
        const list = Array.isArray(d2) ? d2 : (d2.tfrs || [])
        setTfrs(list)
        setFeedErr(list.length > 0 && list.filter(t => !t.effective_start && !t.effective_end).length / list.length > 0.1)
        setUpdatedAt(fmtNow())
      } catch { setTfrs([]) }
    }
  }, [])

  const loadNotams = useCallback(async () => {
    try {
      const r = await fetch('/api/dispatch/api/v1/notams')
      const d = await r.json()
      setNotams(Array.isArray(d) ? d : (d.notams || []))
    } catch { setNotams([]) }
  }, [])

  useEffect(() => {
    loadTfrs()
    loadNotams()
    const id = setInterval(loadTfrs, TFR_POLL_MS)
    return () => clearInterval(id)
  }, [loadTfrs, loadNotams])

  // ── Visible panel counts ───────────────────────────────────────
  const sigCount = [
    ...SIGNAL_TYPES.map(st => layers[st.key] !== false),
    layers.ais !== false,
  ].filter(Boolean).length

  return (
    <div className="panel-view signals-view">

      {/* ── AIRSPACE section ─────────────────────────────────── */}
      <div className="signals-header-row">
        <h2>Airspace Restrictions</h2>
        <button
          className="intel-refresh-btn"
          onClick={() => { loadTfrs(); loadNotams() }}
          disabled={tfrs === null}
          title="Refresh TFRs + NOTAMs"
        >
          {tfrs === null ? '⟳' : '↻'}
        </button>
        <span className="sig-panel-count" style={{ marginLeft: 'auto' }}>
          TFRs · NOTAMs · polls every {TFR_POLL_MS / 1000}s
        </span>
      </div>

      <div className="sig-grid tfr-grid">
        <VipPanel     tfrs={tfrs}   loading={tfrs === null}   updatedAt={updatedAt} />
        <GeneralPanel tfrs={tfrs}   loading={tfrs === null}   feedDegraded={feedErr} />
        <NotamPanel   notams={notams} loading={notams === null} />
      </div>

      {/* ── SIGNALS section ──────────────────────────────────── */}
      <div className="signals-header-row" style={{ marginTop: '2rem' }}>
        <h2 id="signals-heading">Signals Intelligence</h2>
        <span className="sig-panel-count" aria-live="polite">
          {sigCount} panel{sigCount !== 1 ? 's' : ''} visible
        </span>
      </div>
      <p className="sig-subtitle">
        VDL2 / ACARS / HFDL via local decoders or airframes.io —
        AIS via local AIS-catcher or MarineTraffic
      </p>

      <div className="sig-grid" role="region" aria-labelledby="signals-heading" aria-live="polite" aria-atomic="false">
        {SIGNAL_TYPES.map(st =>
          layers[st.key] !== false
            ? <MessageFeed key={st.key} sigType={st} color={st.color} />
            : null
        )}
        {layers.ais !== false && <AisPanel />}

        {sigCount === 0 && (
          <div className="sig-all-hidden" role="status">
            All signal panels hidden — open ⚙ Settings to restore visibility.
          </div>
        )}
      </div>

      {/* ── METEOROLOGY section ──────────────────────────────── */}
      <div className="signals-header-row" style={{ marginTop: '2rem' }} id="meteorology">
        <h2 id="meteorology-heading">Meteorology</h2>
        <span className="sig-panel-count">
          NWS Alerts · METAR · polls every 60s
        </span>
      </div>
      <p className="sig-subtitle">
        NWS hazardous weather alerts — METAR observations from AviationWeather.gov
      </p>

      <div className="sig-grid" role="region" aria-labelledby="meteorology-heading">
        <NwsAlertsPanel />
        {layers.metar !== false && <WeatherPanel />}
      </div>

    </div>
  )
}
