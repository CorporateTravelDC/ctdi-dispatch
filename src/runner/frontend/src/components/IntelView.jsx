import { useState, useEffect, useCallback, useRef, useMemo } from 'react'

const CUSTOM_TAB = { id: '__custom__', label: 'My Feeds' }

// Reuses the same localStorage key AdminView.jsx already established as
// "the operator's bearer token" for this app -- added 2026-08-02 so
// Add Feed / Add Category (now auth-gated, see runner/main.py) work for
// real operators without inventing a second token-entry UI. If unset,
// requests go out with no Authorization header at all, which the backend
// correctly resolves to anonymous (tier0) -- unauthenticated writes are
// now rejected with 401 rather than silently failing in a confusing way.
function authHeaders() {
  const token = localStorage.getItem('adminToken') || ''
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

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

// Shared scope selector — used by both Add Feed and Add Category.
// Added 2026-08-02 for the department/multi-operator visibility model
// (see shared/rss_catalog.py's visible_to()). "company" (default) matches
// today's pre-existing behavior — visible to everyone, including
// anonymous callers.
function ScopeSelector({ scope, setScope, department, setDepartment, busy }) {
  return (
    <div className="custom-feed-add-row">
      <select
        className="custom-feed-input custom-feed-scope-select"
        value={scope}
        onChange={e => setScope(e.target.value)}
        disabled={busy}
      >
        <option value="company">Company-wide</option>
        <option value="department">Department</option>
        <option value="personal">Personal (just me)</option>
      </select>
      {scope === 'department' && (
        <input
          className="custom-feed-input"
          type="text"
          placeholder="Department name (e.g. DISPATCH)"
          value={department}
          onChange={e => setDepartment(e.target.value)}
          disabled={busy}
          required
        />
      )}
    </div>
  )
}

// ── Add-category form ────────────────────────────────────────────────────────
// Added 2026-08-02, parallel to Add Feed.
function AddCategoryForm({ onAdd, onCancel }) {
  const [label,      setLabel]      = useState('')
  const [scope,      setScope]      = useState('company')
  const [department, setDepartment] = useState('')
  const [busy,        setBusy]      = useState(false)
  const [err,         setErr]       = useState(null)
  const labelRef = useRef(null)

  useEffect(() => { labelRef.current?.focus() }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    const trimmed = label.trim()
    if (!trimmed) return
    if (scope === 'department' && !department.trim()) {
      setErr('Department name is required for a department-scoped category.')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const r = await fetch('/api/rss/categories', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ label: trimmed, scope, department: department.trim() || undefined }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`)
      onAdd(body.category)
    } catch (e) {
      setErr(e.message)
      setBusy(false)
    }
  }

  return (
    <form className="custom-feed-add-form" onSubmit={handleSubmit}>
      <div className="custom-feed-add-row">
        <input
          ref={labelRef}
          className="custom-feed-input"
          type="text"
          placeholder="Category name (e.g. AAM, Supply Chain, Oil)"
          value={label}
          onChange={e => setLabel(e.target.value)}
          required
          disabled={busy}
        />
      </div>
      <ScopeSelector scope={scope} setScope={setScope}
                     department={department} setDepartment={setDepartment} busy={busy} />
      {err && <p className="custom-feed-err">{err}</p>}
      <div className="custom-feed-add-actions">
        <button type="submit" className="ntfy-ctrl-btn" disabled={busy || !label.trim()}>
          {busy ? 'Saving…' : 'Add category'}
        </button>
        <button type="button" className="rss-expand-btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  )
}

// ── Add-feed form ─────────────────────────────────────────────────────────────
function AddFeedForm({ onAdd, onCancel, categories }) {
  const [name,          setName]          = useState('')
  const [url,           setUrl]           = useState('')
  const [categoryInput, setCategoryInput] = useState('')
  const [scope,         setScope]         = useState('company')
  const [department,    setDepartment]    = useState('')
  const [busy,          setBusy]          = useState(false)
  const [err,           setErr]           = useState(null)
  const [resolving,     setResolving]     = useState(false)
  const [resolveNote,   setResolveNote]   = useState(null)
  const urlRef = useRef(null)

  useEffect(() => { urlRef.current?.focus() }, [])

  // Detect feed URL from a pasted channel/blog link -- YouTube (@handle,
  // /c/, /user/, /channel/), Rumble (/c/, /user/), or native RSS/Atom
  // autodiscovery on any other page. See shared/feed_resolve.py for the
  // three strategies and the known Rumble/Cloudflare limitation (the
  // resolved URL is still filled in even when Rumble's own bot protection
  // is currently blocking the scrape -- see the note shown below the
  // field, which explains why in that case).
  const handleResolve = async () => {
    const trimmed = url.trim()
    if (!trimmed) return
    setResolving(true)
    setResolveNote(null)
    try {
      const r = await fetch('/api/rss/resolve-source', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: trimmed }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`)
      if (body.resolved && body.feed_url) {
        setUrl(body.feed_url)
      }
      setResolveNote(body.note || null)
    } catch (e) {
      setResolveNote(`Detect failed: ${e.message}`)
    } finally {
      setResolving(false)
    }
  }

  // Resolve typed label to a category id. Matches by id or label
  // (case-insensitive) against every category the caller can currently
  // see (built-in + visible user categories, see IntelView's categories
  // state, fetched from GET /api/rss/categories). Falls back to
  // "__custom__" for empty input -- no longer falls back to an arbitrary
  // raw string, since the backend only accepts ids from that same list
  // (Add Category is now the one way to make a brand-new category, this
  // form only ever assigns an existing one).
  const resolveCategory = (input) => {
    const trimmed = input.trim()
    if (!trimmed) return '__custom__'
    const match = categories.find(
      c => c.id === trimmed || c.label.toLowerCase() === trimmed.toLowerCase()
    )
    return match ? match.id : '__custom__'
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    const trimUrl  = url.trim()
    if (!trimUrl) return
    if (scope === 'department' && !department.trim()) {
      setErr('Department name is required for a department-scoped feed.')
      return
    }
    setBusy(true)
    setErr(null)
    const category = resolveCategory(categoryInput)
    try {
      const r = await fetch('/api/rss/user-feeds', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          name: name.trim(), url: trimUrl, category,
          scope, department: department.trim() || undefined,
        }),
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
          placeholder="Paste a feed URL, a YouTube channel, a Rumble channel, or a blog homepage"
          value={url}
          onChange={e => { setUrl(e.target.value); setResolveNote(null) }}
          required
          disabled={busy}
        />
        <button
          type="button"
          className="rss-expand-btn"
          onClick={handleResolve}
          disabled={busy || resolving || !url.trim()}
          title="Turn a YouTube/Rumble/blog link into an actual feed URL"
        >
          {resolving ? 'Detecting…' : 'Detect feed'}
        </button>
      </div>
      {resolveNote && <p className="custom-feed-resolve-note">{resolveNote}</p>}
      <div className="custom-feed-add-row">
        <input
          className="custom-feed-input custom-feed-name-input"
          type="text"
          placeholder="Label (optional)"
          value={name}
          onChange={e => setName(e.target.value)}
          disabled={busy}
        />
        {/* Pick an existing category by name — creating a new one now
            happens via the separate Add Category form/button. */}
        <input
          className="custom-feed-input custom-feed-cat-select"
          type="text"
          list="intel-category-options"
          placeholder="Category (optional)"
          value={categoryInput}
          onChange={e => setCategoryInput(e.target.value)}
          disabled={busy}
        />
        <datalist id="intel-category-options">
          {categories.map(c => (
            <option key={c.id} value={c.label} />
          ))}
        </datalist>
      </div>
      <ScopeSelector scope={scope} setScope={setScope}
                     department={department} setDepartment={setDepartment} busy={busy} />
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

// ── My Feeds manager (shown in My Feeds tab) ──────────────────────────────────
function MyFeedsManager({ userFeeds, categories, onFeedRemoved, onFeedAdded, onCategoryAdded }) {
  const [showAddFeed, setShowAddFeed] = useState(false)
  const [showAddCat,  setShowAddCat]  = useState(false)

  const handleAddFeed = (feed) => {
    onFeedAdded(feed)
    setShowAddFeed(false)
  }

  const handleAddCat = (cat) => {
    onCategoryAdded(cat)
    setShowAddCat(false)
  }

  const handleRemove = async (feed) => {
    try {
      const r = await fetch(`/api/rss/user-feeds/${feed.id}`, { method: 'DELETE', headers: authHeaders() })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${r.status}`)
      }
      onFeedRemoved(feed.id)
    } catch (e) {
      // Surfaced inline rather than a toast — ownership-denied deletes
      // (403, see runner/main.py's user_feeds_delete) are common enough
      // now that department/personal feeds exist that silent failure
      // would be confusing.
      alert(`Could not remove feed: ${e.message}`)  // eslint-disable-line no-alert
    }
  }

  // Group by category for display
  const grouped = {}
  userFeeds.forEach(f => {
    const cat = f.category || '__custom__'
    if (!grouped[cat]) grouped[cat] = []
    grouped[cat].push(f)
  })

  const catLabel = (id) => {
    const match = categories.find(c => c.id === id)
    if (match) return match.label
    if (id === '__custom__') return 'Uncategorized'
    return id
  }

  return (
    <div className="custom-feeds-mgr">
      <div className="custom-feeds-mgr-header">
        <span className="custom-feeds-mgr-title">My Feeds</span>
        <span className="muted" style={{ fontSize: '0.65rem' }}>
          {userFeeds.length} feed{userFeeds.length !== 1 ? 's' : ''}
        </span>
      </div>

      {userFeeds.length === 0 && !showAddFeed && (
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
              {f.scope && f.scope !== 'company' && (
                <span className="custom-feed-scope-badge" title={f.scope === 'department' ? `Department: ${f.department}` : 'Personal feed'}>
                  {f.scope === 'department' ? f.department : 'Personal'}
                </span>
              )}
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

      {showAddFeed && (
        <AddFeedForm
          onAdd={handleAddFeed}
          onCancel={() => setShowAddFeed(false)}
          categories={categories}
        />
      )}
      {showAddCat && (
        <AddCategoryForm
          onAdd={handleAddCat}
          onCancel={() => setShowAddCat(false)}
        />
      )}
      {!showAddFeed && !showAddCat && (
        <div className="custom-feed-add-actions">
          <button className="custom-feed-add-btn" onClick={() => setShowAddFeed(true)}>
            + Add feed
          </button>
          <button className="custom-feed-add-btn" onClick={() => setShowAddCat(true)}>
            + Add category
          </button>
        </div>
      )}
    </div>
  )
}

// ── Custom tab content: manager + items tagged __custom__ ─────────────────────
function CustomTabView({ userFeeds, categories, onFeedRemoved, onFeedAdded, onCategoryAdded }) {
  const [items,   setItems]   = useState([])
  const [loading, setLoading] = useState(false)
  const [page,    setPage]    = useState(0)
  const PAGE_SIZE = 15

  const uncategorizedFeeds = userFeeds.filter(f => (f.category || '__custom__') === '__custom__')

  const fetchCustom = useCallback(async () => {
    if (!uncategorizedFeeds.length) { setItems([]); return }
    setLoading(true)
    const r = await fetch('/api/rss?category=__custom__', { headers: authHeaders() }).catch(() => null)
    if (r?.ok) {
      const data = await r.json()
      setItems(data.items || [])
    }
    setLoading(false)
  }, [uncategorizedFeeds.length])  // eslint-disable-line

  useEffect(() => {
    setPage(0)
    fetchCustom()
  }, [fetchCustom])

  const visible = items.slice(0, (page + 1) * PAGE_SIZE)

  return (
    <div className="custom-feeds-view">
      <MyFeedsManager
        userFeeds={userFeeds}
        categories={categories}
        onFeedRemoved={onFeedRemoved}
        onFeedAdded={(feed) => { onFeedAdded(feed); fetchCustom() }}
        onCategoryAdded={onCategoryAdded}
      />

      {uncategorizedFeeds.length > 0 && (
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
  const [category,   setCategory]   = useState(
    () => localStorage.getItem('rss_category') || 'corporate_intel'
  )
  const [items,       setItems]       = useState([])
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState(null)
  const [page,        setPage]        = useState(0)
  const [userFeeds,   setUserFeeds]   = useState([])
  const [categories,  setCategories]  = useState([])
  const PAGE_SIZE = 15

  // Load user feeds + categories from backend on mount.
  // UPDATED 2026-08-02: categories are now fetched from the backend
  // (GET /api/rss/categories, identity-filtered for department/personal
  // visibility) instead of a hardcoded frontend array -- see
  // shared/rss_catalog.py's list_all_categories(). Both calls send
  // authHeaders() so a caller with a department/personal token sees their
  // own categories and feeds immediately, not just company-wide ones.
  useEffect(() => {
    fetch('/api/rss/user-feeds', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : { feeds: [] })
      .then(d => setUserFeeds(d.feeds || []))
      .catch(() => {})
    fetch('/api/rss/categories', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : { categories: [] })
      .then(d => setCategories(d.categories || []))
      .catch(() => {})
  }, [])

  const handleFeedAdded     = (feed) => setUserFeeds(prev => [...prev, feed])
  const handleFeedRemoved   = (id)   => setUserFeeds(prev => prev.filter(f => f.id !== id))
  const handleCategoryAdded = (cat)  => setCategories(prev => [...prev, cat])

  // Build dynamic tabs: fetched categories + any orphan category strings
  // still on a feed but not (yet) in the categories list (shouldn't
  // normally happen now that Add Feed only assigns existing categories,
  // but stays defensive for feeds saved before this change) + My Feeds hub.
  const allCategories = useMemo(() => {
    const seen = new Set(categories.map(c => c.id))
    seen.add('__custom__')
    const orphan = []
    userFeeds.forEach(f => {
      const cat = f.category || '__custom__'
      if (!seen.has(cat)) {
        seen.add(cat)
        orphan.push({ id: cat, label: cat })
      }
    })
    return [...categories, ...orphan, CUSTOM_TAB]
  }, [categories, userFeeds])

  const loadFeed = useCallback((cat) => {
    if (cat === '__custom__') return
    setLoading(true)
    setError(null)
    setItems([])
    setPage(0)
    fetch(`/api/rss?category=${encodeURIComponent(cat)}`, { headers: authHeaders() })
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
        {allCategories.map(c => (
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
          categories={categories}
          onFeedAdded={handleFeedAdded}
          onFeedRemoved={handleFeedRemoved}
          onCategoryAdded={handleCategoryAdded}
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
