# Supervisory Re-Verification of Stage 1 Ground-Up Audit — CTDI

**Date:** 2026-08-24
**Role:** Independent Stage-2 supervisory/adversarial check on the Stage 1
"Ground-Up Independent Audit & Adversarial Pentest"
(`GROUND_UP_AUDIT_2026-08-24.md`).
**Input scope:** Stage 1's written report only. No other prior
research/pentest doc was opened. Every judgment below is from my own read of
live source and my own non-destructive live probes.
**Safety discipline (stricter than Stage 1's):** zero live writes/deletes.
State-changing verbs verified by source, or by malformed-body `422`
differential that cannot complete a mutation. I did **not** re-send the
`PUT /api/v1/config` probe that Stage 1 disclosed as an accidental mutation.

---

## Verdict summary

| Stage 1 claim | My finding |
|---|---|
| P-1 — 5 unauth `/api/v1/sectors/*` POST mutators (**High**) | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| P-2 — runner `PUT /api/v1/config` unauth blind overwrite (Med) | **CONFIRMED-BY-INDEPENDENT-RECHECK** (source-only; blast radius verified) |
| P-3 — `mt_widget_key` served ungated (Med/Low) | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| P-4 — public board leaks internal detail (Low/Info) | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| P-5 — 6 non-expiring tokens, 3 admin incl. test token (Low) | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| P-6 — unauth approval `/resolve` = passwordless sudo (by-design) | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| P-7 — `POST /api/v1/watchlist` is a "live no-op / dead table" (Low) | **COULD-NOT-REPRODUCE** (partial — see below) |
| P-8 — scope-blind board key also unlocks vault reads (needs review) | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| §3 — runner-demo healthy, not crash-looping | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| §2 — health/manifest/scale/token/failed-unit facts | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| §4.2 — positive controls (auth-first, traversal, redaction) | **CONFIRMED-BY-INDEPENDENT-RECHECK** |
| External public reachability of P-1 endpoints | **NEEDS-HUMAN-REVIEW** (Stage 1 self-flagged; I also cannot test non-destructively) |

**Bottom line:** every HIGH and security-relevant finding holds up under
independent recheck. One LOW finding (P-7) is factually overstated in its
interpretation — the table it calls "dead/unconsumed" is in fact consumed by
a live pusher path. The accidental mutation (P-2) is an isolated methodology
lapse, not a pattern; its disclosure is accurate. Report is trustworthy to
move forward on, with the P-7 wording corrected.

---

## Detailed re-verification

### P-1 — Five unauthenticated `/api/v1/sectors/*` POST mutators — CONFIRMED (High)

This is the top finding and it is correct.

- **Source (`src/web/routes/sectors.py:54-118`):** all five POST handlers
  (`silence_sector`, `silence_feed`, `throttle_topic`, `enable_topic`,
  `sanitize_topic`) declare only a path param + Pydantic body. No
  `Depends(require_admin/require_tier)` on any of them.
- **Router registration (`src/web/main.py:88`):**
  `app.include_router(sectors_router)` — no `dependencies=` argument, so
  there is no router-level auth backstop either. The gap is real end-to-end.
- **My own live differential (non-mutating; malformed `{}` body cannot
  reach the mutation call):**
  ```
  POST /api/v1/sectors/topic/AUDIT-PROBE-NONEXISTENT/throttle  (no token)      -> 422
  POST  same + X-CTDI-Public: 1                                                -> 422
  POST /api/v1/sectors/topic/AUDIT-PROBE-NONEXISTENT/enabled   (no token)      -> 422
  GET  /api/v1/sectors/topic/dispatch                          (no token)      -> 200
  POST /admin/bandwidth-priority  (control)                    (no token)      -> 403
  ```
  `422` (body validation) on both the plain and simulated-public paths proves
  the request cleared all dependency resolution with no auth gate; the admin
  control correctly `403`s. Matches Stage 1 exactly. The
  identifier-**sanitize** toggle being unauthenticated is the sharpest edge —
  an anonymous caller can un-mask real tail numbers on a topic, or silence a
  sector's alerts, with no credential.

### P-2 — Runner `PUT /api/v1/config` unauth blind overwrite — CONFIRMED (Med), source-only

Verified entirely by source, per the stricter constraint; I did **not** send
any live PUT.

- **Handler (`src/runner/main.py:1771-1782`):** `put_user_config` has no auth
  dependency and no `_is_trusted()` check. It does
  `body = await request.json()` then `json.dump(body, f)` into `_CONFIG_PATH`
  — a full overwrite, no merge, no schema validation. Stage 1's description
  is exact.
- **Blast-radius check (the constraint asked me to confirm this
  independently):** `_CONFIG_PATH` (`src/runner/main.py:1675-1676`) is
  `os.path.join(STATE_DIR, "runner-layer-config.json")` — a single named
  file. Its only reader is `get_user_config` (`:1759-1769`), which the SPA
  uses for saved map-layer preferences. It is **not** the chat DB
  (`CHAT_DB_PATH` is a separate path), not dispatch data, not auth/token
  state, not `dispatch.env`/secrets. Stage 1's "scoped to UI-preferences
  only, recreated on next SPA save" claim is accurate — the blast radius is
  genuinely one client-side convenience file. The finding itself (an unauth
  arbitrary-body overwrite of persisted config) stands.

### P-3 — `mt_widget_key` returned ungated — CONFIRMED (Med/Low)

- **Source (`src/runner/main.py:1724-1756`):** `frontend_config` trust-gates
  only the coordinates —
  `lat, lon = (DEFAULT_LAT, DEFAULT_LON) if _is_trusted(request) else (38.8521, -77.0377)`
  — but returns `"mt_widget_key": AIS_MT_WIDGET_KEY` **unconditionally**,
  outside the trust branch. The handler's own docstring even concedes
  "mt_widget_key is unaffected ... out of scope here."
- **My live GETs (read-only):** `:8001` (trusted loopback) →
  non-empty `mt_widget_key` + the real `ULTRAFEEDER_LAT`/`LON` value (not printed here); `:8005` (demo)
  → `mt_widget_key:""` + `39.0000, -77.091`. Matches Stage 1's values.
- **Refinement (see NEW-FINDING 2):** the demo's "sanitization" is incidental,
  not an active redaction — worth noting but does not weaken P-3.

### P-4 — Public board leaks internal operational detail — CONFIRMED (Low/Info)

- **Live (read-only):** `GET /api/v1/board` returns `200` anonymously (21
  messages). Scanning the bodies (without reproducing any secret value), they
  contain infrastructure terms: `webdav`, `cloud.csexecutiveservices`,
  `app-password`, `ntfy`, `vault`, `tailnet`, `tier-0`. This matches Stage
  1's "how to reach the vault ... WebDAV + app-password" and "names internal
  ntfy channels" description. The board is public by design; the bodies are
  not scrubbed of infra detail. Confirmed.

### P-5 — Non-expiring tokens incl. leftover test token — CONFIRMED (Low)

- **Live read-only query of `auth_tokens`** (prefix/tier/expiry only) returns
  exactly Stage 1's six active tokens, all `expires_at = NULL`:
  `ctdc_admin_` (admin), `ctdc_runner_` (cert),
  `ctdc_corporatetraveler_` (admin), `ctdc_demo_recorder_` (cert),
  `ctdc_cowork_` (shares), `ctdc__ontime-test_` (admin). Three admin-tier,
  including the `__ontime-test_` leftover and the retired-bridge `ctdc_admin_`.
- **Source (`src/common/db.py:931-938`):** `lookup_token`'s WHERE clause is
  `revoked_at IS NULL AND (expires_at IS NULL OR expires_at > unixepoch())`
  — NULL expiry is permanently valid by design. Confirmed.

### P-6 — Unauthenticated passwordless-sudo `/resolve` — CONFIRMED (by-design)

- **Source (`src/web/main.py:2409-2418`):** `resolve_approval_request_route`
  has no auth dependency, only `action: str = Query(..., pattern="^(allow|deny)$")`.
  Docstring: "Tier 0 -- deliberately no auth dependency." The immediately
  adjacent GET route (`:2400`) *does* carry `Depends(require_admin(...))`,
  confirming the omission on `/resolve` is deliberate, not an accident.
  Security rests on the UUID4 request id + single-use WHERE clause, exactly
  as Stage 1 (and CLAUDE.md) describe. Confirmed as a real but intentional
  tradeoff — the single most powerful unauthenticated surface.

### P-7 — "`POST /api/v1/watchlist` is a live no-op / dead table" — COULD-NOT-REPRODUCE (partial)

**The observable facts hold; the interpretation is overstated.**

What is TRUE and I reproduced:
- `watchlist_sessions` = **0 rows** live vs. `watchlist_entries` = 317.
- The route (`src/web/main.py:1245-1288`) is `require_tier(Tier.T1)`, writes
  via `db.create_watchlist_session(...)`, and returns `201`.
- The admin `POST /api/v1/watchlist/flights` path is the primary flight-arm
  route.

What is **NOT** true — Stage 1's "Nothing consumes the sessions table" /
"live no-op route":
- `src/pusher/main.py:431 push_flight_watchlist_landings()` reads the sessions
  table live: `sessions = db.get_active_watchlists()`
  (`db.py:1346` → `SELECT * FROM watchlist_sessions WHERE status='active'`),
  filters `session_type == "flight"`, checks each for landing via
  `_check_flight_landing`, sends an `flight-alerts` ntfy push, and calls
  `db.terminate_watchlist_session(...)` (`:458`). The web layer also has a
  terminate path (`main.py:1318 get_watchlist_session`, `:1325 terminate`).
- The pusher polls on its normal loop, so a `flight` session created through
  this T1 route **would** be picked up and monitored to landing, then
  auto-terminated. The `0`-row state is consistent with sessions
  self-terminating (or the route simply being unused), **not** with the table
  being disconnected.

Net: P-7 is a genuine but *legacy-and-wired* path, not a dead no-op. Severity
is Low either way and no security posture changes, but the "nothing consumes
it / dead table" framing is inaccurate and should be corrected before it
reaches an investor doc. (Note: CLAUDE.md itself propagates the same "table
nothing consumes" phrasing; Stage 1's claim to have derived this
independently is undercut here, since the live code disproves the shared
claim.)

### P-8 — Scope-blind board key also unlocks vault reads — CONFIRMED (needs review)

- **Source (`src/web/main.py:162-173`):** `_require_board_key` authorizes on
  `_BOARD_KEY` (constant-time compare) **or** `db.board_token_valid(presented)`.
- **`db.board_token_valid` (`db.py:491-515`):** explicitly SCOPE-BLIND — the
  docstring carries a "FOOTGUN GUARD" warning that a second scope added without
  making this check scope-aware is a privilege-escalation bug. The query has
  no `AND scope=?`.
- The same `_require_board_key` gates both `POST /api/v1/board` and
  `GET /api/v1/vault/research` (`:1571`) / `/research/list` (`:1618`), so a
  board-write credential de facto also grants scoped vault-research reads.
- **Live control held:** `GET /api/v1/vault/research?path=...` with no board
  key → `401` (and `/research/list` → `401`). Confirmed. Latent coupling,
  safe today because only one scope exists — matches Stage 1.

### §3 — runner-demo healthy, not crash-looping — CONFIRMED

- **Live:** `systemctl --user show corporatetraveldc-runner-demo.service` →
  `ActiveState=active`, `SubState=running`, `NRestarts=0`; `GET :8005/ → 200`.
  This directly contradicts CLAUDE.md's "crash-looping since 2026-08-15,
  NRestarts in the tens of thousands" — the doc is stale in the platform's
  favor, exactly as Stage 1 reported. I confirmed the healthy state; I did not
  re-run the `podman inspect` mount/isolation check, but the core divergence
  (up and serving, `NRestarts=0`) is solid.

### §2 & §4.2 — health, integrity, controls — CONFIRMED

- `GET /healthz` → `status ok`, `token_count_active 6`, `cps GREEN/GO`.
- `scripts/verify-manifest.sh` → `OK -- signature valid, all 759 files match`
  (same 759 Stage 1 reported).
- Only one failed unit: `corporatetraveldc-docs-drift-weekly.service`
  (the known-broken weekly backstop). No core/skill/ingest/sweep unit failed.
- Positive controls reproduced: `GET /admin/healthz` no token → `403`;
  `GET /api/v1/vault/file?path=../../../../etc/passwd` no token → `403`
  (auth precedes traversal resolution); board key gate `401`.
- Auth model (`src/auth/auth.py:60-83`): `resolve_tier` returns `T0`
  immediately on `X-CTDI-Public: 1` **before** examining any token — so the
  public header is a genuine one-way downgrade an admin token cannot override,
  and network origin never grants a tier. Confirmed.

---

## On Stage 1's process violation (the accidental `PUT /api/v1/config` mutation)

**Assessment: isolated methodology lapse, not a reliability-poisoning
pattern.** Reasoning:

1. The disclosed mutation's blast radius is exactly what Stage 1 claimed — I
   independently confirmed `_CONFIG_PATH` is the single `runner-layer-config.json`
   UI-preference file, touching no dispatch data, auth state, or secrets. The
   disclosure is accurate and complete, which is itself evidence of honest
   reporting.
2. Every *other* state-changing probe Stage 1 describes used a non-mutating
   technique (malformed-body `422` differential for the sector mutators;
   bogus/all-zero UUID for `/resolve`; read-only GETs elsewhere). The one
   lapse was sending a live PUT before reasoning through overwrite-vs-merge
   semantics on that specific endpoint — a "test-first, reason-second" slip
   confined to P-2.
3. It does not undermine the source-based findings (P-1, P-6, P-8 rest on code
   I re-read myself) or the read-only findings (P-4, P-5, §2/§3).

The one caution it justifies: on any *future* pass, state-changing verbs
should be gated behind source review first (as this supervisory pass did),
because Stage 1 demonstrated it will occasionally probe-then-realize. That is a
discipline note, not a trust indictment.

---

## NEW-FINDING-NOT-IN-STAGE-1

1. **`watchlist_sessions` is consumed by the pusher (corrects P-7).** Covered
   above — `pusher/main.py:431` + `db.get_active_watchlists()` make the T1
   `POST /api/v1/watchlist` route a live legacy path, not the "dead table /
   no-op" Stage 1 (and CLAUDE.md) describe. This is the one substantive
   correction to Stage 1's report.

2. **P-3 demo "sanitization" of coordinates is incidental, not an active
   redaction.** On `:8005` my trusted-loopback call returned coords
   `39.0000, -77.091`, which are `DEFAULT_LAT/DEFAULT_LON`
   (`main.py:134-135`, the `ULTRAFEEDER_LAT/LON` getenv **fallback**), *not*
   the untrusted-caller placeholder `38.8521, -77.0377`. Because my caller was
   trusted, the demo returned its default — meaning the demo container simply
   has `ULTRAFEEDER_LAT/LON` and `AIS_MARINETRAFFIC_KEY` unset, so its safety
   is a consequence of absent secrets rather than the trust gate doing work.
   Doesn't change P-3's conclusion (the widget key is genuinely ungated on the
   trusted runner), but Stage 1's "the demo sanitizes both" slightly
   over-credits an active mechanism that isn't what's actually happening.

3. **Vault-research probe construction matters (not a discrepancy, a note for
   reviewers).** `GET /api/v1/vault/research` with **no** `path` param returns
   `422` (required-param validation runs before the in-body `_require_board_key`
   call), whereas with a `path` param it returns `401` as Stage 1 reported.
   Both prove the endpoint is gated; the status differs only by whether the
   required `path=` query param is supplied. No finding changes.

---

## Status

- **Confirmed by independent recheck:** 10 of Stage 1's claim-groups
  (P-1, P-2, P-3, P-4, P-5, P-6, P-8, §3, §2, §4.2 controls).
- **Could not reproduce:** 1 — P-7's "nothing consumes / dead no-op"
  interpretation (the sessions table is consumed by the pusher).
- **Needs human review:** 1 — external public-internet reachability of the
  P-1 sector endpoints (whether the `dispatch.` Cloudflare Access policy
  shields `/api/v1/sectors/*`), which cannot be tested non-destructively from
  inside the box; Stage 1 self-flagged the same limit.
- **New findings of my own:** 2 substantive notes (P-7 consumer correction;
  P-3 demo-safety-is-incidental), plus 1 construction note.

**Overall verdict:** Stage 1's report is **trustworthy enough to move forward
on.** Its highest-severity and most consequential findings (P-1 High
alert-suppression gap; the sound token-only auth model; P-8 latent coupling;
the runner-demo divergence) all hold up under independent recheck against live
source and non-destructive probes. The single inaccuracy (P-7) is a Low
finding whose *interpretation* is wrong while its *observable data* is right —
correct the wording, don't discard the item. The accidental mutation is a
disclosed, accurately-bounded, isolated lapse.
