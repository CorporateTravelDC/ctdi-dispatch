# CHANGES — agents, second brain, models domain

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET. Read-only; no tracked file touched. Evidence commands were run as the operator account without sudo.

Docs rewritten (9): `AGENTS.md`, `CONTRIBUTORS.md`, `docs/AGENT_SEGMENTATION.md`, `docs/TEAM_SEGMENTATION_SHOWCASE.md`, `docs/SECOND_BRAIN_STATUS.md`, `docs/SECOND_BRAIN_STATUS.example.md`, `docs/SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT.md`, `docs/DEDICATED_MODELS_PLAN.md`, `docs/lmstudio-dispatch-prompts.md`.

Claims corrected: 80 (one per table row below).

---

## AGENTS.md

| claim as written | evidence | fix |
|---|---|---|
| "Last dumped to second brain: 2026-09-27" implied an automatic dump | `grep AGENTS.md scripts/second-brain-weekly-dump.sh` → no hit; dump handles CLAUDE.md + memory only | stated the dump does not touch this file |
| (silent) Codex runs here | `ls ~/.codex` (last write 10-04), `which codex` → operator `~/.local/bin/codex`; `pgrep codex` → none; registry `ctdc-agent-openai-codex agent claude 1` | added current Codex status |

## CONTRIBUTORS.md

| claim | evidence | fix |
|---|---|---|
| Georou = "Core submodule" | no `.gitmodules` in this repo or `pihole-unbound-selinux-internal`; that repo's `scripts/install-pihole-selinux.sh:6` `git clone https://github.com/georou/pihole-selinux.git` | "upstream module, cloned by the companion repo, not a submodule" |

Other entries are acknowledgements, not checkable claims; FAA program list confirmed referenced in code (`grep -ril` ADX 7 files, LADD 21, NSSR 4, NASR 4).

## docs/AGENT_SEGMENTATION.md

