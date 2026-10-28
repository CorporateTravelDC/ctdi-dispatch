> Status 2026-10-25: record of the 2026-10-12 audit, not rewritten (checked 2026-10-26 UTC against HEAD 5f7fca01 and the live box). Findings no longer current. Backups: off-box Proton sync is configured and has run nightly since 2026-10-13 (14 sync entries); backup freshness checks every agent account; agent snapshots exclude key files, Claude session folders and browser profiles, with a tripwire and a settable exclude list; the Borg repo is 55 GB with 45 GB free, and the disk is 81% full. Agents: the Llama backstop's model maps to a persona (2026-10-16); codex is inert since 2026-10-22; skill grants and render-onboarding cover codex-mode accounts; the leak scan covers every agent account hourly from a root timer. Demo: the demo containers use only the demo Postgres instance, `/api/ask` is refused on the demo for untrusted callers, and the demo corpus is that instance, refreshed nightly. OOOI: every writer goes through the authority gate since 2026-10-16 (FIDS denied since 2026-09-27); the local-receiver escape hatch is still unreachable. Units and counts: 79 user timers live (78 tracked), 9 root timers, 144 user unit files, 83 migrations, 38 verified-exec units, 135 web routes, 1,712 manifest lines, 255 test files, 53 `src/common` modules, 9 vertical profiles; the SDR silence/link watchdogs are enabled; 17 units use `unit-failure-notify@`; 13 timers carry `RandomizedDelaySec` (9 user timers and 4 root system timers, live and tracked); the mcpo units and the nms-v240 timer were retired 2026-10-20; `production.slice` is 6.0 / 13.1 / 13.5 GiB (low/high/max); the integrity sweep runs six checks (seven on the main checkout). Feeds: six ADS-B destinations (OpenSky container added); acars-watcher also matches local VDL2 (7 days: 19 airframes, 7 local); the backup uplink (enu1) probes OK; `flight_resolver` no longer calls AeroAPI (2026-10-18). Governance: approval TTLs are a per-kind table, and board-gate approvals have their own kind with a clamped 300-7200 s TTL (2026-10-16); the shadowed watchlist session routes were removed 2026-10-15. Wrong in the audit itself: `PUSH_FEEDS` and the kickover guardrail already included amtrak at `ddac65e7`, and the pre-commit env-value rule exists as a separate check (28+ characters); only the 64-hex rule is missing. ~~The integrity sweep is failing again at this check (two staged, unsigned unit files and its Claude Code policy check), so the "now success" row no longer holds.~~ (superseded 2026-10-25: the integrity sweep was failing at the first check of this pass; at 2026-10-26 22:08Z it ends with Result=success, with 12 successful runs and 0 failed runs in the previous 3 h, so the "now success" row holds again.)

> **Status (2026-10-13):** committed as the record of the 2026-10-12 multi-agent code-to-docs audit (16 batches, 309 verified findings, read-only at `ddac65e7`). Findings fixed since are closed in the commits that fixed them (e.g. `127a2f76`, `8389b2e7`); the remaining Tier A/B/C doc work is tracked in the backlog ahead of COGS and the investor documents. Generated output lives in `/var/lib/corporatetraveldc/reports/docs`; this copy is the reviewed report.

# Code-to-docs parity audit: verified findings report

**Scope:** private repo at HEAD ddac65e7, read-only. Nothing was edited, staged, restarted or built.
**Inputs:** 309 verified findings from 16 batches: 14 code and doc batches, the cost/investor batch, and the completeness critic.
**Audience:** the operator. This report should drive the documentation work **before** any COGS or investor-document update.

---

## 1. Summary

### Counts by kind (309 raw findings)

| Kind | Count | Meaning |
|---|---|---|
| undocumented | 102 | The component exists and runs, but no living doc explains it |
| stale | 100 | A living doc describes an older state |
| wrong | 59 | A living doc states something the code or live state contradicts |
| unlinked | 31 | A good explanation exists, but README cannot reach it |
| missing-index | 16 | A whole subsystem has no index or overview |
| orphan-doc | 1 | A doc is reachable from nothing and is partly obsolete |

### Counts by investor/COGS relevance

| Relevance | Count |
|---|---|
| high | 82 |
| medium | 118 |
| low | 89 |
| none | 20 |

Batches overlapped, so many raw findings describe the same problem. After merging duplicates there are roughly 250 distinct issues. These are the most repeated:

| Topic | Batches that raised it |
|---|---|
| Overnight Llama backstop | 9 |
| Backups | 9 |
| airplanes.live fallback | 7 |
| Deployment profiles | 7 |
| Cloud-LLM ("zero cloud LLM") claim | 5 |
| Unit and Quadlet counts | 5 |
| Approval TTL table | 2 |
| CIFP link | 3 |
| Skills catalog | 2 |

### Headline gaps in plain language

1. **"No cloud LLM is called" is false.** A weekly timer runs Claude Opus through the Claude CLI and sends CLAUDE.md and memory content. Codex and ChatGPT/Cowork agents also run on vendor plans. Five docs repeat the false claim: README, DESIGN-PRINCIPLES, COMPLIANCE_SECURITY, ISO_42001, and the COST_STRUCTURE/COGS "$0, only power" line. This is the single biggest COGS and investor exposure.
2. **Flight resolution is not "local only."** The airplanes.live fallback is live as of 2026-10-08/09. DATA_SOURCES still says "not queried," and README and executive_summary.md say resolution is local.
3. **Backups are real but undocumented, and they are on-box only.** README never mentions them and BACKUP.md is effectively unlinked. Three BACKUP.md claims are wrong:
   - It says off-box sync happens. In fact the rclone/Proton target is not configured and nothing pages about it.
   - It says the freshness check covers every agent snapshot. In fact it checks one of six accounts.
   - It says credentials are excluded from agent snapshots. In fact only filename globs are excluded, and a session `.key` file was captured.
   - It also says CUI is "shredded" on btrfs, where shred is ineffective. The investor docs must not claim offsite or DR backups yet.
4. **The overnight Llama backstop is deployed but documented as "not deployed."** INFRA_MAP.md:43 still calls it an uncommitted working copy. Its persona or model may not resolve, so it may never generate anything.
5. **Most platform capabilities have no "what it is / how it works" doc.** There is no skills catalog: ops-brief, OSINT, the VIP TFR/route-impact skills, the intel watches, EP advance, board-sweep, and ES publishing are all unexplained. The ingest README calls detectors "staged only" when they are live. SDR_SERVICES overstates the acars-watcher and omits outbound data sharing.
6. **Security and governance docs lag the code.**
   - Approval TTL table: AGENT_TRUST_MODEL disagrees with GUARDRAILS_JUSTIFICATION.
   - Missing audit events: `board.signed.request`, `approval.withdrawn`, `council.status`, `console.login.retap`.
   - GUARDRAILS_PROVEN overstates the transcript leak scan. Codex and agent-account transcripts are not scanned.
   - COMPLIANCE lists common.guardrails as an audit writer, but it has no callers.
   - common.config loads the master secrets file into host processes, which is not recorded.
   - The demo isolation claim is wrong: both demo containers hold a live Postgres socket, and `/api/ask` is ungated on the demo hostname.
7. **Investor capability claims are stale.** Examples:
   - "21 Ollama models": it is now one Qwen3-4B model on llama.cpp.
   - "ACARS fusion pending off-box fix": the cause was on-box and the fix shipped 2026-10-03.
   - "2.45 GB growing demo corpus": it is 1.88 GB, frozen on 2026-08-14.
   - "no CPS test" and "23 test files": a CPS RVR test now exists, and there are 115 test files.
   - "single SQLite DB": the database is Postgres.
   - "redundant uplinks": the backup uplink has been down since 2026-10-06.
   - The COGS totals contradict themselves.
8. **README is not an index.** About 30 living docs are unreachable within two clicks, including BACKUP, GUARDRAILS_PROVEN, CIFP_DATA, deployment-profiles, OOOI_SOURCE_AUTHORITY, CODESHARE, SOVEREIGN_SKILL_SUPPLY_CHAIN, COMPLIANCE_SECURITY (linked only from struck text), the cost and investor docs, and AIRLINE_TIME_FIDELITY_SPEC.

### Backlog facts these findings touch

- **Backlog item 1 (skeleton composition core) is already built.** composition.py and its tests landed in 3d030992, but the spec still says "spec only."
- **Backlog item 2 (Proton `rclone config` plus a test run) is confirmed still open.** The ledger has zero sync entries.
- **The Monday 2026-10-12 airline time-fidelity checkpoint has no findable home.** Its spec and timer are linked from nowhere outside docs/research. Linking them is an S-size task worth doing before the checkpoint.
- **The public-history rewrite is still open (unverified 2026-10-25).** README.md:765-769 wrongly implies it is done for the three LADD identifiers.

---

## 2. Doc-update plan, ordered

Tier A unblocks the COGS and investor update and should be done first. Tier B is capability and subsystem documentation that the investor capability tables cite. Tier C is hygiene. Tier D is a list of code follow-ups surfaced by the audit. Those are not doc tasks and must wait until tracked files unfreeze.

### Tier A: cost drivers, security and governance posture, claims investors will read

