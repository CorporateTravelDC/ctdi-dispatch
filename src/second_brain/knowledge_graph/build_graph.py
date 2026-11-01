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

Code lives in src/second_brain/knowledge_graph/. Generated files default
to /var/lib/corporatetraveldc/knowledge_graph/, the directory served by
the web API. Source files remain untouched. --out selects an alternate
destination without changing the live graph. Publishing a rendered copy
into the vault remains an explicit --push-vault action.

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

Outputs (default DIR = the live-served shared directory):
    graph.json        nodes/edges/meta as structured data
    vault-graph.html  self-contained interactive viz (template + inline data)
"""
import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import psycopg
import requests

from second_brain import webdav_client
from second_brain.index_db import get_conn
from second_brain.knowledge_graph.lexicon import SUBTYPE_OF

log = logging.getLogger(__name__)

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(_PKG_DIR, "viz_template.html")
_LIVE_DIR = "/var/lib/corporatetraveldc/knowledge_graph"

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


_FETCH_MAX_RETRIES = 2
_FETCH_RETRY_BACKOFF_S = 1.5


def fetch_all(sess: requests.Session, rel_paths: list[str]) -> dict[str, str]:
    """Fetch every vault file's content over WebDAV, 8-way concurrent.

    2026-09-05 (real incident): _one() used to have no exception handling
    at all -- a single flaky file GET (requests.exceptions.ReadTimeout,
    among several hundred concurrent 8-way requests against Nextcloud)
    propagated straight out of ex.map()'s iteration and killed the ENTIRE
    multi-minute compile, discarding every file already successfully
    fetched. Confirmed live: an 8-minute run (462s) still failed on one
    bad request near the end, twice in a row -- not the "isolated network
    blip" it first looked like from a single failure alone. Now retries
    each individual file up to _FETCH_MAX_RETRIES times (short backoff)
    before giving up on JUST that one file -- same "log and skip, don't
    abort the batch" treatment already given to a non-200 status code
    below, just extended to cover a request that raises instead of
    returning a bad status."""
    from concurrent.futures import ThreadPoolExecutor

    base = f"{webdav_client._base_url()}/{webdav_client.BUSINESS_ROOT}"

    def _one(p: str) -> tuple[str, str | None]:
        last_exc: Exception | None = None
        for attempt in range(_FETCH_MAX_RETRIES + 1):
            try:
                r = sess.get(f"{base}/{requests.utils.quote(p)}", timeout=30)
            except requests.exceptions.RequestException as e:
                last_exc = e
                if attempt < _FETCH_MAX_RETRIES:
                    time.sleep(_FETCH_RETRY_BACKOFF_S * (attempt + 1))
                    continue
                log.warning(
                    "knowledge-graph fetch: %s failed after %d attempt(s), skipping "
                    "just this file (%s: %s)",
                    p, _FETCH_MAX_RETRIES + 1, type(last_exc).__name__, last_exc,
                )
                return p, None
            if r.status_code != 200:
                return p, None
            return p, r.content.decode("utf-8", "replace")
        return p, None  # unreachable, satisfies type checkers

    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for p, text in ex.map(_one, rel_paths):
            if text is not None:
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

def _load_semantic(existing_note_ids: set[str], *, required: bool = False,
                   snapshot: dict | None = None) -> tuple[dict[str, dict], list[dict]]:
    """Returns (concept_nodes, concept_edges) to merge into build()'s graph.
    concept_nodes keyed like nodes (concept:<id>); concept_edges include both
    concept-to-concept (broader/related) and note-to-concept (tagged) edges.
    Silently returns (empty) if the semantic layer isn't available/compiled
    yet when required=False. The scheduled compiler requires the overlay:
    a database failure must preserve the prior served graph and fail the
    job, rather than publishing a graph stripped of semantic links."""
    try:
        from second_brain.semantic.model import load as load_semantic
    except ImportError:
        if required:
            raise
        return {}, []

    try:
        model = load_semantic()
    except Exception:
        if required:
            raise
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

    # Note assignments and compile metadata come from one read-only snapshot.
    # Stream the assignments instead of buffering the whole result in libpq.
    prefix = f"{webdav_client.BUSINESS_ROOT}/"
    tag_acc: dict[tuple, dict] = {}
    try:
        with get_conn() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            meta = {r["key"]: r["value"] for r in conn.execute(
                "SELECT key,value FROM semantic_meta WHERE key IN ('compiled_at','compile_seq')").fetchall()}
            if required and not all(meta.get(key) for key in ('compiled_at', 'compile_seq')):
                raise RuntimeError("semantic layer has no successful compile metadata")
            cur = conn.cursor(name="knowledge_graph_concepts")
            cur.itersize = 1000
            try:
                cur.execute("SELECT path, concept_id, facet, rule FROM semantic_note_concepts")
                for row in cur:
                    path = row["path"]
                    relative = path[len(prefix):] if path.startswith(prefix) else path
                    note_id = f"note:{relative}"
                    cid = f"concept:{row['concept_id']}"
                    if note_id not in existing_note_ids or cid not in concept_nodes:
                        continue
                    key = (note_id, cid)
                    if key in tag_acc:
                        tag_acc[key]["weight"] += 1
                    else:
                        tag_acc[key] = {"source": note_id, "target": cid,
                                        "type": "concept_tag", "origin": "semantic",
                                        "facet": row["facet"], "rule": row["rule"], "weight": 1}
            finally:
                cur.close()
            if snapshot is not None and meta.get('compiled_at') and meta.get('compile_seq'):
                snapshot.update(semantic_compiled_at=meta['compiled_at'],
                                semantic_compile_seq=int(meta['compile_seq']))
    except psycopg.Error:
        if required:
            raise
        tag_acc.clear()

    if required and existing_note_ids and not tag_acc:
        raise RuntimeError("no semantic note links matched the curated graph; previous output preserved")

    concept_edges.extend(tag_acc.values())

    return concept_nodes, concept_edges


# ── Graph assembly: nodes = notes, edges = real wikilinks ─────────────────────

def build(paths: list[str], contents: dict[str, str], *,
          require_semantic: bool = False) -> dict:
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
    semantic_snapshot: dict = {}
    concept_nodes, concept_edges = _load_semantic(
        set(nodes.keys()), required=require_semantic, snapshot=semantic_snapshot)
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
            "semantic_note_links": sum(e["type"] == "concept_tag" for e in concept_edges),
            **semantic_snapshot,
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


def publish_graph(graph: dict, out_dir: str, *, json_only: bool = False) -> None:
    """Finish every render before replacing a served file.

    Each replacement is atomic on the destination filesystem; the two files
    are not a multi-file transaction. JSON is published last so metadata does
    not claim a completed generation before its HTML replacement succeeds.
    Failures propagate to systemd rather than silently serving stale output
    behind a successful unit result. No writes to the source package tree.
    """
    os.makedirs(out_dir, exist_ok=True)
    staged: list[tuple[str, str]] = []
    try:
        def temporary(name: str) -> str:
            fd, path = tempfile.mkstemp(prefix=f".{name}.", dir=out_dir)
            os.close(fd)
            staged.append((path, os.path.join(out_dir, name)))
            return path

        json_path = temporary("graph.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if not json_only:
            html_path = temporary("vault-graph.html")
            render_html(graph, html_path)
            with open(html_path, "rb") as f:
                os.fsync(f.fileno())
        # Preserve the prior access mode; initial installation matches the
        # regular artifact convention (0644 filtered by the caller's umask).
        for path, destination in staged:
            if os.path.exists(destination):
                os.chmod(path, os.stat(destination).st_mode & 0o777)
            else:
                current_umask = os.umask(0)
                os.umask(current_umask)
                os.chmod(path, 0o644 & ~current_umask)
        for path, destination in reversed(staged):
            os.replace(path, destination)
    finally:
        for path, _ in staged:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=_LIVE_DIR,
                        help="output directory (default: the live-served shared directory)")
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
    if not md:
        print("knowledge-graph: no curated notes enumerated; previous output preserved",
              file=sys.stderr)
        return 1
    print(f"vault: {len(paths)} files, fetching {len(md)} curated markdown",
          flush=True)
    contents = fetch_all(sess, md)
    print(f"fetched {len(contents)} readable notes", flush=True)
    missing = len(set(md) - contents.keys())
    if missing:
        print(f"knowledge-graph: incomplete fetch ({missing} of {len(md)} notes missing); "
              "previous output preserved", file=sys.stderr)
        return 1

    graph = build(paths, contents, require_semantic=True)
    publish_graph(graph, args.out, json_only=args.json_only)
    m = graph["meta"]
    print(f"graph.json: {m['node_count']} nodes, {m['edge_count']} edges "
          f"(links: {m['organic_links']} organic, "
          f"{m['retrofit_links']} retrofit, {m['unresolved_links']} "
          f"unresolved; {m['isolated_notes']} isolated notes)")

    if not args.json_only:
        html_path = os.path.join(args.out, "vault-graph.html")
        print(f"wrote {html_path}")

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
