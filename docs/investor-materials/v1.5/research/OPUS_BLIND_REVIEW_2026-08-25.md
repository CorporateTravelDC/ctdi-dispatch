# CTDI — Independent Blind Ground-Up Audit & Penetration Test

**Date:** 2026-08-25
**Auditor:** Opus 5 (independent agent), fully blind pass
**Target:** `/opt/corporatetraveldc/private/ctdi-dispatch-internal` — live production Raspberry Pi 5, rootless Podman/systemd, real FAA SWIM credentials, real Ollama, real production data.

## Provenance / method

Every claim below comes from something read in the source tree, queried against the live running system, or tested directly during this session. **No pre-existing research, audit, drift, or live-state document was read as a source of truth** — the `docs/investor-materials/v1.5/research/` tree and the docs' own findings were deliberately avoided, and CLAUDE.md was treated as untrusted narrative, not evidence. Where a prior doc happened to describe the same thing, that is convergence, not sourcing.

Discipline observed: non-destructive throughout — 1–2 requests per endpoint, no fuzzing/brute force, no `DELETE`/mutating calls that could change live state (the passwordless-sudo endpoint was probed only with a non-existent all-zero UUID that cannot resolve), and no secret/token/coordinate value is printed even partially anywhere in this report.

Codebase coverage was exhaustive: the full `src/` tree (web, auth, poller + 16 fetchers + ~40 skills, ingest + 7 parsers, runner, demo, second_brain, common/db, shared), all `scripts/`, container/quadlet/nginx/cloudflared config, the signed-manifest chain, the test suite, and the public git remote (every ref, full history + tree, value-scanned against the live secrets file).

---

## Overall assessment

The platform is **real and genuinely operational** — live FAA SWIM push feeds (fdps/tfms/stdds/tbfm/itws/fns), a 24.4 GB production database with 842k flight events, real Ollama inference, and a working signed-manifest + fail2ban/honeypot + Cloudflare-Access defensive posture. The core auth model (`resolve_tier`) is sound: **no forged, garbage, or SQL-injection token got any tier**, and there is no header/IP/network tier grant. The **public git mirror is clean of credentials** — no real credential, FAA username, feeder UUID, or GPS coordinate appears in any ref's tree or its (squashed, 2-commit) history; the scrub pipeline drops the real scrubber and CLAUDE.md and publishes only sanitized substitutes. **One qualification (C-30):** the scrubber is byte-level and does not decompress OOXML, so the **17 `.docx`/`.pptx` files in the public mirror pass through unscrubbed** — several carry the operator's real name + business domain inside their compressed XML. On investor decks that is plausibly intended-public, but the control enforces nothing on this file class, so anything genuinely sensitive inside an Office file would ship silently.

That said, this pass found **multiple genuinely serious issues that are live right now**, the most important of which are architectural rather than typos:

1. **The runner proxy's admin/mutation gate is bypassable with a `/./` path segment** (httpx normalizes it away after the middleware check), and the network-trust decision behind it (`_is_trusted`) is **spoofable with a plain `X-Forwarded-For`/`CF-Connecting-IP` header** — confirmed live. Together these front a deliberately-unauthenticated passwordless-sudo endpoint.
2. **FAA SWIM TLS certificate validation is disabled** in the running ingest image (`without_certificate_validation()`), so six sets of real FAA credentials are MITM-harvestable and every XML parser's input becomes attacker-controllable.
3. **The signed-manifest integrity chain does not pin the signing-key fingerprint** — it trusts whatever public key is committed in the tracked tree, which is exactly the adversary the manifest is meant to defend against.
4. A cluster of **fail-open / silent-failure** patterns on an *alerting* platform: ntfy 401/403 reported as success (1,273 real occurrences), the SR-2 gate permanently suppressing retries after a crash, the cloud-egress kill-switch defaulting open, failed admin authorization writing no audit row, and `/healthz` returning HTTP 200 while `"degraded"`.
5. **Personal/operational research notes are anonymously readable** from the public internet via `GET /api/v1/board` (live: 6 messages in a `research` thread).

None of these requires trusting anyone's prior work to see — each is reproduced from source or the live box below. The system is impressive for a single-operator edge deployment; it is not yet at the security bar its own investor-facing framing implies, primarily because several controls are **present but not actually load-bearing** (the manifest key isn't pinned, `_is_trusted` trusts a client header, DEMO_MODE gates are inert, the scrub gate matches two regexes).

---

## Severity summary

