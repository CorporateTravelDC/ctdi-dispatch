import { useState, useRef, useEffect, useCallback } from 'react'

function Message({ msg }) {
  return (
    <div className={`disp-msg disp-msg-${msg.role}`}>
      <span className="disp-msg-role">{msg.role === 'user' ? 'OPS' : 'DISP'}</span>
      <span className="disp-msg-text">{msg.content}</span>
    </div>
  )
}

export default function DispatchView({ liveState }) {
  const [history, setHistory] = useState([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  const cps = liveState?.cps

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, streamText])

  const send = useCallback(async () => {
    const msg = input.trim()
    if (!msg || streaming) return

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

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() // keep incomplete last line
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
          } catch (_) {
            // ignore malformed SSE events
          }
        }
      }
    } catch (e) {
      setError(e.message)
      setStreaming(false)
      setStreamText('')
    }

    inputRef.current?.focus()
  }, [input, streaming, history])

  const handleKey = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }, [send])

  const clearHistory = () => {
    setHistory([])
    setError(null)
    setStreamText('')
    inputRef.current?.focus()
  }

  const cpsClass = cps?.score?.toLowerCase() || 'unknown'

  return (
    <div className="disp-panel">
      {/* Header bar */}
      <div className="disp-header">
        <span className="disp-title">DISPATCH QUERY</span>
        {cps && (
          <span className={`cps-pill ${cpsClass}`}>
            CPS: {cps.score}
          </span>
        )}
        {liveState?.feeds && (() => {
          const errorFeeds = Object.entries(liveState.feeds || {})
            .filter(([, v]) => typeof v === 'object' && v?.error)
          return errorFeeds.length > 0
            ? <span className="disp-feed-warn">{errorFeeds.length} FEED ERR</span>
            : null
        })()}
        <button className="disp-clear-btn" onClick={clearHistory} title="Clear conversation">
          CLR
        </button>
      </div>

      {/* Chat log */}
      <div className="disp-log">
        {history.length === 0 && !streaming && (
          <div className="disp-empty">
            Ask anything operational — weather, TFRs, flight status, route impact,
            HEMS go/no-go, ADS-B, radio, NOTAMs, Amtrak at WAS.
          </div>
        )}

        {history.map((m, i) => <Message key={i} msg={m} />)}

        {/* Streaming assistant response */}
        {streaming && (
          <div className="disp-msg disp-msg-assistant">
            <span className="disp-msg-role">DISP</span>
            <span className="disp-msg-text">
              {streamText
                ? <>{streamText}<span className="disp-cursor">▊</span></>
                : <span className="disp-thinking">receiving...</span>
              }
            </span>
          </div>
        )}

        {error && (
          <div className="disp-error">ERROR: {error}</div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input row */}
      <div className="disp-input-row">
        <textarea
          ref={inputRef}
          className="disp-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="enter query — shift+enter for newline"
          disabled={streaming}
          rows={2}
          autoComplete="off"
          spellCheck="false"
        />
        <button
          className={`disp-send-btn${streaming ? ' busy' : ''}`}
          onClick={send}
          disabled={streaming || !input.trim()}
          title="Send (Enter)"
        >
          {streaming ? '···' : 'SND'}
        </button>
      </div>
    </div>
  )
}
