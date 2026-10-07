# Design Principles — corporatetraveldc-dispatch

Verified against HEAD 2c3f81b and live state on 2026-10-06 18:25Z / 14:25 ET.

## 1. Local-first, offline-capable by default

The dispatch stack must be **fully operational without any cloud credentials
or internet connectivity** for its core mission. A freshly deployed instance
with only the local llama.cpp server and local config should produce a
working system: feeds poll, CPS scores compute, push alerts fire, the UI
loads.

Cloud services and external APIs are **opt-in enhancements** on top of a
working baseline. No feature may make the baseline inoperable when a cloud
credential is absent.

**Practical test:** cut the uplink. The system should degrade to
last-known-good state — not crash, not error-loop, not refuse to start.

---

## 2. Vendor-neutral inference — local first and only by default

All LLM inference runs locally on one host-level llama.cpp `llama-server`
(since 2026-08-27; Ollama before that). No cloud inference provider is
contacted at runtime unless the operator opts in.

The single cloud path, in `src/common/llm.py`, needs **both** gates open:
`ANTHROPIC_API_KEY` present **and** `ANTHROPIC_FALLBACK_ENABLED` true. The
module default is `false` (since 2026-08-26; `llm.py:399`), and this
deployment also sets `ANTHROPIC_FALLBACK_ENABLED=false` in
`/etc/corporatetraveldc/dispatch.env`. Callers can further pass
`allow_anthropic=False`, as the brief skills do. Result on this deployment:
zero cloud LLM calls.

Rules for contributors:

- **Never** add a cloud LLM SDK import or a dependency-intent entry
  (`requirements.in` or any other `*.in`) without an operator-controlled
  opt-in gate. (The `anthropic` package is in `requirements.in` today, behind
  the two gates above.) <del>**Never** add a cloud LLM SDK import or `requirements.txt` entry without an
  operator-controlled opt-in gate. (The `anthropic` package is in
  `requirements.txt` today, behind the two gates above.)</del> SUPERSEDED 2026-10-07:
  `requirements.txt` is now the generated hash lock; intent lives in
  `requirements.in` (docs/REPRODUCIBLE_BUILDS.md).
- **Never** send inference to a remote endpoint by default. Inference targets
  `LLAMA_BASE_URL` (renamed from `OLLAMA_BASE_URL` on 2026-10-05; the old name
  is read only as a fallback). In `generate()` an unset `LLAMA_BASE_URL` skips
  the local step entirely (`llm.py:924`). Note one exception to "unset means
  off": `src/common/llama_pool.py` falls back to the reference host
  `100.x.x.x:8093` when neither `LLAMA_BASE_URL` nor
  `LLAMA_POOL_HOST`/`LLAMA_PORT` is set, and the runner's chat stream uses
  that pool address. A new deployment must set `LLAMA_BASE_URL`.
- Skills that use inference must fall back to deterministic output when the
  local server is unset or unreachable — never to a cloud provider.
  `generate()` returns `None` on any failure for exactly this.
- Cloud LLM API keys belong in the **optional** section of
  `dispatch-secrets.env.template`. They are never required.

**Rationale:** local inference removes per-token cost and a vendor from the
operational critical path, keeps CUI-adjacent data on-prem, and lets the
system run tailnet-only or offline.

---

## 3. Data sources vs. inference vendors — different standards

External **data feeds** (NWS, METAR, FAA TFR, SWIM NMS, Amtrak, ATCSCC ops
plan) are acceptable dependencies: government or public-interest sources,
read-only, no operator data sent, most with a last-known-good cache.

Their credentials (FAA NMS API client id/secret, SWIM NMS accounts) are
optional: a missing key marks one feed `awaiting_credentials` and degrades
it; it does not break the system. Only NWS, NOTAM and Amtrak have both a push
and a REST path; FDPS, STDDS, TFMS, TBFM and ITWS are push-only and simply go
stale without SWIM.

**Cloud inference vendors are held to a stricter standard**: they receive
operator queries, cost per token, and add a commercial runtime dependency.

---

## 4. Infrastructure dependencies

