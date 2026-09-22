# ⚠️ Guardrail values assume a SINGLE EDGE UNIT (one Raspberry Pi 5)

**Read this before changing — or carrying forward — any resource guardrail, timeout, or scheduling weight in this stack.**

Every performance/resource guardrail in this repository was tuned for **one deployment topology: the entire stack co-resident on a single Raspberry Pi 5** (4× Cortex-A76, 16 GB RAM, **no GPU**, one shared thermal/power envelope, one NIC). The specific *numbers* below are not universal truths — they are the result of **shared-resource contention on one box**. If the topology ever changes (see "When these become obsolete"), most of these values become **wrong, or needlessly conservative**, and must be re-derived per node. Do **not** blindly copy them into a distributed/de-consolidated deployment.

> ## Reconciled 2026-08-19; ollama governance INSTALLED later that day (re-verified live 2026-08-23)
>
> A 2026-08-19 morning audit of this file found that the `ollama.service`
> resource drop-in (`systemd/ollama.service.d/20-resource-limits.conf`) had
> never been installed — the "5:1 inference prioritisation" this document
> described was aspirational at that moment. **That gap was closed the same
> day**: the drop-in was copied to `/etc/systemd/system/ollama.service.d/`,
> `daemon-reload` run, `ollama.service` restarted. Re-verified live
> 2026-08-23: `CPUWeight=500`, `MemoryHigh=6050 MiB`, `MemoryMax=7250 MiB`,
> and the drop-in's own acceptance test now passes — every model load since
> the governed restart logs `prompt cache is disabled` (the pre-install
> journal logged `prompt cache is enabled, size limit: 8192 MiB`).
>
> Rows below marked **LIVE (since 2026-08-19)** were the ones flagged
> NOT DEPLOYED by that morning audit. Rows marked **STALE→corrected** keep
> their 2026-08-19 corrections. `OLLAMA_MAX_LOADED_MODELS` remains genuinely
> unconfigured — that row's correction still stands.
>
> ## Superseded again 2026-08-27 — Ollama retired for llama.cpp (verified 2026-09-03)
>
> The Ollama daemon, its `20-resource-limits.conf` drop-in, and the
> `ollama-governor` unit **no longer exist** — inference is now the
> per-tier `corporatetraveldc-llama-{hot,chat,report-1}` systemd user units
> (llama.cpp `llama-server`, tailnet-IP-bound :8093/8094/8095, one shared
> phi3-mini GGUF, report-1 on-demand). The *principle* of every row below
> stands — everything still shares 4 cores and one thermal envelope — but
> the `ollama.service`, `LLAMA_ARG_CACHE_RAM`, governor,
> `OLLAMA_MAX_LOADED_MODELS`, and `OLLAMA_TIMEOUT`/`OLLAMA_LOAD_TIMEOUT`
> rows now describe a retired unit; the current single-box carve-out lives
> in each llama unit's own directives (e.g. llama-hot `CPUWeight=9000`,
> `MemoryMax=4608M`, never thermally paused). The thermal-ingest-guard row
> also changed 2026-08-27: the fallback-count LOCKDOWN trigger is
> informational-only, and LOCKDOWN no longer stops any LLM service (the
> llama units are deliberately out of scope). Read the Ollama-era rows as
> the historical record of this box's shared-resource tuning, not as
> current config.

## The guardrails this applies to (non-exhaustive)