| # | Target file and section | What to add or fix | Closes | Size |
|---|---|---|---|---|
| A1 | README intro (:11-12); COMPLIANCE_SECURITY §1/§2 (:9-10, :32-35, :60); DESIGN-PRINCIPLES §2 (:33-34) and §4 vendor table (:85-92); ISO_42001 A.7/A.9/A.10 (:45, :48) | Replace "zero cloud LLM" with an exact inventory: (1) the closed llm.py fallback; (2) the weekly `claude --model opus` second-brain dump (what it sends, schedule, Llama backstop); (3) the on-box vendor-CLI agents (Claude, Codex) and gateway agents (Cowork, ChatGPT). Add vendor rows for Anthropic, OpenAI, airplanes.live and the backup target. Downgrade ISO A.7/A.10 to Partial unless justified. Fix the Postgres cutover date. | cloud-LLM claim (×5), commercial vendors, ISO A.7/A.10, PG cutover date | M |
| A2 | DATA_SOURCES.md §3 (plus README.md:389-394, watchlist_README identity section, executive_summary.md:19-21, REGIONALIZATION §5, LADD_CUI_HANDLING exposure section) | Rewrite airplanes.live as "fallback only, enabled on this deployment" (flag, order, LADD never sent, rate limit and cache, no OOOI authority). Add entries for AWC airsigmet, FAA CIFP (linking CIFP_DATA.md), local receivers (ADS-B, VDL2), and derived data (codeshare_map, time-fidelity collector). Record that FLIGHTAWARE_API_KEY reaches 7 allowlists, so AeroAPI may be a live metered cost, then verify presence without printing it. Mark the LADD public download retired 2026-10-08. Make METAR stations profile-sourced. | airplanes.live (×7), AWC airsigmet (×2), CIFP link (×3), local receivers, codeshare/time-fidelity source, FlightAware AeroAPI, LADD download, METAR profile | L |
| A3 | BACKUP.md (top status, §Agents, flags, sizing); README Status, Key paths and backups section; COMPLIANCE_SECURITY backup/DR paragraph; INFRA_MAP §4.4/§4.7; SECRETS_NAMING registry; LADD_CUI_HANDLING storage and shred wording; NETWORK_STORAGE_CONSTRAINTS; PI5-BOOT-CONFIG recovery | Add a dated "Current state: off-box target not configured; on-box only; freshness does not page for it." Correct agent-snapshot freshness coverage (1 of 6) and "credentials excluded" (glob list plus residual risk). Change "shredded" to "deleted (shred best-effort on btrfs)". Document flags, the Borg exit-1 warning, exclusions and the restore-test scope. Re-measure size (19 GB repo, 54 GB free). Add the five timers to INFRA_MAP, `backup/borg-passphrase` to the secrets registry, and Borg restore to the Pi5 recovery steps. Link everywhere. | backups (×9), off-box sync, freshness coverage, CUI shred, snapshot credentials, sizing, registries, ctdi-backup behaviours, network-storage link, Pi5 recovery | L |
| A4 | README "Local LLM"; SECOND_BRAIN_STATUS weekly-dump row; INFRA_MAP:43 and §4.7; DEDICATED_MODELS_PLAN caller list (link it from README); SINGLE_EDGE timeout row; DISPATCH_PANEL_PARITY §4/§5.3 | Document the Llama backstop: failure marker, 6-hourly UTC check, `flock -w 1800` with exit 75 meaning skip, read-only memory mount, and that it never resets anything. It serves second-brain-weekly-dump only; docs-drift-weekly is now local. Record the persona/model-mapping risk. List every llama-slot consumer and the artifacts.sha256 ExecStartPre gate. | llama backstop (×9), docs-drift description, DEDICATED_MODELS link and caller list, llama digest gate (×2) | M |
| A5 | README snapshot table (:16-26, :34, :112, :580, :614, :616, :702); REFERENCE_INFRA §4; INFRA_MAP; COMPLIANCE_SECURITY hardening counts | Re-measure and print each count with the command that produced it: 77/38/39 Quadlets, 70/65 user timers, 8 root timers, 74 migrations, 40 verified-exec, 128/83/17-POST/7 routes, about 1,388 manifest lines. Prefer generated counts. | README counts (×5), REFERENCE_INFRA counts, hardening counts, verified-exec (×3), PG migrations (×2), route inventory (×2), legacy SQLite constants (×2) | M |
| A6 | COST_STRUCTURE.md §1/§2, SR-1 section and see-also | Explain that api-usage.csv records a model label, not inference: tokens are 0 and "deterministic" mostly means a non-LLM skill. Drop the fallback-rate conclusions. Reconcile the Tier 2 figure, use the EIA-sourced power figure, add a backup storage line, and link the COGS doc and BACKUP.md. | SR-1 usage log, COST_STRUCTURE figures, backup COGS line | M |
| A7 | REFERENCE_INFRA §3.1; HARDWARE_GUIDANCE (uplinks, SDR/RF BOM, CPU governor); SINGLE_EDGE_UNIT_ASSUMPTIONS (production.slice, maintenance window); PI5-BOOT-CONFIG, INFRA_MAP, GUARDRAILS_JUSTIFICATION (watchdog); NETWORK_FAILOVER | Uplink caveat: the backup uplink has been non-functional since 2026-10-06. Add the SDR hardware and expansion BOM and the dongle-loss history. Document the production.slice envelope. Watchdog was tuned to 30 s on 2026-10-12; decide stall-monitor's fate. Record that the CPU governor is enforced by governor-watch and that the tracked tuned profile is not active. Update the NETWORK_FAILOVER counts and links. | uplink failover, SDR BOM, production.slice, hardware watchdog (×2), CPU governor, NETWORK_FAILOVER, maintenance window | M |
| A8 | GUARDRAILS_PROVEN (fix plus link); new docs/INTEGRITY_AND_SUPPLY_CHAIN.md index; COMPLIANCE_SECURITY §3/§5 and public-mirror section; REPRODUCIBLE_BUILDS (:38, :41, dependency audit); LADD_CUI_HANDLING (tripwire); README:765-769; REFERENCE_INFRA §10 and INFRA_MAP §10 (scrub gate); SECRETS_NAMING (host importers of the master file); HONEYPOT_FAIL2BAN | Leak scan covers only the operator's Claude transcripts. Drop "64-hex" from the pre-commit row. Lock currency is detected by the sweep, not at sign time. Name the two podman build paths outside policy. Document the dependency audit, LADD tripwire, signed DB manifests, scrub layers, the site-public pipeline and the website integrity sweep. Rewrite push-public gate order with exit codes. Qualify the public-history rewrite as still open. The scrub gate is caller-invoked, not "every write path". Mark common.guardrails dormant. Record that common.config loads dispatch-secrets.env into host processes. Do not cite Cloudflare-edge auto-ban until the SELinux label question is checked. | GUARDRAILS_PROVEN (×3), integrity index, dependency audit, transcript scan, pre-commit, lock currency, build paths, LADD tripwire (×3), DB manifests, scrub mechanism, site-public, website sweep, gate order, public history, scrub-gate claim, guardrails dormant, config master secrets, honeypot edge-ban, security controls since 10-07, integrity sweep scope, drift gate, git hooks doc, templates | L |
| A9 | AGENT_TRUST_MODEL §3/§4/§11; AGENT_SEGMENTATION (status, codex, connectors, board-sweep, time cards, gov-exec, council states, skills scope); BOARD_SIGNING tiers; COMPLIANCE §3 audit catalog; SUDO_JUSTIFICATION; ISO A.3; HEADLESS_ACCESS roster; README:275, :364 | Rebuild the TTL table from APPROVAL_TTL_POLICY. Add the withdraw route and status, the council state machine, the audit events, and time cards as accountability evidence. Add the board-sweep token-gate relay and flag its TTL-cap conflict. Record live connector slugs, six accounts with codex active, and that skill grants reach Claude-mode agents only. Split the governance route-tier claim and refresh the roster everywhere. | approval TTL (×2), audit events (×3), withdraw, council reconcile, board-sweep, connector slugs, team status, onboarding codex (×2), skill grants codex, time cards (×2), gov-exec, BOARD_AUTH tiers, README governance routes, roster, dormancy sweep, approve.sh, cloud pamphlet | L |
| A10 | COMPLIANCE_SECURITY §2; README "Demo mode"; dispatch-runner-design trust model and route table; new DEMO_MODE section or doc | Correct demo isolation: both containers mount the live Postgres socket and `/api/ask` is ungated on the demo hostname (confirm which DB first). ADS-B, datalink and alerts are fabricated, not sanitized. demo-api fetches AWC live. Document access profiles, the admin token (name only), the recorder endpoints and the mock integrations, labelled simulated. Fix the CF-Connecting-IP host scope. | demo isolation, synthetic signals, demo-api egress, demo profiles, recorder, mock integrations, demo admin token, trust model | M |
| A11 | OOOI_SOURCE_AUTHORITY (rename to a living name); ALERT_REFERENCE §5; README Watchlist | List every oooi_phase writer with gated or ungated status. FIDS can effectively assert IN on the schedule-inferred path, contrary to the doc. Add tfms_airline, the ADS-B ON/IN deny, departure-overdue and add-time TFMS seeding. The local-receiver escape hatch is unreachable. Cite symbols, not line numbers. Link from README. Do this before any "SWIM is validator of record" claim. | OOOI gate coverage, OOOI doc content, OOOI link | M |
| A12 | README CPS section (:427-433) | List the SWIM inputs (STDDS RVR on ITWS active runways with METAR fallback, ITWS hazards) and the 91.175(h) table. Get and record the operator's sign-off status on the RVR table. | STDDS RVR in CPS | S |

### Tier B: capability documentation the investor capability tables will cite

| # | Target | What | Closes | Size |
|---|---|---|---|---|
| B1 | New docs/POLLER_SKILLS.md (linked from README :124 and the poller row) | One row per skill: purpose, trigger, inputs and tables, outputs, LLM persona, lock, status. Cover ops-brief, osint-monitor (scoring thresholds), tfr-enrichment and route-impact (VIP path), the intel watch family, EP advance (fix README:417), convective archiver, feed-health guardrails, flight_impact (partly a stub), train_impact (dormant), nms_v240 (completed one-shot), executive_standard_sync (manual), and the poller scheduler internals. | skills catalog (×2), ops-brief, osint, tfr/route, intel watches, EP advance, convective archiver, feed-health guardrails, flight_impact, train_impact, nms_v240, poller scheduler | L |
| B2 | src/ingest/README.md rewrite, linked from README and DATA_SOURCES §2 | Parser capability index (message type, table, alert or store-only). Detectors are live, with history moved to a dated record. Failover contract: only nws, notam and amtrak have REST twins. Postgres backend and psql verify command. VDL2 reader (port knob). Scoped svc/ingest.env regenerate step. 28800 default. Replace the thermal table with a pointer. Amtrak disabled in core. | SWIM index, failover contract, staged-only, DB backend, VDL2, creds, triage default, thermal, Amtrak | L |
| B3 | DATA_SOURCES §2 and ALERT_REFERENCE §7 | SWIM session-health watcher (metric, thresholds, cooldown, state path, --status). | session health | S |
| B4 | SDR_SERVICES.md restructured as the subsystem index | Dataflow (VDL2 to acars_messages to OOOI). Outbound-sharing table (destinations, protocols, station ids, MLAT location exposure, credential file names). acars-watcher reality: only airframes.io produces matches, local VDL2 is not parsed for registrations, callsigns do not match. Monitoring: three watchdogs disabled. ais-watcher and utm-watcher (inert). Host setup (modprobe blacklist, SELinux module, udev). Fix the acarshub binding, dumpvdl2 device scoping and launch.sh, HFDL routing, acarsdec dongle conflict, rtl-eeprom script, deploy-acars-stack (do not run) and the enable procedure. Add acarshub coordinates to GPS_COORDINATE_CONFIGURATION. | VDL2 path, outbound sharing, acars-watcher, SDR watchdogs, ais, utm, host prereqs, acarshub coords and binding, dumpvdl2, enable procedure, HFDL, acarsdec, rtl-eeprom, deploy script, SDR index, ACARS-addendum | L |
| B5 | README "Deployment profiles" section; REGIONALIZATION §1/§2; composition spec status; STAGED.md; GPS_COORDINATE_CONFIGURATION; ALERT_ARCHITECTURE §5 | What is live (reference profile, METAR consumer, DISPATCH_PROFILE precedence) and what is design-stage (8 vertical profiles, compositions). Composition skeleton built in 3d030992 with no runtime reader. Add DISPATCH_LOCAL_TZ and DISPATCH_DAY_ROLLOVER. | deployment profiles (×7), composition spec, regionalization | M |
| B6 | README "Second brain" section; SECOND_BRAIN_STATUS §1-§4 | Vault read surfaces: `/vault/file` is unscoped within the vault. Full writer inventory with scrub-gated yes/no. Folder skeleton. Two-bucket notes routing and the board mirror extra source. Personal-export digests land in the business vault (an open policy conflict). Doc generation and the docgen image. CLI table. Intel helper libraries (trends_signal dormant). Test list. Decide whether the knowledge-graph artifacts belong in the public mirror. | vault read surfaces, writers, personal-export conflict, notes routing, doc generation, SB docs links, SB index, intel libs, public graph artifacts, tests | M |
| B7 | New docs/EXECUTIVE_STANDARD.md, linked from README :78 | Renderer, manual sync, docroot, generated files (llm.txt vs llms.txt), private feed, members host, invites link (OPERATOR_CONSOLE), GPG keys (only the www copy is public). | ES renderer, ES sync, ES invites link, GPG keys, ES assets hostname | M |
| B8 | SOVEREIGN_SKILL_SUPPLY_CHAIN (link from README and AGENT_SEGMENTATION); new agent-skills index | Per-skill purpose and side effects. The .agents/skills Codex mirror is a stale fork. Tracked-versus-live twin gate (skills-sync). Morning skill provenance and licence. Live settings table. | supply-chain link (×2), skills index, Codex mirror (×2), skills-sync, morning provenance, settings | M |
| B9 | Watchlist docs index plus watchlist_README | Link OOOI, codeshare, TRAIN_PARITY and VESSEL_PARITY. Four entry types (drone). Train provider guard and phase model. Bulk import CLI (unaudited). Trigger table in ALERT_REFERENCE §5. Codeshare doc refresh (seed timer, Jazz offset, decay, resolve_operating_callsign). Shadowed session routes and the vessels-batch route fix. | watchlist index, watchlist_README, train guard, import CLI, triggers, codeshare (×2), session routes, vessels batch | M |
| B10 | dispatch-runner-design.md (link from README) plus DISPATCH_PANEL_PARITY link | PWA api-cache and /console denylist (data at rest). Admin token in localStorage. RSS scope and SSRF. Layer sync is broken. External fallback chains and env names; Kpler dropped. Components table. Accessibility layer. Wake lock. | runner design link, panel parity link, PWA cache, localStorage token, RSS/SSRF (×2), layer sync, external sources, frontend index, accessibility, wake lock | M |
| B11 | ALERT_REFERENCE / ALERT_ARCHITECTURE | unit-failure-notify template (13 units). Approval-gate Python publishers. relay-nudge and dependency-audit rows. Four integrity-sweep alert titles. Amtrak in kickover-guardrail. Mark weekly-external-image-update dormant. | unit-failure-notify (×3), approval-gate publishers, ntfy publishers, sweep alerts, kickover, dormant script | M |
| B12 | INFRA_MAP §4.7 and §3/§6 plus a generated unit-to-doc index | Add the airline-time-fidelity timer (before Monday), nextcloud-cron, unbound anchor units, boot-stagger exceptions, .path Requires= rationale, nginx rate-limit matrix, cloudflared restart policy and drop-in, ollama ingress closed, mcpo residue. | fleet index, time-fidelity (×3), nextcloud-cron, unbound, boot exceptions, .path Requires, rate limiting, cloudflared, mcpo | M |

### Tier C: operations and hygiene

