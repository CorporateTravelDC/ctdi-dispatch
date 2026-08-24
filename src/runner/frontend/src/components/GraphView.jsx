import { useState, useEffect, useCallback } from 'react'

// Second-brain knowledge graph -- iframes the self-contained,
// canvas-rendered viz served by GET /api/v1/knowledge-graph/html (see
// src/second_brain/knowledge_graph/build_graph.py). Not reimplemented in
// React: the standalone HTML is already a complete, tested visualization
// with its own pan/zoom/search/click-to-Nextcloud behavior -- iframing it
// reuses all of that instead of porting ~400 lines of canvas rendering.
export default function GraphView() {
  const [meta, setMeta] = useState(null)
  const [metaErr, setMetaErr] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const loadMeta = useCallback(async () => {
    try {
      const r = await fetch('/api/dispatch/api/v1/knowledge-graph/meta')
      if (!r.ok) throw new Error(r.status)
      setMeta(await r.json())
      setMetaErr(false)
    } catch {
      setMeta(null)
      setMetaErr(true)
    }
  }, [])

  useEffect(() => { loadMeta() }, [loadMeta])

  const fmtAge = iso => {
    if (!iso) return ''
    try {
      const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
      if (diff < 60) return `${diff}m ago`
      if (diff < 1440) return `${Math.floor(diff / 60)}h ago`
      return `${Math.floor(diff / 1440)}d ago`
    } catch { return '' }
  }

  return (
    <div className="panel-view signals-view">
      <div className="signals-header-row">
        <h2>Second-Brain Knowledge Graph</h2>
        <button
          className="intel-refresh-btn"
          onClick={() => { loadMeta(); setReloadKey(k => k + 1) }}
          title="Reload"
        >
          ↻
        </button>
        <span className="sig-panel-count" style={{ marginLeft: 'auto' }}>
          {meta
            ? `${meta.node_count} notes · ${meta.edge_count} links · built ${fmtAge(meta.generated_at)}`
            : metaErr ? 'not built yet' : 'loading…'}
        </span>
      </div>
      <p className="sig-subtitle">
        Vault notes as nodes, real [[wikilinks]] as edges. Click a node for
        details and a direct link into Nextcloud at that file — also lives
        in the vault itself at 04-Syntheses/vault-graph.html.
      </p>

      {metaErr ? (
        <div className="sig-empty" style={{ padding: '2rem' }}>
          Graph hasn't been built yet. Run{' '}
          <code>python3 -m second_brain.knowledge_graph.build_graph</code>{' '}
          on the box.
        </div>
      ) : (
        <iframe
          key={reloadKey}
          title="Second-brain knowledge graph"
          src="/api/dispatch/api/v1/knowledge-graph/html"
          style={{
            width: '100%',
            height: 'calc(100vh - 220px)',
            border: '1px solid var(--border, #333)',
            borderRadius: '8px',
            background: '#fff',
          }}
        />
      )}
    </div>
  )
}
