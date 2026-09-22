
## Post-commit check — 19:38 EDT (commit)

_Narrative synthesis unavailable (model call failed, timed out, or slot unavailable). Raw deterministic findings below._

```
## Trigger
reason=commit  commit=341be56 Add maintenance window enforcement, audit tamper-evidence, site-origin provenance, and bulk-migration timeout fixes

## Files changed in this commit
.config/containers/systemd/corporatetraveldc-aam-weekly-watch.container
.config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container
.config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container
.config/containers/systemd/corporatetraveldc-disruption-weather-digest.container
.config/containers/systemd/corporatetraveldc-second-brain-daily.container
.config/containers/systemd/corporatetraveldc-second-brain-weekly.container
.config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container
.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container
.config/containers/systemd/corporatetraveldc-weekly-summary.container
.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
CLAUDE.md
MANIFEST.sha256
MANIFEST.sha256.asc
config/dispatch.env
config/dispatch.env.example
docs/ANP_FEDERATION_RESEARCH_2026-09-21.md
scripts/maintenance-window-guard.sh
scripts/migrate-demo-to-pg.py
scripts/thermal-sample.sh
src/common/pg_schema/0061_site_origin.sql
src/common/pg_schema/0062_audit_tamper_evidence.sql

## Deterministic drift checks
[OK] no retired terms in CLAUDE.md
[OK] no hardcoded unit counts
[OK] model count matches (21)
[OK] single model base: phi3:mini 
[OK] all Modelfiles covered by scrub_tree()'s corporatetraveldc. pattern rule
[OK] Known bad section is 1 days old
[OK] no quoted env values in dispatch.env (podman --env-file safe)
[OK] manifest and signature both clean
[OK] API healthy at http://127.0.0.1:8000
[OK] .git/hooks/pre-commit matches scripts/pre-commit
[OK] .git/hooks/pre-push matches scripts/pre-push
[DRIFT] .git/hooks/post-commit does not match scripts/post-commit-doc-verify.sh -- re-run: cp scripts/post-commit-doc-verify.sh .git/hooks/post-commit && chmod +x .git/hooks/post-commit
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-transport-pattern-digest.timer matches tracked .config/systemd/user/corporatetraveldc-transport-pattern-digest.timer
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-disruption-weather-digest.timer matches tracked .config/systemd/user/corporatetraveldc-disruption-weather-digest.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-board-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-board-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.service matches tracked .config/systemd/user/corporatetraveldc-governor-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.timer matches tracked .config/systemd/user/corporatetraveldc-governor-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.service matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.timer matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.service matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.timer matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-aam-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-aam-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-aviation-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-aviation-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-gig-economy-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-gig-economy-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-concierge-travel-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-concierge-travel-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-trains-yachts-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-trains-yachts-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-executive-protection-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-executive-protection-daily-watch.timer
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ep-advance-venues.timer matches tracked .config/systemd/user/corporatetraveldc-ep-advance-venues.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.service matches tracked .config/systemd/user/corporatetraveldc-llama-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.timer matches tracked .config/systemd/user/corporatetraveldc-llama-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-boot-stagger.service
[WARN] tracked .config/systemd/user/corporatetraveldc-mcpo-public.service has no live installed copy at /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-mcpo-public.service -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-stack-boot-stagger.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-ingest-guard.service matches tracked .config/systemd/user/corporatetraveldc-thermal-ingest-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.service matches tracked .config/systemd/user/corporatetraveldc-dns-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.timer matches tracked .config/systemd/user/corporatetraveldc-dns-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nts-cert-refresh.service matches tracked .config/systemd/user/corporatetraveldc-nts-cert-refresh.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nts-cert-refresh.timer matches tracked .config/systemd/user/corporatetraveldc-nts-cert-refresh.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama.service matches tracked .config/systemd/user/corporatetraveldc-llama.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
[OK] /home/corporatetraveldc/.config/containers/systemd/acars-net.network matches tracked .config/containers/systemd/acars-net.network
[WARN] tracked .config/containers/systemd/amtrak-tracker.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/amtrak-tracker.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[WARN] tracked .config/containers/systemd/corporatetraveldc-acarsdec.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsdec.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/nextcloud-db.container matches tracked .config/containers/systemd/nextcloud-db.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dumpvdl2.container matches tracked .config/containers/systemd/corporatetraveldc-dumpvdl2.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsrouter.container matches tracked .config/containers/systemd/corporatetraveldc-acarsrouter.container
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
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ultrafeeder.container matches tracked .config/containers/systemd/corporatetraveldc-ultrafeeder.container
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
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container matches tracked .config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container
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
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-semantic-compile-daily.container matches tracked .config/containers/systemd/corporatetraveldc-semantic-compile-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-api.container matches tracked .config/containers/systemd/corporatetraveldc-demo-api.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo.container matches tracked .config/containers/systemd/corporatetraveldc-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-poller.container matches tracked .config/containers/systemd/corporatetraveldc-poller.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-web.container matches tracked .config/containers/systemd/corporatetraveldc-web.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-daily.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container matches tracked .config/containers/systemd/corporatetraveldc-transport-pattern-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aam-weekly-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aam-weekly-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container matches tracked .config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-weekly-summary.container matches tracked .config/containers/systemd/corporatetraveldc-weekly-summary.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-weekly.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-weekly.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-disruption-weather-digest.container matches tracked .config/containers/systemd/corporatetraveldc-disruption-weather-digest.container
--
[FAIL] drift found -- reconcile CLAUDE.md above, then re-run

## Failed / crash-looping units
corporatetraveldc-docs-drift-weekly.service
corporatetraveldc-integrity-sweep.service
(empty above means none)

## Prior second-brain findings on the touched area
query: corporatetraveldc-aam-weekly-watch
```

