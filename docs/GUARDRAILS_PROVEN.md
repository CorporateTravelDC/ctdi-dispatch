# Guardrails, proven on the day: transcript leak guard (2026-10-09)

Companion to `TEAM_SEGMENTATION_SHOWCASE.md`. That page shows the account model holding up. This page shows the leak guard working against a real leak in a real session, not a demo.

> Images are in `docs/images/guardrails/` and stay in the **private** repo. `scripts/scrub-public-tree.py` drops unreviewed binaries until the operator adds a file to `REVIEWED_BINARY_OK`. Each image was sanitized before copying: cropped to the terminal, with the agent session's ID blacked out (red-outlined box). What remains visible are secret **names** and hit counts, which is all the scanner ever prints. No values are shown.

## The rule being enforced

Operator rule, 2026-10-09: agent transcripts are files on this box, and anything that can read the box can read them. A secret that leaks into a transcript is **acknowledged and provably redacted on the box before anything leaves it**: before a private push, a public push, or content a cloud agent reads. Pushes go private first, then public.

Enforcement points:
- `scripts/leak-guard/transcript_leak_scan.py`:
  - `scan` blocks on a closed file and only warns on a live one;
  - `redact` replaces each hit atomically and prints a proof line, `PROOF: <n> file(s) redacted; rescan hits = 0`.
- `scripts/pre-push` refuses any push while a closed transcript carries a leak.
- `scripts/push-public.sh` runs the same check (exit 6). It also runs the LADD tripwire on the final public tree (exit 7).

## What it looked like on the day

![redaction proof](images/guardrails/01-redaction-proof.png)

**1. Redaction.** The first full run against every transcript and tool output on the box redacted 41 closed files and the rescan found 0 hits. It skipped one file: the running session's own transcript. A file that's still being written can't be rewritten safely, so the scanner left it, named it, and listed what it carries.

![push blocked](images/guardrails/02-push-blocked-on-transcript-leak.png)

**2. The block.** About two hours later the session sat idle for more than 15 minutes during a long relay. The guard then classed its transcript as closed. The next private push ran the dependency audit (clean), then the transcript check, which refused the push. The failure names the fix.

![clean push](images/guardrails/03-clean-push-private-then-public.png)

**3. The clear.** The operator redacted the idle transcript (`redact --include-live`, run while the agent was idle), the rescan came back clean, and the same relay pushed private (`75d928b0`) and then public (`765c6d9a → 4dedaebc`). The public tree passed the LADD tripwire and verifies itself (`scripts/verify-manifest.sh`).

## Ledger

| Item | Value |
|---|---|
| Guard commit (leak guard, backups, import ledger, LADD tripwire) | `b763878c` (signed) |
| Allowlist + stdin fix commit | `75d928b0` (signed) |
| Public mirror after the clean push | `4dedaebc` |
| Reference-import ledger, seq 1 (10-06 LADD stamp-only, +0/−0) | `134d8baeca41e9aa122751c25f71d14e98c3e692e71b43cd7cf1b51c3d32f238` |
| `01-redaction-proof.png` (sanitized / original) | `17413d72…bceedc` / `11f1d433…f268c968` |
| `02-push-blocked-on-transcript-leak.png` (sanitized / original) | `c67d8d77…35892d` / `d603d93f…f0d09c73` |
| `03-clean-push-private-then-public.png` (sanitized / original) | `7fc4611b…e5d0c` / `30b9f9b1…2b31a980` |

The full hashes are recorded as a `note` entry in the private backup ledger (`scripts/backup/backup_ledger.py`, hash-chained). A copy of that ledger rides inside every encrypted backup. The originals stay on the box and outside the repo.

## The gate chain: every check between an edit and the public mirror

Each stage is a separate script that can stop the pass on its own. The scrollback below is real: either from the operator's terminal during the 2026-10-09 relays, or a fresh read-only run on the same day. Synthetic values only.

