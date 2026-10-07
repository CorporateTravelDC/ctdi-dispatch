# Security review 04 — dependency scanning on every repository (2026-10-07)

> **Review record. Frozen once committed.** Part of the security review chain (`README.md` in this directory). It closes recommendation 2 of `2026-10-07-03-post-deploy-and-dependencies.md`.

## Decision (operator)

Use **both** layers: a local audit on every repository, run **before every push** and **daily**; and GitHub Dependabot alerts on every repository as a second, independent layer.

## What was put in place

| Layer | Scope | Behaviour | Where |
|---|---|---|---|
| Pre-push audit | the platform repo and both website repos | `npm audit` on every lockfile and `pip-audit` on every `requirements*.txt` in the repo being pushed. **Blocks** on npm high/critical or a Python advisory with a fix. An audit that cannot run (offline, tool missing) warns and lets the push through. Operator override: `DEPENDENCY_AUDIT_OVERRIDE=1`, journaled | `scripts/dependency-audit.py --repo`, `scripts/pre-push`, `scripts/git-hooks/site-pre-push`, `scripts/install-git-hooks.sh` |
| Daily audit | every repo in `scripts/lib/dependency-audit-repos.txt` **and each repo's git worktrees**, **plus what is installed in every running first-party image and every dormant local image tag** (rollback `:previous`, test and debug tags, images of disabled units; `pip freeze` offline → `pip-audit --no-deps`) | ntfy p4 on vulnerable packages, p3 if it could not run; state in `~/.cache/ctdc-dependency-audit/last-run.json` | `corporatetraveldc-dependency-audit.{service,timer}`, 09:20 UTC |
| Dependabot alerts | all 12 active GitHub repositories (public and private) | GitHub-side alerting. Automatic fix PRs are deliberately **off**: the public mirrors are regenerated on every push, and private commits must be signed through the manifest flow | GitHub repository settings (enabled 2026-10-07; each returned HTTP 204) |

`pip-audit` runs from a private venv (`~/.local/share/ctdc-dependency-audit/venv`), never the system Python. The audit only reports; it never edits a repository or an image.

## Validation

- First full run: **24 targets clean**. That covers 4 repository manifests, the runner frontend lockfile, and 19 running images, including the three fixed in review 03.
- **Negative check:** a throwaway repository pinned to `python-multipart==0.0.9` was **blocked** (7 fixable advisories, exit 1).
- **Offline tests** (`tests/scripts/test_dependency_audit.py`):
  - manifest discovery skips vendored trees;
  - the blocking policy (npm high/critical blocks, moderate/low is reported, a fixable Python advisory blocks, an unfixed one is reported, an audit that cannot run returns 3);
  - every private repository is in the daily list;
  - both hooks call the audit.
- GitHub reports **0 open Dependabot alerts** on the public platform mirror after review 03's fixes were pushed.

## Dormant code is audited too (same pass)

**Operator rule (2026-10-07, standing):** disabled, dormant or not deployed is **not** a valid reason to leave a known-vulnerable dependency in place, in any container or any other code. Re-enabling a unit or rolling back must never bring a known vulnerability back.

The first run covered only running images and the main checkouts. A sweep of everything else found:

| Where | State | Finding | Disposition |
|---|---|---|---|
| `ais-watcher`, `utm-watcher` Containerfiles | never deployed | `requests==2.32.3` (urllib3 floating): 5 advisories | pinned `requests==2.34.2 urllib3==2.8.0`; trial-built and re-audited clean; a test bans the old pin in any watcher `RUN` line |
| `acars-watcher:previous`, `csexec-contact:previous` | **rollback targets** | the pre-fix images (5 and 14 advisories) | a rollback would have restored them. `:previous` re-pointed to the fixed images, which have run clean since review 03 |
| seven `:integrity-test` tags, `ingest:debug`, `demo-api:latest` | orphaned, 8–9 weeks old, used by no unit (`demo-api` runs on the `demo` image) | `urllib3` 2.7.0 (3 advisories); `csexec-contact:integrity-test` also has the 14 contact-API advisories | removed |
| four scratch worktrees of the platform repo (feature branches, all merged into main) | dormant | the pre-fix runner lockfile (3 high) | the two clean ones retired; the two carrying uncommitted work got main's lockfile (`package.json` identical; `npm audit` 0) |
| every running `:latest` image | running | 0 | none |

**Why the running images were clean:** most first-party `requirements.txt` files are ranges, so the last rebuild happened to resolve `urllib3` to the fixed 2.8.0. The 8-week-old tags show the same files resolving to a vulnerable version. That is luck, not control. The deterministic-builds pass (review 05) replaces it with hash-locked installs.

The scheduled audit now includes dormant image tags and worktrees, and they block like running code (`audit_dormant_images`, `worktrees`; tests in `DormantIsAudited`).

## Not covered (stated plainly)

- `pyproject.toml`-only projects (the public tooling repository) are listed but have no lockfile to audit.
- **Third-party images** (nginx, postgres, Nextcloud, the SDR feeders and others), including dormant ones such as `acarsdec`, are not scanned. That would need an image scanner, which is not installed.
- Operating-system packages and container base images are outside this audit. They belong to the deterministic-builds item at the top of the backlog.
