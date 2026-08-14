# Second-Brain Knowledge Graph — Automation Prompt (DRAFT, not scheduled)

Drafted 2026-08-11 alongside the initial wikilink retrofit + knowledge-graph
build (`src/second_brain/knowledge_graph/`). **This is a prompt for operator
review — no timer, service, or schedule has been installed.** When approved,
it should be wired the same way `scripts/weekly-doc-drift-check.sh` runs its
prompt: `claude --model fable -p "<prompt>" --permission-mode acceptEdits`
with an allowed-tools list, output appended to a dated log under
`/var/lib/corporatetraveldc/`, never committing.

## Cadence recommendation

**Weekly, time-based — not commit-triggered.** Rationale:

- The graph's inputs are *vault* writes (daily digests, notepad drops,
  manual captures), which happen on their own timers and ad hoc, completely
  decoupled from git commits in this repo — a post-commit hook would fire at
  the wrong moments and miss the right ones.
- The retrofit and builder are idempotent and incremental by design (the
  `<!-- auto-wikilinks` marker means already-processed notes are skipped),
  so a weekly pass touches only the ~dozens of new notes, not all ~250.
- Weekly matches the vault's existing synthesis rhythm (`second_brain_weekly`
  Sunday 18:15 ET, 06-AI-Memory synthesis Sunday). Suggested slot: **Sunday
  19:00 ET** — after both weekly syntheses have landed, so the graph pass
  sees the week's full output and its trend report can reference them.
  Daily would add noise, not signal: single-day link deltas are mostly
  auto-generated digests pointing at the same hubs.

## The prompt

```
Weekly second-brain knowledge-graph refresh + trend pass for the
CorporateTravelDC dispatch platform (repo:
/opt/corporatetraveldc/private/ctdi-dispatch-internal, vault: Nextcloud
WebDAV at 127.0.0.1:8090, business root "corporatetraveldc/"). Source env
first: set NEXTCLOUD_ADMIN_USER and NEXTCLOUD_WEBDAV_BASE=
http://127.0.0.1:8090/remote.php/dav/files (credentials resolve via
/etc/corporatetraveldc/dispatch-secrets.env, the standard webdav_client
pattern — never print the password).

This is an INCREMENTAL re-verification pass, not a rebuild-from-scratch.
The retrofit is idempotent (notes carrying an "<!-- auto-wikilinks" marker
are skipped), so the steps below only touch content that is new or changed
since the last run.

1. Snapshot the current baseline. Read
   src/second_brain/knowledge_graph/graph.json (meta block: node_count,
   edge_count, organic_links, retrofit_links, isolated_notes, and the
   per-node/per-edge lists). Also find the most recent
   docs/SECOND_BRAIN_GRAPH_TRENDS_*.md to see what the last pass reported
   and what it flagged as worth watching. Copy graph.json to
   /var/lib/corporatetraveldc/graph-snapshots/graph-$(date +%Y-%m-%d).json
   BEFORE regenerating, so week-over-week diffs stay possible even though
   the repo copy gets overwritten (create the directory if missing; do not
   prune old snapshots).

2. Incrementally retrofit links on new content:
   cd src && python3 -m second_brain.knowledge_graph.retrofit_links --dry-run
   Review its plan: it should only propose edits to notes created/changed
   since the last pass (the marker makes old notes no-ops). Sanity-check a
   couple of proposed edits for false-positive entity matches (an
   over-eager lexicon regex tagging unrelated prose). If the plan looks
   wrong — hundreds of edits, or edits to 00-Inbox/rss/, notepad/processed/,
   or .internal-backups/ (all excluded by design) — STOP, write up why in
   the trends file, and do not run the real pass. Otherwise run it without
   --dry-run. If a genuinely new entity now recurs across >=3 notes but is
   missing from lexicon.py, add it to lexicon.py (repo edit, uncommitted)
   and note the addition.

3. Rebuild the graph and viz:
   python3 -m second_brain.knowledge_graph.build_graph
   This overwrites src/second_brain/knowledge_graph/graph.json and
   vault-graph.html in place (the repo copy is the canonical latest; dated
   history lives in the snapshots directory from step 1).

4. Trend analysis — compare the fresh graph.json against the prior
   snapshot(s), and write docs/SECOND_BRAIN_GRAPH_TRENDS_$(date +%Y-%m-%d).md
   covering, with specific note/hub names and numbers (skip any section
   with nothing real to say — "no change" is a fine answer, don't pad):
   - RISING: entities/hubs whose degree grew notably this week, and which
     new notes drove it. Distinguish "one burst of related notes" from
     "sustained growth across multiple weeks" (use older snapshots).
   - GONE QUIET: hubs that were among the more-connected nodes in earlier
     snapshots but gained zero new links for 2+ consecutive weeks — name
     the topic and when it stalled. This is how a dropped thread (a
     deferred bug, an abandoned workstream) surfaces.
   - NEW CLUSTERS: groups of notes that now interlink (directly or via
     shared hubs) which were not visibly related before — name the cluster
     by its apparent theme. Cross-folder clusters (e.g. a 06-AI-Memory
     notepad thread converging with 01-Sources content) are exactly the
     cross-cutting patterns the flat folder layout hides; call them out
     explicitly.
   - ORGANIC VS RETROFIT: the ratio of organic to retrofitted links, and
     whether any *organic* [[wikilinks]] appeared in newly written notes
     this week. This tracks whether the wiki habit is taking hold at the
     source (agents/writers emitting links themselves) versus the retrofit
     doing all the work — if organic stays at ~zero for many weeks, flag
     that the ingestion writers still don't emit links and name which
     writer would give the most leverage.
   - ISOLATED: count of notes with no links at all, and whether it is
     shrinking. List up to 5 isolated notes that look like they SHOULD
     connect (their text plainly discusses a hub topic) — these are lexicon
     gaps or retrofit misses worth a look.
   - HEALTH: unresolved wikilink count from meta (links pointing at
     nothing — candidate future notes, or typos), and any parse errors.

5. Refresh the vault-side index so backlinks stay queryable:
   python3 -m second_brain.index_db --scan
   (The retrofit wrote new files; the 04:00 ET timer would catch up
   tomorrow anyway, but running it now keeps the FTS/backlink tables
   consistent with what the trends file claims.)

Hard rules: DO NOT commit, stage, or push anything — no git add/commit/push,
no exceptions; leave repo changes as uncommitted working-tree edits for
operator review. Do not touch other git branches or run
checkout/reset/stash. Vault writes are limited to what retrofit_links.py
itself does (marked footers + 03-Entities hub notes); never delete or
rewrite existing vault content. Never print credentials. If the vault is
unreachable, write the trends file saying so and exit — do not retry-loop.
```

## Wiring notes (for when it's approved)

- Sibling script suggestion: `scripts/weekly-graph-refresh.sh`, same shape
  as `scripts/weekly-doc-drift-check.sh` (nohup, dated log under
  `/var/lib/corporatetraveldc/graph-refresh/`, `--allowedTools
  "Bash,Read,Write,Edit,Glob,Grep"`), plus a user-scope systemd timer
  (`Sun 19:00 America/New_York`) following the existing
  `corporatetraveldc-docs-drift-weekly.timer` pattern.
- The prompt deliberately makes step 2's dry-run a gate: an LLM reviewing a
  deterministic script's plan before executing it is the same
  belt-and-suspenders shape as the docs-drift check re-verifying rather
  than re-doing.
- Trends output goes to `docs/` (uncommitted) like
  `docs/LIVE_STATE_CHECK_*.md` does, so review happens in the same place
  the operator already looks.
