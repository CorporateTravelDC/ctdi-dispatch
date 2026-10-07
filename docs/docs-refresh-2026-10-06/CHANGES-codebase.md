# CHANGES — codebase reference

Output: `docs/CODEBASE_REFERENCE_2026-10-06.md`, which replaces
`docs/CODEBASE_REFERENCE_2026-09-28.md`. Verified against HEAD `2c3f81b` and live state
on 2026-10-06, 18:04Z–18:21Z / 14:04–14:21 ET. The pass started at `db64018` with an
uncommitted `scripts/stack-refresh.sh`. The operator signed and committed that file as
`2c3f81b` at 18:10Z, and nothing else changed between the two commits.

The 09-28 document was organised by topic: DB cutover, OOOI lock, geometric reasoning,
recent commits. The new one follows the section list in the task (snapshot, packages,
containers, routes, data, feeds, schedules, scripts, surfaces, TODOs). Three kinds of
09-28 content are not carried over. Point-in-time history (the OOOI incident narrative,
geometric-reasoning edge counts, the six-week commit list) stays in its own design docs
and in git. Its §9.2 manifest-hazard narrative was historical even on 09-28. Its §10 edge
counts were not re-measured, so they are dropped rather than repeated.

## Stale claims in `docs/CODEBASE_REFERENCE_2026-09-28.md`

| Claim as written (09-28) | Evidence (command → output) | Fix in the new reference |
|---|---|---|
| HEAD `78660147`, branch `fix/oooi-premature-landing-tfms-fids`, working tree dirty | `git log -1` → `2c3f81b` on `main`; `verify-manifest.sh` → `OK -- signature valid, all 1246 files match.` | §1 snapshot |
| 172 Python files / 67,748 LOC under `src/` | `find src -name '*.py' \| wc -l` → 180; `… \| xargs wc -l \| tail -1` → 72,091 | §1 |
| 65 Postgres migrations (0001…0065) | `ls src/common/pg_schema/*.sql \| wc -l` → 73 (0066–0073 added: board signers, authz tiers, approval resolve keys, signed approvals/council/workspace, token allowed_actions, agent gateway, ES invites, console + gateway switch) | §5.2 full list |
| 73 live quadlets | `ls ~/.config/containers/systemd/*.container \| wc -l` → 74 live; 76 tracked in `.config/containers/systemd/` | §3.2 |
| 74 host-level user timers | `ls ~/.config/systemd/user/*.timer \| wc -l` → 69 live files; 66 tracked | §7.1 |
| 34 running containers | `podman ps -q \| wc -l` → 36 (35 long-running + 1 oneshot) | §1 |
| 109 operational scripts | `ls scripts/*.sh scripts/*.py \| wc -l` → 132 (`ls scripts \| wc -l` → 143 entries incl. dirs) | §8 |
| "The old ~23 GB SQLite file is gone. `/var/lib/corporatetraveldc/corporatetraveldc.db` no longer exists" | `ls -la` → exists, 69,632 bytes, mtime 2026-10-05 23:21Z | §5.3 (and finding 9) |
| Retired `second_brain_index.db` still on disk, 104 MB (hazard §2.2/§12.2) | `ls /var/lib/corporatetraveldc/second_brain_index.db` → No such file. The vestigial constants are still at `index_db.py:78`, `semantic/compile.py:91` | §5.3: file gone, constants remain |
| `ingest-core` = "NWWS-OI + Amtrak + local airspace monitor" | `ingest-core.container`: `AMTRAK_ENABLED=false`, `SWIM_NMS_ENABLED=false`, `LOCAL_AIRSPACE_ENABLED=true`; Amtrak runs in `amtrak-tracker` | §6.4 |
| llama.cpp servers are "host-level systemd services"; exact GGUF `[UNVERIFIED]` (§5, §12.4) | `systemctl --user show corporatetraveldc-llama.service -p ExecStart` → one user unit, `/usr/local/lib/ollama/llama-server -m /var/lib/corporatetraveldc/models/qwen3-4b-instruct-2507-q4_0.gguf --host 100.x.x.x --port 8093 -np 2` | §3.4 (resolved) |
| "Ollama vocabulary … llama.cpp behind `OLLAMA_*` names" | `llm.py:364` reads `LLAMA_BASE_URL`, with `OLLAMA_BASE_URL` only as a deprecated alias; `config/dispatch.env:115` sets `LLAMA_BASE_URL` | §2.3 |
| "~45 skills … ~17 fetchers" | 47 skill files, 16 fetcher modules (13 in `FETCH_SCHEDULE`); only 10 skills run in-process (`SKILL_SCHEDULE`), the rest are quadlets | §2.3, §6.3, §7.3 |
| maintenance_window = the fixed overnight 23:00–05:00 window | rolling windows since 2026-10-04: `quiet-window-report.py` + `maintenance-window-guard.sh --rolling` + `maintenance-dispatch.sh` + `lib/maintenance-queue.txt` (9 enrolled units) | §7.4 |
| Auth = T0/T1/T2/admin + `X-CTDI-Public` | Still true, but incomplete. Added since: per-token `allowed_actions` (0070), board SSH-signature policy (`main.py:271`), human-signed approvals, council/workspace, OAuth/MCP gateway, phone console, ES invites | §4 tier table, §9.3 |
| "34 containers … `systemd-corporatetraveldc-demo` archive recorder" etc. (container list §3.1/3.2) | `podman ps`. The `corporatetraveldc-execstandard-verifier` container (poller image, :8787) was missing from the list | §3.2 |
| Manifest stale, ~23 units failing (§9.2-hist, §12.1) | `verify-manifest.sh` OK at `2c3f81b`. At 18:05Z the only failed unit was user `corporatetraveldc-integrity-sweep` (unsigned edit, since committed) | §1, §7.2 |
| AIS/UTM "code present, not deployed" | Still true: no running container; quadlets only as `.disabled` | carried (§2.2) |
| Sovereign demo DB SQLite, 1.88 GB | Still true: `demo-source.db` 1,881,227,264 bytes | carried (§5.3) |
| Tier claim "`/api/v1/watchlist` … T1" (implicit, from route shapes) | Two `main.py` routes are shadowed by the router (finding 1) | §4.1 marks them |

