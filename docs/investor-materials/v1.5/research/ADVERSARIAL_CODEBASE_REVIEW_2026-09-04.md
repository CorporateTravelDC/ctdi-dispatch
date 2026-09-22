# Adversarial Codebase Review — Source-Led, Live-Grounded (2026-09-04)

> **What this is.** An **adversarial code review** of this platform's own
> production system, run on the operator's own hardware (the
> `corporatetraveldc-dispatch` Pi 5) against the operator's own repo, at the
> operator's direction. It is deliberately **not** a re-run of an endpoint
> checklist. The modules below were read start-to-finish and reasoned about the
> way a security engineer reasons about code — hunting for logic errors, auth
> bypasses, injection points, race conditions, and unsafe patterns — and live
> probing was used only to **ground and confirm** what the source revealed.
>
> Successor to `docs/LIVE_VALIDATION_AND_PENTEST_2026-08-13.md` (the deeper
> methodology bar: real source reading + live probing) and to the four
> 2026-08-24 passes, of which
> `docs/investor-materials/v1.5/research/ADVERSARIAL_REVERIFICATION_2026-08-24.md`
> is the direct predecessor. Its default posture is inherited: **every
> "resolved" label is wrong until skeptically disproved.**
>
> **Scope discipline (unchanged from every prior pass):** non-destructive only;
> no fuzzing, brute-force, or high-volume scanning; **1–2 requests per endpoint,
> no retries-as-load**; nothing on the live system was modified, restarted, or
> stopped. **No secret, token, key, password, or real sensitive value is printed
> anywhere below** — only that a check was made and its outcome. Destructive
> capabilities were identified by code + reachability and **never invoked**.
> Vault content was never read or printed; existence/metadata checks only.
>
> **Verdict vocabulary:** `STILL-RESOLVED` (adversarial pressure did not reopen
> it) / `REOPENED` (a previously-closed item is open again) / `NEW-FINDING` (not
> raised by any prior pass) / `NEEDS-HUMAN-REVIEW` (genuinely uncertain, or an
> operator policy call).

Snapshot: **2026-09-04, ~05:00–05:40 EDT**, against working tree at HEAD
`b623db9` (with a staged, not-yet-signed change set present — see NF-12).

---

## HEADLINE

**The single most consequential item is closed. The 08-13 CRITICAL vault /
knowledge-graph exposure is FIXED, at two independent layers, and I verified
both of them live today.** Every one of the three endpoints
(`/api/v1/vault/file`, `/api/v1/knowledge-graph/html`,
`/api/v1/knowledge-graph/meta`) now carries an explicit `require_tier(Tier.T1)`
dependency in code, that code is confirmed byte-identical inside the running
container, the endpoints return **403** to an unauthenticated loopback request
(including with the `X-CTDI-Public` pin set), and the public hostname now
returns a **Cloudflare Access login redirect** rather than data. Six weeks ago
the same requests returned 200 with no gate at all.

**No prior finding reopened.** Runner-demo isolation, tier-auth rejection,
token-hash-only storage, clean git history, the signed-manifest execution gate,
the board-key gate, and the LADD Tier-0 mask all held under pressure.

**Twelve new findings, none of them a remote authentication bypass, but three of
them materially contradict claims currently in the v1.5 investor materials:**

1. **NF-1** — the `board_refresh_grace` table added 2026-09-03 stores a
   **plaintext, live board-write token** and has **no retention prune**, so the
   "credentials exist only as one-way hashes / no plaintext column" claim is now
   incomplete.
2. **NF-8** — the thermal remediation the operator believes is deployed
   (ingest `CPUWeight` 9500 → 7500) is **not in effect at runtime**; stale
   `systemctl set-property` drop-ins are silently pinning it at 9500.
3. **NF-5** — the signed-manifest fingerprint pin does not actually defend
   against the adversary its own code comment names, because the pin file is
   `source`d as shell before verification and is excluded from the runtime
   gate's scope.

**The LLM narrative regression is partially remediated, not fully.** Measured
over the last 7 days: `ops-brief` **46.5%** LLM-narrative, `ep-advance`
**57.6%** — recovered from 08-13's 0%, but well short of the heritage "~87%"
figure and materially worse than the "41.7% deterministic fallback" currently
disclosed in the materials.

---

## Part A — The vault / knowledge-graph exposure (highest priority)

### A-1. Current status — **STILL-RESOLVED**, verified at two independent layers

**Stated unambiguously: the second-brain vault and knowledge graph are NOT
anonymously readable from the public internet as of 2026-09-04. The 08-13
CRITICAL finding is closed.**

**Layer 1 — code.** All three endpoints now carry a tier dependency. The fix is
in-line and self-documenting:

- `src/web/main.py:1564-1567` — `knowledge_graph_html(tier: Tier = Depends(require_tier(Tier.T1)))`
- `src/web/main.py:1592-1595` — `knowledge_graph_meta(tier: Tier = Depends(require_tier(Tier.T1)))`
- `src/web/main.py:1627-1631` — `vault_file(path: str = Query(...), tier: Tier = Depends(require_tier(Tier.T1)))`

`src/web/main.py:1571-1576` records the remediation in the code itself:

> `2026-08-13: tier-gated after a live pentest pass found this endpoint (and
> knowledge_graph_meta/vault_file below) had NO auth check at all -- every other
> endpoint touching anything sensitive in this file uses require_tier/
> require_admin, these three didn't.`

**Layer 1 is actually deployed** — this is the check that matters on this box,
where file-vs-running-container drift is the recurring trap. `sha256sum` of
`/app/src/web/main.py` inside the running `systemd-corporatetraveldc-web`
container is **identical** to the repo's `src/web/main.py`
(`319b6611f42a19ce…`), as is `src/auth/auth.py` (`2f4f499cccd3f47b…`) and
`src/common/db.py` (`ede855322144b8bf…`). Container created 2026-09-04
03:59:40 EDT. No drift.

**Live behavior (loopback, 1 request each, no auth):**

| Endpoint | Result |
|---|---|
| `/api/v1/knowledge-graph/meta` | **403** |
| `/api/v1/knowledge-graph/html` | **403** |
| `/api/v1/vault/file?path=<nonexistent>` | **403** |
| `/api/v1/knowledge-graph/meta` + `X-CTDI-Public: 1` | **403** |
| `/api/v1/vault/file` + `X-CTDI-Public: 1` | **403** |
| `/healthz` (control, intended Tier-0) | 200 |
| `/api/v1/opsplan` (control, intended public FAA data) | 200 |

**Layer 2 — Cloudflare Access, restored.** This is the half that was
**absent** on 08-13 and is the more consequential change. Over the real public
internet (HTTPS, no cookies, no credentials, 1 request each):

