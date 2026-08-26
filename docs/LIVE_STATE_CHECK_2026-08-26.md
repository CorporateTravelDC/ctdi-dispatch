# Live-state / doc-drift check — 2026-08-26 (~00:00–00:15 EDT), post-commit 22a66a6

Scope: commit `22a66a6` ("Allowlist 2 more illustrative test values quoted
in LIVE_STATE_CHECK doc prose" — `scripts/scrub-public-tree.py` +2
`ALLOWED_IPV4` entries, manifest re-sign). Tenth pass in this series;
second-brain search ran first, per convention. Prior lineage: pass 8
(`521ba5b`, vault note `01-Sources/manual/20260826T032252Z.md`) and pass 9
(`7423835`, vault note `20260826T040047Z.md`, whose report addendum is the
still-uncommitted edit to `docs/LIVE_STATE_CHECK_2026-08-25.md`). No prior
vault note covers `22a66a6`; this pass builds on 8/9 rather than
re-deriving them. Read-only except this file and one second-brain note.
Nothing staged, committed, or signed by this pass.

**Important context for reading this file:** another session was actively
editing the working tree while this check ran (details in finding 2 —
that's a feature of tonight, not an error in either pass). Every claim
below carries its observation time; re-derive anything time-sensitive
rather than trusting this snapshot.

## The commit itself: verified, achieves its stated purpose, invalidates no doc

- Ran the scrubber end-to-end against HEAD live (`scrub-public-tree.py
  HEAD`, 13.6s): `verify_scrubbed()` passes clean, scrubbed tree
  `2d741d74`. So the two new allowlist entries (`2.49.0.1`, `10.0.0.9`)
  do exactly what the commit message claims for the committed tree.
- The allowlist comment's claim that both values were "since renamed in
  the actual test files" is true — a repo-wide grep finds them only in
  the scrubber itself, the 08-25 LIVE_STATE_CHECK prose, and CLAUDE.md's
  checkpoint-3 notes (the latter two both ship in the public tree, both
  now covered by the same entries).
- No doc anywhere documents `ALLOWED_IPV4`'s contents, so an additive
  allowlist change invalidates nothing. README.md, docs/*,
  `src/ingest/README.md`, `src/shared/watchlist_README.md`: no claims
  touched by this commit's scope. Pass 9's demo-status drift wall is
  unchanged and not re-derived here.

## Finding 1 (REAL, forward-looking, empirically confirmed): the next commit of pass 9's addendum pre-stages a public-push block

The uncommitted pass-9 addendum quotes its own spoofed-CF probe value
`203.0.113[.]9` (defanged here deliberately — writing the contiguous
dotted-quad in this file would add a second instance of the exact
problem). That value is NOT in `ALLOWED_IPV4`; only `203.0.113.50` is,
and `verify_scrubbed()`'s IPv4 check is exact-match (scrub-public-tree.py
line ~757), no TEST-NET range exemption. Git-tree-based scrubbing can't
see uncommitted files, which is why tonight's HEAD run passes — so this
lands the moment the addendum is committed.

Confirmed empirically, not just by code-reading: built a temporary-index
tree (HEAD + the addendum, real index/refs untouched, dangling objects
only) and ran the scrubber against it → `VERIFICATION FAILED`, exactly
one violation, that value in `docs/LIVE_STATE_CHECK_2026-08-25.md`. A
safe failure (push blocked, nothing leaks — the value is RFC 5737
TEST-NET-3, documentation-only, not sensitive), but it would stall the
next `push-public.sh` run, plausibly mid-investor-docs push.

Fix is one line: allowlist it (same rationale as the existing
`203.0.113.50` entry) in whichever signed commit picks up the addendum.
This is the third occurrence of the "doc prose quoting probe/fixture
values trips the scrubber later" class (checkpoint-3 fixtures →
`22a66a6`'s two values → this). Standing suggestion for future passes:
quote already-allowlisted illustrative values (e.g. `203.0.113.50`) or
defang, rather than minting new allowlist entries per pass. Flag-only,
not edited by this pass; persisted to second-brain.

## Finding 2 (REAL regression, observed being fixed concurrently — snapshot only): checkpoint 1's C-0c fix self-blocked push-public.sh

Observed mid-check, timestamped: at ~00:03 EDT `scripts/push-public.sh`
gained an uncommitted edit; by ~00:10 it and `scripts/pre-push` were
**staged** (another session's work, left strictly untouched).

What the staged diff shows: the C-0c fix (`ef6b798`, checkpoint 1) made
`pre-push` block pushes whose URL argument matches the public remote's
resolved URL — but `push-public.sh`'s own sanctioned push is exactly that
shape by design (it force-pushes the already-scrubbed commit via the raw
resolved URL, never the named remote). So since checkpoint 1, the ONE
legitimate public-push path was self-blocking. The concurrent fix adds a
`CTDI_PUSH_PUBLIC_INTERNAL=1` escape hatch set only at that call site;
the staged `pre-push` gates only the public-destination intercept on it,
not the credential-pattern scanner — the hatch is properly narrow.

Doc-drift angle (the part that belongs to this pass): CLAUDE.md's
checkpoint-1 C-0c entry claims "Verified by direct hook invocation:
raw-URL push now blocked, normal `origin` push unaffected." Both halves
were true, but the verification missed the third case — the sanctioned
scrubbed push is itself a raw-URL push. Worth remembering as a test-gap
pattern: when adding a bypass-blocker, verify the legitimate path that
shares the bypass's shape. Persisted to second-brain (attributed as
observed-in-flight, so a future pass doesn't double-fix or contradict
the concurrent session's own writeup).

## Finding 3 (REAL, closes a standing item): docs-drift-weekly root-caused — 0-for-2 on scheduled fires

CLAUDE.md's known-bad list has carried `corporatetraveldc-docs-drift-weekly`
as "failed, not re-investigated" since 08-25; passes 8/9 deferred it.
Checked its journal and its own logs (`/var/lib/corporatetraveldc/docs-drift-check/`):

- **2026-08-17 09:00 (scheduled): failed** — `nohup: failed to run
  command 'claude': No such file or directory` (PATH gap in the unit;
  per the 08-19 log this was subsequently fixed).
- **2026-08-19 09:49 (manual start): succeeded** — full drift report
  produced; confirms the PATH fix works.
- **2026-08-24 09:00 (scheduled): failed** — log contains exactly
  "You've hit your session limit · resets 10:40am (America/New_York)".
  The headless `claude` run fired while the operator's Claude usage
  window was exhausted; exit 1, no retry.

So the timer-triggered run has never once succeeded; the only clean run
was manual. The 08-24 failure mode is environmental and will recur
whenever a Sunday-09:00 fire coincides with an exhausted usage window —
and it applies equally to the brand-new
`corporatetraveldc-second-brain-weekly-dump.timer` (Sun 02:00, same
headless-claude pattern, first fire 2026-08-30). Options for the
operator (not implemented here): retry-with-delay in the scripts (e.g.
re-fire after the stated reset time), or `OnFailure=` notification so a
silent Sunday failure pings ops instead of sitting in `--failed` for two
days. Persisted to second-brain.

## Checked, not drift (explanations worth keeping)

- **`verify-manifest.sh` passing at 00:02 despite the uncommitted pass-9
  addendum is by design**, not an anomaly: `sign-manifest.sh` deliberately
  excludes `docs/LIVE_STATE_CHECK_*.md` from the manifest (2026-08-18
  decision, documented in the script ~line 110) precisely so drift-pass
  addenda don't break integrity between signs. (Distinct from CLAUDE.md,
  which IS in the manifest — that's the known chicken-and-egg.)
- **`integrity-sweep` failed at 00:04:11** — NOT the pre-sign-window
  failure pass 9 described (that was 23:49:11, and the 22a66a6 sign did
  clear it: my 00:02 verify was rc 0, all 786 files matched). The 00:04
  failure's cause is finding 2's concurrent `push-public.sh` edit
  (mtime 00:03), caught by the very next sweep fire — the sweep working
  exactly as intended on not-yet-signed work. Self-clears after the
  concurrent session's next sign.
- **Failed units, full snapshot at 00:00**: only `integrity-sweep`
  (above) and `docs-drift-weekly` (finding 3). Every
  LOCKDOWN/rebuild-window casualty from CLAUDE.md's known-bad list
  (pusher, poller, board-sweep, ops-brief, etc.) has self-cleared as
  predicted.

## Footprint

This file and one second-brain note. Nothing staged, committed, signed,
or deployed; the concurrent session's staged `pre-push`/`push-public.sh`
work and modified manifest were left untouched; temp-index scrub test
used a throwaway `GIT_INDEX_FILE` and produced dangling objects only.
