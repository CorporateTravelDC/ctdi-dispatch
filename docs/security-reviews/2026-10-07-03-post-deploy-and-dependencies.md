# Security review 03 — post-deploy verification and dependency pass (2026-10-07)

> **Review record. Frozen once committed.** Part of the security review chain (`README.md` in this directory). It follows `2026-10-07-02-hardening.md`. The canonical documents are unchanged by this pass apart from the dependency notes below.

## Part 1 — the hardening is live (verified after deploy commit `8d509d1`)

| Check | Result |
|---|---|
| A gated root command without approval (`sudo -n /usr/bin/dnf remove …`) | **"a password is required"**: the last-match override wins over the older passwordless entries |
| Passwordless root rules for the gated commands | only `/usr/local/libexec/ctdc/approved-exec.py` |
| Executor and allowlist | installed root-owned in `/usr/local/libexec/ctdc/` from the signed manifest |
| Approval key pin | `/etc/corporatetraveldc/approval-allowed-signers`, root-owned |
| Running web image | canonical v2, per-kind TTL caps, governance audit hooks and the console redemption bound all present |
| Audit chain | `common.audit_chain`: intact, 419 chained rows |
| Health | `/healthz` 200 |

So `docs/AGENT_TRUST_MODEL.md` §9.1, which said "ENFORCED once the sudoers change is applied", now holds: the change is applied. No governance events had fired yet at check time, since there had been no approvals or console activity after the deploy. The code paths are present in the running image.

## Part 2 — dependency vulnerabilities

**Trigger.** GitHub Dependabot reported 5 alerts (3 high, 2 moderate) on the public mirror.

**Gap found by this pass.** Dependabot is **enabled only on the public platform mirror**. It is disabled on every other repository, including the private ones and the site repositories, so the absence of alerts there proved nothing. The pass therefore also audited **what is actually installed** in every first-party image (`pip freeze` from the running containers, then `pip-audit`).

| Where | Packages | Advisories | Exposure | Fix |
|---|---|---|---|---|
| Runner dashboard frontend (npm lockfile) — the 5 Dependabot alerts | `source-map-js` 1.2.1, `brace-expansion` 2.1.4 and 5.0.9, `fast-uri` 3.1.6 | 5 (DoS, URI authority / host confusion) | build-time toolchain | lockfile-only update to 1.2.2, 2.1.7 / 5.0.12, 3.1.8 (`npm audit`: 0); the build now runs **`npm ci`** (was `npm install`, which could re-resolve versions at build time) |
| [operator LLC abbreviation]utive contact-form API | `starlette` 0.37.2, `python-multipart` 0.0.9 | 14 unique (mostly upload-parsing denial of service) | **public endpoint** | `fastapi` 0.142.2, `starlette` 1.7.0, `uvicorn` 0.54.0, `pydantic` 2.13.5, `python-multipart` 0.0.32; smoke-tested (healthz 200, valid enquiry 200, malformed 422); re-audit clean |
| acars-watcher | `requests` 2.32.3, `urllib3` 2.7.0 | 5 | internal | `requests` 2.34.2, `urllib3` 2.8.0 pinned explicitly; re-audit clean |
| web, poller, ingest, pusher, runner, amtrak-tracker images | — | 0 | — | none needed |

Each fixed image was trial-built under a throwaway tag before deploy.

## Recommendations carried to the backlog

1. **Deterministic, version-locked builds** (the external reviewer's finding, now top of the backlog). Python requirements are mostly ranges; base images float on tags (`node:20-alpine`, `python:3.12-slim`); and the operating system's package managers resolve live. This pass pinned the vulnerable packages, but a full lock (hashes, image digests) is the real fix.
2. **Dependency scanning on every repository.** Either enable Dependabot on the private and site repositories (an operator decision, because it is a GitHub-side setting), or run the local `pip-audit` / `npm audit` pass on a schedule. The local pass is what found 19 of the 24 advisories here.
