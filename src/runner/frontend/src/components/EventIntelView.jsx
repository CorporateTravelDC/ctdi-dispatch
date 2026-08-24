import { useState, useMemo, useCallback } from 'react'
import { useVisibilityAwareInterval } from '../hooks/useVisibilityAwareInterval.js'

// ── OSINT scope_type groupings (mirrors osint_monitor.py / second_brain_daily.py) ──
const EP_TYPES  = new Set(['ep_threat', 'ep_principal', 'ep_venue', 'executive_protection'])
const EVT_TYPES = new Set(['event'])
const MKT_TYPES = new Set(['brand_monitor', 'market_intel', 'competitor', 'marketing'])

function groupKey(scope_type) {
  if (EP_TYPES.has(scope_type))  return 'ep'
  if (EVT_TYPES.has(scope_type)) return 'evt'
  if (MKT_TYPES.has(scope_type)) return 'mkt'
  return 'gen'
}

function relTime(tsSeconds) {
  if (!tsSeconds) return ''
  try {
    const diff = Math.floor(Date.now() / 1000 - tsSeconds)
    if (diff < 60) return 'just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return `${Math.floor(diff / 86400)}d ago`
  } catch (_) { return '' }
}

function itemMatches(item, q) {
  if (!q) return true
  const lq = q.toLowerCase()
  return [item.title, item.headline, item.scope_label, item.narrative, item.source_name, item.outlet]
    .some(v => v && String(v).toLowerCase().includes(lq))
}

// Cluster same-story items (same story_key, crossover_count > 1) together,
// preserving original (recency) order for the first-seen item in each
// cluster. Singleton/unclustered items pass through unchanged.
function groupByStory(items) {
  const seen = new Set()
  const out = []
  for (const it of items) {
    const sk = it.story_key
    if (sk && seen.has(sk)) continue
    if (sk && (it.crossover_count || 1) > 1) {
      seen.add(sk)
      out.push({ type: 'story', key: sk, items: items.filter(x => x.story_key === sk) })
    } else {
      out.push({ type: 'single', key: `i${it.id}`, item: it })
    }
  }
  return out
}

const SCORE_CLASS = { CRITICAL: 'nogo', HIGH: 'orange', MEDIUM: 'cyan', LOW: 'muted' }
function topLabel(items) {
  const order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
  for (const lbl of order) if (items.some(i => i.score_label === lbl)) return lbl
  return 'LOW'
}

// ── One item row ──────────────────────────────────────────────────────────
function OsintRow({ item }) {
  const scoreClass = SCORE_CLASS[item.score_label] || 'muted'

  return (
    <div className="sig-msg osint-row">
      <span className={`tfr-short-chip tfr-chip-${scoreClass}`}>{item.score_label}</span>
      <span className="sig-msg-call">{item.scope_label}</span>
      <a className="sig-msg-text" href={item.url} target="_blank" rel="noreferrer">
        {item.headline || item.title}
      </a>
      {item.narrative && (
        <span className="sig-msg-loc osint-narrative">{item.narrative}</span>
      )}
      <span className="tfr-updated">{relTime(item.ingested_at)}</span>
    </div>
  )
}

// ── Same story, multiple outlets ────────────────────────────────────────────
function OsintStoryRow({ items }) {
  const rep = items.reduce((a, b) => (a.ingested_at >= b.ingested_at ? a : b))
  const scoreClass = SCORE_CLASS[topLabel(items)] || 'muted'
  const byOutlet = [...items].sort((a, b) => (b.ingested_at || 0) - (a.ingested_at || 0))

  return (
    <div className="sig-msg osint-row osint-story-row">
      <span className={`tfr-short-chip tfr-chip-${scoreClass}`}>{topLabel(items)}</span>
      <span className="sig-msg-call">{rep.scope_label}</span>
      <span className="sig-msg-text">{rep.headline || rep.title}</span>
      <span className="osint-crossover-badge" title={`Covered by ${items.length} outlets`}>
        ⚭ {items.length} outlets
      </span>
      <div className="osint-outlet-list">
        {byOutlet.map(o => (
          <a key={o.id} className="osint-outlet-link" href={o.url} target="_blank" rel="noreferrer">
            {o.outlet || o.source_name || 'source'}
          </a>
        ))}
      </div>
      {rep.narrative && (
        <span className="sig-msg-loc osint-narrative">{rep.narrative}</span>
      )}
      <span className="tfr-updated">{relTime(rep.ingested_at)}</span>
    </div>
  )
}

