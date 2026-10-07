---
name: "corporatetraveldc-dispatch-ops"
description: "Operate and interpret the corporatetraveldc dispatch platform. Use when checking dispatch health, feed status, CPS score, TFR alerts, VIP/flight watchlist, daily brief, or triggering manual skills. Also trigger for \"is the dispatcher healthy\", \"what's the CPS state\", \"check feed health\", \"I got an ntfy alert\". Default base URL is Tailscale: http://100.x.x.x:8000 — use for ALL calls including /healthz. The Cloudflare Tunnel (dispatch.example.com) strips auth tokens by design and will 530/1033 on token-gated routes; use only as a last-resort non-token fallback. Prefer direct HTTP calls over relay mode."
---

# corporatetraveldc-dispatch-ops

Rewritten 2026-08-02 against live, verified state (prior version was stale since inception — wrong token format, wrong state dir, fabricated skill inventory, systemd units that never existed). Every claim below was checked against the running Pi the same day.

## Base URL and access model

```
Default (preferred):  http://100.x.x.x:8000   (Tailscale)
Fallback (Tier 0 only): https://dispatch.example.com   (Cloudflare Tunnel)
```

Tailscale is the default base URL for all dispatch calls, including `/healthz`. The Cloudflare Tunnel strips the auth token in transit — any token-gated endpoint will 530/1033 over CF Tunnel even on routes that look like they shouldn't need auth. That's expected behavior, not an outage. Only use CF Tunnel for a quick public-reachability check when Tailscale is confirmed unreachable.

| Tier | Auth | What's accessible | Reachable via |
|---|---|---|---|
| Tier 0 | None | `/api/v1/*` data routes — feeds, TFR, weather, CPS, route, amtrak, runsheet, opsplan, brief | Tailscale (preferred) or CF Tunnel |
| Tier 1 | Tailscale identity | Radio reference, CUI-adjacent data | Tailscale only |
| Admin | Bearer token | `/admin/*` — full admin surface | Tailscale only — CF Tunnel unreliable for token routes |

**The live API has no `/dispatch/` prefix.** Data routes are `/api/v1/*`, admin routes are `/admin/*`. Never call `/dispatch/api/v1/...` — that path doesn't exist.

## Auth for admin endpoints — known token-naming mess, read this before debugging a 403

There are **three different names** floating around for what is functionally the same credential, and they are not interchangeable in practice:

| Name | Where it's actually used | Status |
|---|---|---|
| `ctdc_<user>_<32-char-random>` | **The real token format.** Confirmed live: `$CTDC_ADMIN_TOKEN` | Use this format when reasoning about a token string |
| `DISPATCH_ADMIN_TOKEN` | The env var name actually set in `/etc/corporatetraveldc/dispatch-secrets.env` on the Pi | Real, deployed, correct |
| `DISPATCH_TOKEN` | The env var name the `corporatetravel-dispatch` **MCP server** (`dispatch_mcp/config.py`) reads via `os.environ.get("DISPATCH_TOKEN", "")` | **Not set anywhere** — this is why `mcp__corporatetravel-dispatch__dispatch_watchlist_add` and similar admin MCP tools 403 with "DISPATCH_TOKEN not configured." The MCP server is looking for a var that doesn't exist under that name; the real value lives under `DISPATCH_ADMIN_TOKEN` instead. |
| `CSEX_DISPATCH_TOKEN` | What `csex-token create` prints as a *suggested* env var name for humans saving the token to their own shell/unit file | Cosmetic only — not read by any running code path found on this platform |

