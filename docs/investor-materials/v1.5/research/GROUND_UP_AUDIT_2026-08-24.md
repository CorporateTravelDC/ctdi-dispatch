# Ground-Up Independent Audit & Adversarial Pentest — CTDI

**Date:** 2026-08-24
**Method:** Fully independent, zero-prior-context pass. Every fact below was
personally read from source, queried read-only against the live database, or
tested directly against the live running API/containers during this session.
No prior audit/pentest/research writeup was read or relied upon. Prior audit
conclusions under `docs/investor-materials/v1.5/research/` were deliberately
NOT opened.
**Scope:** Single Raspberry Pi 5 (16 GB) production deployment at
`/opt/corporatetraveldc/private/ctdi-dispatch-internal`, live containers, live
FAA SWIM feeds, live Ollama, real production data.
**Discipline:** Non-destructive. 1–2 requests per endpoint, no fuzzing, no
retries, no secret/coordinate value reproduced. One inadvertent mutation
occurred and is disclosed in full (Finding P-2).

---

## 1. What the platform actually is (verified from source + live state)

CTDI is a real-time executive-travel intelligence platform for the Washington
DC area. Verified architecture:

- **Web (`src/web/`)** — FastAPI, tiered REST API on port 8000 (bound
  `127.0.0.1` + tailnet `100.x.x.x`). Live: `Up`, active since
  `2026-08-24 13:27:49 EDT`.
- **Poller (`src/poller/`)** — async scheduler running fetchers on intervals
  and skills as subprocesses. Live: `active running`.
- **Pusher (`src/pusher/`)** — ntfy sender. Live: `active running`.
- **Ingest (`src/ingest/`)** — SWIM push feeds split into **7 per-feed
  containers**: `ingest-{core,fdps,stdds,tfms,tbfm,itws,notam}`. All 7 live
  and `active running` at audit time.
- **Runner (`src/runner/`)** — a *separate* FastAPI app (ops dashboard SPA +
  API) on port 8001 (tailnet-only) and a public demo instance
  `runner-demo` on port 8005.
- Plus a large auxiliary estate: SDR/ADS-B/ACARS stack (ultrafeeder, piaware,
  dumpvdl2, acarshub/router), ntfy, Nextcloud, demo/demo-api, ccw-demo, and
  ~30 timer-triggered skill oneshots.

Data sources verified flowing (see §2): FAA SWIM (FDPS, STDDS, TFMS, TBFM,
ITWS, FNS/NOTAM), NWS/NWWS-OI weather, Amtrak, plus locally-received ADS-B/
ACARS. Inference is a **local Ollama** (`ollama.service` active); cloud LLM
fallback is closed (`ANTHROPIC_FALLBACK_ENABLED=false` in
`/etc/corporatetraveldc/dispatch.env`), so there is no per-token cloud cost or
external inference dependency. A second-brain knowledge vault is stored over
Nextcloud WebDAV, with a local SQLite index.

Auth is **strictly bearer-token, tiered T0/T1/T2/admin** — verified in source,
`src/auth/auth.py:60-83`: network origin never grants a tier; the old
Tailscale/XFF trust grant was removed as spoofable. This is a genuine, correct
security posture and it holds up under testing (§4).

`podman ps` shows **27 `corporatetraveldc-*` containers up**; the whole core
stack is healthy.

---

## 2. Live capability, scale & health (all read-only)

### 2.1 API health
```
curl -s http://127.0.0.1:8000/healthz
{"status":"ok","reason":null,"snapshot_age_seconds":16,"audit_count_24h":2857,
 "token_count_active":6,"cps":{"score":"GREEN","label":"GO"}}
```
Status `ok`, composite score `GREEN/GO`, snapshot 16 s old, 2,857 audited
actions in 24 h, 6 active tokens. This is a live, healthy platform, not a demo
shell.