| # | Target | What | Closes | Size |
|---|---|---|---|---|
| C1 | New docs/OPERATIONS.md index | Backups and DR, stack refresh (link REFERENCE_INFRA, REBASELINE runbook), serialized-rollout knobs (replace README:547 with full-load rollout), credential rotation (link SECRETS_NAMING), relays and relay-nudge, load stamps, maintenance window and optime, time cards. | ops index, stack refresh, rollout, rotation, relays (×2), load stamps (×2), maintenance window | M |
| C2 | SECRETS_NAMING | DEMO_ADMIN_TOKEN split (name only), allowlist counts, approval-allowed-signers pin, future rclone.conf, relay-nudge as a reader of the master file. | demo token, secrets registry | S |
| C3 | Misc API docs | whoami-token row, SSE contract, serve.py is dev-only, webhook dual push, console route list and header, console vs CLI parity, test counts (or drop them), deny-link line refs. | whoami, SSE, serve.py, webhooks, console header, parity, test counts (×3), line refs (×2) | S |
| C4 | Skill docs | dispatch-ops timer table and Postgres; flight-hifi-track Postgres SQL and ACARS addendum; hub-arrivals primary path; second-brain-remember index; context-guardian; personal-export trigger; skill-capabilities lines; AGENTS.md codex state; render-onboarding row; docs/tasks/scheduled banner. | skill docs (×10), scheduled-task note | M |
| C5 | Misc | Root-installed copies list, LADD CUI link, addenda link and WPC route caveat, retention table (surface-cleanup, retention_prune windows, convective archive), import-ledger verify, src/common index, BOOT_STORM intro, HEADLESS_ACCESS (mosh/screen, gui-window), local-receiver data source, hub_arrivals FIDS code block, ALERT_REFERENCE line numbers, DEMO_PORTALS reference, CLIENT_DEMO_PATTERN link, integrity sweep status row. | remaining low/none items | M |

### Tier D: code follow-ups surfaced (not doc tasks; wait for unfrozen relays)

1. OOOI ungated writers: FIDS can confirm IN.
2. Board-sweep token-gate TTL exceeds the `sudo` cap.
3. Llama backstop persona or model mapping, plus its dead docs-drift branch.
4. Demo `/api/ask` gating and demo DB isolation.
5. Transcript leak scan does not cover Codex or agent homes.
6. Agent-snapshot freshness list.
7. acars-watcher nested VDL2 registration field and callsign-to-registration lookup.
8. Shadowed watchlist session routes.
9. Layer config sync.
10. render-onboarding `codex` case.
11. Skill grants for Codex.
12. common.guardrails: wire it or delete it.
13. Host scripts loading the master secrets file.
14. Docstring fixes: ingest main/config, cps_recompute, skill schedules, agent_gateway path, web main route map, sse, webhooks, stack-refresh header, ES colophon and Sitemap.
15. Retire the mcpo units, nms_v240, weekly-external-image-update, deploy-acars-stack and the stale templates.
16. Ingest-core quadlet Description.
17. trendspy dependency.

---

## 3. README restructure proposal

Goal: every component is reachable as README → index or doc → component.

1. **Keep the top status and snapshot table**, but generate the counts and print the command for each.
2. **Platform overview, local vs cloud.** One honest paragraph saying which inference is local and which jobs use cloud vendors, linking DESIGN-PRINCIPLES and COMPLIANCE.
3. **Documentation index.** Either a new `docs/README.md` linked prominently, or an inline block grouped as below. Dated records live in a separate "History" list.
   - **Architecture and infra:** INFRA_MAP, REFERENCE_INFRA, SINGLE_EDGE_UNIT_ASSUMPTIONS, HARDWARE_GUIDANCE, NETWORK_FAILOVER, PI5-BOOT-CONFIG, BOOT_STORM_TIMER_REQUIRES, NETWORK_STORAGE_CONSTRAINTS, POSTGRES_MIGRATION, new COMMON_LIBRARY.
   - **Data and feeds:** DATA_SOURCES, src/ingest/README (rewritten), SDR_SERVICES, CIFP_DATA, DCA_IAD_FIDS, GPS_COORDINATE_CONFIGURATION, addenda/README, research/AIRLINE_TIME_FIDELITY_SPEC.
   - **Capabilities:** new POLLER_SKILLS, CPS section, watchlist and OOOI index (OOOI_SOURCE_AUTHORITY, CODESHARE, TRAIN_PARITY, VESSEL_PARITY, watchlist_README), ALERT_REFERENCE, ALERT_ARCHITECTURE.
   - **Runner, demo and console:** dispatch-runner-design, DISPATCH_PANEL_PARITY, DEMO_MODE (new), CLIENT_DEMO_PATTERN, DEMO_PORTALS, OPERATOR_CONSOLE.
   - **Security and compliance:** COMPLIANCE_SECURITY, new INTEGRITY_AND_SUPPLY_CHAIN index, GUARDRAILS_PROVEN, GUARDRAILS_JUSTIFICATION, REPRODUCIBLE_BUILDS, SECRETS_NAMING_AND_ISOLATION, SUDO_JUSTIFICATION_PROPOSAL, LADD_CUI_HANDLING, HONEYPOT_FAIL2BAN, HEADLESS_ACCESS, ISO_42001_ALIGNMENT, SECURITY.md.
   - **Agents and governance:** AGENT_SEGMENTATION, AGENT_TRUST_MODEL, BOARD_SIGNING, SOVEREIGN_SKILL_SUPPLY_CHAIN, agent-skills index, TEAM_SEGMENTATION_SHOWCASE.
   - **Second brain and publishing:** SECOND_BRAIN_STATUS, SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT, CAUSAL_REASONING_ROADMAP, EXECUTIVE_STANDARD (new), GPG_KEYS_PUBLISHED.
   - **Local LLM:** DEDICATED_MODELS_PLAN (consider renaming to LOCAL_MODELS), MODEL_EVALUATION.
   - **Operations:** new OPERATIONS index, BACKUP, stack refresh and rollout, rotation, relays.
   - **Regionalization and verticals:** REGIONALIZATION, deployment-profiles/README, STAGED, the compositions spec.
   - **Business (internal, HOLD):** COST_STRUCTURE, the current COGS doc, executive_summary, investor-materials/v1.5, the current CODEBASE_REFERENCE.
4. **Subsystems table.** One row per subsystem with a one-line purpose, its primary doc, and its main units or timers: ingest, poller skills, watchlist and OOOI, SDR, runner, demo, console and gateway, second brain, Executive Standard, backups, integrity, deployment profiles.
5. **Scheduled jobs.** Link INFRA_MAP §4.7 and the generated unit-to-doc index. A script check should stop new units landing unindexed.
6. **Move struck history to the end** or into a separate HISTORY section so live and superseded text are not interleaved. README's current struck text contains the only links to several docs.

---

## 4. Investor/COGS impact list (claims to revisit; no investor text drafted)

**Cost lines**

1. **"Recurring inference cost $0; only recurring line is power"** (COST_STRUCTURE:152-154, COGS:48-51/72, investor executive summaries). A cloud Claude Opus job is scheduled weekly, and vendor agent plans exist. Scope the claim to SR-1 skill inference and add a vendor-subscription line.
2. **SR-1 api-usage.csv used as inference evidence** (COST_STRUCTURE:120-156). It is a model label per run, not real inference.
3. **Backup and DR.** Investor docs say "no backup/DR". Daily encrypted Borg backups and a weekly restore test now exist, but **off-box is not configured**. Do not claim offsite or DR. Add backup storage (local disk, planned Proton) as a COGS line, and resize after a week of increments. One investor-batch finding suggested claiming "daily encrypted offsite backup"; that conflicts with the verified off-box gap, so the off-box finding wins.
4. **FlightAware AeroAPI "dormant"** (DATA_SOURCES:310, README:393). The key name reaches 7 services, so it may be a live metered cost. Verify before stating.
5. **airplanes.live fallback.** It is missing from the COGS feed inventory (:71), and executive_summary.md:19-21 says resolution is local.
6. **COGS internal totals.** Lines :143, :184 and :219 disagree with the :39 headline, and the :158 "floor" wording contradicts :105.
7. **COGS live inputs.** It cites 9 hostnames (now 14), 65 migrations (now 74/76 entries), and an integrity hazard that has cleared.
8. **COST_STRUCTURE figures.** The Tier 2 total and the power estimate disagree with the COGS doc.
9. **Rent-equivalent lines missing:** agent OAuth identity provider, SBOM and supply-chain tooling, approval/PAM workflow, managed backup.
10. **Hardware BOM.** The SDR/RF BOM and expansion hardware are undocumented. The production.slice memory envelope matters for sizing.

**Capability claims**

11. **"21 dedicated Ollama models"** in several investor files. It is one Qwen3-4B model on llama.cpp with about 23 personas.
12. **"ACARS fusion pending an off-box fix."** The root cause was on-box and was fixed 2026-10-03. Re-verify row flow first.
13. **"2.45 GB and growing demo corpus."** It is 1.88 GB and frozen since 2026-08-14 with the refresh off.
14. **Test and verification posture.** Investor docs say no CPS test, 23 files/218 tests, and 33 gated containers. Now a CPS RVR test exists, there are 115 test files and 40 gated containers. Hash-locked builds, SBOM, dependency audit and the pre-push leak guard are new. There is still no hosted CI.
15. **"Single SQLite database"** (aviation-ops FAQ :47/:87). It is Postgres.
16. **"Issued tokens do not expire."** Restate exactly which token classes still never expire, and list the TTL'd approvals, OAuth gateway and rotation tooling.
17. **Second-brain figures and "recompiled daily."** It recompiles 6-hourly plus on write, and the counts are dated 08-24.
18. **Redundant uplinks** (REFERENCE_INFRA:51-52, HARDWARE_GUIDANCE:153). The backup uplink has been down since 2026-10-06.
19. **Demo isolation** (COMPLIANCE §2). Both demo containers reach the live Postgres socket. Demo signals are fabricated, not sanitized.
20. **"SWIM is the validator of record" for OOOI.** FIDS can effectively assert IN on one path.
21. **CPS uses STDDS RVR** under a CFR table whose operator sign-off is unrecorded.
22. **Cloudflare-edge auto-ban.** It may be failing silently because of SELinux labelling. Verify first.
23. **ISO 42001 A.7/A.10 "Aligned".** Not accurate given the cloud jobs and vendor-CLI agents.
24. **"Every vault write is CUI/PII gated."** The gate is caller-invoked, and archives, export-analysis and snapshots skip it.
25. **"CUI shredded."** Shred is ineffective on btrfs.
26. **Transcript leak guard "every transcript on the box".** It covers only the operator's Claude transcripts.
27. **Public-history rewrite "done".** Three LADD identifiers are still in public history.
28. **ACARS "triple-source watcher".** Only airframes.io produces matches.
29. **Partly-stub or dormant features presented as capabilities:**
    - flight_impact: a partial stub.
    - train_impact: no-op.
    - Drone/UTM monitoring: inert.
    - SDR outage alerting: three watchdogs are disabled.
    - common.guardrails: dormant.
30. **Outbound receiver-data sharing** (4 ADS-B aggregators, 2 MLAT networks, ACARS/VDL2 destinations) is undocumented. It is relevant to the data-exchange value story and to location exposure.

**New capabilities to add, graded honestly**

31. Deployment profiles: reference profile live, 8 vertical profiles staged.
32. Airline time-fidelity research corpus: add after the Monday checkpoint.
33. Security and governance controls shipped since v1.5.
34. Agent time cards.
35. Operator console.
36. Accessibility layer.
37. executive_summary.md v1.3.0. Its "What's new" omits backups, approval TTL and withdraw, profiles, time-fidelity and the airplanes.live fallback.

---

## 5. Full verified findings table

Duplicates across batches are merged; "(×N)" is the number of batches that raised the item. Relevance is H, M, L or N (high, medium, low, none). Code and doc paths are given relative to the repo root.

### 5.1 SWIM ingest and parsers

