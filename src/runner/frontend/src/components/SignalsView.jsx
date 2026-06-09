import { useState, useEffect, useRef } from 'react'

const SIGNAL_TYPES = [
  { key: 'vdl2',  label: 'VDL2',  endpoint: '/api/vdl2/messages',  color: '#00d4ff' },
  { key: 'acars', label: 'ACARS', endpoint: '/api/acars/messages', color: '#ffd700' },
  { key: 'hfdl',  label: 'HFDL',  endpoint: '/api/hfdl/messages',  color: '#39ff14' },
]

const SOURCE_LABELS = {
  'local':           'LOCAL',
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

function MessageFeed({ sigType, color }) {
  const [data, setData] = useState({ source: 'local', messages: [], count: 0 })
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

  const isEmpty = !data.messages?.length
  const isPending = data.detail === 'hardware_pending'

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color }}>{sigType.label}</span>
        <SourceBadge source={data.source} />
        <span className="sig-count">{data.count} msg</span>
      </div>
      <div className="sig-feed" ref={feedRef}>
        {isPending ? (
          <div className="sig-pending">Hardware pending -- {sigType.label} decoder not active</div>
        ) : isEmpty ? (
          <div className="sig-empty">No {sigType.label} messages</div>
        ) : (
          [...(data.messages || [])].reverse().slice(0, 50).map((m, i) => (
            <div key={i} className="sig-msg">
              <span className="sig-msg-time">{m.timestamp || m.time || ''}</span>
              <span className="sig-msg-call" style={{ color }}>
                {m.callsign || m.flight || m.icao || m.addr || '?'}
              </span>
              <span className="sig-msg-text">
                {m.text || m.message || m.data || JSON.stringify(m).slice(0, 80)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function AisPanel() {
  const [data, setData] = useState({ source: 'local', vessels: [], count: 0 })

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

  return (
    <div className="sig-panel">
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: '#4a9eff' }}>AIS</span>
        <SourceBadge source={data.source} />
        <span className="sig-count">{data.count} vessels</span>
      </div>
      <div className="sig-feed">
        {isPending ? (
          <div className="sig-pending">Hardware pending -- AIS decoder not active. MarineTraffic fallback requires API key.</div>
        ) : !data.vessels?.length ? (
          <div className="sig-empty">No vessels in range</div>
        ) : (
          data.vessels.slice(0, 30).map((v, i) => (
            <div key={i} className="sig-msg">
              <span className="sig-msg-call" style={{ color: '#4a9eff' }}>
                {v.SHIPNAME || v.name || v.mmsi || 'MMSI: ' + (v.MMSI || '?')}
              </span>
              <span className="sig-msg-text">
                {v.SHIPTYPE ? `Type: ${v.SHIPTYPE} ` : ''}
                {v.SPEED ? `${v.SPEED}kt ` : ''}
                {v.DESTINATION ? `-> ${v.DESTINATION}` : ''}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default function SignalsView() {
  return (
    <div className="panel-view signals-view">
      <h2>Signals Intelligence</h2>
      <p className="sig-subtitle">
        VDL2 / ACARS / HFDL via local decoders or airframes.io (Jumpseat) --
        AIS via local AIS-catcher or MarineTraffic -- all sources within 250nm KDCA
      </p>
      <div className="sig-grid">
        {SIGNAL_TYPES.map(st => (
          <MessageFeed key={st.key} sigType={st} color={st.color} />
        ))}
        <AisPanel />
      </div>
    </div>
  )
}