### 2.2 Feed freshness (`GET /api/v1/feeds`)
All six real SWIM push feeds fresh at audit time:
`push:fdps` 9 s · `push:stdds` 11 s · `push:tfms` 19 s · `push:tbfm` 11 s ·
`push:itws` 8 s · `push:fns` 11 s · `push:nws` 14 s · `push:amtrak` 135 s —
every one inside its stale threshold. REST feeds (`dca_fids` 301 s, `iad_fids`
297 s, `metar` 304 s, `nas` 303 s, `notam` 301 s, `tfr` 309 s, `runsheet`
301 s, `nws` 711 s) all within threshold. Only `eurocontrol` and `jasdat`
report `awaiting_credentials` — foreign feeds that are simply not provisioned;
expected, not a fault. **Real FAA SWIM data is flowing live**, which also
proves the SWIM NMS credentials are genuine.

### 2.3 Database scale (read-only `sqlite3` on `corporatetraveldc.db`)
| Table | Rows |
|---|---|
| `flight_events` | **837,118** |
| `nas_programs` | **24,813** |
| `audit_log` | **4,933** |
| `watchlist_entries` | 317 |
| `nws_alerts` | 20 |
| `watchlist_sessions` | **0** |
| `auth_tokens` (total / active) | 19 / 6 |

The scale is real (>800 k flight events). `watchlist_sessions = 0` confirms
independently that the `POST /api/v1/watchlist` "start session" path writes to
a table nothing consumes (see Finding P-7) — 317 real entries live in
`watchlist_entries` instead.

### 2.4 Test suite (`PYTHONPATH=src python -m pytest tests/ -q`)
```
1 failed, 222 passed, 7 warnings in 11.11s
```
**223 collected, 222 pass, 1 fail** — the single failure is
`tests/ingest/test_marine_one_detection.py::test_smes_parser_basic`, a
pre-existing marine-detection assertion unrelated to the platform's core
paths. Independently reproduced; matches the documented single-failure claim.

### 2.5 Manifest / signature integrity (`scripts/verify-manifest.sh`)
```
verify-manifest: OK -- signature valid, all 759 files match.
```
Tree is fully signed and clean: 759 manifest entries, 724 git-tracked files,
GPG signature valid. No unsigned drift at audit time.

### 2.6 Provenance (`git log`)
**635 commits**, `2026-06-07` → `2026-08-24`, a **single author** across four
email identities all resolving to the operator / [operator LLC] LLC
(`owner@example.com`, `owner@example.com`,
`operator@example.com`, `developer@example.com`).
This is a solo-built system with ~78 days of continuous history.

### 2.7 License
`LICENSE` is a correctly-parameterized **Business Source License 1.1**:
Licensor `[operator LLC], LLC`; Licensed Work `Corporate Travel
Dispatch Intelligence (CTDI), version 2026.08`; Change Date `2030-08-24`;
Change License `GNU GPL v3 or later`. Not a placeholder — the BSL parameters
are filled and coherent.

### 2.8 Systemd unit health
Exactly **one** `corporatetraveldc-*` unit is `failed`:
`corporatetraveldc-docs-drift-weekly.service` (a weekly documentation-drift
*backstop*, known-broken on a PATH/`cd` bug). No core container, skill timer,
integrity sweep, or ingest unit is failed. `integrity-sweep` reads `inactive`
— normal for a timer-triggered oneshot between runs, not a failure.

---

## 3. A notable divergence: documented "Known bad" is stale in the safe direction

The in-repo `CLAUDE.md` documents `corporatetraveldc-runner-demo` as
crash-looping since 2026-08-15 with `NRestarts` in the tens of thousands, and
`DEMO_MODE` inert. **Live state contradicts this in the platform's favor:**

```
systemctl --user show corporatetraveldc-runner-demo.service -p NRestarts -p SubState
NRestarts=0
SubState=running
```
- runner-demo is **up and serving** (`GET :8005/ → 200`, `Up 2 hours`,
  `NRestarts=0`).
- Its data is **isolated**: `podman inspect` shows it mounts host dir
  `/var/lib/corporatetraveldc-demo → /var/lib/corporatetraveldc`, i.e. a
  *separate* demo directory shadowing the production path. Its chat DB
  (`/var/lib/corporatetraveldc-demo/dispatch-chat.db`, created today) is
  **empty**: `GET :8005/api/chat/history → {"messages":[],"count":0}`. No
  production data is reachable from the demo container.
- `DEMO_MODE` is still unset in the running container (verified via
  `podman exec ... printenv DEMO_MODE` → empty), so the app-layer demo gate
  remains inert — but the crash that masked it is resolved and the demo now
  runs on isolated, sanitized state.

