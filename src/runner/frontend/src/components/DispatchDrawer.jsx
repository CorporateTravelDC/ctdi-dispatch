import { useState, useRef, useEffect, useCallback } from 'react'

// Default model — must match OLLAMA_CHAT_MODEL in dispatch.env
const DEFAULT_MODEL = 'llama3.2:3b'

// Known models for the quick-select menu
const KNOWN_MODELS = ['llama3.2:3b', 'mistral', 'llama3.1:8b']

function Message({ msg }) {
  return (
    <div className={`dd-msg dd-msg-${msg.role}`}>
      <span className="dd-msg-role">{msg.role === 'user' ? 'OPS' : 'DISP'}</span>
      {msg.modelUsed && (
        <span className="dd-msg-model" title={`Serviced by ${msg.modelUsed}`}>
          [{msg.modelUsed}]
        </span>
      )}
      <span className="dd-msg-text">{msg.content}</span>
    </div>
  )
}

// open / setOpen are lifted to App so the topbar DISP button can control the drawer
export default function DispatchDrawer({ liveState, open, setOpen }) {
  const [history, setHistory]           = useState([])
  const [input, setInput]               = useState('')
  const [streaming, setStreaming]       = useState(false)
  const [streamText, setStreamText]     = useState('')
  const [streamModel, setStreamModel]   = useState(null)   // model reported mid-stream
  const [error, setError]               = useState(null)
  // modelOverride: null = use server default (OLLAMA_CHAT_MODEL)
  // string = send this model for every request until cleared
  const [modelOverride, setModelOverride] = useState(null)
  const [showModelMenu, setShowModelMenu] = useState(false)

  const bottomRef  = useRef(null)
  const inputRef   = useRef(null)
  const menuRef    = useRef(null)

  const cps = liveState?.cps
  const activeModel = modelOverride || DEFAULT_MODEL

  // Load persisted history from server on mount
  useEffect(() => {
    fetch('/api/chat/history')
      .then(r => r.ok ? r.json() : { messages: [] })
      .then(data => {
        if (Array.isArray(data.messages) && data.messages.length > 0) {
          setHistory(data.messages)
        }
      })
      .catch(() => {/* DB unavailable — start fresh */})
  }, [])

  // Auto-scroll on new content
  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, streamText, open])

  // Focus textarea when drawer opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 120)
  }, [open])

  // Close model menu on outside click
  useEffect(() => {
    if (!showModelMenu) return
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setShowModelMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showModelMenu])

  const send = useCallback(async () => {
    let msg = input.trim()
    if (!msg || streaming) return

    // Parse inline /model directive: "/model <name> <rest>"
    // This lets the operator override for a single message without locking the session.
    let inlineModel = null
    const mxInline = /^\/model\s+(\S+)\s*([\s\S]*)/.exec(msg)
    if (mxInline) {
      inlineModel = mxInline[1]
      msg = mxInline[2].trim() || msg  // keep original if no remainder
    }

    // "/model reset" or "/model clear" clears the session override
    if (inlineModel && (inlineModel === 'reset' || inlineModel === 'clear')) {
      setModelOverride(null)
      setInput('')
      return
    }

    // "/model <name>" with no query body — set session override and return
    if (inlineModel && !mxInline[2].trim()) {
      setModelOverride(inlineModel)
      setInput('')
      return
    }

    const effectiveModel = inlineModel || modelOverride || null  // null → server uses default

    setOpen(true)
    setInput('')
    setError(null)
    setHistory(prev => [...prev, { role: 'user', content: msg }])
    setStreaming(true)
    setStreamText('')
    setStreamModel(null)

    const body = { message: msg }
    if (effectiveModel) body.model = effectiveModel

    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || res.statusText)
      }

      // Capture which model actually serviced this request from response header
      const headerModel = res.headers.get('X-Dispatch-Model')
      if (headerModel) setStreamModel(headerModel)

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let accumulated = ''
      let finished = false
      let resolvedModel = headerModel || effectiveModel || DEFAULT_MODEL

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          let ev
          try { ev = JSON.parse(line.slice(6)) } catch (_) { continue }
          if (ev.type === 'model_info') {
            resolvedModel = ev.model
            setStreamModel(ev.model)
          } else if (ev.type === 'text') {
            accumulated += ev.text
            setStreamText(accumulated)
          } else if (ev.type === 'done') {
            setHistory(prev => [...prev, {
              role: 'assistant',
              content: accumulated,
              modelUsed: resolvedModel,
            }])
            setStreamText('')
            setStreamModel(null)
            setStreaming(false)
            accumulated = ''
            finished = true
          } else if (ev.type === 'error') {
            throw new Error(ev.detail || 'stream error')
          }
          // ev.type === 'no_llm' — local answer follows as text events, done event closes
        }
      }

      if (!finished) {
        if (accumulated) {
          setHistory(prev => [...prev, {
            role: 'assistant',
            content: accumulated,
            modelUsed: resolvedModel,
          }])
        }
        setStreamText('')
        setStreamModel(null)
        setStreaming(false)
      }
    } catch (e) {
      setError(e.message)
      setStreaming(false)
      setStreamText('')
      setStreamModel(null)
    }

    inputRef.current?.focus()
  }, [input, streaming, modelOverride, setOpen])

  const handleKey = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
    if (e.key === 'Escape') setOpen(false)
  }, [send, setOpen])

  const clearHistory = () => {
    setHistory([])
    setError(null)
    setStreamText('')
    inputRef.current?.focus()
    fetch('/api/chat/history', { method: 'DELETE' }).catch(() => {})
  }

  const cpsClass = cps?.score?.toLowerCase() || 'unknown'

  if (!open) return null

  return (
    <div className="dd-drawer dd-open">
      {/* ── Header strip ── */}
      <div className="dd-strip">
        <button
          className="dd-toggle"
          onClick={() => setOpen(false)}
          title="Collapse dispatch panel"
          aria-label="Close dispatch panel"
        >
          ▼
        </button>

        <span className="dd-strip-label">DISP QUERY</span>

        {cps && (
          <span className={`cps-pill ${cpsClass}`} title={cps.narrative}>
            CPS: {cps.score}
          </span>
        )}

        {/* ── Model selector ── */}
        <div className="dd-model-wrap" ref={menuRef}>
          <button
            className={`dd-model-badge${modelOverride ? ' overridden' : ''}`}
            onClick={() => setShowModelMenu(v => !v)}
            title={modelOverride
              ? `Session model override: ${modelOverride} — click to change`
              : `Active model: ${DEFAULT_MODEL} — click to override`}
          >
            {activeModel.length > 14 ? activeModel.slice(0, 13) + '…' : activeModel}
            {modelOverride && <span className="dd-model-lock">🔒</span>}
          </button>

          {showModelMenu && (
            <div className="dd-model-menu">
              <div className="dd-model-menu-title">Select model</div>
              {KNOWN_MODELS.map(m => (
                <button
                  key={m}
                  className={`dd-model-opt${activeModel === m ? ' active' : ''}`}
                  onClick={() => { setModelOverride(m === DEFAULT_MODEL ? null : m); setShowModelMenu(false) }}
                >
                  {m}
                  {m === DEFAULT_MODEL && <span className="dd-model-default"> (default)</span>}
                </button>
              ))}
              {modelOverride && (
                <button
                  className="dd-model-opt dd-model-reset"
                  onClick={() => { setModelOverride(null); setShowModelMenu(false) }}
                >
                  ↩ Reset to default
                </button>
              )}
              <div className="dd-model-hint">
                Or type /model &lt;name&gt; in chat
              </div>
            </div>
          )}
        </div>

        {history.length > 0 && (
          <button className="dd-clr-btn" onClick={clearHistory}>CLR</button>
        )}
      </div>

      {/* ── Message log ── */}
      <div className="dd-body">
        <div className="dd-log">
          {history.length === 0 && !streaming && (
            <div className="dd-empty">
              Ask anything — weather, TFRs, HEMS go/no-go, flight status,
              Amtrak at WAS, route impact, NOTAMs, ADS-B.
              <br />
              <span className="dd-empty-hint">
                Type /model &lt;name&gt; to override the LLM for this session.
              </span>
            </div>
          )}

          {history.map((m, i) => <Message key={i} msg={m} />)}

          {streaming && (
            <div className="dd-msg dd-msg-assistant">
              <span className="dd-msg-role">DISP</span>
              {streamModel && (
                <span className="dd-msg-model dd-msg-model-live">[{streamModel}]</span>
              )}
              <span className="dd-msg-text">
                {streamText
                  ? <>{streamText}<span className="dd-cursor">▊</span></>
                  : <span className="dd-thinking">receiving...</span>
                }
              </span>
            </div>
          )}

          {error && <div className="dd-error">ERROR: {error}</div>}
          <div ref={bottomRef} />
        </div>

        <div className="dd-input-row">
          <textarea
            ref={inputRef}
            className="dd-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="enter query — /model <name> to override LLM — shift+enter newline — esc close"
            disabled={streaming}
            rows={2}
            autoComplete="off"
            spellCheck="false"
          />
          <button
            className={`dd-send-btn${streaming ? ' busy' : ''}`}
            onClick={send}
            disabled={streaming || !input.trim()}
          >
            {streaming ? '···' : 'SND'}
          </button>
        </div>
      </div>
    </div>
  )
}