## [UNVERIFIED] items in the new reference

- Applied migration state on the live Postgres (0071–0073). No DB credentials were used. `/healthz` returned `status: ok`.
- What wrote `/var/lib/corporatetraveldc/corporatetraveldc.db` at 2026-10-05 23:21Z.
- Whether `FLIGHTAWARE_API_KEY` / AISHub ID are set. If they are, the poller and runner call `aeroapi.flightaware.com` / `data.aishub.net`. Secret files were not read.
- The owner of 127.0.0.1:20241 (assumed cloudflared metrics). `ss -p` cannot see other users' processes without privileges.
- Host firewall (firewalld) rules for the `0.0.0.0`/`*` listeners.
- `Containerfile.docgen`: I found no build or usage site.
- Runner per-route auth was not tabulated (Appendix A gives the gate model only).
- Whether `corporatetraveldc-integrity-sweep` cleared after the 18:10Z commit. I did not re-check it.
- SR-1 token accounting (09-28 §12.7): not re-examined in this pass.

## Findings for the operator

These are live defects or likely defects. None were fixed.

1. **Two web routes are unreachable (shadowed).** In `src/web/main.py`, `GET /api/v1/watchlist` (`list_watchlists`, line 1629, T1, lists watchlist *sessions*) and `DELETE /api/v1/watchlist/{session_id}` (`terminate_watchlist`, line 1649, T1) are registered after `app.include_router(watchlist_router)` (`main.py:103`). Starlette returns the first match, so `routes/watchlist.py:83` (`list_watchlist_entries`) and `routes/watchlist.py:535` (`remove_watchlist_entry`, **admin**, deletes from `watchlist_entries`) answer instead. Session termination over HTTP is therefore impossible. A DELETE meant for a session id goes to the admin entry-delete instead. No caller was found in `src/runner/frontend/src` or `tests/` (`grep`). Either way, these are dead routes with misleading tiers.
2. **Four timers now fire on UTC, not Eastern.** The host clock became UTC on 2026-10-06 ~17:15Z (`timedatectl`), and these `OnCalendar` lines carry no timezone suffix:
   - `corporatetraveldc-pull-path-verify.timer` (`*-*-* 06,18:00:00`): last ran 18:00Z, next 2026-10-07 06:00Z. It used to run at 10:00Z/22:00Z.
   - `corporatetraveldc-docs-drift-weekly.timer` (`Mon 09:00`): next 2026-10-12 09:00Z. The last run was 13:00Z.
   - `corporatetraveldc-second-brain-weekly-dump.timer` (`Sun 02:00`): next 2026-10-11 02:00Z, which is Saturday 22:00 ET.
   - `corporatetraveldc-nms-v240-check.timer`: the date has passed, so it is moot.
