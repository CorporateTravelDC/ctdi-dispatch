# scripts/pre-commit

Pre-commit hook that scans staged diffs for known credential patterns and
rejects the commit if any are found. (Added 2026-10-25: it also runs
`scripts/check-env-quoting.sh` and `scripts/check-timer-requires.sh` and rejects
the commit if either fails, `scripts/pre-commit:149-161`.)

## Install

    cp scripts/pre-commit .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit

**Already installed on this box** — `.git/hooks/pre-commit` is byte-identical
to `scripts/pre-commit` (verified 2026-08-23 with `diff`). `scripts/` is the
source of truth; re-copy after editing it, since git hooks are not symlinks.

⚠️ ~~**`post-commit` is the one that drifted — verified 2026-08-23.** Of the
three hooks, `pre-commit` and `pre-push` both `diff` clean against their
repo sources, but `.git/hooks/post-commit` (mtime 2026-08-11) is **stale
against `scripts/post-commit-doc-verify.sh` (mtime 2026-08-18)**. The
installed copy is missing the block the repo version added: the
`scripts/second-brain-search.sh '<topic terms>'` prior-findings lookup that
runs *before* the drift check, and the instruction to persist a real,
non-trivial finding to the second brain via `remember_text()` rather than
relying on the `docs/LIVE_STATE_CHECK_*.md` file alone (that file is
read-in-repo, not indexed for search). Net effect: the doc-verify pass that
actually fires after a commit today still re-derives findings cold and does
not write them anywhere searchable.~~ (corrected 2026-10-25, superseded: true on
2026-08-23. All three installed hooks are now byte-identical to their sources
(`cmp` 2026-10-26 04:46Z; the installed `post-commit` is dated 2026-10-15 03:52Z).
The hook now runs the `scripts/second-brain-search.sh` prior-findings lookup
(`scripts/post-commit-doc-verify.sh:106`). Neither copy persists anything with
`remember_text()`: the report is appended to
`/var/lib/corporatetraveldc/reports/docs/LIVE_STATE_CHECK_<date>.md`, outside the
repo and outside the vault, so findings are still not searchable in the second
brain. The rest of this paragraph and the fix below are history.) This is exactly the failure mode the
"re-copy after editing" line above warns about, caught in the act. Fix with:

    cp scripts/post-commit-doc-verify.sh .git/hooks/post-commit
    chmod +x .git/hooks/post-commit

(Not done by this pass — `.git/hooks/` is outside the tracked tree and
installing a hook changes live behavior on the next commit.)

## The other two hooks

`scripts/pre-commit` is one of three installed hooks. Don't assume it's the
only guard in the path:

| Hook | Repo source | What it does |
|---|---|---|
| `pre-commit` | `scripts/pre-commit` | this file — staged-diff credential scan |
| `pre-push` | `scripts/pre-push` | **blocks any direct `git push public`** (use `scripts/push-public.sh`) ~~plus its own credential scan~~ (corrected 2026-10-25: plus its own credential scan, a blocking `scripts/dependency-audit.py --repo` gate (override `DEPENDENCY_AUDIT_OVERRIDE=1`, which is logged) and a blocking `transcript_leak_scan.py scan --require-ladd` gate, `scripts/pre-push:235-265`). Since 2026-08-13 it deliberately does *not* auto-sync the public mirror — publishing is an explicit separate step (`scripts/push-and-sync.sh`). |
| `post-commit` | `scripts/post-commit-doc-verify.sh` | fires a backgrounded live-system doc-drift check after a major commit/deploy |

## Patterns checked

| Pattern | Credential type |
|---|---|
| `sk_adjs_` | acarsdrama Jumpseat token |
| `sk-ant-api` | Anthropic API key |
| `github_pat_` | GitHub fine-grained PAT |
| `ghp_` | GitHub classic PAT |
| `ghs_` | GitHub app token |
| `Bearer <20+ chars>` | bare Bearer token in source |
| `-----BEGIN ... PRIVATE KEY-----` | PEM private key header (row added 2026-10-25, `scripts/pre-commit:53`) |
| `tskey-<20+ chars>` | Tailscale auth key (row added 2026-10-25, `scripts/pre-commit:54`) |
| `VARNAME=<28+ char value>` | raw credential in env assignment (separate check, not in the `PATTERNS` array) |

⚠️ The script's own header comment also lists `[0-9a-f]{64}` ("raw 64-char
hex"), but that pattern is **not** in the `PATTERNS` array and is not
actually checked — the comment overstates coverage. ~~The table above matches
what the code really does.~~ (corrected 2026-10-25: it missed the PEM
private-key and `tskey-` patterns; with the two rows added above it matches
`PATTERNS`, `scripts/pre-commit:44-55`.)

## False positives

If a match is a genuine false positive (e.g. a test fixture with a
placeholder value), either:

1. Use a placeholder the hook skips. The skip patterns are the literal
   strings `CHANGE_ME`, `YOUR_` (so `YOUR_TOKEN_HERE` works), `example`,
   `placeholder`, and `REDACTED`; anything matching `<...>` angle-bracket
   template form; a literal `...` ~~anywhere on the line~~ (corrected 2026-10-25:
   since afddf419 (2026-08-27 00:00Z) every skip string counts only inside the
   ~20-character window around the match, `scripts/pre-commit:75-77`, not
   anywhere on the line) (a
   truncated-for-display value — no real token contains three literal
   dots); and `deadbeef` in any case (placeholder hex) — the last two
   added 2026-08-24 after an audit report's forged-token evidence table
   false-positived. ~~The env-assignment check additionally skips lines
   containing `localhost`, `127.0.0`, or `http`.~~ (corrected 2026-10-25: the
   env-assignment check has its own list, matched against the assignment
   itself: `CHANGE_ME`, `YOUR_`, `example`, `localhost`, `127.0.0`, `http`,
   `REDACTED`, plus values that start with `/` and names containing
   `FINGERPRINT`; it does not skip `placeholder`, `<...>`, `...` or
   `deadbeef`, `scripts/pre-commit:118-122`.) Same list in both
   `scripts/pre-commit` and `scripts/pre-push`.
2. Use `git commit --no-verify` to bypass (use sparingly, document why)

## Secrets workflow

Values never go in source files. ~~The correct path:~~ (corrected 2026-10-25: the
block below no longer works as written; see the note after it.)

    echo "sk_adjs_..." > ~/.secrets/acarsdrama.token
    chmod 600 ~/.secrets/acarsdrama.token
    bash scripts/populate-secrets.sh
    systemctl --user restart corporatetraveldc-runner.service

(corrected 2026-10-25: `scripts/lib/populate_secrets.py:49-52` maps
`~/.secrets/acarsdrama.token` to `ACARSDRAMA_FEEDER_KEY`. The Jumpseat token
(`sk_adjs_…`) belongs in `~/.secrets/jumpseat.token`, which sets
`ACARSDRAMA_JUMPSEAT_TOKEN` and `JUMPSEAT_API_KEY`. `populate-secrets.sh` writes
only `/etc/corporatetraveldc/dispatch-secrets.env`, and no live quadlet reads that
file. The runner reads `/etc/corporatetraveldc/svc/runner.env`, which only root
rewrites (`scripts/service-env/generate.py --write runner`), so the new value
reaches the runner only after that step and then the restart.)
