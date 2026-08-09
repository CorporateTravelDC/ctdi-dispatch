# ⚠️ Guardrail values assume a SINGLE EDGE UNIT (one Raspberry Pi 5)

**Read this before changing — or carrying forward — any resource guardrail, timeout, or scheduling weight in this stack.**

Every performance/resource guardrail in this repository was tuned for **one deployment topology: the entire stack co-resident on a single Raspberry Pi 5** (4× Cortex-A76, 16 GB RAM, **no GPU**, one shared thermal/power envelope, one NIC). The specific *numbers* below are not universal truths — they are the result of **shared-resource contention on one box**. If the topology ever changes (see "When these become obsolete"), most of these values become **wrong, or needlessly conservative**, and must be re-derived per node. Do **not** blindly copy them into a distributed/de-consolidated deployment.

## The guardrails this applies to (non-exhaustive)

| Guardrail | Current value | Why it's a single-box number |
|---|---|---|
| Per-container `Memory=` / `--memory-swap=` | 1536m / 1536m (zero extra swap) | Sized so N containers fit in 16 GB without host swap-thrash |
| Per-container `CPUWeight` / `CPUQuota` | 100 / 300% | Proportional share of **4 shared cores**; 300% = "never take the last core" |
| `nextcloud-app` | Memory 2048m, MemoryLow 682M, CPUWeight 150, TimeoutStartSec 300 | Carve-out vs the desktop session on the same box |
| `ollama.service` | CPUWeight **500**, CPUQuota **300%**, KEEP_ALIVE 10m | Prioritized 5:1 over dispatch **because they share 4 cores** |
| Ollama thermal governor | freeze ≥75°C / resume ≤68°C | The Pi's **single passive/active thermal envelope** |
| `OLLAMA_PREFLIGHT_COOL_TARGET_C` | 70 | Same shared-thermal assumption |
| `OLLAMA_TIMEOUT` (brief gen) | 240s (fail-fast) | Bounded by CPU-only LLM throughput **and** the 600s container `TimeoutStartSec`; assumes CPU inference under contention |
| Brief container `TimeoutStartSec` | 600s | systemd kill ceiling on the shared box |
| `OLLAMA_MAX_LOADED_MODELS` | 1 | One model in 16 GB shared RAM, one CPU inference engine |
| DNS/Tailscale `CPUWeight` (**LIVE, verified 2026-08-08**) | 10000 | Only needed because Pi-hole/Unbound/tailscaled **contend with Ollama on the same cores**; persisted via `set-property` drop-ins at `/etc/systemd/system.control/{tailscaled,pihole-FTL,unbound}.service.d/50-CPUWeight.conf` (survives reboot). On de-consolidation (DNS on its own device) this becomes moot. |
| Boot stagger (`stack-boot-ctl.sh`, 18 units) | staggered start | Avoids a boot-storm on **one** RAM/CPU/bandwidth budget |

## When these become obsolete (topology changes to watch for)

- **Ollama moved to dedicated hardware (e.g., a GPU box):** `CPUQuota=300%`, `CPUWeight=500`, the thermal governor, `OLLAMA_PREFLIGHT_COOL_TARGET_C=70`, `OLLAMA_TIMEOUT=240`, and `OLLAMA_MAX_LOADED_MODELS=1` all become **obsolete or overly conservative**. A dedicated GPU generates briefs in seconds → the timeout can be short *and* strict again, no thermal freezing is needed, more models can stay loaded, and there is no CPU contention with the dispatch stack to prioritize against.
- **DNS/Tailscale split onto a separate device:** the high `CPUWeight` priority for pihole-FTL/unbound/tailscaled is **moot** — there's no Ollama on that box to starve them.
- **Dispatch containers spread across nodes:** the per-container `Memory=1536m` / `CPUQuota=300%` template was sized for *N containers sharing one 16 GB / 4-core box*; on dedicated or larger nodes it is simply wrong.
- **A larger single box (more cores/RAM, or an NPU/GPU):** the CPU-contention weights and the CPU-throughput-bound timeouts should all be re-derived — they encode the *4-core, no-GPU, 16 GB* reality, not a headroom truth.

## Rule of thumb

These numbers answer **"how do we survive shared-resource contention on one Pi 5?"** — not **"what is correct in general."** On any de-consolidation, treat every value here as a **starting point to re-measure**, not a constant to inherit. Re-tune per node against that node's real cores/RAM/thermal/accelerator profile.

*(Cross-refs: the per-container resource template + rationale live in `CLAUDE.md` → "Container resource limits"; the 240s timeout rationale is in `src/poller/skills/ops_brief.py`; the Ollama governor in `scripts/ollama_governor.sh`; the boot stagger in `scripts/stack-boot-ctl.sh`.)*
