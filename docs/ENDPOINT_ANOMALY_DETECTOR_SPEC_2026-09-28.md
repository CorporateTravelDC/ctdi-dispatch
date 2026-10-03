# Endpoint-Anomaly Detector — Spec (2026-09-28)

Supersedes the Uber-specific `scripts/uber-traffic-watch.py`. This is the
generalized, app-agnostic "sibling anomaly" monitor. It is the concrete
build for backlog item **#8 "DNS-anomaly rename"**, expanded with shape math
and the calibration learned from the 2026-09-28 phone-telemetry captures
(vault `01-Sources/manual/20260928T165736Z.md`).

Parity posture (per aviation-parity rule): DNS behavioral baselining is
**at-parity** with how the aviation feeds are baselined for drift; payload
blindness at the DNS layer is a **defined break**; the Tier-2 flow tap is the
**target-parity** closer.

## Rename
- `scripts/uber-traffic-watch.py` -> `scripts/endpoint-anomaly-watch.py`
- unit files, `STATE_FILE` (migrate `uber_traffic_watch_state.json`, keep old
  path readable on first run), env `UBER_WATCH_CLIENT_IP` -> `ANOMALY_WATCH_CLIENT_IP`
  (old as fallback), fn `classify_uber_domain` -> `classify_domain`,
  `ALERT_REFERENCE.md` rows. In the whole-tree manifest -> re-sign (operator).
  NOT in the verified-exec scoped set, so it won't hard-abort scheduled jobs.
- App-agnostic: watches ANY domain family the same way; `KNOWN_MARKER_DOMAINS`
  and the watched client(s) are config, not hardcoded to Uber.

## Tier 1 — DNS behavioral detector (Pi-hole FTL; no exit node needed)
Per registrable-domain AND per CDN-edge-family, keep a rolling baseline and
alert on deviation:

1. **Shape inversion (the gvt1 signal).** For edge families like
   `rrN---sn-<cluster>.<cdn>`, track the **host:cluster ratio**. Download shape =
   few clusters, many `rrN` mirrors (ratio high, e.g. >=4). Fan-out shape =
   many distinct clusters ~once each (ratio -> 1). Alert when a family that was
   download-shaped inverts to ~1:1 fan-out, or when distinct-cluster count
   spikes above its EWMA baseline. (2026-09-26: 151 distinct clusters, ratio 1.0.)
2. **Beacon cadence.** Low-jitter fixed inter-arrival gaps = telemetry beacon,
   distinct from episodic download bursts. Flag periodicity, not just volume.
3. **Novel family in a reconnect window** (existing logic, kept & generalized).
4. **Download-CDN traffic with no download shape** — CDN edge activity that
   lacks the many-mirrors/few-clusters pattern.

## Calibration (from the 2026-09-28 captures — the key correction)
Direction/volume ALONE does not mean telemetry. Observed counter-example:
`fra-storage.proton.me` at r54.9 outbound (2.86 MB) was a legitimate Proton
Drive upload to the operator's own cloud. Therefore scoring combines:

    score = f(direction_ratio, flow_size, cadence_regularity, destination_class)

- **destination_class** allowlists: user-owned cloud (Proton, dav/ntfy
  csexecutiveservices), and the genuine download shape (few-clusters/many-mirrors
  gvt1/googlevideo). Known trackers (AppsFlyer, Sentry, Datadog RUM, Sift,
  Firebase/app-measurement, DoubleClick, amazon-adsystem/bdtelemetry,
  graph.facebook, mapbox events) score UP.
- **Telemetry signature** = small + periodic + tracker-class + outbound-heavy.
- **Upload (benign)** = large + to user-cloud, regardless of direction ratio.

## Tier 2 — flow confirmation (payload direction/volume)
DNS cannot see bytes/direction. Two ways to confirm push-vs-pull, neither
permanent for the driver phone:
- **Exit-node-through-Pi (temporary):** phone sets the Pi as Tailscale exit node
  for a capture window; a scoped `tcpdump host <phone> and tcp port 443` (root,
  drop-priv with `-Z`) yields a pcap. Analyze with the pure-python
  `pcap_flows.py` / `pcap_labeled.py` (no tshark on the box): per-dest bytes by
  direction, joined to the phone's resolved domains in-window (SNI is
  ECH-wrapped, so DNS-correlation is the attribution path). Validated
  2026-09-28.
- **On-device PCAPdroid (preferred long-term):** per-app attribution, optional
  TLS decrypt with a user CA, no route change. Use this if continuous
  flow-level monitoring is wanted — do NOT pin the phone's whole route to the Pi
  (latency + single point of failure for a live-dispatch device).

## Interim (approved 2026-09-28): allowlist download-shape, alert only on fan-out
Until the rebuild lands, the existing watch keeps firing on the 1:1 fan-out
anomaly but treats the few-clusters/many-mirrors download shape as known-good,
to cut nuisance alerts on legitimate Google downloads.