| Public URL | 08-13 | 2026-09-04 |
|---|---|---|
| `https://dispatch.example.com/healthz` | 200 | **302** |
| `…/api/v1/knowledge-graph/meta` | **200 (data served)** | **302** |
| `…/api/v1/knowledge-graph/html` | (exposed) | **302** |
| `…/api/v1/vault/file?path=…` | (exposed) | **302** |

The 302 `Location` resolves to
`https://csexecutiveservices-corporateinfra.cloudflareaccess.com/cdn-cgi/access/login/dispatch.example.com?kid=…`
— an authentic Cloudflare Access login redirect. The 08-13 statement that *"the
documented 'CF Access gated' property does not hold for this hostname"* is **no
longer true**; it holds today.

I did **not** read or print any vault content to establish this — only status
codes, a nonexistent probe path, and the redirect target, exactly as the
original pass constrained itself.

**Verdict: STILL-RESOLVED.** Defense in depth is real here: either layer alone
would close it, and both are live. The corresponding investor-material claims
(executive-protection `:52`, platform-generic exec `:63`, due-diligence FAQ
`:34`) are **accurate as written**, including the specific "two independent
layers … confirmed by live external request" phrasing.

### A-2. Sibling vault surfaces re-attacked — **STILL-RESOLVED**

`/api/v1/vault/research` (`src/web/main.py:1673`) and
`/api/v1/vault/research/list` (`:1745`) are deliberately tier-less but
board-key gated via `_require_board_key(request)` at `:1709` and `:1756`.
Live, unauthenticated: both return **401** (not 400, not 200) — **auth is
enforced before path validation**, so an attacker cannot probe the traversal
guard without the credential. Matches 08-24 N-1.

The traversal guard itself (`_vault_path_is_safe`, `src/web/main.py:295-326`)
was re-read adversarially and is **sound**. It decodes up to 5 times in a loop
until stable, then re-asserts no `..`, leading `/`, or backslash survives, then
`posixpath.normpath()`s and re-checks. The attack it defends against —
double-encoded `%252e%252e` surviving Starlette's single decode and being
re-decoded by `requests.utils.requote_uri()`'s `quote(unquote(...))` — needs at
most 2 total decode rounds; the guard performs 6. **The guard is strictly
stronger than the decode chain downstream of it.** No bypass found.

Scope enforcement (`_vault_research_path_allowed`, `:289-292`) correctly
requires the trailing separator (`_VAULT_RESEARCH_ROOT + "/"`), so a sibling
folder named e.g. `…/Series-Private/` cannot match `…/Series/`. All four
`_VAULT_RESEARCH_EXTRA_PREFIXES` (`:280-285`) likewise terminate in `/`.

---

## Part B — Prior findings re-attacked

### B-1. Tier resolution and the `X-CTDI-Public` pin — **STILL-RESOLVED**

`src/auth/auth.py:61-84` (`resolve_tier`) was traced through every path:

- The public pin is checked **first**, before any token lookup
  (`auth.py:69-70`), so a tunnel-borne request can never elevate regardless of
  the token presented. Confirmed live (403 with the pin set on both a Tier-1 and
  a formerly-exposed route).
- The pin is set with a **literal** `proxy_set_header X-CTDI-Public "1";` in
  **both** location blocks of `nginx/conf.d/dispatch.example.com.conf`
  (`:33` and `:46`). `proxy_set_header` **replaces** rather than appends, so a
  client-supplied value cannot survive. This is the correct primitive, and the
  contrast is called out in the config's own header comment (`:4-17`) against
  `X-Forwarded-For`'s deliberately-appending `$proxy_add_x_forwarded_for` — the
  exact append-not-replace behavior that made the retired
  Tailscale-XFF-prefix model spoofable. **I specifically checked for the
  multi-header "first occurrence wins" nuance the 08-24 pass found on
  `CF-Connecting-IP`; it does not apply here, because replacement leaves only
  one occurrence.**
- Token resolution is a SHA-256 hash lookup (`auth.py:57-58`, `:73-74`), not a
  string comparison, so there is no meaningful timing side-channel and no
  partial-match or type-confusion path: `record["tier"]` is compared against
  three exact literals (`"admin"`, `"shares"`, `"cert"`, `auth.py:77-82`) and
  anything else falls through to `Tier.T0`. **Fail-closed by construction.**
- `require_tier` (`auth.py:126-138`) orders `[T0, T1, T2, ADMIN]` and compares
  by index. `resolve_tier` can only return enum members, so the `order.index()`
  call cannot raise.

**One design observation, not a defect:** the ordering makes `T2`/`shares`
strictly *more* privileged than `T1`/`cert`, so a "shares" token satisfies every
`require_tier(Tier.T1)` route — including the newly-gated vault and
knowledge-graph endpoints. If "shares" tokens are ever issued to a less-trusted
audience than "cert" tokens, that inversion becomes a real escalation. Flagged
for the operator; not exploitable today (4 active tokens, all operator-held).
→ **NEEDS-HUMAN-REVIEW**.

### B-2. Token storage is hash-only — **STILL-RESOLVED for `auth_tokens` / `board_tokens`** (but see NF-1)

- `src/common/db.py:127-137` — `auth_tokens` has `token_hash TEXT UNIQUE NOT
  NULL`, `token_prefix` (display only), tier/labels, `expires_at`, `revoked_at`.
  **No plaintext column exists.**
- `src/common/db.py:425-433` — `board_tokens` keys on `token_hash TEXT PRIMARY
  KEY`. No plaintext column.
- `src/auth/auth.py:279-310` — `generate_token()` builds the plaintext, stores
  only its hash, returns plaintext once. Entropy is 32 chars from a 36-symbol
  alphabet via `secrets.choice` (`auth.py:295-296`) ≈ **165 bits**. Sound.
- **Stronger than 08-13 recorded:** `lookup_token()` (`db.py:1130-1137`) filters
  `revoked_at IS NULL AND (expires_at IS NULL OR expires_at > unixepoch())`, so
  expiry and revocation are enforced *at resolution time*, not merely stored. I
  specifically hunted for the failure mode where `expires_at` is written at mint
  but never checked at lookup — it is checked.

No token or hash *value* was printed in performing this check.

### B-3. Git history is clean of credentials — **STILL-RESOLVED**

A full-history scan (`git log --all -p`) for `sk-ant-`, `AKIA…`, `ghp_…`, and
PEM private-key headers returned **83 raw matches** — which, on a naive read,
would contradict 08-13's "0 matches." I classified every one rather than trust
either number. Restricting to **added** lines and grouping by file:

| File | Hits | What they actually are |
|---|---|---|
| `scripts/pre-push` | 17 | detector patterns in the hook's own denylist |
| `scripts/pre-commit` | 10 | detector patterns in the hook's own denylist |
| `src/runner/main.py` | 8 | placeholder-rejection logic (`startswith("sk-ant-") and len(...) >= 40`) |
| `dispatch-secrets.env.example` | 7 | `ANTHROPIC_API_KEY=sk-ant-...` placeholder |
| `SESSION_STATE.md` | 5 | setup instructions + the denylist description |
| `scripts/pre-commit-README.md` | 3 | documentation table |
| `docs/LIVE_VALIDATION_AND_PENTEST_2026-08-13.md` | 3 | the prior pass quoting its own grep |
| `dispatch-secrets.env.template` | 1 | placeholder |

**Zero real credentials.** Every hit is a detector pattern, a placeholder, or a
document referencing the pattern. 08-13's conclusion holds; the higher raw count
is an artifact of the repo having since grown its own credential-scanning
tooling, which necessarily contains the strings it scans for.

### B-4. Pre-commit hook (08-13 item 5, the one CONFIRMED gap) — **CLOSED**

08-13 found `SECURITY.md:66`'s claim — *"a pre-commit hook rejects staged
credentials"* — was not true on this clone: `.git/hooks/pre-commit` did not
exist and `core.hooksPath` was unset. Re-checked today:

```
-rwxr-xr-x  pre-commit   6516 bytes  Aug 26 20:16
-rwxr-xr-x  pre-push    11749 bytes  Aug 26 20:20
-rwxr-xr-x  post-commit  3794 bytes  Sep  2 13:43
```

Both hooks are now present and executable. `core.hooksPath` remains unset, which
is correct — the hooks live in the default `.git/hooks/` location. **The
SECURITY.md claim is now accurate**, and the due-diligence FAQ's statement
(`:43`, *"pre-commit/pre-push credential hooks now actually installed (closing
an 08-13 finding)"*) is **verified true**.

### B-5. Runner-demo isolation — **STILL-RESOLVED**

Re-verified against the running container, not the file:

- Repo `.container` vs installed copy: **byte-identical**.
- Mount line: `Volume=/var/lib/corporatetraveldc-demo:/var/lib/corporatetraveldc:z`.
- `podman inspect` live mounts confirm `/var/lib/corporatetraveldc-demo ->
  /var/lib/corporatetraveldc (rw)`.
- Inside the container, `/var/lib/corporatetraveldc/` contains **only** a
  16 KB `dispatch-chat.db`. Direct existence probes: `corporatetraveldc.db`
  → **absent**; `second_brain_index.db` → **absent**.
- Inodes still diverge: demo `123317778`, prod `710109` — **different files**,
  exactly as 08-24 established.

The mount-layer containment property 08-24 identified — that even a *hardcoded
absolute* `/var/lib/corporatetraveldc/...` path inside the demo lands in the
isolated sibling — still holds. **Verdict: STILL-RESOLVED.** (One newly-observed
mount is a separate item; see **NF-9**.)

### B-6. Signed-manifest execution gate — **STILL-RESOLVED, and demonstrably enforcing right now**

`scripts/verify-manifest.sh` was re-read end to end. The core contract is real:

- Signature verified in an **isolated** GPG keyring (`mktemp -d`, `chmod 700`,
  `export GNUPGHOME`, `verify-manifest.sh:81-89`), never the ambient one.
- Signer fingerprint asserted against both pinned fingerprints, accepting either
  the signing subkey or its primary (`:124-137`) — the 2026-08-27 correction for
  normal subkey delegation is present and correct.
- Fails closed on every path: missing files → `exit 2` (`:69-74`); bad signature
  → `exit 1` (`:93-98`); unpinned key → `exit 1` (`:133-137`); a target matching
  nothing in the manifest → `exit 1` (`:168-171`); any checksum mismatch →
  `exit 1` (`:145`/`:177`).
- `scripts/verified-exec.sh:35-42` captures the check's output, tests the exit
  code, and `exit 1`s **without ever reaching `exec "$@"`** on failure. This is
  a genuine hard gate, not log-and-continue.

**Live evidence that the gate is actively enforcing:** running the scoped check
that `verified-exec.sh` itself runs returns `INTEGRITY FAILURE` on
`src/second_brain/doc_generation.py`, and three units are in a `failed` state
right now for that reason (`corporatetraveldc-entity-tracking-digest`,
`corporatetraveldc-integrity-sweep`, `corporatetraveldc-transport-pattern-digest`).

I confirmed this is **benign unsigned drift, not tampering**: the file is staged
(`M`, +458/−2 lines vs HEAD), its mtime is **00:56 EDT** while
`MANIFEST.sha256.asc` was signed at **00:15 EDT** — i.e. edited 41 minutes after
signing. Same self-resolving pattern documented repeatedly in `CLAUDE.md`.
→ Classified **LIKELY FALSE POSITIVE** as an integrity problem, and **positive
evidence** for the control: the system is refusing to run unsigned code, live,
unprompted, which is exactly the claim. See **NF-12** for the honest cost.

Note this differs from 08-13's equivalent observation in one important way: on
08-13 the failure was a `docs/` file failing only the **collective** check, which
the runtime guard never runs. Today's failure is a file **inside `src/`**, so it
fails the **scoped** check too — it genuinely gates execution.

### B-7. SQL construction — **STILL-RESOLVED, no injection found**

Every dynamically-constructed query in `src/common/db.py` (6,610 lines) was
enumerated and each site read individually. All eleven are safe:

- **Placeholder generation only** (`",".join("?" * len(...))`) at `:826`,
  `:2793`, `:4232`, `:4312`, `:6407`, `:6498` — values always bound.
- **Allowlisted column names**: `osint_update_scope` (`:2963`) filters `kwargs`
  against an explicit `allowed` set (`:2957-2959`) *before* building the
  `SET` clause; `watchlist_entries` (`:2145`) builds `sets` only from
  hardcoded literal strings.
- **Internal constants**: `_mark_and_sweep`'s `{table}` (`:3210`, `:3226`) is
  never request-derived; `ALTER TABLE … ADD COLUMN {col}` (`:4740`) iterates a
  hardcoded tuple.

No string-formatted user input reaches SQL anywhere in this module. The board
`thread` parameter, the vault `path` parameter, and the token hash are all
bound parameters.

### B-8. LADD Tier-0 masking (the 2026-08-31 CUI fix) — **STILL-RESOLVED**

`src/web/main.py:2084` — `get_aircraft(identifier, tier: Tier = Depends(resolve_tier))`,
with `ladd_visible = tier != Tier.T0` (`:2150`) applied at **both** return sites
(`:2197`, `:2217`). A Tier-0 caller always sees `"ladd": False`, never the true
flag. The fix is correctly applied on every path out of the handler, which is
where this class of masking usually leaks.