Interpretation: the box was rebuilt/repaired overnight (core containers show
~3 h / ~9 min uptimes; demo dir timestamps are today). The documented crash
loop is fixed; the doc is behind live state. This is the safe direction to be
wrong in, and it materially improves the demo's real posture versus what the
docs claim.

---

## 4. Adversarial penetration test

Confidence labels: **CONFIRMED** (I reproduced it live), **BY-DESIGN**
(intentional, verified in code), **POSITIVE** (a control that held).

### 4.1 Findings summary

| # | Severity | Status | Finding |
|---|---|---|---|
| P-1 | **High** | CONFIRMED | 5 unauthenticated `/api/v1/sectors/*` POST mutators |
| P-2 | Medium | CONFIRMED | Runner `PUT /api/v1/config` — unauth, blind full-overwrite |
| P-3 | Medium | CONFIRMED | MarineTraffic widget key served to any caller by runner config endpoint |
| P-4 | Low/Info | CONFIRMED | Public `/api/v1/board` leaks internal operational detail |
| P-5 | Low | CONFIRMED | 6 active tokens, all non-expiring; 3 admin-tier incl. leftover test token |
| P-6 | Info | BY-DESIGN | Unauthenticated `/admin/.../resolve` grants passwordless-sudo approval |
| P-7 | Low | CONFIRMED | `POST /api/v1/watchlist` is a live no-op route (writes dead table) |
| P-8 | Info | NEEDS REVIEW | Board key is a single scope-blind secret that also unlocks vault-research reads |

### P-1 — Five unauthenticated `/api/v1/sectors/*` POST mutators — **CONFIRMED, High**

Source (`src/web/routes/sectors.py:54-118`): five POST handlers declare only a
path param and a Pydantic body — **no `require_tier`/`require_admin`
dependency**:
`POST /api/v1/sectors/{sector}/silence`,
`POST /api/v1/sectors/feed/{feed_name}/silence`,
`POST /api/v1/sectors/topic/{topic}/throttle`,
`POST /api/v1/sectors/topic/{topic}/enabled`,
`POST /api/v1/sectors/topic/{topic}/sanitize`.
They call `set_sector_silence` / `set_feed_silence` / `set_topic_throttle` /
`set_topic_enabled` / `set_topic_sanitize` respectively.

**Live proof, non-mutating** (invalid body fails validation *before* the
mutation runs, so state is untouched):
```
POST /api/v1/sectors/topic/AUDIT-PROBE-NONEXISTENT/throttle   (no token, body {})   -> 422
POST  same, with header X-CTDI-Public: 1                                             -> 422
GET  /api/v1/sectors/topic/dispatch                            (no token)            -> 200
--- control ---
POST /admin/bandwidth-priority                                (no token, body {})   -> 403
```
The `422` (validation) rather than `403` (auth) proves the request passed all
dependency resolution with **no auth gate**, on both the plain path and the
simulated public-tunnel path (`X-CTDI-Public: 1`). The admin control endpoint
correctly returns `403`.

**Impact:** an unauthenticated caller reaching this app (anyone on the tailnet
or loopback; and the public-tunnel downgrade header does *not* protect it,
since these routes have no tier dependency for the header to downgrade) can
silence any sector's or feed's alerts platform-wide, throttle/disable
arbitrary ntfy topics, or toggle identifier *sanitization* on a topic. Alert
suppression is the most concerning: a dispatch platform whose alerts can be
silenced without credentials can be blinded. This is the same class of gap the
codebase already closed for the knowledge-graph, vault, and OSINT routers — the
`sectors` router was evidently missed. **Recommend adding
`Depends(require_admin(...))` to all five.**

### P-2 — Runner `PUT /api/v1/config` unauthenticated blind overwrite — **CONFIRMED, Medium**

Source (`src/runner/main.py:1771-1782`): the handler takes the raw JSON body
and does `json.dump(body, f)` straight to `runner-layer-config.json` — no auth
dependency, no `_is_trusted()` check, **no merge** (a full overwrite), no
schema validation.

