# Supervisory Validation Notes — Independent Re-Check of `GROUND_UP_AUDIT_SECOND_VALIDATION_2026-08-24.md`

**Date:** 2026-08-24 (late evening, EDT)
**Role:** Independent supervisory / adversarial check on the second-validation test-agent pass. I re-verified a real, prioritized sample of that report's claims against **current** live and source state. Nothing was written, deleted, or state-changed during verification (only `GET`/read-only queries, source reads, and non-mutating git/podman inspection). No secret, token, or coordinate value is printed anywhere in this file.

**Method note on "resolved since report":** several findings were fixed by operator direction *after* the report was written. Where I find something the report called broken is now fixed, I mark it **RESOLVED-SINCE-REPORT** (report was accurate when written), not a contradiction.

---

## Headline

The core report is **substantially accurate and honestly scoped.** Its auth-model analysis holds up line-for-line, its two HIGH findings were real, and several of its findings have since been correctly remediated. I found **one materially important gap in the remediation** and **one factual over-claim** in the report:

- 🔴 **NEW / HIGHEST-STAKES — the P1 NWWS fragment was redacted locally but the public GitHub mirror still carries it, and the password was not rotated to remove the overlap.** The live-repo copy of `research/GROUND_UP_AUDIT_REMEDIATION_CHECK_2026-08-24.md` is now redacted (longest common substring with the live `NWWS_PASSWORD` dropped to 3 chars — coincidental noise). But `public/main`'s copy of that same file **still shares a 6-character contiguous substring with the *current live* `NWWS_PASSWORD`.** The public mirror has not been re-pushed since the redaction, and the live password still contains that 6-char core, so P1's own recommendation (rotate to a non-overlapping value **and** redact) is only half-done. This is the single most important open item.
- ⚠️ **Report over-claim (minor) — the "undocumented 3rd production GPG key" is documented.** `419A864CC29A09513039B6E03033FB4D01903159` is listed in `SECURITY.md:19` ("default signing key since 2026-07-07"). Also, the manifest has since been **re-signed with the agent key `CC15…0B37`** (the one CLAUDE.md/SECURITY.md already name), made 2026-08-24 20:25:16 EDT — so the manifest is no longer even signed by 419A at the moment.

Everything else the report flagged as open, I independently reproduced as still open; everything the operator has since fixed, I independently confirmed as fixed.

---

## Public remote — live state (highest-stakes item, verified directly)

`git fetch public --prune --tags` + `git ls-remote public`:

- **Refs:** exactly **one** — `refs/heads/main @ ee341041fc5866e2799bf9c89d89e437f0577dec`. **No tags. No other branches.** Matches the report exactly.
- **Tree size:** **717 files public vs 776 tracked** internally (59-file delta). Matches the report exactly.
- **Full-value secret cross-check (independent):** I loaded every value from `/etc/corporatetraveldc/dispatch-secrets.env` (names+values in memory, never printed) and `git grep -F` each one, ≥4 chars, against `public/main`. **No real secret value leaks:**
  - The only ≥8-char keys with public hits were `SWIM_NMS_HOST_*` — the value is the **public FAA endpoint** `tcps://ems*.swim.faa.gov:55443`, which is also the hardcoded code default in `src/ingest/config.py:122`. Not a secret. Report correctly did not flag it.
  - The `len=9` hits (`NTFY_TWILIO_*`, `PUSHOVER_*`) are **uncustomized placeholder values equal to the template** (`dispatch-secrets.env.template`) — false positives, not real credentials.
  - Feeder UUIDs (`FEEDER_ID`/`PIAWARE_FEEDER_ID`/`ULTRAFEEDER_UUID`/`UUID`), full `NWWS_PASSWORD`, all SWIM usernames/passwords/queues, all platform tokens, GPS lat/lon: **zero hits in `public/main`.**
- **But the 6-char NWWS fragment persists** (see Headline / P1 below) — this is a *content-fragment* exposure, not a full-value leak, which is exactly why a full-value grep does not catch it and why the report used a longest-common-substring method.

**Verdict on public remote:** clean of full secret values and GPS coordinates, single squashed commit, no stray branches/tags — **but not clean of the P1 password fragment**, which remains live on it.

---

## Claim-by-claim verdicts