| # | Sev | Finding | Verified |
|---|-----|---------|----------|
| C-1 | **Critical** | Runner proxy `/./` dot-segment bypasses `tailscale_gate` admin/mutation gate → reaches passwordless-sudo endpoint | **Live on :8001** (untrusted origin reached `/admin/approval-requests/{id}/resolve`) |
| C-0a | **Critical** | Signed-manifest gate never covers `.pyc`; a planted bytecode file executes while the `.py` hash still verifies clean (`/app` writable, `PYTHONDONTWRITEBYTECODE` unset) | Sub-audit repro + **live** (`/app` WRITABLE, no `PYTHONDONTWRITEBYTECODE`) |
| C-0b | **Critical** | `grant-agent-session.sh` splices shell vars into `python3 -c` strings → injection into the functions gating the agent signing key | Sub-audit repro |
| C-0c | **High** | `pre-push` public-mirror guard checks the remote *name* only, never the URL (`$2`) → `git push <url>` bypasses the scrub step | **Live** (`grep '$2' pre-push` = 0) |
| C-30 | **High** | Scrubber is blind to OOXML: 17 `.docx`/`.pptx` in the public mirror ship unscrubbed; several carry the operator's real name + domain in compressed XML | **Live** (decompress-scan of `public/main`) |
| C-31 | **High** | `faa_upsert_ladd()` wipes the FAA LADD privacy-suppression list on an empty parse result (no empty-guard, unlike sibling functions) | Source + caller trace |
| C-32 | **Med-High** | `board_consume_nonce()` read-then-write race can mint two board-write tokens from one single-use nonce | Source (transaction semantics) |
| C-33 | **Med** | `nas_programs` (and ~10 other tables) has no prune path; explicitly required to retain long-term → unbounded | Source (exhaustive grep) |
| C-34 | **Med** | `.git/hooks/*` untracked/unmonitored → `pre-commit`/`pre-push`/`post-commit` can silently drift (post-commit already stale live) | **Live** (diff vs `scripts/` source) |
| C-2 | **Critical** | `_is_trusted()` trusts client-supplied `X-Forwarded-For`/`CF-Connecting-IP` | **Live** (`XFF: 10.x.x.x` → 200 real chat data; `8.8.8.8` → 404) |
| C-3 | **Critical** | FAA SWIM TLS cert validation disabled in live image; 6 real cred sets MITM-exposed | **Live** (`grep` in running image = 1) |
| C-4 | **High** | Signed-manifest verify does not pin key fingerprint; trusts co-located tracked pubkey | Source (`verify-manifest.sh:59-84`, no `FINGERPRINT` ref) |
| C-5 | **High** | Anonymous read of operator research/personal notes via `GET /api/v1/board` | **Live** (6 msgs, public path 200) |
| C-6 | **High** | ntfy 401/403 returns success; 1,273 pushes silently classified "probably delivered" | **Live** (dedup file = 1273) |
| C-7 | **High** | SR-2 gate writes hash before work → one crashed run permanently suppresses retries (incl. hot VIP/TFR path) | Source + simulation |
| C-8 | **High** | Cloud-egress kill switch defaults **open** and is latched at import; only the absent API key prevents egress | Source + empirical |
| C-9 | **High** | `require_admin` never audits **failed** authorization; audit log stores unredacted request bodies (vault notes, sudo commands, feed URLs) | **Live** (0 denied rows; 153 body rows) |
| C-10 | **High** | `recorder.py` mirrors raw production into 2.6 GB `demo.db` unscrubbed; isolation rests on one out-of-band script | **Live** (2.6 GB file) |
| C-11 | **Med-High** | Wildcard CORS on tunnel-exposed API (`allow_origins=["*"]`, DELETE allowed) | **Live** (preflight from evil origin → `*`) |
| C-12 | **Med-High** | `/openapi.json` served (200, 76 KB, 19 admin paths) despite `docs_url=None` | **Live** |
| C-13 | **Med-High** | Unauthenticated SSRF: runner `GET /api/rss/custom`, poller `osint_monitor` feed_urls, `entity_tracking` LLM-chosen host (redirects followed) | Source |
| C-14 | **Med** | `expire_nws_alerts` deletes all push-sourced NWWS rows on first REST poll after failover | Source + live (22 push rows at risk) |
| C-15 | **Med** | Audit-log `remote_addr` attacker-controlled (`--forwarded-allow-ips=*`, leftmost XFF) | Source |
| C-16 | **Med** | `scrub_gate` matches only 2 regexes (case-sensitive; misses HF freqs); `verify_scrubbed` has no coordinate check | Source |
| C-17 | **Med** | Unauthenticated blocking full-table scan on event loop (`/api/v1/aircraft`) → single-worker self-DoS / watchdog-restart primitive | **Live** (anon 200) |
| C-18 | **Med** | Thermal LOCKDOWN over-trips (10×/day, ~9–11 min each) sheds entire core stack on Ollama/timer bunching | **Live** (tier 2, 535s, 10 trips today) |
| C-19 | **Med** | `fdps_parser.py:950` `gufi_override` is a nonexistent key → GUFI arm can only false-match every watchlist entry | Source + isolation test |
| C-20 | **Med** | Negative `limit` → `LIMIT -1` → unbounded table dumps (osint feed, watchlist history, audit log) | Source |
| C-21 | **Med** | Unbounded growth: `push_dedup` files (notam 4,814 keys / 328 KB rewritten per alert), 11 DB tables with no prune, `demo.db` 2.6 GB, leaked asyncio tasks per NWWS reconnect | **Live** |
| C-22 | **Med** | DEMO_MODE unset on the live public demo → password gate, signal sanitization, ntfy suppression all inert | **Live** (`{"demo_mode":false}`) |
| C-23 | **Low-Med** | Blocking `requests`/WebDAV/PBKDF2 inside `async def` handlers on single worker | Source |
| C-24 | **Low-Med** | Hardcoded receiver GPS defaults in source, NOT covered by `scrub-public-tree.py` | Source |
| C-25 | **Low-Med** | `swim_test.py` disables TLS + prepends cleartext `tcp://` against production FAA creds (diagnostic tool) | Source |
| C-26 | **Low** | `/api/v1/whoami-token` unauthenticated token-validation oracle; `/healthz`/`data-usage`/`demo/readiness` info leaks | **Live** |
| C-27 | **Low** | `scrub_rules.py` hardcodes the operator's full PII dossier in a tracked file | Source |
| C-28 | **Low** | 20 auth tokens, all `expires_at IS NULL` (never expire), incl. 2 active admin | **Live** |

Plus a large tail of correctness bugs (timezone naive/aware, Marine One nationwide false-positives, taxi-alert sector clobber, CPS misses light/heavy precip, marker-string mismatches) enumerated in the per-area sections.

---

## Live system state (independently observed)

