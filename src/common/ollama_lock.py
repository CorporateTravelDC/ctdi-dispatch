"""
common.ollama_lock -- OllamaBusyError only. The process-wide mutex +
hot/report priority arbitration this module used to implement is GUTTED
2026-08-30 -- confirmed zero callers of ollama_slot()/is_hot_pending() (or
any of their private helpers) anywhere in src/.

Why the whole locking mechanism is obsolete, not just unused: it existed
to arbitrate a single shared Ollama instance (2026-07-26) -- a slow
report-priority call (ep-advance-brief, ops-brief, weekly-summary) could
occupy Ollama's one slot for minutes, starving a genuinely time-sensitive
hot (VIP/TFR) call queued behind it. Post llama.cpp-cutover
(common/llama_pool.py), that problem no longer exists structurally: "hot"
and "chat" are permanent, separate, always-resident llama-server
processes with their own dedicated ports and CPUWeight=9000 -- a hot call
never queues behind a report call in the first place, because there is no
longer one shared slot for them to contend over.

OllamaBusyError itself is kept -- common/llm.py's ollama_post_with_retry()
still raises it (wrapping llama_pool.PoolBusyError) so every existing
caller catching this exception name continues to work unchanged.
"""


class OllamaBusyError(TimeoutError):
    """Raised when a llama-server slot could not be claimed (see
    common/llama_pool.py's claim_port()/PoolBusyError, which
    common/llm.py's ollama_post_with_retry() wraps into this exception for
    backward-compatible naming). Callers should treat this exactly like
    "Ollama unavailable" and fall through to their existing deterministic
    fallback."""