**Live proof — and full disclosure of an inadvertent mutation:** probing this
with `PUT :8001/api/v1/config` body `{}` returned **`200 {"ok":true}`**,
confirming no auth gate. Because the handler overwrites rather than merges,
this **wrote `{}` into `/var/lib/corporatetraveldc/runner-layer-config.json`**
(now 2 bytes, mtime today). I did not anticipate the blind-overwrite behavior
before sending the probe. **Impact of my probe is limited to the frontend's
saved map-layer UI preferences resetting to defaults** — it is a client-side
convenience file, not dispatch data, auth state, or security configuration,
and it is recreated the next time the SPA saves layer prefs. No production
data, feed, token, or dispatch function was affected. I am disclosing it
because (a) transparency, and (b) it *is* the finding: an unauthenticated
endpoint that blind-overwrites persisted config from an arbitrary body is a
real (if low-value) integrity gap on the tailnet-scoped runner. The same code
backs the public demo (`:8005`); I did **not** test it against `:8005` to
avoid mutating the public demo.

### P-3 — Widget credential returned to any caller — **CONFIRMED, Medium/Low**

Source (`src/runner/main.py:1724-1756`): `GET /api/v1/frontend-config` returns
`{"mt_widget_key": AIS_MT_WIDGET_KEY, "receiver_lat": lat, "receiver_lon":
lon}`. The **coordinates are correctly trust-redacted** — a trusted
(tailnet/LAN) caller gets the real receiver location, an untrusted caller gets
a hardcoded DC-area placeholder (`main.py:1754`). Verified live: the
production runner (`:8001`, trusted loopback) returns real coords
(the real `ULTRAFEEDER_LAT`/`ULTRAFEEDER_LON` value, not printed here) plus a **non-empty
`mt_widget_key`**; the public demo (`:8005`) returns `mt_widget_key:""` and
*different* coords (`39.0000,-77.091`) — the demo sanitizes both. Good.
**But `AIS_MT_WIDGET_KEY` (a MarineTraffic widget embed key) is returned
unconditionally, with no trust gate**, unlike the coordinates. On the
tailnet-scoped runner this exposes a real third-party credential to any device
on the tailnet without authentication. Value not reproduced here.
**Recommend gating the widget key behind `_is_trusted()` the same way the
coordinates already are, or blanking it for untrusted callers.**

### P-4 — Public board leaks internal operational detail — **CONFIRMED, Low/Info**