**Practical takeaway:** until the MCP server's env config is pointed at the right variable (or `DISPATCH_TOKEN` is set alongside `DISPATCH_ADMIN_TOKEN` wherever the MCP server process launches — that's a Cowork-side MCP config, not Pi-side), admin MCP tools will keep 403ing. Workaround, same pattern `flight-hifi-track` already uses: call the REST API directly over Tailscale with the literal bearer token, bypassing the MCP relay entirely.

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
- Before minting anything, check whether you already have a token: the active admin credential is `~/.secrets/remote-admin-agentic.token` (`auth_tokens` id 23, `remote-admin` tier, created 2026-09-20). Read it at call time, never echo it, and treat empty/missing as fail-closed. Minting a second token because the first was not looked for is how stale credentials accumulate.
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
| `POST /admin/refresh-feed/{feed_name}` | Manual feed refresh — one of `metar, nws, tfr, notam, amtrak, atcscc_opsplan, runsheet` |
| `POST /admin/force-recompute-cps` | Force CPS recalculation |
| `POST /admin/force-opsplan-snapshot` | Force ATCSCC ops plan snapshot |
| `POST /admin/push-alert` | Fire an ntfy push. Body: `{title, message, topic, priority}`. Writes a trigger file, ~30s to fire — **not idempotent, do not retry automatically** |
| `GET/POST/DELETE /admin/vip` | VIP watchlist management |

## Trigger lifecycle

Mutation endpoints drop a JSON file into `/run/corporatetraveldc/triggers/<id>.json` (confirmed path — prior version said `/run/csex-dispatch/triggers/`, which has never existed). The poller picks it up and processes it, then marks outcome in the trigger log.

`202 Accepted` ≠ action completed. Poll `/admin/triggers` after a mutation before assuming it landed.

## Skill inventory — verified live 2026-08-02

Two mechanisms run LLM-backed skills on this platform. Neither matches the prior version's fabricated table (`daily-brief`, `cps-recompute`, `anomaly-investigate`, `codeplug-author`, `historical-query` — none of these exist; zero matching systemd units were ever found).

**Standalone systemd timers** (`systemctl --user list-timers 'corporatetraveldc-*'`):

| Timer | Schedule | What it produces |
|---|---|---|
| `corporatetraveldc-ops-brief` | Hourly :00 (adds 6h trend at 00/06/12/18 ET) | Ops brief → ntfy |
| `corporatetraveldc-ep-advance` | Hourly :30 (adds 12h trend at 00:30/12:30 ET) | EP-advance brief → ntfy |
| `corporatetraveldc-freshness-audit` | Daily 06:00 ET | Feed freshness report |
| `corporatetraveldc-daily-opsplan` | Daily 07:00 ET | ATCSCC ops plan snapshot |
| `corporatetraveldc-weekly-summary` | Sun 18:00 ET | Weekly operational summary |
| `corporatetraveldc-aam-weekly-watch` | Sun 09:00 ET | AAM/eVTOL weekly watch |
| `corporatetraveldc-dispatch-desk-memo` | Sun 09:30 ET | "Dispatch Desk" week-in-review memo |
| `corporatetraveldc-second-brain-daily` | Daily 23:45 ET | Second-brain daily digest → Nextcloud vault |
| `corporatetraveldc-second-brain-weekly` | Sun 18:15 ET | Second-brain weekly compile — **currently in `failed` state as of 2026-08-02, worth a look** |
| `corporatetraveldc-second-brain-index-scan` | Daily 04:00 ET | Vault file-inventory scan |
| `corporatetraveldc-second-brain-demo-archiver-daily` | Daily 04:15 ET | Demo-archiver ingest into second brain |
| `corporatetraveldc-second-brain-rss` | Every 2h | RSS poller |
| `corporatetraveldc-transport-pattern-digest` | Every 12h | Codeshare/route-lock/drift mining across flights, trains, vessels |

