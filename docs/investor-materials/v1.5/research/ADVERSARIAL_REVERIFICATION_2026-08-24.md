# Adversarial Pentest Re-Verification — Live, Non-Destructive (2026-08-24)

> **What this is.** An **adversarial** re-verification pass whose default posture
> is that every `RESOLVED`/`CONFIRMED-RESOLVED` label in the three prior 2026-08-24
> documents is wrong until personally, skeptically disproved under real
> pressure — header-case variants, alternate encodings, malformed inputs,
> multi-header injection, repo-vs-live config drift, running-container-vs-file
> divergence, and sibling-path checks. Successor to (and deliberately not a
> re-run of) `PENTEST_2026-08-24.md`, `REVERIFICATION_2026-08-24.md`, and
> `PENTEST_REVERIFICATION_2026-08-24.md`.
>
> **Scope discipline (unchanged from every prior pass):** non-destructive only,
> no fuzzing/brute-force/high-volume scanning, **1–2 requests per endpoint, no
> retries**. **No secret, token, key, password, or real GPS-coordinate value is
> printed anywhere** — only that a check was made and its outcome. The
> destructive `DELETE /api/chat/history` capability was identified by code +
> reachability and **never invoked**. Nothing on the live system was modified,
> restarted, or stopped.
>
> **Verdict vocabulary:** `STILL-RESOLVED` (adversarial pressure did not reopen
> it) / `REOPENED` (a real bypass was found) / `NEEDS-HUMAN-REVIEW`.

Snapshot: 2026-08-24, live. Thermal guard `tier: 0` (nothing shed — full stack
reachable). `runner-demo` `active (running)`, `NRestarts=0`. `runner` (prod)
`active (running)`.

---

## HEADLINE

**Nothing reopened. No new exploitable exposure found.** The two vulnerabilities
the basic re-verification found and the operator then fixed twice in rapid
succession — the public demo having the real 24 GB production DB mounted
read-write (New-Finding 1) and the public demo sharing the production runner's
`dispatch-chat.db` (New-Finding 2) — are **genuinely and structurally closed at
the mount layer**, verified against the actually-running container, not just the
tracked file. The repo `.container` and the live copy are byte-identical, the
running container was recreated at 14:52 EDT after the final relocation and
actually mounts the demo-only sibling directory, and the demo can no longer see,
read, or write any production file. LICENSE work is in place and correct.

---

## Runner-demo isolation deep-dive (highest-priority item) — **STILL-RESOLVED**

This is the item changed twice in hours (home-dir path → `/var/lib/corporatetraveldc-demo`),
so every layer was checked against live reality rather than the file.

### 1. Repo vs live config — byte-identical, and actually deployed
- `diff` of the repo copy against
  `/home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-runner-demo.container`
  → **BYTE-IDENTICAL**. The mount line is
  `Volume=/var/lib/corporatetraveldc-demo:/var/lib/corporatetraveldc:z`.
- **Byte-identical file is not enough** — the running container must actually
  carry it. `podman inspect systemd-corporatetraveldc-runner-demo` shows the live
  mount is `/var/lib/corporatetraveldc-demo -> /var/lib/corporatetraveldc (rw)`,
  and the container was **created 2026-08-24 14:52:06 EDT**, i.e. after the
  ~14:40 relocation. So the last edit genuinely got deployed; there is no
  file-vs-running-container drift (the recurring trap on this box).

### 2. The demo cannot see any production file — **CONFIRMED**
`podman exec systemd-corporatetraveldc-runner-demo ls -la /var/lib/corporatetraveldc/`
returns **only** a fresh `dispatch-chat.db` (16 KB, created 14:52). Direct
existence probes from inside the container:
- `corporatetraveldc.db` (the real 24 GB prod DB) → **absent**
- `second_brain_index.db` → **absent**