### B-9. Board-key gate re-attacked — **STILL-RESOLVED** (but see NF-4)

`_require_board_key` (`src/web/main.py:201-212`) uses
`secrets.compare_digest(presented, _BOARD_KEY)` OR `db.board_token_valid(presented)`.
`board_token_valid` (`db.py:531-555`) SHA-256-hashes the presentation and does a
primary-key lookup filtered on `expires_at > now`. A bogus ASCII key returns
**401** live. No bypass found on the happy-path-adjacent attacks 08-24 ran.

The **footgun guard** at `db.py:534-545` deserves credit: it explicitly
documents that `board_token_valid` is scope-blind by design and that adding a
second scope without making this check scope-aware *is* a privilege-escalation
bug. I verified the precondition still holds — `board_consume_nonce` (`:526`)
and `board_refresh_token` (`:664`) both mint only `"board-write"`. **One scope
exists; the guard's assumption is intact.**

---

## Part C — New findings

### NF-1. `board_refresh_grace` stores a plaintext live token with **no retention prune** — *moderate; contradicts a current investor claim*

The 2026-09-03 grace-relay feature (`commit 2dddf7d`) is a deliberate,
documented exception to "never persist a usable secret at rest"
(`src/common/db.py:448-465`). The design intent states the exposure is bounded:

> `grace_expires_at bounds how long that plaintext is recoverable; rows past it
> are treated as absent and opportunistically deleted` — `db.py:456-457`

**That is true for the relay API and false for at-rest persistence**, and the
gap is a real bug, not a wording quibble:

- The **only** cleanup is `DELETE FROM board_refresh_grace WHERE
  grace_expires_at < ?` at `db.py:631`, which runs **inside
  `board_refresh_token()` and only when `presented` is non-empty** (`:627`).
- `prune_expired_board_auth()` (`db.py:1042-1055`) — the scheduled retention job
  wired into `src/poller/skills/retention_prune.py:30` — deletes from
  `board_enroll_nonces` and `board_tokens` and **does not mention
  `board_refresh_grace` at all**. Confirmed by grep: the table appears in
  `db.py` only, at lines 459, 615, 631, 633, 676.

Consequence: if refresh traffic stops — which is precisely the scenario the
feature exists to survive (a stranded session) — the last-minted token's
**plaintext persists indefinitely** with nothing to sweep it. And the window in
which that plaintext is a *live, usable credential* is not
`_BOARD_REFRESH_GRACE_S = 120` (`db.py:595`) but
`_BOARD_TOKEN_TTL_S = 86400` (`db.py:404`) — **24 hours, not 2 minutes** —
because the row stores `new_token`, whose validity is governed by its own TTL.

**Live state:** `SELECT COUNT(*) FROM board_refresh_grace` → **0 rows**. So this
is a **latent design gap, not a live exposure today.** Stated fairly: there is
no plaintext credential sitting in the production database at this moment.

**Investor-materials impact — this is the part that matters.** The
due-diligence FAQ (`:43`) states *"tokens stored as SHA-256 hashes only, no
plaintext column"*; executive-protection (`:54`) states *"Credentials are stored
only as one-way hashes (the token table has no plaintext column)"*. Those were
accurate when written and are **now incomplete**: `board_refresh_grace.new_token
TEXT NOT NULL` (`db.py:461`) is a plaintext credential column, added after the
last verification pass. The claim needs either a carve-out sentence or the
prune gap closed first.

→ **NEW-FINDING.**

### NF-2. The grace relay bypasses the presence-attestation gate — *low; contradicts an explicit in-code contract*

`board_refresh_token()` documents a two-factor requirement:

> `Requires BOTH: (1) the presented token itself still valid/unexpired, AND
> (2) a currently-valid weekly presence attestation.` — `db.py:601-603`

and the route docstring is more emphatic:

> `once that 7-day window lapses, refresh fails closed (403) regardless of the
> presented token's own validity` — `src/web/main.py:462-464`

**Neither statement holds inside the grace window.** Reading the actual control
flow at `db.py:627-643`: the grace lookup runs **first**, and on a hit it
`return`s a 200 with the new token at `:639-643` — **before**
`board_token_valid()` at `:644` and **before** `board_presence_status()` at
`:648`. So for 120 seconds after any successful rotation, re-presenting the
just-superseded token yields a live 24-hour credential **even if the weekly
human presence attestation has lapsed in the interim**.

Narrow (120 s, requires possession of the old token, which the actor already
had), but it is a genuine deviation from a security contract the code states
twice in absolute terms. → **NEW-FINDING.**

### NF-3. `board_refresh_token()` has the double-mint race that was already fixed for nonces — *low-moderate*

This repo has already found and fixed exactly this bug class once. `db.py:488-496`
documents the 2026-08-26 fix (Opus blind review C-32) to `board_consume_nonce`:

> `this used to SELECT the row, check consumed_at/expires_at in Python, then
> separately INSERT the token and UPDATE consumed_at -- two concurrent requests
> against the same nonce could both pass the Python check before either write
> landed, minting two valid board-write tokens from one single-use nonce.`

The fix was to make an atomic conditional `UPDATE` with a `rowcount` check the
single operation that decides the winner (`:505-510`).

**`board_refresh_token()`, added a week later, reintroduces the original
pattern.** `board_token_valid(presented)` at `db.py:644` opens **its own
connection** (`:550`), returns, and only then does a **separate** `with conn()`
block (`:656`) perform the mint. There is no atomic claim, no `rowcount` guard,
and no `WHERE expires_at > ?` on the superseding `UPDATE` at `:674`. Two
concurrent refreshes presenting the same valid token both pass the check and
both mint — yielding **N valid board-write tokens from one rotation**, with only
the last one's plaintext surviving in the grace table (`INSERT OR REPLACE`,
`:676`), so the earlier extra tokens are live but unrecoverable and untracked.

Requires a valid token to exploit and produces credential multiplication rather
than unauthorized access — but it is an un-mitigated instance of a bug class
this codebase has already identified, fixed, and written a docstring about.
→ **NEW-FINDING.**

### NF-4. Non-ASCII credential header returns **HTTP 500** instead of 401 — *low; live-confirmed*

`secrets.compare_digest()` raises `TypeError: comparing strings with non-ASCII
characters is not supported` when either `str` argument contains a codepoint
> 127. Starlette decodes inbound header bytes as **latin-1**, so any header byte
above `0x7F` produces exactly such a string.

Two call sites take an attacker-controlled header value directly:

- `src/web/main.py:208` — `_secrets.compare_digest(presented, _BOARD_KEY)`,
  where `presented = request.headers.get("X-Board-Key", "")` (`:206`).
  Reaches `/api/v1/vault/research`, `/api/v1/vault/research/list`, and
  `POST /api/v1/board`.
