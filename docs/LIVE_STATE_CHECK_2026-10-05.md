# Live State Check — 2026-10-05

_Findings are gathered deterministically; narratives are written
by the local model (no cloud, no credits) and summarize those
findings only — the raw facts are authoritative._

## Weekly drift check — 09:05 EDT

_Narrative synthesis unavailable (model call failed, timed out, or slot unavailable). Raw deterministic findings below._

```
## Deterministic drift checks (scripts/check-claude-md-drift.sh)
[OK] no retired terms in CLAUDE.md
[OK] no hardcoded unit counts
[OK] model count matches (21)
[OK] single model base: phi3:mini 
[OK] all Modelfiles covered by scrub_tree()'s corporatetraveldc. pattern rule
[OK] no failed or crash-looping units
[OK] no failed or crash-looping system units
[OK] Known bad section is 1 days old
[OK] no quoted env values in dispatch.env (podman --env-file safe)
[OK] manifest and signature both clean
[OK] API healthy at http://127.0.0.1:8000
[OK] .git/hooks/pre-commit matches scripts/pre-commit
[OK] .git/hooks/pre-push matches scripts/pre-push
[OK] .git/hooks/post-commit matches scripts/post-commit-doc-verify.sh
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-daily-opsplan.timer matches tracked .config/systemd/user/corporatetraveldc-daily-opsplan.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-freshness-audit.timer matches tracked .config/systemd/user/corporatetraveldc-freshness-audit.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-container-mem-watch.service matches tracked .config/systemd/user/corporatetraveldc-container-mem-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-container-mem-watch.timer matches tracked .config/systemd/user/corporatetraveldc-container-mem-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ingest-restart.service matches tracked .config/systemd/user/corporatetraveldc-ingest-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ingest-restart.timer matches tracked .config/systemd/user/corporatetraveldc-ingest-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nextcloud-health.service matches tracked .config/systemd/user/corporatetraveldc-nextcloud-health.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nextcloud-health.timer matches tracked .config/systemd/user/corporatetraveldc-nextcloud-health.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-daily.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-demo-archiver-daily.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-demo-archiver-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-index-scan.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-index-scan.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-rss.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-rss.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-ingest-guard.timer matches tracked .config/systemd/user/corporatetraveldc-thermal-ingest-guard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/nextcloud-cron.service matches tracked .config/systemd/user/nextcloud-cron.service
[OK] /home/corporatetraveldc/.config/systemd/user/nextcloud-cron.timer matches tracked .config/systemd/user/nextcloud-cron.timer
[OK] /home/corporatetraveldc/.config/systemd/user/ops-brief-rebuild-watcher.service matches tracked .config/systemd/user/ops-brief-rebuild-watcher.service
[OK] /home/corporatetraveldc/.config/systemd/user/ops-brief-rebuild-watcher.timer matches tracked .config/systemd/user/ops-brief-rebuild-watcher.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-sample.service matches tracked .config/systemd/user/corporatetraveldc-thermal-sample.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-sample.timer matches tracked .config/systemd/user/corporatetraveldc-thermal-sample.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-compliance-egress-push.timer matches tracked .config/systemd/user/corporatetraveldc-compliance-egress-push.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-compliance-egress-push.service matches tracked .config/systemd/user/corporatetraveldc-compliance-egress-push.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-link-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-adsb-link-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-link-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-adsb-link-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.timer
[WARN] tracked .config/systemd/user/corporatetraveldc-mcpo.service has no live installed copy at /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-mcpo.service -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-data-usage-snapshot.service matches tracked .config/systemd/user/corporatetraveldc-data-usage-snapshot.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-data-usage-snapshot.timer matches tracked .config/systemd/user/corporatetraveldc-data-usage-snapshot.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-personal-export-watch.timer matches tracked .config/systemd/user/corporatetraveldc-personal-export-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-feed-db-integrity-check.timer matches tracked .config/systemd/user/corporatetraveldc-feed-db-integrity-check.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nms-v240-check.timer matches tracked .config/systemd/user/corporatetraveldc-nms-v240-check.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-tbfm-arrival-enrichment.timer matches tracked .config/systemd/user/corporatetraveldc-tbfm-arrival-enrichment.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-pull-path-verify.timer matches tracked .config/systemd/user/corporatetraveldc-pull-path-verify.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-brief-fallback-monitor.service matches tracked .config/systemd/user/corporatetraveldc-brief-fallback-monitor.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-brief-fallback-monitor.timer matches tracked .config/systemd/user/corporatetraveldc-brief-fallback-monitor.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-personal-notes-import.timer matches tracked .config/systemd/user/corporatetraveldc-personal-notes-import.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-research-board-mirror.timer matches tracked .config/systemd/user/corporatetraveldc-research-board-mirror.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-integrity-sweep.service matches tracked .config/systemd/user/corporatetraveldc-integrity-sweep.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-integrity-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-integrity-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-website-integrity-sweep.service matches tracked .config/systemd/user/corporatetraveldc-website-integrity-sweep.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-website-integrity-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-website-integrity-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ingest-feed-watch.timer matches tracked .config/systemd/user/corporatetraveldc-ingest-feed-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-board-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-board-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.service matches tracked .config/systemd/user/corporatetraveldc-governor-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.timer matches tracked .config/systemd/user/corporatetraveldc-governor-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.timer matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.service matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.timer matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-claude-md-drift-daily.service matches tracked .config/systemd/user/corporatetraveldc-claude-md-drift-daily.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-claude-md-drift-daily.timer matches tracked .config/systemd/user/corporatetraveldc-claude-md-drift-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-failover-kickover-guardrail.service matches tracked .config/systemd/user/corporatetraveldc-failover-kickover-guardrail.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-failover-kickover-guardrail.timer matches tracked .config/systemd/user/corporatetraveldc-failover-kickover-guardrail.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-entity-tracking-digest.timer matches tracked .config/systemd/user/corporatetraveldc-entity-tracking-digest.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ops-brief.timer matches tracked .config/systemd/user/corporatetraveldc-ops-brief.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ep-advance.timer matches tracked .config/systemd/user/corporatetraveldc-ep-advance.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-unit-failure-notify@.service matches tracked .config/systemd/user/corporatetraveldc-unit-failure-notify@.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.service matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.service
[OK] /home/corporatetraveldc/.config/systemd/user/cloudflared.service matches tracked .config/systemd/user/cloudflared.service
[OK] /home/corporatetraveldc/.config/systemd/user/production.slice matches tracked .config/systemd/user/production.slice
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-convective-sigmet-archiver.timer matches tracked .config/systemd/user/corporatetraveldc-convective-sigmet-archiver.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-swim-session-health.service matches tracked .config/systemd/user/corporatetraveldc-swim-session-health.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-swim-session-health.timer matches tracked .config/systemd/user/corporatetraveldc-swim-session-health.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-24h.service matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-24h.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-24h.timer matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-24h.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-7d.service matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-7d.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-7d.timer matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-7d.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.service matches tracked .config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-weekly-external-image-update.service matches tracked .config/systemd/user/corporatetraveldc-weekly-external-image-update.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-weekly-external-image-update.timer matches tracked .config/systemd/user/corporatetraveldc-weekly-external-image-update.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.timer matches tracked .config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-semantic-compile-daily.timer matches tracked .config/systemd/user/corporatetraveldc-semantic-compile-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-knowledge-graph-compile.timer matches tracked .config/systemd/user/corporatetraveldc-knowledge-graph-compile.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-semantic-compile-daily.path matches tracked .config/systemd/user/corporatetraveldc-semantic-compile-daily.path
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-knowledge-graph-compile.path matches tracked .config/systemd/user/corporatetraveldc-knowledge-graph-compile.path
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-runner-health-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-runner-health-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-runner-health-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-runner-health-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-faa-cifp-pull.timer matches tracked .config/systemd/user/corporatetraveldc-faa-cifp-pull.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-faa-cifp-parse.timer matches tracked .config/systemd/user/corporatetraveldc-faa-cifp-parse.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-net-failover-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-net-failover-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-net-failover-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-net-failover-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-aam-weekly-watch.timer matches tracked .config/systemd/user/corporatetraveldc-aam-weekly-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dispatch-desk-memo.timer matches tracked .config/systemd/user/corporatetraveldc-dispatch-desk-memo.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-weekly-summary.timer matches tracked .config/systemd/user/corporatetraveldc-weekly-summary.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.service matches tracked .config/systemd/user/corporatetraveldc-llama-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.timer matches tracked .config/systemd/user/corporatetraveldc-llama-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-boot-stagger.service
[WARN] tracked .config/systemd/user/corporatetraveldc-mcpo-public.service has no live installed copy at /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-mcpo-public.service -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-stack-boot-stagger.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-ingest-guard.service matches tracked .config/systemd/user/corporatetraveldc-thermal-ingest-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.service matches tracked .config/systemd/user/corporatetraveldc-dns-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.timer matches tracked .config/systemd/user/corporatetraveldc-dns-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama.service matches tracked .config/systemd/user/corporatetraveldc-llama.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-retrofit-links.timer matches tracked .config/systemd/user/corporatetraveldc-retrofit-links.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.service matches tracked .config/systemd/user/corporatetraveldc-podman-prune.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.timer matches tracked .config/systemd/user/corporatetraveldc-podman-prune.timer
[OK] /home/corporatetraveldc/.config/systemd/user/agents.slice matches tracked .config/systemd/user/agents.slice
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.service matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.service matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.timer matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.service matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.timer matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.timer
[WARN] tracked .config/systemd/user/humans.slice has no live installed copy at /home/corporatetraveldc/.config/systemd/user/humans.slice -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/acars-net.network matches tracked .config/containers/systemd/acars-net.network
[WARN] tracked .config/containers/systemd/amtrak-tracker.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/amtrak-tracker.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/nextcloud-db.container matches tracked .config/containers/systemd/nextcloud-db.container
[OK] /home/corporatetraveldc/.config/containers/systemd/ntfy.container matches tracked .config/containers/systemd/ntfy.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-client-demo@.container matches tracked .config/containers/systemd/corporatetraveldc-client-demo@.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acars-watcher.container matches tracked .config/containers/systemd/corporatetraveldc-acars-watcher.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarshub.container matches tracked .config/containers/systemd/corporatetraveldc-acarshub.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-airnavradar.container matches tracked .config/containers/systemd/corporatetraveldc-airnavradar.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-fr24feed.container matches tracked .config/containers/systemd/corporatetraveldc-fr24feed.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-pgsql.container matches tracked .config/containers/systemd/corporatetraveldc-pgsql.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-piaware.container matches tracked .config/containers/systemd/corporatetraveldc-piaware.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-planefinder.container matches tracked .config/containers/systemd/corporatetraveldc-planefinder.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-protonbridge.container matches tracked .config/containers/systemd/corporatetraveldc-protonbridge.container
[OK] /home/corporatetraveldc/.config/containers/systemd/csexec-contact.container matches tracked .config/containers/systemd/csexec-contact.container
[OK] /home/corporatetraveldc/.config/containers/systemd/nextcloud-app.container matches tracked .config/containers/systemd/nextcloud-app.container
[OK] /home/corporatetraveldc/.config/containers/systemd/openwebui.container matches tracked .config/containers/systemd/openwebui.container
[OK] /home/corporatetraveldc/.config/containers/systemd/rss-bridge.container matches tracked .config/containers/systemd/rss-bridge.container
[WARN] tracked .config/containers/systemd/corporatetraveldc-demo-portal-client.container has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-portal-client.container -- fine if deliberately disabled/dormant, otherwise install it
[WARN] tracked .config/containers/systemd/corporatetraveldc-demo-portal-personal.container has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-portal-personal.container -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-daily-opsplan.container matches tracked .config/containers/systemd/corporatetraveldc-daily-opsplan.container
[OK] /home/corporatetraveldc/.config/containers/systemd/amtrak-tracker.container matches tracked .config/containers/systemd/amtrak-tracker.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-board-sweep.container matches tracked .config/containers/systemd/corporatetraveldc-board-sweep.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-entity-tracking-digest.container matches tracked .config/containers/systemd/corporatetraveldc-entity-tracking-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-faa-cifp-parse.container matches tracked .config/containers/systemd/corporatetraveldc-faa-cifp-parse.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-freshness-audit.container matches tracked .config/containers/systemd/corporatetraveldc-freshness-audit.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-feed-watch.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-feed-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-tbfm.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-tbfm.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ops-brief.container matches tracked .config/containers/systemd/corporatetraveldc-ops-brief.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-runner.container matches tracked .config/containers/systemd/corporatetraveldc-runner.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-index-scan.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-index-scan.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-tbfm-arrival-enrichment.container matches tracked .config/containers/systemd/corporatetraveldc-tbfm-arrival-enrichment.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aam-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aam-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aviation-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aviation-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-convective-sigmet-archiver.container matches tracked .config/containers/systemd/corporatetraveldc-convective-sigmet-archiver.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ep-advance.container matches tracked .config/containers/systemd/corporatetraveldc-ep-advance.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ep-advance-venues.container matches tracked .config/containers/systemd/corporatetraveldc-ep-advance-venues.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-executive-protection-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-executive-protection-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-faa-cifp-pull.container matches tracked .config/containers/systemd/corporatetraveldc-faa-cifp-pull.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-feed-db-integrity-check.container matches tracked .config/containers/systemd/corporatetraveldc-feed-db-integrity-check.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-gig-economy-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-gig-economy-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-core.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-core.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-fdps.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-fdps.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-itws.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-itws.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-notam.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-notam.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-stdds.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-stdds.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-tfms.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-tfms.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-nms-v240-check.container matches tracked .config/containers/systemd/corporatetraveldc-nms-v240-check.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ops-brief-deferred.container matches tracked .config/containers/systemd/corporatetraveldc-ops-brief-deferred.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-personal-export-watch.container matches tracked .config/containers/systemd/corporatetraveldc-personal-export-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-personal-notes-import.container matches tracked .config/containers/systemd/corporatetraveldc-personal-notes-import.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-pull-path-verify.container matches tracked .config/containers/systemd/corporatetraveldc-pull-path-verify.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-pusher.container matches tracked .config/containers/systemd/corporatetraveldc-pusher.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-research-board-mirror.container matches tracked .config/containers/systemd/corporatetraveldc-research-board-mirror.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-runner-demo.container matches tracked .config/containers/systemd/corporatetraveldc-runner-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-demo-archiver-daily.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-demo-archiver-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-rss.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-rss.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-api.container matches tracked .config/containers/systemd/corporatetraveldc-demo-api.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo.container matches tracked .config/containers/systemd/corporatetraveldc-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-daily.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aam-weekly-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aam-weekly-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container matches tracked .config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-weekly-summary.container matches tracked .config/containers/systemd/corporatetraveldc-weekly-summary.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-weekly.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-weekly.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-disruption-weather-digest.container matches tracked .config/containers/systemd/corporatetraveldc-disruption-weather-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container matches tracked .config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-semantic-compile-daily.container matches tracked .config/containers/systemd/corporatetraveldc-semantic-compile-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container matches tracked .config/containers/systemd/corporatetraveldc-transport-pattern-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-poller.container matches tracked .config/containers/systemd/corporatetraveldc-poller.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-web.container matches tracked .config/containers/systemd/corporatetraveldc-web.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-retrofit-links.container matches tracked .config/containers/systemd/corporatetraveldc-retrofit-links.container
[WARN] tracked .config/containers/systemd/corporatetraveldc-acarsdec.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsdec.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsrouter.container matches tracked .config/containers/systemd/corporatetraveldc-acarsrouter.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dumpvdl2.container matches tracked .config/containers/systemd/corporatetraveldc-dumpvdl2.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ultrafeeder.container matches tracked .config/containers/systemd/corporatetraveldc-ultrafeeder.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-execstandard-verifier.container matches tracked .config/containers/systemd/corporatetraveldc-execstandard-verifier.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ccw-demo.container matches tracked .config/containers/systemd/corporatetraveldc-ccw-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ccw-preview1.container matches tracked .config/containers/systemd/corporatetraveldc-ccw-preview1.container
[OK] /home/corporatetraveldc/.cloudflared/config.yml matches tracked cloudflared/config.yml
[OK] skills: 9 tracked, 0 divergent, 0 missing-live, 0 untracked-live (vendor dirs ignored via skills-vendor-ignore.txt)
--
[OK] CLAUDE.md matches live state

## Failed / crash-looping units
(empty above means none)

## Running containers
corporatetraveldc-ccw-demo	Up 13 hours
corporatetraveldc-ccw-preview1	Up 13 hours
ntfy	Up 13 hours
corporatetraveldc-pgsql	Up 13 hours (healthy)
nextcloud-db	Up 13 hours
nextcloud-app	Up 13 hours
corporatetraveldc-ultrafeeder	Up 13 hours
corporatetraveldc-piaware	Up 13 hours
corporatetraveldc-fr24feed	Up 13 hours
corporatetraveldc-planefinder	Up 13 hours
corporatetraveldc-airnavradar	Up 13 hours
systemd-corporatetraveldc-protonbridge	Up 13 hours
corporatetraveldc-acarshub	Up 13 hours
corporatetraveldc-dumpvdl2	Up 13 hours
openwebui	Up 13 hours
corporatetraveldc-acarsrouter	Up 12 hours
systemd-corporatetraveldc-ingest-notam	Up 2 hours
systemd-corporatetraveldc-ingest-itws	Up 2 hours
systemd-corporatetraveldc-ingest-tbfm	Up 2 hours
systemd-corporatetraveldc-ingest-tfms	Up 2 hours
systemd-corporatetraveldc-ingest-stdds	Up 2 hours
systemd-corporatetraveldc-ingest-fdps	Up 2 hours
systemd-corporatetraveldc-ingest-core	Up 2 hours
systemd-corporatetraveldc-poller	Up 2 hours
systemd-corporatetraveldc-execstandard-verifier	Up 2 hours
systemd-corporatetraveldc-pusher	Up 2 hours
systemd-corporatetraveldc-web	Up 2 hours
systemd-corporatetraveldc-runner	Up 2 hours
systemd-corporatetraveldc-runner-demo	Up 2 hours
systemd-corporatetraveldc-demo-api	Up 2 hours
systemd-corporatetraveldc-demo	Up 2 hours
systemd-amtrak-tracker	Up About an hour
corporatetraveldc-acars-watcher	Up About an hour
csexec-contact	Up About an hour
rss-bridge	Up About an hour
systemd-corporatetraveldc-ep-advance	Up 25 minutes
systemd-corporatetraveldc-feed-db-integrity-check	Up 2 seconds
systemd-corporatetraveldc-tbfm-arrival-enrichment	Up 2 seconds
systemd-corporatetraveldc-board-sweep	Up 1 second
systemd-corporatetraveldc-personal-notes-import	Up 1 second

## Commits since the previous LIVE_STATE_CHECK
9ee6556 NOTAMs: CRANE is VIP only as a callsign (CRANE01/05/50); TFR priority by citing authority -- 91.141/91.143/99.7/49 USC 40103(b) = 5 everywhere, 91.137/91.145 = home 5 / monitor 4 / else 3; home + monitor zones are per-deployment settings with no code default (this deployment ZDC + ZNY,ZID,ZOB,ZLA,ZTL; public edition ships them empty); authority TFRs alerted nationwide
3fc2c66 Deploy follow-up: podman prune waits out a rollout/stack refresh and retries the in-use race; rollout health check reads BOARD_KEY directly (operator-owned file) instead of an approval that expires while mobile; sign-manifest leaves the manifest pair 0644 (mktemp 0600 broke ctdc-agent-llama's verify); verify-manifest stops its temp gpg-agent
c6921c0 Batch 2026-10-05: no secret on any command line (32 scripts: tokens via private fd, PG password via env); NTS cert refresh as a root installed unit (user unit needed interactive sudo); five day-keyed skills on the operational day; every first-party container on a scoped secrets file (52 quadlets, scan.py import-closure allowlists); LLAMA_BASE_URL replaces OLLAMA_BASE_URL (semantic --ask off the dead Ollama API); plan.sh --rename-account + board-signer-ctl rename; guardrails/ISO/README docs off the Allow tap
ac0ebb6 Wave 2 remainder + segmentation end state: plan.sh re-asserts repo group READ (it re-applied g+rwX every run) and drops systemd-journal; verify fails repo/.git/hooks write; fail2ban actions run root-installed copies via installed-check (installer deploys action/jail/filter, tests + reloads; live limit-req jail still carried the Aug-26 lockdown); drift check sees failed system units; U6 signed query + single-use signatures; human-signed approvals with a separate passphrase-protected key, deny-only phone pushes; council/arena convenes; create-only attributed shared workspace with per-account/per-task grants; llama council runner; migration 0069; pamphlet: read-only repos, draft never publish, never approve
ecfe4f2 Wave 2 remainder + segmentation end state: plan.sh re-asserts repo group READ (it re-applied g+rwX every run) and drops systemd-journal; verify fails repo/.git/hooks write; fail2ban actions run root-installed copies via installed-check (installer deploys action/jail/filter, tests + reloads; live limit-req jail still carried the Aug-26 lockdown); drift check sees failed system units; U6 signed query + single-use signatures; human-signed approvals with a separate passphrase-protected key, deny-only phone pushes; council/arena convenes; create-only attributed shared workspace with per-account/per-task grants; llama council runner; migration 0069; pamphlet: read-only repos, draft never publish, never approve
bb333f7 Skill grants: list directories through a fresh fd (btrfs fixes readdir at open; first real apply hashed every copy as empty), one bad skill is a finding not an abort, apply suite also runs on btrfs; watchdog drop-in renamed zz-ctdc-watchdog.conf so it sorts after the stock watchdog.conf (90- never took effect); cert renew uses reload-or-restart and starts a down nginx (19:54 boot race left nginx failed); contract tests
cd5977c Root boundary + skill grants: root-run scripts execute from root-owned /usr/local/libexec/ctdc (install-root-copies.sh verifies the signed manifest first); trust pin under /etc parsed not sourced; watchdog/cert-renew no source-as-root; kill orders staged root-only in /var/lib/ctdc-liveness (outside every container mount -- fixes the 20:05 exit-126 incident) with symlink/owner/squat/size/count checks; registry-unavailable holds; per-agent/per-task skill grants with clawback (signed + hash-pinned vendor + project skills, root-owned copies, quarantine, tamper restore, settings merged as the agent), pamphlet "Your skills"; guardian uses the account's own key and never generates one; watchdog 180s diagnostic + stall monitor + one-shot tune 2026-10-11; verifier scoped env
6d02bf9 Duel fix wave 1: approval gate needs a push-only per-request key (0068) and agent/ops subsets drop admin/board/ntfy/nextcloud-admin creds; rollout deploys the gated image (FROM_REFRESH), postgres canary-only, :previous on change only, built-ids audit trust, 4h deadline; rolling guard reads the current weekday, dispatcher tracks outcomes; optime helper + naive-date guard; per-service env allowlists + verifier pilot; airline zero-strip, plan.sh fail-fast; codex account renamed ctdc-agent-openai-codex
1de33fa Account kinds (agent/claude, agent/ssh, service, preloaded) with /etc/ctdc-accounts.conf; plan.sh --add-service/--activate/--rehome-inbound-key/--ssh-pubkey-file; per-kind liveness; kind-aware verify/rollback/pamphlet; team segmentation showcase with 15 reviewed screenshots; redact-screenshot.py (noise under mosaic); board-sign.sh prints HTTP status
7a1a521 with-dispatch-env: fall back to the account's own secrets subset when the production files are unreadable (unit-started agent sessions have no profile export); BOARD_SIGNING.md: inbound vs signing identity
0a16209 Segmentation: inbound vs signing identity -- the first agent inherits the cowork pubkey for ssh but generates its own on-box ctdc-agent_ed25519 and registers THAT as its board signer (cowork private half is off-box, so it had nothing to sign with); every agent gets its own named remote-control unit
aedfc66 verify.sh: manager queries escalate (runuser needs root); 6c tests repo readability directly
be60fa3 render-onboarding: signer lookup root-safe and non-fatal (stage C ran silent on it)
4c9603c Segmentation tooling: agent user manager via runuser + runtime dir (not the machine transport); pamphlet render root-safe; dry-run test scoped to artefacts that must never exist
0519765 Segmentation tooling: reach the agent's user manager via runuser + its runtime dir instead of the machine transport (refuses while logind reports the user closing); dry-run test scoped to artefacts that must never exist
f2025f9 Checkpoint: segmentation stages A+B done; team-liveness first-contact fixes (new-agent grace, signer hook as operator under root, passwordless lock tolerated, unprivileged verdict labelled); onboarding pamphlet rendered per account into its home (static mirror; WorkingDirectory stays the home)
f2e9af6 stack-refresh: final clean-tree check before promotion (dirty = discard check tags, all held), :previous moves only on a real change, our own builds are pending-restart not tampering; rollout list gains ccw-demo/ccw-preview1 and service= labels
17cc7eb personal-export-analysis: longitudinal store + vault conventions (Uber exports CURRENT/history, pointers)
d5bad73 stack-refresh: a rollout refusal at hand-off is 'all held for review' not 'left behind'; StartedAt via .Unix; export skill keeps the voice profile's CURRENT.md mirror fresh
3ef6290 stack-refresh: restart every container every run (StartedAt as audit signal); explicit 'held for review, no restart' with persistent review debt; left-behind post-check
8fd6112 stack-refresh: restart every container every run (StartedAt as audit signal); explicit 'held for review, no restart' with persistent review debt; left-behind post-check
058b369 Key convention <account>_ed25519 for generated keys and signing clients; stack-refresh classifies running==:previous as pending-restart (warn + force rollout) not tampering; serialized-rollout restarts externals running behind their tag
3d32bba Authorization model: liveness is an AND (login, active key, no token revocation, no kill order); board tiers key/normal/high/cosign with role+kind on signers (0067); signed file-based kill orders with quorum (one admin or two non-admins; admins need quorum; operator never a target); path-triggered liveness unit
65730ad Team liveness dead-man switch (agents: refresh-token + 7d access-token refresh; humans: shell/key/lock/idle rules; inert = terminate+lock+revoke, reactivate explicit); board signing by account identity (SSH ed25519 over canonical message, board_signers registry, either/both policy, X-Board-Key unchanged); serialized-rollout refuses a dirty tree
51f3698 First stack-refresh audit: track three live-only quadlets (execstandard-verifier, ccw-demo, ccw-preview1), verifier joins the rollout list, audit compares image IDs not digest representations
b8b02b3 Standing practice + team segmentation: stack-refresh weekly and unclockable adversarial tripwire (quiet-window draw seeded by the 30-day load profile, 36h floor, 7d ceiling, monthly new window); rolling maintenance windows + queue dispatcher for 9 long-runners; quiet-window profile; guard stops shell-sourcing dispatch.env; serialized-rollout :previous + SKIP_UNITS; segmentation generalised to humans + agents with one SSH key per account
f132ff0 Swarm 2: tbfm-arrival-enrichment root cause (psycopg3 executemany shim returned None since d34aa67; skill now fails loudly), 15 long-runner quadlets get bounded lock waits + contract test, second-brain-daily keys its day-file on the ET operational day (poller image is UTC), dispatch-panel parity doc; CLAUDE.md post Gate-2 reset
97b9075 Swarm close-out: 2A twin gate (skills tracked + sync + hard drift check), ctdc-agent segmentation tooling, self-verifying public manifest, second-brain-daily bounded lock wait (root cause: unbounded flock on the shared long-runner lock), ACARS IATA/ICAO + tail matching, test suite 549/0, cifp_lookup + PG_TABLES fixes; CLAUDE.md consolidated
d9c2e24 ACARS/VDL2 night: local_airspace reads the router's VDL2 stream (acars_messages had never held a real row); airframes VDL2 credited for the first time -- router TCP 5553 with AR_ADD_PROXY_ID=false, direct dumpvdl2 output overlay kept off; prod contaminant sweep; CLAUDE.md
c2b381e local_airspace: read the router's VDL2 stream (:15555) -- acars_messages had never held a real row (C-8 root cause); router quadlet publishes 15555; dumpvdl2 frame normalizer + live-frame test; CLAUDE.md: prod contaminant sweep results
c19c7c5 with-dispatch-env: verbatim env loader; pg-migrate stops shell-sourcing secrets (fragment leak); restore labels idle REST fallbacks; CLAUDE.md: nws_alerts fixture row, push:fns never-connected, NWWS rotation
51be8cc context-guardian: restore script read key names the API never returned (every field printed as ?); hook logs each run, 90s save timeout, 4M transcript tail
2bcce06 CLAUDE.md: record 2026-10-03 deploy reconcile and the heredoc-only rule
224f6a0 context-guardian: read live context size from the transcript tail; Stop payload carries no usage block (hook silent since 2026-08-16)
4f7330f Exposure push: test/prod isolation, agent segmentation, integrity coverage, guard resume bar, fail2ban token path, docs
e4ce37c Retire the public executivestandard hostname from the Pi: tunnel ingress and nginx vhost
0ea0f06 cf-dns-record.sh: fix inline-python quoting in record printers and the proxied boolean in the JSON body (caught on first --show; no DNS change was made)
946b939 Add cf-dns-record.sh: upsert/delete one Cloudflare DNS record with the local management token
91e40ce Executive Standard: canonical articles/ source; members host becomes the Pi-hosted site's canonical URL
75dee10 Feeder names -> CS-KDCA-{MODE}; drop router station override; dual-port airframes VDL2 diagnostic; retire duplicate Quadlets

## Load / thermal (most recent samples)
2026-10-05 08:50:00,66.7,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",3441,69,17.38,14.83
2026-10-05 08:55:00,64.5,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",3439,69,18.35,16.51
2026-10-05 09:00:00,65.0,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",2329,49,17.19,17.12
```

