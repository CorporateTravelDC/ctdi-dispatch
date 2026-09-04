"""
second_brain.knowledge_graph.build_graph -- build the vault knowledge graph
from real [[wikilink]] structure.

Model (settled 2026-08-11 after the framing correction): Karpathy's LLM-Wiki
method already has native graph logic -- notes cross-reference each other
with [[wikilinks]] and Obsidian's Graph View renders the backlink network.
The vault's ingestion pipeline had only ever implemented the filed-markdown
half of that pattern (pre-retrofit census: see meta.organic_links), so the
companion module retrofit_links.py first RETROFITS genuine links into the
content (entity hub notes in 03-Entities/ + marked per-note link footers).
This builder then does the faithful thing: nodes are notes, edges are the
actual [[wikilinks]] now present in the markdown -- no invented relationship
types. Edges carry origin="organic" (link appears above the retrofit
marker, i.e. written by a human/agent in the note body) or
origin="retrofit" (added by retrofit_links.py), so the visualization can
show which structure was native and which was backfilled.

Why a standalone HTML viz when Obsidian Graph View exists: the vault is
WebDAV-hosted and consumed by agents and the operator's browser, not a
desktop Obsidian instance; the standalone page needs no app, works over any
static channel, and can be regenerated on a timer -- same rationale as the
demo-archiver giving visual/longitudinal lookup over otherwise
event-by-event-buried data.

Location decision: code AND outputs live in the repo at
src/second_brain/knowledge_graph/ (not in the vault) because (a) the
builder must be re-runnable/diffable/reviewable like any other second-brain
module, (b) the HTML is a build artifact of repo code, and (c)
webdav_client has no delete, so iterating on generated files inside the
vault would strand stale copies. Pushing a rendered copy into the vault is
a one-line webdav_client.put() if wanted -- deliberately not automatic.

Access pattern (corrected 2026-09-03, see enumerate_vault()'s own comment
for the full root-cause): this module was only ever run by hand before
2026-09-03, always outside a container, where 127.0.0.1:8090 genuinely
reaches Nextcloud's loopback-bound WebDAV port directly. That path is
structurally unreachable from inside a container (the port is strictly
loopback-bound on the host, under any pasta flag) -- when actually
scheduled as a container (corporatetraveldc-knowledge-graph-compile.
container), it must go through the nginx vhost like every other
containerized second-brain script does (host.containers.internal:80,
Host-header spoofed to cloud.example.com, same pattern as
corporatetraveldc-second-brain-daily.container). The vhost's real
"extra business-root path segment" requirement is a trailing slash on
the bare business-root PROPFIND -- omit it and nginx 301s to the public
https:// URL regardless of which host/port the request arrived on,
which is what looked like "the public vhost is broken" before this was
actually run in the environment it's scheduled to run in. Credentials
come from webdav_client._auth(), i.e. the NEXTCLOUD_ADMIN_USER /
NEXTCLOUD_APP_PASSWORD pattern backed by
/etc/corporatetraveldc/dispatch-secrets.env. This module is read-only
(PROPFIND + GET); only retrofit_links.py writes.

Scope: every markdown note under the business root EXCEPT 00-Inbox/rss/
(3,300+ hash-named untriaged feed items -- not wiki content until triaged
into the garden), notepad/processed/ (archived duplicates of notes already
in the graph), and .internal-backups/.

Usage (env comes from dispatch.env/dispatch-secrets.env as usual):
    python3 -m second_brain.knowledge_graph.build_graph [--out DIR] [--json-only]

Outputs (default DIR = this package's directory):
    graph.json        nodes/edges/meta as structured data
    vault-graph.html  self-contained interactive viz (template + inline data)
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

from second_brain import webdav_client
from second_brain.index_db import INDEX_DB
from second_brain.knowledge_graph.lexicon import SUBTYPE_OF

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(_PKG_DIR, "viz_template.html")

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MARKER = "<!-- auto-wikilinks"
HUB_DIR = "03-Entities"
RSS_PREFIX = "00-Inbox/rss/"
SKIP_PREFIXES = (".internal-backups/", RSS_PREFIX,
                 "06-AI-Memory/notepad/processed/")
MD_EXT = ".md"


# ── WebDAV enumeration / fetch (read-only, localhost) ─────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.auth = webdav_client._auth()
    s.headers["Host"] = webdav_client.HOST_HEADER
    a = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8)
    s.mount("http://", a)
    return s


def enumerate_vault(sess: requests.Session) -> list[str]:
    """All file paths (relative to BUSINESS_ROOT) via one Depth:infinity
    PROPFIND -- the workaround path described in the module docstring.

    2026-09-03: root-caused the module docstring's "extra business-root
    path segment" bug precisely, while wiring the first-ever scheduled
    run of this script (previously only ever invoked by hand). It's a
    missing TRAILING SLASH: PROPFINDing the bare business-root path (no
    trailing slash) makes nginx's cloud.example.com vhost
    issue a 301 to the canonical public https:// URL regardless of which
    host/port the request actually arrived on (confirmed live: same 301
    whether hit via the public domain or host.containers.internal) --
    and requests' default allow_redirects=True follows it transparently,
    landing on a URL/auth context that then 401s. A trailing slash routes
    correctly with no redirect at all (confirmed live: 207 Multi-Status).
    fetch_all() below never hit this because it always PROPFINDs/GETs a
    real sub-path, never the bare root."""
    url = f"{webdav_client._base_url()}/{webdav_client.BUSINESS_ROOT}/"
    body = ('<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop>'
            '</d:propfind>')
    r = sess.request("PROPFIND", url, data=body, timeout=120,
                     headers={"Content-Type": "application/xml",
                              "Depth": "infinity"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    marker = (f"/remote.php/dav/files/{webdav_client.NEXTCLOUD_USER}/"
              f"{webdav_client.BUSINESS_ROOT}/")
    paths = []
    for resp in root.findall("{DAV:}response"):
        href = resp.find("{DAV:}href")
        rt = resp.find(".//{DAV:}resourcetype")
        is_dir = rt is not None and rt.find("{DAV:}collection") is not None
        if href is None or href.text is None or is_dir:
            continue
        h = requests.utils.unquote(href.text)
        if marker in h:
            paths.append(h.split(marker, 1)[1])
    return paths


def fetch_all(sess: requests.Session, rel_paths: list[str]) -> dict[str, str]:
    from concurrent.futures import ThreadPoolExecutor
    base = f"{webdav_client._base_url()}/{webdav_client.BUSINESS_ROOT}"

    def _one(p: str) -> tuple[str, str]:
        r = sess.get(f"{base}/{requests.utils.quote(p)}", timeout=30)
        if r.status_code != 200:
            return p, ""
        return p, r.content.decode("utf-8", "replace")

    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for p, text in ex.map(_one, rel_paths):
            if text:
                out[p] = text
    return out


# ── Extraction helpers ────────────────────────────────────────────────────────

def note_title(path: str, text: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()[:80]
    return os.path.splitext(os.path.basename(path))[0]


def _frontmatter_field(text: str, key: str) -> str | None:
    m = re.search(rf"^{key}:\s*(.+)$", text[:2000], re.MULTILINE)
    return m.group(1).strip() if m else None


def _norm_target(raw: str) -> str:
    t = raw.strip().split("/")[-1]
    return re.sub(r"\.md$", "", t, flags=re.IGNORECASE).lower()


# ── Semantic layer overlay: concept nodes + concept/note edges ────────────────
# 2026-08-18: the semantic layer (second_brain.semantic, built independently
# on branch semantic-layer-2026-08-18) computes its own concept vocabulary
# and per-note concept assignments, materialized into the same index DB this
# module already knows how to read (INDEX_DB). This overlay folds that in as
# additional nodes/edges on the SAME graph the wikilink structure already
# produces, so the visualization and every consumer of graph.json see one
# graph, not two disconnected ones. Read-only (sqlite3 + second_brain.semantic
# .model.load()); does not touch the semantic layer's own compiled artifacts.

def _load_semantic(existing_note_ids: set[str]) -> tuple[dict[str, dict], list[dict]]:
    """Returns (concept_nodes, concept_edges) to merge into build()'s graph.
    concept_nodes keyed like nodes (concept:<id>); concept_edges include both
    concept-to-concept (broader/related) and note-to-concept (tagged) edges.
    Silently returns (empty) if the semantic layer isn't available/compiled
    yet -- this overlay is additive, never required for the base graph."""
    try:
        from second_brain.semantic.model import load as load_semantic
    except ImportError:
        return {}, []

    try:
        model = load_semantic()
    except Exception:
        return {}, []

    concept_nodes: dict[str, dict] = {}
    concept_edges: list[dict] = []
    for c in model.concepts():
        cid = f"concept:{c.id}"
        concept_nodes[cid] = {
            "id": cid, "label": c.pref_label,
            "type": "concept", "subtype": c.facet,
            "definition": getattr(c, "definition", None),
        }
        if getattr(c, "broader", None):
            concept_edges.append({
                "source": cid, "target": f"concept:{c.broader}",
                "type": "broader_than", "origin": "semantic", "weight": 1,
            })
        for rel in getattr(c, "related", None) or []:
            concept_edges.append({
                "source": cid, "target": f"concept:{rel}",
                "type": "related_to", "origin": "semantic", "weight": 1,
            })
        if getattr(c, "in_domain", None):
            concept_edges.append({
                "source": cid, "target": f"concept:{c.in_domain}",
                "type": "in_domain", "origin": "semantic", "weight": 1,
            })

    # note -> concept assignments, restricted to notes already present as
    # graph nodes from the wikilink pass (keeps one consistent node universe
    # -- same excluded-path posture as SKIP_PREFIXES above).
    try:
        conn = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT path, concept_id, facet, rule FROM semantic_note_concepts"
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        rows = []

    prefix = f"{webdav_client.BUSINESS_ROOT}/"
    tag_acc: dict[tuple, dict] = {}
    for path, concept_id, facet, rule in rows:
        if not path.startswith(prefix):
            continue
        note_id = f"note:{path[len(prefix):]}"
        if note_id not in existing_note_ids:
            continue
        cid = f"concept:{concept_id}"
        if cid not in concept_nodes:
            continue  # concept referenced by an assignment but not in the
            # current ontology (e.g. stale/backlog tag) -- skip rather than
            # invent a node for it.
        key = (note_id, cid)
        if key in tag_acc:
            tag_acc[key]["weight"] += 1
        else:
            tag_acc[key] = {
                "source": note_id, "target": cid, "type": "concept_tag",
                "origin": "semantic", "facet": facet, "rule": rule, "weight": 1,
            }
    concept_edges.extend(tag_acc.values())

    return concept_nodes, concept_edges


# ── Graph assembly: nodes = notes, edges = real wikilinks ─────────────────────

def build(paths: list[str], contents: dict[str, str]) -> dict:
    curated = [p for p in paths if p.endswith(MD_EXT)
               and not p.startswith(SKIP_PREFIXES)]

    nodes: dict[str, dict] = {}
    # resolution index: lowered basename and lowered H1 title -> node id
    resolve: dict[str, str] = {}
    for p in curated:
        text = contents.get(p, "")
        nid = f"note:{p}"
        is_hub = p.startswith(f"{HUB_DIR}/")
        label = note_title(p, text)
        if is_hub:
            subtype = (_frontmatter_field(text, "entity_type")
                       or SUBTYPE_OF.get(label, "entity"))
        else:
            subtype = p.split("/", 1)[0]
        nodes[nid] = {
            "id": nid, "label": label,
            "type": "hub" if is_hub else "note",
            "subtype": subtype,
            "source_file": f"{webdav_client.BUSINESS_ROOT}/{p}",
        }
        resolve.setdefault(_norm_target(os.path.basename(p)), nid)
        resolve.setdefault(label.lower(), nid)

    # edges from actual [[wikilinks]]; position vs the retrofit marker
    # decides origin (organic wiki structure vs backfilled)
    edge_acc: dict[tuple, dict] = {}
    organic_links = retrofit_links = unresolved = 0
    notes_with_links = 0
    for p in curated:
        text = contents.get(p, "")
        marker_pos = text.find(MARKER)
        found = list(WIKILINK_RE.finditer(text))
        if found:
            notes_with_links += 1
        for m in found:
            origin = ("retrofit" if marker_pos != -1 and m.start() > marker_pos
                      else "organic")
            if origin == "organic":
                organic_links += 1
            else:
                retrofit_links += 1
            tgt = resolve.get(_norm_target(m.group(1)))
            if not tgt or tgt == f"note:{p}":
                if not tgt:
                    unresolved += 1
                continue
            key = (f"note:{p}", tgt, origin)
            if key in edge_acc:
                edge_acc[key]["weight"] += 1
            else:
                edge_acc[key] = {"source": f"note:{p}", "target": tgt,
                                 "type": "wikilink", "origin": origin,
                                 "weight": 1}
    edges = list(edge_acc.values())

    # semantic layer overlay: concept nodes + concept/note edges, additive
    concept_nodes, concept_edges = _load_semantic(set(nodes.keys()))
    nodes.update(concept_nodes)
    edges.extend(concept_edges)

    # degree bookkeeping for meta (viz recomputes its own)
    deg: dict[str, int] = defaultdict(int)
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    isolated = sum(1 for nid in nodes if deg[nid] == 0)

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "vault_root": webdav_client.BUSINESS_ROOT,
            "semantic_concept_nodes": len(concept_nodes),
            "semantic_concept_edges": len(concept_edges),
            # 2026-08-12: base for the "Open in Nextcloud" deep-link on each
            # node in the viz -- always the public web vhost (not whatever
            # NEXTCLOUD_WEBDAV_BASE happens to be pointed at for this build
            # run, e.g. the 127.0.0.1:8090 local-bypass override), since
            # this is a link a human clicks in a browser.
            "nextcloud_web_base": "https://cloud.example.com",
            # 2026-08-12: found live -- this vhost only routes the WebDAV
            # API (/remote.php/dav/...), NOT the Nextcloud web UI itself
            # (/, /index.php, /apps/files/ all 404 -- deliberately narrow
            # public surface, same posture as the root-PROPFIND WAF block).
            # The viz's "Open in Nextcloud" link was built against
            # nextcloud_web_base + /apps/files/, which 404'd for real on a
            # live click-through. A direct WebDAV GET to a known file path
            # DOES resolve (401 -- auth required, not 404), so that's the
            # link that actually works; the browser will prompt for the
            # operator's own Nextcloud credentials.
            # 2026-08-12 (rev 4): both the Files-app link and a direct
            # WebDAV-GET link failed on a real browser click -- Nextcloud's
            # own CSRF "strict cookie" middleware rejects unauthenticated
            # top-level navigations to the DAV endpoint, and there's no
            # login page on that vhost to ever set that cookie. Routing
            # through our own backend (GET /api/v1/vault/file, web/main.py)
            # fixes that -- but rev 3's fallback here was ALSO wrong: it
            # hardcoded dispatch-runner.example.com, which
            # cloudflared/config.yml documents explicitly as the PUBLIC
            # ROLLING DEMO hostname, not the real internal instance. Real
            # internal access is Tailscale-only
            # (corporatetraveldc-dispatch.tailxxxxxxx.ts.net) with no stable
            # public HTTPS hostname of its own. The viz template now prefers
            # a same-origin RELATIVE link whenever it detects it's iframed
            # (the normal PWA case -- inherits whatever host is actually
            # serving the PWA, internal or demo, no hardcoding needed at
            # all); this absolute URL is only the fallback for the
            # standalone vault-hosted copy (opened outside the PWA, e.g. a
            # locally synced file) -- Tailscale hostname since that's the
            # real instance, not the demo.
            "file_open_base": "https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net/api/dispatch/api/v1/vault/file?path=",
            "builder": "second_brain.knowledge_graph.build_graph",
            "curated_notes": len(curated),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "organic_links": organic_links,
            "retrofit_links": retrofit_links,
            "unresolved_links": unresolved,
            "notes_with_links": notes_with_links,
            "isolated_notes": isolated,
            "excluded": list(SKIP_PREFIXES),
        },
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def render_html(graph: dict, out_path: str) -> None:
    with open(_TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    payload = json.dumps(graph, separators=(",", ":"))
    # </script> inside data would break the inline block; escape defensively.
    payload = payload.replace("</", "<\\/")
    html = template.replace("/*__GRAPH_DATA__*/null", payload)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=_PKG_DIR,
                        help="output directory (default: this package dir)")
    parser.add_argument("--json-only", action="store_true",
                        help="skip the HTML render")
    parser.add_argument("--push-vault", action="store_true",
                        help="also push the rendered HTML into the vault "
                             "itself (04-Syntheses/vault-graph.html) via "
                             "webdav_client.put(), so it's reachable "
                             "directly through Nextcloud, not just as a "
                             "repo build artifact. Deliberately opt-in, "
                             "not automatic -- see module docstring.")
    args = parser.parse_args()

    sess = _session()
    paths = enumerate_vault(sess)
    md = [p for p in paths if p.endswith(MD_EXT)
          and not p.startswith(SKIP_PREFIXES)]
    print(f"vault: {len(paths)} files, fetching {len(md)} curated markdown",
          flush=True)
    contents = fetch_all(sess, md)
    print(f"fetched {len(contents)} readable notes", flush=True)

    graph = build(paths, contents)
    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "graph.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=1)
    m = graph["meta"]
    print(f"graph.json: {m['node_count']} nodes, {m['edge_count']} edges "
          f"(links: {m['organic_links']} organic, "
          f"{m['retrofit_links']} retrofit, {m['unresolved_links']} "
          f"unresolved; {m['isolated_notes']} isolated notes)")

    if not args.json_only:
        html_path = os.path.join(args.out, "vault-graph.html")
        render_html(graph, html_path)
        print(f"wrote {html_path}")

        # 2026-08-12: also write to the shared data volume every
        # dispatch container already mounts (/var/lib/corporatetraveldc)
        # -- the repo-path copy above is baked into container images at
        # BUILD time (Containerfile.web's `COPY src/ src/`), which would
        # go stale between rebuilds. web/main.py's GET
        # /api/v1/knowledge-graph/html serves from this path instead, so
        # a fresh build here shows up in the PWA immediately, no
        # container rebuild needed.
        try:
            live_dir = "/var/lib/corporatetraveldc/knowledge_graph"
            os.makedirs(live_dir, exist_ok=True)
            with open(os.path.join(live_dir, "vault-graph.html"), "w", encoding="utf-8") as f:
                with open(html_path, encoding="utf-8") as src:
                    f.write(src.read())
            with open(os.path.join(live_dir, "graph.json"), "w", encoding="utf-8") as f:
                json.dump(graph, f, indent=1)
            print(f"wrote {live_dir}/ (live-served copy)")
        except OSError as e:
            print(f"could not write live-served copy (non-fatal): {e}")

        if args.push_vault:
            # webdav_client.put() always writes through NEXTCLOUD_WEBDAV_BASE
            # as currently set -- fine here since this write targets a
            # normal subfolder, not the root PROPFIND path this module's
            # own enumerate_vault() has to work around.
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
            vault_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/vault-graph.html"
            webdav_client.put(vault_path, html)
            print(f"pushed to vault: {vault_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
