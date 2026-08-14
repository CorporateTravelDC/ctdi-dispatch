import { useState, useEffect, useRef, useCallback } from 'react'
import { useDemoStatus } from '../hooks/useDemoStatus.js'

const ALL_TOPICS = [
  { id: 'dispatch',          label: 'General',     color: 'cyan'   },
  { id: 'wx-alerts',         label: 'Weather',     color: 'orange' },
  { id: 'tfr-alert',         label: 'TFR',         color: 'nogo'   },
  { id: 'hot-alerts',        label: 'Hot',         color: 'nogo'   },
  { id: 'flight-alerts',     label: 'Flights',     color: 'go'     },
  { id: 'cps',               label: 'CPS',         color: 'cyan'   },
  { id: 'ops-health',        label: 'Health',      color: 'muted'  },
  { id: 'train-alerts',      label: 'Rail',        color: 'go'     },
  { id: 'dispatch-debriefs', label: 'Debriefs',    color: 'muted'  },
  { id: 'ops-brief',         label: 'Brief',       color: 'muted'  },
  { id: 'osint-alerts',      label: 'OSINT',       color: 'cyan'   },
]

function relTime(iso) {
  if (!iso) return ''
  try {
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
    if (diff < 60) return `${diff}s ago`
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    return `${Math.floor(diff / 3600)}h ago`
  } catch { return '' }
}

function topicColor(topicId) {
  return ALL_TOPICS.find(t => t.id === topicId)?.color || 'muted'
}

function AlertItem({ msg }) {
  const [expanded, setExpanded] = useState(false)
  const topic = msg.topic || '?'
  const color = topicColor(topic)
  const ts    = msg.time ? new Date(msg.time * 1000).toISOString() : null

  return (
    <div
      className={`ntfy-item ntfy-color-${color}${expanded ? ' expanded' : ''}`}
      onClick={() => setExpanded(e => !e)}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === 'Enter' && setExpanded(x => !x)}
    >
      <div className="ntfy-item-header">
        <span className={`ntfy-topic-chip ntfy-color-${color}`}>{topic}</span>
        {msg.title && <span className="ntfy-title">{msg.title}</span>}
        <span className="ntfy-ts">{relTime(ts)}</span>
        <span className="ntfy-expand-icon">{expanded ? '▲' : '▼'}</span>
      </div>
      <div className={`ntfy-body${expanded ? '' : ' ntfy-body-collapsed'}`}>
        {msg.message}
      </div>
    </div>
  )
}

const _RECEIVERS = [
  { key: 'limoanywhere', label: 'LimoAnywhere',  hint: 'Reservation / dispatch platform' },
  { key: 'threecx',      label: '3CX',           hint: 'Call center / PBX platform' },
  { key: 'ringcentral',  label: 'RingCentral',    hint: 'Call center / UCaaS platform' },
]

function relTimeShort(iso) {
  if (!iso) return ''
  try {
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
    if (diff < 5) return 'just now'
    if (diff < 60) return `${diff}s ago`
    return `${Math.floor(diff / 60)}m ago`
  } catch { return '' }
}

/**
 * IntegrationsPanel -- shows each mock receiver (LimoAnywhere / 3CX /
 * RingCentral) and the most recent webhook payload it's gotten, polling
 * /api/demo/webhook-log every 4s. Only rendered for the chauffeur-pitch
 * and ea-pitch demo profiles (see NtfyFeedView below) -- those are the
 * two audiences who'd actually care that this alert bus can reach into
 * their reservation/call-center stack, not just push to a phone.
 * Demo-only: the endpoint itself 404s for live/trusted requests, so this
 * component has nothing to show and nothing to poll outside that context.
 */