| Guardrail | Current value | Why it's a single-box number |
|---|---|---|
| Per-container `Memory=` / `--memory-swap=` | 1536m / 1536m (zero extra swap) | Sized so N containers fit in 16 GB without host swap-thrash |
| Per-container `CPUWeight` / `CPUQuota` | 100 / 300% | Proportional share of **4 shared cores**; 300% = "never take the last core" |
| `nextcloud-app` | Memory 2048m, MemoryLow 682M, CPUWeight 150, TimeoutStartSec 300 | Carve-out vs the desktop session on the same box |
| `ollama.service` | CPUWeight 500, CPUQuota 300%, `OLLAMA_KEEP_ALIVE=10m` — **LIVE (since 2026-08-19)**, drop-in installed at `/etc/systemd/system/ollama.service.d/20-resource-limits.conf` | Prioritises inference 5:1 over dispatch **because they share 4 cores** |
| `ollama.service` memory | MemoryLow 4850M / High 6050M / Max 7250M / SwapMax 0 — **LIVE (since 2026-08-19)** | Same drop-in |
| `LLAMA_ARG_CACHE_RAM` | 0 (prompt cache disabled) — **LIVE and confirmed**: every model load since the governed restart logs `prompt cache is disabled` (acceptance test observed, journal-verified 2026-08-23) | llama.cpp's 8 GiB host-RAM prompt cache would otherwise sit against the memory ceiling |
| Ollama thermal governor | freeze ≥75°C / resume ≤68°C — **live and correct** (`/usr/local/bin/ollama_governor.py:7-8`, `ollama-governor.service` active) | The Pi's **single passive/active thermal envelope** |
| `thermal-ingest-guard.py` shed tiers (**added to this table 2026-08-23** — it was always a single-box guardrail, just never listed here) | temp tier 1 ≥74°C sheds `tfms,stdds`; LOCKDOWN at ≥79°C **or** `load1` ≥40 **or** ≥2 Ollama-contention brief fallbacks/300s sheds *the entire stack except `web`*; restore <65°C **and** `load1` <15 held 300s. Redesigned 2026-08-23; **two real LOCKDOWNs observed the same day** (12:18:22 → 12:29:35 and 14:34:42 → 14:45:51, both clean restores, both fired by the Ollama-contention signal rather than temp or load) | `load1` thresholds are only meaningful against **4 shared cores** — a load of 40 is 10× the core count here and means something entirely different on a bigger node. The whole mechanism exists because ingest and inference contend for one CPU/thermal budget; on a de-consolidated stack the LOCKDOWN scope (which stops `poller`/`pusher`/`runner`/host `ollama.service` too) is actively wrong. Detail: `docs/GUARDRAILS_JUSTIFICATION.md` §4, `docs/INFRA_MAP.md` §4.1 |
| `OLLAMA_PREFLIGHT_COOL_TARGET_C` | 70 — **live and correct** (`dispatch.env:306` = 70.0; note `llm.py:482`'s hardcoded fallback is 62.0 if the env var is ever absent) | Same shared-thermal assumption |
| `OLLAMA_TIMEOUT` (brief gen) | ~~240s (fail-fast)~~ → **STALE**. Live `dispatch.env:118` = **3600**. Philosophy was deliberately inverted 2026-08-13: a generous client budget, with the real gate moved to the load-phase timeout | No longer bounded by a 600s container ceiling — see next row |
| Brief container `TimeoutStartSec` | ~~600s~~ → **STALE**. No brief container uses 600. Live: ops-brief 3600, ep-advance 4500, weekly-summary 2800, dispatch-desk-memo 10400 | The stated rationale ("bounded by the 600s ceiling") no longer holds |
| `OLLAMA_LOAD_TIMEOUT` | `dispatch.env:144` = 1800 (client-side load-phase probe in `llm.py`). **Caveat:** ollama's *server* honours an identically-named variable, which is at its **5m default** because dispatch.env is loaded into the containers, not into `ollama.service` — so a load exceeding 5 min is cut server-side regardless of the 1800s client budget | This is now the real gate, per the 2026-08-13 change |
| `OLLAMA_MAX_LOADED_MODELS` | ~~1~~ → **NOT CONFIGURED**. Set in no env file and no drop-in; live ollama reports `OLLAMA_MAX_LOADED_MODELS:0` (auto) | Single-model residency is emergent from memory pressure, not enforced |
| DNS/Tailscale `CPUWeight` (**LIVE, verified 2026-08-08**) | 10000 | Only needed because Pi-hole/Unbound/tailscaled **contend with Ollama on the same cores**; persisted via `set-property` drop-ins at `/etc/systemd/system.control/{tailscaled,pihole-FTL,unbound}.service.d/50-CPUWeight.conf` (survives reboot). On de-consolidation (DNS on its own device) this becomes moot. |
| Boot stagger (`stack-boot-ctl.sh`, 19 units in `ORDER`, `STAGGER=15`s apart) | staggered start | Avoids a boot-storm on **one** RAM/CPU/bandwidth budget |

## When these become obsolete (topology changes to watch for)

- **Ollama moved to dedicated hardware (e.g., a GPU box):** `CPUQuota=300%`, `CPUWeight=500`, the thermal governor, `OLLAMA_PREFLIGHT_COOL_TARGET_C=70`, `OLLAMA_TIMEOUT` (now 3600, not 240), and `OLLAMA_MAX_LOADED_MODELS=1` all become **obsolete or overly conservative** — noting that per the table above, `OLLAMA_MAX_LOADED_MODELS` was never actually configured. A dedicated GPU generates briefs in seconds → the timeout can be short *and* strict again, no thermal freezing is needed, more models can stay loaded, and there is no CPU contention with the dispatch stack to prioritize against.
- **DNS/Tailscale split onto a separate device:** the high `CPUWeight` priority for pihole-FTL/unbound/tailscaled is **moot** — there's no Ollama on that box to starve them.
- **Dispatch containers spread across nodes:** the per-container `Memory=1536m` / `CPUQuota=300%` template was sized for *N containers sharing one 16 GB / 4-core box*; on dedicated or larger nodes it is simply wrong. `thermal-ingest-guard.py` goes with it: its `load1` bands are calibrated to 4 cores, and its LOCKDOWN action (stopping `poller`/`pusher`/`runner`/host `ollama.service` alongside the SWIM feeds) only makes sense while all of those share one thermal/CPU budget.
- **A larger single box (more cores/RAM, or an NPU/GPU):** the CPU-contention weights and the CPU-throughput-bound timeouts should all be re-derived — they encode the *4-core, no-GPU, 16 GB* reality, not a headroom truth.

## Rule of thumb

These numbers answer **"how do we survive shared-resource contention on one Pi 5?"** — not **"what is correct in general."** On any de-consolidation, treat every value here as a **starting point to re-measure**, not a constant to inherit. Re-tune per node against that node's real cores/RAM/thermal/accelerator profile.

*(Cross-refs: the per-container resource template + rationale live in `CLAUDE.md` → "Container resource limits"; the timeout rationale is in `src/poller/skills/ops_brief.py` and the `OLLAMA_TIMEOUT` comment block in `dispatch.env`; the Ollama governor is `/usr/local/bin/ollama_governor.py`, run by `ollama-governor.service`. **Corrected 2026-08-23:** an earlier revision here said `scripts/ollama_governor.sh` "does not exist" — it does (repo-tracked, in `MANIFEST.sha256`, last touched 2026-08-06). It is the **installer** that writes `/usr/local/bin/ollama_governor.py` and the systemd unit, not the governor itself. **The two have drifted, and the direction matters:** the tracked installer's heredoc is *newer* (2026-08-06) than the live `/usr/local/bin/ollama_governor.py` (2026-07-25) — diffed directly 2026-08-23, the installer's copy makes the thresholds env-var configurable (`OLLAMA_GOVERNOR_MAX_TEMP_C` / `_RECOVER_TEMP_C` / `_CHECK_INTERVAL_S`) and adds a startup guard rejecting `RECOVER >= MAX`, neither of which the running file has. **Effective behaviour is nonetheless identical today** — the installer's defaults are the live file's hardcoded values (75.0 / 68.0 / 2.0) and none of those env vars is set in `dispatch.env` or the unit — so re-running the installer would be an upgrade, not a regression. Still read the live `.py` for what is actually running; the boot stagger in `scripts/stack-boot-ctl.sh`.)*

*(Reconciled against live state 2026-08-19; ollama drop-in installed later that day and re-verified live 2026-08-23. The DNS/Tailscale `CPUWeight=10000` row was re-verified and is genuinely live — drop-ins present at `/etc/systemd/system.control/{tailscaled,pihole-FTL,unbound}.service.d/50-CPUWeight.conf`, all three reporting 10000.)*