- **Host:** RPi 5, up 6d, load ~7–8, temp ~66 °C. 36 podman containers (`podman ps -a`), 124 `corporatetraveldc-*` user units.
- **Failed units at audit time:** `corporatetraveldc-integrity-sweep` and `corporatetraveldc-docs-drift-weekly`. The sweep fails only on 2 uncommitted-but-unsigned docs edits (`docs/dispatch-runner-design.md`, `scripts/pre-commit-README.md`) — the expected edit-then-sign cycle, not a compromise; the GPG signature itself verifies **Good** against the operator's rotated key.
- **`runner-demo` is UP** (NRestarts=0, ~22 h) and serving 200 on :8005 — the previously-documented multi-week crash loop is resolved; its DB is now a dedicated `/var/lib/corporatetraveldc-demo` mount (isolated from production).
- **Thermal LOCKDOWN (tier 2) was active for much of the audit** — poller/pusher/runner/ingest/ollama shed, `web` surviving by design. 10 LOCKDOWN trips today; `/healthz` flipped `ok`→`degraded` (still HTTP 200). This blocked live re-confirmation of C-1 on the production runner.
- **Test suite:** `222 passed, 1 failed` — the one failure is the known pre-existing `test_smes_parser_basic` marine-detection assertion, unrelated to any finding here.
- **Auth tokens:** 20 total, 5 active (2 admin, 2 cert, 1 shares); **all 20 have `expires_at IS NULL`**.
- **Listening surface:** web :8000, runner :8001, demo-api :8004, runner-demo :8005, ccw-demo :8085 (all on loopback + tailnet 100.x.x.x); ntfy :2586, openwebui :3000, ultrafeeder :8080/:30005 on 0.0.0.0; SSH :22 on 0.0.0.0.

---

## Secrets scan — tracked tree & public remote (clean, with nuance)

Method: read the live `/etc/corporatetraveldc/dispatch-secrets.env` (readable as the `corporatetraveldc` user), extracted every non-placeholder value, and value-scanned (a) the full tracked tree and (b) **every ref of the `public` remote** — full tree (718 files) and full history (2 commits) — reporting only key-name + file, never a value.

**Findings:**

- **Public mirror is clean of real secrets.** No real FAA username/password, feeder UUID, GPS coordinate, or API token appears in the public tree or history. The only secrets-file values present publicly are the **SWIM host endpoints** (`tcps://ems1/ems2.swim.faa.gov:55443`) — which are the **hardcoded public defaults in `src/ingest/config.py:122`** and are documented FAA infrastructure, not secrets. The real scrubber (`scrub-public-tree.py`, which itself contains real FAA usernames and the feeder UUID as substitution keys) is **dropped** from public; only `scrub-public-tree.example.py` ships. `pre-commit`/`pre-push` ship but contain only template placeholders.
- **Internal repo (`origin`) contains real values in tracked files by design** — `scrub-public-tree.py` (SWIM usernames, feeder UUID as substitution literals) and `.config/containers/systemd/corporatetraveldc-ultrafeeder.container` (real `FEEDER_ID` UUID). Acceptable for an internal repo *only because* the scrub pipeline reliably strips them for public — which it does. Twilio/Pushover values that matched tracked files are **template placeholders** (len 9, identical to the template), i.e. those secrets were never actually set.
- **`dispatch-secrets.env` is git-ignored and not tracked; no private-key material is tracked** (root `.gpg` files are public keys; `security/signing.env` holds only GPG *fingerprints*, no passphrase).
- **Gap (C-24):** receiver GPS coordinates are hardcoded as env-var *defaults* in source (`src/ingest/local_airspace.py:47-48`, `src/runner/main.py:152-153`) and there is **no substitution or regex for those literals in `scrub-public-tree.py`** — the scrubber's own comments (`:112, :286-287`) acknowledge the files "should stop carrying the literal at all." Today the public values differ from the live coordinates, so nothing leaked, but the scrubber does not defend this class.

---

## Web API & auth (`src/web`, `src/auth`)

101 operations across 94 paths (enumerated from live `/openapi.json` and cross-checked against the app's route table). `resolve_tier` (`auth/auth.py:60-83`) is the sole tier authority: `X-CTDI-Public: 1` forces T0 (downgrade only, never elevation), else a valid bearer token maps `admin/shares/cert` → tiers, else T0. **Verified live:** forged admin-shaped token, garbage, and `'; DROP TABLE auth_tokens;--` all → 403; no header/IP grant exists. Token compare is SHA-256 hex equality (not constant-time, but the compared value is a hash of attacker input — not practically exploitable). No `eval/exec/os.system/subprocess/pickle/yaml.load` anywhere in scope; no f-string SQL reachable with attacker input.

Key findings (full detail retained from the web sub-audit):

- **C-9 audit gaps.** `require_admin` (`auth.py:140-203`) logs *before* the handler (good, verified via FastAPI dependency ordering) but **never audits a failed authorization** — the 403 is raised before the `db.audit()` write, so admin-surface probing leaves zero trace (**live: 0 denied rows in `audit_log`**). It also persists **full unredacted request bodies** into `audit_log.detail` for 90 days (**live: 153 rows with `feed_urls`/`command`/`message`**) — including `POST /api/v1/remember` note text that the scrub gate exists to keep out of the vault, and `feed_urls` that commonly carry API keys.
- **C-15.** The container runs `uvicorn --proxy-headers --forwarded-allow-ips=*`, so `request.client.host` (→ `audit_log.remote_addr`, `board_messages.remote_addr`) is the **client's own leftmost XFF value** on tunnel traffic — forgeable from the internet. No authz depends on it, so this is audit-integrity corruption, not a bypass.
- **C-12.** `docs_url=None, redoc_url=None` but `openapi_url` left default → `GET /openapi.json` returns 200 / 76,885 bytes / **19 admin paths** with full schemas (live). Currently behind CF Access on the public path; open to loopback + entire tailnet.
- **C-11.** `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])` (`main.py:77-82`) on a tunnel-exposed API; the "tighten at nginx" comment is unfulfilled. Live preflight from `https://evil.example` → `access-control-allow-origin: *`, methods incl. DELETE. `allow_credentials` unset, so blast radius is Tier-0 data + any `_is_trusted`-gated (not cookie-gated) runner route.
- **C-17.** `GET /api/v1/aircraft/{id}` is `async def` calling a **blocking** query that wraps the indexed column in `LOWER()/UPPER(REPLACE())` → full scan of 519,991 rows on the single-worker event loop (sub-audit measured 1.9 s; I measured 0.45 s live under different load). Anonymous. A request loop saturates the loop and, via the watchdog's 5 s `/healthz` timeout, becomes an **unauthenticated remote stack-restart primitive**. `init_db_v17()` is also never called by web → 500 on a fresh DB.
- **C-20.** Negative `limit` → SQLite `LIMIT -1` → unbounded: `/api/v1/osint/feed` (no `ge`), watchlist history (`min` only), `/admin/audit`. `/api/v1/opsplan/range` has no LIMIT at all.
- **C-26 & info leaks.** `/api/v1/whoami-token` is an unauthenticated, unrated token-validation oracle. `/healthz` leaks `audit_count_24h`+`token_count_active`; `/api/v1/data-usage` leaks per-interface/per-day network stats + `log_path`; `/api/v1/demo/readiness` leaks `db_size_mb` and paths; five handlers return `str(exc)`.
- **Shadowed routes:** `DELETE /api/v1/watchlist/{session_id}` (documented T1 "terminate session") is shadowed by the admin `{entry_id}` route — a caller following the docstring silently deletes a different object type.
- **Board (`GET /api/v1/board`)** is deliberately anonymous and publicly reachable (verified 200) with only a narrow `_redact_board_body` mask (vault host + "app-password"). See C-5.
- **Webhooks** (`/webhooks/*`) carry `X-Webhook-Secret` shared-secret checks; currently return 503 (feature/secret gated). `ringcentral` reflects a client header before the secret check (low-risk fingerprint/reflection).

