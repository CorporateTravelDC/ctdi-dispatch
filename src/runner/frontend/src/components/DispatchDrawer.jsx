import { useState, useRef, useEffect, useCallback } from 'react'

function Message({ msg }) {
  return (
    <div className={`dd-msg dd-msg-${msg.role}`}>
      <span className="dd-msg-role">{msg.role === 'user' ? 'OPS' : 'DISP'}</span>
      <span className="dd-msg-text">{msg.content}</span>
    </div>
  )
}

// open / setOpen are lifted to App so the topbar DISP button can control the drawer
export default function DispatchDrawer({ liveState, open, setOpen }) {
  const [history, setHistory]       = useState([])
  const [input, setInput]           = useState('')
  const [streaming, setStreaming]   = useState(false)
  const [streamText, setStreamText] = useState('')
  const [error, setError]           = useState(null)

  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  const cps = liveState?.cps

  // Auto-scroll on new content
  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, streamText, open])

  // Focus textarea when drawer opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 120)
  }, [open])

  const send = useCallback(async () => {
    const msg = input.trim()
    if (!msg || streaming) return

    setOpen(true)
    setInput('')
    setError(null)
    const snapshot = [...history]
    setHistory(prev => [...prev, { role: 'user', content: msg }])
    setStreaming(true)
    setStreamText('')

    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, history: snapshot }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || res.statusText)
      }

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const ev = JSON.parse(line.slice(6))
            if (ev.type === 'text') {
              accumulated += ev.text
              setStreamText(accumulated)
            } else if (ev.type === 'done') {
              setHistory(prev => [...prev, { role: 'assistant', content: accumulated }])
              setStreamText('')
              setStreaming(false)
              accumulated = ''
            } else if (ev.type === 'error') {
              throw new Error(ev.detail)
            }
          } catch (_) {}
        }
      }
    } catch (e) {
      setError(e.message)
      setStreaming(false)
      setStreamText('')
    }

    inputRef.current?.focus()
  }, [input, streaming, history, setOpen])

  const handleKey = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
    if (e.key === 'Escape') setOpen(false)
  }, [send, setOpen])

  const clearHistory = () => {
    setHistory([])
    setError(null)
    setStreamText('')
    inputRef.current?.focus()
  }

  const cpsClass = cps?.score?.toLowerCase() || 'unknown'

  if (!open) return null  // drawer closed — nothing rendered; topbar DISP button is the entry point

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
            </div>
          )}

          {history.map((m, i) => <Message key={i} msg={m} />)}

          {streaming && (
            <div className="dd-msg dd-msg-assistant">
              <span className="dd-msg-role">DISP</span>
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
            placeholder="enter query — shift+enter for newline, esc to close"
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