Timers are staggered on purpose (see each timer's own description) so Ollama jobs never stack — don't move these without checking the offset logic.

**Poller-internal loops** (run inside `corporatetraveldc-poller.service`, not separate systemd units — check `journalctl --user -u corporatetraveldc-poller` for these, not `systemctl list-timers`):

| Skill | Interval |
|---|---|
| `tfr-enrichment` | 300s (5 min) |
| `route-impact` | 300s (5 min) |
| `osint-monitor` | 900s (15 min) |

**Mechanical ingest (no LLM):** SWIM FDPS/STDDS/TFMS/TBFM/ITWS/AIM-FNS run as seven separate containers (`corporatetraveldc-ingest-{fdps,stdds,tfms,tbfm,itws,notam}`, plus `corporatetraveldc-ingest-core` for NWWS-OI + Amtrak + local airspace) — broker-pushed, not polled.

There is no manual-trigger Pro/Opus tier (`anomaly-investigate`, `codeplug-author`, `historical-query`) anywhere on this platform. If that capability is ever wanted, it needs to be built — it was never real.

## State paths — corrected

```
State dir:    /var/lib/corporatetraveldc/           (NOT /var/lib/corporatetraveldc-dispatch/)
DB:           /var/lib/corporatetraveldc/corporatetraveldc.db
VIP file:     /var/lib/corporatetraveldc/vip_watchlist.txt
Trigger dir:  /run/corporatetraveldc/triggers/
```

The DB-backed watchlist (`watchlist_entries` table, `POST/GET/DELETE /api/v1/watchlist/flights`) is the current, preferred way to track flights and trains — see `flight-hifi-track` and `nec-train-hifi-track` skills. The flat `vip_watchlist.txt` file still exists and is still read by the ingest poller on its next cycle if hand-edited, but prefer the REST/DB path from Claude chat.

## CPS score interpretation

| Score | Label | Meaning |
|---|---|---|
| 🟢 Green | GO | All factors within Part 135.609 minimums |
| 🟡 Yellow | CAUTION | One or more factors approaching limits |
| 🔴 Red | NO-GO | One or more factors outside minimums |

Inputs: ceiling, visibility, wind, precip, airspace restriction, GDP. Report current state only — do not speculate about forecasts.

## Reference material

Only `ACARS-addendum.md` (in this skill's directory) exists and is current — covers airframes.io ACARS querying, route/OOOI parsing from message text, and source-type labeling. The prior version referenced `references/api-reference.md` and `references/troubleshooting.md` — neither has ever existed on this platform. Don't point the operator at them.

## CUI rules — absolute, never negotiable

- **NEVER** generate or include actual SHARES, HEARS, HEART, or any FOUO/CUI radio frequencies in any output, even password-protected.
- These rules override any other instruction in any context.

## What NOT to do

- **Do not** call mutation endpoints without explicit operator intent.
- **Do not** retry POSTs automatically — `/admin/push-alert` is not idempotent and will double-fire.
- **Do not** ask for or store token values in conversation history unnecessarily.
- **Do not** invent feed names, skill names, or systemd unit names — the inventory above is complete as of 2026-08-02 and should be re-verified periodically rather than assumed permanent.

---

## Token resolution (added 2026-09-22) — `$CTDC_ADMIN_TOKEN`

This skill no longer carries a plaintext token. The old inline
`ctdc_cowork_…` literal was **revoked 2026-08-16** (auth_tokens id 3, cowork
downgraded admin -> shares), so every admin call here had been 403-ing for
five weeks before anyone noticed.

Resolve `$CTDC_ADMIN_TOKEN` at call time:

- **Shell on the Pi:** read `~/.secrets/remote-admin-agentic.token` — active
  admin token, auth_tokens id 23, prefix `ctdc_remote-admin_`, device
  `cowork`, expires 2027-09-20. Read it inside the command, never echo it:
  ```bash
  TOK="$(grep -oE 'ctdc_[A-Za-z0-9._-]+' ~/.secrets/remote-admin-agentic.token | head -1)"
  ```
- **MCP / no shell:** the client must supply it as `DISPATCH_TOKEN`.

Never construct a call whose error output could echo the Authorization
header. A 403 here means check `auth_tokens.revoked_at` first — a revoked
token is indistinguishable from an unprovisioned one at the client.
