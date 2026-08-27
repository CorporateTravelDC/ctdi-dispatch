# Live state check — 2026-08-25 (post-commit 7ef44c4; addendum for 4c0fe5f below)

Scope: targeted doc-drift check after commit `7ef44c4` ("Fix 5 Opus
blind-review findings: SWIM TLS, .pyc integrity gap, admin-path bypass,
ntfy fail-open, FAA LADD privacy wipe" + CLAUDE.md rewritten to
scratchpad-only). **Not** a from-scratch rewrite pass — only checked
whether existing claims in README.md, CLAUDE.md, SECURITY.md,
`src/ingest/README.md`, `src/shared/watchlist_README.md`, and the
evergreen docs under `docs/` were invalidated by this specific change,
verified against the live system (systemctl, podman ps, exec-greps
inside running containers, curl, current source line numbers).

## Headline live finding (not doc drift — deployment gap)

**None of the five code fixes are running anywhere.** Every
`localhost/corporatetraveldc-*` image was built ~21:30 on 2026-08-24 —
before today's fixes. The ingest ×7 / poller / pusher / runner
containers were restarted 14:31–14:32 today (before the 15:11 commit)
from those pre-fix images; web is 18 h old, runner-demo 24 h old.
Verified directly by exec-grep in the running containers:
`swim_client.py` has no `with_certificate_validation` (ingest-fdps),
`ntfy_push.py` still carries the old return-True branch (pusher, poller,
web), `db.py` has no LADD-wipe refusal, runner `/app/main.py` has no
`_normalized_path`, and `PYTHONDONTWRITEBYTECODE` is unset in all six
containers checked. Concretely: **the six live SWIM sessions are still
connecting without certificate validation right now.** The fixes take
effect only after an image rebuild (`build-images.sh`) + container
restarts. CLAUDE.md's checklist claims are accurate about the *code*
(all five confirmed present at HEAD) and it makes no deployment claim,
but anyone reading "fixed, signed" should know the running fleet
predates the commit.

Residual nuance on C-0a: the fix is `ENV PYTHONDONTWRITEBYTECODE=1`
only — it stops *runtime* bytecode writes. There is no
`.containerignore`/`.dockerignore` in the repo, so `COPY src/ ...` can
still bake host `__pycache__` into images; the live runner container
has `/app/__pycache__` baked in (from the old build, but nothing in the
new Containerfiles prevents a recurrence). The 2026-08-18 check's
"images bake host pycache" observation remains true and is unchanged by
this commit.

## Doc drift found (4 items)