3. **The quiet-window load profile is now skewed by 4 h.** `scripts/thermal-sample.sh:48` stamps `date '+%Y-%m-%d %H:%M:%S'` in host-local time, which is now UTC. `scripts/quiet-window-report.py:59-60` parses every row as America/New_York. Every sample since ~17:15Z is therefore bucketed 4 hours late. Over the 30-day window this will move the rolling maintenance windows and the tripwire candidates (`today_windows`) away from the real quiet hours.
4. **`transport-pattern-digest` reports real failures as success.** Its `Exec=` is `/bin/sh -c 'flock -w 1200 … python3 …transport_pattern_digest.py; rc=$?; [ "$rc" -eq 1 ] && { echo "…lock still held… (not a failure)"; exit 0; }; exit "$rc"'` (`.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container:51`). A lock timeout exits 1 (no `-E 75`), but so does a genuine skill failure, so both are logged as "lock still held" and exit 0. The sibling long-runners use `flock -E 75` with `SuccessExitStatus=75`.
5. **Live units outside the repo and the manifest.** These exist in `~/.config/systemd/user` only:
   - `corporatetraveldc-ollama-swap-alert.service` (disabled), whose `ExecStart` script `scripts/ollama-swap-alert.sh` **does not exist**.
   - `corporatetraveldc-ccw-demo-webdev-expiry.{service,timer}`.
   - `blog-substack-reminder.{service,timer}`.
   - `corporatetraveldc-ops-brief-deferred.timer` (the quadlet is tracked, its timer is not).
   - `corporatetraveldc-claude-remote-control.service`.
   - `nextcloud-net.network`.
6. **Tracked units not installed:** `corporatetraveldc-mcpo.service`, `corporatetraveldc-mcpo-public.service`, and the quadlets `corporatetraveldc-demo-portal-client` / `-personal`. They are either retired-in-place or never deployed, and the docs should say which.
7. **A timer runs a script from `$HOME`.** `ops-brief-rebuild-watcher.timer` (tracked, currently disabled) runs `%h/ops-brief-rebuild-watcher.sh`, an unsigned script outside the repo. It is harmless while disabled, but enabling it bypasses the manifest.
8. **A dead public hostname.** The Cloudflare tunnel still routes `ollama.example.com` → nginx (`cloudflared/config.yml`, comment "raw Ollama API — CF Access gated"). Ollama was retired 2026-08-27 and no live nginx `server_name` matches it, so it falls to `000-default-catchall.conf`.
9. **Stale SQLite files.**
   - `corporatetraveldc.db` was recreated (69 KB, mtime 2026-10-05 23:21Z), so something still opens the SQLite `DISPATCH_DB` path; `config.py:65` defaults it there.
   - `demo.db` (4.45 GB, migrated by 0057), `demo_access.db`, `dispatch-chat.db` (migrated by 0056) and an empty `dispatch.db` are still in `/var/lib/corporatetraveldc/`.
10. **Skills with no scheduler in this repo:** `src/poller/skills/daily_brief.py` and `src/poller/skills/executive_standard_sync.py`. Both are referenced by nothing in `.config`, `systemd`, `scripts` or `poller/main.py`, so they are dead or scheduled from another repo.
11. **Stale one-shot still enabled.** The `corporatetraveldc-nms-v240-check` quadlet and timer (2026-08-08) are still enabled.
12. **Receive-side silence watchdogs are disabled.** `acars-feed-silence-watchdog`, `adsb-feed-silence-watchdog` and `adsb-link-watchdog` timers are tracked but `disabled/inactive`. So are the `knowledge-graph-compile`, `retrofit-links` and `demo-source-refresh` timers; knowledge-graph-compile and semantic-compile run on `.path` triggers instead. Confirm this is intended.
13. **A Tier-0 route leaks exception text.** `GET /api/v1/aircraft-registry/status` (`main.py:2438-2446`) returns `{"error": str(e)}` with a 500 to T0 callers. The C-26 fix removed this pattern elsewhere.
14. **Code outside the signed manifest runs in a gate container.** `corporatetraveldc-execstandard-verifier` executes `/opt/es-verify.py`, which is bind-mounted from `executivestandard-website/verifier/verify.py`. That code is outside this repo's signed manifest and is not wrapped in `verified-exec.sh`, yet it is the auth gate for `members.` and `invite.`.
15. **Gateway routes have no in-app Host check.** `/oauth/*` and `/mcp/*` are also served on `dispatch.example.com` (behind CF Access) and on `100.x.x.x:8000`. Only the `agents.` vhost is path-restricted. This is not a defect by itself, because the gateway authenticates with its own bearer tokens. It does widen the surface, and the console shows the alternative (`CONSOLE_HOSTS`).
16. **Docs and comments drift.**
    - The `src/common/db_backend.py` module docstring (lines 40-46, 83) and the `ref_conn()` docstring still say the reference tables live in SQLite forever; 0052–0054 moved them.
    - `src/common/pg_schema/0057_demo.sql`'s header says "Migration 0056".
    - `scripts/second-brain-search.sh`'s header says "FTS5 index".
    - `scripts/weekly-external-image-update.sh` survives, but its timer now runs `stack-refresh.sh --weekly`.
17. **Third-party listeners on all interfaces:** `openwebui` *:3000, `ntfy` *:2586, `fr24feed` *:8754, `acarsrouter` *:9080/*:15555, `ultrafeeder` *:30005, Pi-hole web 0.0.0.0:8091. They are LAN-reachable unless firewalld blocks them (not verified).
