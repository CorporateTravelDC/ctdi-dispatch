#!/usr/bin/env python3
"""
Save corporatetraveldc dispatch state to a persistent snapshot file.
Called automatically when context approaches 900k tokens.
"""

import json
import os
import sys
from datetime import datetime, timezone

try:
    import urllib.request
    import urllib.error
except ImportError:
    print("ERROR: urllib not available", file=sys.stderr)
    sys.exit(1)

# NOTE 2026-07-27: was "https://ops.example.com" -- that hostname is
# reserved by the Cloudflare tunnel for the dispatch-runner PWA frontend, not the
# API (same collision documented for dispatch-mcp, fixed there 2026-07-17/18 --
# this script was never updated to match). Every call under "ops." either 404s,
# returns the SPA's index.html (JSON-parse failure), or a bogus stub payload like
# {"service":"dispatch-runner"} -- confirmed live 2026-07-27. Every endpoint below
# has been silently capturing garbage or nothing since this script was written.
DISPATCH_BASE = "https://dispatch.example.com"
STATE_FILE = os.path.expanduser("~/.config/Claude/dispatch_state_snapshot.json")

# Tier-0 (no auth) endpoints, reachable over the public Cloudflare tunnel domain.
ENDPOINTS = {
    "health":   "/healthz",
    "feeds":    "/api/v1/feeds",
    "tfr":      "/api/v1/tfr",
    "weather":  "/api/v1/weather",
    "alerts":   "/api/v1/alerts",
    "cps":      "/api/v1/cps",
    "amtrak":   "/api/v1/amtrak",
}

# Tier-1 (Tailscale-network or admin-token gated) endpoints. Confirmed live
# 2026-07-27 that /api/v1/runsheet returns 403 over the public tunnel domain
# without either a Tailscale-sourced request or a valid admin bearer token --
# it is NOT Tier-0 despite living in the same dispatch API. Handled separately
# from ENDPOINTS above so a missing token degrades to a clear WARN instead of
# masquerading as a Tier-0 fetch that just happened to fail.
TIER1_ENDPOINTS = {
    "runsheet": "/api/v1/runsheet",
}

# Optional -- if this env var is set (e.g. sourced from dispatch-secrets.env on
# the Pi) it's used to satisfy the Tier-1 gate on TIER1_ENDPOINTS regardless of
# network path. Never hardcode a token here -- this file is tracked in both the
# private repo and its public mirrors.
ADMIN_TOKEN = os.environ.get("DISPATCH_ADMIN_TOKEN")

TIMEOUT = 8  # seconds per endpoint


def fetch(path: str, extra_headers: dict | None = None) -> dict | None:
    url = DISPATCH_BASE + path
    headers = {"Accept": "application/json", "User-Agent": "curl/8.0"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(f"  WARN: {path} 403 (Tier-1 gated, no Tailscale path and "
                  f"no/invalid DISPATCH_ADMIN_TOKEN)", file=sys.stderr)
        else:
            print(f"  WARN: {path} HTTP {e.code} - {e}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"  WARN: {path} unreachable - {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  WARN: {path} failed - {e}", file=sys.stderr)
        return None


def main():
    print("dispatch-context-guardian: saving state snapshot...")

    state = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "session_token_estimate": None,  # caller may inject this
    }

    # Accept optional token count as first arg
    if len(sys.argv) > 1:
        try:
            state["session_token_estimate"] = int(sys.argv[1])
        except ValueError:
            pass

    for key, path in ENDPOINTS.items():
        print(f"  fetching {path}...", end=" ", flush=True)
        data = fetch(path)
        state[key] = data
        print("ok" if data is not None else "failed")

    auth_headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"} if ADMIN_TOKEN else None
    for key, path in TIER1_ENDPOINTS.items():
        print(f"  fetching {path} (tier-1)...", end=" ", flush=True)
        data = fetch(path, extra_headers=auth_headers)
        state[key] = data
        print("ok" if data is not None else "failed")

    # Snapshot SSH public key so restore can detect key changes after compact
    ssh_pub = os.path.expanduser("~/.ssh/cowork_ed25519.pub")
    if os.path.exists(ssh_pub):
        try:
            with open(ssh_pub) as f:
                state["ssh_pubkey"] = f.read().strip()
        except Exception:
            state["ssh_pubkey"] = None
    else:
        state["ssh_pubkey"] = None

    # Ensure destination directory exists
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\nState saved -> {STATE_FILE}")
    print(f"  Saved at: {state['saved_at']}")
    print(f"  Endpoints captured: {sum(1 for v in state.values() if isinstance(v, dict))}")


if __name__ == "__main__":
    main()