function IntegrationsPanel() {
  const [log, setLog] = useState(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const r = await fetch('/api/demo/webhook-log')
        if (!r.ok) return
        const data = await r.json()
        if (!cancelled) setLog(data)
      } catch { /* transient -- keep last known state */ }
    }
    poll()
    const id = setInterval(poll, 4000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  return (
    <div className="ntfy-integrations-panel">
      <div className="ntfy-integrations-head">
        <h3>Webhook integrations</h3>
        <span className="ntfy-integrations-sub">
          Every alert above also fans out via webhook to your reservation and call-center platforms — shown live below.
        </span>
      </div>
      <div className="ntfy-integrations-grid">
        {_RECEIVERS.map(r => {
          const items = log?.[r.key] || []
          const latest = items[0]
          return (
            <div key={r.key} className="ntfy-integration-card">
              <div className="ntfy-integration-card-head">
                <span className="ntfy-integration-name">{r.label}</span>
                <span className="ntfy-integration-hint">{r.hint}</span>
              </div>
              {latest ? (
                <div className="ntfy-integration-latest">
                  <div className="ntfy-integration-latest-row">
                    <span className="ntfy-integration-title">{latest.title}</span>
                    <span className="ntfy-integration-ts">{relTimeShort(latest.timestamp)}</span>
                  </div>
                  <div className="ntfy-integration-message">{latest.message}</div>
                  <div className="ntfy-integration-meta">POST /webhook · {latest.event}</div>
                </div>
              ) : (
                <div className="ntfy-integration-empty">Waiting for the next alert…</div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function NtfyFeedView() {
  const [demoStatus] = useDemoStatus()
  // Only genuine public demo visitors (not the operator over Tailscale, who
  // should just see the plain real feed like every other tab) get the
  // marketing emphasis below, and only for the two audiences who asked
  // for it -- ota-pitch/concierge-pitch see the ordinary feed.
  const isPublicDemo = demoStatus !== null && demoStatus.demo_mode === true && demoStatus.trusted_origin !== true
  const showBoxEmphasis = isPublicDemo && (demoStatus.label === 'chauffeur-pitch' || demoStatus.label === 'ea-pitch')

  const [enabledTopics, setEnabledTopics] = useState(
    () => {
      try {
        const saved = localStorage.getItem('ntfy_topics')
        return saved ? JSON.parse(saved) : ALL_TOPICS.map(t => t.id)
      } catch { return ALL_TOPICS.map(t => t.id) }
    }
  )
  const [messages, setMessages]   = useState([])
  const [status,   setStatus]     = useState('connecting')
  const [paused,   setPaused]     = useState(false)
  const bufRef    = useRef([])
  const esRef     = useRef(null)
  const pausedRef = useRef(false)

  pausedRef.current = paused

  const connectStream = useCallback((topics) => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
    if (!topics.length) { setStatus('idle'); return }
    setStatus('connecting')
    const qs = topics.join(',')
    const es = new EventSource(`/api/ntfy/stream?topics=${encodeURIComponent(qs)}`)
    esRef.current = es

    es.onopen = () => setStatus('live')

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'heartbeat' || data.type === 'open') return
        if (data.type === 'error') { setStatus('error'); return }
        // ntfy message format: {id, time, event, topic, title, message, ...}
        if (!data.message) return
        const msg = { ...data, _recv: Date.now() }
        if (!pausedRef.current) {
          setMessages(prev => [msg, ...prev].slice(0, 200))
        } else {
          bufRef.current = [msg, ...bufRef.current].slice(0, 200)
        }
      } catch (_) {}
    }

    es.onerror = () => {
      setStatus('error')
      es.close()
      // Reconnect after 5s
      setTimeout(() => connectStream(topics), 5000)
    }
  }, [])

  useEffect(() => {
    connectStream(enabledTopics)
    return () => { esRef.current?.close() }
  }, [enabledTopics, connectStream])

  // Flush buffer when unpausing
  const togglePause = () => {
    setPaused(p => {
      if (p) {
        // Flushing
        setMessages(prev => [...bufRef.current, ...prev].slice(0, 200))
        bufRef.current = []
      }
      return !p
    })
  }

  const toggleTopic = (id) => {
    setEnabledTopics(prev => {
      const next = prev.includes(id) ? prev.filter(t => t !== id) : [...prev, id]
      try { localStorage.setItem('ntfy_topics', JSON.stringify(next)) } catch {}
      return next
    })
  }

  const clearMessages = () => {
    setMessages([])
    bufRef.current = []
  }

  const statusLabel = status === 'live' ? '● LIVE'
                    : status === 'connecting' ? '○ CONNECTING…'
                    : status === 'error' ? '✕ RECONNECTING…'
                    : status === 'idle' ? '— IDLE'
                    : status

  return (
    <div className="panel-view ntfy-view">
      <div className="ntfy-toolbar">
        <div className="ntfy-toolbar-left">
          <h2>Alert Feed</h2>
          <span className={`ntfy-status ntfy-status-${status}`}>{statusLabel}</span>
        </div>
        <div className="ntfy-toolbar-right">
          <button className={`ntfy-ctrl-btn${paused ? ' active' : ''}`} onClick={togglePause}>
            {paused ? `▶ RESUME (${bufRef.current.length})` : '⏸ PAUSE'}
          </button>
          <button className="ntfy-ctrl-btn" onClick={clearMessages}>CLEAR</button>
        </div>
      </div>

      {showBoxEmphasis && (
        <div className="ntfy-box-banner">
          Self-hosted alert bus — every message here stays on this box. No third-party push service, no cloud relay.
        </div>
      )}

      {showBoxEmphasis && <IntegrationsPanel />}

      {/* Topic filter chips */}
      <div className="ntfy-topic-filters">
        {ALL_TOPICS.map(t => (
          <button
            key={t.id}
            className={`ntfy-filter-chip ntfy-color-${t.color}${enabledTopics.includes(t.id) ? ' on' : ' off'}`}
            onClick={() => toggleTopic(t.id)}
          >
            {t.label}
          </button>
        ))}
        <button className="ntfy-filter-chip ntfy-filter-all" onClick={() => {
          const next = enabledTopics.length === ALL_TOPICS.length ? [] : ALL_TOPICS.map(t => t.id)
          setEnabledTopics(next)
          try { localStorage.setItem('ntfy_topics', JSON.stringify(next)) } catch {}
        }}>
          {enabledTopics.length === ALL_TOPICS.length ? 'NONE' : 'ALL'}
        </button>
      </div>

      {/* Message stream */}
      <div className="ntfy-stream">
        {paused && bufRef.current.length > 0 && (
          <div className="ntfy-paused-banner">
            Paused — {bufRef.current.length} buffered message{bufRef.current.length !== 1 ? 's' : ''}
          </div>
        )}
        {messages.length === 0 ? (
          <div className="ntfy-empty">
            {status === 'live' ? 'Waiting for alerts…' : 'Connecting to ntfy…'}
          </div>
        ) : (
          messages.map((m, i) => <AlertItem key={m.id || `${m._recv}-${i}`} msg={m} />)
        )}
      </div>
    </div>
  )
}