<details><summary>Raw deterministic findings</summary>

```
## Deterministic drift checks (scripts/check-claude-md-drift.sh)
[OK] no retired terms in CLAUDE.md
[OK] no hardcoded unit counts
[OK] model count matches (21)
[OK] single model base: phi3:mini 
[OK] all Modelfiles covered by scrub_tree()'s corporatetraveldc. pattern rule
[OK] no failed or crash-looping units
[OK] no failed or crash-looping system units
[OK] Known bad section is 1 days old
[OK] no quoted env values in dispatch.env (podman --env-file safe)
[OK] manifest and signature both clean
[OK] API healthy at http://127.0.0.1:8000
[OK] .git/hooks/pre-commit matches scripts/pre-commit
[OK] .git/hooks/pre-push matches scripts/pre-push
[OK] .git/hooks/post-commit matches scripts/post-commit-doc-verify.sh
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-daily-opsplan.timer matches tracked .config/systemd/user/corporatetraveldc-daily-opsplan.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-freshness-audit.timer matches tracked .config/systemd/user/corporatetraveldc-freshness-audit.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-container-mem-watch.service matches tracked .config/systemd/user/corporatetraveldc-container-mem-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-container-mem-watch.timer matches tracked .config/systemd/user/corporatetraveldc-container-mem-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ingest-restart.service matches tracked .config/systemd/user/corporatetraveldc-ingest-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ingest-restart.timer matches tracked .config/systemd/user/corporatetraveldc-ingest-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nextcloud-health.service matches tracked .config/systemd/user/corporatetraveldc-nextcloud-health.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nextcloud-health.timer matches tracked .config/systemd/user/corporatetraveldc-nextcloud-health.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-daily.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-demo-archiver-daily.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-demo-archiver-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-index-scan.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-index-scan.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-rss.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-rss.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-ingest-guard.timer matches tracked .config/systemd/user/corporatetraveldc-thermal-ingest-guard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/nextcloud-cron.service matches tracked .config/systemd/user/nextcloud-cron.service
[OK] /home/corporatetraveldc/.config/systemd/user/nextcloud-cron.timer matches tracked .config/systemd/user/nextcloud-cron.timer
[OK] /home/corporatetraveldc/.config/systemd/user/ops-brief-rebuild-watcher.service matches tracked .config/systemd/user/ops-brief-rebuild-watcher.service
[OK] /home/corporatetraveldc/.config/systemd/user/ops-brief-rebuild-watcher.timer matches tracked .config/systemd/user/ops-brief-rebuild-watcher.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-sample.service matches tracked .config/systemd/user/corporatetraveldc-thermal-sample.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-sample.timer matches tracked .config/systemd/user/corporatetraveldc-thermal-sample.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-ntfy-topic-count-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-compliance-egress-push.timer matches tracked .config/systemd/user/corporatetraveldc-compliance-egress-push.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-compliance-egress-push.service matches tracked .config/systemd/user/corporatetraveldc-compliance-egress-push.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-link-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-adsb-link-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-link-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-adsb-link-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-acars-feed-silence-watchdog.timer
[WARN] tracked .config/systemd/user/corporatetraveldc-mcpo.service has no live installed copy at /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-mcpo.service -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-data-usage-snapshot.service matches tracked .config/systemd/user/corporatetraveldc-data-usage-snapshot.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-data-usage-snapshot.timer matches tracked .config/systemd/user/corporatetraveldc-data-usage-snapshot.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-personal-export-watch.timer matches tracked .config/systemd/user/corporatetraveldc-personal-export-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-feed-db-integrity-check.timer matches tracked .config/systemd/user/corporatetraveldc-feed-db-integrity-check.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nms-v240-check.timer matches tracked .config/systemd/user/corporatetraveldc-nms-v240-check.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-tbfm-arrival-enrichment.timer matches tracked .config/systemd/user/corporatetraveldc-tbfm-arrival-enrichment.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-pull-path-verify.timer matches tracked .config/systemd/user/corporatetraveldc-pull-path-verify.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-brief-fallback-monitor.service matches tracked .config/systemd/user/corporatetraveldc-brief-fallback-monitor.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-brief-fallback-monitor.timer matches tracked .config/systemd/user/corporatetraveldc-brief-fallback-monitor.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-personal-notes-import.timer matches tracked .config/systemd/user/corporatetraveldc-personal-notes-import.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-research-board-mirror.timer matches tracked .config/systemd/user/corporatetraveldc-research-board-mirror.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-integrity-sweep.service matches tracked .config/systemd/user/corporatetraveldc-integrity-sweep.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-integrity-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-integrity-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-website-integrity-sweep.service matches tracked .config/systemd/user/corporatetraveldc-website-integrity-sweep.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-website-integrity-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-website-integrity-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ingest-feed-watch.timer matches tracked .config/systemd/user/corporatetraveldc-ingest-feed-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-board-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-board-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.service matches tracked .config/systemd/user/corporatetraveldc-governor-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.timer matches tracked .config/systemd/user/corporatetraveldc-governor-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.timer matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.service matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.timer matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-claude-md-drift-daily.service matches tracked .config/systemd/user/corporatetraveldc-claude-md-drift-daily.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-claude-md-drift-daily.timer matches tracked .config/systemd/user/corporatetraveldc-claude-md-drift-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-failover-kickover-guardrail.service matches tracked .config/systemd/user/corporatetraveldc-failover-kickover-guardrail.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-failover-kickover-guardrail.timer matches tracked .config/systemd/user/corporatetraveldc-failover-kickover-guardrail.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-entity-tracking-digest.timer matches tracked .config/systemd/user/corporatetraveldc-entity-tracking-digest.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ops-brief.timer matches tracked .config/systemd/user/corporatetraveldc-ops-brief.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ep-advance.timer matches tracked .config/systemd/user/corporatetraveldc-ep-advance.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-unit-failure-notify@.service matches tracked .config/systemd/user/corporatetraveldc-unit-failure-notify@.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.service matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.service
[OK] /home/corporatetraveldc/.config/systemd/user/cloudflared.service matches tracked .config/systemd/user/cloudflared.service
[OK] /home/corporatetraveldc/.config/systemd/user/production.slice matches tracked .config/systemd/user/production.slice
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-convective-sigmet-archiver.timer matches tracked .config/systemd/user/corporatetraveldc-convective-sigmet-archiver.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-swim-session-health.service matches tracked .config/systemd/user/corporatetraveldc-swim-session-health.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-swim-session-health.timer matches tracked .config/systemd/user/corporatetraveldc-swim-session-health.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-24h.service matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-24h.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-24h.timer matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-24h.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-7d.service matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-7d.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-cowork-coord-7d.timer matches tracked .config/systemd/user/corporatetraveldc-cowork-coord-7d.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.service matches tracked .config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-weekly-external-image-update.service matches tracked .config/systemd/user/corporatetraveldc-weekly-external-image-update.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-weekly-external-image-update.timer matches tracked .config/systemd/user/corporatetraveldc-weekly-external-image-update.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.timer matches tracked .config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-semantic-compile-daily.timer matches tracked .config/systemd/user/corporatetraveldc-semantic-compile-daily.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-knowledge-graph-compile.timer matches tracked .config/systemd/user/corporatetraveldc-knowledge-graph-compile.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-semantic-compile-daily.path matches tracked .config/systemd/user/corporatetraveldc-semantic-compile-daily.path
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-knowledge-graph-compile.path matches tracked .config/systemd/user/corporatetraveldc-knowledge-graph-compile.path
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-runner-health-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-runner-health-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-runner-health-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-runner-health-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-faa-cifp-pull.timer matches tracked .config/systemd/user/corporatetraveldc-faa-cifp-pull.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-faa-cifp-parse.timer matches tracked .config/systemd/user/corporatetraveldc-faa-cifp-parse.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-net-failover-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-net-failover-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-net-failover-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-net-failover-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-aam-weekly-watch.timer matches tracked .config/systemd/user/corporatetraveldc-aam-weekly-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dispatch-desk-memo.timer matches tracked .config/systemd/user/corporatetraveldc-dispatch-desk-memo.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-weekly-summary.timer matches tracked .config/systemd/user/corporatetraveldc-weekly-summary.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.service matches tracked .config/systemd/user/corporatetraveldc-llama-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.timer matches tracked .config/systemd/user/corporatetraveldc-llama-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-boot-stagger.service
[WARN] tracked .config/systemd/user/corporatetraveldc-mcpo-public.service has no live installed copy at /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-mcpo-public.service -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-stack-boot-stagger.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-ingest-guard.service matches tracked .config/systemd/user/corporatetraveldc-thermal-ingest-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.service matches tracked .config/systemd/user/corporatetraveldc-dns-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.timer matches tracked .config/systemd/user/corporatetraveldc-dns-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama.service matches tracked .config/systemd/user/corporatetraveldc-llama.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-retrofit-links.timer matches tracked .config/systemd/user/corporatetraveldc-retrofit-links.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.service matches tracked .config/systemd/user/corporatetraveldc-podman-prune.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.timer matches tracked .config/systemd/user/corporatetraveldc-podman-prune.timer
[OK] /home/corporatetraveldc/.config/systemd/user/agents.slice matches tracked .config/systemd/user/agents.slice
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.service matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.service matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.timer matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.service matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.timer matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.timer
[WARN] tracked .config/systemd/user/humans.slice has no live installed copy at /home/corporatetraveldc/.config/systemd/user/humans.slice -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/acars-net.network matches tracked .config/containers/systemd/acars-net.network
[WARN] tracked .config/containers/systemd/amtrak-tracker.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/amtrak-tracker.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/nextcloud-db.container matches tracked .config/containers/systemd/nextcloud-db.container
[OK] /home/corporatetraveldc/.config/containers/systemd/ntfy.container matches tracked .config/containers/systemd/ntfy.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-client-demo@.container matches tracked .config/containers/systemd/corporatetraveldc-client-demo@.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acars-watcher.container matches tracked .config/containers/systemd/corporatetraveldc-acars-watcher.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarshub.container matches tracked .config/containers/systemd/corporatetraveldc-acarshub.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-airnavradar.container matches tracked .config/containers/systemd/corporatetraveldc-airnavradar.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-fr24feed.container matches tracked .config/containers/systemd/corporatetraveldc-fr24feed.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-pgsql.container matches tracked .config/containers/systemd/corporatetraveldc-pgsql.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-piaware.container matches tracked .config/containers/systemd/corporatetraveldc-piaware.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-planefinder.container matches tracked .config/containers/systemd/corporatetraveldc-planefinder.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-protonbridge.container matches tracked .config/containers/systemd/corporatetraveldc-protonbridge.container
[OK] /home/corporatetraveldc/.config/containers/systemd/csexec-contact.container matches tracked .config/containers/systemd/csexec-contact.container
[OK] /home/corporatetraveldc/.config/containers/systemd/nextcloud-app.container matches tracked .config/containers/systemd/nextcloud-app.container
[OK] /home/corporatetraveldc/.config/containers/systemd/openwebui.container matches tracked .config/containers/systemd/openwebui.container
[OK] /home/corporatetraveldc/.config/containers/systemd/rss-bridge.container matches tracked .config/containers/systemd/rss-bridge.container
[WARN] tracked .config/containers/systemd/corporatetraveldc-demo-portal-client.container has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-portal-client.container -- fine if deliberately disabled/dormant, otherwise install it
[WARN] tracked .config/containers/systemd/corporatetraveldc-demo-portal-personal.container has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-portal-personal.container -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-daily-opsplan.container matches tracked .config/containers/systemd/corporatetraveldc-daily-opsplan.container
[OK] /home/corporatetraveldc/.config/containers/systemd/amtrak-tracker.container matches tracked .config/containers/systemd/amtrak-tracker.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-board-sweep.container matches tracked .config/containers/systemd/corporatetraveldc-board-sweep.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-entity-tracking-digest.container matches tracked .config/containers/systemd/corporatetraveldc-entity-tracking-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-faa-cifp-parse.container matches tracked .config/containers/systemd/corporatetraveldc-faa-cifp-parse.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-freshness-audit.container matches tracked .config/containers/systemd/corporatetraveldc-freshness-audit.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-feed-watch.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-feed-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-tbfm.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-tbfm.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ops-brief.container matches tracked .config/containers/systemd/corporatetraveldc-ops-brief.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-runner.container matches tracked .config/containers/systemd/corporatetraveldc-runner.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-index-scan.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-index-scan.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-tbfm-arrival-enrichment.container matches tracked .config/containers/systemd/corporatetraveldc-tbfm-arrival-enrichment.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aam-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aam-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aviation-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aviation-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-convective-sigmet-archiver.container matches tracked .config/containers/systemd/corporatetraveldc-convective-sigmet-archiver.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ep-advance.container matches tracked .config/containers/systemd/corporatetraveldc-ep-advance.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ep-advance-venues.container matches tracked .config/containers/systemd/corporatetraveldc-ep-advance-venues.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-executive-protection-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-executive-protection-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-faa-cifp-pull.container matches tracked .config/containers/systemd/corporatetraveldc-faa-cifp-pull.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-feed-db-integrity-check.container matches tracked .config/containers/systemd/corporatetraveldc-feed-db-integrity-check.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-gig-economy-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-gig-economy-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-core.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-core.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-fdps.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-fdps.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-itws.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-itws.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-notam.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-notam.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-stdds.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-stdds.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ingest-tfms.container matches tracked .config/containers/systemd/corporatetraveldc-ingest-tfms.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-nms-v240-check.container matches tracked .config/containers/systemd/corporatetraveldc-nms-v240-check.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ops-brief-deferred.container matches tracked .config/containers/systemd/corporatetraveldc-ops-brief-deferred.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-personal-export-watch.container matches tracked .config/containers/systemd/corporatetraveldc-personal-export-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-personal-notes-import.container matches tracked .config/containers/systemd/corporatetraveldc-personal-notes-import.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-pull-path-verify.container matches tracked .config/containers/systemd/corporatetraveldc-pull-path-verify.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-pusher.container matches tracked .config/containers/systemd/corporatetraveldc-pusher.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-research-board-mirror.container matches tracked .config/containers/systemd/corporatetraveldc-research-board-mirror.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-runner-demo.container matches tracked .config/containers/systemd/corporatetraveldc-runner-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-demo-archiver-daily.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-demo-archiver-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-rss.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-rss.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-api.container matches tracked .config/containers/systemd/corporatetraveldc-demo-api.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo.container matches tracked .config/containers/systemd/corporatetraveldc-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-daily.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aam-weekly-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aam-weekly-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container matches tracked .config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-weekly-summary.container matches tracked .config/containers/systemd/corporatetraveldc-weekly-summary.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-weekly.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-weekly.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-disruption-weather-digest.container matches tracked .config/containers/systemd/corporatetraveldc-disruption-weather-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container matches tracked .config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-semantic-compile-daily.container matches tracked .config/containers/systemd/corporatetraveldc-semantic-compile-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container matches tracked .config/containers/systemd/corporatetraveldc-transport-pattern-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-poller.container matches tracked .config/containers/systemd/corporatetraveldc-poller.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-web.container matches tracked .config/containers/systemd/corporatetraveldc-web.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-retrofit-links.container matches tracked .config/containers/systemd/corporatetraveldc-retrofit-links.container
[WARN] tracked .config/containers/systemd/corporatetraveldc-acarsdec.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsdec.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsrouter.container matches tracked .config/containers/systemd/corporatetraveldc-acarsrouter.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dumpvdl2.container matches tracked .config/containers/systemd/corporatetraveldc-dumpvdl2.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ultrafeeder.container matches tracked .config/containers/systemd/corporatetraveldc-ultrafeeder.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-execstandard-verifier.container matches tracked .config/containers/systemd/corporatetraveldc-execstandard-verifier.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ccw-demo.container matches tracked .config/containers/systemd/corporatetraveldc-ccw-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ccw-preview1.container matches tracked .config/containers/systemd/corporatetraveldc-ccw-preview1.container
[OK] /home/corporatetraveldc/.cloudflared/config.yml matches tracked cloudflared/config.yml
[OK] skills: 9 tracked, 0 divergent, 0 missing-live, 0 untracked-live (vendor dirs ignored via skills-vendor-ignore.txt)
--
[OK] CLAUDE.md matches live state

## Failed / crash-looping units
(empty above means none)

## Running containers
corporatetraveldc-ccw-demo	Up 13 hours
corporatetraveldc-ccw-preview1	Up 13 hours
ntfy	Up 13 hours
corporatetraveldc-pgsql	Up 13 hours (healthy)
nextcloud-db	Up 13 hours
nextcloud-app	Up 13 hours
corporatetraveldc-ultrafeeder	Up 13 hours
corporatetraveldc-piaware	Up 13 hours
corporatetraveldc-fr24feed	Up 13 hours
corporatetraveldc-planefinder	Up 13 hours
corporatetraveldc-airnavradar	Up 13 hours
systemd-corporatetraveldc-protonbridge	Up 13 hours
corporatetraveldc-acarshub	Up 13 hours
corporatetraveldc-dumpvdl2	Up 13 hours
openwebui	Up 13 hours
corporatetraveldc-acarsrouter	Up 12 hours
systemd-corporatetraveldc-ingest-notam	Up 2 hours
systemd-corporatetraveldc-ingest-itws	Up 2 hours
systemd-corporatetraveldc-ingest-tbfm	Up 2 hours
systemd-corporatetraveldc-ingest-tfms	Up 2 hours
systemd-corporatetraveldc-ingest-stdds	Up 2 hours
systemd-corporatetraveldc-ingest-fdps	Up 2 hours
systemd-corporatetraveldc-ingest-core	Up 2 hours
systemd-corporatetraveldc-poller	Up 2 hours
systemd-corporatetraveldc-execstandard-verifier	Up 2 hours
systemd-corporatetraveldc-pusher	Up 2 hours
systemd-corporatetraveldc-web	Up 2 hours
systemd-corporatetraveldc-runner	Up 2 hours
systemd-corporatetraveldc-runner-demo	Up 2 hours
systemd-corporatetraveldc-demo-api	Up 2 hours
systemd-corporatetraveldc-demo	Up 2 hours
systemd-amtrak-tracker	Up About an hour
corporatetraveldc-acars-watcher	Up About an hour
csexec-contact	Up About an hour
rss-bridge	Up About an hour
systemd-corporatetraveldc-ep-advance	Up 25 minutes
systemd-corporatetraveldc-feed-db-integrity-check	Up 2 seconds
systemd-corporatetraveldc-tbfm-arrival-enrichment	Up 2 seconds
systemd-corporatetraveldc-board-sweep	Up 1 second
systemd-corporatetraveldc-personal-notes-import	Up 1 second

## Commits since the previous LIVE_STATE_CHECK
9ee6556 NOTAMs: CRANE is VIP only as a callsign (CRANE01/05/50); TFR priority by citing authority -- 91.141/91.143/99.7/49 USC 40103(b) = 5 everywhere, 91.137/91.145 = home 5 / monitor 4 / else 3; home + monitor zones are per-deployment settings with no code default (this deployment ZDC + ZNY,ZID,ZOB,ZLA,ZTL; public edition ships them empty); authority TFRs alerted nationwide
3fc2c66 Deploy follow-up: podman prune waits out a rollout/stack refresh and retries the in-use race; rollout health check reads BOARD_KEY directly (operator-owned file) instead of an approval that expires while mobile; sign-manifest leaves the manifest pair 0644 (mktemp 0600 broke ctdc-agent-llama's verify); verify-manifest stops its temp gpg-agent
c6921c0 Batch 2026-10-05: no secret on any command line (32 scripts: tokens via private fd, PG password via env); NTS cert refresh as a root installed unit (user unit needed interactive sudo); five day-keyed skills on the operational day; every first-party container on a scoped secrets file (52 quadlets, scan.py import-closure allowlists); LLAMA_BASE_URL replaces OLLAMA_BASE_URL (semantic --ask off the dead Ollama API); plan.sh --rename-account + board-signer-ctl rename; guardrails/ISO/README docs off the Allow tap
ac0ebb6 Wave 2 remainder + segmentation end state: plan.sh re-asserts repo group READ (it re-applied g+rwX every run) and drops systemd-journal; verify fails repo/.git/hooks write; fail2ban actions run root-installed copies via installed-check (installer deploys action/jail/filter, tests + reloads; live limit-req jail still carried the Aug-26 lockdown); drift check sees failed system units; U6 signed query + single-use signatures; human-signed approvals with a separate passphrase-protected key, deny-only phone pushes; council/arena convenes; create-only attributed shared workspace with per-account/per-task grants; llama council runner; migration 0069; pamphlet: read-only repos, draft never publish, never approve
ecfe4f2 Wave 2 remainder + segmentation end state: plan.sh re-asserts repo group READ (it re-applied g+rwX every run) and drops systemd-journal; verify fails repo/.git/hooks write; fail2ban actions run root-installed copies via installed-check (installer deploys action/jail/filter, tests + reloads; live limit-req jail still carried the Aug-26 lockdown); drift check sees failed system units; U6 signed query + single-use signatures; human-signed approvals with a separate passphrase-protected key, deny-only phone pushes; council/arena convenes; create-only attributed shared workspace with per-account/per-task grants; llama council runner; migration 0069; pamphlet: read-only repos, draft never publish, never approve
bb333f7 Skill grants: list directories through a fresh fd (btrfs fixes readdir at open; first real apply hashed every copy as empty), one bad skill is a finding not an abort, apply suite also runs on btrfs; watchdog drop-in renamed zz-ctdc-watchdog.conf so it sorts after the stock watchdog.conf (90- never took effect); cert renew uses reload-or-restart and starts a down nginx (19:54 boot race left nginx failed); contract tests
cd5977c Root boundary + skill grants: root-run scripts execute from root-owned /usr/local/libexec/ctdc (install-root-copies.sh verifies the signed manifest first); trust pin under /etc parsed not sourced; watchdog/cert-renew no source-as-root; kill orders staged root-only in /var/lib/ctdc-liveness (outside every container mount -- fixes the 20:05 exit-126 incident) with symlink/owner/squat/size/count checks; registry-unavailable holds; per-agent/per-task skill grants with clawback (signed + hash-pinned vendor + project skills, root-owned copies, quarantine, tamper restore, settings merged as the agent), pamphlet "Your skills"; guardian uses the account's own key and never generates one; watchdog 180s diagnostic + stall monitor + one-shot tune 2026-10-11; verifier scoped env
6d02bf9 Duel fix wave 1: approval gate needs a push-only per-request key (0068) and agent/ops subsets drop admin/board/ntfy/nextcloud-admin creds; rollout deploys the gated image (FROM_REFRESH), postgres canary-only, :previous on change only, built-ids audit trust, 4h deadline; rolling guard reads the current weekday, dispatcher tracks outcomes; optime helper + naive-date guard; per-service env allowlists + verifier pilot; airline zero-strip, plan.sh fail-fast; codex account renamed ctdc-agent-openai-codex
1de33fa Account kinds (agent/claude, agent/ssh, service, preloaded) with /etc/ctdc-accounts.conf; plan.sh --add-service/--activate/--rehome-inbound-key/--ssh-pubkey-file; per-kind liveness; kind-aware verify/rollback/pamphlet; team segmentation showcase with 15 reviewed screenshots; redact-screenshot.py (noise under mosaic); board-sign.sh prints HTTP status
7a1a521 with-dispatch-env: fall back to the account's own secrets subset when the production files are unreadable (unit-started agent sessions have no profile export); BOARD_SIGNING.md: inbound vs signing identity
0a16209 Segmentation: inbound vs signing identity -- the first agent inherits the cowork pubkey for ssh but generates its own on-box ctdc-agent_ed25519 and registers THAT as its board signer (cowork private half is off-box, so it had nothing to sign with); every agent gets its own named remote-control unit
aedfc66 verify.sh: manager queries escalate (runuser needs root); 6c tests repo readability directly
be60fa3 render-onboarding: signer lookup root-safe and non-fatal (stage C ran silent on it)
4c9603c Segmentation tooling: agent user manager via runuser + runtime dir (not the machine transport); pamphlet render root-safe; dry-run test scoped to artefacts that must never exist
0519765 Segmentation tooling: reach the agent's user manager via runuser + its runtime dir instead of the machine transport (refuses while logind reports the user closing); dry-run test scoped to artefacts that must never exist
f2025f9 Checkpoint: segmentation stages A+B done; team-liveness first-contact fixes (new-agent grace, signer hook as operator under root, passwordless lock tolerated, unprivileged verdict labelled); onboarding pamphlet rendered per account into its home (static mirror; WorkingDirectory stays the home)
f2e9af6 stack-refresh: final clean-tree check before promotion (dirty = discard check tags, all held), :previous moves only on a real change, our own builds are pending-restart not tampering; rollout list gains ccw-demo/ccw-preview1 and service= labels
17cc7eb personal-export-analysis: longitudinal store + vault conventions (Uber exports CURRENT/history, pointers)
d5bad73 stack-refresh: a rollout refusal at hand-off is 'all held for review' not 'left behind'; StartedAt via .Unix; export skill keeps the voice profile's CURRENT.md mirror fresh
3ef6290 stack-refresh: restart every container every run (StartedAt as audit signal); explicit 'held for review, no restart' with persistent review debt; left-behind post-check
8fd6112 stack-refresh: restart every container every run (StartedAt as audit signal); explicit 'held for review, no restart' with persistent review debt; left-behind post-check
058b369 Key convention <account>_ed25519 for generated keys and signing clients; stack-refresh classifies running==:previous as pending-restart (warn + force rollout) not tampering; serialized-rollout restarts externals running behind their tag
3d32bba Authorization model: liveness is an AND (login, active key, no token revocation, no kill order); board tiers key/normal/high/cosign with role+kind on signers (0067); signed file-based kill orders with quorum (one admin or two non-admins; admins need quorum; operator never a target); path-triggered liveness unit
65730ad Team liveness dead-man switch (agents: refresh-token + 7d access-token refresh; humans: shell/key/lock/idle rules; inert = terminate+lock+revoke, reactivate explicit); board signing by account identity (SSH ed25519 over canonical message, board_signers registry, either/both policy, X-Board-Key unchanged); serialized-rollout refuses a dirty tree
51f3698 First stack-refresh audit: track three live-only quadlets (execstandard-verifier, ccw-demo, ccw-preview1), verifier joins the rollout list, audit compares image IDs not digest representations
b8b02b3 Standing practice + team segmentation: stack-refresh weekly and unclockable adversarial tripwire (quiet-window draw seeded by the 30-day load profile, 36h floor, 7d ceiling, monthly new window); rolling maintenance windows + queue dispatcher for 9 long-runners; quiet-window profile; guard stops shell-sourcing dispatch.env; serialized-rollout :previous + SKIP_UNITS; segmentation generalised to humans + agents with one SSH key per account
f132ff0 Swarm 2: tbfm-arrival-enrichment root cause (psycopg3 executemany shim returned None since d34aa67; skill now fails loudly), 15 long-runner quadlets get bounded lock waits + contract test, second-brain-daily keys its day-file on the ET operational day (poller image is UTC), dispatch-panel parity doc; CLAUDE.md post Gate-2 reset
97b9075 Swarm close-out: 2A twin gate (skills tracked + sync + hard drift check), ctdc-agent segmentation tooling, self-verifying public manifest, second-brain-daily bounded lock wait (root cause: unbounded flock on the shared long-runner lock), ACARS IATA/ICAO + tail matching, test suite 549/0, cifp_lookup + PG_TABLES fixes; CLAUDE.md consolidated
d9c2e24 ACARS/VDL2 night: local_airspace reads the router's VDL2 stream (acars_messages had never held a real row); airframes VDL2 credited for the first time -- router TCP 5553 with AR_ADD_PROXY_ID=false, direct dumpvdl2 output overlay kept off; prod contaminant sweep; CLAUDE.md
c2b381e local_airspace: read the router's VDL2 stream (:15555) -- acars_messages had never held a real row (C-8 root cause); router quadlet publishes 15555; dumpvdl2 frame normalizer + live-frame test; CLAUDE.md: prod contaminant sweep results
c19c7c5 with-dispatch-env: verbatim env loader; pg-migrate stops shell-sourcing secrets (fragment leak); restore labels idle REST fallbacks; CLAUDE.md: nws_alerts fixture row, push:fns never-connected, NWWS rotation
51be8cc context-guardian: restore script read key names the API never returned (every field printed as ?); hook logs each run, 90s save timeout, 4M transcript tail
2bcce06 CLAUDE.md: record 2026-10-03 deploy reconcile and the heredoc-only rule
224f6a0 context-guardian: read live context size from the transcript tail; Stop payload carries no usage block (hook silent since 2026-08-16)
4f7330f Exposure push: test/prod isolation, agent segmentation, integrity coverage, guard resume bar, fail2ban token path, docs
e4ce37c Retire the public executivestandard hostname from the Pi: tunnel ingress and nginx vhost
0ea0f06 cf-dns-record.sh: fix inline-python quoting in record printers and the proxied boolean in the JSON body (caught on first --show; no DNS change was made)
946b939 Add cf-dns-record.sh: upsert/delete one Cloudflare DNS record with the local management token
91e40ce Executive Standard: canonical articles/ source; members host becomes the Pi-hosted site's canonical URL
75dee10 Feeder names -> CS-KDCA-{MODE}; drop router station override; dual-port airframes VDL2 diagnostic; retire duplicate Quadlets

## Load / thermal (most recent samples)
2026-10-05 08:50:00,66.7,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",3441,69,17.38,14.83
2026-10-05 08:55:00,64.5,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",3439,69,18.35,16.51
2026-10-05 09:00:00,65.0,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",2329,49,17.19,17.12
```

</details>