The real production directory is simply **not mounted into the demo container at
all** — there is no container-internal path that resolves to it. This closes
New-Finding 1's blast-radius concern at the source: the worst case for any future
arbitrary-file-read / path-traversal / RCE bug in the demo runner is now "the
empty demo directory," not "all of production, writable." This is a genuine
structural containment layer, not the earlier "isolation depends on which file
the app chooses to open" model.

### 3. Demo dir is a hardened sibling — **CONFIRMED**
- `ls -ld /var/lib/corporatetraveldc-demo` → `drwx------` (mode **700**),
  owner **`corporatetraveldc:corporatetraveldc`**.
- `ls -ld /var/lib/corporatetraveldc` → `drwxr-xr-x` (755), same owner.
- The demo dir's parent is `/var/lib` (`root:root`), confirming it is a genuine
  **sibling** of the production dir, **not nested inside it**. A bind mount only
  exposes the exact host path given, so the sibling gives identical isolation and
  keeps the production dir visually clean of demo state.

### 4. `dispatch-chat.db` is now two different files — **CONFIRMED**
- demo: `inode=123317778` (`/var/lib/corporatetraveldc-demo/dispatch-chat.db`)
- prod: `inode=710109` (`/var/lib/corporatetraveldc/dispatch-chat.db`)

Different inodes → different files. The prior pass's "same inode 710109 on both
`:8005` and `:8001`" finding is disproved live.

### 5. Chat endpoints — production data unreachable through the demo — **CONFIRMED**
- `GET /api/chat/history` on demo `:8005` → `{"messages":[],"count":0}`
- `GET /api/chat/history` on prod runner `:8001` → **count=2**, real operator
  dispatch chat (content not reproduced here).
- Over the **public internet**: `https://dispatch-runner.example.com/`
  → **HTTP 200** (crash loop cured, no regression), and its
  `/api/chat/history?limit=2` → `{"messages":[],"count":0}`.

So an anonymous internet visitor to the public demo reads the demo's own empty
chat, never the operator's production chat. New-Finding 2 is closed.

### 6. Code audit of every local (non-proxied) file open — **CONFIRMED isolated**
`grep` of `src/runner/main.py` + `src/shared/rss_catalog.py` for every
`sqlite3.connect`/`open()`/path constant that is **not** proxied over HTTP to
`DISPATCH_BASE_URL`:
- `CHAT_DB_PATH` and `_CONFIG_PATH` → `os.getenv("STATE_DIR", "/var/lib/corporatetraveldc")/…`
  (`STATE_DIR` unset on the demo → default → the container-internal
  `/var/lib/corporatetraveldc`, which **is** the demo mount).
- `USER_FEEDS_PATH` (`rss_catalog.py:153`) → hardcoded absolute
  `/var/lib/corporatetraveldc/user_rss_feeds.json`.

Key adversarial point: even the **hardcoded absolute** path lands in the isolated
demo directory, because the demo container's `/var/lib/corporatetraveldc` mount
point *is* the demo sibling. The mount-layer fix therefore contains **every**
`/var/lib/corporatetraveldc/*` access — including any future new hardcoded path a
developer might add — which the previous "app-code-choice" model did not. This is
strictly stronger isolation.

