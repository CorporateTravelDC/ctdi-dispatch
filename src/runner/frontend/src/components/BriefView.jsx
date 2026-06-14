import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

// ── Built-in brief type configs ───────────────────────────────────────────────
// Any undiscovered type gets the default config (ops-style schedule/workflow).
const BUILTIN = {
  ops:    { label: 'OPS BRIEF',   emptyMsg: 'No brief yet. Generates hourly; 6-hour trend analysis at 00:00, 06:00, 12:00, 18:00 ET.' },
  weekly: { label: 'WEEKLY',      emptyMsg: 'No weekly summary yet. Runs Sunday 18:00 ET.' },
}

function typeLabel(t) {
  return BUILTIN[t]?.label || t.toUpperCase().replace(/_/g, ' ')
}

function typeEmpty(t) {
  return BUILTIN[t]?.emptyMsg || `No ${t} brief found. It will appear here once generated.`
}

// ── Format timestamp ──────────────────────────────────────────────────────────
function fmtTs(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
      timeZone: 'UTC', hour12: false,
    }) + 'Z'
  } catch { return iso }
}

// ── Brief API helpers ─────────────────────────────────────────────────────────
function briefUrl(type)    {
  if (type === 'ops')    return '/api/dispatch/api/v1/brief'
  if (type === 'weekly') return '/api/dispatch/api/v1/brief/weekly'
  return `/api/dispatch/api/v1/brief/${type}`  // custom types: GET by type slug
}

function historyUrl(type) {
  return `/api/dispatch/api/v1/brief/history?limit=12&type=${type}`
}

// ── Shared localStorage for operator-defined types ────────────────────────────
const LS_KEY = 'ctdc_brief_custom_types'
function loadCustomTypes() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) || [] } catch { return [] }
}
function saveCustomTypes(arr) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(arr)) } catch {}
}