| Component | Vendor | Replaceability | Notes |
|---|---|---|---|
| llama.cpp (`llama-server`) | Open source | High — model-agnostic | The inference runtime |
| PostgreSQL 16 | Open source | High | Live database since 2026-09-18 |
| ntfy | Open source | High — self-hosted here | Push broker |
| Pi-hole + Unbound | Open source | High | DNS |
| Tailscale | Commercial | Medium — headscale | Network identity; not in the inference path |
| Cloudflare Tunnel + Access | Commercial | Medium — nginx + DDNS | Public ingress and identity gate; not in the inference path |

Tailscale and Cloudflare are acceptable because they are outside the
inference path and have alternatives. A contributor who removes them should
add the alternative as an opt-in, not a replacement.

---

## 5. CUI handling — absolute, non-negotiable

See `SECURITY.md` (repo root). These rules override everything else here.

No credentialed radio frequencies (SHARES, HEARS, HEART, or any FOUO/CUI
data) appear in code, configs, exports or documentation — ever. The
infrastructure ships with placeholder files; the operator populates them on
the deployed device only.

---

## 6. New feature checklist

- [ ] Works with no cloud credentials present
- [ ] Any inference goes through `common.llm` / `common.llama_pool` to `LLAMA_BASE_URL`; any cloud path is gated by explicit operator config
- [ ] No new cloud LLM SDK in `requirements.in` (or its generated lock) without that gate <del>No new cloud LLM SDK in `requirements.txt` without that gate</del> (SUPERSEDED 2026-10-07: intent moved to `requirements.in`)
- [ ] New external feed: documented in README, graceful fallback, optional credentials
- [ ] New secret: added to `dispatch-secrets.env.template` **and** to the allowlist of only the service(s) that need it under `scripts/service-env/`
- [ ] CUI rules satisfied (`SECURITY.md`)

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-core.md`.


### Design Principles — corporatetraveldc-dispatch › 1. Local-first, offline-capable by default

~~The dispatch stack must be **fully operational without any cloud credentials or internet connectivity** for its core mission. A freshly deployed instance with only the local llama.cpp inference stack and local config should produce a working system: feeds poll, CPS scores compute, push alerts fire, the UI loads.~~

~~Cloud services and external APIs are **opt-in enhancements** that add capability on top of an already-working baseline. No feature may make the baseline inoperable if a cloud credential is absent.~~

~~**Practical test:** Pull the Ethernet cable. The system should degrade gracefully to last-known-good state — not crash, not error-loop, not refuse to start.~~


### Design Principles — corporatetraveldc-dispatch › 2. Vendor-neutral inference — local first and only by default

~~All LLM inference runs locally — since the **2026-08-27 cutover** via host-level **llama.cpp** (`llama-server`) processes; before that via Ollama. The `OLLAMA_*` env-var and parameter names throughout the codebase were deliberately kept at the cutover so zero call sites changed — read them as "the local inference server", not as evidence Ollama is running. No cloud inference provider (Anthropic, OpenAI, Google Gemini, Cohere, or any other) is contacted at runtime unless the operator has explicitly opted in. Concretely, the single cloud-fallback path in `src/common/llm.py` requires **both** gates open: `ANTHROPIC_API_KEY` present, **and** the global switch `ANTHROPIC_FALLBACK_ENABLED` — whose module default was flipped **fail-closed (`false` when unset) on 2026-08-26** (`src/common/llm.py`; re-derive with `grep -n ANTHROPIC_FALLBACK_ENABLED src/common/llm.py`). This deployment *additionally* sets `ANTHROPIC_FALLBACK_ENABLED=false` explicitly in `/etc/corporatetraveldc/dispatch.env`, so the path is closed twice over — zero cloud calls. Skills can further hard-disable it per call with `allow_anthropic=False`, as the brief skills do. _(Wording corrected 2026-08-11 — the opt-in gate lives in `src/common/llm.py`, not `src/runner/main.py`; corrected 2026-08-23 to document both gates; corrected 2026-09-03 for the llama.cpp cutover and the fail-closed default flip.)_~~

- ~~**Never** add a cloud LLM import (`anthropic`, `openai`, `google-generativeai`, etc.) to `requirements.txt` or any source file without a corresponding operator-controlled opt-in gate.~~
- ~~**Never** call `api/generate` or `api/chat` against a remote endpoint by default. All inference calls target `OLLAMA_BASE_URL` (legacy name — it now points at the local llama.cpp tier servers) — note the code default is empty/unset (`os.getenv("OLLAMA_BASE_URL", "")`), and unset skips the local-inference path entirely; the live values are set in `dispatch.env`.~~
- ~~Skills that require inference must fall back to `"deterministic"` output if the local server is unset or unreachable — not to a cloud provider.~~
- ~~Cloud LLM API keys belong in the **optional** section of `dispatch-secrets.env`. They are never required for a standard deploy.~~

~~**Rationale:** Local inference eliminates per-token cost, removes a vendor dependency from the operational critical path, keeps CUI-adjacent data on-prem, and ensures the system works airgapped (Tailscale-only or fully offline).~~


### Design Principles — corporatetraveldc-dispatch › 3. Data sources vs. inference vendors — different standards

~~External **data feeds** (NWS, METAR, FAA TFR, SWIM NMS, Amtrak, ATCSCC ops-plan) are acceptable dependencies because:~~

- ~~They are government or public-interest sources with no commercial alternative.~~
- ~~They are read-only; no operator data is sent to them.~~
- ~~Most have a last-known-good cache that covers short outages.~~

~~These feeds may require credentials (FAA NOTAM API key, SWIM NMS account) but those credentials are **always optional** — the poller falls back to REST polling or cached state automatically. A missing key degrades one feed; it does not break the system.~~

~~**Cloud inference vendors are held to a stricter standard** because they receive operator queries, consume per-token cost, and create a runtime dependency on a third-party commercial service.~~


### Design Principles — corporatetraveldc-dispatch › 4. Infrastructure dependencies

| ~~Component~~ | ~~Vendor~~ | ~~Replaceability~~ | ~~Notes~~ |
|---|---|---|---|
| ~~llama.cpp (`llama-server`)~~ | ~~Open-source~~ | ~~High — self-hostable, model-agnostic~~ | ~~Default inference runtime since 2026-08-27 (previously Ollama, itself llama.cpp-based)~~ |
| ~~ntfy~~ | ~~Open-source~~ | ~~High — self-hostable~~ | ~~Push broker; no cloud account needed~~ |
| ~~Pi-hole~~ | ~~Open-source~~ | ~~High~~ | ~~DNS + ad-block~~ |
| ~~Tailscale~~ | ~~Commercial~~ | ~~Medium — self-host headscale~~ | ~~Network identity; not in inference path~~ |
| ~~Cloudflare Tunnel~~ | ~~Commercial~~ | ~~Medium — nginx + DDNS fallback~~ | ~~Named HTTPS ingress; not in inference path~~ |

~~Tailscale and Cloudflare Tunnel are acceptable infrastructure dependencies because they are not in the **inference path** and have viable alternatives. Any future contributor who wishes to eliminate these dependencies should implement the fallback (headscale, nginx+DDNS) as an opt-in, not a replacement.~~


### Design Principles — corporatetraveldc-dispatch › 5. CUI handling — absolute, non-negotiable

~~See `SECURITY.md`. These rules override any other consideration including this document.~~

~~No credentialed radio frequencies (SHARES, HEARS, HEART, or any FOUO/CUI data) appear in code, configs, exports, or documentation — ever. The infrastructure ships with placeholder files. The operator populates from authorized sources on the deployed device only.~~


### Design Principles — corporatetraveldc-dispatch › 6. New feature checklist

~~Before opening a PR that adds a new capability, verify:~~

- ~~[ ] Works with no cloud credentials present (offline baseline unbroken)~~
- ~~[ ] Any inference uses `OLLAMA_BASE_URL`; cloud provider is gated behind an explicit operator config~~
- ~~[ ] No new entry in `requirements.txt` that pulls a cloud LLM SDK without the above gate~~
- ~~[ ] If a new external data feed: documented in README, poller has a graceful fallback, credentials are optional~~
- ~~[ ] If a new secret: added to `dispatch-secrets.env.template` in the appropriate section (data credentials vs. optional cloud LLM)~~
- ~~[ ] CUI rules satisfied (see `SECURITY.md`)~~
