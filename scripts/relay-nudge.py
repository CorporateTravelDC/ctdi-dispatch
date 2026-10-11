#!/usr/bin/env python3
"""scripts/relay-nudge.py -- push a phone notification from a relay (2026-10-11).

Relays call this right BEFORE an interactive step (GPG or SSH passphrase,
sudo, an approval) and from an EXIT trap, so a long relay never sits unseen at
a prompt. Operator request after a push-public waited 8 h at its GPG prompt.

    relay-nudge.py "<title>" "<message>" [--priority 1-5] [--topic ops-health]

The ntfy URL is read from /etc/corporatetraveldc/dispatch.env and the token
from dispatch-secrets.env, by PARSING them (never sourcing). The token goes
only into the request header, never into argv or the output. Never fails the
caller: any error prints one line and exits 0.

Bash helper for relays:
    nudge() { python3 scripts/relay-nudge.py "$@" || true; }
    trap 'rc=$?; nudge "Relay N $([[ $rc == 0 ]] && echo finished OK || echo FAILED rc=$rc)" "see Remote-Admin" --priority $([[ $rc == 0 ]] && echo 3 || echo 5)' EXIT
    nudge "Relay N: GPG passphrase needed" "signing step is waiting in Remote-Admin" --priority 5
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

ENV = Path("/etc/corporatetraveldc/dispatch.env")
SECRETS = Path("/etc/corporatetraveldc/dispatch-secrets.env")


def read_key(path: Path, key: str) -> str:
    try:
        for line in path.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("title")
    ap.add_argument("message")
    ap.add_argument("--priority", type=int, default=4, choices=range(1, 6))
    ap.add_argument("--topic", default="ops-health")
    a = ap.parse_args(argv)
    port = (read_key(ENV, "NTFY_URL") or "http://127.0.0.1:2586").rsplit(":", 1)[-1].strip("/")
    token = read_key(SECRETS, "NTFY_TOKEN").split(":")[0]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/{a.topic}", data=a.message.encode(), method="POST")
    req.add_header("Title", a.title)
    req.add_header("Priority", str(a.priority))
    req.add_header("Tags", "bell")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"relay-nudge: sent ({a.title})")
    except Exception as e:  # noqa: BLE001 -- a nudge must never break a relay
        print(f"relay-nudge: not sent ({type(e).__name__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
