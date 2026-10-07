# Second Brain — Current State (template)

> Generalized, sanitized template of a private document
> (`docs/SECOND_BRAIN_STATUS.md`): the machinery of the reference deployment's
> second brain, with no real hostnames, accounts, vault folder names beyond the
> generic PARA skeleton, or operator content. The real file is excluded from
> the public mirror (`scripts/scrub-public-tree.py` lists it in `DROP_FILES`).
> Verified against the private source on 2026-10-06 22:45Z / 18:45 ET.

_Living status doc for the "second brain" workstream: the vault, its index,
the semantic layer, the knowledge graph and every job that writes to the
vault. The vault itself is the source of truth for every agent; if chat
memory or a session scratchpad conflicts with it, the vault wins. Give every
number the time it was read, and every timer its time zone._

## 1. Where the vault lives (reference layout)

- **Nextcloud**, under a dedicated **non-admin** account, with all business
  content under one project root folder (`<project>/`), separate from any
  personal files on the same server. A personal account, if any, is separate
  and feeds the vault one way only (§3).
- Two hostnames in front of it:
  - `dav.example.com` — interactive (web UI, WebDAV/CalDAV/CardDAV, mobile).
  - `cloud.example.com` — automation-only vhost exposing the vault WebDAV
    path and nothing else (`/` is 404). External auth through it is refused.
- Containers reach Nextcloud with a narrow network opt-in
  (`Network=pasta:--map-gw` and `NEXTCLOUD_WEBDAV_BASE=http://host.containers.internal:80/remote.php/dav/files`,
  i.e. the existing nginx vhost in front of the loopback-bound app); host
  processes default to `http://127.0.0.1:8090/...`. No container uses host
  networking for this.
- Folder skeleton (PARA-style): `00-Inbox/` (+ `rss/`, `cross-link-findings/`),
  `01-Sources/` (`daily/`, `manual/`, `demo-archive/`, `transport-patterns/`,
  `personal-notes/`), `02-Concepts/`, `03-Entities/`, `04-Syntheses/`
  (`daily/`, `weekly/`, `entity-tracking/`), `05-Skills/`, `06-AI-Memory/`,
  `99-Archive/`, plus `TOC.md`.

## 2. Code (`src/second_brain/`)

| Module | Role |
|---|---|
| `webdav_client.py` | The one WebDAV client: `mkcol`, `mkdirs`, `put`, `put_create_only` (`If-None-Match: *`), `get`, `delete`, `list_files`. No `move` (GET + PUT + DELETE at the call site). Collections are always requested with a trailing `/` and redirects are never followed (a redirect can drop credentials). |
| `scrub_gate.py` | CUI/PII **block** gate: raises, never redacts. First-pass regex heuristics (e.g. SSN-shaped tokens; regulated-information markers co-occurring with frequency-shaped tokens); not exhaustive. |
| `remember.py` | Manual capture: gate → `01-Sources/<subdir>/<UTC stamp>.md` with frontmatter (`human-authored` / `agent-authored` tag) → index. A write to `manual/` touches a trigger file watched by `.path` units. Shared by a CLI and an admin-tier `POST /api/v1/remember`. |
| `index_db.py` | Vault index in Postgres: `scan_vault()` (WebDAV inventory), `index_note()`, `search_notes()` (tsvector), `[[wikilink]]` links/backlinks. Exits non-zero on any PROPFIND failure. |
| `client_entity_ingest.py` | One-shot contact/entity ingest; no live CRM source. |
| `doc_generation.py` | Markdown → `.docx` / `.pptx` / PDF (LibreOffice headless). |
| `semantic/` | Ontology (faceted: domain, genre, lifecycle, provenance, derivation; entities from the lexicon), deterministic compiler (concepts, derivations, chronology, geometry, causal associations, clusters), exporters. **Never LLM-classified**; imports neither the WebDAV client nor the LLM client. |
| `knowledge_graph/` | Entity lexicon, idempotent link retrofit (entity hubs + a marked auto-wikilink footer), read-only graph builder (`graph.json` + an HTML view), served at `/api/v1/knowledge-graph/{html,meta}` (Tier 1). |

**Storage**: Postgres (same database as the platform) since 2026-09-19/20 —
migration `0055` creates the index, entity, link and `semantic_*` tables
(`vault_notes_fulltext` with a GIN tsvector); `0059` adds geometry refs. The
earlier SQLite index file is no longer written.

## 3. Scheduled writers and compilers

User units unless noted. Wall-clock jobs carry `America/New_York`
explicitly; the host clock is UTC.