| Kind | Component | Code paths | Doc paths | Evidence | Proposed fix | Rel |
|---|---|---|---|---|---|---|
| undocumented | STDDS RVR substitution in CPS | src/poller/skills/cps_recompute.py:7, :64-145; smes_parser.py | README.md:427-433; src/ingest/README.md:171-181 | README's CPS inputs list only METAR, airspace and GDP. The code prefers STDDS touchdown RVR on ITWS runways via a hand-transcribed 91.175(h) table with no runtime gate. The code comment asks for operator sign-off, and no doc records whether it happened. | Document the SWIM inputs. Record the sign-off or gate the feature. | H |
| undocumented | SWIM session-health watcher | scripts/swim-session-health-watch.sh; its .service/.timer | INFRA_MAP.md:212; ALERT_REFERENCE.md:235 | A live 5-minute timer sums keep-alive failures with threshold 15 per sample, sustained 600 s, 1800 s cooldown, observe only. Docs only name it. | Add a subsection to DATA_SOURCES §2 and a row to ALERT_REFERENCE §7. | M |
| undocumented (×2) | AWC airsigmet source and convective_sigmet_archive | convective_sigmet_archiver.py; common/airsigmet.py; pg_schema/0045 | DATA_SOURCES.md; POSTGRES_MIGRATION.md §6; src/ingest/README.md:425-458 | A live 10-minute archiver writes an append-only table that is never pruned. The ingest README calls it "staged only". DATA_SOURCES has no entry and the retention section omits it. | Add a DATA_SOURCES §3 entry and record "append-only by design" in POSTGRES_MIGRATION §6. | M |
| missing-index | SWIM parser capability index | ingest/parsers/*.py; db_swim.py | src/ingest/README.md; DATA_SOURCES.md:95-110; README.md:126 | DATA_SOURCES:104 names the parser but does not link the ingest README. | Rewrite the ingest README as a parser→table→alert index and link it from README and DATA_SOURCES. | H |
| wrong | Push/REST failover contract | src/ingest/failover.py:33, :51-55 | src/ingest/README.md:11-13, :117-124 | The README says REST fallback covers the SWIM feeds. REST_FALLBACKS has only nws, notam and amtrak; the five other SWIM feeds have no twin by operator decision. amtrak is missing from PUSH_FEEDS. | Rewrite the section or point to DATA_SOURCES §1. | H |
| stale | Amtrak ingest path | ingest-core.container:45 (AMTRAK_ENABLED=false); config.py:73; failover.py:54 | src/ingest/README.md:9-13, :25, :94 | Amtrak is disabled in core and amtrak-tracker is primary. The quadlet Description still lists Amtrak. | Update the module table; fix Description later. | M |
| stale | local_airspace ACARS/VDL2 reader | local_airspace.py:54-64, :444-560, :580-622 | src/ingest/README.md:95, :544-558; DATA_SOURCES.md:324-334 | The doc says "zero rows ever" and blames an upstream fault. c2b381ef added a VDL2 reader, dumpvdl2 normalizer and OOOI matching. VDLM2_ROUTER_PORT is undocumented. | Describe both readers and the OOOI evidence path. | H |
| stale | Ingest DB backend | ingest/main.py:63-71 | src/ingest/README.md:9-10, :165-167, :196, :594-597 | The README says SQLite and gives a sqlite3 verify command. Ingest uses Postgres via pg_schema, and the .db file holds nothing live. | Say Postgres and give a psql verify command. | M |
| stale | Ingest credentials | ingest quadlets; service-env/ingest.allowlist; config.py:1-8 | src/ingest/README.md:113-115 | The containers read svc/ingest.env (since 2026-10-05), not dispatch-secrets.env. | Add the generate.py regenerate step. | M |
| stale | SWIM_BACKLOG_STALE_SECONDS | swim_client.py:77 | src/ingest/README.md:125-127 | Doc says 7200; code default is 28800 since d3af6680. | Fix the number or link DATA_SOURCES. | L |
| wrong | Thermal guard summary | scripts/thermal-ingest-guard.py:236, :456-459, :631, :683 | src/ingest/README.md:37-73 | The README says ollama is shed, the fallback trigger is active and resume is at 15. In code: no LLM unit is touched, the fallback trigger is informational, resume is at 20. | Replace with a pointer to DATA_SOURCES §9. | M |
| stale | "Staged only, NOT deployed" detectors | fdps/tfms/smes parsers; archiver; pg_schema 0040-0045 | src/ingest/README.md:169-458 | Five audit passes are labelled not deployed. The code is at HEAD, migrations are applied, containers were recreated today, and ALERT_REFERENCE lists the alerts as live. | Write a current "Parser capabilities" section; move history to a dated record. | H |
| stale | ingest main and config docstrings | ingest/main.py:4-8, :22-29; config.py:4-5 | (in-code) | They name a unit removed 2026-07-26, a wrong quadlet path and the master secrets file. | Code relay. | N |

### 5.2 Poller core, watchlist, OOOI, codeshare

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| unlinked | OOOI authority doc | db.py:6050-6215, :6349-6420; pg_schema/0065 | docs/OOOI_SOURCE_AUTHORITY_2026-09-28.md; README | No .md links it. README documents only the priority tie-break. | Link it and add a summary paragraph. | H |
| wrong | OOOI gate coverage | poller/main.py:1124-1215, :1277-1317, :1446-1466; watchlist.py:884; db.py:4417 | OOOI doc:71; ALERT_REFERENCE §5 | The schedule-inferred IN path accepts FIDS alone and writes through the ungated writer, so FIDS can effectively assert IN. The ACARS rescue is also ungated. | List every writer with gated or ungated status; flag the code gap. | H |
| stale | OOOI doc content | db.py:6050, :6067-6100, :6166-6186, :6422; poller/main.py:1489; watchlist.py:1328 | OOOI doc | Omits tfms_airline, the ADS-B ON/IN deny, departure-overdue and TFMS seeding. record_oooi_local_track has no caller. Line citations are off by about 300. | Make it a living doc and cite symbols. | M |
| unlinked | Codeshare/FBO doc and seed timer | seed-dc-codeshare.py; codeshare-seed .service/.timer | docs/CODESHARE_AND_FBO_2026-09-28.md | Nothing links it. The timer appears only in an OnFailure list. | Link from Watchlist and add to the timer inventory. | M |
| stale | codeshare_map resolution | db.py:5072-5230; seed-dc-codeshare.py:45-139; poller/main.py:1590-1620 | CODESHARE doc | Decay is wired via transport_pattern_digest. The daily timer exists. Jazz offset rule, FIDS codeshares and resolve_operating_callsign are not described. Citations are about 280 lines off. | Update the doc. | M |
| undocumented | Train provider guard and phase machine | poller/main.py:1896-1975, :732-760; db.py:6292-6345 | README; watchlist_README; TRAIN_PARITY | Non-Amtrak entries are silently skipped. The train_phase and ETA model is undocumented. | Document both and link TRAIN_PARITY. | M |
| undocumented | Poller scheduler internals | poller/main.py:162-208, :280-485, :2253-2283; maintenance_window.py | README | Startup hold-off, push grace, trigger files and maintenance_only appear only in a dated reference. | Add a scheduler subsection or catalog. | M |
| undocumented | watchlist_import CLI | poller/tools/watchlist_import.py | watchlist_README; ALERT_REFERENCE:17, :56 | Unexplained. Writes straight to the DB, bypassing the audit row and add-time seeding. | Add a "Bulk import" paragraph. | L |
| undocumented (×2) | shared.ssrf_guard | ssrf_guard.py:1-64; runner/main.py:2628; osint_monitor.py:132 | COMPLIANCE; GUARDRAILS docs | No doc mentions it. It fails closed on a 5 s DNS timeout. | Add to COMPLIANCE and the runner design doc. | M |
| undocumented | feed_resolve and catalog de-duplication | feed_resolve.py; rss_catalog.py:225-347 | README:334-344 | YouTube/Rumble/blog resolution, the Rumble 403 limitation, duplicate-category refusal and visibility are undocumented. | Add 2-3 sentences. | L |
| undocumented | Watchlist triggers | poller/main.py various; watchlist.py:770-918 | ALERT_REFERENCE §5 | 11+ triggers are uncatalogued, including the auto-sweep rules. | Add a trigger table. | L |
| missing-index | Watchlist/OOOI doc set | watchlist.py; routes/watchlist.py; poller/main.py; db.py | 8 docs | README links only watchlist_README. | Add a watchlist docs index. | M |
| stale (×4) | airplanes.live fallback | common/airplanes_live.py; watchlist.py:351-410; poller/main.py:901-915 | README:389-394; DATA_SOURCES:318-322; watchlist_README:210-215 | Docs say "not queried". The fallback has been live since 2026-10-08, has a LADD guard, and is enabled in dispatch.env. | Rewrite as "fallback only". | H |
| wrong | Shadowed watchlist session routes | web/main.py:103, :1639-1676; routes/watchlist.py:82, :534 | README:280-284 | The router is registered first, so session GET and DELETE are unreachable and DELETE hits the admin entry-remove route. | Note it in README; code fix. | L |
| wrong | Vessels batch route | routes/watchlist.py:136-798 | README:300 | There is no /vessels/batch route. | Correct the route list. | N |
| stale | watchlist_README | watchlist.py:39-46, :1151-1158; poller/main.py:501 | src/shared/watchlist_README.md | Missing: drone entry type, drone file, OpenSky sweep, new add-time fields. Points at CLAUDE.md sections that no longer exist. | Refresh it. | L |

### 5.3 Poller skills (A–M and N–Z)

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented (×9 across batches) | claude-weekly-llama-fallback | claude_weekly_llama_fallback.py; common/claude_weekly_fallback.py; the .container/.timer; second-brain-weekly-dump.sh:154-170; personas.py | README Local LLM; SECOND_BRAIN_STATUS:52; INFRA_MAP:43; DEDICATED_MODELS:26 | Live since b2a97645, every 6 h UTC, flock -w 1800, exit 75. No doc explains it. INFRA_MAP says "not deployed". The default model has no persona mapping and no env override, so it likely cannot generate. | Document it (Tier A4); code fix for the persona. | H |
| stale (×3) | Llama fallback scope and docs-drift | claude_weekly_llama_fallback.py:1-27, :103-141, :200; weekly-doc-drift-check.sh:8-16; docs-drift unit | INFRA_MAP:43 | The docstring and units say it covers two jobs. docs-drift no longer calls Claude, so that branch is dead. | State "second-brain-weekly-dump only"; code follow-up. | M |
| stale | INFRA_MAP timer table | llama-fallback timer | INFRA_MAP:206-214 | The timer is missing. | Add a row. | L |
| stale | DEDICATED_MODELS caller list | claude_weekly_llama_fallback.py | DEDICATED_MODELS:26 | 22 callers listed; 23 exist (unverified 2026-10-25). It uses inline prompts, not a persona. | Update. | M |
| undocumented | board-sweep governance behaviours | board_sweep.py:1-372; governance.py:200-239; board-presence-attest.sh | AGENT_SEGMENTATION:324, :340; BOARD_SIGNING | The token-gate relay into approvals, the board write-back, the weekly GPG attestation reminder and dormancy are undocumented. A 12 h lead gives a TTL of about 42,600 s against the 600 s `sudo` cap, so creation would be rejected. | Document it; flag the TTL conflict for code. | H |
| missing-index (×2) | Poller skills catalog | poller/main.py:96-148; skills/; maintenance-queue.txt | README:124; INFRA_MAP; CODEBASE_REFERENCE (dated) | The only full inventory is dated and unlinked. | Create a living docs/POLLER_SKILLS.md. | H |
| undocumented | Intel watch and digest family | aam/aviation/concierge/EP/gig daily watches; aam_weekly; disruption digest; dispatch_desk_memo | ALERT_REFERENCE:56, :210, :211 | Only topics are documented. The aam docstring points to a nonexistent doc. | Add to the catalog. | H |
| undocumented | EP advance brief and venues | ep_advance_brief.py; ep_advance_venues.py; units | README:417; ALERT_REFERENCE:60, :203 | README says "hourly briefs", but venues runs daily from the queue. The timer Description time is wrong. | Add an entry; fix README:417. | M |
| undocumented | Feed-health guardrails | feed_db_integrity_check.py; ingest_feed_watch.py | ALERT_REFERENCE; ALERT_ARCHITECTURE | Undocumented. A docstring still says "SQLite level". | Add a "Feed health guardrails" section. | M |
| unlinked (×3) | CIFP pipeline and CIFP_DATA.md | faa_cifp_pull/parse; cifp_lookup.py; timers | CIFP_DATA.md | Accurate but linked by nothing living. | Link from README and DATA_SOURCES. | M |
| undocumented | executive_standard_sync | executive_standard_sync.py; render.py | GPG_KEYS_PUBLISHED:7; README:1374 (struck) | Manual publisher. The only description is struck. | Add an ES publishing section. | M |
| undocumented | flight_impact | flight_impact.py:1-17, :98, :113 | README; DEDICATED_MODELS | Writes a route narrative when rows exist. The TODO and "hot_alerts" claims are stale. | Catalog it as a partial stub. | M |
| stale | README platform counts | .config/containers/systemd; .config/systemd/user | README:19, :21, :34, :112, :580 | Docs say 76/38/39/64; actual is 77/39/40/70. | Re-measure (A5). | H |
| wrong | cps_recompute docstring | cps_recompute.py:4; poller/main.py:101 | (README is correct) | Names a nonexistent timer. | Code follow-up. | L |
| undocumented | ops-brief | ops_brief.py; units | README:417; INFRA_MAP:207; ALERT_REFERENCE:58, :199 | Contents, outputs, webinar deferral and fallback are undocumented. | Add to the catalog. | H |
| undocumented | osint-monitor | osint_monitor.py:1-75, :667; poller/main.py:108 | README:279-298; ALERT_REFERENCE:62, :206 | Scope model, thresholds 9/7/4, source bonus, retention and consumers are undocumented. | Add an OSINT section. | H |
| undocumented | tfr-enrichment and route-impact | tfr_enrichment.py; route_impact.py; poller/main.py:97-100 | ALERT_REFERENCE:43-44, :204-205 | The VIP corridor capability is explained only in struck text. | Add to the catalog. | H |
| undocumented | train-impact (no-op) | train_impact.py; poller/main.py:102-104 | README:124; DEDICATED_MODELS | Dormancy is not stated. | Mark dormant. | M |
| unlinked | tbfm-arrival-enrichment | tbfm_arrival_enrichment.py; units | CIFP_DATA:62-70 | Explained only in the unlinked CIFP_DATA. | Link CIFP_DATA. | M |
| undocumented | nms_v240 pair and timer | nms_v240_*.py; units | INFRA_MAP:214; ALERT_REFERENCE:208 | Completed one-shot; the timer is still enabled. | Mark completed or retire. | L |
| wrong | SR-1 usage log meaning | tfr_enrichment.py:20, :204-239; route_impact.py; ops_brief.py:1205; etc. | COST_STRUCTURE:120-156; README:603-606 | Records model labels, not inference. "Deterministic" mostly means a non-LLM skill. | Correct COST_STRUCTURE (A6). | H |
| stale | Retention coverage | surface_events_cleanup.py; retention_prune.py; db.py:1349-1429 | POSTGRES_MIGRATION:170-186 | surface-cleanup and the retention_prune windows are missing. | Write a full retention table. | M |
| wrong | personal-export-watch output | personal_export_watch.py; export_analysis.py:98; web/main.py:486-490 | SECOND_BRAIN_STATUS:52; personal-export-analysis SKILL | Digests land in the business vault, which agents can read. This contradicts the skill's policy. | Document it; open an operator decision. | M |
| stale | Notes import and board mirror | second_brain_personal_notes_import.py; research_board_mirror.py | SECOND_BRAIN_STATUS:15, :49-50, :61 | Two-bucket routing and the entity-tracking extra source are undocumented. | Update §1 and §3. | L |
| stale | Skill docstrings and scheduler comment | ops_brief, weekly_summary, second_brain_*, semantic_compile, etc.; poller/main.py:151-154 | ALERT_REFERENCE:200 | Schedules, models and topics are wrong. | Code relay. | L |

### 5.4 Web API, console, gateway, ES invites

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented | GET /api/v1/whoami-token | web/main.py:181-230; runner/main.py:2471 | README Tier 0; auth-token-proxy-pattern | Undocumented; rate-limited 60/60 s. | Add a row. | M |
| undocumented | SSE /api/v1/events | web/sse.py; web/main.py:948-952 | README:263 | Publicly proxied with no in-repo client. | Add an "Event stream" subsection. | L |
| undocumented | web/serve.py | serve.py; Containerfile.web:73-78 | — | Alternate entrypoint; board_sign.py:81 wrongly cites it. | Delete it or mark dev-only. | L |
| unlinked | ES reader invites | es_invites.py; es-invite.sh | README:78; OPERATOR_CONSOLE:78-131 | README does not link the explanation. | Append a link. | M |
| undocumented | Gateway dormancy driver | board_sweep.py:362-372; agent_gateway.py:457-471 | AGENT_SEGMENTATION:340; OPERATOR_CONSOLE:147 | The sweep driver is unnamed. | Add a sentence. | L |
| stale (×2) | Approval TTL table | governance.py:195-240; agent_gateway.py:162-168, :473-487 | AGENT_TRUST_MODEL:148-158 | Doc says 24 h and 3600 s. Policy is 1800 s (300-3600), council 10% of deadline, close 25%. COUNCIL_APPROVAL_TTL_S is dead. Contradicts GUARDRAILS_JUSTIFICATION. | Rewrite from APPROVAL_TTL_POLICY. | H |
| stale (×3) | Governance audit catalog | governance.py:625-651; console.py:670; routes/agent_gateway.py:318; web/main.py:409-419; es_invites.py:105 | COMPLIANCE:153; AGENT_TRUST_MODEL §11; SUDO_JUSTIFICATION | Missing events: board.signed.request, approval.withdrawn, council.status, console.login.retap, agent.tool.call. retire-legacy is not in _AUDITED. | Add rows. | H |
| stale | Connector slugs | agent_gateway.py:100-130 | AGENT_SEGMENTATION:336, :380 | Says "not set up"; README and :190 show four active. | Replace with the live list. | H |
| stale | Route inventory counts | web/main.py; console.py | README:235-238 | Should be 128 / 83 / 17 POST / console 7, plus the withdraw route. | Update. | M |
| wrong | Console vs CLI parity | console.py:722-811; es-invite.sh; agent-gateway.sh | OPERATOR_CONSOLE:57-62 | The console lacks retire-legacy and add-connector, and the batch device count is fixed at 3. | List the gaps. | L |
| wrong | web/main.py docstring route map | web/main.py:1-40, :2214-2240 | (in-code) | Calls /adsb an airplanes.live proxy; it is the local receiver. | Code relay. | M |
| stale (×2) | Test counts | tests/web/*; tests/scripts/*; tests/second_brain/* | OPERATOR_CONSOLE:64, :149; AGENT_SEGMENTATION:274-320; BOARD_SIGNING:142; SECOND_BRAIN_STATUS:32 | Counts are out of date. | Update or drop counts. | L |
| stale | OPERATOR_CONSOLE header and routes | console.py:556-722 | OPERATOR_CONSOLE:3 | Verified at db64018 but describes 10-12 behaviour. The 7 routes are not listed. | Re-stamp and list. | L |
| stale | Webhook fan-out and token source | routes/webhooks.py:131-290 | INTEGRATION_SPEC:166-181 | It is a dual push, and the docstring names the wrong env file. | Fix both. | L |
| wrong | agent_gateway docstring path | agent_gateway.py:3 | (in-code) | Names a nonexistent file. | Code relay. | N |
| stale (×2) | Deny-link line reference | web/main.py:2821 | README:364 | Cites :2804. | Use the function name. | N |

### 5.5 Governance, approvals, segmentation

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented | Requester withdraw and "withdrawn" status | governance.py:610-628; web/main.py:283, :2931; approve.sh:13, :154 | AGENT_TRUST_MODEL; AGENT_SEGMENTATION:322; BOARD_SIGNING:61-67 | None of those docs mentions "withdraw". | Add rows to §3 and §4 and update the usage line. | M |
| undocumented | board.signed.request and other new audit events | web/main.py:409-419; governance.py:625, :648; console.py | AGENT_TRUST_MODEL §11 | Missing from the evidence table. | Add rows. | H |
| undocumented | Council lifecycle reconcile | governance.py:631-652, :387-410; council.sh:10 | AGENT_SEGMENTATION:324 | The expired/withdrawn/denied states are undocumented. | Add the state machine. | L |
| unlinked (×2) | Agent time cards and snapshots | agent_timecard.py; timers; agent-home-snapshot.sh | BACKUP:85-92; AGENT_SEGMENTATION; AGENT_TRUST_MODEL | Explained only in BACKUP.md, which is unlinked. The docstring's "council seats" are not counted. | Link and add a "Time cards" subsection. | H |
| undocumented | gov-exec.sh execution path | scripts/lib/gov-exec.sh; approver-ctl and others | AGENT_SEGMENTATION; AGENT_TRUST_MODEL | Governance CLIs run inside the signed web container; undocumented. | Add a paragraph. | L |
| stale | BOARD_AUTH_POLICY tiers | web/main.py:271-288 | BOARD_SIGNING:61-67; AGENT_SEGMENTATION:280-286; SHOWCASE:110 | The withdraw route is missing and the verification stamp is stale. | Add and re-stamp. | L |
| wrong (×2) | Codex onboarding pamphlet | render-onboarding.sh:30-34; onboarding-template.md:47, :72, :92, :117-128; team-liveness.sh:93 | AGENT_SEGMENTATION:137, :169-170 | Codex is told it never logs in; Claude liveness, skills and hooks text is hard-coded. Line 47 says "approve" though a tap can only deny. | Add a codex case; fix the wording. | M |
| stale | Team segmentation status | /etc/ctdc-accounts.conf; team-liveness.sh | AGENT_SEGMENTATION:3-32, :38, :122, :142; SHOWCASE:91 | Should be six accounts with codex live; the lapsed-grace finding is stale. | Re-verify and rewrite. | M |
| wrong | README governance route claim | web/main.py:280-286, :2886 | README:275 | GET routes are normal tier, and resolve verifies the approval key. | Split the row. | M |
| stale | approve.sh explanation block | approve.sh:55-100 | AGENT_TRUST_MODEL:172 | Undocumented UI friction. | Add it. | L |

### 5.6 Runner PWA and demo

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented | Demo access profiles | demo_api.py:64-825; profiles.py:50-186; demo-secrets.env.example | README:437-460 | Windows, speed, admin routes, admin token, env knobs, headers and PBKDF2 are undocumented. The example file lacks DEMO_ADMIN_TOKEN. | Write a DEMO_MODE doc. | H |
| undocumented | Demo recorder endpoints | recorder.py:45-90 | README:439-444 | 13 endpoints and a Tier-1 token; the comment names the wrong env file. | Document them. | M |
| undocumented | Demo mock integrations | runner/main.py:2190-2236 | dispatch-runner-design:76 | Synthetic payloads are not labelled as simulated. | Label as simulated. | H |
| undocumented | PWA api-cache | vite.config.js:26-48 | dispatch-runner-design:41 | NetworkFirst cache of API responses on devices; /console denylist. | Document it and the data-at-rest implication. | M |
| undocumented | Admin token in localStorage | SettingsPanel.jsx:19, :95-130; useLayerConfig.js | design doc; auth-token-proxy-pattern | Contradicts "never exposed to the browser". | Document or remove. | M |
| undocumented | RSS visibility model and SSRF | runner/main.py:2448-2868 | design doc:106-121 | Scopes, identity resolution and the SSRF guard are undocumented. | Add a subsection. | M |
| unlinked | dispatch-runner-design.md | runner | README | Not linked from README. | Link it. | L |
| unlinked | CLIENT_DEMO_PATTERN.md | client-demo@ units; scripts | README:20, :153 | Not linked. | Link it. | L |
| undocumented / wrong (×2) | Demo portals | demo-portal-*.container; demo-portal.sh | CLIENT_DEMO_PATTERN:17-22 | Headers reference the missing DEMO_PORTALS.md. | Write it, repoint, or retire. | L |
| unlinked | DISPATCH_PANEL_PARITY.md | runner/main.py:1290-1800; DispatchDrawer.jsx | — | Reachable only via the unlinked design doc. Records a still-present context-shape bug. | Link it. | M |
| missing-index | Frontend components | src/runner/frontend/src | design doc:123-140 | 27 components and 8 hooks are unindexed. | Add a table. | L |
| undocumented | Accessibility layer | AccessibleTable; AriaCompassRegion; useCompassSummary | design doc | Undocumented. | Document it. | M |
| undocumented | Wake lock and visibility polling | useWakeLock; WakeLockIndicator; useVisibilityAwareInterval | design doc | Only in a dated record. | Add a note. | L |
| wrong | Demo isolation | runner-demo and demo-api quadlets; runner/main.py:1636-1760, :2007-2034; profiles.py; demo_api.py:9-18 | COMPLIANCE:64-70; README:89-94, :445-447; design doc:19-27 | Both mount the live PG socket. /api/ask is ungated and shares chat_messages with the live runner. | Confirm the DB, correct docs, code fix. | H |
| wrong | Demo signals synthetic, not sanitized | runner/main.py:486-1050, :2239-2290 | README:451-454; design doc:55, :79-86 | Datalink and alerts are fabricated. _sanitize_signal_payload is unused. | Rewrite. | H |
| wrong | demo-api live AWC egress | demo_api.py:565-735 | README:445-447; COMPLIANCE:64 | Fetches airsigmet live; feeds and wx-config are stubs. | State it. | M |
| stale | Runner trust model | runner/main.py:164-357 | design doc:19-22; README:322-325 | CF-Connecting-IP is honoured only for Cloudflare-fronted hosts; peer-or-XFF fallback otherwise. | Update. | M |
| wrong | Layer config sync | useLayerConfig.js:70-100; runner/main.py:1895, :1981-2123 | design doc:93; README:93, :334 | PUT returns 405 and GET returns 404, so sync is broken. | Mark broken or fix. | L |
| stale | Runner external sources and env | runner/main.py:53-102, :917-1290 | design doc:82-84, :131-147 | Third-party ACARS fallbacks, Kpler dropped, UTM env names; env table incomplete. | Update. | M |

### 5.7 Second brain and Executive Standard

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented | Vault read surfaces | web/main.py:1783-1890, :486-540; GraphView.jsx | SECOND_BRAIN_STATUS:59-65; README:281 | /vault/file reads the whole vault for any Tier-1 token; knowledge-graph routes are omitted. | Add a read-surfaces table. | H |
| undocumented | ES renderer and sync | executive_standard/render.py; assets; sync | README:78, :710; OPERATOR_CONSOLE | No how-it-builds doc. | Write EXECUTIVE_STANDARD.md. | M |
| unlinked | Second-brain doc set and CLI | second_brain/; semantic/__main__.py; second-brain-search.sh | README; INFRA_MAP:360 | No README section; CLI modes appear only in struck text. | Add a section and CLI table. | M |
| undocumented | Document generation | doc_generation.py; Containerfile.docgen | SECOND_BRAIN_STATUS:26; REPRODUCIBLE_BUILDS:93 | Branded renderers and the docgen image path are undocumented. | Add a subsection. | H |
| missing-index | SB and ES overview | second_brain/; executive_standard/ | README; docs/ | No index. | Add a subsystems table. | M |
| wrong | Scrub gate "every write path" | scrub_gate.py; export_analysis.py; archivers; skill-snapshot.sh | REFERENCE_INFRA:460-462; INFRA_MAP:362-363 | The gate is caller-invoked, and several writers skip it. | Correct the wording. | H |
| wrong | Public mirror ships vault note paths | knowledge_graph/graph.json, vault-graph.html; scrub-public-tree.py | scrub-public-tree.py:158; SECOND_BRAIN_STATUS.example | The public tree carries real vault folder paths (hundreds of occurrences). | Decide and add to DROP_FILES or the rewrite backlog. | H |
| stale | SB writer inventory | agent_timecard.py; llama fallback; archivers; skill-snapshot; quiet-window-report; export_analysis; board_sweep | SECOND_BRAIN_STATUS:5, :15, :34-55 | Writers and folders are missing; the stamp is stale. | Add rows. | M |
| stale | ES assets hostname | robots.txt:9; llm.txt:8; render.py:360-372; sw.js; sync docstring | (struck README; retired vhost) | Assets still name the hostname now owned by Substack. | Code fix; explain llm.txt vs llms.txt. | L |
| wrong | Published GPG keys via ES | assets/keys; sync:319-325 | GPG_KEYS_PUBLISHED:5-14 | The ES host is Substack and the members copy is gated, so only www serves the keys publicly. | Rewrite the Blog row. | L |
| stale | Investor SB figures | index_db.py; semantic_compile_daily timer | investor platform-generic exec-summary:29, pitch-deck:28 | Says "recompiled daily"; actual is 6-hourly plus on write, and the counts are from 08-24. | Re-derive. | H |

### 5.8 Integrity, signing, public mirror, supply chain

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| unlinked (×3) | GUARDRAILS_PROVEN.md | pre-commit; pre-push; push-public.sh; leak-guard/ | GUARDRAILS_PROVEN; README | Nothing links it. | Link from README and COMPLIANCE. | H |
| missing-index | Integrity and supply-chain overview | about 16 scripts | README; COMPLIANCE; REPRODUCIBLE_BUILDS; AGENT_TRUST_MODEL; pre-commit-README | About 35 controls spread over 5 docs. | Create INTEGRITY_AND_SUPPLY_CHAIN.md. | H |
| undocumented | Dependency audit | dependency-audit.py; repos list; units; pre-push:227-247 | REPRODUCIBLE_BUILDS:250-261; GUARDRAILS_PROVEN:59 | Modes, policy, override and the dormant-image rule are unexplained. | Add a section, an INFRA_MAP row and an ALERT row. | H |
| undocumented | Signed DB manifests | sign-db-manifest.sh; snapshot-db-manifest.py; manifests/ | POSTGRES_MIGRATION | The header points to a doc that has zero mentions. | Add a subsection. | M |
| undocumented (×3) | LADD push-time tripwire | ladd_public_check.py; allowlist; push-public.sh:147-187 | LADD_CUI_HANDLING; README:754-760 | Undocumented in the canonical doc. Fails closed below 1,000 identifiers; hash-only allowlist. | Add a section. | H |
| unlinked (×2) | Sovereign skill supply chain doc | skills/; vendor-pins; skill_grants; skill-snapshot | SOVEREIGN_SKILL_SUPPLY_CHAIN | Not linked from README or AGENT_SEGMENTATION. | Link it. | H |
| undocumented (×2) | .agents/skills Codex mirror | .agents/skills/* | SOVEREIGN:21-24 | A diverged stale fork (old 900k blocking hook, wrong paths), not in the grant catalog. | Document it, then regenerate or mark it unmaintained. | M |
| undocumented | scrub-public-tree mechanism | scrub-public-tree.py | COMPLIANCE:120; SECURITY.md:99 | The layers and exception procedure are unexplained. | Add "How the public scrub works". | M |
| undocumented | Sign-time CLAUDE.md drift gate | sign-manifest.sh:59-90, :156-157; check-claude-md-drift.sh | README:510-547; AGENT_TRUST_MODEL §10 | Exit 3, bypass and exclusions are undocumented. Unclear what it checks now that CLAUDE.md is a scratchpad. | Document it and confirm the purpose. | L |
| undocumented | Website integrity sweep | website-integrity-sweep units | INFRA_MAP:212; COMPLIANCE | Name only. | Add a coverage row. | L |
| wrong | Transcript leak guard coverage | transcript_leak_scan.py:19-21, :85-86; install-root-copies.sh | GUARDRAILS_PROVEN | Says "every transcript on the box". It scans only the operator's Claude transcripts; there is no root copy or timer; Codex is not scanned. | Correct it or extend the scan. | H |
| stale | Integrity sweep scope | scheduled-integrity-sweep.sh:71-165 | COMPLIANCE:87; INFRA_MAP:145; ALERT_REFERENCE:228, :531 | Five checks; the alert titles are missing. | Update. | M |
| stale (×3) | verified-exec count | verified-exec.sh; llama-fallback quadlet | README:580; COMPLIANCE:84; INFRA_MAP:143 | Docs say 39; there are 40. | Update or derive. | L |
| stale | Public mirror gate order | push-public.sh:44-249 | COMPLIANCE:118-121, :137 | Leak scan and tripwire steps and exit codes are missing; an UNVERIFIED line remains. | Rewrite. | H |
| wrong | pre-commit pattern set | pre-commit:14, :44-51, :146-155 | GUARDRAILS_PROVEN:52 | The 64-hex and env-value patterns are only in the header comment. | Fix the row or the hook. | M |
| stale | Git hooks doc | pre-commit; pre-push; post-commit-doc-verify; install-git-hooks.sh; site-pre-push | scripts/pre-commit-README.md | Stale post-commit warning; pre-push dependency and leak checks missing; installer unmentioned. | Move into docs and rewrite. | M |
| wrong | Lock currency "ENFORCED at sign" | sign-manifest.sh; build-policy.py; sweep:113-121 | REPRODUCIBLE_BUILDS:38 | Only the sweep detects it (unverified 2026-10-25). | Correct or wire it. | M |
| wrong | podman builds outside policy | deploy-acars-stack.sh:46; session-restore.sh:137 | REPRODUCIBLE_BUILDS:41; README:552 | "Every podman build" is false; acars-watcher builds outside policy. | Name the exceptions or route them through policy. | M |
| stale | Public-history rewrite claim | ladd_public_check.py:4-7; push-public.sh | README:765-769 | Three LADD identifiers remain in public history; the rewrite is still open. | Qualify the sentence. | H |
| stale | Public-mirror templates | push-public.example.sh; scrub-public-tree.example.py | — | July baseline lacking the October gates. | Regenerate or label. | L |
| undocumented | Website public-mirror pipeline | site-public.sh; site_public.py; public-manifest.sh | COMPLIANCE; REPRODUCIBLE_BUILDS | A second publishing path, undocumented. | Add a subsection. | M |
| undocumented | Skill twin gate (skills-sync) | skills-sync.sh; skills-vendor-ignore.txt; stack-refresh:259 | SOVEREIGN:24 | Undocumented. | Add a paragraph. | L |

### 5.9 Backups, operations tooling

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| unlinked / undocumented (×9) | Backups subsystem | scripts/backup/*; agent_timecard.py; 5 timers | BACKUP.md; README; COMPLIANCE; REFERENCE_INFRA; INFRA_MAP; investor docs | README has no backup section. BACKUP.md is linked only as plain text from the unlinked LADD doc. | Tier A3. | H |
| wrong | Off-box sync | ctdi-backup.sh:41, :140-150; backup_freshness.py:60 | BACKUP:3, :73 | BACKUP_TARGET is unset, there is no rclone config and zero sync ledger entries. The repo shares the disk with the data. Freshness skips the off-box check. | Add a current-state line; no DR claims. | H |
| wrong | Freshness agent-snapshot coverage | backup_freshness.py:62-66; agent-home-snapshot.sh | BACKUP:87, :94 | Checks codex only; 6 accounts are snapshotted. | Fix the code or the doc. | M |
| wrong | CUI "shredded" on btrfs | ctdi-backup.sh:94, :68; restore-test:21 | BACKUP:31; LADD_CUI_HANDLING:18, :29, :61 | The script itself says shred is ineffective on btrfs. | Reword; record the real control. | H |
| wrong | Snapshot "credentials excluded" | agent-home-snapshot.sh:24-28; system unit | BACKUP:91, :87 | Glob-based. A session .key file and a browser profile were captured. The exclude variable cannot be set. | Restate the glob list and residual risk; add EnvironmentFile. | M |
| stale | Backup sizing | ctdi-backup.sh:121-124 | BACKUP:65-73 | Estimated 10-14 GB; repo is 19 GB with 54 GB free. vnstat was added after the measurement. | Re-measure; add to COGS. | H |
| stale | Registries missing backups | backup timers; ctdi-backup.sh:44 | INFRA_MAP:200-215; SECRETS_NAMING:97-112; COMPLIANCE | No rows; passphrase file not registered. | Add them. | H |
| undocumented | ctdi-backup flags and exclusions | ctdi-backup.sh:33-125 | BACKUP | --no-sync/--dry-run, Borg exit-1 warning, honeypot-token exclusion and restore-test scope are undocumented. | Add a paragraph. | M |
| undocumented (×3) | Load stamps | scripts/lib/load_stamp.py; conftest:205-215 | README; SINGLE_EDGE | No mention anywhere. Relay use cannot be verified from the repo. | Add a section. | L |
| undocumented (×3) | Relay tooling and relay-nudge | relay-nudge.py | README; OPERATOR_CONSOLE; SECRETS_NAMING:105 | Undocumented. Reads the master file but is not listed as a reader. | Add a Relays section and register it as a reader. | L |
| unlinked | Stack refresh and holds | stack-refresh.sh; tripwire_draw.py; units | REFERENCE_INFRA:269-282; README:558-565 | --weekly also holds on listen-port and unit-set changes; no REBASELINE runbook. | Link and add a runbook. | M |
| undocumented | serialized-rollout knobs | serialized-rollout.sh:20-50 | README:545-550, :711 | Knobs undocumented. README suggests a locals-only restart, against the full-load rule. | Replace README:547; add a knob table. | L |
| undocumented | Demo admin token separation | demo_api.py; allowlists; rotate-credential.sh:7-17 | SECRETS_NAMING; regulated-operator-setup:89 | The b2a97645 split is unrecorded; rotation covers four names. | Add to SECRETS_NAMING. | M |
| unlinked | Credential rotation | rotate-credential.sh | regulated-operator-setup:89; SECRETS_NAMING:105 | Neither doc is linked from README; modes are undocumented. | Add a subsection and link. | M |
| missing-index | Operations tooling | backup/; lib/; stack-refresh; rollout; rotate; relay; timecard | 8+ docs | No index. | Create OPERATIONS.md. | M |
| stale (×3) | unit-failure-notify coverage | 13 units with OnFailure= | ALERT_REFERENCE:235 | Lists 7 units; 13 exist. | List all or give the grep. | L |
| stale | stack-refresh header | stack-refresh.sh:12-17 | (in-code) | Says nightly with a 1-7 day gap; actually hourly check with a 36 h-7 d draw. | Code fix. | N |
| stale | weekly-external-image-update.sh | the script; service comment; safe-pg-image-update.sh:10 | ALERT_REFERENCE:235 | Dormant but listed as live. | Retire or mark dormant. | N |

### 5.10 Systemd, Quadlets, nginx, cloudflared, network, watchdogs

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented | .path units with self-Requires= | knowledge-graph-compile.path; semantic-compile-daily.path; check-timer-requires.sh | BOOT_STORM; INFRA_MAP §10 | The same mechanism banned for timers; the guard checks *.timer only. | Drop Requires= or document the exemption. | L |
| undocumented | nginx rate limiting and cfreal log | 00-rate-limit; 00-log-format; vhosts | HONEYPOT_FAIL2BAN; INFRA_MAP §6b | Zone/vhost matrix undocumented; gaps on cloud/dav; demo_login depends on an untracked vhost. | Add a subsection. | M |
| unlinked | Honeypot/fail2ban and edge-ban question | honeypot.conf; jail; action.d:59; cf-honeypot-ban.sh; SELinux .te | HONEYPOT_FAIL2BAN | Not linked from README or INFRA_MAP. [UNVERIFIED] edge bans may fail since 2026-10-03 because of SELinux labelling. | Link; run the check before citing edge auto-ban. | H |
| undocumented | cloudflared restart drop-in | cloudflared.service.d/restart.conf | INFRA_MAP | Tracked but not installed; restart policy undocumented. | Install or delete; document the policy. | M |
| undocumented | unit-failure-notify@ template | the .service; unit-failure-notify.sh | ALERT_REFERENCE; ALERT_ARCHITECTURE; BACKUP:83 | 13 units; topic and priority undocumented; Description says "weekly". | Add a row and a paragraph. | L |
| undocumented | production.slice envelope | production.slice | SINGLE_EDGE; GUARDRAILS_JUSTIFICATION; HARDWARE_GUIDANCE | 6 G / 13.4 G / 13.78 G values appear only in struck or dated text. | Add a row. | M |
| unlinked (×4) | Airline time-fidelity collector | timer and service; scripts/research/* | AIRLINE_TIME_FIDELITY_SPEC | Linked from nowhere outside docs/research. Monday is the first checkpoint. | Link it and add to INFRA_MAP now. | M |
| undocumented | Boot-stagger exceptions | stack-boot-ctl.sh; execstandard-verifier, ccw-demo, ccw-preview1 | README:526-528 | Three containers start outside ORDER. | Add to ORDER or document. | L |
| missing-index | Unit fleet index | user, Quadlet and system unit dirs | INFRA_MAP; README; ALERT_REFERENCE | About 87 user units; no unit→doc map; INFRA_MAP links none of the five ops docs. | Add a Doc column, generated. | M |
| undocumented | nextcloud-cron | nextcloud-cron .timer/.service | INFRA_MAP | Not in any doc. | Add a row. | L |
| undocumented | Unbound anchor units and tmpfiles | systemd/unbound-anchor-*; tmpfiles.d | INFRA_MAP; REFERENCE_INFRA | Partly uninstalled; undocumented. | Document or point to the owning repo. | L |
| stale (×2) | Hardware watchdog | zz-ctdc-watchdog.conf; watchdog-tune units; stall-monitor | INFRA_MAP:54, :172; GUARDRAILS_JUSTIFICATION:159-164; SINGLE_EDGE:41; PI5-BOOT-CONFIG:71-89 | Tuned to 30 s on 2026-10-12. Docs say 180 s and a diagnostic week. Repo copy drifts from live. | Update and decide stall-monitor's fate. | M |
| wrong (×5) | Scheduled cloud LLM job | second-brain-weekly-dump .service/.timer; script:139 | README:11-12; COMPLIANCE:9-10, :32-35; DESIGN-PRINCIPLES:33-34; COST_STRUCTURE:152-154; COGS:48 | `claude --model opus` runs weekly and the timer is enabled. | Tier A1 and A6. | H |
| wrong | ISO 42001 A.7/A.10 | weekly dump; accounts; gateway | ISO_42001:45, :48 | Marked "Aligned" despite the third-party AI paths. | Reassess. | H |
| undocumented | Commercial vendors | weekly dump; backup; airplanes_live; gateway | DESIGN-PRINCIPLES:85-92 | Anthropic, OpenAI, airplanes.live and the backup target are missing. | Add rows. | H |
| stale (×3) | README, INFRA_MAP and REFERENCE_INFRA counts | unit dirs; pg_schema; MANIFEST | README:16-26; REFERENCE_INFRA:101-252 | 70/65 timers, 77 Quadlets, 74 migrations, about 1,388 manifest lines. | Re-measure (A5). | M |
| wrong | Uplink failover | net-failover-watchdog.sh; timer | REFERENCE_INFRA:51-52; HARDWARE_GUIDANCE:153-154 | Backup uplink DOWN since 2026-10-06; no real failover. | Add a caveat. | H |
| stale | NETWORK_FAILOVER findings and links | timers | NETWORK_FAILOVER #3; BOOT_STORM | 11 timers have RandomizedDelaySec (doc says 2). Dead cross-reference; unlinked. | Update and link. | L |
| wrong | BOOT_STORM intro | check-timer-requires.sh | BOOT_STORM:8-11 | References a nonexistent Findings section; the violations are already fixed. | Replace the sentence. | L |
| stale | cloudflared ingress | cloudflared/config.yml:51-52, :99-109 | INFRA_MAP §11 item 5, §6 | The ollama ingress is gone; the Worker is omitted. | Close the item; add the Worker. | L |
| stale | Retired mcpo units | corporatetraveldc-mcpo*.service | INFRA_MAP §9 | Still in the active directory. | Move and note. | L |
| wrong | Kickover guardrail coverage | failover-kickover-guardrail.py | ALERT_REFERENCE:224 | Omits amtrak. | Add it. | L |
| undocumented | CPU governor control | config/tuned/...; governor-watch.py; units | HARDWARE_GUIDANCE; GUARDRAILS_JUSTIFICATION | The tracked tuned profile is not active; governor-watch and a pinned sudo grant hold the governor. | Document; install or retire the profile. | L |

### 5.11 SDR and watchers

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented / stale (×3) | VDL2→local_airspace→acars_messages path | local_airspace.py:54-76, :449-470; acarsrouter quadlet; test | SDR_SERVICES; DATA_SOURCES:324-334; ACARS-addendum:110-145; INFRA_MAP:230 | Only a diagram arrow. The addendum still says "zero rows". | Add a dataflow section. | H |
| undocumented | Outbound receiver-data sharing | ultrafeeder, acarsrouter and feeder quadlets | SDR_SERVICES:26-31; DATA_SOURCES | 4 ADS-B destinations, 2 MLAT networks (location exposure) and ACARS/VDL2 to two services are undocumented. | Add an outbound-feeds table. | H |
| undocumented | SDR hardware BOM | dumphfdl-0008 (disabled); udev rules; sdr-crashloop-guard.sh:4-16 | HARDWARE_GUIDANCE; COST_STRUCTURE:80-113 | Antennas, LNA, expansion hardware and the dongle-loss history are missing. | Add an "SDR/RF hardware" section. | H |
| undocumented | Disabled SDR watchdogs | acars/adsb silence and link watchdogs and timers | SDR_SERVICES; INFRA_MAP:390; ALERT_REFERENCE:234 | All three are disabled; only crashloop-guard runs, so quiet-channel outages are unalerted. | Add a Monitoring section. | M |
| undocumented | ais-watcher behaviour | ais_watcher.py:1-110 | SDR_SERVICES; DATA_SOURCES | Static MMSI only; ignores the vessel watchlist API. | Document it. | M |
| undocumented | utm-watcher | utm_watcher.py; runner/main.py:1239-1262; permanent_drones.json | SDR_SERVICES:45; DATA_SOURCES | Inert stub. Env names mismatch; no drones.json provider. | Say inert; list the env names. | M |
| undocumented | Host SDR prerequisites | 99-rtlsdr-blacklist.conf; corporatetraveldc-sdr-usb.te | SDR_SERVICES; COMPLIANCE:278-281 | Undocumented; a new deployment would miss them. | Add a Host setup section. | L |
| undocumented | acarshub receiver coordinates | acarshub quadlet (ADSB_LAT/LON literals) | GPS_COORDINATE_CONFIGURATION; README:568-570 | A fourth coordinate location (ties to the "receiver position" backlog item). | Add it. | L |
| missing-index | SDR subsystem overview | SDR quadlets, timers, watchers, scripts | SDR_SERVICES; SDR_SERVICES_README | README:146 links it as plain text; its scope is insufficient. | Restructure as an index. | M |
| wrong | acars-watcher "triple-source" | acars_watcher.py:170-350; acarsrouter | SDR_SERVICES:30; INFRA_MAP:98-99; ACARS-addendum:60-63 | 7 days: 3 airframes matches, 0 local, 0 Jumpseat. Nested VDL2 registration not parsed; callsigns treated as registrations. | State the reality; code fix. | H |
| wrong | acarshub UI binding | acarshub quadlet | SDR_SERVICES:29 | Tailnet port 8092, not loopback 9081. | Fix. | M |
| wrong | dumpvdl2 device passthrough and launch.sh | dumpvdl2 quadlet; containers/dumpvdl2/launch.sh | SDR_SERVICES:28 | Scoped device plus read-only bus since 08-26 (container_t); overlay undocumented. | Fix and add a note. | M |
| stale | Disabled-service enable procedure | systemd/*.disabled; build provenance; stack-boot-ctl | SDR_SERVICES:54-73; REPRODUCIBLE_BUILDS:41-46 | Bypasses the build policy. ais and utm watchers load the master secrets file; nonexistent network. | Rewrite with per-quadlet prerequisites. | M |
| wrong | HFDL routing | dumphfdl-00xx disabled; acarsrouter | SDR_SERVICES:41, :106-107 | HFDL does not reach UDP 5005; there is no router HFDL input; old FEED_ID form. | Correct. | L |
| wrong | acarsdec staged quadlet | acarsdec.container.disabled:16, :21 | SDR_SERVICES:34-40 | Nonexistent duplicate path. Uses the in-service VDL2 dongle serial, so it would conflict. | Fix. | L |
| stale | ACARS-addendum | acars_watcher.py:40-47, :360-396; allowlist | ACARS-addendum:66-153 | Fixed defects shown as open; wrong credential location. | Strike through and repoint. | L |
| wrong | rtl-eeprom-reserialize.sh | the script; udev rules | SDR_SERVICES:77-78 | A one-off rename that would break the VDL2 chain if run as documented. | Document the raw procedure; mark historical. | L |
| stale | deploy-acars-stack.sh | the script | SDR_SERVICES | Obsolete and unsafe (boot-stagger violation). | Mark do-not-run. | N |

### 5.12 Common libraries

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| unlinked | DEDICATED_MODELS_PLAN | llm.py; llama_pool.py; personas.py | README:623-662, :1319 (struck) | Current-state doc linked only from struck text. | Link it; consider renaming. | M |
| unlinked / undocumented (×7) | Deployment profiles | deployment_profile.py; ctdi-reference.json; validate_profiles.py; metar.py:25-34 | deployment-profiles/STAGED, README; REGIONALIZATION; GPS; ALERT_ARCH; investor docs | Live loader with the METAR consumer; no link from any top-level doc. smes regional airports are not yet profile-driven. | Tier B5. | H |
| undocumented | Maintenance window and optime | maintenance_window.py; maintenance-window-guard.sh; optime.py | SINGLE_EDGE:40; SECOND_BRAIN_STATUS:40 | Env knobs and footgun undocumented; not reconciled with quiet windows. | Add a section. | L |
| undocumented | Intel helper libraries | rss_retrieval; entity_tracking; aam_watch; disruption_weather_watch; export_analysis; trends_signal | SECOND_BRAIN_STATUS:48-55 | Undocumented. trends_signal is dormant but trendspy ships in every image. | Add a subsection; decide on trendspy. | M |
| undocumented | import_ledger.verify | import_ledger.py:86-99; ctdi-backup.sh:92 | LADD_CUI_HANDLING:61; BACKUP:37 | Never called; no scheduled verification. | Document or schedule it. | L |
| missing-index | src/common | about 45 modules | README; dated reference | No living index. | Create COMMON_LIBRARY.md. | L |
| wrong | config.py loads the master secrets file | config.py:26-28; db.py:19; db_backend.py:62; host scripts | SECRETS_NAMING:105; README:506-508 | Every host importer gets all master secrets. | Document; decide on scoping. | H |
| wrong | common.guardrails listed as an audit writer | guardrails.py:40, :112 | COMPLIANCE:151 | No callers, ever. Naming clashes with SR-1/SR-2. | Mark dormant. | H |
| stale (×3) | PG migration and table counts | pg_schema/0074; db_backend.py:90-109 | README:26, :71, :614; POSTGRES_MIGRATION:13-18, :66-71 | Says 73; there are 74. | Update. | L |
| stale / wrong (×2) | Legacy SQLite constants | db.py; db_swim.py | README:615-617 | V44, V45 and SWIM_V47 are missing. | Fix the ranges. | N |
| stale | REGIONALIZATION vs profiles | metar.py; deployment_profile.py; optime.py | REGIONALIZATION:12-38 | METAR is listed as a constant. DISPATCH_PROFILE, LOCAL_TZ and DAY_ROLLOVER are missing. | Update. | M |
| stale | Composition spec status | composition.py; test_composition.py; example JSON | EMCOMM spec:3, :55-66; STAGED.md | Says "spec only", but the skeleton has shipped. It claims dataclasses; there are none. Backlog item 1 is already done. | Update the status. | M |
| stale | FlightAware AeroAPI tier | flight_resolver.py:96-108; allowlists | DATA_SOURCES:310-315; README:393 | The key name is on 7 allowlists, so "dormant" is unproven. | Verify without printing; record cost. | H |

### 5.13 Agent skills and tooling

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| missing-index | Agent skills catalog | skills/; .claude/skills; .agents/skills; AGENTS.md | README; AGENT_SEGMENTATION:298-302; SOVEREIGN:21-23 | Names only; side effects (watchlist adds, pushes, vault writes) undocumented. | Create an AGENT_SKILLS index. | H |
| wrong | Skill grants for Codex | skill_grants.py:28-32, :458-483; codex unit | AGENT_SEGMENTATION:304; SOVEREIGN:34 | Grants only reach ~/.claude/skills, so Codex gets nothing. | State it or extend grants. | M |
| stale | Cloud pamphlet publisher | publish-pamphlet-to-vault.sh:8, :20-26 | AGENT_SEGMENTATION:345 | Tells ChatGPT it runs in Anthropic's cloud and names the wrong credential. | Rewrite vendor-neutral. | M |
| stale | skill-capabilities.txt | :10, :13 | AGENT_SEGMENTATION | Says the workspace route is "coming" (it exists). Wrong "needs" for morning. | Update. | L |
| stale | AGENTS.md | AGENTS.md:11 | AGENTS.md | Says codex is "preloaded"; it is active. | Update. | L |
| stale | context-guardian SKILL.md | context_hook.py; restore/save scripts | SKILL.md:23, :107 | Wrong token source; fixed formatters listed as open; log path undocumented. | Update. | L |
| stale | dispatch-ops skill | SKILL.md:97-131 | — | Timer table, Ollama and SQLite DB are wrong. | Regenerate. | L |
| stale | flight-hifi-track and ACARS addendum | SKILL.md:88-124; ACARS-addendum:3-12, :122 | — | SQLite SQL; a direct airframes call contradicts the local-only rule. | Rewrite for Postgres; route the call. | M |
| stale | hub-arrivals-lookup skill | SKILL.md:12-33 | — | Primary MCP tools do not exist; FDPS claimed broken. | Repoint. | L |
| stale | second-brain-remember | SKILL.md:1-8, :44 | — | Says SQLite FTS; duplicate frontmatter. | Fix. | N |
| wrong | personal-export-analysis trigger | SKILL.md:264-267 | — | Says no timer; a weekly timer exists. | Fix. | N |
| stale | Operator settings table | live settings.json | SOVEREIGN | Missing keys. | Refresh from live. | M |
| wrong | morning skill provenance | skills/morning/* | SOVEREIGN:21 | Appears vendor-authored; licence unrecorded; pushed public. | Record origin and licence. | M |
| stale | render-onboarding runbook row | render-onboarding.sh | AGENT_SEGMENTATION:127 | Omits the AGENTS.md targets. | List all three. | N |

### 5.14 README and architecture/security/ops doc batches (items not already merged above)

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| missing-index (×4) | Docs index / living docs unreachable | — | README:46-52; docs/ | No docs/ index. COMPLIANCE is linked only from struck text. CIFP, PI5-BOOT, NETWORK_STORAGE, PANEL_PARITY, BACKUP, SECRETS, SUDO, ISO, HEADLESS and REFERENCE_INFRA are not linked from README. | Section 3 of this report. | H |
| undocumented | Recent timer jobs | dns-anomaly-watch; time-fidelity; codeshare-seed; timecard; load_stamp; relay-nudge | README; INFRA_MAP | Name and schedule only, or nothing. | Scheduled-jobs table and docs. | M |
| stale | Integrity sweep status row | integrity-sweep units | README:76 | One-off "failing" wording; the sweep is now success. | Update. | L |
| unlinked | LADD CUI procedure | import-ladd-filter.py | README:718-725 | Reachable only via DATA_SOURCES. | Add a sentence and link. | L |
| stale | Key paths: scoped secrets | service-env/; demo-api quadlet:59 | README:702 | Count is stale; per-container secrets files missing. | Update and link. | L |
| stale | Root-installed copies | install-root-copies.sh:43-47 | README:537-540 | List incomplete. | Update. | L |
| undocumented | Security controls since 10-07 | leak-guard; dependency-audit; dns-anomaly-watch; agent_timecard | COMPLIANCE; REPRODUCIBLE_BUILDS | Zero references. | Add §5 supply-chain subsection. | M |
| undocumented | Headless admin channels | gui-window.sh; console.py | HEADLESS_ACCESS | mosh/screen undocumented; no pointers. | Add a section. | N |
| stale | Team roster | /etc/ctdc-accounts.conf | ISO:41; SUDO:18; HEADLESS:42-43 | ChatGPT and codex states are missing. | Refresh. | L |
| stale | Quadlet hardening counts | live quadlets | COMPLIANCE:84, :265, :268, :304, :314 | Five figures have drifted. | Re-run. | N |
| stale | Secrets registry | allowlists; ctdi-backup.sh:45 | SECRETS_NAMING:103-112 | Counts, demo token split, backup passphrase, approval pin and rclone config. | Add rows. | N |
| stale | PG cutover date | db_backend.py | COMPLIANCE:60 | Says 09-20; it was 09-18 (last tables 09-20) (unverified 2026-10-25). | Fix. | N |
| undocumented | Approval-gate Python publishers | console.py:633-653; web/main.py:2963-2981; routes/agent_gateway.py:126; board_sweep.py:238 | ALERT_REFERENCE:65, :209 | Lists only shell scripts. | Extend. | L |
| undocumented | New ntfy publishers | relay-nudge; dependency-audit; backup units | ALERT_REFERENCE:213-235 | Missing. | Add rows. | L |
| undocumented | Local receivers as a data source | local_airspace.py; ultrafeeder | DATA_SOURCES:5-13, :324-334 | The primary position source has no entry. | Add a subsection. | M |
| undocumented | Codeshare seed and time-fidelity as derived data | seed-dc-codeshare.py; research/* | DATA_SOURCES; DCA_IAD_FIDS | Missing. | Add a "Derived reference data" subsection. | M |
| unlinked | Backups as a network-storage use case | ctdi-backup.sh | NETWORK_STORAGE_CONSTRAINTS:76-85 | Still hypothetical; translate_sql line reference stale. | Link BACKUP. | L |
| unlinked | hub_arrivals_lookup FIDS consumer | hub_arrivals_lookup.py | DCA_IAD_FIDS:38-47 | Not in the Code block. | Add it. | N |
| stale | LADD automated download | faa_registry.py:337-365 | DATA_SOURCES:289-293 | Retired 2026-10-08. | Update. | L |
| stale | METAR station list | metar.py:25-34 | DATA_SOURCES:223-227 | Now profile-sourced. | Update. | M |
| stale | ALERT_REFERENCE fdps line numbers | fdps_parser.py:1188, :1395, :1845 | ALERT_REFERENCE:48-52, :144-147 | Off by 4 (other parsers match). | Cite functions. | N |
| wrong | PI5 watchdog subsection | watchdog-tune; stall-monitor | PI5-BOOT-CONFIG:71-88 | Outcome (30 s) not recorded. | Rewrite. | L |
| stale | Pi5 recovery sequence | backup scripts; llama units; artifacts.sha256 | PI5-BOOT-CONFIG:24, :132-161 | No Borg restore step; llama units and digest gate missing; disk is 77% (doc says 65%). | Update. | M |
| orphan-doc | addenda/ and WPC route | addenda/*; db.py:3864-3881 | README:256 | Addendum says no data flowing; the route is listed as working; addenda is unlinked. Rows not re-verified. | Link and caveat. | L |
| stale | Scheduled-task skills note | skills/nec-train-hifi-track, overwater-adsb-handoff-track | docs/tasks/scheduled/README.md:3-9 | Says only one of the three skills ships; all three are in skills/. | Re-banner or correct. | N |

### 5.15 Cost and investor documents

| Kind | Component | Code paths | Doc paths | Evidence | Fix | Rel |
|---|---|---|---|---|---|---|
| undocumented | Backups vs investor "no backup/DR" | backup timers; agent-home-snapshot (system) | investor v1.5 exec summaries and FAQs; COGS:75; COST_STRUCTURE | Backups exist but off-box is not configured. | Add a COGS line; claim on-box only. | H |
| undocumented | Profiles vs "four verticals; no automated onboarding" | profiles; loader; metar.py:28 | platform-generic exec summary:105; multivertical COGS | Not mentioned. | Add a "staged" capability row. | H |
| undocumented | Security controls as rent-equivalents | agent_gateway; build/SBOM; dependency audit; leak guard; approved-exec; audit chain; DNS watch | COGS:126, :196-200; investor exec summary:59-86; executive_summary:25-41 | The security stack is priced at $0-12/yr and omits the new controls. | Add lines and a controls list. | H |
| undocumented | Time-fidelity corpus | timer; 3366743e | investor docs; COGS §4 | Not mentioned. | Add after Monday. | M |
| missing-index | Cost and investor docs reachability | README | COST_STRUCTURE; COGS; executive_summary; investor-materials; CODEBASE_REFERENCE | No live README path. | Add a Business section. | H |
| wrong | "$0 cloud LLM; only power recurs" | second-brain-weekly-dump; llama-fallback quadlet | COST_STRUCTURE:152-154; COGS:48-51, :72; investor exec summary:43, :49; EGT FAQ:58 | Scheduled Opus and vendor plans. | Scope it and add a subscription line. | H |
| stale | "21 dedicated Ollama models" | llama.service; personas.py; build-models.sh | several investor src files | One Qwen3-4B model on llama.cpp. | Re-describe. | H |
| wrong | ACARS fusion "pending off-box fix" | local_airspace VDL2; acarsrouter | investor exec summaries and pitch decks | Fixed 2026-10-03; the cause was on-box. | Re-verify, then update. | M |
| stale | Demo corpus "2.45 GB growing" | demo-source.db (1.88 GB, frozen 08-14); refresh timer disabled | 8 investor files | executive_summary.md is already correct. | Align. | H |
| stale | Test and verification posture | test_cps_rvr; 115 test files; 40 gated | investor exec summaries and FAQs | Says no CPS test, 23 files, 33 containers. | Re-derive; add supply-chain gates; "no hosted CI" stays true. | H |
| stale | Database "single SQLite" | pg_schema | aviation-ops FAQ:47, :87 | It is Postgres. | Replace. | M |
| stale | Token expiry | web/main.py:299, :670-732; agent_gateway; rotate-credential.sh | platform-generic:68, :98; EGT:68 | Imprecise. | Restate exactly. | M |
| wrong | COGS internal totals | — | COGS:39, :143, :158, :184, :219 | :143 and :184/:219 are pre-broker-comp leftovers; :158 contradicts :105. | Recompute. | H |
| stale | COGS live inputs | cloudflared config (14 hostnames); pg_schema; quadlets | COGS:67-68, :114, :169, :215-217 | Says 9 hostnames and 65 migrations; the integrity hazard has cleared. | Re-derive. | H |
| wrong | Flight identity sourcing | airplanes_live.py; watchlist.py:354, :405 | executive_summary:19-21; COGS:71 | The fallback is unmentioned. | Qualify; add to the inventory. | M |
| stale | COST_STRUCTURE figures | — | COST_STRUCTURE:90, :113, :175, :189; COGS:66 | Tier 2 and power figures inconsistent; no COGS link. | Reconcile. | M |
| stale | executive_summary v1.3.0 | backup, profile, TTL, time-fidelity, airplanes_live paths | executive_summary:3, :25-41, :121-133 | Omits everything since 10-06 (the verified items are still true). | Cut v1.4.0 after the swarm. | M |