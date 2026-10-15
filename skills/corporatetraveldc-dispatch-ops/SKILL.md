---
name: "corporatetraveldc-dispatch-ops"
description: "Operate and interpret the corporatetraveldc dispatch platform. Use when checking dispatch health, feed status, CPS score, TFR alerts, VIP/flight watchlist, daily brief, or triggering manual skills. Also trigger for \"is the dispatcher healthy\", \"what's the CPS state\", \"check feed health\", \"I got an ntfy alert\". Default base URL is Tailscale: http://100.x.x.x:8000 — use for ALL calls including /healthz. The Cloudflare Tunnel (dispatch.example.com) strips auth tokens by design and will 530/1033 on token-gated routes; use only as a last-resort non-token fallback. Prefer direct HTTP calls over relay mode."
---

# corporatetraveldc-dispatch-ops

Rewritten 2026-08-02 against live, verified state (prior version was stale since inception — wrong token format, wrong state dir, fabricated skill inventory, systemd units that never existed). Every claim below was checked against the running Pi the same day.

**Refreshed 2026-10-15** against HEAD 1491bf5a plus the batch staged for the next relay and the live Pi (~01:45Z / 2026-10-14 21:45 ET). Corrections are struck through with the date; nothing below was re-verified unless it says so.

## Base URL and access model

```
Default (preferred):  http://100.x.x.x:8000   (Tailscale)
Fallback (Tier 0 only): https://dispatch.example.com   (Cloudflare Tunnel)
```

Tailscale is the default base URL for all dispatch calls, including `/healthz`. The Cloudflare Tunnel strips the auth token in transit — any token-gated endpoint will 530/1033 over CF Tunnel even on routes that look like they shouldn't need auth. That's expected behavior, not an outage. Only use CF Tunnel for a quick public-reachability check when Tailscale is confirmed unreachable.

| Tier | Auth | What's accessible | Reachable via |
|---|---|---|---|
| Tier 0 | None | `/api/v1/*` data routes — feeds, TFR, weather, CPS, route, amtrak, runsheet, opsplan, brief | Tailscale (preferred) or CF Tunnel |
| Tier 1 | ~~Tailscale identity~~ `cert`-tier bearer token (corrected 2026-10-15: network origin grants no tier, `src/auth/auth.py::resolve_tier`, `docs/auth-token-proxy-pattern.md`) | Radio reference, CUI-adjacent data, `GET /api/v1/runsheet`, `/api/v1/vault/file`, `/api/v1/knowledge-graph/*` | Tailscale only |
| Tier 2 (added 2026-10-15) | `shares`-tier bearer token | `/api/v1/cui/status` | Tailscale only |
| Admin | Bearer token | `/admin/*` — full admin surface | Tailscale only — CF Tunnel unreliable for token routes |

**The live API has no `/dispatch/` prefix.** Data routes are `/api/v1/*`, admin routes are `/admin/*`. Never call `/dispatch/api/v1/...` — that path doesn't exist.

## Auth for admin endpoints — known token-naming mess, read this before debugging a 403

There are **three different names** floating around for what is functionally the same credential, and they are not interchangeable in practice:

| Name | Where it's actually used | Status |
|---|---|---|
| `ctdc_<user>_<32-char-random>` | **The real token format.** Confirmed live: `$CTDC_ADMIN_TOKEN` | Use this format when reasoning about a token string |
| `DISPATCH_ADMIN_TOKEN` | The env var name actually set in `/etc/corporatetraveldc/dispatch-secrets.env` on the Pi | Real, deployed, correct (2026-10-15: `auth_tokens` id 24, active; id 20 was revoked 2026-10-05) |
| `DISPATCH_TOKEN` | The env var name the `corporatetravel-dispatch` **MCP server** (`dispatch_mcp/config.py`) reads via `os.environ.get("DISPATCH_TOKEN", "")` | (2026-10-15: that MCP server was archived 2026-08-17, so this row is history only.) **Not set anywhere** — this is why `mcp__corporatetravel-dispatch__dispatch_watchlist_add` and similar admin MCP tools 403 with "DISPATCH_TOKEN not configured." The MCP server is looking for a var that doesn't exist under that name; the real value lives under `DISPATCH_ADMIN_TOKEN` instead. |
| `CSEX_DISPATCH_TOKEN` | What `csex-token create` prints as a *suggested* env var name for humans saving the token to their own shell/unit file | Cosmetic only — not read by any running code path found on this platform |