- `src/web/routes/webhooks.py:58` — `secrets.compare_digest(provided, expected)`
  on `X-Webhook-Secret`. Reaches all three webhook receivers, once their
  respective secrets are configured (before that, `:50-54` returns 503 first).

**Confirmed live** (loopback, `/api/v1/vault/research/list`, 1 request each):

| `X-Board-Key` value | Result |
|---|---|
| ASCII bogus string | **401** (correct) |
| single non-ASCII byte | **500** |

This is **not an auth bypass** — the exception propagates, so no access is
granted; it fails closed. The impact is (a) an unauthenticated caller can
reliably drive unhandled 500s on credential-gated endpoints, (b) any monitoring
or fail2ban rule keyed on 401s silently misses these attempts, and (c) it is a
robustness defect in a security-critical comparison path. Fix is a one-line
`.encode()`/`try` guard at both sites. → **NEW-FINDING.**

### NF-5. The manifest fingerprint pin does not defend against the adversary it names — *moderate; design-level*

`scripts/verify-manifest.sh:46-51` states the purpose of the 2026-08-25 C-4 fix
plainly:

> `verifying against "whatever key is in the tracked pubkey file" alone trusts
> exactly the adversary this manifest is meant to defend against: someone who
> can write tracked files can replace trusted-signing-key.pub.asc with their own
> key and re-sign MANIFEST.sha256, and the old check would have passed it clean.`

The pin's protection is only as strong as the file holding the pinned
fingerprints. Three facts, each verified, collapse it:

1. **The pin file is `source`d as shell before any verification happens.**
   `verify-manifest.sh:77` — `source "${SIGNING_ENV}"`, where `SIGNING_ENV` is
   `security/signing.env`. This executes arbitrary shell as the invoking user,
   inside the very script meant to detect tampering, *before* the signature is
   checked at `:93`.
2. **It has the same write permissions as the pubkey it protects.** Both are
   `-rw-r--r--`, owner `corporatetraveldc`. An attacker who can write
   `trusted-signing-key.pub.asc` can, by construction, also write
   `signing.env` and set `SIGNING_KEY_FINGERPRINT` /
   `AGENT_SIGNING_KEY_FINGERPRINT` to their substituted key.
3. **It is excluded from the runtime gate's own scope.**
   `scripts/verified-exec.sh:35-37` verifies exactly
   `src/`, `scripts/verify-manifest.sh`, `scripts/verified-exec.sh`, and
   `security/trusted-signing-key.pub.asc`. **`security/signing.env` is not in
   that list** — even though it *is* in the manifest (line 513) and *is* baked
   into the running container (confirmed: `/app/security/signing.env` hashes to
   `4c62d07bb93f13a3…`, matching both the repo copy and the manifest entry, so
   it is currently untampered).

Net: the pin adds real protection only against an attacker who can write the
pubkey but *not* `signing.env` — an implausible split, since both are ordinary
tracked files with identical permissions. **Concrete hardening, in priority
order:** add `security/signing.env` to `verified-exec.sh`'s target list (a
one-token change that closes item 3 immediately); replace `source` with a
restrictive parse (`grep '^[A-Z_]*=' | cut -d= -f2`) to close item 1; and, for
item 2, consider a fingerprint pinned somewhere the repo-writing adversary does
not reach (a build-time constant baked into the image, or root-owned
`0644` on-host).

→ **NEW-FINDING.** This does not mean the integrity system is ineffective — it
demonstrably works against on-disk tampering and bad deploys (see B-6), which is
its stated threat model. It means one specific hardening the code claims credit
for does not deliver what its comment says.

### NF-6. The CUI/PII scrub gate structurally misses the HF band — i.e. the actual SHARES/HEARS frequencies — *moderate; live-confirmed by execution*

`src/second_brain/scrub_gate.py` fires only when a program name **and** a
frequency-shaped token co-occur (`:47`). The frequency pattern is:

```python
_FREQ_SHAPED = re.compile(r"\b\d{3}\.\d{3,4}\b")   # scrub_gate.py:38
```

This requires **exactly three digits before the decimal**. SHARES and HEARS are
**HF** programs; HF frequencies are one or two digits of MHz (e.g. `14.3960`,
`5.3305`). The pattern therefore cannot match the frequencies of the very
programs it names. Separately, the trailing `\b` fails whenever a unit is
written adjacent to the number, because letters are word characters.

Executed against the live module (`PYTHONPATH=src`, no live-system impact):

| Input | `scan()` result |
|---|---|
| `SHARES net freq 123.4567` | blocked ✓ |
| `SHARES net freq 123.456 MHz` | blocked ✓ |
| `SHARES net freq 123.456MHz` | **PASS (clean)** ✗ |
| `HEARS primary 60.9750kHz` | **PASS (clean)** ✗ |
| `SHARES freq: 14.3960` | **PASS (clean)** ✗ — real HF shape |
| `SSN 123-45-6789` | blocked ✓ |

The module is honest about being *"a first-pass heuristic gate (regex-based),
not exhaustive"* (`:16-19`), and it does genuinely **block rather than redact**
(`gate()`, `:57-62`) — so the platform-generic claim (`:86`) and FAQ claims
(`:43`, `:85`) are **literally accurate about the mechanism**. But a reader of
those claims would reasonably infer coverage the gate does not have, and the
two failures above are not exotic adversarial inputs — `14.3960` and
`123.456MHz` are how these values are ordinarily written. Widening to
`\b\d{1,3}\.\d{3,4}\b(?!\d)` and dropping the trailing `\b` would close both.

**Secondary gap:** in `board_post` the gate is applied to
`subject + body + refs` only (`src/web/main.py:514`). The `from_`, `to`,
`thread`, and `in_reply_to` fields (`BoardMsgIn`, `:329-336`) are stored
unscanned. → **NEW-FINDING.**

### NF-7. Unauthenticated requests force writes to the 24 GB production SQLite — *low-moderate; availability*

Three anonymous paths cause production-database **writes** with no app-level
rate limit:

1. **Admin-surface probing.** `require_admin`'s 2026-08-25 C-9 fix audits
   *denied* attempts before raising (`src/auth/auth.py:243-252`) — correct for
   forensics, but it means any anonymous request to any of the ~34
   `require_admin` routes writes an `audit_log` row before returning 403.
2. **`GET /api/v1/board/refresh`** (`src/web/main.py:454-497`) — **no rate
   limiter at all**, unlike its siblings. Any garbage `X-Board-Key` drives
   `board_refresh_token()`, which executes `DELETE FROM board_refresh_grace …`
   (`db.py:631`) **and** an `audit()` insert (`db.py:645-646`) before returning
   401. **Two writes per anonymous request.**
