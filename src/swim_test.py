#!/usr/bin/env python3
"""
FAA SWIM NMS/SCDS connectivity test.

Reads credentials from /etc/corporatetraveldc/dispatch.env and
dispatch-secrets.env, attempts to connect to the Solace broker for
each configured feed, binds to the queue, and tries to receive one
message within a 15-second window.

Usage:
    python3 swim_test.py [FEED]

    FEED: TFMS, FDPS, STDDS, AIM, TBFM, ITWS  (default: all)

Exit codes:
    0  — at least one feed connected and received a message
    1  — connection failed (credentials/network issue, not our code)
    2  — connected but no message in timeout (queue may be empty or topic wrong)
"""

import os, sys, time, pathlib

# ── Load env files ──────────────────────────────────────────────────────────

def load_env(path: str) -> dict:
    env = {}
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env

cfg = {**load_env("/etc/corporatetraveldc/dispatch.env"),
       **load_env("/etc/corporatetraveldc/dispatch-secrets.env")}

FEEDS = ["TFMS", "FDPS", "STDDS", "AIM", "TBFM", "ITWS"]
TARGET = [sys.argv[1].upper()] if len(sys.argv) > 1 else FEEDS

HOST = cfg.get("SWIM_NMS_HOST", "")
if not HOST:
    print("ERROR: SWIM_NMS_HOST not set in dispatch.env")
    sys.exit(1)

# ── Solace connect + receive one message ────────────────────────────────────

from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.queue import Queue
from solace.messaging.receiver.persistent_message_receiver import PersistentMessageReceiver
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError as PubSubPlusClientException
from solace.messaging.config.transport_security_strategy import TLS

TIMEOUT_S = 8
received = {}

def test_feed(feed: str) -> str:
    user  = cfg.get(f"SWIM_NMS_USER_{feed}", "")
    passwd = cfg.get(f"SWIM_NMS_PASS_{feed}", "")
    queue = cfg.get(f"SWIM_NMS_QUEUE_{feed}", "")
    vpn   = cfg.get(f"SWIM_NMS_VPN_{feed}", "")

    if not all([user, passwd, queue, vpn]):
        return f"SKIP  [{feed}] — credentials not set"

    # Per-feed host override takes precedence over global SWIM_NMS_HOST
    host = cfg.get(f"SWIM_NMS_HOST_{feed}", HOST)
    if not host.startswith(("tcp://", "tcps://", "ws://", "wss://")):
        host = f"tcp://{host}"

    props = {
        "solace.messaging.transport.host": host,
        "solace.messaging.service.vpn-name": vpn,
        "solace.messaging.authentication.scheme.basic.username": user,
        "solace.messaging.authentication.scheme.basic.password": passwd,
        # Prevent indefinite hangs on slow broker handshake
        "SOLCLIENT_SESSION_PROP_CONNECT_TIMEOUT_MS": "15000",
        "SOLCLIENT_SESSION_PROP_CONNECT_RETRIES": "0",
        "SOLCLIENT_SESSION_PROP_RECONNECT_RETRIES": "0",
    }

    svc = None
    try:
        # Use system trust store; skip validation for diagnostic test to isolate auth vs TLS
        tls = TLS.create().without_certificate_validation()
        svc = (MessagingService.builder()
                .from_properties(props)
                .with_transport_security_strategy(tls)
                .build())
        svc.connect()
    except PubSubPlusClientException as e:
        return f"FAIL  [{feed}] CONNECT error: {e}"
    except Exception as e:
        return f"FAIL  [{feed}] unexpected connect error: {type(e).__name__}: {e}"

    try:
        q = Queue.durable_non_exclusive_queue(queue)
        rcvr: PersistentMessageReceiver = (
            svc.create_persistent_message_receiver_builder()
               .with_message_auto_acknowledgement()
               .build(q))
        rcvr.start()
    except PubSubPlusClientException as e:
        svc.disconnect()
        return f"FAIL  [{feed}] QUEUE BIND error: {e}"
    except Exception as e:
        svc.disconnect()
        return f"FAIL  [{feed}] unexpected bind error: {type(e).__name__}: {e}"

    # Try to receive one message
    deadline = time.time() + TIMEOUT_S
    msg = None
    while time.time() < deadline:
        msg = rcvr.receive_message(timeout=1000)
        if msg:
            break

    rcvr.terminate()
    svc.disconnect()

    if msg:
        payload_len = len(msg.get_payload_as_bytes() or b"")
        topic = msg.get_destination_name() or "?"
        return f"OK    [{feed}] connected, received {payload_len}B on topic: {topic}"
    else:
        return f"EMPTY [{feed}] connected + queue bound OK — no message in {TIMEOUT_S}s (queue may be empty or credentials invalid for this topic)"

# ── Run ─────────────────────────────────────────────────────────────────────

print(f"SWIM NMS host: {HOST}")
print(f"Testing feeds: {', '.join(TARGET)}")
print("-" * 60)

results = []
for feed in TARGET:
    print(f"  Testing {feed}...", end=" ", flush=True)
    r = test_feed(feed)
    sys.stdout.buffer.write((r.split("]", 1)[-1].strip() + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()
    results.append(r)

print("-" * 60)
for r in results:
    sys.stdout.buffer.write((r + "\n").encode("utf-8", errors="replace"))
sys.stdout.buffer.flush()

fails  = [r for r in results if r.startswith("FAIL")]
oks    = [r for r in results if r.startswith("OK")]
empties = [r for r in results if r.startswith("EMPTY")]

if oks:
    sys.exit(0)
elif empties:
    sys.exit(2)
else:
    sys.exit(1)
