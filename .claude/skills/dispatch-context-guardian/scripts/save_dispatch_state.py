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

DISPATCH_BASE = "https://ops.example.com"
STATE_FILE = os.path.expanduser("~/.config/Claude/dispatch_state_snapshot.json")

ENDPOINTS = {
    "health":   "/healthz",
    "feeds":    "/api/v1/feeds",
    "tfr":      "/api/v1/tfr",
    "weather":  "/api/v1/weather",
    "alerts":   "/api/v1/alerts",
    "cps":      "/api/v1/cps",
    "amtrak":   "/api/v1/amtrak",
    "runsheet": "/api/v1/runsheet",
}

TIMEOUT = 8  # seconds per endpoint


def fetch(path: str) -> dict | None:
    url = DISPATCH_BASE + path
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
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