3. **`GET /api/v1/board/enroll`** (`:428-451`) — no rate limiter; drives an
   `UPDATE` attempt in `board_consume_nonce` (`db.py:505-509`).

By contrast `POST /api/v1/board` (`:509-512`), `/api/v1/vault/research[/list]`
(`:1722-1725`, `:1778-1782`), and `/api/v1/whoami-token` (`:159`, per its own
C-26 fix) all carry sliding-window limiters. The omission on the two board GETs
looks like oversight rather than intent.

**Live corroboration:** an unauthenticated `GET /admin/tokens` on loopback
returned **403 in 5.9 seconds** — my first probe at a 12 s timeout returned
`000`. Nearly six seconds of server work for a request that should be rejected
in microseconds, consistent with contention on the production DB write. This
matters because `sqlite3.OperationalError: database is locked` is already a
documented recurring failure class on this box (`CLAUDE.md` records it hitting
`aim_parser.py`, `ingest/nwws.py`, and
`failover-kickover-guardrail.service`). nginx's `limit_req zone=corporatetraveldc_lr
burst=20 nodelay` (`nginx/conf.d/dispatch…conf:27`) is the only throttle, and it
does not apply to tailnet or loopback callers.

→ **NEW-FINDING.** Cheapest fix: add the existing sliding-window limiter to the
two board GETs, and consider making the denied-path audit write
best-effort/batched.

### NF-8. The thermal remediation is **not in effect at runtime** — stale `set-property` drop-ins pin `CPUWeight=9500` — *high operational significance; live-confirmed*

This is the finding with the most direct bearing on any reliability claim, and
it is the one most likely to be believed already fixed.

The 2026-09-03 remediation lowered ingest `CPUWeight` from 9500 to 7500 so the
SWIM ingest tier would stop out-competing `llama-chat` (`CPUWeight=9000`). That
change is **fully and correctly deployed at the unit-file layer**:

- Tracked repo copy: `CPUWeight=7500` (`.config/containers/systemd/corporatetraveldc-ingest-core.container:97`)
- Installed copy: **byte-identical** to the repo copy
- Generated quadlet unit: `CPUWeight=7500`
  (`/run/user/1000/systemd/generator/corporatetraveldc-ingest-core.service:105`,
  regenerated 2026-09-04 00:02)

**And yet the live effective value is still 9500:**

```
systemctl --user show corporatetraveldc-ingest-{core,fdps,itws,notam,stdds,tbfm,tfms}.service -p CPUWeight
  -> 9500  (all seven)
```

Root cause, found by chasing the contradiction rather than accepting either
number: a persistent **runtime drop-in** exists for each of the seven units at

```
~/.config/systemd/user.control/corporatetraveldc-ingest-<feed>.service.d/50-CPUWeight.conf
    # created via "systemctl set-property"
    [Service]
    CPUWeight=9500
```

all seven stamped **2026-09-01 22:00:34** — i.e. an earlier manual intervention.
systemd drop-ins take precedence over the main unit file, so this **silently
overrides the tracked 7500 remediation on every unit, permanently, and survives
reboot.** The operator's fix is real, correct, committed, and inert.

**Live consequences, measured:**

- `thermal-ingest-guard` recorded **16 LOCKDOWN trips in the last 36 hours**,
  the most recent at **04:17:06 EDT today** — ~50 minutes before this check.
- Shed-state occupancy: **`tier=2` on 327 of 1,077 samples over 36 h (~30%)**;
  **135 of 359 over 12 h (~38%)**.
- System load at sample time was routinely 26–40 against the guard's
  `[15-40)` watch band; `ops-brief` logged *"load still 40.18 after 180s wait,
  proceeding anyway"* at 05:13 EDT.

**Remediation is a single command** (`systemctl --user revert
'corporatetraveldc-ingest-*.service'`, then `daemon-reload`). **I did not run
it** — this pass is strictly non-destructive and modifies nothing. Flagged for
the operator.

**Investor-materials impact:** the availability caveat currently reads *"10
automatic shed-and-restore cycles of ~9-11 minutes each"* in ~32 h (aviation-ops
`:46`, concierge `:48`, ground-transport `:45`, platform-generic `:33`, EP `:84`).
The current measured rate is **16 trips in 36 h** — roughly 60% higher — and the
tuning item those documents describe as "known and documented" is, in fact,
**fixed-but-not-applied**. Both numbers should be refreshed, and it would be
materially more credible to say so than to restate the 08-24 figure.

→ **NEW-FINDING.**

### NF-9. A shared read-write runtime directory is mounted into the public demo container — *low; new surface not covered by the 08-24 isolation assessment*

The 08-24 deep-dive concluded the demo's worst case is *"the empty demo
directory."* That was true of the mounts present then. The running demo
container now carries a **second** mount:

```
Volume=/run/corporatetraveldc:/run/corporatetraveldc:z    (runner-demo.container:132)
podman inspect -> /run/corporatetraveldc -> /run/corporatetraveldc (rw)
```

`/run/corporatetraveldc` is **shared read-write with production**: the same path
is mounted by `corporatetraveldc-poller`, `-runner`, `-pusher`, and `-web`. It
currently contains one subdirectory, `triggers/` (empty at inspection, mtime
04:36 EDT today).

So the accurate current statement is *"the demo can reach no production
**data**, but it does share a production **control** directory read-write."*
Whether that is exploitable depends entirely on how `triggers/` is consumed by
the production side — if a trigger file dropped there is acted upon without
provenance checks, a compromised public demo could induce production work.
I did **not** write anything into it to find out; that would be a mutation, and
this pass modifies nothing.

→ **NEW-FINDING / NEEDS-HUMAN-REVIEW.** Two questions for the operator: does the
demo actually need this mount, and is `triggers/` consumed with any
authentication? If the answer to the first is no, `:ro` or removal is the clean
fix and restores the 08-24 conclusion verbatim.

### NF-10. `GET /admin/approval-requests/{request_id}/resolve` is unauthenticated by design — *NEEDS-HUMAN-REVIEW, not a defect*

`src/web/main.py:2581-2590` is the one route under `/admin/` with **no
`require_admin` dependency**, and it resolves (allow/deny) a pending **sudo
command approval**. This is deliberate and well-argued at `:2540-2547`:
Cloudflare strips `Authorization` through the tunnel, so a token-gated endpoint
could never work from the operator's phone; security rests on the UUID4 request
id being a 122-bit magic link, single-use, with the DB accepting one resolution
per id.

The reasoning is sound **conditional on the id never traversing an untrusted
channel** — and by design it traverses **ntfy** (the alert carries Allow/Deny
action buttons). The 122-bit-entropy argument protects against brute force, not
against anyone who can subscribe to or observe the ntfy topic. The id is not
otherwise recoverable: `POST`/`GET`/`LIST` on
`/admin/approval-requests` are all `require_admin`-gated (`:2560`, `:2573`,
`:2597`), so nothing else leaks it.

Not scored as a finding — it is a documented, reasoned design decision — but the
threat model deserves one explicit line the code comment does not currently
carry: *the security of the sudo-approval gate reduces to the confidentiality of
the ntfy topic.* → **NEEDS-HUMAN-REVIEW.**

### NF-11. `RingCentral` webhook reflects an arbitrary unauthenticated header — *informational*

`src/web/routes/webhooks.py:106-108` echoes `Validation-Token` verbatim into a
response header **before** `_check_secret` runs (`:110`). This is required by
RingCentral's subscription handshake and carries no payload, as the module
docstring explains (`:23-27`). Response-splitting is not achievable — the HTTP
parser rejects CRLF in header values before Starlette ever sees them — so the
practical impact is limited to an unauthenticated 200-returning reflection
endpoint. Noted for completeness; **not** scored as a finding.

### NF-12. Integrity-gate cost is real and currently being paid — *informational, investor-relevant*

As established in B-6, three units are `failed` right now because the gate is
correctly refusing to run against a staged, unsigned `src/` change. That is the
control working. The honest framing for diligence is that this control has a
**real availability cost**: any editing session that touches `src/` takes the
dependent skills offline until the manifest is re-signed. The materials describe
the gate as a strength (platform-generic `:30`, EP `:55`, FAQ `:19`/`:43`) and
should ideally note the trade-off, because a diligence reader who runs
`systemctl --user list-units --state=failed` during a screen-share will see it.

---

## Part D — Carried operational items, re-measured live

### D-1. LLM narrative rate (08-13 Finding A) — **PARTIALLY REMEDIATED**, not resolved, not still-zero

08-13 found a **regression to 0%** narrative, with the dedicated
`corporatetraveldc-pi5-*` models absent from Ollama. The substrate has since
changed entirely: **Ollama is retired** — the binary is absent from the box,
nothing listens on `:11434`, and the runtime is now llama.cpp
(`corporatetraveldc-llama-hot/chat/report-1.service`, ports 8093/8094/8095 per
`src/common/llama_pool.py:54-58`).

Measured over the **last 7 days** from each skill's own completion log lines
(note: two distinct log wordings are in use — `brief generated (X)` and
`brief generated via X` — a single-pattern grep silently undercounts, which it
did on my first attempt):

| Skill | LLM narrative | Deterministic | **Narrative rate** |
|---|---:|---:|---:|
| `ops-brief` | 92 | 106 | **46.5%** |
| `ep-advance` | 152 | 112 | **57.6%** |
| **Combined** | **244** | **218** | **52.8%** |

The dedicated per-persona models **are** present and serving (log lines name
`corporatetraveldc-pi5-ops-brief:latest` and
`corporatetraveldc-pi5-ep-advance:latest`, with real
`POST http://100.x.x.x:8094|8095/v1/chat/completions -> 200 OK`).