1. **`docs/DATA_SOURCES.md` (FAA LADD section, ~:313-314)** — says an
   empty/dead LADD download is treated as "a non-fatal warning" that
   "carries on with the registry import." Superseded: as of `7ef44c4`,
   an empty parse logs at **ERROR** in `faa_registry.py` and
   `db.faa_upsert_ladd()` **refuses** to replace a non-empty list with
   an empty one (existing rows preserved). Still non-fatal to the
   surrounding registry import, and "reliably imports nothing" remains
   true (list is live-empty; upstream endpoint apparently discontinued
   — see CLAUDE.md's open operator decision). The section's code line
   refs (`_FAA_LADD_URL :45`, `_parse_ladd :259`) are still correct
   (the insertion landed after both).

2. **`docs/ALERT_REFERENCE.md` (:128 "401/403 ambiguous-status guard",
   :517 "ambiguous-403 logic")** — the guard's *semantics* changed. It
   used to mark 401/403 as probable-delivery and return success; it now
   logs at ERROR and returns failure, while still suppressing the
   resend inside the 90 s dedup window. The doc never states the old
   return-True behavior explicitly, so no sentence is outright false,
   but "guard" as described no longer implies delivered — worth a
   one-line refresh on the next ALERT_REFERENCE pass.

3. **`docs/CLAUDE_MD_DRIFT_REPORT.md`** — "No drift found," generated
   05:15 today by the daily checker against the *old* 3,800-line
   CLAUDE.md. That file was wholesale-replaced at 15:11 by the
   scratchpad rewrite, so the report describes a file that no longer
   exists. Self-healing (regenerates daily), but note: how
   `check-claude-md-drift.sh` behaves against the new scratchpad
   format is unverified — tomorrow's run is worth a glance. (Separately,
   `corporatetraveldc-docs-drift-weekly.service` is in failed state;
   per CLAUDE.md, its journal wasn't re-investigated this pass.)

4. **`docs/dispatch-runner-design.md`** (line-number staleness only) —
   its 2026-08-23 correction block cites `src/runner/main.py:1498-1507`,
   `:1508-1509`, `:1511-1513`. Those regions now sit at ~1567–1582
   (`_TIER1_PATHS` frozenset + rationale) and 1584–1586 (the stale
   "deliberately excluded" NOTE comment), shifted by this commit's
   +24-line `_normalized_path` insertion plus the 08-24 changes. The
   *substance* verified true at HEAD: watchlist paths are still in
   `_TIER1_PATHS`, and the contradictory NOTE comment the doc flags for
   deletion is still present (still not deleted).

## Checked, no drift

- **README.md** — SWIM sections describe provisioning, credentials,
  thermal shedding, and `tcps://` hosts; makes no claim about TLS
  certificate validation, ntfy delivery semantics, LADD, or the runner
  admin gate. Unaffected.
- **`src/ingest/README.md`** — describes `swim_client.py` (sessions,
  heartbeats, backlog) with no TLS-validation claim. Unaffected.
- **`src/shared/watchlist_README.md`** — commit touched nothing it
  covers (`_fire_ntfy_dual` in shared/watchlist.py is unchanged).
- **SECURITY.md, `docs/COMPLIANCE_SECURITY.md`, `docs/INFRA_MAP.md`** —
  no invalidated claims. The COMPLIANCE_SECURITY signed-manifest
  coverage section and INFRA_MAP §4.2 don't yet *mention* the new
  `PYTHONDONTWRITEBYTECODE` mitigation (additive gap, nothing wrong).
- **`docs/dispatch-runner-design.md` demo-status block** (rewritten in
  this same commit) — re-verified live: `:8005/healthz` → 200,
  `:8001/healthz` → 200, runner-demo `SubState=running`, `NRestarts=0`.
  Accurate.
- **`scripts/pre-commit-README.md`** (also updated in this commit) —
  matches the current skip-pattern list; internally consistent.
- **The two Opus review reports** under
  `docs/investor-materials/v1.5/research/` still describe C-3/C-6/C-31/
  C-0a/C-1 as open — they are dated audit snapshots and CLAUDE.md is
  the fix-status tracker, so this is by design, not drift.
- **Failed units** (re-derived per CLAUDE.md instruction): only
  `corporatetraveldc-docs-drift-weekly` — consistent with the known-bad
  list; pusher and integrity-sweep are healthy, consistent with the
  "all 5 items signed" claim.

## Incidental working-tree observation (left untouched)

At check time the working tree showed a staged (uncommitted)
modification to `scripts/scrub-public-tree.py` and an untracked
`MANIFEST.sha256.DSwFOv` (mktemp-suffix pattern — looks like residue of
an interrupted `sign-manifest.sh` run). Neither was produced by this
check (read-only commands only) and neither was modified, staged, or
cleaned by it. Flagged for the operator.

> **Superseded by the second pass below:** the staged scrubber edit
> became commit `4c0fe5f` at 15:16, and the `MANIFEST.sha256.DSwFOv`
> residue is gone. Only `MANIFEST.sha256` + `.asc` remain on disk.

---

# Addendum — second pass, post-commit 4c0fe5f (~15:17 onward)

Scope: the follow-on commit `4c0fe5f` ("Allowlist illustrative test IPs
(C-2 XFF-spoofing examples) in scrub-public-tree.py") — a 12-line
addition to `ALLOWED_IPV4` in `scripts/scrub-public-tree.py` (five
illustrative IPs from the two Opus review reports: `10.x.x.x`,
`192.168.x.x`, `172.x.x.x`, `169.254.169.254`, `100.64.1.1`, each
justified in an in-file comment). Everything in the first pass above was
independently spot-re-verified where cheap, not just trusted.

## Doc drift from 4c0fe5f: none

No living doc enumerates the scrubber's IP allowlist or claims "all
non-allowlisted IPs are scrubbed" in a way this additive change would
invalidate. `docs/COMPLIANCE_SECURITY.md`'s scrubber mention (`:49`) is
generic ("public-mirror discipline") and still accurate;
`scripts/pre-commit-README.md` covers the pre-commit/pre-push hooks, not
the scrubber, and is untouched by this change. The allowlist entries are
self-documented in-file, consistent with the two pre-existing C-2
example entries (`10.x.x.x`, `10.x.x.x`) allowlisted the same way.

## Live integrity finding: manifest not re-signed after 4c0fe5f

- `scripts/verify-manifest.sh` → **INTEGRITY FAILURE**, exactly one
  file: `scripts/scrub-public-tree.py` (covered at `MANIFEST.sha256:445`;
  the commit changed the script but no re-sign followed).
- `corporatetraveldc-integrity-sweep.service` is now **failed**
  (it was healthy in the first pass above). This matches CLAUDE.md's
  known-bad entry verbatim — "expected while any tracked file sits
  unsigned since the last sign-manifest.sh run; self-clears on next
  sweep after signing" — so it is **not doc drift**; CLAUDE.md's "all 5
  items signed" claim refers to the `7ef44c4` items and remains true.
  The open action is simply a re-sign covering `4c0fe5f` (and this
  file). A `sign-manifest.sh --agent` attempt was auto-launched at the
  end of this pass per the standing approval-gate directive; its
  outcome post-dates this file's content.
- Failed-unit re-derivation: `docs-drift-weekly` (unchanged from first
  pass, journal still uninvestigated per CLAUDE.md) +
  `integrity-sweep` (explained above). `pusher` remains active.

## First-pass claims re-verified live this pass

- **Deployment gap stands.** All six `localhost/corporatetraveldc-*`
  images: created 2026-08-25 01:30–01:31 UTC (= 2026-08-24 ~21:31 EDT),
  i.e. still pre-fix. Direct exec checks in the running pusher:
  `/app/src/common/ntfy_push.py:146-156` still carries the old
  "treating as delivered, NOT resending" 401/403 branch (`return True`
  path), and `PYTHONDONTWRITEBYTECODE` is unset in the container env.
  Nothing has been rebuilt or restarted since the fixes landed; the
  rebuild + restart remains the open deployment step for all five
  `7ef44c4` fixes.
- Working tree at end of this pass: clean except this file (untracked)
  and, if the sign attempt is approved, refreshed `MANIFEST.sha256` +
  `MANIFEST.sha256.asc` as uncommitted edits. Nothing staged or
  committed by either pass.

---

# Addendum — third pass, post-commit ef6b798 (~20:52–20:56 EDT)

Scope: commit `ef6b798` ("Fix C-9 (audit failed admin auth + redact
sensitive detail), C-4 (pin manifest signing-key fingerprint), C-0c
(pre-push URL check)"). Same rule as the passes above: only checked
whether existing doc claims are invalidated by this specific change,
verified live where relevant. Everything here is read-only observation
except this file.

## Headline forward-looking breakage: next image rebuild will fail-close in-container verification

The new `verify-manifest.sh` hard-requires `security/signing.env` (exits
2 "missing … refusing to trust anything" if absent, then `:?`-asserts
both fingerprint vars). But `Containerfile.{web,poller,pusher,ingest}`
each `COPY` only `scripts/verify-manifest.sh`, `scripts/verified-exec.sh`
and `security/trusted-signing-key.pub.asc` — **not `signing.env`**.
Confirmed live: the running poller image's `/app/security/` contains only
the pubkey. Today's images still carry the *old* script (built pre-fix),
so nothing is broken right now — but the **next rebuild** bakes the new
script into images that lack its required input, and every in-container
invocation (`verified-exec.sh` skill preflight; `src/common/llm.py:73-91`
pre-inference gate) will exit 2 fail-closed: skills and LLM inference
refuse to run. That rebuild is *exactly the already-pending deployment
step* for the five `7ef44c4` fixes (deployment gap re-verified below), so
this will bite the moment that step happens unless the Containerfiles
first gain `COPY security/signing.env security/signing.env` (the file is
tracked and carries only public key fingerprints, per SECURITY.md's
"public keys ship in-repo, named by full fingerprint" posture). No doc
claims this can't happen — flagged as a latent break introduced by the
commit, not doc drift.

## Doc drift found (3 items)

1. **`src/shared/watchlist_README.md:73-79`** — says the `require_admin`
   factory "writes an `audit_log` row (actor, action, tier, source IP,
   request body) before the handler runs." Two changes: the stored body
   now passes through `_redact_audit_detail()` (sensitive-keyed values
   blanked, userinfo/sensitive query params stripped from URL-shaped
   values), so "request body" unqualified is stale; and denied non-admin
   attempts now also write a row (`{"result": "denied"}`) before the 403.
   Nothing outright false, but the parenthetical describes pre-C-9
   capture semantics.
2. **`docs/COMPLIANCE_SECURITY.md` RESOLVED block (:358-370)** — same
   staleness: "captures the request body as `detail` for
   POST/PUT/PATCH/DELETE … and writes the row via the existing
   `db.audit()` before the route handler runs" describes unredacted,
   success-only auditing. The 2026-08-19 "Verified live … `detail`
   captured exactly for the POST body" paragraph is a dated snapshot and
   fine as history, but the present-tense mechanism description needs the
   redaction + denied-path sentences. Also `:414` "**Deployed** — the
   `require_admin` factory is live in the running web image" remains true
   for the *factory* but must not be read as covering C-9: the redaction/
   denied-audit code is **not** in the running web image (below).
3. **CLAUDE.md (self-staleness within the commit)** — checkpoint 1 says
   "Pending sign for this checkpoint" and the risk list marks C-9/C-4/
   C-0c "pending sign", but the same commit shipped the re-signed
   `MANIFEST.sha256` + `.asc`: at 20:52 `verify-manifest.sh` → rc 0, all
   769 files (and `integrity-sweep` was not failed). Checkpoint 1 is
   signed; the "pending sign" lines were true mid-batch and stale at
   HEAD. (Superseded minutes later by in-flight checkpoint-2 edits — see
   the concurrent-work note at the end.)

Line-number staleness only, dated snapshots, note-and-move-on:
`docs/COGS_VENDOR_COMPARISON_2026-08-18.md:1552` ("payload-capturing
audit row at `src/auth/auth.py:197`") and
`…/research/GROUND_UP_AUDIT_SECOND_VALIDATION_2026-08-24.md:46`
(":140 / :169-201") — `auth.py` grew +81 lines; both also describe
pre-redaction capture. Same class: the v1.5 investor executive summaries'
"actor/IP/payload capture" audit-trail rows now mean *redacted* payload
capture — worth carrying into the planned investor-docs reverification
pass, not editing dated material now.

## Checked, no drift

- **SECURITY.md "Integrity guarantees"** — fingerprint pinning is purely
  additive to its description; the key inventory and fingerprints are
  unchanged and the manifest still signs with the agent delegate key the
  doc names. C-4's pin was re-verified live this pass: `verify-manifest.sh`
  rc 0 against the real signed manifest at 20:52. One residual worth the
  same threat-model honesty the script itself uses: `signing.env` is a
  *tracked* file, so an adversary who can write tracked files can move
  the pin along with the key — the pin defends against key substitution
  that doesn't also touch `signing.env` (and edits there are
  git-visible), not against a full tree-writer.
- **`scripts/pre-commit-README.md:43`** — "blocks any direct `git push
  public`" still true and now broader (raw-URL invocation of the same
  destination is also caught). Re-verified live by direct hook
  invocation: `pre-push <public-url> <public-url>` → blocked, rc 1;
  `pre-push origin <origin-url>` → rc 0. Additive; a one-line enrichment
  is optional, nothing false.
- **`pre-commit-README.md:16` sync claim + CLAUDE.md's "synced to
  `.git/hooks/pre-push`"** — re-verified: `scripts/pre-push` and
  `scripts/pre-commit` are byte-identical to their `.git/hooks` copies.
- **README.md** (`require_admin` route counts :257, manifest-baking
  :583) and **`docs/auth-token-proxy-pattern.md:216`** — reference
  `require_admin` only for which routes it gates; no call-site or
  gating change in this commit. Unaffected.
- **The two Opus reports** still describe C-9/C-4/C-0c as open — dated
  audit snapshots by design, CLAUDE.md is the fix-status tracker.
- **New tests** — `tests/auth/test_require_admin_audit.py`: 5 passed
  (run this pass).

## Deployment gap re-verified (third time; changed shape tonight)

All `localhost/corporatetraveldc-*` images are **still the 2026-08-25
01:30–01:31 UTC builds** (= 08-24 ~21:31 EDT, pre-*everything* from
today). What changed: ingest ×7, poller, pusher, runner and the poller
one-shots were **restarted 20:30–20:45 EDT tonight from those same old
images** — consistent with recovery from the 18:21 tier-2 LOCKDOWN shed
CLAUDE.md records (and indeed pusher/poller are no longer failed; the
only failed unit now is `docs-drift-weekly`, unchanged). Web was *not*
restarted (up 23 h). Exec-confirmed in the running containers: web
`auth.py` has no `_redact_audit_detail` and no denied-audit branch (C-9
not deployed — and web wouldn't have picked it up anyway; the restarts
predate the 20:51 commit), ingest-fdps `swim_client.py` has no cert-
validation fix, pusher `ntfy_push.py` still has the old
"treating as delivered" branch, `PYTHONDONTWRITEBYTECODE` unset. So the
rebuild+restart step now covers **six** container-code fixes (five from
`7ef44c4` + C-9). C-4 and C-0c are host-side scripts and are effective
immediately on the host — no deployment step needed (but see the
headline: the rebuild that deploys the six must add `signing.env` to the
images first).

## Concurrent work observed mid-check (left untouched)

Between 20:52 (verify rc 0, tree clean) and 20:55, the working tree
gained uncommitted edits with checkpoint-2 shape: `src/common/sr2_gate.py`
+ six poller skills modified (+78/−26) and untracked
`tests/common/test_sr2_gate_crash_safety.py` — i.e. C-7 work in flight
by another session. `verify-manifest.sh` correspondingly flipped to rc 1
(7 files FAILED) — the expected transient per CLAUDE.md's known-bad
`integrity-sweep` pattern; it will trip the 15-min sweep until the
checkpoint-2 sign. Nothing here was produced, modified, staged, or
signed by this check (read-only commands + this file only).

---

# Addendum — fourth pass, post-commit a0d6c2a (~21:11–21:15 EDT)

Scope: commit `a0d6c2a` ("Fix C-7 (SR-2 gate crash-safety), C-5 (board
thread anonymous-read gating), C-14 (NWWS push-row expiry scoping)") —
the checkpoint-2 batch whose in-flight edits the third pass saw land.
Same rule as the passes above: only checked whether existing doc claims
are invalidated by this specific change, verified live where relevant.
Read-only except this file.

## Doc drift found (3 items)

1. **`README.md:618-620` (Skill runtime rules, SR-2)** — "call
   `hash_gate()` before any expensive computation or LLM call … If it
   returns `"skipped"`, `sys.exit(0)` immediately." `hash_gate()` no
   longer exists anywhere in `src/` (confirmed: only a docstring mention
   of the old name survives). The contract is now two calls:
   `check_gate()` (read-only, returns `(gate_result, current_hash)`
   tuple, not a bare string) + a mandatory `commit_gate(skill_name,
   current_hash)` after the guarded work succeeds — a skill written to
   README's current instruction would crash on import and, if
   pattern-matched loosely, would silently reintroduce the C-7
   crash-suppression bug by never committing. This is the one evergreen
   doc actively teaching the dead API. (README `:594` "--force bypasses
   the SR-2 hash gate" and `:783` state-dir row remain true.)
2. **`docs/lmstudio-dispatch-prompts.md:329`** — "SR-2 `hash_gate()` —
   unchanged." Both halves now false: the name is gone and the gate API
   is exactly what changed in this commit. A skill ported per this doc's
   SR-1/SR-2 compatibility section needs the check/commit split.
3. **`README.md:287` (Tier-0 table)** — "`GET /api/v1/board*` |
   Coordination board (read; posts need `X-Board-Key`)" listed in the
   **Tier 0 — Anonymous** selection. At HEAD this is true only for
   `thread=coord`; every other thread (including `research`) now 403s at
   Tier 0 (`_BOARD_ANONYMOUS_THREADS = {"coord"}`, default-gated for
   future threads). The row needs a "coord thread only" qualifier.
   The `:256` "37 are anonymous" AST-derived count is self-flagged as
   re-derivable and `board_get` still has no `require_tier` dependency
   (it resolves tier in-body), so the count methodology likely still
   classes it anonymous — arguably right for the default thread;
   note-and-move-on.

CLAUDE.md self-staleness, same pattern as checkpoint 1 last pass:
checkpoint 2 says "Pending sign" but `a0d6c2a` itself shipped the
re-signed `MANIFEST.sha256` + `.asc` — at 21:14 `verify-manifest.sh`
fails on exactly one file, the *uncommitted* checkpoint-3 scrubber edit
(below), and everything from `a0d6c2a` verifies. Checkpoint 2 is signed.

Dated snapshots, note-and-move-on (by design, not drift):
`docs/DRIFT_AUDIT_2026-08-16.md:82-87` ("sr2_gate hash poisoning" —
that exact bug is now fixed at HEAD, nine days after the audit flagged
it); `docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md:62/:360` F7 "board
reads Tier-0 … accepted by design" — the acceptance is now narrowed to
`coord` only, so F7 should not be cited as blanket cover for board
anonymity in future passes; the two Opus reports still describe
C-5/C-7/C-14 as open (CLAUDE.md is the fix-status tracker).

## Checked, no drift

- **C-14 / NWWS expiry** — no evergreen doc describes
  `expire_nws_alerts()`'s sweep semantics at all, so nothing to
  invalidate. `docs/DATA_SOURCES.md` NWWS-OI section,
  `src/ingest/README.md`, and `addenda/wpc_forecast_discussions/`
  describe the push ingest path, which is untouched (fix is in the REST
  poller's expiry call). Unaffected.
- **`src/shared/watchlist_README.md`** — commit touched nothing it
  covers.
- **`docs/COMPLIANCE_SECURITY.md`** board mentions (`:144`
  `dispatch-board-public-bypass` CF rule, `:268` `board_refresh` audit
  event) — unaffected; the edge bypass is still consistent with C-5
  because the new gating is app-tier. `docs/HEADLESS_ACCESS.md` never
  mentions the board.
- **`osint_monitor.py`** comment-only change ("no check_gate/commit_gate
  needed") — consistent with its documented INSERT-OR-IGNORE dedup
  design; no doc describes it otherwise.
- **CLAUDE.md checkpoint-2 claims verified at source:** `check_gate`/
  `commit_gate` split present; all 5 real call sites
  (`cps_recompute`, `train_impact`, `flight_impact`, `route_impact`,
  `tfr_enrichment`) call `commit_gate`; `_BOARD_ANONYMOUS_THREADS`
  allowlist present in `src/web/main.py`; `NOT LIKE 'nwws:%'` scoping in
  `db.expire_nws_alerts()`. All 9 new tests pass (4+4+1), and the full
  suite reproduces CLAUDE.md's claim exactly: **241 passed / 1
  pre-existing unrelated failure** (`test_smes_parser_basic`), run under
  the documented `PYTHONPATH=src` convention (README `:591`; without it
  the new `common.*`-importing tests fail collection — invocation
  requirement, not a defect).

## Deployment gap re-verified (fourth time) — C-5 exposure still live

All `localhost/corporatetraveldc-*` images are **still the 2026-08-25
01:30–01:31 UTC builds** (= 08-24 ~21:31 EDT). Web has been up 24 h;
poller/pusher/ingest/runner restarted ~20:33 EDT from those same old
images (pre-commit — `a0d6c2a` landed 21:10). Exec-confirmed absent in
the running containers: web `main.py` has no `_BOARD_ANONYMOUS_THREADS`,
poller `db.py` has no `NOT LIKE 'nwws:'`, poller `sr2_gate.py` has no
`commit_gate`. Live consequence, verified at 21:14 EDT:
**`GET /api/v1/board?thread=research` with `X-CTDI-Public: 1` →
200 with all 6 research messages — the exact C-5 exposure is still
serving anonymously right now**, and the C-14 push-row wipe + C-7
write-before-work gate behavior are likewise still what runs. The
pending rebuild+restart now covers **nine** container-code fixes (five
from `7ef44c4`, plus C-9, C-7, C-5, C-14). The third pass's headline
still applies to that rebuild: the Containerfiles must first gain
`COPY security/signing.env` or the new `verify-manifest.sh` will
fail-close all in-container skill/LLM execution.

## Failed units + concurrent work (left untouched)

Failed units re-derived: `docs-drift-weekly` (unchanged, journal still
uninvestigated per CLAUDE.md) + `integrity-sweep` — the latter matches
its known-bad pattern: the working tree holds an uncommitted +62-line
edit to `scripts/scrub-public-tree.py` (OOXML decompress-scrub-repackage
— i.e. **C-30, checkpoint 3, in flight by another session**), which is
the single file failing `verify-manifest.sh`. Nothing was produced,
modified, staged, committed, or signed by this check (read-only
commands + this file only).

Mid-check update (~21:16): the other session's checkpoint-3 work
advanced while this pass ran — the scrubber edit grew to +115/−23 and
was **staged**, together with a staged rename of the C-14 test's
synthetic REST alert IDs (`urn:oid:2.49.0.1.840…` → `urn:oid:REST-alert-
840-…`, presumably scrub/hook hygiene on the OID-like shape; the test's
assertions are otherwise unchanged). The suite/verify results above
predate those staged edits by minutes. Staging was done by that session,
not this check.

---

# Addendum — fifth pass, post-commit c4af022 (~21:33–21:45 EDT)

Scope: commit `c4af022` ("Fix C-30: scrub-public-tree.py now decompresses
and scrubs .docx/.pptx OOXML content instead of treating it as opaque
binary") — the checkpoint-3 batch whose staged edits the fourth pass saw
in flight, plus the two incidental test-fixture IP/OID renames and the
re-signed manifest. Same rule as the passes above: only checked whether
existing doc claims are invalidated by this specific change, verified
live where relevant. Read-only except this file (the scrubber preflight
run below creates only dangling git objects — nothing staged, committed,
or pushed).

## Headline live finding: the C-30 leak is still published — the fix gates future pushes only

- **All 17 `.docx`/`.pptx` files on the public GitHub mirror's tip
  (`public/main` = `eebd1f4`) still carry FORBIDDEN_LITERALS (the
  operator's real name/domain) in their decompressed XML** — verified
  directly this pass by running the new `_ooxml_text_parts()` extraction
  against every OOXML blob in the public tip tree. The public tip was
  pushed at **18:52 EDT today, ~2.5 h before the fix landed (21:32)**,
  so it was scrubbed with the old byte-level no-op scrubber.
- All three commits of public history (08-24 18:51, 08-24 21:53, 08-25
  18:52) carry the same 17 files, all produced by the broken scrubber.
  `push-public.sh` parents each push on the public mirror's own current
  tip, so a plain corrective re-push adds a clean fourth commit but
  leaves the three leaky trees **reachable in public history on
  GitHub**. Full remediation is an operator decision: re-push cleans the
  tip; purging history needs a public-branch reset first (delete/reset
  `public/main` → `push-public.sh` then creates a fresh orphan root, a
  path the script already supports), and even then previously-fetched
  clones and GitHub's unreachable-object retention are outside our
  control.
- This is **not doc drift** — CLAUDE.md's C-30 entry accurately frames
  the fix as the preflight gating *future* publishes and claims no
  retroactive cleanup. Flagged because nothing else in the repo records
  that the already-published leak persists until a re-push happens.

## Doc drift found (1 item)

1. **CLAUDE.md self-staleness (third occurrence of the same pattern)** —
   checkpoint 3 says "Pending sign for this checkpoint," but `c4af022`
   itself shipped the re-signed `MANIFEST.sha256` + `.asc`:
   `verify-manifest.sh` → OK, all 773 files, at 21:33; and
   `corporatetraveldc-integrity-sweep` — failed at the start of this
   check — **self-cleared mid-check** (21:34:06 "sweep OK … all 773
   files match"), exactly per its known-bad entry. Checkpoint 3 is
   signed.

Note-and-move-on: `docs/TAILNET_MIGRATION_INVENTORY.md:161-164` cites
`scrub-public-tree.py:84/:87/:235/:237` for the old-tailnet literals —
those refs were already stale *before* this commit (the literals sit at
:214/:222/:573 today after weeks of file growth; this commit added +2
import lines at the top and ~+130 mid-file). Substance intact: both old
and new tailnet suffixes are still in the substitution/forbidden lists.
The two Opus reports still describe C-30 as open — dated audit snapshots
by design, CLAUDE.md is the fix-status tracker.

## Checked, no drift

- **No evergreen doc describes the scrubber's binary/OOXML handling**,
  so there was nothing for the fix to invalidate. All scrubber mentions
  re-read this pass are generic and still accurate: SECURITY.md:66,
  `docs/COMPLIANCE_SECURITY.md:49` ("public-mirror discipline"),
  `docs/SECOND_BRAIN_STATUS.md:704-706` (push-public mechanism,
  unchanged), `docs/ALERT_REFERENCE.md:752`, and
  `docs/GPS_COORDINATE_CONFIGURATION.md:57` — whose warning that the
  fixed-literal list "cannot catch a new or rotated coordinate" remains
  true: C-30 widened *where* known literals are found, not *what* is
  known.
- **README.md, `scripts/pre-commit-README.md`** — no scrubber-behavior
  claims at all (grep-verified). **`src/ingest/README.md`,
  `src/shared/watchlist_README.md`** — commit touches nothing they
  cover.
- **CLAUDE.md checkpoint-3 claims verified at source and live:**
  `_scrub_ooxml_bytes()` wired into `scrub_blob()` (extension-dispatched,
  with a bad-zip fallback that still byte-scrubs rather than passing
  through); `_ooxml_text_parts()` wired into `verify_scrubbed()` with
  `check_uuids=False` for decompressed parts only. The changed tests
  pass (11 = 5 new OOXML + the two edited fixture files' 6), and the
  full suite reproduces CLAUDE.md's claim exactly: **246 passed / 1
  pre-existing unrelated failure** (`test_smes_parser_basic`), under the
  documented `PYTHONPATH=src` convention. End-to-end preflight
  re-verified: ran `scrub-public-tree.py` against HEAD's real tree — rc
  0, scrubbed tree emitted, `verify_scrubbed()` (which now scans
  decompressed OOXML parts) passed clean. The next `push-public.sh` run
  will therefore publish clean decks.
- **Hook sync** — `scripts/pre-push` and `scripts/pre-commit` still
  byte-identical to their `.git/hooks` copies.
- **Renamed fixture values** — no doc anywhere references the old
  `10.x.x.x` test IP; the OID rename was covered in the fourth pass.

## Deployment gap: unchanged — and C-30 is exempt from it

The scrubber is a host-side script invoked by `push-public.sh`, so C-30
is **effective immediately** — no image rebuild involved. The gap itself
is unchanged: newest `localhost/corporatetraveldc-*` images are still
the 2026-08-25 01:30–01:31 UTC builds, so the pending rebuild+restart
still covers the nine container-code fixes (7ef44c4's five + C-9, C-7,
C-5, C-14), and the third pass's prerequisite stands: the Containerfiles
must gain `COPY security/signing.env` before that rebuild or the new
`verify-manifest.sh` fail-closes in-container skill/LLM execution.

## Failed units + concurrent work (left untouched)

Failed units re-derived: `docs-drift-weekly` only (unchanged, journal
still uninvestigated per CLAUDE.md); `integrity-sweep` failed at check
start and self-cleared at 21:34:06 as described above. Mid-check
(~21:40), the working tree gained uncommitted edits with
**checkpoint-4 / C-2 shape**: `src/runner/main.py`,
`Containerfile.runner`, and `nginx/conf.d/tailscale-dispatch-runner.conf`
modified — the next batch in the operator's fix order, in flight by
another session. Those edits will trip `verify-manifest.sh`/the sweep
transiently until the checkpoint-4 sign, per the known-bad pattern.
Nothing was produced, modified, staged, committed, or signed by this
check (read-only commands + this file only).

---

# Addendum — sixth pass, post-commit 1419396 (~21:47–21:50 EDT)

Scope: commit `1419396` ("Fix C-2: host-scope CF-Connecting-IP trust,
close identical bug in `_client_ip()`, stage nginx/uvicorn header
hardening") — the checkpoint-4 batch whose in-flight edits the fifth pass
saw. Same rule as the passes above: only checked whether existing doc
claims are invalidated by this specific change, verified live where
relevant. Read-only except this file. The one live mutation risk this
pass took was two `GET /api/whoami` probes against `:8001` with a spoofed
`CF-Connecting-IP` (read-only endpoint, same probe the Opus supervisory
notes used).

## Headline live finding: the nginx half of C-2 is ALREADY deployed — CLAUDE.md's "NOT yet deployed live" is half-stale

CLAUDE.md's checkpoint-4 entry says items 3–4 (uvicorn flag + nginx conf)
are "staged in the tracked repo but NOT yet deployed live," pending the
operator's sudo. Half of that is already done:

- **Item 4 (nginx) IS live.** `/etc/nginx/conf.d/tailscale-dispatch-runner.conf`
  is byte-identical to the repo copy (including the C-2 comment and
  `X-Forwarded-For $remote_addr`), mtime **21:44:03**, and nginx's
  `ExecReload` ran at **21:44:04, rc 0** — one minute before the 21:45
  commit. The operator evidently did the sudo copy+reload right before
  the commit landed; CLAUDE.md was written mid-batch and never updated.
- **Item 3 (runner image) is NOT deployed** — and neither are items 1–2
  (the code fix). Both runner containers (`systemd-corporatetraveldc-runner`,
  restarted 21:31 EDT — before the fix existed in the tree — and
  `-runner-demo`, up 31 h) run image `c7d39febd0b9` built 2026-08-25
  01:31 UTC (= 08-24 ~21:31 EDT). Exec-confirmed: `/app/main.py` has no
  `_CLOUDFLARE_FRONTED_HOSTNAMES` in either container, and both PID-1
  cmdlines still carry `--forwarded-allow-ips=*`.
- **Live spoof re-test confirms the old behavior still runs on `:8001`:**
  from loopback, `CF-Connecting-IP: 8.8.8.8` → `{"tailnet":false}` and
  `CF-Connecting-IP: 100.64.1.1` → `{"tailnet":true}` — i.e. the raw
  header still solely decides trust with no host scoping (a loopback
  caller being *demoted* by a spoofed public IP proves the header is
  honored unconditionally). Net live posture: the vhost path no longer
  forwards client XFF, but direct `:8001` callers can still spoof both
  headers until the runner image is rebuilt+restarted.

## Doc drift found (5 items)

1. **`README.md:333-336` ("Runner API (port 8001, Tailnet-only)")** —
   "gated by `_is_trusted()` (… `CF-Connecting-IP` honored exclusively
   when present)." Stale at HEAD: the header is now honored only when the
   request's `Host` is in `_CLOUDFLARE_FRONTED_HOSTNAMES`
   (`{"dispatch-runner.example.com"}`) — on the tailnet
   instance this section describes, it is now *never* honored (tailnet
   `.ts.net` name / bare IP fall through to the direct-IP/XFF check).
2. **`docs/dispatch-runner-design.md:59-63` ("Auth model" §1)** — "client
   IP from `CF-Connecting-IP` exclusively when present (never falls
   through to loopback), else socket peer." Same staleness as above, and
   `_client_ip()` no longer has a CF branch at all. (Line-ref note: the
   doc's 2026-08-23 correction-block refs shifted again — main.py grew a
   net +25 lines from this commit, so the `_TIER1_PATHS` region the
   first pass located at ~1567–1586 now sits ~25 lines lower.)
3. **`docs/auth-token-proxy-pattern.md:50-66` (2026-08-23 precision
   note)** — "`_is_trusted()` … is a pure IP classifier —
   `CF-Connecting-IP` when present, else
   `request.client.host`/`X-Forwarded-For`." Now host-scoped, not pure
   header-preference. More practically, **step 3 of the doc's
   verification recipe (:392-394)** — "from an untrusted origin (or with
   `CF-Connecting-IP` set to a public address), confirm the endpoint
   403s" — no longer works once the fix deploys: on the tailnet instance
   the spoofed header is ignored, so a tailnet tester following that
   recipe gets their real (trusted) classification and could wrongly
   conclude an operator-only path is public-open. The recipe needs a
   genuine untrusted origin (or a test-client `Host` override).
4. **`docs/COMPLIANCE_SECURITY.md:824-843`** — the `_is_trusted`
   paragraph says the CF branch is "currently unreachable in practice"
   and the check was "**Left as-is** (not rewritten to the marker model)
   since it isn't exploitable today." Both halves are invalidated: C-2
   demonstrated the branch was reachable by any caller who simply sent
   the header (the Opus supervisory notes' live spoof, re-reproduced
   this pass), and as of
   `1419396` it has NOT been left as-is — it is host-scoped. The
   closing recommendation ("if re-exposed publicly, needs `X-CTDI-Public`
   treatment, not a revival of IP-header trust") is still sound advice
   but now describes a superseded state.
5. **CLAUDE.md self-staleness (fourth occurrence of the pattern)** —
   checkpoint 4 says "Pending sign for this checkpoint" and "items 3-4
   … NOT yet deployed live." Both stale: `1419396` itself shipped the
   re-signed manifest (`verify-manifest.sh` → OK, all 773 files, at
   21:48; `integrity-sweep` not failed), and item 4 is deployed per the
   headline. Checkpoint 4 is signed; only the runner rebuild/restart
   (items 1–3 taking effect) remains.

Dated snapshots / note-and-move-on (by design, not drift):
`docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md` F5 ("runner trust forgeable
via client `CF-Connecting-IP` — STILL OPEN") is now fixed at HEAD though
not yet in the running image — like F7 last pass, F5 should no longer be
cited as open-by-design in future passes. The v1.5 investor materials'
"trust boundary currently depends on a Cloudflare header with **no
app-layer backstop** — named as a hardening candidate"
(executive-protection `pitch-deck.md:50`, similar lines in the
due-diligence FAQs) now understates the code: the app-layer backstop
exists at HEAD — carry into the planned investor-docs reverification
pass rather than editing versioned material now.
`docs/TAILNET_MIGRATION_INVENTORY.md:152-154`'s conf line refs (`:2/:6/:26`)
shifted again (+6 comment lines; second `server_name` now `:34`) — same
already-stale class as last pass. The two Opus reports still describe
C-2 as open — CLAUDE.md is the fix-status tracker.

## Checked, no drift

- **`docs/HONEYPOT_FAIL2BAN.md`** — its `CF-Connecting-IP` resolution is
  nginx's own `map` in `00-honeypot.conf` (untouched by this commit),
  not the runner's Python trust code. On CF-fronted vhosts nginx-side
  CF-Connecting-IP remains authoritative. Unaffected.
- **`docs/COMPLIANCE_SECURITY.md:331`** — "`--forwarded-allow-ips=*`"
  describes **`Containerfile.web`**, which this commit did not touch
  (grep-confirmed still `*` there). Still accurate. (Observation, not
  drift: web/uvicorn thus retains the same wildcard-forwarded-IPs
  pattern C-2 just tightened on the runner — but web's trust decisions
  ride the `X-CTDI-Public` marker, not XFF-derived IP, per the same
  doc's fix narrative. A candidate for the Low-tail triage, not a doc
  problem.)
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — commit
  touches nothing they cover (grep-confirmed no trust/header claims).
- **Public-path consistency of the fix** (live-only, repo-untracked
  `/etc/nginx/conf.d/dispatch-runner.example.com.conf`) —
  it sets `proxy_set_header Host $host`, so once the new image deploys,
  the public demo hostname still lands in `_CLOUDFLARE_FRONTED_HOSTNAMES`
  and CF-set `CF-Connecting-IP` keeps working there; the tailnet vhost
  also passes `Host $host` (the `.ts.net` name → correctly not
  allowlisted). The fix is consistent with the live topology. (That
  public vhost still uses `$proxy_add_x_forwarded_for` — pre-existing,
  tunnel-only where the CF header is authoritative, and that file being
  untracked is already known via C-34 in the Low tail.)
- **CLAUDE.md checkpoint-4 claims verified at source and live:**
  `_CLOUDFLARE_FRONTED_HOSTNAMES` allowlist + host-scoped check in
  `_is_trusted()`; `_client_ip()`'s CF branch removed;
  `Containerfile.runner` → `--forwarded-allow-ips=127.0.0.1`; nginx conf
  → `$remote_addr`. `tests/runner/test_proxy_dispatch.py`: 10 passed.
  Full suite reproduces CLAUDE.md's claim exactly: **249 passed / 1
  pre-existing unrelated failure** (`test_smes_parser_basic`), under the
  documented `PYTHONPATH=src` convention.
- **Hook sync** — `scripts/pre-push`/`pre-commit` still byte-identical
  to `.git/hooks` copies. **Health**: `:8001/healthz` and `:8005/healthz`
  both 200.

## Deployment gap re-verified (sixth time)

Newest `localhost/corporatetraveldc-*` images are still the 2026-08-25
01:30–01:31 UTC builds. The pending rebuild+restart now covers **ten**
container-code fixes (`7ef44c4`'s five + C-9, C-7, C-5, C-14 + C-2's
`main.py` fix) **plus** the `Containerfile.runner` uvicorn-flag change,
which only takes effect on rebuild. The third pass's prerequisite still
stands: Containerfiles must gain `COPY security/signing.env` before that
rebuild, or the new `verify-manifest.sh` fail-closes in-container
skill/LLM execution. The nginx half of C-2 is the one piece of today's
work already fully live.

## Failed units (left untouched)

Re-derived per CLAUDE.md instruction: `corporatetraveldc-docs-drift-weekly`
only (unchanged all day; journal still uninvestigated per CLAUDE.md).
`integrity-sweep` healthy — consistent with checkpoint 4 being signed.
Nothing was produced, modified, staged, committed, or signed by this
check (read-only commands + the two whoami probes + this file only).

---

# Addendum — seventh pass, post-commit 0cd894e (~22:25–22:30 EDT)

Scope: commit `0cd894e` ("Fix C-4 regression: copy security/signing.env
into all 6 affected Containerfiles") **plus** the operator-directed
full-stack rebuild+restart at ~22:00–22:11 EDT that landed between the
sixth pass and this commit. Same rule as the passes above: only checked
whether existing doc claims are invalidated, verified live where
relevant. Read-only except this file; live probes were the same
read-only `whoami`/board/healthz curls used in earlier passes.

## Headline live finding: the deployment gap is CLOSED — all ten fixes now run

The standing headline of all six prior passes is over. Every
`corporatetraveldc-*` compose-role container was restarted ~22:11 EDT
from images built 21:53–22:16 EDT tonight (runner first at ~21:53, the
six commit-affected images 22:01–22:16). Exec-verified in the running
containers: runner `main.py` has `_CLOUDFLARE_FRONTED_HOSTNAMES` and
PID 1 runs `--forwarded-allow-ips=127.0.0.1`; web has
`_BOARD_ANONYMOUS_THREADS`; poller has `commit_gate` and the
`nwws:` expiry scoping; ingest-fdps `swim_client.py` has certificate
validation; pusher `ntfy_push.py` has the fixed 401/403 branch;
`PYTHONDONTWRITEBYTECODE=1` is set. Behavioral retests confirm the two
previously-live exposures are gone:

- **C-2:** from loopback against `:8001`, `CF-Connecting-IP: 8.8.8.8` →
  `{"tailnet":true}` — the spoofed header is now *ignored* (sixth pass:
  the same probe demoted the caller to `false`, proving unconditional
  header trust). All three probes (spoof-public, spoof-CGNAT, no
  header) return the caller's real loopback classification.
- **C-5:** anonymous (`X-CTDI-Public: 1`) `GET /api/v1/board?thread=
  research` → **403**; `thread=coord` → 200. The research-notes
  exposure that was still serving at 21:14 is closed.
- Health: `:8000`, `:8001`, `:8004`, `:8005` healthz all 200.

## The commit itself: third pass's predicted breakage happened, then this fixed it

The forward-looking break flagged in the third pass ("next rebuild will
fail-close in-container verification — Containerfiles lack
`signing.env`") materialized exactly on schedule: journal shows
`corporatetraveldc-demo-api` crash-looping from 22:00:03 with
`verify-manifest: missing security/signing.env -- cannot verify,
refusing to trust anything`. `0cd894e` is the fix — `COPY
security/signing.env` added to all 6 affected Containerfiles
(fingerprints only, no private material; runner correctly untouched, it
ships no verify tooling at all). Verified live: `signing.env` present
in `/app/security/` of web, poller, pusher, ingest, demo, demo-api, and
amtrak-tracker containers; the scoped in-container check
(`verified-exec.sh`'s exact invocation) returns **rc 0** in web,
poller, and demo-api; demo-api is up, healthy, `NRestarts=0`; and two
verified-exec skill quadlets (`board-sweep`,
`tbfm-arrival-enrichment`) fired to "Finished" at 22:15 on the new
images. (Runner returns 127 for the same check — no
`scripts/`/`security/` in that image by design, matching
`docs/INFRA_MAP.md:337` exactly; not a regression.)

Minor build-order nuance, no action needed: the new images were built
21:53–22:16, *before* the 22:23 re-sign, so they bake the
checkpoint-4-signed manifest, not HEAD's. The two manifests differ only
in Containerfile/`signing.env` entries, which are outside the scoped
in-container check (and Containerfiles aren't baked), so every
in-container verification passes — confirmed by the rc-0 results above.

## Doc drift found (1 item)

1. **CLAUDE.md checkpoint-4 deployment claims (fifth occurrence of the
   self-staleness pattern, now with internal inconsistency)** — the
   checkpoint-4 bullet still says items 3–4 are "staged in the tracked
   repo but NOT yet deployed live" and the runner rebuild "wasn't done
   this pass either, pending operator confirmation," and still says
   "Pending sign for this checkpoint." All stale: the nginx half was
   live at 21:44 (sixth pass), the runner image/uvicorn half deployed
   in tonight's rebuild, checkpoint 4 was signed in `1419396`, and the
   *same file's own known-bad section* — added by this very commit —
   records the 22:00–22:11 rebuild. C-2 is now fully live end-to-end;
   nothing about it remains pending except nothing.

Note-and-move-on: this file's own prior standing lines ("pending
rebuild covers ten fixes," "Containerfiles must first gain `COPY
security/signing.env`") are all resolved as of tonight — dated
observations, superseded by this addendum, left as history.

## Checked, no drift

- **`docs/INFRA_MAP.md` §4.2 and `docs/COMPLIANCE_SECURITY.md`
  "Signed-manifest coverage"** — the exact surface this commit touched.
  Both survive intact: the commit only *adds* a `COPY` line; every
  substantive claim (web/poller/pusher/ingest launch via bare `CMD`
  with no startup check; runner ships no verify tooling; enforcement =
  skills at exec time + `llm.py` at inference + 15-min sweep) re-reads
  true against the new Containerfiles and the running fleet. The
  "NEEDS OPERATOR DECISION" block about start-time vs after-the-fact
  enforcement is likewise unchanged in substance.
- **README.md `:576-585` signed-manifest note** — coverage description
  unchanged and still accurate; its "sign → build → restart" ordering
  warning is unaffected (tonight's demo-api crash-loop was a
  missing-input failure, not the unsigned-manifest failure that note
  warns about, and the note claims nothing about file inventory).
- **SECURITY.md** — key inventory and "public fingerprints only,
  no private material in-repo" posture unchanged; baking `signing.env`
  (fingerprints only) into images is consistent with it, as the
  commit's own Containerfile comment argues. `docs/INFRA_MAP.md:873`'s
  key-inventory row likewise unaffected.
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** —
  neither mentions Containerfiles or image contents (grep-confirmed).
- **`docs/COMPLIANCE_SECURITY.md:414` "Deployed — `require_admin`
  … live in the running web image"** — the third pass's caveat (C-9
  redaction not yet in the image) is now resolved; the claim is fully
  true again with no edit needed.
- Residual observation, not drift: `verified-exec.sh`'s scoped check
  still verifies only `src/` + its three original files —
  `security/signing.env` is *consumed* by the check but not itself
  hash-verified in-container. Same trust posture as the sixth pass's
  signing.env note (tracked file, git-visible edits); candidate for the
  Low-tail triage.

## Failed units (left untouched)

Six failed, all explained, none a regression: `docs-drift-weekly`
(unchanged all day); `integrity-sweep` (failed 22:19:08 on the 7
then-unsigned Containerfile edits — mid-batch window before the 22:23
sign; host `verify-manifest.sh` → rc 0 at HEAD now, so it self-clears
on the next 15-min sweep); and 4 of the 6 rebuild-window kills from
CLAUDE.md's new known-bad entry (`feed-db-integrity-check`,
`ingest-feed-watch`, `ops-brief`, `trains-yachts-daily-watch`) —
awaiting their next scheduled fire. The other two (`board-sweep`,
`tbfm-arrival-enrichment`) already self-cleared at 22:15 exactly as
that entry predicts, which also validates the entry itself. Nothing was
produced, modified, staged, committed, or signed by this check
(read-only commands + read-only curl probes + this file only).

# Addendum — eighth pass, post-commit 521ba5b (~23:18–23:35 EDT)

Scope: commit `521ba5b` ("Fix C-8 + 15 Med/Low findings" — checkpoint 5,
the full Med/Low triage batch: CORS, openapi.json, SSRF, unbounded-scan
DoS, dead GUFI-override, board-nonce race, table retention, dedup-file
growth, GPS scrubber gap, PII in scrub_rules.py, token expiry defaults,
hook drift + automated check). Same rule as all prior passes: only
checked whether existing doc claims are invalidated, verified live where
relevant. Second-brain search first (semantic + literal) confirmed no
prior pass has covered this commit; built on pass 7's closure of the
deployment gap. Read-only except this file and one second-brain note.

## Headline live finding: the deployment gap has RE-OPENED for checkpoint 5

Pass 7's "deployment gap is CLOSED" was true for exactly one commit.
`521ba5b` landed 23:15:59 EDT — an hour after the 22:00–22:11 full-stack
rebuild+restart — so **none of this batch's container-side fixes are
running anywhere**. Verified live, not inferred from timestamps alone:

- **C-12 still exposed on all four apps**: `GET /openapi.json` → 200 on
  web `:8000` (77.8 KB, full admin-path schemas), runner `:8001`
  (28.5 KB), demo `:8004` (17.7 KB), demo-api `:8005` (28.5 KB). The
  demo pair is the public-facing surface the commit's own comment calls
  "the most internet-exposed of the three."
- **C-11 still live**: web `:8000` answers
  `access-control-allow-origin: *` to an arbitrary
  `Origin: https://evil.example.com`.
- Exec-greps in the running containers: web `main.py` has no
  `_CORS_ALLOWED_ORIGINS`/`openapi_url`/`init_db_v38`; poller has no
  `/app/skills/retention_prune.py` (C-33 — the daily retention-prune
  skill is not scheduled anywhere live) and `osint_monitor.py` has no
  `is_safe_public_url`; runner `main.py` has no `ssrf_guard` and no
  `openapi_url` (C-13: `/api/rss/custom` SSRF is still open live).
- **C-17 not live at the DB layer either**: none of the three v38
  expression indexes (`idx_faa_registry_mode_s_hex_lower`,
  `idx_opensky_registry_icao24_lower`,
  `idx_opensky_registry_registration_upper_nodash`) exist in
  `/var/lib/corporatetraveldc/corporatetraveldc.db` (69 indexes, zero
  matches, read-only query) — `init_db_v38()` only runs at web/poller
  startup on a post-rebuild image.

Still live until the next rebuild+restart: C-11, C-12, C-13, C-17,
C-19, C-20, C-21, C-26 (whoami-token rate limit), C-32, C-33. C-8 is
code-fixed but the old default-open `llm.py` is what's baked; this box
stays safe via `dispatch.env`'s explicit `false` + no API key, exactly
the two-safeguard posture the finding warned about. Not affected by the
gap (host-side, effective immediately from the working tree): C-24/C-27
(scrub-public-tree.py + DROP_FILES), C-34 + drift-check check 10, and
the re-synced hooks.

**C-28 nuance — half live, half not**: README's own token examples run
the CLI host-side (`PYTHONPATH=src python3 src/ctdc_token/cli.py`),
which reads the working tree — the 365-day default is **already
effective** for host-run creates. `docs/auth-token-proxy-pattern.md`'s
rotation runbook instead does `podman exec` into the web container,
whose baked copy predates the commit and still defaults to
never-expires. The two paths disagree until the next rebuild.

## The commit itself: signed clean, no chicken-and-egg breakage

Host `verify-manifest.sh` → rc 0, all 782 files match at HEAD.
`integrity-sweep` is not among the failed units. Checkpoint 5 is signed
— which is itself a doc-drift item (below).

## Doc drift found (5 items + CLAUDE.md)

1. **`docs/DESIGN-PRINCIPLES.md:15`** — "`ANTHROPIC_FALLBACK_ENABLED`
   (module default true when unset — `src/common/llm.py:383` …)".
   Superseded by C-8: the module default is now `"false"` (fail-closed).
   The paragraph's conclusion (zero cloud calls on this box) is
   unchanged — in fact now over-cautious: the path is closed even
   *without* `dispatch.env`. The doc's own "re-derive with grep" hedge
   covers the line number, not the stated default.
2. **`README.md:713-714`** — "the `true` default only applies when the
   var is unset, which it is not here". Same C-8 supersession: there is
   no `true` default any more. Claim about this box's behavior remains
   true; the parenthetical's mechanism is now wrong.
3. **`docs/auth-token-proxy-pattern.md:~317`** (rotation runbook) —
   "`--expires <DAYS>`. Optional but currently unused platform-wide …
   all 19 rows in auth_tokens have expires_at IS NULL". Two-part drift:
   (a) the dated 19-row observation is now 20 per CLAUDE.md's C-28
   verification, and new tokens will populate `expires_at`, so
   "currently unused platform-wide" stops being true on first
   post-rebuild rotation; (b) **operational footgun**: once the web
   image is rebuilt, following the runbook's `create` command verbatim
   mints a runner *service* token that silently expires in 365 days —
   a scheduled auth outage in August 2027. The runbook should either
   pass `--expires 0` explicitly (a permanent service token is arguably
   the legitimate case the flag was kept for) or document the expiry +
   a rotation reminder. Persisted to second-brain (see below) since
   this bites a year from now, far outside any session's memory.
4. **`docs/ALERT_REFERENCE.md:198-199`** — "matches a parsed FDPS
   event's callsign/gufi against active flight watchlist entries".
   Superseded by C-19: the GUFI arm was removed entirely (it never
   worked — no `gufi_override` column ever existed — and was the
   every-entry-false-match landmine). Matching is callsign-only now.
5. **`docs/dispatch-runner-design.md:129`** — "`/api/rss/custom` |
   Server-side fetch of arbitrary feed URL (CORS bypass)". "Arbitrary"
   is exactly what C-13 removed: post-fix it is public-host-only
   (ssrf_guard) with redirects disabled. Minor wording drift, but it's
   a security-relevant "arbitrary."
6. **CLAUDE.md self-staleness (recurring pattern, now 6th occurrence)**
   — the header says checkpoint 5 "pending sign+commit" and the
   checkpoint-5 section ends "Pending sign for this checkpoint," but
   `521ba5b` *is* that sign+commit (manifest verifies rc 0 at HEAD).
   Inherent chicken-and-egg: the signed manifest includes CLAUDE.md, so
   the file can never describe its own commit as done. Also carried
   forward unfixed from pass 7: checkpoint-4's "items 3-4 … NOT yet
   deployed live … pending operator confirmation" text survived this
   commit's own 190-line CLAUDE.md edit, still contradicted by the same
   file's known-bad section recording the 22:00–22:11 rebuild.

## Checked, no drift

- **`docs/GPS_COORDINATE_CONFIGURATION.md:57`** — its scrubber-backstop
  framing ("a backstop for values it already knows about — it cannot
  catch a new or rotated coordinate") is *strengthened* by C-24, which
  added the two known default literals to that list. Warning still
  valid, no edit needed.
- **`docs/COMPLIANCE_SECURITY.md` retention claims (`:29`, `:355-356`,
  `:394-398`)** — all scoped to `audit_log`, all still true;
  `retention_prune.py` is a sibling skill, not a change to the
  audit-log story. The new 9-table pruning is claimed nowhere yet —
  absence, not drift (candidate for the next real docs pass).
- **`docs/INFRA_MAP.md:301`** — "check-claude-md-drift.sh check 9"
  reference unaffected; C-34's new hook-drift check was appended as
  check 10, no renumbering.
- **`docs/INFRA_MAP.md:638`** — "one-time enrollment nonces": C-32
  makes the one-time property race-safe, so the claim is now
  unconditionally true rather than true-except-under-concurrency.
- **`src/ingest/README.md:135`** — "watchlist matching" is generic, no
  GUFI-mechanism claim. **`src/shared/watchlist_README.md`** — the
  `/history?limit=N` row makes no bounds claim; nothing else touched.
- **`SECURITY.md`**, README token examples (`:368`, `:545-547`) — no
  expiry claims; examples still work (host-side creates now get 365-day
  expiry — a behavior change but no documented claim invalidated).
- **`docs/COGS_VENDOR_COMPARISON_2026-08-18.md`, `ISO_42001_ALIGNMENT.md`,
  cost-projection docs** — their fallback-gate claims are stated as
  this-box's `dispatch.env=false` posture (still true) or are dated
  snapshots; only DESIGN-PRINCIPLES and README state the *module
  default* as evergreen fact.

## Failed units (left untouched)

Down to 2: `docs-drift-weekly` (unchanged all day) and
`trains-yachts-daily-watch` (rebuild-window kill, awaiting its next
scheduled fire). The other four rebuild-window kills from CLAUDE.md's
known-bad list have all self-cleared on their next fires (`ops-brief`
23:05, `gig-economy-daily-watch` 23:00, `concierge-travel-daily-watch`
23:15 all up and running at check time) — the known-bad entry's
self-clear prediction continues to hold. Nothing was staged, committed,
or signed by this check (read-only commands, read-only curl probes,
this file, and one second-brain note).

## Concurrent work observed mid-check (left untouched)

`CLAUDE.md` gained an uncommitted working-tree edit at 23:20:42 EDT —
mid-check, not made by this pass: another session rewrote the ntfy
FCM/Android paragraph from "wrong token shape, flagged back" to a
confirmed dead end (stock ntfy Android app hardcodes Firebase to the
official ntfy.sh server; GrapheneOS lacks Play Services FCM needs
anyway; real fix stays Instant Delivery + battery exemption). So `git
status` at this check's end shows ` M CLAUDE.md` (that session's edit)
plus `?? docs/LIVE_STATE_CHECK_2026-08-25.md` (this file) — this
check's own footprint remains this file and the second-brain note
(`01-Sources/manual/20260826T032252Z.md`, verified indexed and
searchable) only. That edit also means the manifest no longer matches
the working tree, so `integrity-sweep` will flag CLAUDE.md as unsigned
until the other session's next sign — expected, not a regression.

# Addendum — ninth pass, post-commit 7423835 (~00:15–00:45 EDT 2026-08-26)

Scope: commit `7423835` ("Fix C-22 (DEMO_MODE + scoped demo-secrets
convention), add weekly Second Brain consolidation dump infra"). Same
rule as all prior passes: only checked whether existing doc claims are
invalidated by this specific change, verified live where relevant.
Second-brain search first: pass 8's note
(`01-Sources/manual/20260826T032252Z.md`) covers the previous commit
`521ba5b`; no prior pass covers `7423835` — this pass builds on pass 8
rather than re-deriving its findings. Read-only except this file and one
second-brain note. Nothing staged, committed, or signed.

## Headline live finding 1: C-22 IS deployed and the gate really fires

Verified independently, not just trusting CLAUDE.md's own deploy notes:

- `corporatetraveldc-runner-demo`: `active (running)`, **NRestarts=0**,
  started 23:33:17 EDT — the crash loop running since 2026-08-15
  (NRestarts formerly ~50k) is gone. `demo-api` also up.
- Public vhost `dispatch-runner.example.com` → **200** (was
  502 since 2026-08-15).
- `podman exec … printenv DEMO_MODE` → `true` in runner-demo; unset in
  demo-api (matches CLAUDE.md — only the secrets file was wired there).
- `/etc/corporatetraveldc/demo-secrets.env` exists, `0600`,
  owner `corporatetraveldc` — exactly what
  `config/demo-secrets.env.example`'s header demands.
- **Gate behavior, probed directly**: the demo gate lives on the
  `/api/dispatch/{path}` proxy route (`src/runner/main.py:1714`).
  Untrusted simulation (allowlisted `Host:` + spoofed
  `CF-Connecting-IP: 203.0.113.9`, i.e. what a real Cloudflare-fronted
  stranger looks like post-C-2) → **401 `{"detail":"Demo login
  required"}`**. Trusted loopback with no CF header → passes the gate
  (upstream 404 for a bogus path, as expected). Note for future probes:
  bare paths like `/api/v1/aircraft` on :8005 return 200 **HTML** — that
  is the SPA catch-all (`main.py:2654`) serving the dashboard shell, not
  a gate bypass; the data plane is `/api/dispatch/*`.
- Nuance for any "live public demo" claim: the surface is up and gated,
  but the end-to-end verification profile was created and then deleted
  (per CLAUDE.md), so there is not necessarily a working public login
  today — "up and gated" ≠ "demo-able to an investor" until a real demo
  session/profile is provisioned.

## Headline live finding 2 (REAL drift): C-22's quadlet halves are live-only — the repo would redeploy the demo ungated

The commit message says C-22 is fixed, but `7423835` contains **no
quadlet changes**. Diffing tracked vs installed:

- `.config/containers/systemd/corporatetraveldc-runner-demo.container`
  (tracked, at HEAD) **lacks** `Environment=DEMO_MODE=true` and
  `EnvironmentFile=/etc/corporatetraveldc/demo-secrets.env` — both
  present only in the installed
  `~/.config/containers/systemd/` copy (with their C-22 comments).
- `…corporatetraveldc-demo-api.container` (tracked) **lacks** the
  `EnvironmentFile=/etc/corporatetraveldc/demo-secrets.env` line the
  installed copy has.

Consequence: re-provisioning from the repo (the tracked quadlets are
the source of record for exactly that) would silently bring the demo
back **ungated** — DEMO_MODE unset again — and reintroduce the
split-session-secret landmine C-22's deploy specifically fixed. This is
the same tracked-vs-installed drift class C-34 just closed for git
hooks, but `check-claude-md-drift.sh` check 10 covers the 3 git hooks
only, not quadlets — so nothing automated will catch this one. Fix is
mechanical (copy the two installed quadlets over the tracked copies in
the next signed commit); extending check 10's pattern to
repo-tracked-vs-installed quadlets would close the class. **Not edited
by this pass** (flag-only convention). Persisted to second-brain.

Checked the same class on the rest of this commit: the new
`corporatetraveldc-second-brain-weekly-dump.service`/`.timer` tracked
copies are byte-identical to the installed `~/.config/systemd/user/`
ones. No other drift of this class from this commit.

## Headline live finding 3: pass 8's deployment gap is now PUBLICLY reachable

The 23:33 runner-demo restart runs the **21:52 EDT image build**
(pre-`521ba5b`, confirmed via `podman inspect` image creation time), so
checkpoint 5's container-side fixes are still not live — unchanged from
pass 8. What changed is exposure: `/openapi.json` still returns 200 on
all four apps (:8000/:8001/:8004/:8005), and now **also 200 on the
public vhost to an untrusted (spoofed-CF) request** — the demo gate does
not cover it (only `/api/dispatch/*`). Until tonight the 502 crash loop
was accidentally masking the public half of C-12; the C-22 deploy
un-masked it. Same logic applies to the rest of pass 8's still-live
list on the demo pair. Not a new bug — the same known gap, elevated
from "would be exposed if the demo were up" to "exposed now." Next
runner/demo image rebuild+restart closes it.

## Doc drift found: the demo-status wall (invalidated by the deploy this commit records)

Every evergreen doc stating the pre-tonight demo state is now stale in
the same direction — demo was down/502/ungated/`DEMO_MODE` unset, all
four now false. These were accurate until ~23:33 EDT tonight:

1. **`README.md:70`** — status row: "DOWN — crash-looping since
   2026-08-15, and the password gate was never actually enabled …
   `DEMO_MODE` is set **nowhere**". Also `README.md:121` (quadlet "does
   not set it"), `:249` ("Currently 502 … password gate not enabled"),
   `:461-468` (same claims in prose).
2. **`docs/COMPLIANCE_SECURITY.md:30`** ("gate is **inert** because
   `DEMO_MODE` is unset") and the whole §2 block `:81-152` — "three
   sources disagree", "`DEMO_MODE` **is set nowhere**", "**NEEDS
   OPERATOR DECISION**". The operator decision it demands **has now
   been made and executed** (operator-confirmed deploy, this commit);
   the section's open question is resolved.
3. **`docs/INFRA_MAP.md:350`** (crash-loop narrative, NRestarts ≈49.6k,
   ":8005 refused", "password gate is **inert**") and `:558` (public
   hostname row: "currently 502 … gate inert … decision still
   pending").
4. **`docs/REFERENCE_INFRA.md:77`** — "currently unset, so that
   instance's demo protections are inert (it is also crash-looping …
   502)". The row's *advice* (treat public demo as opt-in you must
   verify) survives; the parenthetical state is stale.
5. **`docs/executive_summary.md:24-27`** — "`DEMO_MODE` is set in no
   environment file".
6. **`docs/auth-token-proxy-pattern.md:166`** — "(currently
   crash-looping) and `DEMO_MODE` is unset".

