# Design Principles — corporatetraveldc-dispatch

## 1. Local-first, offline-capable by default

The dispatch stack must be **fully operational without any cloud credentials or internet connectivity** for its core mission. A freshly deployed instance with only Ollama and local config should produce a working system: feeds poll, CPS scores compute, push alerts fire, the UI loads.

Cloud services and external APIs are **opt-in enhancements** that add capability on top of an already-working baseline. No feature may make the baseline inoperable if a cloud credential is absent.

**Practical test:** Pull the Ethernet cable. The system should degrade gracefully to last-known-good state — not crash, not error-loop, not refuse to start.

---

## 2. Vendor-neutral inference — Ollama first and only by default

All LLM inference runs locally via **Ollama**. No cloud inference provider (Anthropic, OpenAI, Google Gemini, Cohere, or any other) is contacted at runtime unless the operator has explicitly opted in and wired a provider into `src/runner/main.py`.

Rules for contributors:

- **Never** add a cloud LLM import (`anthropic`, `openai`, `google-generativeai`, etc.) to `requirements.txt` or any source file without a corresponding operator-controlled opt-in gate.
- **Never** call `api/generate` or `api/chat` against a remote endpoint by default. All inference calls target `OLLAMA_BASE_URL` (default: `http://host.containers.internal:11434`).
- Skills that require inference must fall back to `"deterministic"` output if `OLLAMA_BASE_URL` is unset or unreachable — not to a cloud provider.
- Cloud LLM API keys belong in the **optional** section of `dispatch-secrets.env`. They are never required for a standard deploy.

**Rationale:** Local inference eliminates per-token cost, removes a vendor dependency from the operational critical path, keeps CUI-adjacent data on-prem, and ensures the system works airgapped (Tailscale-only or fully offline).

---

## 3. Data sources vs. inference vendors — different standards

External **data feeds** (NWS, METAR, FAA TFR, SWIM NMS, Amtrak, ATCSCC ops-plan) are acceptable dependencies because:

- They are government or public-interest sources with no commercial alternative.
- They are read-only; no operator data is sent to them.
- Most have a last-known-good cache that covers short outages.

These feeds may require credentials (FAA NOTAM API key, SWIM NMS account) but those credentials are **always optional** — the poller falls back to REST polling or cached state automatically. A missing key degrades one feed; it does not break the system.

**Cloud inference vendors are held to a stricter standard** because they receive operator queries, consume per-token cost, and create a runtime dependency on a third-party commercial service.

---

## 4. Infrastructure dependencies

| Component | Vendor | Replaceability | Notes |
|---|---|---|---|
| Ollama | Open-source | High — self-hostable, model-agnostic | Default inference runtime |
| ntfy | Open-source | High — self-hostable | Push broker; no cloud account needed |
| Pi-hole | Open-source | High | DNS + ad-block |
| Tailscale | Commercial | Medium — self-host headscale | Network identity; not in inference path |
| Cloudflare Tunnel | Commercial | Medium — nginx + DDNS fallback | Named HTTPS ingress; not in inference path |

Tailscale and Cloudflare Tunnel are acceptable infrastructure dependencies because they are not in the **inference path** and have viable alternatives. Any future contributor who wishes to eliminate these dependencies should implement the fallback (headscale, nginx+DDNS) as an opt-in, not a replacement.

---

## 5. CUI handling — absolute, non-negotiable

See `SECURITY.md`. These rules override any other consideration including this document.

No credentialed radio frequencies (SHARES, HEARS, HEART, or any FOUO/CUI data) appear in code, configs, exports, or documentation — ever. The infrastructure ships with placeholder files. The operator populates from authorized sources on the deployed device only.

---

## 6. New feature checklist

Before opening a PR that adds a new capability, verify:

- [ ] Works with no cloud credentials present (offline baseline unbroken)
- [ ] Any inference uses `OLLAMA_BASE_URL`; cloud provider is gated behind an explicit operator config
- [ ] No new entry in `requirements.txt` that pulls a cloud LLM SDK without the above gate
- [ ] If a new external data feed: documented in README, poller has a graceful fallback, credentials are optional
- [ ] If a new secret: added to `dispatch-secrets.env.example` in the appropriate section (data credentials vs. optional cloud LLM)
- [ ] CUI rules satisfied (see `SECURITY.md`)