**Cause of the residual fallback is contention, not absence** — and it is the
same root cause as NF-8. `ops-brief` logs
*"pre-flight load gate -- load 33.22 is above target 7.00, waiting up to 180s"*
followed by *"load still 28.36 after 180s wait, proceeding anyway."* Successful
generations correlate with low load (one succeeded at load 5.92); failures
correlate with sustained load 15–40. **Fixing NF-8 should directly raise this
rate**, which makes NF-8 the highest-leverage single item in this report.

**Verdict: the 08-13 regression is PARTIALLY REMEDIATED.** Do not restate
"~87%." The defensible current figure is **~53% combined**, or the two per-skill
figures.

**Materials impact:** the disclosed *"41.7% of skill LLM calls … ran the labeled
deterministic-template fallback"* (concierge `:44`/`:92`, EP `:36`/`:85`,
platform-generic `:27`) understates the current measured deterministic rate of
**~47%**. The direction of the error is unfavourable, so it should be
re-measured before v1.6 rather than carried.

**Two naming corrections the materials need:**
- EP `:36` says *"local Ollama only"* and platform-generic `:27` says *"21
  dedicated Ollama models."* **Ollama has been retired since 2026-08-27.** The
  runtime is llama.cpp. (The log strings still say `Ollama/` because they are
  hardcoded legacy labels — `src/poller/skills/ops_brief.py:859`, `:1044`;
  `src/poller/skills/ep_advance_brief.py:1073` — which also means the
  **user-facing brief text** says "Ollama" for a system that no longer runs it.)
- The "**21 dedicated local LLM models**" claim (used in all five verticals) is
  supportable — `build-models.sh` defines exactly **21** distinct
  `corporatetraveldc-pi5-*` names — but they are per-persona configurations over
  shared base weights (only one GGUF, `phi3-mini-q4_0.gguf`, is resident), not
  21 independently-trained models. The current wording is defensible; "dedicated
  model configurations" would be unimpeachable.

### D-2. Thermal guard / `CPUWeight` — see **NF-8**. LOCKDOWN trips have **not** stopped: 16 in 36 h, most recent 04:17 EDT today.

---

## Part E — Investor-claim accuracy cross-check

Claims verified against live state. Only discrepancies and confirmations
material to diligence are listed.

| Claim (source) | Live measurement | Verdict |
|---|---|---|
| Vault exposure "closed at two independent layers … confirmed by live external request" (EP `:52`, FAQ `:34`, platform `:63`) | Both layers confirmed live today | ✅ **accurate** |
| "pre-commit/pre-push credential hooks now actually installed" (FAQ `:43`) | Both present, executable | ✅ **accurate** |
| "git history is clean of credentials" (platform `:64`) | 83 pattern hits, all detector/placeholder/doc; zero real | ✅ **accurate** |
| "tokens stored as SHA-256 hashes only, no plaintext column" (FAQ `:43`, EP `:54`) | True of `auth_tokens`/`board_tokens`; **`board_refresh_grace.new_token` is plaintext** | ⚠️ **now incomplete — see NF-1** |
| "GPG-signed **706**-file manifest" (aviation `:13`, concierge `:15`, ground-transport `:66`, platform `:13`/`:30`, EP `:55`) | `MANIFEST.sha256` = **902** lines | ⚠️ **stale (−22%)**, understates the control |
| "**6,742** indexed vault documents" (platform `:29`/`:87`, FAQ `:23`/`:85`) | **9,657** live | ⚠️ **badly stale**; also contradicted *within* platform-generic, which says 9,579 at `:57` |
| "99 concepts" (EP `:23`, platform `:29`) | `semantic_concepts` = **99** | ✅ **exact** |
| "51,317 note-to-concept edges" (EP `:23`, platform `:29`) | `semantic_note_concepts` = **73,646** | ⚠️ stale, **understates** |
| "causal derivation graph of 26,448 edges" (platform `:29`) | `semantic_note_derivations` = **38,027** | ⚠️ stale, **understates** |
| "3,384 audit rows in prior 24 h" (concierge `:45`, platform `:28`, EP `:56`) | **402** in the last 24 h | ⚠️ point-in-time snapshot, not representative |
| "10 shed/restore cycles in ~32 h" (5 files) | **16 LOCKDOWN trips in 36 h**; ~30% shed occupancy | ⚠️ **understates current** — see NF-8 |
| "41.7% deterministic fallback" (concierge, EP, platform) | ~**47%** current | ⚠️ **understates current** — see D-1 |
| "21 dedicated **Ollama** models" (platform `:27`), "local Ollama only" (EP `:36`) | Ollama retired 2026-08-27; runtime is llama.cpp | ❌ **factually wrong on runtime** |
| "CUI/PII scrub gate that blocks rather than redacts" (platform `:86`, FAQ `:43`/`:85`) | Mechanism accurate; coverage misses the HF band | ⚠️ **literally true, materially incomplete — see NF-6** |
| "issued API tokens do not expire (expiry implemented but never used at mint time)" (FAQ `:47`) | Confirmed: `lookup_token` enforces expiry+revocation; `generate_token` accepts `expires_at` | ✅ **accurate and appropriately self-critical** |
| "24/7" opener vs "not always-on / no SLA" caveat (all six files) | Caveat is the accurate half | ⚠️ **internal tension** — the caveat should lead, given NF-8 |

