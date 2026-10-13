# ntfy topic audit (2026-10-13)

Read-only audit of every topic the platform publishes to, after the operator saw several topics with no
notification in over a month. Question per topic: **silent by design, or broken?**

**Limits of the evidence.** `/etc/ntfy/server.yml` sets no `cache-duration`, so ntfy keeps its 12 h default:
`cache.db` only reached back to 2026-10-13 05:35Z. Per-topic counts below are 12 h counts; the 30-day verdicts
come from the trigger side (Postgres tables, the watchdog log, pusher state files, `trigger_log`).

**Auth.** The server denies anonymous publishes. Every service token matches the one server token. A tokenless
test publish to a throwaway topic returned HTTP 403 (40301).

## Verdicts

| Topic | Verdict | Cause | Status |
|---|---|---|---|
| **ops-health / hot-alerts from root and manual scripts** | **broken** | `watchdog.sh`, `lockdown.sh`, `threat-initiate.sh`, `threat-resolve.sh`, `restart-stack.sh`, `renew-tailscale-cert.sh`, `restore-network.sh` publish with no token; the 403 is hidden by `\|\| true`. Five p5 "System Restart Needed" pages were lost in 30 days (pihole 09-30 and 10-06, cloudflared 10-03 and 10-05, tailscaled 10-08). | open: needs a root-readable scoped token file (operator, sudo) **and** push-aware staleness plus a cooldown in the watchdog first, or ops-health pages every 90 s on the push-backed REST feeds |
| **tfr-alert** | **broken** | FAA marks VIP TFRs `type: "VIP"`; `src/poller/fetchers/tfr.py` only matched "MARINE ONE"/"POTUS"/"SECURITY TFR" text. All `tfrs` rows had is_vip=0, including 9 active VIP TFRs. | open: operator decision on DC-area-only paging (otherwise 9 nationwide TFRs page hourly); same gating for `tfr_enrichment.py` and `route_impact.py` |
| **all `_fire_ntfy_dual` topics** (dispatch, flight-alerts, stdds-*, tbfm-*) | **intermittent drops** | about 13 intermittent 401/403s in 27 h were logged as "known false-negative, treating as delivered"; 3 of 3 checked were never stored by ntfy | **fixed 2026-10-13**: one delayed retry, then logged as NOT delivered (`src/shared/watchlist.py`, `src/common/ntfy_push.py`) |
| **fdps-zdc** and other fdps zones | **broken (NOTAM path)** | the FDC-NOTAM path sends `KZDC`; it resolved to OTHER, so ZDC restriction NOTAMs reached fdps-alerts but never fdps-zdc | **fixed 2026-10-13**: K-prefixed center codes normalised (`src/shared/sector_coalesce.py`) |
| **dispatch-debriefs** | **partial loss** | two ~46k-character ops briefs rejected with 400 40014 (over `message-size-limit` 65536) | **fixed 2026-10-13**: bodies capped at 60,000 bytes with a truncation note |
| **stdds-zny/zid/zob/zatl/zhu/zla/zse** | **misnamed** | taxi alerts go to undocumented per-airport topics (stdds-atl, -las, -lax, -sea, -iah); `smes_parser.py` overwrites the zone with the bare airport code (since 752e0e0e, Aug 3) | open: operator decision, zone topics (code fix) or subscribe to the airport topics |
| **dispatch-alerts** | **dead** | `scripts/check-pat-expiry.sh` has no timer, reads two token files that don't exist, and posts with no token and no `-f` | open: fix and add a timer, or retire it with the topic |
| cps | working | 84 changes in 30 days; wind changes go to wx-alerts, not cps (docstring and ALERT_REFERENCE are wrong) | docs |
| vessel-alerts | silent by design | no vessel watches, no AIS source | latent: `pusher/main.py:528` retries vessel events onto train-alerts |
| reservations / calls | silent by design | the LimoAnywhere / RingCentral / 3CX webhooks were never connected | none |
| itws-alerts, itws-zdc | silent by design | every ITWS row is severity 0 or 3 ("no storms within 15 nm"); the threshold is 4 | none |
| ep-briefs | no sender | reserved name only | unsubscribe or wire |
| osint-marketing | retired | merged into osint-alerts with a `[MKT]` prefix | unsubscribe |
| tbfm/tfms/aim zones | silent by design | escalating-only; most zones fired in the 12 h window | none |

Working in the 12 h window: hot-alerts 11, nas-alerts 143, wx-alerts 13, flight-alerts 30, train-alerts 9,
dispatch 411, dispatch-ops 6, ops-brief 10, ep and ep-advance 11 each, osint-alerts 4, approval-gate 1.

## Other findings

- **Raise `cache-duration`** (e.g. 72h) in `/etc/ntfy/server.yml` (operator): a phone offline for more than 12 h
  currently misses messages, and audits like this one can't count 30 days.
- `poller/main.py` records `push_test_alert` as success even when the send fails.
- **Not ntfy:** 230 `refresh_feed` triggers have been stuck `in_flight` since the 2026-09-18 Postgres cutover
  (latest 10-13 07:25Z), plus 3 `push_test_alert`.
