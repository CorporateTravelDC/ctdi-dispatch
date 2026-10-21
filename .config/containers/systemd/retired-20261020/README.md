# Retired 2026-10-20 -- dead Quadlets (cleanup sweep)

Nothing here is installed or run. Indexed in `docs/RETIRED.md`.

**Never copy this folder into `~/.config/containers/systemd/`.** Podman's Quadlet
generator also reads subdirectories there, so a copied `*.container` would come
back as a live unit. The drift checker (`scripts/check-claude-md-drift.sh` check
11) compares only the top level of `.config/containers/systemd/`.

| File | What it was | Why retired | Replaced by |
|---|---|---|---|
| `corporatetraveldc-ops-brief-deferred.container` | deferred post-webinar ops-brief rerun (`ops_brief.py --deferred-rerun`) | the deferred rerun was removed from `src/poller/skills/ops_brief.py` on 2026-07-20; nothing triggers the unit; its live timer and service were `/dev/null` masks | nothing (the hourly `corporatetraveldc-ops-brief` covers it) |
| `corporatetraveldc-nms-v240-check.container` | one-shot FAA NMS v2.4.0 post-deploy verification | its timer's single date (2026-08-08) is past | nothing (one-shot done); timer in `.config/systemd/user/retired-20261020/` |
| `amtrak-tracker.container.disabled` | stale 2026-07 first draft of the amtrak tracker Quadlet (commit `f9584869`) | mounts `/var/lib/amtrak-tracker` (absent), no Postgres socket, no memory cap; created no unit | `.config/containers/systemd/amtrak-tracker.container` (live, unchanged) |