### Auth model & live tier matrix (§3, §4)
- **§4.1 admin endpoints reject no-token and forged tokens** — **CONFIRMED.** Live: `GET /admin/tokens` no-token → 403; forged `ctdc_admin_00…0` prefix → 403. Source: `resolve_tier()` SHA-256s the bearer and `db.lookup_token()` is the only authority; prefix is not trusted.
- **§4.1 token expiry genuinely enforced; NULL = permanent** — **CONFIRMED** (`db.py:931` `WHERE … (expires_at IS NULL OR expires_at > unixepoch())`).
- **§4.2 board write gated (401 before any DB write)** — **CONFIRMED via source** (no live POST issued). `board_post()` (`web/main.py:415`) calls `_require_board_key(request)` as its first statement (`:421`), before `db.board_insert()` (`~:432`).
- **§4.2 mutating endpoints 403 no-token** — **CONFIRMED** (admin mutators 403; `osint/scopes` 403).
- **§4.3 `GET /api/v1/data-usage` anonymous 200** — **CONFIRMED, still open.** Live → 200; `get_data_usage()` (`web/main.py:1138`) has no auth dependency (docstring: "Tier 0"). Deliberately not fixed, as the task anticipated.
- **§4.3 `GET /api/v1/osint/feed` anonymous** — **RESOLVED-SINCE-REPORT.** Live now → **403**; source now carries `tier: Tier = Depends(require_tier(Tier.T1))` with a dated 2026-08-24 fix comment. (This is report finding **F6**.)
- **§4.4 passwordless-sudo resolve is a Tier-0 GET; DB logic single-use/atomic** — **CONFIRMED.** Route is `@app.get("/admin/approval-requests/{request_id}/resolve")` (`web/main.py:2462`); the residual weakness is the HTTP verb (state change via GET → prefetch/unfurl risk), exactly as the report scoped **F5**. DB guard is sound. Still open.

### Demo / runner (§5)
- **F1 (HIGH) — demo protections inert on a live public surface** — **CONFIRMED.** `runner-demo` is `active/running`, `NRestarts=0`, up 7h; `DEMO_MODE` unset in the container env, both env files, and the Quadlet. All three protections key off `DEMO_MODE` (`runner/main.py:48,758,1663,1959`), so all inert. Cloudflare ingress for `dispatch-runner.example.com` is live.
- **F2 (HIGH) — unauth GET/DELETE `/api/chat/history`** — **RESOLVED-SINCE-REPORT.** Both handlers (`runner/main.py:1520,1529`) now begin with `if not _is_trusted(request): return 404`, with a dated 2026-08-24 fix comment attributing the discovery to the blind audit. Live loopback (trusted) → 200; the gate returns 404 for untrusted origins. The report was accurate when written.
- **§5.2 demo data isolation** — **CONFIRMED.** `podman inspect`: the container's internal `/var/lib/corporatetraveldc` is backed by host `/var/lib/corporatetraveldc-demo` (mode **700**), a separate directory from production (**755**). Production DB not exposed to the demo.
- **F3 (MED) — unconditional cert-token injection for `_TIER1_PATHS` GETs** — **CONFIRMED, still open.** `_dispatch_proxy_headers()` (`runner/main.py:1630`) injects `RUNNER_ENRICHED_TOKEN` for `path in _TIER1_PATHS` with no `_is_trusted` guard; code default `DISPATCH_BASE_URL` is prod `:8000`.
- **F4 (MED) — confused-deputy `X-CTDI-Public` strip** — **CONFIRMED, still open** (as the task anticipated). Proxy sets only `Authorization`/`Content-Type`, never forwards `X-CTDI-Public`, and a client `Authorization` wins (`:1628`). The in-code comment even acknowledges the marker is not forwarded.
- **F7 (LOW) — header-derived trust brittle** — **CONFIRMED** by source (`_is_trusted()` trusts `CF-Connecting-IP`/XFF against `_TRUSTED_NETS`); not practically exploitable given loopback+tailnet binding. Consistent with report.