None of these were edited this pass (flag-only; §2 of
COMPLIANCE_SECURITY in particular is a narrative section whose rewrite
belongs in a deliberate docs pass, not a drift-check side edit). The
dated investor research docs under `docs/investor-materials/v1.5/research/`
also carry the old state but are point-in-time snapshots per standing
convention — one forward-looking note: `REVERIFICATION_2026-08-24.md`'s
"Do **not** claim a live public demo in v1.5" gate is now obsolete in
the *favorable* direction, subject to the login-provisioning nuance in
finding 1.

## Checked, no drift

- **`.gitignore` change**: `git check-ignore` confirms
  `config/demo-secrets.env.example` is NOT ignored (stays tracked) while
  `config/demo-secrets.env` and a hypothetical `ea-demo-secrets.env`
  ARE (`.gitignore:92`) — the pattern does what its comment claims, no
  tracked-file collateral.
- **Weekly-dump infra vs CLAUDE.md's claims**: timer installed and
  enabled, next fire `Sun 2026-08-30 02:00:00 EDT` — matches CLAUDE.md
  verbatim. `scripts/second-brain-weekly-dump.sh` exists and is
  executable. CLAUDE.md's "Not yet run for real" remains true.
- **`docs/INFRA_MAP.md` timer section**: explicitly defers to
  `systemctl --user list-timers` for the full list, so the new timer's
  absence from the doc is absence-not-drift (candidate for the next
  real docs pass, same bucket as pass 8's retention_prune note).
- **`docs/ALERT_REFERENCE.md:753`** — "in `DEMO_MODE`, synthesises fake
  events": mechanism claim, now *more* relevant since DEMO_MODE is
  actually on; still accurate.
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — no
  demo/quadlet/secrets/timer claims; untouched by this commit's scope.
- **Pass 8's five evergreen drift items** (DESIGN-PRINCIPLES.md:15,
  README.md:713-714, ALERT_REFERENCE.md:198-199,
  dispatch-runner-design.md:129, auth-token-proxy-pattern.md runbook
  footgun) — still open, unchanged by this commit; not re-derived here,
  see pass 8 / second-brain note `20260826T032252Z`.