### 7. Endpoints were fixed by isolation, not gating (accurate characterization)
`GET`/`DELETE /api/chat/history` (`src/runner/main.py:1489`/`:1496`) remain
**ungated** (no auth, no `DEMO_MODE`, no `_is_trusted`) — unchanged from the prior
pass. The operator chose the *mount-isolation* remediation (New-Finding 1's
option) rather than the *endpoint-gating* remediation (New-Finding 2's option).
Both were valid options; isolation was taken. Consequence, for the record: the
demo's own empty chat db is still world-readable and world-wipeable via those
ungated endpoints — but it holds no production data and is demo-scoped, so this
is **harmless**, not a finding. The destructive DELETE was not invoked.

**Runner-demo isolation verdict: STILL-RESOLVED.** New-Finding 1 and New-Finding 2
are both genuinely closed, verified against the running container and the live
filesystem, with the fix confirmed strictly stronger than the model it replaced.

---

## Prior findings re-attacked

### N-1 — Board-key gate on `/api/v1/vault/research[/list]` — **STILL-RESOLVED**
Re-attacked `_require_board_key()` (`src/web/main.py:162`) with bypass attempts
rather than the happy path (real key never presented; only bogus values used):

| Attack | Result |
|---|---|
| No `X-Board-Key` header | **401** |
| lowercase `x-board-key: <bogus>` | **401** |
| uppercase `X-BOARD-KEY: <bogus>` | **401** |
| whitespace-only value `X-Board-Key:    ` | **401** |
| empty value | **401** |
| no key + `?path=../../etc/passwd` (bare `research`) | **401** |
| no key + double-encoded `%2e%2e%2f…Contacts` (`research/list`) | **401** |

Header-case variants resolve to the same header (Starlette normalizes) and still
require a valid key — no case-sensitivity bypass. Whitespace does not match: the
stored key is `.strip()`ped but `presented` is not, so a padded key fails
`compare_digest`, and `board_token_valid()` SHA-256-hashes the presentation so any
padding changes the hash → no match. **Auth is enforced before path validation**
(401, not 400/200), so an attacker cannot even probe the traversal guard without
the credential. Expiry/revocation: `board_token_valid()` (`src/common/db.py:491`)
returns true only for `expires_at > now` on a parameterized indexed `token_hash`
lookup — an expired minted token fails by construction (code-verified; live count
skipped because the 24 GB prod DB WAL was locked, a normal live condition, not a
finding). **No bypass found.**

### N-3 — GPS / widget-key trust gate on `/api/v1/frontend-config` — **STILL-RESOLVED (public/demo vector); one documented residual, unchanged**
Re-attacked `_is_trusted()` (`src/runner/main.py:175`) against the **production**
runner (`:8001`, tailnet-only) with header spoofs beyond the prior pass.
Results classified against the known untrusted placeholder `(38.8521,-77.0377)`;
**no real coordinate printed**:

| Attack (on `:8001`) | Trust result |
|---|---|
| plain loopback (baseline) | real coords (correct — loopback trusted) |
| `CF-Connecting-IP: 8.8.8.8` (public) | **placeholder** (correct reject) |
| `CF-Connecting-IP: ::1` (IPv6 loopback) | **placeholder** (correct — `_TRUSTED_NETS` is IPv4-only, fail-closed) |
| `CF-Connecting-IP: not-an-ip` (malformed) | **placeholder** (correct — `ValueError → trusted=False`) |
| `CF-Connecting-IP: 10.9.9.9` (spoofed private) | real coords (documented residual) |
| two `CF-Connecting-IP` headers, public-then-private | **placeholder** |
| two `CF-Connecting-IP` headers, private-then-public | real coords |

Two adversarial confirmations:
- **IPv6 `::1` and malformed IPs fail closed** — the gate does not have an
  IPv6-loopback or parse-error hole.
- **New nuance found (not in the prior pass):** with two `CF-Connecting-IP`
  headers, `request.headers.get()` returns the **first** occurrence, so header
  ordering decides trust. This sharpens New-Finding 3's dependency on Cloudflare:
  the app trusts whatever `CF-Connecting-IP` arrives first, with no app-layer
  backstop. It is **not exploitable today** — a private-IP `CF-Connecting-IP`
  reads trusted, but `:8001` is tailnet/loopback-only (already trusted), and on
  the genuine public path Cloudflare sets `CF-Connecting-IP` authoritatively at
  its edge. It strengthens the existing NEEDS-HUMAN-REVIEW recommendation to
  narrow `--forwarded-allow-ips` / require an authoritative `CF-Connecting-IP`
  for public-hostname trust.

**Demo (`:8005`) leaks nothing regardless of trust** — verified: even a
trust-spoofed request returns `receiver_lat=39.0000` (the hardcoded *code
fallback* `ULTRAFEEDER_LAT` default, **not** the real receiver coordinate) and
`mt_widget_key=""` (empty). The demo container loads only `dispatch.env`, so the
real coordinate and widget key are structurally absent — it has no secret to
leak whether trusted or not. **CONFIRMED-RESOLVED for the public/demo vector.**

### Phase-1 item 2 — Tier auth rejects unauth / forged / garbage — **STILL-RESOLVED**
Re-attacked `/api/v1/runsheet` with genuinely malformed bearer tokens (not just a
well-formed forgery):

| Attack | Result |
|---|---|
| `Authorization: Bearer garbage!!!not-a-token` | **403** |
| `Authorization: notbearer xyz` (malformed scheme) | **403** |
| `Authorization: Bearer ctdc_admin_0000…0000` (forged real-shape) | **403** |
| `Authorization: Bearer ` (empty) | **403** |

No garbage/forged/empty token is accepted. Bearer-only, network-origin grants no
tier. **No bypass found.**

### Phase-1 item 6 — Whole-vault endpoints closed (code layer) — **STILL-RESOLVED**
| Attack | Result |
|---|---|
| `knowledge-graph/meta` + `X-CTDI-Public: 1` | **403** |
| `knowledge-graph/meta` + lowercase `x-ctdi-public: 1` | **403** |
| `vault/file` + `X-CTDI-Public: 1` | **403** |
| `knowledge-graph/meta` no token | **403** |

Header-case variant of the public-pin does not bypass the tier gate. **Holds.**

### Phase-1 items 5 / 7 — pre-commit hooks / demo-prod isolation — **STILL-RESOLVED**
Item 5 (hooks installed) re-confirmed present in the prior pass and unchanged.
Item 7 (demo isolation from production) is superseded and *strengthened* by the
runner-demo deep-dive above — filesystem isolation, which the prior pass flagged
as "a separate, now-weaker story," is now the strongest layer.

### New-Finding 3 — `X-Forwarded-For` / `CF-Connecting-IP` trust — **NEEDS-HUMAN-REVIEW (unchanged, not exploitable today)**
Re-confirmed above via the two-header nuance; still defended solely by Cloudflare
at the edge with no app-layer backstop. Not exploitable in the current topology
(`:8001` tailnet-only; `:8005` has no secrets). Hardening candidate, carried.

### New-Finding 4 — `mt_widget_key` unconditional on `/api/v1/frontend-config` — **LIKELY FALSE POSITIVE (unchanged)**
On prod `:8001` the widget key is returned regardless of trust, but `:8001` is
tailnet-only and the public demo `:8005` returns an empty key. Tailnet-only
exposure of a widget key; unchanged, low severity.

---

## LICENSE verification — **CONFIRMED correct and consistent**

### File presence and scrub safety
- `LICENSE` exists at repo root (7,201 bytes, 2026-08-24 14:11).
- `scripts/scrub-public-tree.py` `DROP_FILES` was read in full: `LICENSE` is
  **not** in it, and no pattern rule (e.g. the `corporatetraveldc.` prefix drop)
  matches it. LICENSE will transit to the public mirror. **No regression.**

### Text is canonical BSL 1.1, unmodified except the permitted Parameters
- Header carries the required MariaDB copyright + trademark lines.
- **Terms**, **Notice**, and **Covenants of Licensor** sections are **verbatim
  canonical BSL 1.1** — not modified (satisfies Covenant 4, "not to modify this
  License in any other way"). Only the Parameters block is customized, as
  intended.

### Parameters block — all four target values correct
- **Licensor:** `[operator LLC], LLC` ✓
- **Change Date:** `2030-08-24` ✓
- **Change License:** "Version 3 or later of the GNU General Public License" =
  **GPL v3-or-later** ✓. Adversarial check against Covenant 1 (which requires the
  Change License be "GPL Version 2.0 or any later version, or a license
  compatible with" it): GPLv3 **is** "a later version" of GPL 2.0, so
  GPLv3-or-later satisfies the covenant. This is correct and is *not* the common
  GPLv2-vs-GPLv3-incompatibility trap, because the covenant's own text explicitly
  admits "any later version." ✓

### Additional Use Grant — says what the operator intended
Read adversarially for garbling or lost intent:
- **(a)** personal / self-hosted home-lab use — free. ✓
- **(b)** internal relay/middleware by an org **of any size**, retrieving/
  submitting data for its own internal operations, **"with no part of that use
  serving, supporting, or contributing to any fee-based product or service
  rendered to a third-party client or customer."** ✓
- **(i)–(iii)** hosted/managed/white-label/embedded resale, rebranding/
  distribution, and platform-absorption beyond a distinct relay each require a
  commercial license, **"whether or not for a fee."** ✓
- **(iv)** the controlling paid-service rule: any use "in connection with any
  fee-based service rendered to a third-party client or customer, **including
  solely as an internal relay or middleware step never surfaced to the client
  directly**, regardless of whether that use is billed as a separate, itemized
  charge or **bundled into an overall fee, retainer, or rate.**" ✓✓

This is exactly the intended boundary: internal relay/middleware use is free
**unless** it touches any fee-based client service, even invisibly and even when
bundled. Clause (iv) is expressly named the controlling rule; (i)–(iii) are
independently disqualifying. **No intent was lost or garbled.**

### README consistency — matches
`README.md` §License references BSL 1.1, links `LICENSE`, and summarizes:
non-production free always; personal self-hosted + internal relay/middleware
(org of any size) free **unless** it serves a fee-based third-party client
service (with white-label/hosted-resale/platform-absorption carve-outs); other
production use — including using it "even invisibly" in a fee-based client
service — requires a commercial license from [operator LLC], LLC; per-
version conversion to GPLv3-or-later four years after first public distribution.
The **"currently under legal review / working draft / not confirmed by counsel"**
status note is present. All consistent with `LICENSE`.

### Minor editorial observations (not defects, not security)
- Grant (b) already excludes fee-based use in its own text, and clause (iv) also
  requires a commercial license for fee-based use — belt-and-suspenders, not a
  contradiction; (iv) usefully makes the bundled/never-surfaced case explicit.
- The grant is silent on the edge case of a relay supporting a **free** (non-fee)
  client-facing service that is not itself "offered as a hosted service" — clause
  (i) catches services "offered… whether or not for a fee," so a surfaced free
  service is covered, but a purely-internal relay behind a free client service is
  arguably permitted under (b). This appears consistent with the operator's
  fee-based focus; flagged only for counsel's awareness during the stated legal
  review.

---

## Summary by confidence label

**Prior findings re-attacked (adversarial pressure applied, not happy-path re-run):**
- **STILL-RESOLVED:** N-1 board-key gate; N-3 public/demo GPS vector; Phase-1
  items 2, 5, 6, 7; runner-demo isolation (New-Finding 1 + New-Finding 2, the
  two same-day-fixed items).
- **NEEDS-HUMAN-REVIEW (unchanged, not exploitable today):** New-Finding 3
  (`CF-Connecting-IP` sole-dependency, now with a two-header first-wins nuance);
  N-2 separation-boundary policy; N-5 token hygiene (carried).
- **LIKELY FALSE POSITIVE (unchanged):** New-Finding 4 (`mt_widget_key`
  tailnet-only).
- **REOPENED:** **none.**

**LICENSE:** CONFIRMED correct — canonical unmodified BSL 1.1, Parameters exact,
GPLv3-or-later valid under the covenants, Additional Use Grant faithful to intent,
README consistent, present in the public push, status note in place.

**Bottom line:** the twice-relocated runner-demo fix holds against live
adversarial checks; the isolation is real, deployed, and strictly stronger than
the model it replaced. No prior finding reopened; no new exploitable exposure
found this pass.
