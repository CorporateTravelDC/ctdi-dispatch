# Pending Work — Staged 2026-06-05

---

## ~~TASK 1 — FAA SWIM NMS Credentials Request Email~~ ✅ DONE 2026-06-05

Final version accounts for NMS Split architecture. AIM already working.
Feeds requested: FDPS, STDDS, TFMS, TBFM, ITWS.
Send via https://swim.faa.gov — include read-only GitHub token on request.

---

## TASK 2 — `docs/ARCHITECTURE.md` — Deferred to next week

Re-read key source files before writing (repo may have changed):
src/web/main.py, src/poller/main.py, src/pusher/main.py,
src/ingest/main.py + swim_client.py + nms_client.py,
src/common/db.py, src/auth/auth.py, CLAUDE.md

Output: commit to docs/ARCHITECTURE.md via Contents API.

---

## TASK 3 — Mirror README to Obsidian Vault — Blocked on Cowork rebuild

Blocked until Cowork reinstalled on laptop + Obsidian MCP reconnected.
Alternative: Obsidian Local REST API plugin over Tailscale (no Cowork needed).
README source: commit 59976f9.

---

## TASK 4 — dispatch-assistant Image Builder Pipeline — CONFIRMED LOST, NEEDS REBUILD ⚠️

**Status:** Confirmed lost. No separate GitHub repo exists. Pipeline source was
never committed — only build/ artifacts were .gitignored, but the Makefile,
firstboot scripts, virt-customize overlay, and cloud-init seed were simply
never added to git.

**What needs to be reconstructed:**
- `Makefile` — `make build` target invoking libguestfs virt-customize
- `overlay/` — files baked into the image (Quadlets, dispatch.env template,
  nginx vhosts, Let's Encrypt hooks, Tailscale bootstrap)
- `firstboot/` — scripts running lexically on first Pi boot:
  - `10-users.sh` — create corporatetraveldc user
  - `20-network.sh` — Tailscale join
  - `30-storage.sh` — mount /var/lib/corporatetraveldc
  - `40-containers.sh` — podman/quadlet setup
  - `50-services.sh` — pull/start containers
  - `60-nginx.sh` — vhost install + certbot DNS-01
  - `99-cleanup.sh` — remove firstboot service, reboot
- `cloud-init/` — user-data + meta-data seed
- NVMe config.txt flags must be baked into overlay
  (see docs/PI5-BOOT-CONFIG.md for exact flags)

**Reconstruction sources:**
- Project context file (userPreferences) — full pipeline description
- Session history (chat sessions 1–2, 2026) — firstboot scripts were
  written in detail during those sessions; search past chats
- docs/PI5-BOOT-CONFIG.md — NVMe/PCIe config.txt flags

**Target:** Separate directory in this repo — `image-builder/` — so it
is never lost again. This is a dedicated session task, not a quick fix.

---

## Session notes

- README.md: 59976f9
- PI5-BOOT-CONFIG.md: 24c325b
- Token valid 90 days from 2026-06-05
- Base URL: https://dispatch.csexecutiveservices.com
- Repo: https://github.com/CorporateTravelDC/corporatetraveldc-dispatch