| claim as written | evidence | fix |
|---|---|---|
| "privilege separation designed ... not executed -- no team account exists yet" | `cat /etc/ctdc-accounts.conf` (5 lines); `getent group ctdc-agents` (5 members) | status table with the five live accounts, uids, kinds |
| Agent subset carries `DISPATCH_ADMIN_TOKEN`, `NTFY_TOKEN`, `NEXTCLOUD_ADMIN_USER/APP_PASSWORD`, `BOARD_KEY` | `secrets-allowlist-agent.txt`: all marked REMOVED 2026-10-04; live names = `NTFY_AGENT_TOKEN?`, `LLAMA_BASE_URL?`, `OLLAMA_BASE_URL?`, `DISPATCH_PG_HOST/PORT/DB`, `DISPATCH_PG_RO_*?` | rewrote the allowlist section from the files |
| Ops subset carries `NTFY_TOKEN` | `secrets-allowlist-ops.txt`: NTFY_TOKEN REMOVED, `NTFY_OPS_TOKEN?` | fixed |
| "Containers keep reading the production file via EnvironmentFile= (59 Quadlets)" / split into `agent-secrets.env root:ctdc-agent` | `grep -l 'EnvironmentFile=/etc/corporatetraveldc/svc/' .config/containers/systemd/*.container \| wc -l` → 53; `... dispatch-secrets.env` → 0; `ls /etc/corporatetraveldc/svc` → 9 files | replaced with scoped-env facts |
| "52 quadlets moved off" | same grep → 53 | 53 |
| "/etc/corporatetraveldc/dispatch-secrets.env (0600, 62 keys)" | `stat` → corporatetraveldc:corporatetraveldc 600; key count not re-read (rule) | dropped the count |
| Known-agents table: Claude Code remote-control in operator's `~/.config/systemd/user`, Slice=agents.slice; Codex daemons from SSH; Cowork via scoped board token | `systemctl --user show corporatetraveldc-claude-remote-control` → inactive/disabled; `pgrep -u ctdc-agent-anthropic-claude -fa remote-control` → `(CTDC-AGENT-ANTHROPIC-CLAUDE) ...`; no codex process; Cowork = service account + gateway | replaced by the account table |
| Operator-side agent sessions are covered | `cat /proc/self/cgroup` → `user-1000.slice/session-c8.scope`; `pgrep -u corporatetraveldc -fa claude` → 2× `claude --resume` | added "Known gap" paragraph |
| Phased migration steps 1-7 ("Create ctdc-agent", "--add-agent ctdc-codex") | accounts exist under new names; `ctdc-codex` never existed | removed; history pointer to showcase |
| Stage A/B/C runbook with `ctdc-agent` | `getent passwd ctdc-agent` → none; plan.sh 235/266 `ctdc-agent` branches only match the old name | removed; noted branches are dead bootstrap |
| "Target team" block (step 1 creates `ctdc-agent-cowork --login-mode ssh`) | cowork is `service none 0`, nologin (`getent passwd`); plan.sh `--convert-to-service` (lines 437-449) | replaced with current add/activate commands; documented `--convert-to-service` (absent from old doc) |
| `register ctdc-agent-cowork ... --kind agent` | registry kind service | removed |
| Liveness "agent/claude" factor omits session check | team-liveness.sh ~l.140-145 `session=INACTIVE` stale verdict | added |
| Locked = `passwd -S` | team-liveness.sh l.160-165 comment: shadow `!` + real hash since 2026-10-05 | fixed |
| Install block (`sudo cp systemd/system/...`, `register corporatetraveldc ~/.ssh/cowork_ed25519.pub`) | units installed and active (`systemctl list-units`) | removed install recipe, stated live state |
| "Inert ... deactivate its board signer ... when that hook exists" | hook exists (`board-signer-ctl.sh deactivate`) | fixed |
| Impact tiers: `high` = "high-impact actions" | `BOARD_AUTH_POLICY` main.py:271-285 → no route uses `high` or `cosign`; `signed` tier exists (council, close, workspace) | table with routes; `signed` added; high/cosign "none yet" |
| Tests "27 tests" skill grants | `grep -c 'def test_' tests/scripts/test_skill_grants.py` → 28 | 28 |
| (no count) liveness tests | → 63; board_signer 22; signed_approvals 21; agent_gateway 15 | added counts |
| Skill grant examples use `ctdc-agent`, `ctdc-agent-cowork` | renamed | current names |
| Root installed copies list: team-liveness, watchdog, renew-tailscale-cert | `ls /usr/local/libexec/ctdc` → 13 entries | full list |
| "skills synced from the repo" agent home | plan.sh l.219-220 grants via skill-grants.sh | fixed |
| Gateway: "one remote-MCP endpoint per agent identity" | only add-connector mechanism exists; per-agent slugs requested 10-05, not verified as created | marked UNVERIFIED |
| Gateway tests (no count); migration only 0071 | 15 tests; `agent_gateway_settings` in 0073 | added |
| Gateway: pending/code TTLs, loopback redirects not documented | agent_gateway.py:48-49, 194-216 | added |
| Open question: Cowork `ctdc_cowork_*` HTTP key stays; SSH arrival = `ctdc-agent-cowork` | cowork converted to service; reaches platform via gateway | removed; replaced |
| Open question: `gui-window.sh` as agent | out of scope / not current | removed |
| llama council "skips at load1 >= 12" (no outcome) | `journalctl -u corporatetraveldc-llama-council` 3 d: 30 skips on load, 1 no-convene | added outcome |
| `rename` loop moves `/var/lib/ctdc-liveness/orders/F` | orders live at `/var/lib/corporatetraveldc/team-liveness/orders/` (team-liveness.sh `ORDERS_DIR`) | noted in findings |
| Preload/activate: "must log in within 7 days of activation" | `created_at()` = home birth time (team-liveness.sh:174-175); grace 3 d from creation | corrected; finding |

## docs/TEAM_SEGMENTATION_SHOWCASE.md

| claim | evidence | fix |
|---|---|---|
| Repo row: "write via ctdc-dev (setgid, sharedRepository=group)" | `stat` repo `drwxr-sr-x`; `git config core.sharedRepository` → false | read-only |
| Account names `ctdc-agent`, `ctdc-agent-cowork` as current | registry | rename banner; narrative kept as dated history |
| Model table lacks service kind | registry | added |
| Secrets cells imply admin tokens | allowlists | described actual contents |
| Tiers high/cosign presented as live | main.py policy | marked unused; added `signed` |
| Login factor only claude/human | team-liveness.sh header | added ssh/service/session |
| Pamphlet "repos discovered from group write bits" | render-onboarding.sh l.55-61 (read and write) | fixed |
| "How a new agent is added" uses `--add-agent ctdc-agent-openai-codex` | account exists, preloaded | three-column table incl. `--activate` |
| Phrasing 13 "Agents never sign" | agents sign board posts | "never commit-sign" |
| Images "stay private until REVIEWED_BINARY_OK" | `scrub-public-tree.py` REVIEWED_BINARY_OK → none of the 15 | confirmed, stated |

## docs/SECOND_BRAIN_STATUS.md

Rewritten from a 1,065-line dated log to a current-state doc; superseded history cut.

