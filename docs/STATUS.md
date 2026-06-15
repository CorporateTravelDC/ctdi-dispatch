# Platform Status — 2026-06-15

## Health
- **Web API:** https://dispatch.csexecutiveservices.com
- **CPS:** Computed from live feeds
- **All containers:** Running — ingest fully operational
- **SWIM:** All 6 NMS feeds live (CS Exec subscription, corey.sheldon@csexecutiveservices.com)
- **NWWS-OI:** Connected — XMPP MUC joined as `corporatetraveldc`

## Services
| Service | Status | Notes |
|---------|--------|-------|
| corporatetraveldc-web | ✅ running | FastAPI, port 8000 |
| corporatetraveldc-poller | ✅ running | fetchers + skill scheduler |
| corporatetraveldc-pusher | ✅ running | ntfy alert sender |
| corporatetraveldc-ingest | ✅ running | All 6 SWIM NMS feeds + NWWS-OI + Amtrak |
| corporatetraveldc-runner | ✅ running | PWA frontend, port 8001 |
| ntfy | ✅ running | port 2586, auth enforced |
| corporatetraveldc-ultrafeeder | installed | awaiting SDR dongle (ADSB1090) |
| corporatetraveldc-acarsrouter | installed | awaiting SDR dongle (ACARS0130) |
| corporatetraveldc-acarshub | installed | awaiting hardware |
| corporatetraveldc-dumpvdl2 | installed | awaiting hardware |

## Feed Status
| Feed | Status | Notes |
|------|--------|-------|
| atcscc_opsplan | ✅ OK | REST polling active |
| metar | ✅ OK | REST polling active |
| nws | ✅ OK | REST polling active |
| tfr | ⚠️ degraded | FAA tfr.faa.gov upstream issue; STDDS push provides TFR data |
| nas | ⚠️ empty | FAA NAS/OIS empty upstream; TFMS push provides GDP/GS data |
| notam | ✅ OK | FAA_NOTAM_API_KEY provisioned |
| amtrak | ✅ OK | Push ingest live via amtrak_tracker + poller fallback |
| push:fdps | ✅ live | FAA SWIM NMS — FDPS VPN, ems2 |
| push:fns | ✅ live | FAA SWIM NMS — AIM_FNS VPN, ems1 (digital NOTAMs) |
| push:itws | ✅ live | FAA SWIM NMS — ITWS VPN, ems2 |
| push:stdds | ✅ live | FAA SWIM NMS — STDDS VPN, ems1 |
| push:tbfm | ✅ live | FAA SWIM NMS — TBFM VPN, ems1 |
| push:tfms | ✅ live | FAA SWIM NMS — TFMS VPN, ems1 |
| push:nws | ✅ live | NWWS-OI XMPP MUC, WFO filter: LWX/AKQ/CTP/PHI |

## FAA SWIM NMS Credentials
- **Account:** corey.sheldon@csexecutiveservices.com
- **Subscription:** Corporate Travel Dispatch Intelligence
- **Portal:** https://portal.swim.faa.gov
- **Feeds:** TFMS, FDPS, TBFM, AIM_FNS, ITWS, STDDS
- **Shared password:** in dispatch-secrets.env (SWIM_NMS_PASS_*)
- **Per-feed queues:** see dispatch-secrets.env SWIM_NMS_QUEUE_*
- **Host split:** TFMS/TBFM/AIM/STDDS → ems1; FDPS/ITWS → ems2

## FAA Registry (N-number lookup)
- **Status:** Loaded (weekly import, local SQLite cache)
- **Use:** Local N-number → ICAO hex cross-reference, LADD privacy list

## Cloudflare Tunnel
- **Tunnel:** `dispatch` (`28bde9a2-0bb2-4cca-a207-9b759c4739f1`)

| Subdomain | Backend | Access |
|-----------|---------|--------|
| dispatch.csexecutiveservices.com | :8000 | CF Access gated |
| ops.csexecutiveservices.com | :8000 | CF Tunnel, open |
| dispatch-runner.csexecutiveservices.com | :8001 | CF Tunnel, open |
| adsb.csexecutiveservices.com | :8080 | CF Tunnel, open |
| acars.csexecutiveservices.com | :9081 | CF Tunnel, open |
| ntfy.csexecutiveservices.com | :2586 | CF Tunnel, open |
| pihole.csexecutiveservices.com | :8091 | Tailscale A record + CF Access |

## ntfy topics
`tfr-alert` · `hot-alerts` · `flight-alerts` · `train-alerts` · `dispatch` · `dispatch-debriefs` · `cps` · `wx-alerts` · `ops-brief` · `ops-health`

## Open Items
- SDR dongles: tag with serials ADSB1090 / ACARS0130, then start ultrafeeder + ACARS stack
- Tailscale + pihole stability: port-forward UDP 41641 to Pi; set Pi Tailscale IP as custom nameserver in admin.tailscale.com
- Image builder reconstruction (docs/PENDING.md TASK 4)
- FAA TFR feed: upstream XML bad — STDDS push feed now provides TFR data as fallback
- NAS feed: upstream empty — TFMS push feed now provides GDP/GS data as replacement