| Job | Schedule (reference) | What it does |
|---|---|---|
| Vault daily | 23:45 ET (03:45Z during EDT), then every 2 h; maintenance-window exec condition; shared long-runner lock | daily digest → scrub gate → local LLM narrative (deterministic fallback) → `01-Sources/daily/<operational day>.md` (day rolls at 05:00 local) |
| Vault weekly | Mon 04:30 ET (08:30Z during EDT) | past week's daily sources, transport patterns and daily syntheses → `04-Syntheses/weekly/<YYYY-Www>.md` |
| Index scan | daily 04:00 ET | `python3 -m second_brain.index_db --scan` |
| Demo archiver | daily 04:15 ET | last ~30 h of recorded demo snapshots (Postgres) → `01-Sources/demo-archive/<date>.md` |
| RSS | every 2 h | RSS/Atom → `00-Inbox/rss/` (feed list from an env var; separate from the dashboard's broader RSS catalog) |
| Semantic compile (+ `.path`) | every 6 h, `Persistent=true`, and on every `manual/` note | recompile the semantic tables; no LLM |
| Knowledge-graph compile (+ `.path`) | 6-hour timer + `.path` trigger | rebuild the graph |
| Link retrofit | 6-hour timer | add entity hubs and auto-wikilink footers |
| Entity-tracking digest | every 6 h | consolidate entity findings from the daily watches → `04-Syntheses/entity-tracking/`; no LLM |
| Personal-notes import | every 2 min | one-way copy of the operator's personal notes app into `01-Sources/personal-notes/`, routed by category; never deletes |
| Research-board mirror | every 15 min | one-way, scrub-gated, idempotent mirror of research items to the coordination board |
| Personal-export watch | weekly | process new personal data exports |
| Agent-memory dump (host script) | weekly | dump the agent's scratchpad + persistent memory into one vault note, then reset them |
| Maintenance dispatcher | every 20 min + jitter | starts enrolled long-runners one at a time inside the day's rolling quiet windows |

Enrolled long-runners (no timers of their own): six daily topic watches
(each writes `04-Syntheses/daily/<topic>-watch-daily-<day>.md`), a
disruption/weather digest, a transport-pattern digest
(`01-Sources/transport-patterns/`), and an advance-venues job. Monday weeklies
run on their own timers.

Every LLM-using writer goes through one client (`common.llm`) to the one local
llama.cpp server (`LLAMA_BASE_URL`) with a persona; cloud fallback is off in
the reference deployment, so a model failure degrades to the job's
deterministic fallback.

## 4. Agent access to the vault

- **Reads**: `GET /api/v1/vault/research?path=…` and `/list?path=…` (board
  tier `normal`), scoped to an allowlist of folders (agent series,
  syntheses, concepts, cross-link findings, manual sources). Cloud agents
  reach the same scope through the agent gateway's `research_list` /
  `research_read` tools.
- **Writes by team accounts**: only `POST /api/v1/workspace/contribute` —
  signed, scrub-gated, create-only, at a server-chosen path
  (`Series/contributions/<task>/<account>/<UTC stamp>-<slug>.md`). No team
  account holds vault credentials.
- **Operator writes**: `remember.py` / `POST /api/v1/remember` and a
  capture skill.
- **Agent pamphlets**: `Series/agents/<account>/PAMPHLET.md`, published by a
  script.

## 5. Failure patterns to watch for (from the reference deployment)

- **A scan that "succeeds" with 0 files**: for about two months the index
  scan recorded nothing and exited 0 — a slashless collection URL was
  redirected by the automation vhost and the follow-up lost its credentials
  (401). Fixed by always requesting collections with `/`, never following
  redirects, and exiting 1 on PROPFIND failure.
- **Tracked timers disabled live**: the graph only rebuilt on manual notes
  and the link retrofit did not run. Diff tracked vs enabled timers.
- **Exports go stale while tables stay fresh**: the scheduled compile
  refreshes Postgres only; exported JSON/TTL/Markdown snapshots age silently.
- **Timers without a zone** shift when the host clock changes.
- **Tag growth outpaces concept mapping**: most distinct tags end up
  unmapped unless mapping is maintained.
- **Few organic links**: most writers do not emit `[[wikilinks]]`; retrofit
  links dominate.
- **"Image predates the file"**: a new job fails on first install if the image
  was built before the file was signed. Order: sign → build → restart.
- **Planned vs built**: e.g. a weekly retrieval step over earlier weeklies was
  designed and not built. Say which, explicitly.

## Explicitly deferred — do not resurface

- _List anything your operator has explicitly deferred, so future agent
  sessions don't re-suggest it._

## Open gaps (fill in for your deployment)

- _Known gaps — no refresh timer, no live CRM feed, a disabled compile — so
  they are visible without digging._
- _Whether every automatic ingestion path has run end-to-end against the
  real storage backend inside its container, not just in unit tests._
- _When a container needs a loopback-bound host service, which narrow opt-in
  was used (per-container alias, an existing vhost) and why — not host
  networking._

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-agents.md`.


### Second Brain — Current State (template)

~~This is a placeholder template. The real `docs/SECOND_BRAIN_STATUS.md` (operator-specific paths, task detail, and vault layout) is intentionally excluded from the public mirror -- see `scripts/scrub-public-tree.py`'s `DROP_FILES`. Fill in the sections below for your own deployment.~~


### Second Brain — Current State (template) › Status

| ~~Task~~ | ~~Status~~ | ~~Notes~~ |
|---|---|---|
| ~~Vault deployment~~ | ~~_todo_~~ | ~~Where your notes/files vault is hosted and how it's reached.~~ |
| ~~Vault index~~ | ~~_todo_~~ | ~~Path to any indexing tool/output, and whether it's on a refresh schedule or one-shot.~~ |
| ~~Entity ingest~~ | ~~_todo_~~ | ~~Which sources feed contact/client entities, and which are stubbed pending credentials.~~ |
| ~~Folder taxonomy~~ | ~~_todo_~~ | ~~If you're using a PARA/Karpathy-method style layout (Inbox/Sources/Concepts/Entities/Syntheses/etc.), note whether the folders actually exist yet or only the plan does -- a planning discussion is not a migration.~~ |
| ~~Other linkages~~ | ~~_todo_~~ | ~~Any other systems tied into the vault.~~ |