| claim | evidence | fix |
|---|---|---|
| semantic-compile-daily daily 03:47 ET | `systemctl --user cat ...timer` → `00,06,12,18:02 America/New_York` + `.path` | fixed |
| entity-tracking-digest "every 6 h" (unspecified) | `00,06,12,18:12` ET | fixed |
| second-brain-weekly Sun 18:15 ET | timer → `Mon 04:30 America/New_York` | fixed |
| daily 23:45 + 2-hourly refresh | confirmed; plus `ExecCondition=maintenance-window-guard.sh`, flock; day file = ET operational day (common.optime) | added |
| index-scan 04:00, demo-archiver 04:15, rss 0/2:10 | confirmed | kept |
| semantic numbers (40,253 assignments, 79/100 tags, coverage 0.42) | journal 16:12Z: 109,454; 115/605 mapped; 490 unmapped | current numbers |
| 983-row PK defect "NEEDS OPERATOR DECISION" | 0055: PK `(path, concept_id, rule, evidence)` and `(path, relation, target, evidence)` | resolved |
| Provenance h3 bleed open | compile.py:450 "FIXED ... level-aware" | resolved |
| Exports land in `/var/lib/corporatetraveldc/semantic/` (implied current) | `ls -la` → all dated Aug 18 | finding |
| `remember.py` path only; webdav surface lacks `put_create_only` | `grep '^def ' webdav_client.py` | added |
| `doc_generation.py` not mentioned | file exists (750 lines) | added |
| `index_db --scan` healthy | journal 2026-10-06 08:00Z: PROPFIND 401 | finding |
| knowledge-graph "not scheduled" | `knowledge-graph-compile.{container,timer,path}` tracked; timer disabled, path active; live graph 10:58Z | fixed + finding |
| graph meta 545 curated notes | live `/var/lib/corporatetraveldc/knowledge_graph/graph.json` → 2,959 / 3,058 nodes / 11,425 edges | current |
| RSS catalog 32 feeds / 11 keys | re-counted → 11 / 32 | kept |
| 15 tables in 0055 | 15 distinct + `semantic_note_instance_refs` in 0059 | stated |
| semantic tests "31/31" | `grep -c def test_` → 43 | 43 |
| research-board-mirror, personal-notes-import, personal-export-watch, maintenance-queue enrolment absent | timers/queue file | added |
| Gate-2 dump timing | timer `Sun 02:00:00` no TZ; next 2026-10-11 02:00Z | finding |
| "Ollama narrative" | llama.cpp via common.llm | fixed |

## docs/SECOND_BRAIN_STATUS.example.md

| claim | evidence | fix |
|---|---|---|
| (template) | `scrub-public-tree.py:153` DROP_FILES confirmed | added header; two template rows (scheduled writers with timezone; agent access) |

## docs/SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT.md

| claim | evidence | fix |
|---|---|---|
| "no timer, service, or schedule has been installed" | `retrofit-links` and `knowledge-graph-compile` container+timer tracked (both timers disabled live; graph `.path` active) | status table |
| Wire it like `weekly-doc-drift-check.sh` with `claude --model fable -p` | that script header: rewritten 2026-09-21 after six failed cloud-CLI runs; now deterministic facts + `local-llm-synthesize.sh` | recorded as abandoned; trend spec recast |
| Prompt sources env files / 127.0.0.1:8090 | standing rule; webdav_client container base | dropped |
| graph.json 545 notes "last real build" | repo copy 2026-08-18; live copy 10-06 | fixed |
| Weekly second-brain Sunday 18:15 | Mon 04:30 ET | fixed |

## docs/DEDICATED_MODELS_PLAN.md

Rewritten as current state + abandoned + unbuilt.

| claim | evidence | fix |
|---|---|---|
| ~23 personas | `grep -cE "^    '[a-z0-9-]+': *\{" personas.py` → 22 (chat 1, hot 2, report 19) | 22 |
| `CPUWeight=9000` | unit drop-in `CPUWeight=10000`; also `IOWeight=10000`, `MemoryLow/High`, `MemorySwapMax=0` | full table |
| Ollama per-task models, smoke/promote gate, `_abandon_ollama_generation`, host governance | no Ollama, `ss -ltn` only :8093 | moved to abandoned table |
| Model transfer workflow via `ollama create` | no Ollama; one resident model | rewritten for llama.cpp |
| Fine-tune base phi3:mini | model is Qwen3-4B since 09-21 (`/v1/models`) | fixed |
| n_ctx not stated | `/v1/models` n_ctx 12288 | added |
| 24 call sites / 19 files | grep → 22 files | 22 |
| `corporatetraveldc-pi5-brief` decision pending | still in 3 label chains + docstrings | kept as finding |

