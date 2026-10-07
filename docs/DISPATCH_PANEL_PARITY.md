# Dispatch Panel — Parity Spec for the All-Local Rebuild

Verified against HEAD 2c3f81b and live state on 2026-10-06 18:25Z / 14:25 ET.

Written 2026-10-04 from the live code and a read-only look at the live
system. Purpose: let someone rebuild the dispatch chat panel ("dispatch
drawer" / chat bar) to behavioural parity **without reading the current
implementation**, as an all-local service (no external agent logic) with a
self-learning layer (RAG + fine-tune over the codebase, the second-brain
vault and the topical/vocabulary stores; training on a rented GPU swarm,
serving on the Pi). Every claim below carries a `file:line` so the rebuild
can diff against the original. Lines refer to commit `97b9075`; they are
still valid at HEAD 2c3f81b for `src/runner/main.py` (the only change since is
the one-line `LLAMA_BASE_URL` rename at line 98, which does not shift lines).
Where a reference names a decorator line, the `def` is the next line.

Nothing in this document is a secret or PII; env vars are named, never
valued. Known defects in the current panel are marked **DO NOT REPLICATE**.

---

## 1. Inventory — what the panel is today

| Piece | Where | Notes |
|---|---|---|
| UI (drawer) | `src/runner/frontend/src/components/DispatchDrawer.jsx` | React; slide-up drawer with input box, history list, streaming bubble, `/model` menu |
| UI (second caller) | `src/runner/frontend/src/components/DispatchView.jsx:39` | Older full-view variant; POSTs `{message, history}` to the same endpoint |
| Backend | `src/runner/main.py` (`dispatch-runner`, FastAPI) | Prod instance port **8001** (tailnet + loopback); demo instance port **8005** with `DEMO_MODE=true` (`~/.config/containers/systemd/corporatetraveldc-runner{,-demo}.container`) |
| Chat endpoint | `POST /api/ask` — `src/runner/main.py:1636` | SSE stream |
| History endpoints | `GET /api/chat/history`, `DELETE /api/chat/history` — `main.py:1781,1790` | trust-gated (404 when untrusted) |
| Context source | `_build_context_rich()` — `main.py:1313` | 8 parallel GETs to the dispatch API (`DISPATCH_BASE_URL`, container env = the Tailscale address :8000) |
| Local resolver | `_local_answer()` — `main.py:1416`; topic regexes `_TOPIC_RX` — `main.py:1301` | zero-LLM structured answers |
| LLM client | `_llm_stream()` — `main.py:1537` → `http://{llama_pool.HOST}:{port}/v1/chat/completions` | OpenAI-compatible streaming to llama-server |
| Pool facts | `src/common/llama_pool.py:53-67` | `HOST` = `LLAMA_POOL_HOST`, else the host of `LLAMA_BASE_URL`, else the Tailscale address; `LLAMA_PORT` likewise, default 8093; HOT/CHAT/REPORT all resolve to it (changed 2026-10-05) |
| Persona table | `src/common/personas.py:87` (`PERSONAS`), `build_system_prompt()` :924, `persona_key_for()` :943 | 22 personas; `chat` tier=chat temp 0.3 top_p (see table) num_predict 350 |
| Model serving | `~/.config/systemd/user/corporatetraveldc-llama.service` (tracked copy in `.config/systemd/user/`) | `llama-server -m qwen3-4b-instruct-2507-q4_0.gguf --host <tailscale> --port 8093 -np 2 --kv-unified -c 12288 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 2 --cache-ram 0 --no-webui`, `Slice=production.slice`, `CPUQuota=200%`, `MemoryMax=8448M` |
| Persistence | `chat_messages` table — `src/common/pg_schema/0056_dispatch_chat.sql`; helpers `main.py:2007-2031` | Postgres via `common.db.conn()`; **0 rows as of 2026-10-04** [UNVERIFIED 2026-10-06: table not re-queried] |
| Front proxy | `nginx/conf.d/tailscale-dispatch-runner.conf` | `limit_req zone=corporatetraveldc_lr burst=20 nodelay` (zone `10r/s`, `nginx/conf.d/00-rate-limit-corporatetraveldc.conf:34`), `proxy_read_timeout 120s` |
| Audit trail | none on `/api/ask` | `audit_log` is written by admin/approval paths only; chat writes `chat_messages` only |

### Tools / function calls the panel can make
None. The model never calls tools. All "tool use" is pre-computed: the
backend fetches the 8 endpoints before the model runs and injects a text
block. The only operator "command" is the `/model` directive (client- and
server-parsed).

### What is external today ("external agent logic" to remove)
- **Nothing in the request path is cloud.** `/api/ask` → runner → llama-server (local) → dispatch API (local). The Anthropic fallback the docstring at `main.py:1646` still describes was removed at the 2026-08-27 cutover (`has_llm = True`, `main.py:1658`).
- Residual config only: `LLAMA_BASE_URL` (read with `OLLAMA_BASE_URL` as fallback since 2026-10-05), `OPENWEBUI_URL`, `OPENWEBUI_API_KEY` (`main.py:98-100`) are read but unused by the chat path (it uses `llama_pool.HOST`). `openwebui` is a separate container (not in the panel's path).
- The *persona text* embeds the operator's identity and locality strings (`main.py:1697-1716`) — these are part of the behavioural contract (section 2) and must be sourced from a local config, not hard-coded, in the rebuild.

---

## 2. Behavioural contract (checklist)

Each line: behaviour → source. Replay these as parity tests.

**Input handling**
- [ ] Accepts `{"message": str, "history": [], "model": str|null}` (`main.py:1292`). `history` from the client is **ignored** on the server (server loads its own last 40 turns, `main.py:1679`); `DispatchView.jsx:39` still sends it.
- [ ] `/model <name> <rest>` inline directive: strips the directive, uses `<name>` for this request only, stores the stripped text in history (`main.py:1668-1676`). Body `model` field wins over inline.
- [ ] Client-side: `/model <name>` with no body sets a session override; `/model reset|clear` clears it; neither reaches the server (`DispatchDrawer.jsx:81-100`). Session override is React state only (lost on reload).
- [ ] Empty/whitespace message is not sent (`DispatchDrawer.jsx:77`). Input disabled while streaming (`:323`). Shift+Enter newline, Esc closes, Enter sends (placeholder text `:322`).
- [ ] No max length enforced anywhere (client or server). nginx rate limit 10 r/s burst 20 per client address applies to the whole runner.

**Resolution order** (`main.py:1638-1652` docstring, implemented `:1683-1770`)
- [ ] 1. Always fetch the 8 context endpoints in parallel, 5 s timeout each, failures silently omitted (`:1321-1344`).
- [ ] 2. Run the local resolver: if the message matches a topic regex (`cps|go/no-go|hems`, `weather|metar|wind|ceiling|vis|wx`, `tfr|flight restrict|potus|marine one|vip air`, `amtrak|train|was|union sta`, `notams?`, `alert|warning|advisory|nws`, `feed|health|nominal|degrad|error`, `brief|summary|status|situation|sitrep`) build a structured text answer from the data (`:1416`).
- [ ] 3. Stream the LLM with the full context injected into the system prompt and the last 40 persisted turns + the new user turn as messages.
- [ ] 4. If the model backend is unreachable, errors mid-stream, or yields nothing: stream the local answer (or the raw context string, or a fixed "Dispatch spine unreachable" line) as a single `text` event then `done` (`:1730-1760`).
- [ ] The LLM is a synthesis layer, never a gatekeeper: every query returns *something* while the dispatch API is reachable.

**System prompt** (`main.py:1697-1716`) — the rebuild must reproduce the *effect*, not the literal:
- [ ] Identity: "dispatch AI assistant for [operator LLC], LLC" + the four business lines; operator name + ham/ARES/Skywarn identifiers (sourced from config in the rebuild).
- [ ] Authority rule: "All live operational data below comes from a local dispatch spine… It is the authoritative source. Do not speculate beyond it."
- [ ] Injected `CURRENT DISPATCH STATE (<local timestamp>)` block = `_context_to_str(ctx)` or the literal "No data available from dispatch spine."
- [ ] Static OPERATOR CONTEXT: location/KDCA, DC FRZ/SFRA + P-56 + 50/100/150/250 nm rings, ground ops partners, emergency affiliations, "Dispatch spine: Pi 5, Tailscale".
- [ ] Style: "Respond in plain text. No markdown. Brief and tactical unless elaboration requested. For HEMS go/no-go, always cite CPS score and narrative."
- [ ] Note: `_llm_stream` would use `personas.build_system_prompt("chat")` (825-token preamble+task) **only if** no system prompt is passed (`:1567`); `/api/ask` always passes its own, so the `chat` persona's text is **not** what the panel uses today. The persona only supplies sampling params (`temperature`, `top_p`, `max_tokens`=350).

**Context block semantics** (`_context_to_str`, `main.py:1346`) — intended content:
- [ ] `CPS: <score> / <label> — <narrative>`
- [ ] `WEATHER (METAR):` up to 6 stations, raw METAR text
- [ ] `TFRS: N active (M VIP/POTUS) — id, id, …` or `TFRS: none active`
- [ ] `FEED ERRORS: …` | `FEEDS STALE: …` (age > 900 s) | `FEEDS: nominal`
- [ ] `NWS ALERTS (N): headline; headline; headline`
- [ ] `AMTRAK/WAS: <summary>`
- [ ] `NOTAMS: N active`
- **DO NOT REPLICATE** — live shape mismatch (verified 2026-10-04 against the API): weather reads `.stations` (API: `metars[]` with `station/ceiling_ft/visibility_sm/wind_kt`, no raw text), tfr/alerts/notam are read as lists (API: `{tfrs[]}`, `{alerts[]}`, `{notams[]}`), so **the model receives only CPS/FEEDS/AMTRAK and is told "TFRS: none active" while 87 are active** (re-checked 2026-10-06: `_context_to_str` at `main.py:1346` is unchanged; `/api/v1/tfr` returned 87 entries; `/api/v1/brief` is `text/plain`). The local resolver (`:1416`) has the identical mismatch for the weather/tfr/notam/alerts topics, so those topics fall through to the LLM. The same bug class was fixed in `restore_dispatch_state.py` on 2026-10-03. The rebuild must consume the shapes in section 3.

**Streaming / output**
- [ ] Response is `text/event-stream`, headers `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`, `X-Dispatch-Model: <effective model>` (`:1762-1770`).
- [ ] First bytes are a `: keep-alive` SSE comment before any work, to flush proxy buffers (`:1684`).
- [ ] Events: `{"type":"model_info","model":…}` (once, before tokens) → `{"type":"text","text":…}`* → `{"type":"done"}`; `{"type":"no_llm"}` when no backend; `{"type":"error","detail":…}` is documented but never emitted by the current code.
- [ ] Client renders `text` events incrementally, finalises on `done`, shows `ERROR: <detail>` on `error`, treats `no_llm` as "local answer follows" (`DispatchDrawer.jsx:150-170`).
- [ ] Model shown to the user = `X-Dispatch-Model` header, overridden by the `model_info` event.

**Latency budget** (documented, `main.py:1573-1584`)
- [ ] LLM read timeout 110 s (interactive fail-fast → local answer), under nginx's 120 s `proxy_read_timeout`. Measured reference: 1,289-token prompt ≈ 90 s eval at ~14 tok/s on this box. No first-token target is documented.

**Persistence**
- [ ] After every exchange, user turn + assistant turn are inserted into `chat_messages(role, content, ts)` (`:2017`); history is the last 40 rows chronological (`:2007`); `GET /api/chat/history?limit=80` returns `{"messages":[{role,content}], "count"}`; `DELETE` clears all (`:2031`).
- [ ] Both history endpoints 404 (not 403) unless `_is_trusted(request)` — IP-based: CF-Connecting-IP if present, else peer/X-Forwarded-For against `_TRUSTED_NETS` (`:188-260`).
- [ ] `/api/ask` itself is **not** trust-gated and not in the `tailscale_gate` prefixes (`:382`); on prod it is reachable only because the runner is tailnet/loopback-bound. On the demo instance it is behind `DEMO_MODE`'s cookie gate in `proxy_dispatch` for data, but the ask path itself is open.

**Refusals / safety**
- [ ] No content filter, no refusal logic, no tool sandbox — the model is instructed not to speculate beyond the injected data. Model override accepts any string; an unknown model name maps to no persona → `no_llm` → local answer (`:1562`).

---

## 3. Data contract

### `POST /api/ask`
Request `application/json`:
```json
{"message": "string", "history": [{"role":"user|assistant","content":"…"}], "model": "corporatetraveldc-pi5-chat:latest|null"}
```
(`history` ignored server-side; keep accepting it.)

Response `text/event-stream`; one JSON object per `data:` line:
| event | payload | when |
|---|---|---|
| comment `: keep-alive` | — | first bytes |
| `model_info` | `{"model": str}` | before first token |
| `text` | `{"text": str}` | each delta |
| `done` | `{}` | end (also after a fallback text) |
| `no_llm` | `{}` | backend absent; a `text`+`done` follows from the server's fallback |
| `error` | `{"detail": str}` | reserved |

Headers: `X-Dispatch-Model`.

### `GET /api/chat/history?limit=N` → `{"messages":[{"role","content"}], "count":N}`; `DELETE /api/chat/history` → `{"status":"cleared"}`; both 404 when untrusted.

### Context endpoints consumed (dispatch API `:8000`, live shapes 2026-10-04)
| key | path | live shape (top level) |
|---|---|---|
| cps | `/api/v1/cps` | `{score, label, factors{ceiling,visibility,wind,precip,airspace,gdp}, narrative, computed_at}` |
| weather | `/api/v1/weather` | `{metars:[{station, ceiling_ft, visibility_sm, wind_kt, precip_code, obs_time, fetched_at}]}` (no raw METAR text) |
| tfr | `/api/v1/tfr` | `{tfrs:[{tfr_id, is_vip, effective_start, effective_end}], count}` |
| feeds | `/api/v1/feeds` | `{feeds:[{feed_name, fetched_at, age_seconds, stale_threshold_seconds, push_covered, error, pull_error, …}]}` |
| alerts | `/api/v1/alerts` | `{alerts:[{alert_id, event_type, area_desc, severity, certainty, effective, expires, headline}], count}` |
| notam | `/api/v1/notams` | `{notams:[{notam_id, facility, classification, effective_start, effective_end, text_body}], count}` (~6,000 rows — do not inject raw) |
| amtrak | `/api/v1/amtrak` | `{available, summary, fetched_at, trains:[{train_num, route, origin, destination, status, delay_minutes, …}]}` |
| brief | `/api/v1/brief` | **not JSON** (text body) — current code's `r.json()` raises and the topic is silently dropped |

### DB tables read/written by the panel
- `chat_messages(role TEXT, content TEXT, ts DOUBLE)` — read last 40, write 2 per exchange. Nothing else is read directly; all state comes via the HTTP API above.

### Model API
`POST http://<LLAMA_POOL_HOST>:8093/v1/chat/completions` with `{"messages":[{system},…history…,{user}], "temperature", "top_p", "max_tokens", "stream": true}`; consumes OpenAI-style `data:` deltas, `[DONE]` sentinel (`main.py:1569-1606`).

---

## 4. Model / serving facts (current)

- **Model**: `qwen3-4b-instruct-2507-q4_0.gguf` (local file, `/var/lib/corporatetraveldc/models/`, 4.3 GB dir incl. the retired `phi3-mini-q4_0.gguf`). The chat default name `corporatetraveldc-pi5-chat:latest` (`main.py:101`, env `OLLAMA_CHAT_MODEL`) is now only a **persona key** (`persona_key_for` strips `corporatetraveldc-pi5-` → `chat`); every persona runs on the one qwen3 instance.
- **Serving**: llama-server, one process, 2 slots sharing a unified **12,288-token** KV (`-np 2 --kv-unified -c 12288`), q8_0 KV cache, flash-attn on, **2 threads** (`-t 2 -tb 2`, hard-capped so ingest keeps the other cores), bound to the Tailscale address :8093, `production.slice`.
- **Load gate** (`src/common/llm.py:599-623`): pre-flight wait while 1-min load is above target, bounded (`max_wait_s` + deadline), logs and proceeds or gives up; used by the batch skills. The panel's `/api/ask` path does **not** use the load gate — it goes straight to the slot with the 110 s read timeout.
- **Prompt budget**: system prompt ≈ 300 tokens static + context block (today ≈ 100–200 tokens because of the shape bug; with weather/TFR/alerts correctly injected expect 400–800) + up to 40 history turns (unbounded by tokens — **DO NOT REPLICATE**: 40 turns of long answers can exceed the slot; cap by tokens) + 350 max output.
- **Throughput reference**: ~14 tok/s prompt eval, documented 2026-08-15 (`main.py:1574`).
- **Already local**: everything in the request path. **Not local**: nothing. The rebuild's "remove external agent logic" target is therefore the *development* side (agents editing the code), not the runtime — and the dead cloud config vars.

---

## 5. Self-learning plan — skeleton

### 5.1 Corpora on this box (read-only inventory, 2026-10-04; repo rows re-counted 2026-10-06, database row counts not re-queried)
| Corpus | Location | Size | Suitability |
|---|---|---|---|
| Platform source | repo `src/` (72,091 Python lines in 180 tracked `.py` files at 2c3f81b), 1,273 tracked files, 21.5 MB | code + docstrings carry the operational vocabulary; already secret-free by policy and gate | fine-tune (code/ops Q&A), RAG |
| Docs | `docs/*.md` 139 tracked files, 46,794 lines (2c3f81b) | design rationale, incident narratives | RAG first; fine-tune after scrub |
| Skills | `skills/` + `.claude/skills/` 12 tracked SKILL.md files (2c3f81b) | procedures the agents follow | RAG; the panel should *not* learn agent-tooling procedures as user-facing behaviour |
| Personas | `src/common/personas.py` 46.8 KB (22 personas, preamble + task text) | the house voice; the single best fine-tune seed for tone | fine-tune |
| Second-brain vault (Nextcloud) | `vault_documents` 13,609 docs; `vault_notes_fulltext` 13,367 rows / 44 MB; local backup `/var/lib/corporatetraveldc/second-brain-vault-backup-2026-08-11` 411 MB (stale) | daily digests, watches, close-out notes, research | RAG primary; fine-tune only the agent-authored notes after PII scrub |
| Semantic layer | `semantic_note_derivations` ~1.05 M rows / 794 MB; `semantic_note_concepts` 107 k; `semantic_note_chronology` 26 k; `semantic_concepts` 99; `semantic_labels` 565; `semantic_relations` 74; `semantic_unmapped_tags` 475 | already-derived concept/relation graph over the vault | RAG index source / graph-RAG |
| Knowledge graph | `docs/SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT.md` (draft, not scheduled); `knowledge-graph-compile.timer` disabled by config | not yet a corpus | decision needed |
| Live ops stream | `hot_alerts` 40 k rows / 270 MB (route narratives, 5-min cadence); `osint_items` 1,840; `board_messages` 126 | the situational language the panel must speak | fine-tune Q→A pairs synthesised from (state block, local-resolver answer) |
| Chat history | `chat_messages` **0 rows** | no real usage to learn from | goldens must be synthesised (5.4) |
| Vocabulary / topical stores | none exist as separate stores today; vocabulary lives in `personas.py`, `_TOPIC_RX`, feed/skill code, and the vault tag set (`semantic_unmapped_tags`) | — | build one as a first deliverable |

### 5.2 What existing docs already specify
- `docs/SOVEREIGN_SKILL_SUPPLY_CHAIN.md` — skill snapshots are hashed/signed (`scripts/skill-snapshot.sh`); any corpus derived from skills must come from a signed snapshot, and the audit procedure there applies to a training set the same way (provenance by hash).
- `docs/SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT.md` — the graph-compile prompt and cadence; if the knowledge graph becomes a RAG source it must run on the Pi's maintenance window and the single llama slot (see the 2026-10-03 long-runner lock incident).
- Integrity model (`scripts/verify-manifest.sh`, `verified-exec.sh`): a rebuilt panel runs from the poller/web image lineage and must pass the in-image scoped gate; a model file or RAG index shipped into an image must be listed in the signed manifest or mounted from `/var/lib/corporatetraveldc` like the current GGUF.

### 5.3 Data-handling constraints before anything leaves the box (rented GPU)
- Absolute rule (CLAUDE.md): no real secret, credential or PII value in any artifact. `scripts/scrub-public-tree.py` (substitution table + regex sweeps + live-secret-value scan, now also filename scrub) is the existing scrubber — run the training corpus through it as a tree, and extend its substitution table for vault content (client names, addresses, phone numbers in `entities`, `osint_items` sources).
- The vault contains client/entity data (`entities` 51 rows from a contact import, board threads, EP advance notes): **exclude `entities`, `board_*`, `auth_*`, `audit_log`, `approval_requests`, anything under the vault's client/EP folders** from any off-box corpus by default; allowlist per folder.
- `hot_alerts.route_narrative` embeds live aircraft/train identifiers — fine for training the *format*, but date-shift and hash identifiers if the set leaves the box.
- Ship only a signed tarball with its own `MANIFEST.sha256(.asc)` (reuse `scripts/lib/public-manifest.sh`); keep the manifest on-box so the returned model can be tied to exactly the corpus it saw.
- Transport: Tailscale to the rented host, or an encrypted object store; never a public repo. The returned GGUF/LoRA goes through `scripts/safe-*`-style canary (load in a scratch llama-server on a spare port, replay the goldens, then swap the model path).

### 5.4 Parity test plan (golden transcripts)
There are no real transcripts (0 rows). Synthesise goldens from the contract:
1. **Topic goldens** — for each `_TOPIC_RX` topic, 3 phrasings × a fixed context fixture (captured JSON from the 8 endpoints, section 3 shapes) → expected structured local answer. These are deterministic and test the resolver without a model.
2. **Synthesis goldens** — 20 prompts over 3 frozen context fixtures (nominal day, RED CPS + VIP TFR day, degraded feeds day) with rubric assertions, not exact text: cites CPS score+label+narrative for go/no-go; never claims a TFR count other than the fixture's; plain text, no markdown; ≤ 350 tokens; says "no data" when the fixture is empty.
3. **Protocol goldens** — SSE event order, `X-Dispatch-Model`, `/model` inline vs body vs session semantics, 404 on history when untrusted, keep-alive first, fallback on backend down (kill the scratch llama-server mid-stream).
4. **Regression guards for the known defects** — weather/TFR/alerts/NOTAM must appear in the state block when present in the fixture (fails on the current code by design); history injected must be token-capped.
5. Replay harness: a pytest module under the sqlite guard that serves the fixtures from a stub dispatch API and a stub `/v1/chat/completions`, so the whole panel is testable on the Pi without the model.

---

## 6. Unknowns / decisions for the operator
1. **Persona ownership**: should the panel use the shared `chat` persona text (`personas.py`) plus state injection, or keep its own inline prompt? Today it is the inline one; the persona file is the better single source for fine-tuning tone.
2. **Identity strings in the prompt** (operator name, callsigns, affiliations, location) — keep in the system prompt (model sees them every turn) or move to a local config the rebuilt backend reads? They are in `main.py` today.
3. **History policy**: 40 turns forever vs. token-capped window vs. per-session; and whether `chat_messages` should be per-user once the ctdc-agent split lands.
4. **Trust gating of `/api/ask`**: currently open on the runner; gate it like history (404 when untrusted) or keep open on the tailnet-only instance?
5. **Fine-tune vs RAG-only first**: the 794 MB semantic layer + 13.6 k vault docs make RAG viable immediately on the Pi (needs an embedding model and an index store — none exists today); fine-tuning needs the GPU rental and the scrub pipeline. Recommended order: fix the context-shape bug (gives parity today) → RAG over vault+docs → synthesise goldens → fine-tune on personas + synthesised Q/A.
6. **Which model to fine-tune**: qwen3-4b (current) on a 2-thread, 12 k-context budget, or a smaller model with a bigger context for RAG? The box's constraint is the 2 cores reserved for llama, not RAM.
7. **Knowledge graph**: enable `knowledge-graph-compile.timer` as a RAG source (adds load in the maintenance window) or treat the semantic tables as sufficient.
8. **`/api/v1/brief` returns text** — make it JSON (it is dropped today) or have the panel accept text.
9. **Demo instance**: does the rebuilt panel need to serve the sanitised demo runner (:8005) with the same contract? Today both instances run the same code path.
10. **Vocabulary store**: build it from `_TOPIC_RX` + personas + feed names + `semantic_unmapped_tags` as a tracked JSON (so the resolver and the retriever share it), or leave it implicit in code.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-core.md`.


### Dispatch Panel — Parity Spec for the All-Local Rebuild

~~Written 2026-10-04 from the live code and a read-only look at the live system. Purpose: let someone rebuild the dispatch chat panel ("dispatch drawer" / chat bar) to behavioural parity **without reading the current implementation**, as an all-local service (no external agent logic) with a self-learning layer (RAG + fine-tune over the codebase, the second-brain vault and the topical/vocabulary stores; training on a rented GPU swarm, serving on the Pi). Every claim below carries a `file:line` so the rebuild can diff against the original. Lines refer to commit `97b9075`.~~


### Dispatch Panel — Parity Spec for the All-Local Rebuild › 1. Inventory — what the panel is today

| ~~Piece~~ | ~~Where~~ | ~~Notes~~ |
|---|---|---|
| ~~UI (drawer)~~ | ~~`src/runner/frontend/src/components/DispatchDrawer.jsx`~~ | ~~React; slide-up drawer with input box, history list, streaming bubble, `/model` menu~~ |
| ~~UI (second caller)~~ | ~~`src/runner/frontend/src/components/DispatchView.jsx:39`~~ | ~~Older full-view variant; POSTs `{message, history}` to the same endpoint~~ |
| ~~Backend~~ | ~~`src/runner/main.py` (`dispatch-runner`, FastAPI)~~ | ~~Prod instance port **8001** (tailnet + loopback); demo instance port **8005** with `DEMO_MODE=true` (`~/.config/containers/systemd/corporatetraveldc-runner{,-demo}.container`)~~ |
| ~~Chat endpoint~~ | ~~`POST /api/ask` — `src/runner/main.py:1636`~~ | ~~SSE stream~~ |
| ~~History endpoints~~ | ~~`GET /api/chat/history`, `DELETE /api/chat/history` — `main.py:1781,1790`~~ | ~~trust-gated (404 when untrusted)~~ |
| ~~Context source~~ | ~~`_build_context_rich()` — `main.py:1313`~~ | ~~8 parallel GETs to the dispatch API (`DISPATCH_BASE_URL`, container env = the Tailscale address :8000)~~ |
| ~~Local resolver~~ | ~~`_local_answer()` — `main.py:1416`; topic regexes `_TOPIC_RX` — `main.py:1301`~~ | ~~zero-LLM structured answers~~ |
| ~~LLM client~~ | ~~`_llm_stream()` — `main.py:1537` → `http://{llama_pool.HOST}:{port}/v1/chat/completions`~~ | ~~OpenAI-compatible streaming to llama-server~~ |
| ~~Pool facts~~ | ~~`src/common/llama_pool.py:53-59`~~ | ~~`HOST` = `LLAMA_POOL_HOST` (default the Tailscale address), one port `LLAMA_PORT`=8093; HOT/CHAT/REPORT all resolve to it~~ |
| ~~Persona table~~ | ~~`src/common/personas.py:87` (`PERSONAS`), `build_system_prompt()` :924, `persona_key_for()` :943~~ | ~~22 personas; `chat` tier=chat temp 0.3 top_p (see table) num_predict 350~~ |
| ~~Model serving~~ | ~~`~/.config/systemd/user/corporatetraveldc-llama.service`~~ | ~~`llama-server -m qwen3-4b-instruct-2507-q4_0.gguf --host <tailscale> --port 8093 -np 2 --kv-unified -c 12288 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 2 --cache-ram 0 --no-webui`, `Slice=production.slice`~~ |
| ~~Persistence~~ | ~~`chat_messages` table — `src/common/pg_schema/0056_dispatch_chat.sql`; helpers `main.py:2007-2031`~~ | ~~Postgres via `common.db.conn()`; **0 rows live** (never used in prod since the 2026-09-20 cutover, and 0 before it)~~ |
| ~~Front proxy~~ | ~~`nginx/conf.d/tailscale-dispatch-runner.conf`~~ | ~~`limit_req zone=corporatetraveldc_lr burst=20 nodelay` (zone `10r/s`, `nginx/conf.d/00-rate-limit-corporatetraveldc.conf:34`), `proxy_read_timeout 120s`~~ |
| ~~Audit trail~~ | ~~none on `/api/ask`~~ | ~~`audit_log` is written by admin/approval paths only; chat writes `chat_messages` only~~ |


### Dispatch Panel — Parity Spec for the All-Local Rebuild › 1. Inventory — what the panel is today › What is external today ("external agent logic" to remove)

- ~~**Nothing in the request path is cloud.** `/api/ask` → runner → llama-server (local) → dispatch API (local). The Anthropic fallback the docstring at `main.py:1646` still describes was removed at the 2026-08-27 cutover (`has_llm = True`, `main.py:1659`).~~
- ~~Residual config only: `OLLAMA_BASE_URL`, `OPENWEBUI_URL`, `OPENWEBUI_API_KEY` (`main.py:98-100`) are read but unused by the chat path. `openwebui` is a separate container (not in the panel's path).~~
- ~~The *persona text* embeds the operator's identity and locality strings (`main.py:1697-1716`) — these are part of the behavioural contract (section 2) and must be sourced from a local config, not hard-coded, in the rebuild.~~


### Dispatch Panel — Parity Spec for the All-Local Rebuild › 2. Behavioural contract (checklist)

- ~~[ ] `CPS: <score> / <label> — <narrative>`~~
- ~~[ ] `WEATHER (METAR):` up to 6 stations, raw METAR text~~
- ~~[ ] `TFRS: N active (M VIP/POTUS) — id, id, …` or `TFRS: none active`~~
- ~~[ ] `FEED ERRORS: …` | `FEEDS STALE: …` (age > 900 s) | `FEEDS: nominal`~~
- ~~[ ] `NWS ALERTS (N): headline; headline; headline`~~
- ~~[ ] `AMTRAK/WAS: <summary>`~~
- ~~[ ] `NOTAMS: N active`~~
- ~~**DO NOT REPLICATE** — live shape mismatch (verified 2026-10-04 against the API): weather reads `.stations` (API: `metars[]` with `station/ceiling_ft/visibility_sm/wind_kt`, no raw text), tfr/alerts/notam are read as lists (API: `{tfrs[]}`, `{alerts[]}`, `{notams[]}`), so **today the model receives only CPS/FEEDS/AMTRAK and is told "TFRS: none active" while ~78 are active**. The local resolver (`:1416`) has the identical mismatch for the weather/tfr/notam/alerts topics, so those topics fall through to the LLM. The same bug class was fixed in `restore_dispatch_state.py` on 2026-10-03. The rebuild must consume the shapes in section 3.~~


### Dispatch Panel — Parity Spec for the All-Local Rebuild › 5. Self-learning plan — skeleton

**~~5.1 Corpora on this box (read-only inventory, 2026-10-04)~~** *(former heading)*


### Dispatch Panel — Parity Spec for the All-Local Rebuild › 5. Self-learning plan — skeleton › 5.1 Corpora on this box (read-only inventory, 2026-10-04)

| ~~Corpus~~ | ~~Location~~ | ~~Size~~ | ~~Suitability~~ |
|---|---|---|---|
| ~~Platform source~~ | ~~repo `src/` (68,081 Python lines), 1,132 tracked files, 19 MB~~ | ~~code + docstrings carry the operational vocabulary; already secret-free by policy and gate~~ | ~~fine-tune (code/ops Q&A), RAG~~ |
| ~~Docs~~ | ~~`docs/*.md` 134 files, 44,886 lines~~ | ~~design rationale, incident narratives~~ | ~~RAG first; fine-tune after scrub~~ |
| ~~Skills~~ | ~~`skills/` + `.claude/skills/` 16 tracked SKILL.md files~~ | ~~procedures the agents follow~~ | ~~RAG; the panel should *not* learn agent-tooling procedures as user-facing behaviour~~ |
| ~~Personas~~ | ~~`src/common/personas.py` 46.8 KB (22 personas, preamble + task text)~~ | ~~the house voice; the single best fine-tune seed for tone~~ | ~~fine-tune~~ |
| ~~Second-brain vault (Nextcloud)~~ | ~~`vault_documents` 13,609 docs; `vault_notes_fulltext` 13,367 rows / 44 MB; local backup `/var/lib/corporatetraveldc/second-brain-vault-backup-2026-08-11` 411 MB (stale)~~ | ~~daily digests, watches, close-out notes, research~~ | ~~RAG primary; fine-tune only the agent-authored notes after PII scrub~~ |
| ~~Semantic layer~~ | ~~`semantic_note_derivations` ~1.05 M rows / 794 MB; `semantic_note_concepts` 107 k; `semantic_note_chronology` 26 k; `semantic_concepts` 99; `semantic_labels` 565; `semantic_relations` 74; `semantic_unmapped_tags` 475~~ | ~~already-derived concept/relation graph over the vault~~ | ~~RAG index source / graph-RAG~~ |
| ~~Knowledge graph~~ | ~~`docs/SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT.md` (draft, not scheduled); `knowledge-graph-compile.timer` disabled by config~~ | ~~not yet a corpus~~ | ~~decision needed~~ |
| ~~Live ops stream~~ | ~~`hot_alerts` 40 k rows / 270 MB (route narratives, 5-min cadence); `osint_items` 1,840; `board_messages` 126~~ | ~~the situational language the panel must speak~~ | ~~fine-tune Q→A pairs synthesised from (state block, local-resolver answer)~~ |
| ~~Chat history~~ | ~~`chat_messages` **0 rows**~~ | ~~no real usage to learn from~~ | ~~goldens must be synthesised (5.4)~~ |
| ~~Vocabulary / topical stores~~ | ~~none exist as separate stores today; vocabulary lives in `personas.py`, `_TOPIC_RX`, feed/skill code, and the vault tag set (`semantic_unmapped_tags`)~~ | ~~—~~ | ~~build one as a first deliverable~~ |
