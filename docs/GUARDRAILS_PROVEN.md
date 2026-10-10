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