### Secrets in tracked tree (§6)
- **P2 — live feeder UUID hardcoded in `.config/containers/systemd/corporatetraveldc-ultrafeeder.container:56`** — **CONFIRMED.** Line 56 embeds `uuid=<live FEEDER_ID>`; value present in worktree (+ scrub script literal at `:290`), **absent from `public/main`** (scrubbed). Private-tree/history exposure only.
- **P3 — pre-rotation NWWS password verbatim in `CLAUDE.md`** — **CONFIRMED.** The value in CLAUDE.md shares **no ≥6-char substring** with the current live password (independent LCS scan of all tracked files returned zero ≥6 hits) → genuinely a different, burned credential. `CLAUDE.md` is in `DROP_FILES` (`scrub-public-tree.py:122`), so it is **not** on `public/main`. Private history only.
- **Coordinate / token / key sweep clean** — **CONFIRMED.** No `ctdc_<user>_<32>` tokens, `sk-*`, or `BEGIN PRIVATE KEY` blocks in the tree; the two root `.gpg` blobs are public-key blocks.

### Public git history hygiene (§7)
- **P1 — 6-char fragment of the *current live* NWWS password on public** — **CONFIRMED, and remediation is INCOMPLETE (see Headline).** Local worktree copy of the cited doc: LCS with live pw = **3** (redacted/noise). `public/main` copy of the same doc: LCS = **6**. Live password still contains that 6-char core (not rotated). **NEEDS-HUMAN-REVIEW / action:** re-push the scrubbed mirror *and* rotate `NWWS_PASSWORD` to a value sharing no substring with the burned value or this fragment.
- **P5 — overshare of pentest/incident narrative to public** — **CONFIRMED.** `public/main` carries **21** research/audit/pentest/live-state docs, including `research/GROUND_UP_AUDIT_2026-08-24.md`, `PENTEST_2026-08-24.md`, `ADVERSARIAL_REVERIFICATION_2026-08-24.md`, a prior `GROUND_UP_AUDIT_SUPERVISORY_NOTES_2026-08-24.md`, the `REMEDIATION_CHECK` doc itself, 11× `LIVE_STATE_CHECK_*`, `DRIFT_AUDIT_2026-08-16.md`, and `SUDO_JUSTIFICATION_PROPOSAL.md`. Same incident-narrative class for which CLAUDE.md was dropped.
- **§7 internal vault hostname surfaced by the redaction regex** — **CONFIRMED.** `src/web/main.py:291` contains the literal `cloud.example.com` inside the redaction regex; `src/web/main.py` is on `public/main`.
- **§7 no full secret values / private keys on public** — **CONFIRMED** (see Public-remote section above).