## The commit itself: signed clean; integrity-sweep's failure predates the sign

`scripts/verify-manifest.sh` → **rc 0, all 786 files match** the
current working tree (including the concurrent staged work below).
`integrity-sweep` shows failed, but its failure fired at **23:49:11
EDT** — during the pre-commit window (2 checksum mismatches), before
both the 23:52 commit and the subsequent re-sign — and will self-clear
on the next 15-minute sweep. CLAUDE.md self-staleness, **7th
occurrence**: the committed CLAUDE.md still reads checkpoint 5 "pending
sign+commit" / "Pending sign for this checkpoint" although `521ba5b` +
its sign already happened — the known chicken-and-egg (the manifest
includes CLAUDE.md), noted, not edited.

## Failed units (left untouched)

2: `docs-drift-weekly` (unchanged all day, still uninvestigated per
CLAUDE.md's own instruction to check its journal before assuming cause)
and `integrity-sweep` (explained above, self-clears).
`trains-yachts-daily-watch` self-cleared on its next fire exactly as
pass 8 predicted.

## Concurrent work observed mid-check (left untouched)

At check start the index carried **staged** changes from another
session: `scripts/scrub-public-tree.py` (+2 `ALLOWED_IPV4` allowlist
entries, `2.49.0.1` and `10.x.x.x`, so *this file's own* pass-3 prose
quoting those old fixture values passes the scrubber) and a re-signed
`MANIFEST.sha256`/`.asc` covering it. Mid-check that session committed
it as `22a66a6` (23:59:59 EDT) — HEAD moved under this pass, which is
why the verify-manifest rc 0 above reads 786 files. Nothing in that
commit touches this pass's findings (it is doc-prose allowlisting, not
code). This pass's footprint: this addendum (now the only working-tree
modification) and one second-brain note
(`01-Sources/manual/20260826T040047Z.md`, verified indexed and
searchable).
