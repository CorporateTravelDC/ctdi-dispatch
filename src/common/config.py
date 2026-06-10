"""
Config loader.
Reads /etc/corporatetraveldc/dispatch.env (non-secrets) and /etc/corporatetraveldc/dispatch-secrets.env (mode 0600).
Falls back to environment variables for local dev.
"""

import os
from pathlib import Path


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a file into os.environ if not already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# Load on import — idempotent.
_load_env_file(Path("/etc/corporatetraveldc/dispatch.env"))
_load_env_file(Path("/etc/corporatetraveldc/dispatch-secrets.env"))


def require(key: str) -> str:
    """Return env var or raise at startup — fail fast."""
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Required env var {key!r} is not set. "
                           "Check /etc/corporatetraveldc/dispatch.env or dispatch-secrets.env.")
    return val


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ── Named accessors ────────────────────────────────────────────────────────────


def ntfy_url() -> str:
    return get("NTFY_URL", "http://localhost:8080")

def ntfy_token() -> str:
    return get("NTFY_TOKEN", "")

def db_path() -> str:
    return get("DISPATCH_DB", "/var/lib/corporatetraveldc/corporatetraveldc.db")

def state_dir() -> str:
    return get("DISPATCH_STATE_DIR", "/var/lib/corporatetraveldc")

def trigger_dir() -> str:
    return get("DISPATCH_TRIGGER_DIR", "/run/corporatetraveldc/triggers")

def token_secret() -> str:
    return require("DISPATCH_TOKEN_SECRET")

def web_host() -> str:
    return get("DISPATCH_WEB_HOST", "127.0.0.1")

def web_port() -> int:
    return int(get("DISPATCH_WEB_PORT", "8000"))

def vip_watchlist_path() -> str:
    return get("VIP_WATCHLIST_PATH",
               "/var/lib/corporatetraveldc/vip_watchlist.txt")

def tailscale_domain_suffix() -> str:
    """Tailscale magic DNS suffix — used by Tier 1 auth."""
    return get("TAILSCALE_DOMAIN_SUFFIX", ".csexecutiveservices.ts.net")


def faa_notam_api_key() -> str:
    """FAA NOTAM API key — register free at https://api.faa.gov"""
    return get("FAA_NOTAM_API_KEY", "")

def amtrak_local_url() -> str:
    """URL of the local Amtrak tracker container. Empty string to disable."""
    return get("AMTRAK_LOCAL_URL", "http://host.containers.internal:8898")

def ops_plan_path() -> str:
    """Path to the operator-populated ops plan JSON file."""
    return get("OPS_PLAN_PATH",
               "/var/lib/corporatetraveldc/ops-plan.json")