// ── One grouped panel (EP / Events / Marketing / General) ──────────────────
function OsintPanel({ label, items, loading, emptyText, highlight }) {
  const [search, setSearch] = useState('')
  const filtered = useMemo(() => items.filter(i => itemMatches(i, search)), [items, search])
  const rows = useMemo(() => groupByStory(filtered), [filtered])

  return (
    <div className={`sig-panel${highlight && items.length > 0 ? ' tfr-vip-active' : ''}`}>
      <div className="sig-panel-header">
        <span className="sig-label" style={{ color: highlight ? undefined : 'var(--text-2)' }}>
          {label}
        </span>
        <span className="sig-count">{items.length} item{items.length !== 1 ? 's' : ''}</span>
        <input
          className="sig-search"
          type="search"
          placeholder="search…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label={`Search ${label}`}
        />
      </div>
      <div className="sig-feed">
        {loading ? (
          <div className="sig-empty">Loading…</div>
        ) : items.length === 0 ? (
          <div className="sig-empty">{emptyText}</div>
        ) : filtered.length === 0 ? (
          <div className="sig-empty">No items matching &ldquo;{search}&rdquo;</div>
        ) : (
          rows.map(r => r.type === 'story'
            ? <OsintStoryRow key={r.key} items={r.items} />
            : <OsintRow key={r.key} item={r.item} />)
        )}
      </div>
    </div>
  )
}

// ── Root view ─────────────────────────────────────────────────────────────
const POLL_MS = 60_000
const MIN_SCORE = 4 // MEDIUM+ -- matches ep_advance_brief.py / second_brain_daily.py

export default function EventIntelView() {
  const [items, setItems] = useState(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/dispatch/api/v1/osint/feed?min_score=${MIN_SCORE}&limit=150`)
      if (!r.ok) throw new Error(r.status)
      const d = await r.json()
      setItems(Array.isArray(d.items) ? d.items : [])
    } catch {
      setItems([])
    }
  }, [])

  useVisibilityAwareInterval(load, POLL_MS)

  const grouped = useMemo(() => {
    const g = { ep: [], evt: [], mkt: [], gen: [] }
    ;(items || []).forEach(i => g[groupKey(i.scope_type)].push(i))
    return g
  }, [items])

  const loading = items === null

  return (
    <div className="panel-view signals-view">
      <div className="signals-header-row">
        <h2>OSINT &amp; Event Intelligence</h2>
        <button
          className="intel-refresh-btn"
          onClick={load}
          disabled={loading}
          title="Refresh now"
        >
          {loading ? '⟳' : '↻'}
        </button>
        <span className="sig-panel-count" style={{ marginLeft: 'auto' }}>
          Polls every {POLL_MS / 1000}s
        </span>
      </div>
      <p className="sig-subtitle">
        Scored RSS/keyword monitoring — EP/security, upcoming DC-area events
        (conferences, summits), market/brand intelligence, and general OSINT.
        MEDIUM+ score only.
      </p>

      <div className="sig-grid tfr-grid">
        <OsintPanel
          label="EP / SECURITY"
          items={grouped.ep}
          loading={loading}
          emptyText="No EP/security OSINT items"
          highlight
        />
        <OsintPanel
          label="EVENTS"
          items={grouped.evt}
          loading={loading}
          emptyText="No event intel (COS26, conference sweep) today"
        />
        <OsintPanel
          label="MARKET / BRAND"
          items={grouped.mkt}
          loading={loading}
          emptyText="No market/brand intelligence items"
        />
        <OsintPanel
          label="GENERAL"
          items={grouped.gen}
          loading={loading}
          emptyText="No general OSINT items"
        />
      </div>
    </div>
  )
}