Confirmed **not** vulnerable: SQL injection (all parameterized; two f-string builders are key-allowlisted), path traversal on vault routes (the multi-decode guard `main.py:242-273` is correct and strictly stronger than the `requests` sink), SSRF in `/api/v1/adsb` (float format specs), any header/IP tier grant.

---

## Runner / demo / second_brain (`src/runner`, `src/demo`, `src/second_brain`)

- **C-1 (Critical) — proxy dot-segment bypass.** `tailscale_gate` (`runner/main.py:333-350`) blocks `/api/dispatch/admin/*` and non-GET `/api/dispatch/api/v1/*` from untrusted origins by inspecting the **raw** path. The proxy then builds `url = f"{DISPATCH_BASE}/{path}"` and hands it to `httpx`, which **normalizes dot segments**. Confirmed: `httpx.URL("http://127.0.0.1:8000/./admin/vip").path == "/admin/vip"` and `.../api/v1/../../admin/vip` → `/admin/vip`. So `/api/dispatch/./admin/...` is allowed by the middleware but forwarded to `/admin/...`. nginx `proxy_pass http://127.0.0.1:8005;` (no URI part) forwards the unnormalized path, so this is reachable from the public hostname. No token is injected on the bypass path, but the dispatch-web endpoint it most dangerously reaches — `GET /admin/approval-requests/{id}/resolve` — is **deliberately unauthenticated and grants passwordless sudo**. **Confirmed live on the production runner :8001** once it restored from LOCKDOWN, from a forced-untrusted origin (`CF-Connecting-IP: 8.8.8.8`):

```
/api/dispatch/admin/version                                  -> 404  {"detail":"Not Found"}         (gate blocks)
/api/dispatch/./admin/version                                -> 403  {"detail":"Admin tier required"} (gate BYPASSED; reached dispatch-web auth)
/api/dispatch/./admin/approval-requests/000...000/resolve?action=allow
                                                             -> 404  {"detail":"approval request not found"} (reached the unauthenticated sudo endpoint)
```

