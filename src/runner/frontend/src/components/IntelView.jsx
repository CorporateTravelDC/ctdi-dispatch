import { useState, useEffect, useCallback } from 'react'

const CATEGORIES = [
  { id: 'corporate_intel', label: 'Corporate Intel'     },
  { id: 'marketing_intel', label: 'Marketing Intel'     },
  { id: 'travel_trends',   label: 'Client Travel Trends'},
  { id: 'dc_area',         label: 'DC Area'             },
  { id: 'aviation',        label: 'Aviation'            },
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
  const [expanded, setExpanded] = useState(false)
  return (
    <article
      className={`rss-item${expanded ? ' expanded' : ''}`}
      style={{ animationDelay: `${index * 30}ms` }}
    >
      <div className="rss-item-meta">
        <span className="rss-source">{item.source}</span>
        <span className="rss-ts">{relTime(item.published)}</span>
      </div>
      <h3 className="rss-title">
        <a href={item.link} target="_blank" rel="noopener noreferrer"
           onClick={e => e.stopPropagation()}>
          {item.title}
        </a>
      </h3>
      {item.summary && (
        <>
          <button
            className="rss-expand-btn"
            onClick={() => setExpanded(e => !e)}
            aria-expanded={expanded}
          >
            {expanded ? 'Less ▲' : 'More ▼'}
          </button>
          {expanded && (
            <p className="rss-summary">{item.summary}</p>
          )}
        </>
      )}
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

export default function IntelView() {
  const [category, setCategory] = useState(
    () => localStorage.getItem('rss_category') || 'corporate_intel'
  )
  const [items,   setItems]   = useState([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)
  const [page,    setPage]    = useState(0)
  const PAGE_SIZE = 15

  const loadFeed = useCallback((cat) => {
    setLoading(true)
    setError(null)
    setItems([])
    setPage(0)
    fetch(`/api/rss?category=${encodeURIComponent(cat)}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(data => {
        setItems(data.items || [])
        setLoading(false)
      })
      .catch(e => {
        setError(e.message)
        setLoading(false)
      })
  }, [])

  useEffect(() => {
    try { localStorage.setItem('rss_category', category) } catch {}
    loadFeed(category)
  }, [category, loadFeed])

  const visible = items.slice(0, (page + 1) * PAGE_SIZE)

  return (
    <div className="panel-view intel-view">
      <div className="intel-header">
        <h2>Intelligence Feed</h2>
        <button className="intel-refresh-btn" onClick={() => loadFeed(category)}
                disabled={loading} title="Refresh">
          {loading ? '⟳' : '↻'}
        </button>
      </div>

      {/* Category tabs */}
      <div className="intel-cat-tabs">
        {CATEGORIES.map(c => (
          <button
            key={c.id}
            className={`intel-cat-tab${category === c.id ? ' active' : ''}`}
            onClick={() => setCategory(c.id)}
          >
            {c.label}
          </button>
        ))}
      </div>

      {/* Feed content */}
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
          <button
            className="rss-load-more"
            onClick={() => setPage(p => p + 1)}
          >
            Load more ({items.length - visible.length} remaining)
          </button>
        )}
      </div>
    </div>
  )
}
