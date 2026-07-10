# CTDI Dispatch: On-Premises Architecture & Compliance Datasheet
**For Regulated Industries (Financial Services, SEC Rule 17a-4, FINRA Rule 4511)**

The Corporate Travel Dispatch Intelligence (CTDI) platform is uniquely architected for zero-trust, completely isolated on-premises deployments. Unlike cloud-reliant LLM providers, CTDI runs entirely within your firm's managed perimeter, eliminating third-party data supply chain risks.

---

## 1. Data Sovereignty & Isolation Matrix

| Data Classification | Processing Location | External Network Escape | Storage State |
| :--- | :--- | :--- | :--- |
| **Travel Booking / PNR** | Internal Podman Containers | None (0% outbound) | In-Memory / Local DB |
| **LLM Inference Matrix** | Native Host Ollama Daemon | None (Air-gapped compatible) | Ephemeral / Static Weights |
| **Audit Logs** | Systemd Journald / Text Logs | None (0% outbound) | Write-Once Local Drive |

### Complete Cloud Air-Gapping
By default, the platform binds its web interfaces, backend processes, and LLM orchestration layer (`Gemma3`/`Mistral-Nemo`) strictly to the host environment or internal container network interfaces. No travel data, itineraries, or employee records are leaked to public APIs, external training sets, or third-party web apps.

---

## 2. On-Premises Compliance Hook Infrastructure

To comply with archiving mandates regarding operational notifications (such as alerts sent to corporate messaging channels), CTDI contains a native **Compliance Egress Hook Engine** built into the core runner workflow.

### Integration Mechanism
Rather than communicating directly with external communications networks, the CTDI execution loop pushes an unalterable JSON data packet over the local network via HTTP POST to the firm's pre-configured internal recording node.

### Configurable Environmental Variables
The platform reads institutional mapping targets directly from the central, non-secret configuration file at `/etc/corporatetraveldc/dispatch.env`:

```ini
COMPLIANCE_HOOK_ENABLED=true
COMPLIANCE_TARGET_URL=http://firm.local
COMPLIANCE_FORMAT=JSON_STRICT
COMPLIANCE_RETRY_LIMIT=5
```

---

## 3. Standardized Audit Record Format

All events processed through the egress loop are automatically wrapped in a strict structural envelope designed for ingestion by institutional indexing tools (e.g., Global Relay, Smarsh, or native internal SIEM platforms):

```json
{
  "record_id": "ctdi_1719782400",
  "timestamp_utc": "2026-06-30T21:20:00Z",
  "source_node": "ctdi-dispatch-pi5-primary",
  "compliance_classification": "REGULATED_TRAVEL_INTELLIGENCE",
  "data_payload": {
    "event": "reservation_created",
    "operator_id": "OP-901",
    "itinerary_id": "PNR-77A91",
    "risk_level": "LOW",
    "routing": "Local Container Grid"
  }
}
```

---

## 4. Host Integration & Hardening Guidelines

* **SELinux Support:** The system includes ready-to-use Type Enforcement modules (`.te`) that authorize the background systemd service layers to function within targeted enforcement contexts.
* **Process Priority:** The platform isolates worker processes inside rootless Podman containers, keeping system resources strictly ringfenced away from host operations.
* **Thermal Resource Caps:** System configurations map physical thread caps directly to the hardware (`PARAMETER num_thread 3`), ensuring core components (such as Pi-hole or local network routers) always have dedicated computing headroom.

### SELinux Grant Policy: Scoped Labels Over Domain-Wide Booleans

Every SELinux exception this platform requires is granted at the narrowest
scope that resolves it -- a specific `.te` allow rule or a specific
`semanage port` label -- rather than a domain-wide boolean such as
`container_use_devices` or `httpd_can_network_connect`. A boolean grants a
whole security domain (every container, or `nginx` itself) blanket reach
to a whole permission class; a scoped label or rule grants exactly the one
resource one process needs, and nothing else.

This keeps `semanage port -l` and `semodule -l` a complete, self-explaining
audit trail: every entry maps to one named module or one documented
backend, not an open-ended grant whose blast radius has to be inferred.
Concretely:

* `selinux/corporatetraveldc-sdr-usb.te` grants `container_t` access to the
  RTL-SDR USB devices only (`usb_device_t` chr_file) -- not the broader
  `container_use_devices` boolean, which would apply to every container on
  the host, present or future.