## docs/lmstudio-dispatch-prompts.md

| claim | evidence | fix |
|---|---|---|
| Banner: inference is hot/chat/report tiers over phi3-mini | single server, Qwen3-4B | fixed |
| "real knob is OLLAMA_BASE_URL"; "re-derive with ollama list" | `LLAMA_BASE_URL`; no ollama | fixed |
| 340 lines of per-skill prompts | not running anywhere | removed (git history pointer) |

---

## [UNVERIFIED] items

- Contents of `/etc/ctdc-agent/agent-secrets.env` / `/etc/ctdc-ops/ops-secrets.env` (root:group 0640, not readable; not attempted).
- Gateway connector rows (DB not queried); `cowork` → `ctdc-agent-anthropic-cowork` inferred from the working claude.ai connector in this session's tool list. Whether per-agent slugs exist.
- Whether `second-brain-weekly` (and the other Monday weeklies) ran on 2026-10-05: `LastTriggerUSec` empty; a broad journal scan was stopped to avoid load.
- Vault folder listing (`05-Skills/`, `06-AI-Memory/` subfolders) not re-read.
- Whether Cowork's `06-AI-Memory` scheduled tasks still run.
- Root cause of the index-scan 401.
- sudoers.d contents (directory unreadable); `/var/lib/ctdc-liveness/skill-grants.json` (root 0700).

## Findings for the operator

1. **Operator-account agent sessions are outside the segmentation.** Two `claude --resume` processes run as uid 1000 in `session-c8.scope` (not `agents.slice`, full read of `dispatch-secrets.env`). The model constrains team accounts only.
2. **Skill grants have HELD every hour since at least 13:07Z** on 2026-10-06: "checkout does not verify against the signed manifest" — the working tree carries an uncommitted `scripts/stack-refresh.sh` edit. Grants/clawbacks are not being applied while the tree is dirty.
3. **`second-brain-index-scan` PROPFIND 401** at 2026-10-06 08:00Z, unit exit 0 (silent failure; `vault_documents` not refreshed).
4. **`knowledge-graph-compile.timer` and `retrofit-links.timer` are disabled** live while tracked with a 6-hour grid; the graph rebuilds only on manual notes and the retrofit never runs.
5. **Semantic exports stale since 2026-08-18** (`/var/lib/corporatetraveldc/semantic/*`); the scheduled compile only refreshes Postgres.
6. **Timers without a timezone now follow UTC** (host clock UTC since 2026-10-06 ~17:15Z): `second-brain-weekly-dump` (`Sun 02:00`, next 2026-10-11 02:00Z = Sat 22:00 ET, was 02:00 ET), `docs-drift-weekly` (`Mon 09:00`, now 09:00Z = 05:00 ET), `pull-path-verify` (06,18:00). Others in the list are interval-style and unaffected.
7. **llama council participant effectively never runs**: 30 of 31 runs in 3 days skipped at load1 >= 12 (observed 17-19).
8. **Preload grace counts from account creation**: `ctdc-agent-openai-codex` (created 2026-10-05) loses its 3-day grace after 2026-10-08; activating it later without same-sitting login + signer activate makes it inert at the next hourly run.
9. **Cloud pamphlet text is stale for Cowork**: `publish-pamphlet-to-vault.sh` prepends "your only credential is the board token ... X-Board-Key ... refresh every 12 h"; Cowork now uses the gateway connector (its board token lapsed 2026-09-29 per the session scratchpad).
10. **Leftover kill-order dirs for retired names**: `/var/lib/corporatetraveldc/team-liveness/orders/ctdc-agent` and `.../ctdc-agent-cowork` (root-owned, harmless, but plan.sh `--rename-account` moves `/var/lib/ctdc-liveness/orders/<F>`, which is not where order dirs live, so renames leave these behind).
11. **No route uses the `high` or `cosign` board tiers**; they are implemented and tested but protect nothing yet.
12. **Stale code references to dead models/ports**: `corporatetraveldc-pi5-brief` in 3 label chains and 5 docstrings; `personas.py` tier comment (ports 8094/8095-9005); `config/dispatch.env.example` lines 93-96 still point deployers at Ollama `:11434`; `config/dispatch.env` `OLLAMA_OSINT_MODEL=corporatetraveldc-pi5-osint` maps to no persona; disabled `corporatetraveldc-ollama-swap-alert.service` still installed.
13. **Read-only Postgres role** (`DISPATCH_PG_RO_*`) still not created; agents have no DB read path.
