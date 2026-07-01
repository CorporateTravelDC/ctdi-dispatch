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

