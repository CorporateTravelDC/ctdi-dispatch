# Live State Check — 2026-10-12

_Findings are gathered deterministically; narratives are written
by the local model (no cloud, no credits) and summarize those
findings only — the raw facts are authoritative._

## Weekly drift check — 09:00 UTC

_Narrative synthesis unavailable (model call failed, timed out, or slot unavailable). Raw deterministic findings below._

```
## Deterministic drift checks (scripts/check-claude-md-drift.sh)
[OK] no retired terms in CLAUDE.md
[OK] no hardcoded unit counts
[OK] model count matches (21)
[OK] single model base: phi3:mini 
[OK] all Modelfiles covered by scrub_tree()'s corporatetraveldc. pattern rule
[DRIFT] unit corporatetraveldc-codeshare-seed is failed/crash-looping and is absent from CLAUDE.md's Known bad section
[OK] no failed or crash-looping system units
[OK] Known bad section is 2 days old
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.service matches tracked .config/systemd/user/corporatetraveldc-governor-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.timer matches tracked .config/systemd/user/corporatetraveldc-governor-watch.timer
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-retrofit-links.timer matches tracked .config/systemd/user/corporatetraveldc-retrofit-links.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.service matches tracked .config/systemd/user/corporatetraveldc-podman-prune.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.timer matches tracked .config/systemd/user/corporatetraveldc-podman-prune.timer
[OK] /home/corporatetraveldc/.config/systemd/user/agents.slice matches tracked .config/systemd/user/agents.slice
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.service matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.timer matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.service matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.timer matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.timer
[WARN] tracked .config/systemd/user/humans.slice has no live installed copy at /home/corporatetraveldc/.config/systemd/user/humans.slice -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-codeshare-seed.service matches tracked .config/systemd/user/corporatetraveldc-codeshare-seed.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-codeshare-seed.timer matches tracked .config/systemd/user/corporatetraveldc-codeshare-seed.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-pull-path-verify.timer matches tracked .config/systemd/user/corporatetraveldc-pull-path-verify.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dependency-audit.service matches tracked .config/systemd/user/corporatetraveldc-dependency-audit.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dependency-audit.timer matches tracked .config/systemd/user/corporatetraveldc-dependency-audit.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama.service matches tracked .config/systemd/user/corporatetraveldc-llama.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-airline-time-fidelity.service matches tracked .config/systemd/user/corporatetraveldc-airline-time-fidelity.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-airline-time-fidelity.timer matches tracked .config/systemd/user/corporatetraveldc-airline-time-fidelity.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-claude-weekly-llama-fallback.timer matches tracked .config/systemd/user/corporatetraveldc-claude-weekly-llama-fallback.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-anomaly-watch.service matches tracked .config/systemd/user/corporatetraveldc-dns-anomaly-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-anomaly-watch.timer matches tracked .config/systemd/user/corporatetraveldc-dns-anomaly-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-agent-timecard.service matches tracked .config/systemd/user/corporatetraveldc-agent-timecard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-agent-timecard.timer matches tracked .config/systemd/user/corporatetraveldc-agent-timecard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-freshness.service matches tracked .config/systemd/user/corporatetraveldc-backup-freshness.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-freshness.timer matches tracked .config/systemd/user/corporatetraveldc-backup-freshness.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-restore-test.service matches tracked .config/systemd/user/corporatetraveldc-backup-restore-test.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-restore-test.timer matches tracked .config/systemd/user/corporatetraveldc-backup-restore-test.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup.service matches tracked .config/systemd/user/corporatetraveldc-backup.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup.timer matches tracked .config/systemd/user/corporatetraveldc-backup.timer
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
[OK] /home/corporatetraveldc/.config/containers/systemd/openwebui.container matches tracked .config/containers/systemd/openwebui.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-claude-weekly-llama-fallback.container matches tracked .config/containers/systemd/corporatetraveldc-claude-weekly-llama-fallback.container
[OK] /home/corporatetraveldc/.cloudflared/config.yml matches tracked cloudflared/config.yml
[OK] skills: 9 tracked, 0 divergent, 0 missing-live, 0 untracked-live (vendor dirs ignored via skills-vendor-ignore.txt)
--
[FAIL] drift found -- reconcile CLAUDE.md above, then re-run

## Failed / crash-looping units
corporatetraveldc-codeshare-seed.service
(empty above means none)

## Running containers
corporatetraveldc-pgsql	Up 5 days (healthy)
nextcloud-db	Up 5 days
systemd-corporatetraveldc-ingest-notam	Up 5 hours
systemd-corporatetraveldc-ingest-itws	Up 5 hours
systemd-corporatetraveldc-ingest-tbfm	Up 5 hours
systemd-corporatetraveldc-ingest-tfms	Up 5 hours
systemd-corporatetraveldc-ingest-stdds	Up 5 hours
systemd-corporatetraveldc-ingest-fdps	Up 5 hours
systemd-corporatetraveldc-ingest-core	Up 5 hours
systemd-corporatetraveldc-poller	Up 4 hours
systemd-corporatetraveldc-execstandard-verifier	Up 4 hours
systemd-corporatetraveldc-pusher	Up 4 hours
systemd-corporatetraveldc-web	Up 4 hours
systemd-corporatetraveldc-runner	Up 4 hours
systemd-corporatetraveldc-runner-demo	Up 4 hours
systemd-corporatetraveldc-demo-api	Up 4 hours
systemd-corporatetraveldc-demo	Up 4 hours
systemd-amtrak-tracker	Up 4 hours
corporatetraveldc-acars-watcher	Up 4 hours
csexec-contact	Up 4 hours
nextcloud-app	Up 4 hours
ntfy	Up 4 hours
rss-bridge	Up 4 hours
openwebui	Up 4 hours
systemd-corporatetraveldc-protonbridge	Up 4 hours
corporatetraveldc-ultrafeeder	Up 4 hours
corporatetraveldc-piaware	Up 4 hours
corporatetraveldc-fr24feed	Up 4 hours
corporatetraveldc-planefinder	Up 4 hours
corporatetraveldc-airnavradar	Up 4 hours
corporatetraveldc-dumpvdl2	Up 4 hours
corporatetraveldc-acarsrouter	Up 4 hours
corporatetraveldc-acarshub	Up 4 hours
corporatetraveldc-ccw-demo	Up 4 hours
corporatetraveldc-ccw-preview1	Up 4 hours
systemd-corporatetraveldc-feed-db-integrity-check	Up 2 seconds
systemd-corporatetraveldc-tbfm-arrival-enrichment	Up 2 seconds
systemd-corporatetraveldc-board-sweep	Up 2 seconds

## Commits since the previous LIVE_STATE_CHECK
ddac65e7 Approvals: per-kind, content-derived expiry (councils 10% of deadline, 30m-4h; close 25% of remaining, 15m-2h; connector-hold/gateway-thaw 30m, was 24h/1h; env overrides clamped); requester-only withdraw (signed route + approve.sh withdraw, audited); councils follow an expired/withdrawn/denied approval (c90f70d9768); council push shows the real expiry. Console sign-in: push on every request and re-tap (what/why/effects/device/expiry UTC+ET); a re-tap while pending reuses the browser's request but is its own audit row (console.login.retap) and push; Cancel withdraws it. Docs: audit + notify fan-out to admin/SOC/IT as a silent tripwire (design note, not wired). Load stamps: test sessions, failed/timed-out/slow tests and relays record load, PSI and top CPU commands to a private ledger (load_stamp.py summary). Backup: + /var/lib/vnstat. llama-fallback: bounded flock wait (-w 1800, exit 75 = skip). watchdog-tune: optional ntfy config reads no longer fatal under pipefail (unit failed 2026-10-12 00:00Z after a successful tune)
89120420 Backup: pass Borg stats via the environment (the heredoc was stdin, so the piped JSON was silently dropped and the parse failed after a complete archive); rm instead of shred for stage/restore temp dirs (shred is ineffective on btrfs copy-on-write and cost ~15 min per run)
93246cea Backup: exclude the root-only cf-honeypot token; treat Borg exit 1 as a recorded warning (archive written) instead of aborting -- first run stopped there after a complete archive
68c67028 Backups live: daily encrypted Borg backup, weekly restore test, 6-hourly freshness (now incl. agent snapshots); root daily snapshot of EVERY agent home (credentials excluded, installed root copy) as default agent behaviour; daily agent time cards (shift / work / exceptions / custody, from the audit log; vault card + ledger hash); BACKUP.md + tests
57da7a05 relay-nudge.py (phone push before every interactive relay step, and on exit; token only in the header, never fails the relay) + tests; approve.sh show/allow explains every approval kind in plain language (what / why / approve-effect / deny-effect / requested-expires in UTC+ET) before the exact signed content (operator rule 2026-10-11)
7b62a05e DNS anomaly watch generalized (renamed from uber-traffic-watch -- the renames themselves were swept into 076e3910 under its audit-only message, and its manifest missed 7 files; this commit's manifest covers everything): state migrated, full CNAME-chain following fixes the tb-static false positive, one readable line per domain, config-driven labels, neutral tags + tests; deployment composition skeleton (common.composition + disabled EMCOMM/hazard example) + tests
3d030992 DNS anomaly watch generalized (renamed from uber-traffic-watch -- the renames themselves were swept into 076e3910 under its audit-only message, and its manifest missed 7 files; this commit's manifest covers everything): state migrated, full CNAME-chain following fixes the tb-static false positive, one readable line per domain, config-driven labels, neutral tags + tests; deployment composition skeleton (common.composition + disabled EMCOMM/hazard example) + tests
076e3910 Audit every authorised signed write (board.signed.request: route, signer, policy, body and signature SHA-256 only) -- signed workspace contributions and council requests were the one write path with no audit row; regression test
b2a97645 Demo admin token rotation (old name carried the live admin bearer into web/poller/recorder; demo-api now has a dedicated value); llama-fallback merged (overnight Llama backstop for second-brain-weekly-dump, scoped secrets, never performs the destructive reset); EMCOMM/hazard composition spec (staged, disabled); ops/session removed; CLAUDE.md backlog + Known bad
74af669d docs: GUARDRAILS_PROVEN gate chain -- every check between an edit and the public mirror (pre-commit, signing/drift gate, pre-push, push-public, deploy provenance) with real scrollback from the 2026-10-09 relays, sanitized
a9ab1753 docs: GUARDRAILS_PROVEN -- transcript leak guard proven on the day (redaction proof, push refused, clean private-then-public push); sanitized screenshots (session id blacked out), hashes in the private backup ledger
75d928b0 LADD tripwire: operator-confirmed coincidence allowlist (20 hashes, no tokens); push-public tripwire no longer consumes the caller's stdin (it swallowed the rest of a heredoc relay)
b763878c Leak guard + backups + import ledger: transcript leak scan/redact wired into pre-push and push-public; Borg (AES-256/HMAC-SHA256) backups with parameterized rclone targets and a hash-chained private ledger (LADD stays on-box, '#'-stamped); reference_import_ledger (0074) stamped by LADD imports (--stamp-only identity check) and the FAA registry fetch; push-time LADD tripwire (exit 7, locations only); quoted LADD identifiers removed from a doc and a docstring
672e1d52 Deployment profiles: typed loader + geography provider (common.deployment_profile); generalized reference profile as the known-good default (reproduces DC behaviour, pinned by tests) + private live profile (dropped from the public mirror); schema notes resolved (open maps, host platform, typed airports, strict validator); METAR stations from the profile and any ICAO station parses
1194aae1 Security review 09: adversarial validation of provenance, deployment identity, agent authority and audit integrity (record + 12 agent-authority tests)
9d5736a2 Record: 72eddf54 carries the airplanes.live FALLBACK-ONLY lookup (src/common/airplanes_live.py, shared/watchlist.py wiring, tests, AIRPLANES_LIVE_FALLBACK config) -- it was committed by a re-run of the deployment-profiles staging relay, so its message is wrong; daily drift report
13099786 Stage parameterized deployment profiles (multi-vertical repurposing, design-stage, not wired): base + 8 verticals, schema, validator, as received; STAGED.md with provenance and review notes
72eddf54 Stage parameterized deployment profiles (multi-vertical repurposing, design-stage, not wired): base + 8 verticals, schema, validator, as received; STAGED.md with provenance and review notes
c279dd45 Stage parameterized deployment profiles (multi-vertical repurposing, design-stage, not wired): base + 8 verticals, schema, validator, as received; STAGED.md with provenance and review notes
f7598039 Research monitor: rolling 30/60/90-day windows (never stops), scope adds 14-day watched flights/aircraft + their routes + BOS/LGA-DCA shuttle pairs; public spec anchors-only, private ledger (bellwethers, watched flights, codeshare breakdown) under docs/research/private dropped from the public mirror; spec dates corrected (struck through)
3366743e Research: airline time-fidelity spec + read-only collector (airline-reported OOOI vs FDPS, ASDE-X surface, TDES, TBFM, our watches) for DCA/IAD/BWI and the UAL1279/UAL2344 bellwethers; 6-hourly timer through Monday 2026-10-12
2d8fcc91 Security review 08: live credentials in the public mirror's history (June secrets files, values in old scrubber versions); history rewritten; leaked admin token removed from three task docs; operator remote-control session is the platform default
703e6e60 Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
2d800f2d Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
87de70f5 Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
833fcabf Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
9bbd513b Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
92231b1d ChatGPT: read/write tool set like Cowork, the vendor plan decides (operator); plan.sh copies the whole Codex standalone package, verified per file (the binary alone does not run)
553ffb58 Codex agent onboarding: login mode codex (liveness by ~/.codex/auth.json refresh + its own remote-control unit); plan.sh --activate/--add-agent --login-mode codex installs the Codex CLI from a pinned digest (no download) and its own unit, removes the Claude unit; pamphlet as AGENTS.md; verify.sh codex checks; ChatGPT documented as read-only reviewer
8b5be869 Security review 06: builds pin the host platform and receipts refuse a foreign architecture (amd64 outage 2026-10-08); build-images.sh runner records provenance; LADD lookup fixed, public LADD download retired; FDPS lookup for registration callsigns; synthetic Q registry made the default (real tails and hexes replaced, live watchlist dropped from the public mirror, leak gate + guard test); agent_gateway escape fix
3c9f2965 Security review 05: build inputs + provenance. Hash-locked Python for every image (intent *.in, universal locks), base images by multi-arch digest, apt from a dated Debian snapshot, SBOM + provenance receipt per build, rollout deploy gate, sweep verification of running ids and tags, llama artifact digests checked at start. Fix: .gitignore hid build/ from the signed manifest. Level 1-2 reached, Level 3 after rebuild, Level 4 not claimed (docs/REPRODUCIBLE_BUILDS.md)
7ef48354 Security review 05: build inputs + provenance. Hash-locked Python for every image (intent *.in, universal locks), base images by multi-arch digest, apt from a dated Debian snapshot, SBOM + provenance receipt per build, rollout deploy gate, sweep verification of running ids and tags, llama artifact digests checked at start. Fix: .gitignore hid build/ from the signed manifest. Level 1-2 reached, Level 3 after rebuild, Level 4 not claimed (docs/REPRODUCIBLE_BUILDS.md)
320ce637 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
9f1103e1 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
4b3f37a5 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
718b1e55 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
7134bd4e Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
dacf11b7 Dependency scanning: pre-push + daily audit of every repo, its worktrees, running AND dormant images (rollback/test/debug tags); Dependabot alerts on all 12 repos. Rule: disabled or undeployed code is not exempt -- ais/utm watchers pinned requests 2.34.2 / urllib3 2.8.0. Security review 04
912a3472 Dependencies: runner frontend lockfile fixes the 5 Dependabot alerts (source-map-js, brace-expansion x2, fast-uri) and the build uses npm ci; acars-watcher pins requests 2.34.2 / urllib3 2.8.0 (5 advisories). Security review 03: hardening verified live, dependency pass incl. 14 contact-API advisories in the site repo; trust model 9.1 verified
8d509d10 Hardening + security review chain. Root approved-exec (signature re-verified against a root-owned pin, allowlist, single use, timing) replaces passwordless gated sudo; approval TTL caps per kind; requester bound to the token label; canonical v2 signs pattern and reason; console redemption bounded server-side; governance events in the hash-chained audit_log, verified every integrity sweep. Docs: single-operator origin and the operator-as-agent designed exception. Canonical vs interstitial: docs/security-reviews/ (chain index, review 01 trust-model pass + snapshot, review 02 hardening + snapshot); canonical security docs point at the trail

## Load / thermal (most recent samples)
2026-10-12T08:50:01Z,64.5,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",2333,49,17.34,13.64
2026-10-12T08:55:00Z,65.0,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",2331,49,13.37,14.34
2026-10-12T09:00:08Z,61.7,Ssl,"qwen3-4b-instruct-2507-q4_0.gguf|",2335,49,9.14,12.64
```

