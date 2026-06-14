import { useState, useEffect, useCallback, useRef } from 'react'

const CATALOG_CATEGORIES = [
  { id: 'corporate_intel', label: 'Corporate Intel'     },
  { id: 'marketing_intel', label: 'Marketing Intel'     },
  { id: 'travel_trends',   label: 'Client Travel Trends'},
  { id: 'dc_area',         label: 'DC Area'             },
  { id: 'aviation',        label: 'Aviation'            },
]

const ALL_CATEGORIES = [
  ...CATALOG_CATEGORIES,
  { id: '__custom__', label: 'Custom' },
]

function relTime(dateStr) {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    if (isNaN(d)) return dateStr.slice(0, 16)
    const diff = Math.floor((Date.now() - d.getTime()) / 1000)
    if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch { return '' }
}

function RssItem({ item, index }) {
  const [expanded,  setExpanded]  = useState(false)
  const [showAudio, setShowAudio] = useState(false)
  const isPodcast = !!item.audio_url
  return (
    <article
      className={`rss-item${expanded ? ' expanded' : ''}${isPodcast ? ' podcast-item' : ''}`}
      style={{ animationDelay: `${index * 30}ms` }}
    >
      <div className="rss-item-meta">
        <span className="rss-source">
          {isPodcast && <span className="podcast-badge" title="Podcast episode">▶</span>}
          {item.source}
        </span>
        <span className="rss-ts">{relTime(item.published)}</span>
      </div>
      <h3 className="rss-title">
        <a href={item.link} target="_blank" rel="noopener noreferrer"
           onClick={e => e.stopPropagation()}>
          {item.title}
        </a>
      </h3>
      <div className="rss-item-actions">
        {isPodcast && (
          <button
            className={`rss-expand-btn podcast-play-btn${showAudio ? ' active' : ''}`}
            onClick={() => setShowAudio(v => !v)}
          >
            {showAudio ? '▼ Hide player' : '▶ Play'}
          </button>
        )}
        {item.summary && (
          <button
            className="rss-expand-btn"
            onClick={() => setExpanded(e => !e)}
            aria-expanded={expanded}
          >
            {expanded ? 'Less ▲' : 'More ▼'}
          </button>
        )}
      </div>
      {showAudio && item.audio_url && (
        <audio
          className="podcast-player"
          controls
          preload="none"
          src={item.audio_url}
        />
      )}
      {expanded && item.summary && <p className="rss-summary">{item.summary}</p>}
    </article>
  )
}

function RssSkeleton() {
  return (
    <div className="rss-skeleton-list">
      {[...Array(6)].map((_, i) => (
        <div key={i} className="rss-skeleton-item">
          <div className="rss-skeleton-line short" />
          <div className="rss-skeleton-line long" />
          <div className="rss-skeleton-line medium" />
        </div>
      ))}
    </div>
  )
}