* `selinux/label-nginx-backend-ports.sh` labels each nginx `proxy_pass`
  target individually as `http_port_t`, and `selinux/corporatetraveldc-nginx-proxy.te`
  grants `httpd_t` name_connect to that type specifically -- not the
  `httpd_can_network_connect` boolean, whose scope is every TCP port class
  in the base policy, not just the ones this repo defines. Each labeled
  port traces to exactly one vhost in `nginx/conf.d/`.
* `selinux/corporatetraveldc-fail2ban-lockdown.te` grants `fail2ban_t`
  exactly three things -- search on `systemd_unit_file_t` and
  `user_home_dir_t`, and name_connect on `http_port_t` -- discovered via a
  full enforcing sweep on 2026-07-10: fail2ban's actionban/actionunban
  invoke `scripts/lockdown.sh`/`restore-network.sh` from `fail2ban_t`, which
  otherwise can't reach the quadlets under `/home/corporatetraveldc`, can't
  run `systemctl daemon-reload`/`restart`, and can't send the ntfy incident
  notification. No broader domain transition or unconfined exec for
  fail2ban -- just the three grants the scripts actually use.

**Adding a new network-facing service:** add its `proxy_pass` target to
`nginx/conf.d/`, add one line to the `PORTS` list in
`selinux/label-nginx-backend-ports.sh`, and re-run
`selinux/apply-selinux-policy.sh`. Commit both changes together so the port
grant and the vhost that needs it are reviewed as one auditable unit.

### Container Network Isolation: Air-Gapped by Default

The same scoped-grant principle applies to container-to-host networking.
Podman's rootless networking (`pasta`) is air-gapped from the host by
default on this platform -- a container cannot reach anything bound to the
host's own interfaces unless it explicitly opts in. As of 2026-07-10, 15 of
18 containers in this stack need no host access at all and have none; the
LLM inference layer stays true to the "None (Air-gapped compatible)" row in
the Data Sovereignty & Isolation Matrix above -- Ollama listens on this
host's own Tailscale IP only, never on the public-WiFi-facing interface or
any container-default-reachable address.

**Three opt-in mechanisms, chosen by network mode and by what the target
service itself binds to, all scoped to the one container or the one
capability that needs them:**

* **Pasta-mode containers reaching a `0.0.0.0`-bound host service**
  (Podman's default, no `Network=` line or an explicit `Network=pasta`) opt
  in individually with `Network=pasta:--map-gw` on that container's own
  quadlet. This restores the `host.containers.internal` alias for that
  container only. See `corporatetraveldc-pusher.container` (reaching `ntfy`)
  for the pattern -- it carries a comment explaining what it needs to reach
  and why.
* **Pasta-mode containers reaching a service bound to a specific
  non-loopback host IP** (e.g. Ollama on its Tailscale address) address
  that IP directly instead -- `Network=pasta:--map-gw` doesn't apply here
  and isn't needed: the kernel refuses externally-arriving traffic destined
  for `127.0.0.0/8` regardless of pasta's routing (anti-spoofing), so
  `host.containers.internal` can never reach a strictly loopback-bound
  service under any pasta flag, while a real routable IP is reachable via
  normal outbound NAT with no opt-in at all. See `openwebui.container`
  (reaching Ollama) and `corporatetraveldc-runner.container` (reaching
  `dispatch`/`ultrafeeder`) for the pattern.
* **Bridge-mode containers** (`Network=<name>.network`, e.g.
  `corporatetraveldc-acarshub` on `acars-net.network`) have no per-container
  equivalent for the `host.containers.internal` alias: rootless Podman's
  bridge networking is itself tunneled through one shared `rootless-netns`
  pasta process, so that opt-in is host-wide --
  `pasta_options = ["--map-gw"]` in `.config/containers/containers.conf`.
  This is the one case where Podman's current architecture doesn't allow
  per-container scoping; it's documented there as affecting every
  bridge-networked container on the host, not just the one that needed it.
  (This mechanism only works for host services bound beyond loopback, same
  constraint as above.)

**`Network=host` requires a comment justifying it, the same way
`host.containers.internal` usage does.** It grants a container the host's
full network stack with no isolation boundary at all -- broader than either
opt-in mechanism above. `corporatetraveldc-runner.container` ran with
`Network=host` undocumented until 2026-07-10; it was removed in favor of
the same IP-scoped `PublishPort=` pattern `corporatetraveldc-web.container`
already used, since nothing about that service actually required full host
networking.

**Adding host-reach to a new container:** default to no `Network=` line at
all. If it needs to reach a host-bound service, use
`Network=pasta:--map-gw` and comment why. Reach for the bridge-mode
host-wide setting or `Network=host` only if the per-container mechanism
genuinely doesn't apply, and say so in a comment either way.