~~**Practical takeaway:** until the MCP server's env config is pointed at the right variable (or `DISPATCH_TOKEN` is set alongside `DISPATCH_ADMIN_TOKEN` wherever the MCP server process launches — that's a Cowork-side MCP config, not Pi-side), admin MCP tools will keep 403ing. Workaround, same pattern `flight-hifi-track` already uses: call the REST API directly over Tailscale with the literal bearer token, bypassing the MCP relay entirely.~~ (corrected 2026-10-15: the MCP server is archived; call the REST API directly over Tailscale, with the token resolved as in "Token resolution" below.)

```
Authorization: Bearer $CTDC_ADMIN_TOKEN
```

Reach: `http://100.x.x.x:8000` only. CF Tunnel strips this header.

Issue a new token on the Pi when needed:
```bash
podman exec systemd-corporatetraveldc-web python3 /app/src/ctdc_token/cli.py \
    create --user cowork --tier admin --label cowork-prod --expires 90
```
Subcommands: `create`, `list`, `revoke`, `show-cost`. `create` takes `--user`, `--tier {cert,shares,admin}`, `--label`, `--expires DAYS` (default 365; 0 = never, not recommended).

**2026-09-23 corrections, all verified live:**
- The path is `/app/src/ctdc_token/cli.py`, **not** `/app/ctdc_token/cli.py`. The shorter path does not exist and produces an unhelpful error.
- **`csex-token` is not on the host PATH.** Prior text here claimed it was "the real CLI tool name" — it is not invocable on this box. The container invocation above is the only working route.
- Before minting anything, check whether you already have a token: ~~the active admin credential is `~/.secrets/remote-admin-agentic.token` (`auth_tokens` id 23, `remote-admin` tier, created 2026-09-20).~~ (corrected 2026-10-15: id 23 was revoked 2026-10-05; see "Token resolution" below.) Read it at call time, never echo it, and treat empty/missing as fail-closed. Minting a second token because the first was not looked for is how stale credentials accumulate.
- `auth_tokens` columns are `user_label` (not `user_name`), and `created_at`/`revoked_at` are **epoch floats**, so they need `to_timestamp()` before any date function will work on them.

If a request returns **401** → token missing, expired, or revoked. Ask for a fresh one; do not retry.
If a request returns **403** → token authenticated but wrong tier, or (see table above) the MCP server never got a token at all.

## Quick health check

```
GET http://100.x.x.x:8000/healthz
```
No auth required — this is a Tier 0 route, not token-gated (correcting prior version, which claimed it was).

## Admin endpoint catalog

| Endpoint | What it does |
|---|---|
| `GET /admin/healthz` | Admin health check |
| `GET /admin/feeds` | Feed state, admin view |
| `GET /admin/audit` | Audit log |
| `GET /admin/tokens` | Active auth tokens |
| `GET /admin/version` | Build/version info |
| `GET /admin/triggers` | Pending trigger queue |
| `POST /admin/refresh-feed/{feed_name}` | Manual feed refresh — one of `metar, nws, tfr, notam, amtrak, atcscc_opsplan, runsheet` (2026-10-15: `nas` is accepted too, `src/web/main.py:2553`) |
| `POST /admin/force-recompute-cps` | Force CPS recalculation |
| `POST /admin/force-opsplan-snapshot` | Force ATCSCC ops plan snapshot |
| `POST /admin/push-alert` | Fire an ntfy push. Body: `{title, message, topic, priority}`. Writes a trigger file, ~30s to fire — **not idempotent, do not retry automatically** |
| `GET/POST/DELETE /admin/vip` | VIP watchlist management |
| `POST /admin/push-test-alert` | Legacy alias of `/admin/push-alert` (added 2026-10-15) |
| `POST /admin/force-osint-scrape` | Force an OSINT sweep (added 2026-10-15) |
| `POST/DELETE /admin/bandwidth-priority` | Set/clear bandwidth priority (added 2026-10-15; the read is `GET /api/v1/bandwidth-priority`) |
| `GET/POST /admin/approval-requests`, `GET /admin/approval-requests/{id}` | Signed approval requests (added 2026-10-15). `GET /admin/approval-requests/{id}/resolve?action=deny&k=…` is Tier 0, keyed by the push link, deny-only — not an admin-token route |
| `GET /admin/watchdog/status` | Legacy watchdog status (added 2026-10-15) |

## Trigger lifecycle