`GET /api/v1/board` is fully anonymous (`T0`, verified `200` with and without
`X-CTDI-Public: 1`) and, per `CLAUDE.md`, exposed publicly via a Cloudflare
Access bypass. The returned messages are real inter-service coordination
threads whose *bodies* contain internal infrastructure hints — e.g. one message
describes how to reach the vault ("reachable at Tier-0 via
`cloud.example.com` WebDAV + the vault app-password") and names
internal ntfy channels. The board being public is by design; the **content is
not sanitized of internal operational detail**, which is an information-
disclosure concern for an internet-reachable endpoint. **Recommend either
scrubbing infra references from board bodies or reconsidering the public
bypass.**

### P-5 — Non-expiring tokens, including a leftover test token — **CONFIRMED, Low**

Read-only query of `auth_tokens` (prefix/tier/expiry only, no secrets):
```
ctdc_admin_            admin  NEVER
ctdc_runner_           cert   NEVER
ctdc_corporatetraveler_ admin NEVER
ctdc_demo_recorder_    cert   NEVER
ctdc_cowork_           shares NEVER
ctdc__ontime-test_     admin  NEVER
```
All **6 active tokens have `expires_at = NULL` (never expire)** — the token
lookup WHERE clause (`db.py:931-938`) treats NULL expiry as permanently valid
by design, and nothing sets a default TTL at mint time. **Three are
admin-tier**, including `ctdc__ontime-test_` (an apparent leftover *test*
token that should be revoked) and `ctdc_admin_` (the retired MCP-bridge
token, still active). **Recommend revoking the test and retired-bridge admin
tokens and setting a default TTL on new mints.**

### P-6 — Unauthenticated passwordless-sudo approval resolve — **BY-DESIGN, sensitive**

`GET /admin/approval-requests/{request_id}/resolve` (`src/web/main.py:2409`)
carries **no auth dependency** — only an `action` query param constrained to
`^(allow|deny)$`. This is intentional (Cloudflare strips `Authorization`, so a
phone tap must be unauthenticated) and it is the endpoint that grants
passwordless-sudo approval. Security rests entirely on the 122-bit UUID4
request id (a magic link) plus single-use enforcement in the resolving WHERE
clause. Live probe with a bogus UUID returned `422` (param validation), and an
all-zero UUID returned `422` — the endpoint reaches validation without auth, as
designed. This is a legitimate design tradeoff but is the single most powerful
unauthenticated surface; worth keeping under review (e.g. request-id entropy,
expiry-on-read, rate limiting).

### P-7 — `POST /api/v1/watchlist` is a live no-op route — **CONFIRMED, Low**

The T1 "start session" route writes to `watchlist_sessions`, which holds **0
rows** (verified) versus 317 in `watchlist_entries`. Nothing consumes the
sessions table. The route returns 201 so a caller believes it armed tracking.
Real flight watchlisting is the `POST /api/v1/watchlist/flights` (admin) path.
Recommend deprecating or wiring the dead route.

### P-8 — Scope-blind board key also unlocks vault reads — **NEEDS REVIEW**

`_require_board_key` (`main.py:162-173`) authorizes on a single shared
`BOARD_KEY` env value **or** any `db.board_token_valid(...)` token, and that
validator (`db.py:491-515`) is **scope-blind** — it checks existence and
non-expiry only. The same key/token now gates both `POST /api/v1/board` and
the `GET /api/v1/vault/research*` reads (`main.py:1571,1618`). So a
"board-write" token de facto also grants scoped vault-research reads. The code
itself documents this as a footgun. Currently only one scope exists so it is
safe today, but this is a latent privilege-coupling to watch. Live control held
correctly: `GET /api/v1/vault/research` with **no** board key → `401`.

### 4.2 Controls that HELD (POSITIVE)

- `GET /admin/healthz` — no token → `403`; garbage bearer token → `403`.
- `GET /api/v1/vault/file` — no token → `403`; and a path-traversal attempt
  (`?path=../../../../etc/passwd`) → `403` (**auth is evaluated before the
  path is ever resolved** — traversal can't be reached unauthenticated). The
  traversal guard itself (`_vault_path_is_safe`, `main.py:241-272`) loops
  `unquote` up to 5× and rejects `..`/leading-slash/backslash — sound design.
- `GET /api/v1/knowledge-graph/html` — no token → `403`.
- `GET /api/v1/vault/research` — no board key → `401`.
- **No SQL injection surface found.** Independent read of `db.py`,
  `index_db.py`, `semantic/compile.py`: every dynamic-SQL site interpolates
  only placeholder lists / hardcoded column names / whitelisted keys, with all
  values bound as parameters.
- **No hardcoded secrets in tracked source.** Pattern scan of `src/**.py` and
  targeted read of `db.py`/`webdav_client.py`/`index_db.py` found only env-var
  reads and `secrets`-module generation; tokens stored as SHA-256 hashes only.
- **Tier model is bearer-token-only** — origin never elevates; `X-CTDI-Public`
  is a one-way downgrade to T0 that even a valid admin token cannot override.
- **Coordinate redaction works** for untrusted callers on the runner config
  endpoint (P-3).
- **Watchlist writes** (`/flights`, `/trains`, `/vessels`, `/batch`, DELETE)
  are all `require_admin`; **admin actions are audit-logged** via the
  `require_admin(action)` factory (4,933 audit rows; 2,857 in 24 h).

---

## 5. Security posture — overall assessment

The **foundational security model is genuinely sound**: token-only tiering with
no spoofable origin grant, SHA-256-hashed token storage, parameterized SQL
throughout, an auth-first request pipeline (auth precedes path/traversal
evaluation), a working audit trail on every admin mutation, a
cryptographically-signed manifest integrity gate over the tree, and closed
cloud-LLM fallback (no data egress to an external model). The traversal
defenses and the public/untrusted redaction paths are real and were observed
working.

The weaknesses are **coverage gaps, not architectural flaws** — a few routers
and one runner endpoint were never wired to the auth dependency the rest of the
codebase uses consistently:
- P-1 (unauthenticated sector/alert control) is the one I would fix first — it
  is a *live alert-suppression* vector and trivially exploitable from the
  tailnet.
- P-2/P-3 (runner config overwrite; unconditional widget-key exposure) are
  lower-value but same-class oversights on the runner app, which has its own
  weaker IP-based trust model separate from the web app's token tiers.
- P-5/P-8 are hygiene/latent-coupling items.

Nothing observed suggests compromise, data loss, or a break in the core
dispatch function. The platform is running, healthy, ingesting real FAA data,
and its integrity gate is clean.

---

## 6. Economics (independently derived — conservative)

I derived these from the actual hardware in use and verifiable inputs only; I
did **not** import any prior cost analysis.

**Hardware (capex, one-time):** confirmed `Raspberry Pi 5 Model B Rev 1.1`,
16 GB (`/proc/cpuinfo`, `free -h` → 15 GiB). `docs/COST_STRUCTURE.md`
(operator's own internal figures) puts one fully-built Pi at **≈ $635–700**
(Pi 5 16 GB ~$350 + NVMe ~$200 + PSU ~$40 + case ~$50), plus SDR dongles and
antennas for the RF-ingest tier. A single-node deployment is thus roughly a
**~$700 one-time build**.

**Recurring opex — essentially electricity only.** The data sources are free
(FAA SWIM, NWS/NWWS-OI, Amtrak public), inference is a **local Ollama** (no
per-token cost, cloud fallback verified closed → $0 external LLM spend), and
ntfy is self-hosted. A Pi 5 with NVMe + SDR under sustained load draws on the
order of 10–15 W. At 12 W average: 12 W × 730 h/mo ≈ **8.8 kWh/mo**; at the
US-average ~$0.13/kWh that is **≈ $1.15/month**. Even at a pessimistic 20 W
continuous it is ≈ $1.90/month. Recurring marginal cost is therefore
**~$1–2/month of electricity plus a share of an existing internet
connection** — no metered API, subscription-feed, or cloud-compute cost was
found anywhere in the running configuration.

I deliberately make **no** subscription-replacement dollar claim (e.g. "vs
$X/mo of commercial aviation-intel subscriptions"): that figure is real in
spirit but I cannot verify specific competitor pricing from inside this box, so
I leave it out rather than invent it.

---

## 7. What surprised me / could not fully verify

- **runner-demo is healthy, not crash-looping** (§3) — the most striking
  divergence from the in-repo docs, and in the platform's favor. The box was
  clearly repaired/rebuilt overnight.
- **Ollama model enumeration was empty at audit time** — `GET
  /api/tags` and `/api/ps` returned nothing while `ollama.service` is `active`.
  Consistent with no model resident (idle `keep_alive` expired), not a fault; I
  could not independently count the "21 models" live, but the service is up and
  briefs/CPS are green.
- **Public-internet reachability of the P-1 endpoints could not be tested from
  an external vantage.** From loopback/tailnet the auth gap is definitive.
  Whether the public `dispatch.` hostname's Cloudflare Access policy happens to
  shield `/api/v1/sectors/*` (it is not in the known bypass allowlist) I could
  not confirm non-destructively — but relying on an edge ACL for
  authentication that the app itself omits is fragile regardless.
- **Foreign feeds** (`eurocontrol`, `jasdat`) are `awaiting_credentials` —
  expected, not provisioned.
- I **inadvertently reset one UI-preference file** (P-2) and have disclosed it
  in full; no production data or security state was touched.

---

## Appendix — commands run (representative)

```
podman ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
systemctl --user list-units 'corporatetraveldc-*' --all --no-legend
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/api/v1/feeds
sqlite3 -readonly /var/lib/corporatetraveldc/corporatetraveldc.db "SELECT ..."   (read-only)
PYTHONPATH=src python -m pytest tests/ -q --tb=no
bash scripts/verify-manifest.sh
git log --format=... | ...
# auth probes (no token / garbage token / X-CTDI-Public / invalid-body)
curl -s -o /dev/null -w '%{http_code}' [various endpoints]
podman inspect systemd-corporatetraveldc-runner-demo --format '{{range .Mounts}}...'
podman exec systemd-corporatetraveldc-runner-demo printenv DEMO_MODE CHAT_DB_PATH
```
All destructive/mutating operations were avoided except the single disclosed
`PUT /api/v1/config` side-effect (P-2). No secret, token, key, or coordinate
value is reproduced in this report.