| Stage | Gate | Blocks on | Source |
|---|---|---|---|
| `git commit` | pre-commit hook | credential shapes in added lines (GitHub/Anthropic/vendor tokens, bare `Bearer`, 64-hex, long env values); quoted values in env files; self-referential systemd timer dependencies | `scripts/pre-commit` |
| `git commit` | signed commits | any unsigned commit (`commit.gpgsign=true`) | git config |
| `git commit` | post-commit | nothing (background documentation drift check after a major commit) | `scripts/post-commit-doc-verify.sh` |
| sign | CLAUDE.md drift gate | CLAUDE.md, hooks, units or skills disagreeing with live state | `scripts/sign-manifest.sh` → `check-claude-md-drift.sh` |
| sign | signed manifest | nothing to sign until the gate passes; then every tracked file is hashed and GPG-signed | `scripts/sign-manifest.sh` |
| `git push` | public-remote block | a direct `git push public` (publishing only goes through push-public) | `scripts/pre-push` |
| `git push` | credential scan | the same patterns over every commit being pushed | `scripts/pre-push` |
| `git push` | dependency audit | npm high/critical, or a fixable Python advisory, in any of 9 targets | `scripts/pre-push` → `dependency-audit.py` |
| `git push` | transcript leak guard | a live secret value or token shape in any closed agent transcript or tool output on the box | `scripts/pre-push` → `transcript_leak_scan.py` |
| publish | transcript leak guard | the same check, before anything is built (exit 6) | `scripts/push-public.sh` |
| publish | scrub | private dirs dropped, sensitive values substituted, unreviewed binaries dropped | `scripts/scrub-public-tree.py` |
| publish | leak gate | any secret value left in the final tree (injected manifests included) | `scrub-public-tree.py --verify-only` |
| publish | LADD tripwire | any LADD (CUI) identifier in the final tree, reporting locations only (exit 7) | `scripts/leak-guard/ladd_public_check.py` |
| publish | self-verification | the public tree failing its own `verify-manifest.sh` in a fresh extraction | `scripts/push-public.sh` |
| deploy | provenance binding | an image whose tag has no build receipt for the signed commit | `serialized-rollout.sh` → `provenance.py check-deploy` |
| deploy | clean-tree rule | building from a dirty or unsigned tree | `scripts/serialized-rollout.sh` |

### pre-commit: a credential refused (synthetic token, throwaway repo)

```text
[FAIL] pre-commit: credential pattern detected -- commit rejected
       Pattern: ghp_[A-Za-z0-9]   (GitHub classic PAT)
       Matches:
         +API_TOKEN="ghp_<synthetic demo value, 37 chars>"

       If this is a false positive, either:
         1. Move the value to ~/.secrets/ and reference via env var name
         2. Use a placeholder value (CHANGE_ME / YOUR_TOKEN_HERE)
```

### sign: the drift gate refusing to sign (operator terminal, relay 1)

```text
[FAIL] drift found -- reconcile CLAUDE.md above, then re-run
XX CLAUDE.md drift found (above) -- signing pass BLOCKED.
   Reconcile CLAUDE.md against live state, or re-run with
   SKIP_DRIFT_CHECK=1 to sign anyway (not recommended).
```

The cause was real, but narrow. The pass was being signed from a git worktree, which has no `.git/hooks` directory of its own, so the checker reported all three hooks as not installed. The fix was to move the change into the main checkout and sign there, not to bypass the gate. In the main checkout the same check passes:

```text
[OK] skills: 9 tracked, 0 divergent, 0 missing-live, 0 untracked-live (vendor dirs ignored via skills-vendor-ignore.txt)
[OK] CLAUDE.md matches live state
```

### git push: dependency audit clean, transcript guard refusing (operator terminal)

```text
dependency-audit: 9 target(s) -- clean
  ok ctdi-dispatch-internal:requirements.txt  python advisories=0 (fixable 0)
  ok ctdi-dispatch-internal:src/runner/frontend/package-lock.json  npm high+critical=0 moderate=0 low=0
  ...
transcript-leak-scan: LEAK in 1 file(s) (names and counts only):
  /home/corporatetraveldc/.claude/projects/<project>/<session-id>.jsonl: <secret names and counts>
[FAIL] pre-push: transcript leak on this box -- run
       scripts/leak-guard/transcript_leak_scan.py redact   then push again
error: failed to push some refs to '<private repo>'
```

After the redaction, the same scan:

```text
transcript-leak-scan: clean (51 secret value(s) + 4 token shapes checked)
```

### publish: LADD tripwire, before and after review (dry-run and real)

```text
ladd-public-check: LADD identifier(s) on 62 line(s) in 30 file(s) (locations only, never values):
[push-public] (dry-run) LADD tripwire tripped -- a REAL push would stop here (exit 7).
```

The operator reviewed the matches in their own terminal. All 20 distinct tokens were coincidences (old or demo scripts, ordinary tokens), and their hashes, never the tokens, went into `ladd-public-allow.sha256`. Then:

```text
ladd-public-check: clean (73539 identifiers checked)
[push-public] ✓ public/main: a9ab1753 → 066ebb33
[push-public]   public tree is self-verifying: clone it and run scripts/verify-manifest.sh
```

### deploy: every running image bound to the signed commit

```text
failed units: 0
     18 VERIFIED
```

That is 9 local images, each checked running and as tagged: image id, SBOM digest, commit signature, signed manifest, build inputs, base-image digests, current source.