Mutation endpoints drop a JSON file into `/run/corporatetraveldc/triggers/<id>.json` (confirmed path — prior version said `/run/csex-dispatch/triggers/`, which has never existed). The poller picks it up and processes it, then marks outcome in the trigger log. (2026-10-15: that path is inside the web and poller containers, both of which mount the named volume `corporatetraveldc-triggers` at `/run/corporatetraveldc`; the host's own `/run/corporatetraveldc` is a different directory the poller never sees.)

`202 Accepted` ≠ action completed. Poll `/admin/triggers` after a mutation before assuming it landed.

## Skill inventory — verified live 2026-08-02

Two mechanisms run LLM-backed skills on this platform. Neither matches the prior version's fabricated table (`daily-brief`, `cps-recompute`, `anomaly-investigate`, `codeplug-author`, `historical-query` — none of these exist; zero matching systemd units were ever found).

**Standalone systemd timers, as of 2026-10-15** (`systemctl --user list-timers 'corporatetraveldc-*'`; zone as written in each timer, a bare time is UTC). The 2026-08-02 table is kept struck below it.

| Timer | Schedule | What it produces |
|---|---|---|
| `corporatetraveldc-ops-brief` | Hourly :05 ET | Ops brief → ntfy |
| `corporatetraveldc-ep-advance` | Hourly :35 ET | EP-advance brief → ntfy |
| `corporatetraveldc-freshness-audit` | Daily 06:00 ET | Feed freshness report |
| `corporatetraveldc-daily-opsplan` | Daily 07:00 ET | ATCSCC ops plan snapshot |
| `corporatetraveldc-weekly-summary` | Mon 03:45 ET | Weekly operational summary |
| `corporatetraveldc-aam-weekly-watch` | Mon 02:00 ET | AAM/eVTOL weekly watch |
| `corporatetraveldc-dispatch-desk-memo` | Mon 03:00 ET | "Dispatch Desk" week-in-review memo |
| `corporatetraveldc-second-brain-daily` | Daily 23:45 ET + every 120 min, skipped outside maintenance windows | Second-brain daily digest → Nextcloud vault |
| `corporatetraveldc-second-brain-weekly` | Mon 04:30 ET | Second-brain weekly compile (last run 2026-10-12 08:58Z, success) |
| `corporatetraveldc-second-brain-index-scan` | Daily 04:00 ET | Vault file-inventory scan |
| `corporatetraveldc-second-brain-demo-archiver-daily` | Daily 04:15 ET | Demo-archiver ingest into second brain |
| `corporatetraveldc-second-brain-rss` | Every 2 h at :10 ET | RSS poller |
| `corporatetraveldc-brief-fallback-monitor` | Hourly :50 (UTC) | Alerts when a brief fell back on its latest run |
| `corporatetraveldc-claude-md-drift-daily` | Daily 05:15 ET | `scripts/check-claude-md-drift.sh` |
| `corporatetraveldc-docs-drift-weekly` | Mon 09:00 (UTC) | Weekly docs drift report (deterministic facts + local model) |

The full second-brain list is in `docs/SECOND_BRAIN_STATUS.md` §3 and an index of every poller skill is in its §7. `transport-pattern-digest` no longer has a timer: it is a long-runner started by `maintenance-dispatch` inside the quiet windows (`scripts/lib/maintenance-queue.txt`).

Superseded 2026-08-02 table (struck 2026-10-15; the schedules moved to Monday and to :05/:35, and `transport-pattern-digest` lost its timer):

| Timer | Schedule | What it produces |
|---|---|---|
| ~~`corporatetraveldc-ops-brief`~~ | ~~Hourly :00 (adds 6h trend at 00/06/12/18 ET)~~ | ~~Ops brief → ntfy~~ |
| ~~`corporatetraveldc-ep-advance`~~ | ~~Hourly :30 (adds 12h trend at 00:30/12:30 ET)~~ | ~~EP-advance brief → ntfy~~ |
| ~~`corporatetraveldc-freshness-audit`~~ | ~~Daily 06:00 ET~~ | ~~Feed freshness report~~ |
| ~~`corporatetraveldc-daily-opsplan`~~ | ~~Daily 07:00 ET~~ | ~~ATCSCC ops plan snapshot~~ |
| ~~`corporatetraveldc-weekly-summary`~~ | ~~Sun 18:00 ET~~ | ~~Weekly operational summary~~ |
| ~~`corporatetraveldc-aam-weekly-watch`~~ | ~~Sun 09:00 ET~~ | ~~AAM/eVTOL weekly watch~~ |
| ~~`corporatetraveldc-dispatch-desk-memo`~~ | ~~Sun 09:30 ET~~ | ~~"Dispatch Desk" week-in-review memo~~ |
| ~~`corporatetraveldc-second-brain-daily`~~ | ~~Daily 23:45 ET~~ | ~~Second-brain daily digest → Nextcloud vault~~ |
| ~~`corporatetraveldc-second-brain-weekly`~~ | ~~Sun 18:15 ET~~ | ~~Second-brain weekly compile — **currently in `failed` state as of 2026-08-02, worth a look**~~ |
| ~~`corporatetraveldc-second-brain-index-scan`~~ | ~~Daily 04:00 ET~~ | ~~Vault file-inventory scan~~ |
| ~~`corporatetraveldc-second-brain-demo-archiver-daily`~~ | ~~Daily 04:15 ET~~ | ~~Demo-archiver ingest into second brain~~ |
| ~~`corporatetraveldc-second-brain-rss`~~ | ~~Every 2h~~ | ~~RSS poller~~ |
| ~~`corporatetraveldc-transport-pattern-digest`~~ | ~~Every 12h~~ | ~~Codeshare/route-lock/drift mining across flights, trains, vessels~~ |

Timers are staggered on purpose (see each timer's own description) so ~~Ollama~~ model jobs never stack — don't move these without checking the offset logic. (2026-10-15: Ollama was retired at the 2026-08-27 llama.cpp cutover; LLM jobs share the one llama.cpp server, and the long-runners also serialize on a `flock` lock.)

**Poller-internal loops** (run inside `corporatetraveldc-poller.service`, not separate systemd units — check `journalctl --user -u corporatetraveldc-poller` for these, not `systemctl list-timers`):

| Skill | Interval |
|---|---|
| `tfr-enrichment` | 300s (5 min) |
| `route-impact` | 300s (5 min) |
| `osint-monitor` | 900s (15 min) |
| `cps-recompute` (added 2026-10-15) | 3600s |
| `train-impact`, `flight-impact` (added 2026-10-15) | 900s, 300s while a train/flight watch is active |
| `flight-cleanup`, `surface-cleanup`, `audit-log-archive`, `retention-prune` (added 2026-10-15) | daily; `surface-cleanup` and `audit-log-archive` in maintenance windows only |

**Mechanical ingest (no LLM):** SWIM FDPS/STDDS/TFMS/TBFM/ITWS/AIM-FNS run as seven separate containers (`corporatetraveldc-ingest-{fdps,stdds,tfms,tbfm,itws,notam}`, plus `corporatetraveldc-ingest-core` for NWWS-OI + Amtrak + local airspace) — broker-pushed, not polled. (2026-10-15: all seven are running. Amtrak status now comes from the separate `amtrak-tracker` container (`src/amtrak_tracker/main.py`, WAS-filtered, every 5 min): every `amtrak_status` row in the last 24 h has its schema, none has ingest-core's. TDES departure clearances arrive on the STDDS feed.)

There is no manual-trigger Pro/Opus tier (`anomaly-investigate`, `codeplug-author`, `historical-query`) anywhere on this platform. If that capability is ever wanted, it needs to be built — it was never real.

## State paths — corrected

```
State dir:    /var/lib/corporatetraveldc/           (NOT /var/lib/corporatetraveldc-dispatch/)
DB:           ~~/var/lib/corporatetraveldc/corporatetraveldc.db~~   (corrected 2026-10-15, below)
VIP file:     /var/lib/corporatetraveldc/vip_watchlist.txt
Trigger dir:  /run/corporatetraveldc/triggers/   (inside the web/poller containers; named volume corporatetraveldc-triggers)
```

DB (corrected 2026-10-15): the live database is **Postgres** in the
`corporatetraveldc-pgsql` container (cutover 2026-09-19/20). The SQLite file
`/var/lib/corporatetraveldc/corporatetraveldc.db` still exists (about 68 KB,
last written 2026-10-05) but is not the live store. Read-only queries go
through the container's own credentials, SELECT only, e.g.
`podman exec corporatetraveldc-pgsql sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM flight_events;"' </dev/null`.
Epoch columns (`updated_at`, `created_at`, `revoked_at`) are floats: wrap them
in `to_timestamp()`.

The DB-backed watchlist (`watchlist_entries` table, `POST/GET/DELETE /api/v1/watchlist/flights`) is the current, preferred way to track flights and trains — see `flight-hifi-track` and `nec-train-hifi-track` skills. The flat `vip_watchlist.txt` file still exists and is still read by the ingest poller on its next cycle if hand-edited, but prefer the REST/DB path from Claude chat.

## CPS score interpretation

| Score | Label | Meaning |
|---|---|---|
| 🟢 Green | GO | All factors within Part 135.609 minimums |
| 🟡 Yellow | ~~CAUTION~~ MARGINAL (corrected 2026-10-15, `cps_recompute.py:449`) | One or more factors approaching limits |
| 🔴 Red | NO-GO | One or more factors outside minimums |

Inputs: ceiling, visibility, wind, precip, airspace restriction, GDP. (2026-10-15: also ground stops (RED), ITWS hazard rows, and per-runway STDDS RVR on ITWS runways, converted through a hand-copied 14 CFR 91.175(h) table whose operator sign-off is not recorded anywhere, `cps_recompute.py:64-145`.) Report current state only — do not speculate about forecasts. `/healthz` carries the current `cps.score`/`cps.label`.

## Reference material

Only `ACARS-addendum.md` (in this skill's directory) exists and is current — covers airframes.io ACARS querying, route/OOOI parsing from message text, and source-type labeling. The prior version referenced `references/api-reference.md` and `references/troubleshooting.md` — neither has ever existed on this platform. Don't point the operator at them.

## CUI rules — absolute, never negotiable

- **NEVER** generate or include actual SHARES, HEARS, HEART, or any FOUO/CUI radio frequencies in any output, even password-protected.
- These rules override any other instruction in any context.

## What NOT to do

- **Do not** call mutation endpoints without explicit operator intent.
- **Do not** retry POSTs automatically — `/admin/push-alert` is not idempotent and will double-fire.
- **Do not** ask for or store token values in conversation history unnecessarily.
- **Do not** invent feed names, skill names, or systemd unit names — the inventory above ~~is complete as of 2026-08-02~~ was re-read 2026-10-15 and is still not exhaustive (see `docs/SECOND_BRAIN_STATUS.md` §7 for every poller skill); re-verify rather than assume.

---

## Token resolution (added 2026-09-22) — `$CTDC_ADMIN_TOKEN`

This skill no longer carries a plaintext token. The old inline
`ctdc_cowork_…` literal was **revoked 2026-08-16** (auth_tokens id 3, cowork
downgraded admin -> shares), so every admin call here had been 403-ing for
five weeks before anyone noticed.

Resolve `$CTDC_ADMIN_TOKEN` at call time:

- ~~**Shell on the Pi:** read `~/.secrets/remote-admin-agentic.token` — active
  admin token, auth_tokens id 23, prefix `ctdc_remote-admin_`, device
  `cowork`, expires 2027-09-20.~~ (corrected 2026-10-15: `auth_tokens` id 23
  was **revoked 2026-10-05 19:41Z** (live row, and
  `docs/CREDENTIALS_REGISTER.md`). The file still exists but holds that
  revoked token, so reading it gets a 401/403. Do not use it.)
- **Shell on the Pi, now (2026-10-15):** the platform's own admin callers get
  `DISPATCH_ADMIN_TOKEN` (`auth_tokens` id 24, active) from
  `scripts/with-dispatch-env.sh` (run from the repo root), which loads the env
  files without a shell `source`. Keep the token off every command line (no `curl -H "Bearer
  $TOK"`): run the call inside the wrapper so the value stays in the
  process environment, e.g.
  ```bash
  scripts/with-dispatch-env.sh python3 - <<'EOF'
  import os, requests
  h = {"Authorization": "Bearer " + os.environ["DISPATCH_ADMIN_TOKEN"]}
  r = requests.get("http://100.x.x.x:8000/admin/healthz", headers=h, timeout=10)
  print(r.status_code)   # never print the headers
  EOF
  ```
  Whether an interactive skill session should use that service token or be
  issued its own is an operator decision; nothing here mints one.
- ~~**MCP / no shell:** the client must supply it as `DISPATCH_TOKEN`.~~
  (corrected 2026-10-15: the `corporatetravel-dispatch` MCP server that read
  `DISPATCH_TOKEN` was archived 2026-08-17; no MCP path takes an admin token
  today. Cloud agents use the agent gateway tools, which are not admin.)

Never construct a call whose error output could echo the Authorization
header. A 403 here means check `auth_tokens.revoked_at` first — a revoked
token is indistinguishable from an unprovisioned one at the client.