// ── BriefTab: reusable content panel for any brief type ──────────────────────
function BriefTab({ type }) {
  const [brief,       setBrief]       = useState(null)
  const [loading,     setLoading]     = useState(true)
  const [history,     setHistory]     = useState([])
  const [selected,    setSelected]    = useState(null)
  const [archText,    setArchText]    = useState(null)
  const [archLoading, setArchLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    setBrief(null)
    setHistory([])
    setSelected(null)
    setArchText(null)
    Promise.all([
      fetch(briefUrl(type)).then(r => r.ok ? r.text() : null).catch(() => null),
      fetch(historyUrl(type)).then(r => r.ok ? r.json() : []).catch(() => []),
    ]).then(([text, hist]) => {
      setBrief(text?.trim() ? text : null)
      setHistory(Array.isArray(hist) ? hist : [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [type])

  useEffect(() => {
    if (selected === null) { setArchText(null); return }
    setArchLoading(true)
    fetch(`/api/dispatch/api/v1/brief/${selected}`)
      .then(r => r.ok ? r.text() : Promise.reject(r.status))
      .then(t => { setArchText(t); setArchLoading(false) })
      .catch(() => { setArchText('Failed to load archived brief.'); setArchLoading(false) })
  }, [selected])

  const displayText    = selected === null ? brief   : archText
  const displayLoading = selected === null ? loading : archLoading

  return (
    <>
      {history.length > 0 && (
        <div className="brief-history-nav">
          <span className="brief-hist-label">HISTORY:</span>
          <button
            className={`brief-hist-btn${selected === null ? ' active' : ''}`}
            onClick={() => setSelected(null)}
          >CURRENT</button>
          {history.map(h => (
            <button
              key={h.id}
              className={`brief-hist-btn${selected === h.id ? ' active' : ''}`}
              onClick={() => setSelected(h.id)}
              title={`${h.brief_type?.toUpperCase() || type.toUpperCase()} — ${fmtTs(h.generated_at)}`}
            >{fmtTs(h.generated_at)}</button>
          ))}
        </div>
      )}

      {displayLoading ? (
        <p className="muted">Loading…</p>
      ) : displayText ? (
        <div className="brief-card">
          {selected !== null && (() => {
            const h = history.find(x => x.id === selected)
            return h ? (
              <p className="brief-meta">
                Archived: {fmtTs(h.generated_at)} — {(h.brief_type || type).toUpperCase()} / {h.source || '—'}
              </p>
            ) : null
          })()}
          <pre className="brief-text">{displayText}</pre>
        </div>
      ) : (
        <div className="brief-card">
          <p className="muted brief-text">{typeEmpty(type)}</p>
        </div>
      )}
    </>
  )
}

// ── Root BriefView ────────────────────────────────────────────────────────────
export default function BriefView() {
  const [searchParams] = useSearchParams()
  // ?tab=TYPE in the URL (set by ntfy click-through) selects a tab on load
  const urlTab = searchParams.get('tab') || 'ops'

  const [tab,          setTab]          = useState(urlTab)
  const [discovered,   setDiscovered]   = useState([])   // from brief history
  const [customTypes,  setCustomTypes]  = useState(loadCustomTypes)
  const [addingType,   setAddingType]   = useState(false)
  const [newTypeInput, setNewTypeInput] = useState('')
  const [newTypeErr,   setNewTypeErr]   = useState('')

  // Auto-discover brief types from history
  useEffect(() => {
    fetch('/api/dispatch/api/v1/brief/history?limit=50')
      .then(r => r.ok ? r.json() : [])
      .catch(() => [])
      .then(hist => {
        if (!Array.isArray(hist)) return
        const seen = new Set()
        hist.forEach(h => { if (h.brief_type) seen.add(h.brief_type) })
        // Exclude builtins from discovered list (they have their own fixed tabs)
        const extra = [...seen].filter(t => !BUILTIN[t])
        setDiscovered(extra)
      })
  }, [])

  // Merge: builtins first, then discovered, then operator-defined
  const builtinTypes   = Object.keys(BUILTIN)
  const allCustom      = [...new Set([...discovered, ...customTypes])]
  const allTypes       = [...builtinTypes, ...allCustom]

  const handleAddType = useCallback(() => {
    const slug = newTypeInput.trim().toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '')
    if (!slug) { setNewTypeErr('Type name is required.'); return }
    if (allTypes.includes(slug)) { setNewTypeErr(`"${slug}" already exists.`); return }
    const updated = [...customTypes, slug]
    setCustomTypes(updated)
    saveCustomTypes(updated)
    setTab(slug)
    setAddingType(false)
    setNewTypeInput('')
    setNewTypeErr('')
  }, [newTypeInput, allTypes, customTypes])

  const handleRemoveCustom = useCallback((slug) => {
    const updated = customTypes.filter(t => t !== slug)
    setCustomTypes(updated)
    saveCustomTypes(updated)
    if (tab === slug) setTab('ops')
  }, [customTypes, tab])

  return (
    <div className="panel-view briefs-view">
      <div className="brief-header-row">
        <div className="brief-tab-row">
          <h2>Briefs</h2>
          <div className="brief-type-tabs">
            {allTypes.map(t => (
              <span key={t} className="brief-type-btn-wrap">
                <button
                  className={`brief-type-btn${tab === t ? ' active' : ''}${discovered.includes(t) ? ' brief-tab-discovered' : ''}`}
                  onClick={() => setTab(t)}
                  title={customTypes.includes(t) ? 'Operator-defined brief type' : discovered.includes(t) ? 'Auto-discovered brief type' : ''}
                >{typeLabel(t)}</button>
                {customTypes.includes(t) && (
                  <button
                    className="brief-type-remove"
                    onClick={() => handleRemoveCustom(t)}
                    title={`Remove ${t} tab`}
                    aria-label={`Remove ${t}`}
                  >×</button>
                )}
              </span>
            ))}

            {/* Add custom type */}
            {addingType ? (
              <span className="brief-type-add-form">
                <input
                  className="brief-type-add-input"
                  value={newTypeInput}
                  onChange={e => { setNewTypeInput(e.target.value); setNewTypeErr('') }}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddType() } if (e.key === 'Escape') { setAddingType(false); setNewTypeInput(''); setNewTypeErr('') } }}
                  placeholder="type slug (e.g. ep)"
                  autoFocus
                  spellCheck={false}
                  autoComplete="off"
                />
                <button className="brief-type-btn" onClick={handleAddType}>ADD</button>
                <button className="brief-type-btn" onClick={() => { setAddingType(false); setNewTypeInput(''); setNewTypeErr('') }}>✕</button>
                {newTypeErr && <span className="brief-add-err">{newTypeErr}</span>}
              </span>
            ) : (
              <button
                className="brief-type-btn brief-type-add"
                onClick={() => setAddingType(true)}
                title="Add a custom brief type"
              >＋</button>
            )}
          </div>
        </div>
      </div>

      <BriefTab key={tab} type={tab} />
    </div>
  )
}