**Note on the stale-but-understating numbers** (manifest files, vault documents,
semantic edges, derivation edges): every one of these has grown since it was
last measured. Refreshing them *strengthens* the materials. The vault-document
figure is the one that must be fixed regardless, because platform-generic
currently contains two different numbers for it in the same document.

---

## Summary by confidence label

**STILL-RESOLVED** (adversarial pressure applied; nothing reopened)
- **Vault / knowledge-graph anonymous-read exposure (08-13 item 6, the CRITICAL)** — closed at two independent layers, both verified live (A-1)
- Sibling vault-research surfaces + traversal guard (A-2)
- Tier resolution, `X-CTDI-Public` pin, nginx replace-not-append semantics (B-1)
- Token hash-only storage, with expiry/revocation enforced at lookup (B-2)
- Clean git history — all 83 pattern hits classified as benign (B-3)
- Pre-commit/pre-push hooks — 08-13's one CONFIRMED gap is **closed** (B-4)
- Runner-demo isolation: separate inodes, prod DBs absent, no file drift (B-5)
- Signed-manifest execution gate — enforcing live, right now (B-6)
- SQL construction — no injection anywhere in 6,610 lines of `db.py` (B-7)
- LADD Tier-0 masking on both return paths (B-8)
- Board-key gate; scope-blind footgun guard's precondition intact (B-9)

**REOPENED**
- **None.**

**PARTIALLY REMEDIATED**
- 08-13 Finding A (LLM narrative rate): 0% → **~53% combined**; dedicated models present and serving; residual fallback is load-driven and traces to NF-8 (D-1)

**NEW-FINDING**
- **NF-1** — `board_refresh_grace` stores a plaintext live token with **no retention prune**; usable window is 24 h, not the documented 120 s; contradicts a current investor claim *(moderate)*
- **NF-8** — ingest `CPUWeight` remediation **inert at runtime**; stale `set-property` drop-ins pin 9500 on all 7 units; 16 LOCKDOWN trips in 36 h *(high operational significance)*
- **NF-5** — manifest fingerprint pin defeated by `source`-before-verify + exclusion of `signing.env` from the runtime gate's scope *(moderate, design-level)*
- **NF-6** — scrub-gate frequency regex structurally cannot match HF frequencies, i.e. the actual SHARES/HEARS band; plus unit-adjacency bypass; live-confirmed by execution *(moderate)*
- **NF-2** — grace relay bypasses the presence-attestation gate, contradicting an explicit two-place in-code contract *(low)*
- **NF-3** — `board_refresh_token()` double-mint race; same class already fixed for nonces in C-32 *(low-moderate)*
- **NF-4** — non-ASCII credential header → **HTTP 500** instead of 401, at two call sites; live-confirmed *(low)*
- **NF-7** — unauthenticated requests force production-SQLite writes on `/admin/*`, `/board/refresh`, `/board/enroll`; measured 5.9 s for an anonymous 403 *(low-moderate)*
- **NF-9** — `/run/corporatetraveldc` shared **rw** with production, mounted into the public demo; new since the 08-24 isolation assessment *(low)*
- **NF-11 / NF-12** — RingCentral header reflection; integrity-gate availability cost *(informational)*

**NEEDS-HUMAN-REVIEW**
- **NF-9** — does the demo need `/run/corporatetraveldc`, and is `triggers/` consumed with provenance checks?
- **NF-10** — the sudo-approval magic link's security reduces to ntfy-topic confidentiality; sound design, threat model should be stated
- **B-1** — `T2`/"shares" outranks `T1`/"cert", so a shares token satisfies every Tier-1 route including the vault endpoints; benign today, an escalation if shares tokens are ever issued more widely
- Carried unchanged from 08-24: `CF-Connecting-IP` sole-dependency (New-Finding 3); separation-boundary policy (N-2); token hygiene (N-5)

**LIKELY FALSE POSITIVE**
- Current `verify-manifest` INTEGRITY FAILURE on `src/second_brain/doc_generation.py` — benign unsigned drift (file edited 00:56 EDT, manifest signed 00:15 EDT, staged in git), resolved by the next signing pass; the control behaving exactly as designed

---

## Recommended order of operations

1. **NF-8** — `systemctl --user revert 'corporatetraveldc-ingest-*.service'`.
   One command; fixes the thermal trips *and* should lift the LLM narrative rate
   (D-1), because both trace to the same contention. Highest leverage in the
   report.
2. **NF-1** — add `board_refresh_grace` to `prune_expired_board_auth()`, then
   correct or carve out the "no plaintext column" claim.
3. **NF-5** — add `security/signing.env` to `verified-exec.sh`'s target list
   (one token) and stop `source`-ing it.
4. **NF-4** — guard both `compare_digest` call sites against non-ASCII input.
5. **NF-6 / NF-2 / NF-3 / NF-7 / NF-9** — as scheduling allows.
6. **Materials** — refresh the manifest file count, vault document count (and
   resolve the 6,742-vs-9,579 internal contradiction), semantic/derivation edge
   counts, shed-cycle cadence, and fallback rate; correct "Ollama" to llama.cpp.

**Nothing in this pass was fixed, patched, restarted, or changed — review and
reporting only.** No secret, token, key, password, or real sensitive value
appears anywhere above. Vault content was never read.
