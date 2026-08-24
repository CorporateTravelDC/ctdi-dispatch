# scripts/pre-commit

Pre-commit hook that scans staged diffs for known credential patterns and
rejects the commit if any are found.

## Install

    cp scripts/pre-commit .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit

**Already installed on this box** — `.git/hooks/pre-commit` is byte-identical
to `scripts/pre-commit` (verified 2026-08-23 with `diff`). `scripts/` is the
source of truth; re-copy after editing it, since git hooks are not symlinks.

⚠️ **`post-commit` is the one that drifted — verified 2026-08-23.** Of the
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
not write them anywhere searchable. This is exactly the failure mode the
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
| `pre-push` | `scripts/pre-push` | **blocks any direct `git push public`** (use `scripts/push-public.sh`) plus its own credential scan. Since 2026-08-13 it deliberately does *not* auto-sync the public mirror — publishing is an explicit separate step (`scripts/push-and-sync.sh`). |
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
| `VARNAME=<28+ char value>` | raw credential in env assignment (separate check, not in the `PATTERNS` array) |

⚠️ The script's own header comment also lists `[0-9a-f]{64}` ("raw 64-char
hex"), but that pattern is **not** in the `PATTERNS` array and is not
actually checked — the comment overstates coverage. The table above matches
what the code really does.

## False positives

If a match is a genuine false positive (e.g. a test fixture with a
placeholder value), either:

1. Use a placeholder the hook skips. The literal skip strings are
   `CHANGE_ME`, `YOUR_` (so `YOUR_TOKEN_HERE` works), `example`, and
   `placeholder`; the env-assignment check additionally skips lines
   containing `localhost`, `127.0.0`, or `http`.
2. Use `git commit --no-verify` to bypass (use sparingly, document why)

## Secrets workflow

Values never go in source files. The correct path:

    echo "sk_adjs_..." > ~/.secrets/acarsdrama.token
    chmod 600 ~/.secrets/acarsdrama.token
    bash scripts/populate-secrets.sh
    systemctl --user restart corporatetraveldc-runner.service