// ── Add-feed form ─────────────────────────────────────────────────────────────
function AddFeedForm({ onAdd, onCancel }) {
  const [name,     setName]     = useState('')
  const [url,      setUrl]      = useState('')
  const [category, setCategory] = useState('__custom__')
  const [busy,     setBusy]     = useState(false)
  const [err,      setErr]      = useState(null)
  const urlRef = useRef(null)

  useEffect(() => { urlRef.current?.focus() }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    const trimUrl = url.trim()
    if (!trimUrl) return
    setBusy(true)
    setErr(null)
    try {
      const r = await fetch('/api/rss/user-feeds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), url: trimUrl, category }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`)
      onAdd(body.feed)
    } catch (e) {
      setErr(e.message)
      setBusy(false)
    }
  }

  return (
    <form className="custom-feed-add-form" onSubmit={handleSubmit}>
      <div className="custom-feed-add-row">
        <input
          ref={urlRef}
          className="custom-feed-input"
          type="url"
          placeholder="https://example.com/feed.rss  ·  or youtube.com/feeds/videos.xml?channel_id=UC…"
          value={url}
          onChange={e => setUrl(e.target.value)}
          required
          disabled={busy}
        />
      </div>
      <div className="custom-feed-add-row">
        <input
          className="custom-feed-input custom-feed-name-input"
          type="text"
          placeholder="Label (optional)"
          value={name}
          onChange={e => setName(e.target.value)}
          disabled={busy}
        />
        <select
          className="custom-feed-input custom-feed-cat-select"
          value={category}
          onChange={e => setCategory(e.target.value)}
          disabled={busy}
        >
          {CATALOG_CATEGORIES.map(c => (
            <option key={c.id} value={c.id}>{c.label}</option>
          ))}
          <option value="__custom__">Custom only</option>
        </select>
      </div>
      {err && <p className="custom-feed-err">{err}</p>}
      <div className="custom-feed-add-actions">
        <button type="submit" className="ntfy-ctrl-btn" disabled={busy || !url.trim()}>
          {busy ? 'Validating…' : 'Add feed'}
        </button>
        <button type="button" className="rss-expand-btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  )
}

// ── My Feeds manager (shown in Custom tab) ────────────────────────────────────
function MyFeedsManager({ userFeeds, onFeedRemoved, onFeedAdded }) {
  const [showAdd, setShowAdd] = useState(false)

  const handleAdd = (feed) => {
    onFeedAdded(feed)
    setShowAdd(false)
  }

  const handleRemove = async (feed) => {
    try {
      await fetch(`/api/rss/user-feeds/${feed.id}`, { method: 'DELETE' })
      onFeedRemoved(feed.id)
    } catch {}
  }

  // Group by category for display
  const grouped = {}
  userFeeds.forEach(f => {
    const cat = f.category || '__custom__'
    if (!grouped[cat]) grouped[cat] = []
    grouped[cat].push(f)
  })

  const catLabel = (id) =>
    ALL_CATEGORIES.find(c => c.id === id)?.label || id

  return (
    <div className="custom-feeds-mgr">
      <div className="custom-feeds-mgr-header">
        <span className="custom-feeds-mgr-title">My Feeds</span>
        <span className="muted" style={{ fontSize: '0.65rem' }}>
          {userFeeds.length} feed{userFeeds.length !== 1 ? 's' : ''}
        </span>
      </div>

      {userFeeds.length === 0 && !showAdd && (
        <p className="muted rss-empty" style={{ padding: '0.25rem 0' }}>
          No custom feeds yet. Add RSS, podcast, or YouTube channel feeds below.
        </p>
      )}

      {Object.entries(grouped).map(([cat, feeds]) => (
        <div key={cat} className="custom-feeds-group">
          <div className="custom-feeds-group-label">{catLabel(cat)}</div>
          {feeds.map(f => (
            <div key={f.id} className="custom-feed-row">
              <span className="custom-feed-row-name">{f.name}</span>
              <span className="custom-feed-row-url muted">{f.url}</span>
              <button
                className="custom-feed-remove"
                onClick={() => handleRemove(f)}
                title="Remove feed"
                aria-label={`Remove ${f.name}`}
              >✕</button>
            </div>
          ))}
        </div>
      ))}

      {showAdd
        ? <AddFeedForm onAdd={handleAdd} onCancel={() => setShowAdd(false)} />
        : (
          <button className="custom-feed-add-btn" onClick={() => setShowAdd(true)}>
            + Add feed
          </button>
        )
      }
    </div>
  )
}

// ── Custom tab content: manager + items tagged __custom__ ─────────────────────
function CustomTabView({ userFeeds, onFeedRemoved, onFeedAdded }) {
  const [items,   setItems]   = useState([])
  const [loading, setLoading] = useState(false)
  const [page,    setPage]    = useState(0)
  const PAGE_SIZE = 15

  const customFeeds = userFeeds.filter(f => f.category === '__custom__')

  const fetchCustom = useCallback(async () => {
    if (!customFeeds.length) { setItems([]); return }
    setLoading(true)
    const r = await fetch('/api/rss?category=__custom__').catch(() => null)
    if (r?.ok) {
      const data = await r.json()
      setItems(data.items || [])
    }
    setLoading(false)
  }, [customFeeds.length])  // eslint-disable-line

  useEffect(() => {
    setPage(0)
    fetchCustom()
  }, [fetchCustom])

  const visible = items.slice(0, (page + 1) * PAGE_SIZE)

  return (
    <div className="custom-feeds-view">
      <MyFeedsManager
        userFeeds={userFeeds}
        onFeedRemoved={onFeedRemoved}
        onFeedAdded={(feed) => { onFeedAdded(feed); fetchCustom() }}
      />

      {customFeeds.length > 0 && (
        <div className="rss-feed">
          {loading && <RssSkeleton />}
          {!loading && items.length === 0 && (
            <div className="muted rss-empty">No items yet — feeds may be loading.</div>
          )}
          {!loading && visible.map((item, i) => (
            <RssItem key={`${item.link}-${i}`} item={item} index={i} />
          ))}
          {!loading && visible.length < items.length && (
            <button className="rss-load-more" onClick={() => setPage(p => p + 1)}>
              Load more ({items.length - visible.length} remaining)
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Root view ─────────────────────────────────────────────────────────────────
export default function IntelView() {
  const [category,  setCategory]  = useState(
    () => localStorage.getItem('rss_category') || 'corporate_intel'
  )
  const [items,     setItems]     = useState([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState(null)
  const [page,      setPage]      = useState(0)
  const [userFeeds, setUserFeeds] = useState([])
  const PAGE_SIZE = 15

  // Load user feeds from backend on mount
  useEffect(() => {
    fetch('/api/rss/user-feeds')
      .then(r => r.ok ? r.json() : { feeds: [] })
      .then(d => setUserFeeds(d.feeds || []))
      .catch(() => {})
  }, [])

  const handleFeedAdded   = (feed) => setUserFeeds(prev => [...prev, feed])
  const handleFeedRemoved = (id)   => setUserFeeds(prev => prev.filter(f => f.id !== id))

  const loadFeed = useCallback((cat) => {
    if (cat === '__custom__') return
    setLoading(true)
    setError(null)
    setItems([])
    setPage(0)
    fetch(`/api/rss?category=${encodeURIComponent(cat)}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(data => { setItems(data.items || []); setLoading(false) })
      .catch(e  => { setError(e.message); setLoading(false) })
  }, [])

  useEffect(() => {
    try { localStorage.setItem('rss_category', category) } catch {}
    loadFeed(category)
  }, [category, loadFeed])

  const visible   = items.slice(0, (page + 1) * PAGE_SIZE)
  const isCustom  = category === '__custom__'
  const customCnt = userFeeds.length

  return (
    <div className="panel-view intel-view">
      <div className="intel-header">
        <h2>Intelligence Feed</h2>
        {!isCustom && (
          <button className="intel-refresh-btn" onClick={() => loadFeed(category)}
                  disabled={loading} title="Refresh">
            {loading ? '⟳' : '↻'}
          </button>
        )}
      </div>

      <div className="intel-cat-tabs">
        {ALL_CATEGORIES.map(c => (
          <button
            key={c.id}
            className={`intel-cat-tab${category === c.id ? ' active' : ''}${c.id === '__custom__' ? ' custom-tab' : ''}`}
            onClick={() => setCategory(c.id)}
          >
            {c.label}{c.id === '__custom__' && customCnt > 0 ? ` (${customCnt})` : ''}
          </button>
        ))}
      </div>

      {isCustom ? (
        <CustomTabView
          userFeeds={userFeeds}
          onFeedAdded={handleFeedAdded}
          onFeedRemoved={handleFeedRemoved}
        />
      ) : (
        <div className="rss-feed">
          {loading && <RssSkeleton />}
          {error && (
            <div className="rss-error">
              <p>Could not load feed: {error}</p>
              <button className="ntfy-ctrl-btn" onClick={() => loadFeed(category)}>Retry</button>
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="muted rss-empty">No items found. Feeds may be temporarily unavailable.</div>
          )}
          {!loading && !error && visible.map((item, i) => (
            <RssItem key={`${item.link}-${i}`} item={item} index={i} />
          ))}
          {!loading && !error && visible.length < items.length && (
            <button className="rss-load-more" onClick={() => setPage(p => p + 1)}>
              Load more ({items.length - visible.length} remaining)
            </button>
          )}
        </div>
      )}
    </div>
  )
}