<details><summary>Raw deterministic findings</summary>

```
## Deterministic drift checks (scripts/check-claude-md-drift.sh)
[OK] no retired terms in CLAUDE.md
[OK] no hardcoded unit counts
[OK] model count matches (21)
[OK] single model base: phi3:mini 
[OK] all Modelfiles covered by scrub_tree()'s corporatetraveldc. pattern rule
[DRIFT] unit corporatetraveldc-codeshare-seed is failed/crash-looping and is absent from CLAUDE.md's Known bad section
[OK] no failed or crash-looping system units
[OK] Known bad section is 2 days old
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.service matches tracked .config/systemd/user/corporatetraveldc-governor-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.timer matches tracked .config/systemd/user/corporatetraveldc-governor-watch.timer
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-retrofit-links.timer matches tracked .config/systemd/user/corporatetraveldc-retrofit-links.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.service matches tracked .config/systemd/user/corporatetraveldc-podman-prune.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-podman-prune.timer matches tracked .config/systemd/user/corporatetraveldc-podman-prune.timer
[OK] /home/corporatetraveldc/.config/systemd/user/agents.slice matches tracked .config/systemd/user/agents.slice
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer matches tracked .config/systemd/user/corporatetraveldc-stack-refresh-tripwire.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.service matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-quiet-window-report.timer matches tracked .config/systemd/user/corporatetraveldc-quiet-window-report.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.service matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-maintenance-dispatch.timer matches tracked .config/systemd/user/corporatetraveldc-maintenance-dispatch.timer
[WARN] tracked .config/systemd/user/humans.slice has no live installed copy at /home/corporatetraveldc/.config/systemd/user/humans.slice -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-codeshare-seed.service matches tracked .config/systemd/user/corporatetraveldc-codeshare-seed.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-codeshare-seed.timer matches tracked .config/systemd/user/corporatetraveldc-codeshare-seed.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-pull-path-verify.timer matches tracked .config/systemd/user/corporatetraveldc-pull-path-verify.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dependency-audit.service matches tracked .config/systemd/user/corporatetraveldc-dependency-audit.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dependency-audit.timer matches tracked .config/systemd/user/corporatetraveldc-dependency-audit.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama.service matches tracked .config/systemd/user/corporatetraveldc-llama.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-airline-time-fidelity.service matches tracked .config/systemd/user/corporatetraveldc-airline-time-fidelity.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-airline-time-fidelity.timer matches tracked .config/systemd/user/corporatetraveldc-airline-time-fidelity.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-claude-weekly-llama-fallback.timer matches tracked .config/systemd/user/corporatetraveldc-claude-weekly-llama-fallback.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-anomaly-watch.service matches tracked .config/systemd/user/corporatetraveldc-dns-anomaly-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-anomaly-watch.timer matches tracked .config/systemd/user/corporatetraveldc-dns-anomaly-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-agent-timecard.service matches tracked .config/systemd/user/corporatetraveldc-agent-timecard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-agent-timecard.timer matches tracked .config/systemd/user/corporatetraveldc-agent-timecard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-freshness.service matches tracked .config/systemd/user/corporatetraveldc-backup-freshness.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-freshness.timer matches tracked .config/systemd/user/corporatetraveldc-backup-freshness.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-restore-test.service matches tracked .config/systemd/user/corporatetraveldc-backup-restore-test.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup-restore-test.timer matches tracked .config/systemd/user/corporatetraveldc-backup-restore-test.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup.service matches tracked .config/systemd/user/corporatetraveldc-backup.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-backup.timer matches tracked .config/systemd/user/corporatetraveldc-backup.timer
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
[OK] /home/corporatetraveldc/.config/containers/systemd/openwebui.container matches tracked .config/containers/systemd/openwebui.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-claude-weekly-llama-fallback.container matches tracked .config/containers/systemd/corporatetraveldc-claude-weekly-llama-fallback.container
[OK] /home/corporatetraveldc/.cloudflared/config.yml matches tracked cloudflared/config.yml
[OK] skills: 9 tracked, 0 divergent, 0 missing-live, 0 untracked-live (vendor dirs ignored via skills-vendor-ignore.txt)
--
[FAIL] drift found -- reconcile CLAUDE.md above, then re-run

## Failed / crash-looping units
corporatetraveldc-codeshare-seed.service
(empty above means none)

## Running containers
corporatetraveldc-pgsql	Up 5 days (healthy)
nextcloud-db	Up 5 days
systemd-corporatetraveldc-ingest-notam	Up 5 hours
systemd-corporatetraveldc-ingest-itws	Up 5 hours
systemd-corporatetraveldc-ingest-tbfm	Up 5 hours
systemd-corporatetraveldc-ingest-tfms	Up 5 hours
systemd-corporatetraveldc-ingest-stdds	Up 5 hours
systemd-corporatetraveldc-ingest-fdps	Up 5 hours
systemd-corporatetraveldc-ingest-core	Up 5 hours
systemd-corporatetraveldc-poller	Up 4 hours
systemd-corporatetraveldc-execstandard-verifier	Up 4 hours
systemd-corporatetraveldc-pusher	Up 4 hours
systemd-corporatetraveldc-web	Up 4 hours
systemd-corporatetraveldc-runner	Up 4 hours
systemd-corporatetraveldc-runner-demo	Up 4 hours
systemd-corporatetraveldc-demo-api	Up 4 hours
systemd-corporatetraveldc-demo	Up 4 hours
systemd-amtrak-tracker	Up 4 hours
corporatetraveldc-acars-watcher	Up 4 hours
csexec-contact	Up 4 hours
nextcloud-app	Up 4 hours
ntfy	Up 4 hours
rss-bridge	Up 4 hours
openwebui	Up 4 hours
systemd-corporatetraveldc-protonbridge	Up 4 hours
corporatetraveldc-ultrafeeder	Up 4 hours
corporatetraveldc-piaware	Up 4 hours
corporatetraveldc-fr24feed	Up 4 hours
corporatetraveldc-planefinder	Up 4 hours
corporatetraveldc-airnavradar	Up 4 hours
corporatetraveldc-dumpvdl2	Up 4 hours
corporatetraveldc-acarsrouter	Up 4 hours
corporatetraveldc-acarshub	Up 4 hours
corporatetraveldc-ccw-demo	Up 4 hours
corporatetraveldc-ccw-preview1	Up 4 hours
systemd-corporatetraveldc-feed-db-integrity-check	Up 2 seconds
systemd-corporatetraveldc-tbfm-arrival-enrichment	Up 2 seconds
systemd-corporatetraveldc-board-sweep	Up 2 seconds

## Commits since the previous LIVE_STATE_CHECK
ddac65e7 Approvals: per-kind, content-derived expiry (councils 10% of deadline, 30m-4h; close 25% of remaining, 15m-2h; connector-hold/gateway-thaw 30m, was 24h/1h; env overrides clamped); requester-only withdraw (signed route + approve.sh withdraw, audited); councils follow an expired/withdrawn/denied approval (c90f70d9768); council push shows the real expiry. Console sign-in: push on every request and re-tap (what/why/effects/device/expiry UTC+ET); a re-tap while pending reuses the browser's request but is its own audit row (console.login.retap) and push; Cancel withdraws it. Docs: audit + notify fan-out to admin/SOC/IT as a silent tripwire (design note, not wired). Load stamps: test sessions, failed/timed-out/slow tests and relays record load, PSI and top CPU commands to a private ledger (load_stamp.py summary). Backup: + /var/lib/vnstat. llama-fallback: bounded flock wait (-w 1800, exit 75 = skip). watchdog-tune: optional ntfy config reads no longer fatal under pipefail (unit failed 2026-10-12 00:00Z after a successful tune)
89120420 Backup: pass Borg stats via the environment (the heredoc was stdin, so the piped JSON was silently dropped and the parse failed after a complete archive); rm instead of shred for stage/restore temp dirs (shred is ineffective on btrfs copy-on-write and cost ~15 min per run)
93246cea Backup: exclude the root-only cf-honeypot token; treat Borg exit 1 as a recorded warning (archive written) instead of aborting -- first run stopped there after a complete archive
68c67028 Backups live: daily encrypted Borg backup, weekly restore test, 6-hourly freshness (now incl. agent snapshots); root daily snapshot of EVERY agent home (credentials excluded, installed root copy) as default agent behaviour; daily agent time cards (shift / work / exceptions / custody, from the audit log; vault card + ledger hash); BACKUP.md + tests
57da7a05 relay-nudge.py (phone push before every interactive relay step, and on exit; token only in the header, never fails the relay) + tests; approve.sh show/allow explains every approval kind in plain language (what / why / approve-effect / deny-effect / requested-expires in UTC+ET) before the exact signed content (operator rule 2026-10-11)
7b62a05e DNS anomaly watch generalized (renamed from uber-traffic-watch -- the renames themselves were swept into 076e3910 under its audit-only message, and its manifest missed 7 files; this commit's manifest covers everything): state migrated, full CNAME-chain following fixes the tb-static false positive, one readable line per domain, config-driven labels, neutral tags + tests; deployment composition skeleton (common.composition + disabled EMCOMM/hazard example) + tests
3d030992 DNS anomaly watch generalized (renamed from uber-traffic-watch -- the renames themselves were swept into 076e3910 under its audit-only message, and its manifest missed 7 files; this commit's manifest covers everything): state migrated, full CNAME-chain following fixes the tb-static false positive, one readable line per domain, config-driven labels, neutral tags + tests; deployment composition skeleton (common.composition + disabled EMCOMM/hazard example) + tests
076e3910 Audit every authorised signed write (board.signed.request: route, signer, policy, body and signature SHA-256 only) -- signed workspace contributions and council requests were the one write path with no audit row; regression test
b2a97645 Demo admin token rotation (old name carried the live admin bearer into web/poller/recorder; demo-api now has a dedicated value); llama-fallback merged (overnight Llama backstop for second-brain-weekly-dump, scoped secrets, never performs the destructive reset); EMCOMM/hazard composition spec (staged, disabled); ops/session removed; CLAUDE.md backlog + Known bad
74af669d docs: GUARDRAILS_PROVEN gate chain -- every check between an edit and the public mirror (pre-commit, signing/drift gate, pre-push, push-public, deploy provenance) with real scrollback from the 2026-10-09 relays, sanitized
a9ab1753 docs: GUARDRAILS_PROVEN -- transcript leak guard proven on the day (redaction proof, push refused, clean private-then-public push); sanitized screenshots (session id blacked out), hashes in the private backup ledger
75d928b0 LADD tripwire: operator-confirmed coincidence allowlist (20 hashes, no tokens); push-public tripwire no longer consumes the caller's stdin (it swallowed the rest of a heredoc relay)
b763878c Leak guard + backups + import ledger: transcript leak scan/redact wired into pre-push and push-public; Borg (AES-256/HMAC-SHA256) backups with parameterized rclone targets and a hash-chained private ledger (LADD stays on-box, '#'-stamped); reference_import_ledger (0074) stamped by LADD imports (--stamp-only identity check) and the FAA registry fetch; push-time LADD tripwire (exit 7, locations only); quoted LADD identifiers removed from a doc and a docstring
672e1d52 Deployment profiles: typed loader + geography provider (common.deployment_profile); generalized reference profile as the known-good default (reproduces DC behaviour, pinned by tests) + private live profile (dropped from the public mirror); schema notes resolved (open maps, host platform, typed airports, strict validator); METAR stations from the profile and any ICAO station parses
1194aae1 Security review 09: adversarial validation of provenance, deployment identity, agent authority and audit integrity (record + 12 agent-authority tests)
9d5736a2 Record: 72eddf54 carries the airplanes.live FALLBACK-ONLY lookup (src/common/airplanes_live.py, shared/watchlist.py wiring, tests, AIRPLANES_LIVE_FALLBACK config) -- it was committed by a re-run of the deployment-profiles staging relay, so its message is wrong; daily drift report
13099786 Stage parameterized deployment profiles (multi-vertical repurposing, design-stage, not wired): base + 8 verticals, schema, validator, as received; STAGED.md with provenance and review notes
72eddf54 Stage parameterized deployment profiles (multi-vertical repurposing, design-stage, not wired): base + 8 verticals, schema, validator, as received; STAGED.md with provenance and review notes
c279dd45 Stage parameterized deployment profiles (multi-vertical repurposing, design-stage, not wired): base + 8 verticals, schema, validator, as received; STAGED.md with provenance and review notes
f7598039 Research monitor: rolling 30/60/90-day windows (never stops), scope adds 14-day watched flights/aircraft + their routes + BOS/LGA-DCA shuttle pairs; public spec anchors-only, private ledger (bellwethers, watched flights, codeshare breakdown) under docs/research/private dropped from the public mirror; spec dates corrected (struck through)
3366743e Research: airline time-fidelity spec + read-only collector (airline-reported OOOI vs FDPS, ASDE-X surface, TDES, TBFM, our watches) for DCA/IAD/BWI and the UAL1279/UAL2344 bellwethers; 6-hourly timer through Monday 2026-10-12
2d8fcc91 Security review 08: live credentials in the public mirror's history (June secrets files, values in old scrubber versions); history rewritten; leaked admin token removed from three task docs; operator remote-control session is the platform default
703e6e60 Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
2d800f2d Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
87de70f5 Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
833fcabf Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
9bbd513b Security review 07: agents + adversarial receipt; gateway tool calls audited (agent.tool.call, args hashed); public history rewrite to the synthetic Q registry (F2); real hexes and foreign registrations left in tests replaced (F9); README re-verified against code and live state 2026-10-08; CLAUDE.md trimmed to the checkpoint
92231b1d ChatGPT: read/write tool set like Cowork, the vendor plan decides (operator); plan.sh copies the whole Codex standalone package, verified per file (the binary alone does not run)
553ffb58 Codex agent onboarding: login mode codex (liveness by ~/.codex/auth.json refresh + its own remote-control unit); plan.sh --activate/--add-agent --login-mode codex installs the Codex CLI from a pinned digest (no download) and its own unit, removes the Claude unit; pamphlet as AGENTS.md; verify.sh codex checks; ChatGPT documented as read-only reviewer
8b5be869 Security review 06: builds pin the host platform and receipts refuse a foreign architecture (amd64 outage 2026-10-08); build-images.sh runner records provenance; LADD lookup fixed, public LADD download retired; FDPS lookup for registration callsigns; synthetic Q registry made the default (real tails and hexes replaced, live watchlist dropped from the public mirror, leak gate + guard test); agent_gateway escape fix
3c9f2965 Security review 05: build inputs + provenance. Hash-locked Python for every image (intent *.in, universal locks), base images by multi-arch digest, apt from a dated Debian snapshot, SBOM + provenance receipt per build, rollout deploy gate, sweep verification of running ids and tags, llama artifact digests checked at start. Fix: .gitignore hid build/ from the signed manifest. Level 1-2 reached, Level 3 after rebuild, Level 4 not claimed (docs/REPRODUCIBLE_BUILDS.md)
7ef48354 Security review 05: build inputs + provenance. Hash-locked Python for every image (intent *.in, universal locks), base images by multi-arch digest, apt from a dated Debian snapshot, SBOM + provenance receipt per build, rollout deploy gate, sweep verification of running ids and tags, llama artifact digests checked at start. Fix: .gitignore hid build/ from the signed manifest. Level 1-2 reached, Level 3 after rebuild, Level 4 not claimed (docs/REPRODUCIBLE_BUILDS.md)
320ce637 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
9f1103e1 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
4b3f37a5 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
718b1e55 Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
7134bd4e Manifest re-signed: docs/INFRA_MAP.md landed after the dacf11b signature (edit raced the relay). Records the Ground News retirement: vendor has no API or feed; worktrees, branches and patch removed, work kept as local archive/ground-news-* tags
dacf11b7 Dependency scanning: pre-push + daily audit of every repo, its worktrees, running AND dormant images (rollback/test/debug tags); Dependabot alerts on all 12 repos. Rule: disabled or undeployed code is not exempt -- ais/utm watchers pinned requests 2.34.2 / urllib3 2.8.0. Security review 04
912a3472 Dependencies: runner frontend lockfile fixes the 5 Dependabot alerts (source-map-js, brace-expansion x2, fast-uri) and the build uses npm ci; acars-watcher pins requests 2.34.2 / urllib3 2.8.0 (5 advisories). Security review 03: hardening verified live, dependency pass incl. 14 contact-API advisories in the site repo; trust model 9.1 verified
8d509d10 Hardening + security review chain. Root approved-exec (signature re-verified against a root-owned pin, allowlist, single use, timing) replaces passwordless gated sudo; approval TTL caps per kind; requester bound to the token label; canonical v2 signs pattern and reason; console redemption bounded server-side; governance events in the hash-chained audit_log, verified every integrity sweep. Docs: single-operator origin and the operator-as-agent designed exception. Canonical vs interstitial: docs/security-reviews/ (chain index, review 01 trust-model pass + snapshot, review 02 hardening + snapshot); canonical security docs point at the trail

## Load / thermal (most recent samples)
2026-10-12T08:50:01Z,64.5,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",2333,49,17.34,13.64
2026-10-12T08:55:00Z,65.0,Rsl,"qwen3-4b-instruct-2507-q4_0.gguf|",2331,49,13.37,14.34
2026-10-12T09:00:08Z,61.7,Ssl,"qwen3-4b-instruct-2507-q4_0.gguf|",2335,49,9.14,12.64
```

</details>