### Manifest / tests / hooks (§8)
- **Signed manifest clean now** — **CONFIRMED.** `verify-manifest.sh` → exit 0, "all **763** files match" (grew from 762). `gpg --verify` → Good signature, made **2026-08-24 20:25:16 EDT by agent key CC15…0B37** — *not* the 419A key the report observed at 19:47. (Manifest was re-signed after the report's source fixes.)
- **"Undocumented 3rd production GPG key" (419A)** — **COULD-NOT-REPRODUCE.** 419A is documented in `SECURITY.md:19` as the default signing key. The report's claim that it is "undocumented in those files" does not hold.
- **Ghost tracked file `MANIFEST.sha256.3zbNoX`** — **CONFIRMED, still open.** Present in `git ls-files`; `git status` shows it deleted-but-uncommitted (`D`). `git rm --cached` still needed.
- **Stale `post-commit` hook** — **CONFIRMED, still open.** `.git/hooks/post-commit` (2026-08-11) differs from `scripts/post-commit-doc-verify.sh` (2026-08-18). Documented one-command fix not applied.
- **Tests 222/1** — **NOT RE-RUN** (low stakes; the single failure is the long-documented `test_smes_parser_basic`). No reason to doubt.

### Operational integrity (§9 / P4)
- **~18 scheduled skills failing closed against a stale poller image** — **RESOLVED-SINCE-REPORT (remediation applied, clearing in progress).** The report was accurate: the failing runs used image `build-date=20260824T211010Z`, built *before* the manifest sign. Since then the correct order was applied — manifest re-signed **20:25 EDT**, poller image rebuilt **21:30 EDT** (`build-date=20260825T013027Z`). Proof the rebuild fixed it: `personal-notes-import` re-fired at **21:38 EDT** with `ExecMainStatus=0` (success). The failed count has dropped **18 → 16**; the remaining 16 are **stale last-run snapshots** from the 21:05 pre-rebuild fire and will clear on their next scheduled timer (e.g. `ops-brief` next fires 22:05). (My in-image `verify-manifest.sh` "failures" were a test artifact — the image intentionally omits `.gpg`/`addenda` files — not a real gate failure.)

### Divergences from CLAUDE.md (§10)
- **§10.1 `runner-demo` NOT crash-looping** — **CONFIRMED** (`NRestarts=0`, active/running).
- **§10.2 retired mcpo `ctdc_admin_` token now revoked** — **CONFIRMED** (`auth_tokens`: `ctdc_admin_ | admin | revoked`). A separate active admin token `ctdc_dispatch-admin-gate_` now exists — confirmed.
- **§10.3 all tokens never expire** — **CONFIRMED.** `SELECT count(*) FROM auth_tokens WHERE expires_at IS NOT NULL` → **0**; 2 active admin tokens, both NULL expiry. `token_count_active` in `/healthz` = 5, consistent with 2 admin + 1 cert(runner) + 1 cert(demo_recorder) + 1 shares(cowork).
- **§10.4 `CLAUDE.md` now in `DROP_FILES`** — **CONFIRMED** (`scrub-public-tree.py:122`).
- **§10.5 tree/manifest counts** — **CONFIRMED** (776 tracked / 763 manifest now).

---

## NEW FINDINGS NOT IN REPORT

1. 🔴 **P1 remediation is incomplete on the public remote (highest stakes).** Local redaction happened; the public mirror was not re-pushed and still exposes the 6-char fragment of the *current* live NWWS password, which itself was not rotated. The exposure the report identified is, as of this check, **still live on `github.com/CorporateTravelDC/ctdi-dispatch`.** Both the re-push and the rotation are required to actually close it.
2. ⚠️ **Report over-claim — 419A GPG key is documented** (`SECURITY.md:19`), contradicting the report's D1 "undocumented 3rd production key" note. Additionally, the manifest is currently signed by the agent key `CC15…0B37`, not 419A, so the "production manifest key" framing no longer describes the active signature.
3. ℹ️ **This supervisory doc — and the entire `research/` tree — will publish to the public mirror on the next `push-public.sh` run**, since `research/*` is not in `DROP_FILES` (21 such docs already public). If the operator does not want this class of security-narrative doc public, `research/` should be added to `DROP_FILES` before the next public push — the same reasoning that dropped CLAUDE.md.

---

## Compact status

- **Claims checked:** ~27 across auth model, live tier matrix, demo/runner isolation, secrets, public-remote hygiene, manifest/hooks, operational integrity, and CLAUDE.md divergences.
- **CONFIRMED-BY-INDEPENDENT-RECHECK:** ~21 (admin gating, board-write gating, data-usage-open, F1, F3, F4, F5, F7, demo isolation, P2 feeder UUID, P3 CLAUDE.md pre-rotation pw, public remote refs/size/full-value-clean, P5 overshare, vault-hostname, manifest-clean, ghost file, stale post-commit hook, mcpo token revoked, no-expiry, DROP_FILES, and P1 fragment-still-on-public).
- **RESOLVED-SINCE-REPORT:** 3 (F2 chat-history auth gate; F6 osint/feed → T1; P4 stale-image → rebuilt in correct order, clearing on re-fire).
- **COULD-NOT-REPRODUCE:** 1 (419A "undocumented" — it is documented in SECURITY.md).
- **NEEDS-HUMAN-REVIEW:** 1 primary (P1 public re-push + password rotation) + the research/-dir publish-policy question.
- **New findings:** 3 (above).
- **Public remote state:** one branch (`main @ ee341041`), no tags, no other branches, one squashed commit, 717 files, **zero full secret values / GPS coords / private keys** — **but the 6-char fragment of the current live NWWS password is still present**, and 21 security-narrative docs are published.

### Overall verdict
The system's **core security posture is genuinely sound and the test agent's report is trustworthy** — its two HIGH findings were real and have been correctly closed (F2, F6, and the demo mount isolation), its still-open findings all reproduce, and the signed-manifest gate is working as designed. It is **not yet in a fully clean state**, for one concrete reason: the highest-severity hygiene item (P1) is only half-remediated — the burned NWWS password fragment remains live on the public GitHub mirror and the credential has not been rotated to a non-overlapping value. Until the public mirror is re-pushed from the scrubbed tree **and** `NWWS_PASSWORD` is rotated, treat that federal-feed credential as exposed. Secondary cleanups (ghost manifest temp file, stale post-commit hook, GET-based sudo verb, the F3/F4 runner proxy gaps, research/-dir publish policy) remain as the report described.