The 403 (not 404) on the dot-segment admin path proves the request passed `tailscale_gate` and hit dispatch-web's own auth; the 404 "approval request not found" proves an unauthenticated, untrusted caller reached the passwordless-sudo resolver itself — an all-zero UUID that cannot resolve was used deliberately, so nothing mutated, but with a real pending request id, `action=allow` would have granted sudo. **Fix:** `posixpath.normpath` the path (and reject `.`/`..`) before the gate check.
- **C-2 (Critical) — `_is_trusted` header spoof.** `_is_trusted` (`runner/main.py:193-252`) trusts `CF-Connecting-IP` (early return) else `X-Forwarded-For`/socket against RFC1918+loopback+tailnet nets, with **nothing verifying the request actually came through Cloudflare**. nginx does not `set_real_ip_from`/`real_ip_header`, and :80 listens on 0.0.0.0. **Verified live** on :8001: no header → 200 (real operator chat history returned); `X-Forwarded-For: 10.x.x.x` / `192.168.x.x` / `172.x.x.x` / `100.64.0.1` / `127.0.0.1` → **200**; `X-Forwarded-For: 8.8.8.8` / `CF-Connecting-IP: 8.8.8.8` → 404. Spoofing trust unlocks: demo password gate, token injection for `api/v1/vault/file` (arbitrary vault read) + knowledge-graph, the C-1 admin gate, real receiver GPS via `/api/v1/frontend-config`, disabled signal sanitization, and `DELETE /api/chat/history`.
- **C-11 restated for runner:** wildcard CORS + IP-trust means a page the operator visits on the tailnet can read `/api/chat/history`, `PUT /api/v1/config`, and vault files cross-origin.
- **C-22 — DEMO_MODE inert on the live public demo.** `DEMO_MODE=os.getenv("DEMO_MODE","false")...` — unset everywhere (**live: `/api/demo/status` → `{"demo_mode":false,"authenticated":true}`**). The six gates it controls (password gate, ACARS/VDL2/HFDL sanitization, ntfy suppression) are all **inert**, failing *open*. The demo forwards to demo-api :8004 (isolated), which mitigates data exposure, but the gates the design relies on are off.
- **C-10 — recorder captures raw production unscrubbed.** `demo/recorder.py` pulls 13 endpoints with a **cert-tier token** and writes raw responses to `/var/lib/corporatetraveldc/demo.db` (**live: 2.6 GB**) with **zero `scrub_rules` calls** (deliberate per `:72-73` comment). All scrubbing is out-of-band in `scripts/scrub-demo-source.py`; the whole privacy claim rests on that one script running correctly, with a full production mirror sitting on disk indefinitely.
- **C-16 — `verify_scrubbed()` has no coordinate check** (only FORBIDDEN_LITERALS/email/phone/freq), and **`scrub_rules.py` itself hardcodes the operator's full PII dossier** (legal name, two emails, FCC+GMRS callsigns → ULS-searchable to a home address, Skywarn/ARES/CERT identifiers, EP venue matrix) in a tracked file (C-27). The "synthetic" demo tails are real allocatable N-numbers.
- **`scrub_gate.py` (second_brain)** is a real block gate but scans only two regexes (`SHARES|HEARS|HEART` co-occurring with a 3-digit freq; SSN shape) — **case-sensitive**, and the freq regex misses all HF SHARES frequencies. No emails/tokens/keys/coordinates/names blocked. Gate runs on body only; frontmatter (`source: os.getcwd()`, tags, author) is unscanned. `client_entity_ingest.py` doesn't import it at all.
- **`remember.py` arbitrary vault write (C-13 class):** `--dest-subdir` is unvalidated → `../../../` escapes `01-Sources/`; CLI-only today (the web route deliberately doesn't expose it).
- **`webdav_client.py`:** Basic auth over **plaintext `http://`** (loopback default OK; the module explicitly supports remote hosts, at which point the app password crosses the network in the clear). No credential logging (good).
- **SSRF (C-13):** runner `GET /api/rss/custom` (unauthenticated, `follow_redirects=True`, no host/IP denylist, reachable from public demo) can make the runner GET `127.0.0.1:*`, the tailnet, `169.254.169.254`, or nginx:80 with a spoofed CF header (chains into C-1/C-2). Two unauthenticated UDP ingest ports (`acars_watcher` :5005, `ais_watcher`) bound 0.0.0.0 with no source check flow to `/admin/push-alert` with the admin token, attacker controlling title/body/priority.
- **`__main__.py cmd_ask`** deliberately bypasses the inference integrity gate + slot-lock governance (documented, copyable pattern). `compile.py:736` executes SQL loaded verbatim from `ontology.json` (manifest-signed path, not remote).

---

## Poller / skills / common (`src/poller`, `src/common`)

- **C-5 — anonymous read of research/personal notes.** `second_brain_research_board_mirror.py:120-125` mirrors vault notes into `board_messages`, which `GET /api/v1/board` serves anonymously. **Live: the `research` thread already holds 6 messages** (subjects incl. "voice-profile: linkedin-full-history", "Uber & LinkedIn", "Device tracker-surveillance snapshot", homelab cost/infra research) — anon-readable via the public path (verified 200 with `X-CTDI-Public: 1`). The redaction masks the vault host but serves the substance. The `research-board-mirror.timer` is **not installed** (so *new* mirroring is dormant), but content is already exposed; installing the timer (the documented drift fix) also re-posts every note (no dedup, UUID per insert).
- **C-6 — ntfy false success.** `ntfy_push.py:144-163` returns `True` on HTTP 401/403 with no resend. **Live: `pusher-ntfy-ambiguous-status-dedup.json` holds 1,273 entries, oldest ~36 days** — 1,273 alerts classified "probably delivered" on an alerting platform. A wrong/revoked `NTFY_TOKEN` is indistinguishable from success.
- **C-7 — SR-2 permanent suppression.** `sr2_gate.py:43` writes the input hash and returns `"new"` *before* the caller does work. If the caller then crashes (e.g. Ollama down), the next identical-input run returns `"skipped"` forever until inputs change — including `tfr-enrichment`, the hot VIP/POTUS path — and it's logged at `debug` under `INFO`. Confirmed by simulation.
- **C-8 — cloud-egress kill switch fail-open.** `llm.py:383 ANTHROPIC_FALLBACK_ENABLED = os.getenv(..., "true")` — default **open**, evaluated once at import, and `llm.py` never imports `common.config` so it reads only the raw process env. **What actually prevents egress today is the absence of `ANTHROPIC_API_KEY`** (verified: set in neither env file), not the switch. Add the key on a manual/CI path and it egresses silently. (All 30+ in-code call sites pass `allow_anthropic=False`, so per-call gating is correct; the structural default is the risk.)
- **C-14 — cross-path NWWS deletion.** `db.expire_nws_alerts` (called from `fetchers/nws.py:173` with only REST ids) does `DELETE ... WHERE alert_id NOT IN (rest_ids)`, but push writes `nwws:*` ids that can never be in that set. **Live: 22 push rows vs 1 REST row** — the first REST poll after push goes stale wipes all 22, during exactly the incident window they matter.
- **Prompt injection:** untrusted RSS/feed data is interpolated into LLM prompts with no delimiting and (often) no length cap, instruction-last in 2 of 3 brief skills; outputs drive priority-5 pushes, EP threat-posture, and persisted DB rows re-fed into later prompts. `entity_tracking.py` closes an injection→SSRF→persistence loop (LLM-chosen hostname fetched with redirects, then written permanently into `user_rss_feeds.json`).
- **Correctness:** CPS go/no-go misses light/heavy precip (`-FZRA`/`+TSRA` stored with intensity prefix, compared bare — verified); `FLIGHTAWARE_AEROAPI_KEY` orphan name → dead OOOI branch; `aam_watch` marker-string mismatch splices raw headlines into hourly briefs; `ingest_feed_watch` stops re-alerting; sweep-chain exceptions logged at `debug` under `INFO` (invisible failures). No `subprocess shell=True`, no `verify=False`, every HTTP call has a timeout.
- **Unbounded growth (C-21):** `push_dedup` files never evict (notam 4,814 keys / 328 KB rewritten under flock per alert — verified); several module-level dicts grow unbounded; `api-usage.csv` 2.5 MB unrotated.

---

## Ingest / parsers (`src/ingest`)

- **C-3 (Critical) — SWIM TLS validation disabled.** `swim_client.py:436 tls = TLS.create().without_certificate_validation()` — **confirmed present in the running `localhost/corporatetraveldc-ingest:latest` image**. All six FAA feeds connect to `tcps://ems1/ems2.swim.faa.gov:55443` with basic auth and **no cert/hostname verification**, so `SWIM_NMS_USER_*`/`SWIM_NMS_PASS_*` are harvestable by any on-path attacker, and every parser's input becomes attacker-controllable. Fix is to pin the FAA CA, not disable validation.
- **XML hardening (none).** Every entry point uses stdlib `xml.etree.ElementTree` with no `defusedxml`, no size cap, no DTD rejection. Tested in the production image: XXE file-read **blocked**, external DTD **not fetched**, but **internal entity expansion is live** — billion-laughs is contained only by libexpat's ~100× amplification guard (with an 8 MiB activation floor). ITWS (documented 645 KB payloads) is the worst case. `itws_parser._sanitize_xml` explicitly lets `<!DOCTYPE`/`<!ENTITY` through and never touches entity references — false comfort. Combined with C-3, this is reachable under MITM.
- **Truthiness bugs:** the Element-truthiness class is eradicated (`_first_present` used correctly), but the **numeric-falsy** variant survives: `fdps_parser.py:804/997` drop altitude/FL `0` (ground level → NULL), `tbfm_parser.py:337` drops empty-string ETAs.
- **Logic bugs:** `fdps_parser.py:950` `gufi_override` is a **nonexistent key** → the GUFI arm can only false-match *every* watchlist entry (latent: real fixtures all populate GUFI) (C-19); `smes_parser.py:1042` dead-store clobbers the correctly-scoped STDDS taxi sector back to per-airport topics; Marine One fires nationwide priority-5 "POTUS MOVEMENT" on squawk alone for `source="FH"` (no distance check); NWWS all-cancellation products early-return so cancelled warnings never clear; AIS dedup has no time component; naive/aware timezone mixes silently mis-stamp `nas_programs` times or report trains "on time" on error.
- **Heartbeat "green light, no data" trap** is an explicit architectural choice (`failover.py:12-14`) and nothing compensates — 7 sites stamp health on connection/HTTP-200 rather than data flow (the exact pattern behind the prior ~4.8-day NWWS silence). `ingest/amtrak.py` is the one correct counter-example.
- **DB growth (C-21):** `corporatetraveldc.db` is **24.4 GB** (verified), driven by `flight_events` (842k rows) persisting the full raw FIXM XML per event (`fdps_parser.py:806`); `surface_movement_events` (121k), `watchlist_history`, `local_airspace_alerts`, `acars_messages` are append-only with no prune path; leaked asyncio tasks + client objects on every NWWS reconnect (`nwws.py:459-463`).
- **Race conditions:** non-atomic RMW on shared `feed_state` (`failover.py:47-50`) can clobber a healthy heartbeat back to stale — the value that decides REST failover; unlocked throttle check-then-act (`sector_coalesce.py`); several truncate-then-write state files with an external reader; a DB write from a signal handler (`amtrak_tracker`).

---

## Integrity chain, scripts & sudo (independently verified)

- **C-4 (High) — the signed-manifest chain does not pin the signing key.** `verify-manifest.sh:59-84` sets `PUBKEY="security/trusted-signing-key.pub.asc"` (a **tracked** file), imports it into a fresh temp keyring, and runs `gpg --verify` — with **no reference to a pinned fingerprint** (grep for `FINGERPRINT`/`signing.env`/`VALIDSIG` in the script = none). `verified-exec.sh` (the skill gate) just calls `verify-manifest.sh`. The fingerprint *exists* in `security/signing.env` (`SIGNING_KEY_FINGERPRINT`, `AGENT_SIGNING_KEY_FINGERPRINT`) but the verifier never consults it. **Consequence:** an attacker who can write tracked files — exactly the adversary the manifest defends against — can replace `security/trusted-signing-key.pub.asc` with their own public key and re-sign `MANIFEST.sha256`; verification then passes against the attacker's key. Trust is pinned only by the operator's out-of-band knowledge of the real fingerprint (`419A864C…903159`), which the automated gate does not enforce. **Fix:** verify with `--status-fd` and assert the `VALIDSIG`/`GOODSIG` fingerprint equals `SIGNING_KEY_FINGERPRINT` from `signing.env`.
- **Sudo surface.** `sudo -n -l` for the `corporatetraveldc` user shows `(ALL) ALL` (full root, password-gated — expected for the admin account) **plus** several NOPASSWD grants. Most are appropriately scoped (ollama/ollama-governor/argononed/cpupower/nginx-reload). Two are dangerous:
  - **`(root) NOPASSWD: /usr/bin/dnf remove *, /usr/bin/dnf autoremove`** — a passwordless wildcard package-removal. Any code-exec as this user (see the SSRF/injection chains) can `sudo -n dnf remove <anything>` — including `sudo`, `selinux-policy`, the kernel, or cloudflared — a passwordless destructive-root / brick-the-box primitive, and `dnf remove` runs package scriptlets as root.
  - **`(root) NOPASSWD: /usr/bin/semanage port -a *`** — passwordless wildcard SELinux port relabeling.
  These should be tightened to specific package/port arguments. `scripts/sudo-approval-gate.sh` itself is sound: it fails closed (denied/expired/unresolved all → do not run) and only wraps the two ollama grants behind an ntfy/approval round-trip.
- **Scrub pipeline (confirmed working).** `scrub-public-tree.py` `DROP_FILES` now includes `CLAUDE.md` (added 2026-08-24 after an incident), `scrub-public-tree.py` itself, and `INFRA_MAP.md`; 19 substitution rules cover hostnames/IPs/UUIDs/keys. Confirmed live: CLAUDE.md and the real scrubber are **absent from `public/main`**, and the value-scan of every public ref found no real secret. A separate `verify_scrubbed()` allowlist scan fails the push on any UUID-shaped string that isn't an explicit placeholder — a fail-closed backstop independent of DROP_FILES completeness.
- **Integrity-sweep behavior (expected, not a compromise).** The sweep fails only while unsigned edits sit on disk (2 docs files at audit time); the GPG signature itself verifies **Good** against the operator's rotated key `419A864C…903159`. This is the normal edit→sign cycle.

### db.py (SQL / schema / transactions / prune)

- **SQL injection: none.** Every `execute()` was enumerated; all f-string/`.format()` SQL interpolates only structure (identifier lists, `IN (...)` placeholder counts, `SET` keys) with values always bound. The one place caller-influenced *keys* reach SQL — `osint_update_scope` (`db.py:2664-2676`) — is gated by a hardcoded column allowlist. Correct but fragile.
- **Token auth (`lookup_token` `:931-938`):** expiry AND revocation enforced in the WHERE clause (`revoked_at IS NULL AND (expires_at IS NULL OR expires_at > unixepoch())`), mirrored consistently. `approval_requests` resolve is atomic (`UPDATE ... WHERE id=? AND status='pending' AND expires_at>?`) — race-safe.
- **C-32 (Med-High) — `board_consume_nonce()` (`:462-488`) is NOT atomic:** `SELECT` → check `consumed_at IS NULL` in Python → mint token → `UPDATE`. Two requests racing the same nonce both pass the check → **two valid board-write tokens from a single-use credential.** Fix: single conditional `UPDATE ... WHERE nonce_hash=? AND consumed_at IS NULL` + rowcount check (the pattern `resolve_approval_request` already uses 200 lines away).
- **`init_db_v36()` (`:4604-4672`) re-runs a data `UPDATE` on every startup** (called from web/poller/ingest main). The two `ALTER`s are idempotent-guarded; the `UPDATE nas_programs SET key_scheme=1 WHERE ... key_scheme IS NULL` is not — so any new REST GDP/GS row is silently re-stamped "legacy" at the next restart. Documented as intentional, but a semantic trap.
- **C-31 (High) — `faa_upsert_ladd()` (`:2852-2862`) wipes the whole FAA LADD table on an empty list**, with no empty-guard — unlike `expire_tfrs`/`expire_nws_alerts`, which explicitly `if not active_ids: return` for exactly this reason. The caller `faa_registry.py` returns `[]` (only a warning, no exception) if the FAA zip layout changes, silently un-suppressing every privacy-opted-out aircraft with zero alerting.
- **C-14 restated:** `expire_nws_alerts` deletes push-sourced `nwws:*` rows because it's called with only REST ids (see ingest section).
- **C-33 (Med) — no prune path** for `nas_programs` (explicitly required to retain, so unbounded), `train_events`, `board_messages`, `webhook_events`, `flight_ooooi_times`, `stdds_safety_status_history`, `local_airspace_alerts`, `international_aviation_feed`, `session_grants`, `board_enroll_nonces`/`board_tokens`. Tables that *do* prune: audit_log, notams, tfrs/nws_alerts, osint_items, wpc_discussions, registries, flight_events, watchlist_entries.
- **Concurrency:** `conn()` (WAL, 10s busy timeout, commit/rollback/close) is well-built; 12 SELECT-then-write functions run the read in autocommit (SQLite default `isolation_level`), of which `board_consume_nonce` (C-32) is the one genuinely exploitable race. `osint_delete_scope` cascades silently and returns unconditional `True`.

### Integrity chain & scripts (sub-audit, with my independent confirmations)

- **C-0a (Critical) — `.pyc` bytecode bypass of the manifest gate.** `sign-manifest.sh` hashes `git ls-files` (which never includes gitignored `__pycache__/*.pyc`); `verify-manifest.sh`/`verified-exec.sh` therefore validate `.py` source while Python executes `.pyc`. Reproduced by execution (a PEP-552 unchecked-hash `.pyc` runs attacker code while `sha256sum -c` reports the `.py` `OK`). **Live preconditions confirmed:** the running poller container's `/app` is **WRITABLE**, `PYTHONDONTWRITEBYTECODE` is **set nowhere**, and the quadlets carry no `ReadOnly=`/`NoNewPrivileges=`. Any filesystem-write foothold in a skill container plants persistent code the gate can't see. **Fix:** `PYTHONDONTWRITEBYTECODE=1` platform-wide + strip `__pycache__` from images, or cover `.pyc` in the manifest and refuse unlisted ones.
- **C-0b (Critical) — `grant-agent-session.sh` Python injection.** Its four call sites splice shell vars into `python3 -c "..."` literals calling `db.create_session_grant`/`revoke_session_grant` — the functions gating the passphrase-less agent signing key's exemption path. A `'` breaks out → code execution with a direct path to forging/revoking manifest-signing trust. "Human-run only" today, but any wrapping automation or an apostrophe in a reasoning string reaches it.
- **C-0c (High) — `pre-push` guard bypass.** `pre-push:26-38` checks only the remote *name* (`$1 == "public"`); the URL git passes as `$2` is never referenced (**confirmed live: `grep '$2' pre-push` = 0**). `git push <public-url> HEAD:main` skips the guard and the scrub step entirely.
- **C-4 (High) — no pinned trust root** (also established independently, see below): `verify-manifest.sh:76` imports and trusts *whatever* key(s) are in the tracked `security/trusted-signing-key.pub.asc` (live: two keys — operator `419A864C…` and the passphrase-less agent key `CC1509BD…`) with no fingerprint assertion against `signing.env`.
- **C-30 (High) — OOXML scrubber blindness** (confirmed live above): 17 public `.docx`/`.pptx`, several with the operator's real name + domain in compressed XML the byte-level scrubber never decompresses. `BINARY_SKIP_EXTENSIONS` covers `.pdf/.zip/…` but not `.docx/.pptx`, so they take the raw-blob path — scrubbed and verified in form, blind in substance.
- **C-34 (Med) — `.git/hooks/*` untracked/unmonitored** and outside the manifest — `post-commit` is already stale vs its `scripts/` source live; `pre-commit` (the credential scanner) and `pre-push` (the mirror guard) can drift the same way silently.
- **Scrubber case-sensitivity (Med):** `SUBSTITUTIONS`/`REGEX_SWEEPS`/`FORBIDDEN_LITERALS` are exact-case; a differently-cased identifier (`ops.CSExecutiveServices.com`, `COREY SHELDON`, `TAIL03C8AD`) bypasses both scrub layers.
- **Sudo (independently enumerated, above):** `(ALL) ALL` + NOPASSWD wildcard `dnf remove *` / `semanage port -a *`. `sudo-approval-gate.sh` itself is sound (fails closed). Several root scripts `source` the deliberately-unquoted `dispatch.env` (shell-hostile by design); not currently exploitable (file is `0640`, owner-only writable) but a real defense-in-depth gap. `threat-initiate.sh` reports "[OK] banned" regardless of `firewall-cmd`'s exit status (silent IR failure) — the same unconditional-success anti-pattern seen across the automation scripts.

*(This section merges a dedicated `db.py`+`scripts/` sub-audit with my own direct reads and live enumeration; the C-0a/C-0c/C-30 preconditions were re-confirmed live during this pass.)*

---

## What was checked and found sound

- Core tier auth: no forged/garbage/SQLi token obtained any tier; no header/IP/network grant; `X-CTDI-Public` is downgrade-only.
- Public git mirror: clean of real credentials/usernames/UUIDs/coordinates across every ref's tree and full history; scrub pipeline drops the real scrubber + CLAUDE.md and publishes only sanitized substitutes. **Caveat: `.docx`/`.pptx` are not decompressed by the scrubber (C-30)** — they carry real name/domain into public, plausibly by intent for investor decks but unverified by the control.
- No `shell=True`, `os.system`, `eval`, `exec`, `pickle`, or `yaml.load` anywhere in the audited tree; every HTTP call carries a timeout; `verify=False` appears nowhere in application code (the SWIM cert-disable is via the Solace TLS builder, C-3).
- Vault path-traversal guard (`main.py:242-273`) is multi-decode and strictly stronger than its `requests` sink.
- No private-key material or live secret file is tracked; `dispatch-secrets.env` is git-ignored.
- fail2ban + honeypot + Cloudflare Access (on sensitive public hostnames) + rate-limiting scaffolding are present.
- Test suite: 222 pass / 1 known-unrelated pre-existing failure.

---

## Top remediation order

1. **C-1** — `posixpath.normpath` + reject `.`/`..` in the runner proxy before `tailscale_gate` (remotely exploitable today, fronts passwordless sudo).
2. **C-3** — pin the FAA CA and re-enable SWIM TLS validation (`swim_client.py:436`); six real cred sets are MITM-exposed.
3. **C-0a** — `PYTHONDONTWRITEBYTECODE=1` platform-wide + strip `__pycache__` from images (or cover `.pyc` in the manifest); make skill-container `/app` read-only. Closes the bytecode bypass of the entire integrity gate.
4. **C-2** — stop trusting client `CF-Connecting-IP`/`X-Forwarded-For`; resolve real IP in nginx and have the app trust only `request.client.host`.
5. **C-0b / C-0c** — parameterize `grant-agent-session.sh`'s python calls (no shell splice); make `pre-push` check the remote URL (`$2`), not just the name.
6. **C-4** — assert the signature fingerprint against `signing.env` in `verify-manifest.sh`.
7. **C-11** — drop wildcard CORS to an explicit origin list.
8. **C-13 / SSRF** — gate `/api/rss/custom`, add private-range/redirect guards to the feed-URL fetchers, restrict the two 0.0.0.0 UDP listeners.
9. **C-6 / C-7 / C-8 / C-9** — the fail-open cluster: stop returning success on ntfy 401/403; write the SR-2 hash only after success; default the egress switch closed; audit failed authorization and stop persisting raw bodies.
10. **C-14 / C-31** — scope `expire_nws_alerts` to REST-sourced ids; add an empty-guard to `faa_upsert_ladd`.
11. **C-30 / C-16 / C-24 / C-27** — decompress-and-scrub (or drop) `.docx`/`.pptx`; add coordinate detection + case-insensitivity to the scrub layers; cover the hardcoded GPS defaults; stop hardcoding the PII dossier in `scrub_rules.py`.
12. **C-32** — make `board_consume_nonce` an atomic conditional UPDATE.
13. **C-21 / C-33 / C-18** — add prune paths (esp. `flight_events` raw XML, `nas_programs`, `push_dedup` files) and recalibrate the LOCKDOWN fallback-count trigger.
14. Tighten the two wildcard NOPASSWD sudo grants; track `.git/hooks/` (C-34); revoke stale never-expiring admin tokens (C-28).