<details><summary>Raw deterministic findings</summary>

```
## Trigger
reason=commit  commit=341be56 Add maintenance window enforcement, audit tamper-evidence, site-origin provenance, and bulk-migration timeout fixes

## Files changed in this commit
.config/containers/systemd/corporatetraveldc-aam-weekly-watch.container
.config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container
.config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container
.config/containers/systemd/corporatetraveldc-disruption-weather-digest.container
.config/containers/systemd/corporatetraveldc-second-brain-daily.container
.config/containers/systemd/corporatetraveldc-second-brain-weekly.container
.config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container
.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container
.config/containers/systemd/corporatetraveldc-weekly-summary.container
.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
CLAUDE.md
MANIFEST.sha256
MANIFEST.sha256.asc
config/dispatch.env
config/dispatch.env.example
docs/ANP_FEDERATION_RESEARCH_2026-09-21.md
scripts/maintenance-window-guard.sh
scripts/migrate-demo-to-pg.py
scripts/thermal-sample.sh
src/common/pg_schema/0061_site_origin.sql
src/common/pg_schema/0062_audit_tamper_evidence.sql

## Deterministic drift checks
[OK] no retired terms in CLAUDE.md
[OK] no hardcoded unit counts
[OK] model count matches (21)
[OK] single model base: phi3:mini 
[OK] all Modelfiles covered by scrub_tree()'s corporatetraveldc. pattern rule
[OK] Known bad section is 1 days old
[OK] no quoted env values in dispatch.env (podman --env-file safe)
[OK] manifest and signature both clean
[OK] API healthy at http://127.0.0.1:8000
[OK] .git/hooks/pre-commit matches scripts/pre-commit
[OK] .git/hooks/pre-push matches scripts/pre-push
[DRIFT] .git/hooks/post-commit does not match scripts/post-commit-doc-verify.sh -- re-run: cp scripts/post-commit-doc-verify.sh .git/hooks/post-commit && chmod +x .git/hooks/post-commit
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-transport-pattern-digest.timer matches tracked .config/systemd/user/corporatetraveldc-transport-pattern-digest.timer
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-disruption-weather-digest.timer matches tracked .config/systemd/user/corporatetraveldc-disruption-weather-digest.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer matches tracked .config/systemd/user/corporatetraveldc-adsb-feed-silence-watchdog.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-board-sweep.timer matches tracked .config/systemd/user/corporatetraveldc-board-sweep.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-docs-drift-weekly.timer matches tracked .config/systemd/user/corporatetraveldc-docs-drift-weekly.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.service matches tracked .config/systemd/user/corporatetraveldc-governor-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-governor-watch.timer matches tracked .config/systemd/user/corporatetraveldc-governor-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.service matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-uber-traffic-watch.timer matches tracked .config/systemd/user/corporatetraveldc-uber-traffic-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.service matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-demo-source-refresh.timer matches tracked .config/systemd/user/corporatetraveldc-demo-source-refresh.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer matches tracked .config/systemd/user/corporatetraveldc-sdr-crashloop-guard.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-aam-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-aam-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-aviation-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-aviation-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-gig-economy-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-gig-economy-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-concierge-travel-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-concierge-travel-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-trains-yachts-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-trains-yachts-daily-watch.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-executive-protection-daily-watch.timer matches tracked .config/systemd/user/corporatetraveldc-executive-protection-daily-watch.timer
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
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-ep-advance-venues.timer matches tracked .config/systemd/user/corporatetraveldc-ep-advance-venues.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.service matches tracked .config/systemd/user/corporatetraveldc-llama-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama-restart.timer matches tracked .config/systemd/user/corporatetraveldc-llama-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-boot-stagger.service
[WARN] tracked .config/systemd/user/corporatetraveldc-mcpo-public.service has no live installed copy at /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-mcpo-public.service -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-stack-boot-stagger.service matches tracked .config/systemd/user/corporatetraveldc-stack-boot-stagger.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-thermal-ingest-guard.service matches tracked .config/systemd/user/corporatetraveldc-thermal-ingest-guard.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.service matches tracked .config/systemd/user/corporatetraveldc-dns-restart.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-dns-restart.timer matches tracked .config/systemd/user/corporatetraveldc-dns-restart.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nts-cert-refresh.service matches tracked .config/systemd/user/corporatetraveldc-nts-cert-refresh.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-nts-cert-refresh.timer matches tracked .config/systemd/user/corporatetraveldc-nts-cert-refresh.timer
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-llama.service matches tracked .config/systemd/user/corporatetraveldc-llama.service
[OK] /home/corporatetraveldc/.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service matches tracked .config/systemd/user/corporatetraveldc-second-brain-weekly-dump.service
[OK] /home/corporatetraveldc/.config/containers/systemd/acars-net.network matches tracked .config/containers/systemd/acars-net.network
[WARN] tracked .config/containers/systemd/amtrak-tracker.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/amtrak-tracker.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[WARN] tracked .config/containers/systemd/corporatetraveldc-acarsdec.container.disabled has no live installed copy at /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsdec.container.disabled -- fine if deliberately disabled/dormant, otherwise install it
[OK] /home/corporatetraveldc/.config/containers/systemd/nextcloud-db.container matches tracked .config/containers/systemd/nextcloud-db.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dumpvdl2.container matches tracked .config/containers/systemd/corporatetraveldc-dumpvdl2.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-acarsrouter.container matches tracked .config/containers/systemd/corporatetraveldc-acarsrouter.container
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
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-ultrafeeder.container matches tracked .config/containers/systemd/corporatetraveldc-ultrafeeder.container
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
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container matches tracked .config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container
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
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-semantic-compile-daily.container matches tracked .config/containers/systemd/corporatetraveldc-semantic-compile-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo-api.container matches tracked .config/containers/systemd/corporatetraveldc-demo-api.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-demo.container matches tracked .config/containers/systemd/corporatetraveldc-demo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-poller.container matches tracked .config/containers/systemd/corporatetraveldc-poller.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-web.container matches tracked .config/containers/systemd/corporatetraveldc-web.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-concierge-travel-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-daily.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-daily.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container matches tracked .config/containers/systemd/corporatetraveldc-transport-pattern-digest.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container matches tracked .config/containers/systemd/corporatetraveldc-trains-yachts-daily-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-aam-weekly-watch.container matches tracked .config/containers/systemd/corporatetraveldc-aam-weekly-watch.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container matches tracked .config/containers/systemd/corporatetraveldc-dispatch-desk-memo.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-weekly-summary.container matches tracked .config/containers/systemd/corporatetraveldc-weekly-summary.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-second-brain-weekly.container matches tracked .config/containers/systemd/corporatetraveldc-second-brain-weekly.container
[OK] /home/corporatetraveldc/.config/containers/systemd/corporatetraveldc-disruption-weather-digest.container matches tracked .config/containers/systemd/corporatetraveldc-disruption-weather-digest.container
--
[FAIL] drift found -- reconcile CLAUDE.md above, then re-run

## Failed / crash-looping units
corporatetraveldc-docs-drift-weekly.service
corporatetraveldc-integrity-sweep.service
(empty above means none)

## Prior second-brain findings on the touched area
query: corporatetraveldc-aam-weekly-watch
```

</details>
