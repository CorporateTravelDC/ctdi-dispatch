"""
nms_client.py — FAA NMS initial load + SWIM delta subscription stub.

Handles:
  - Auth to the NMS REST API (BASIC / HMAC-SHA256 / TOKEN, switchable via FAA_NMS_AUTH_METHOD)
  - Initial load signed-URL fetch
  - AIXM gz download + decompress
  - AIXM stub parser (TODOs for full parse)
  - Solace PubSub+ delta subscription stub

Environment variables (all injected from dispatch-secrets.env):
  FAA_NMS_AUTH_METHOD   BASIC | HMAC | TOKEN
  FAA_NMS_API_KEY
  FAA_NMS_API_SECRET
  FAA_NMS_BASE_URL      e.g. https://nms.swim.faa.gov

Never log credentials. Any exception that re-surfaces key/secret material
must be caught here before it reaches the root logger.

Deploy to: /opt/corporatetraveldc/src/ingest/nms_client.py
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import logging
import os
import time
from base64 import b64encode
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers — never surface values in log lines
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise EnvironmentError(f"Required env var {name!r} is not set")
    return val


def _cfg() -> dict[str, str]:
    return {
        "method":   os.environ.get("FAA_NMS_AUTH_METHOD", "BASIC").upper(),
        "key":      _require_env("FAA_NMS_API_KEY"),
        "secret":   _require_env("FAA_NMS_API_SECRET"),
        "base_url": os.environ.get("FAA_NMS_BASE_URL", "https://nms.swim.faa.gov"),
    }


# ---------------------------------------------------------------------------
# Auth builders
# ---------------------------------------------------------------------------

def _auth_basic(cfg: dict) -> dict:
    """HTTP Basic auth header."""
    token = b64encode(f"{cfg['key']}:{cfg['secret']}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _auth_hmac(cfg: dict, method: str, path: str) -> dict:
    """
    HMAC-SHA256 auth header — common FAA SWIM pattern.
    Exact signing string and header names are TODO pending FAA NMS API docs.
    Stub matches a plausible structure; adjust once the spec arrives.
    """
    ts = str(int(time.time()))
    signing_string = f"{method.upper()}\n{path}\n{ts}"
    sig = hmac.new(
        cfg["secret"].encode(),
        signing_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-NMS-ApiKey":    cfg["key"],
        "X-NMS-Timestamp": ts,
        "X-NMS-Signature": sig,
    }


def _auth_token(cfg: dict) -> dict:
    """
    Bearer token auth — stub for OAuth2/token-endpoint flow.
    TODO: exchange key+secret for a short-lived bearer token if FAA uses this model.
    """
    # Placeholder: treat key as a pre-issued bearer token.
    # Replace with a real token-exchange POST when spec is confirmed.
    return {"Authorization": f"Bearer {cfg['key']}"}


def _build_headers(cfg: dict, method: str = "GET", path: str = "/") -> dict:
    auth_method = cfg["method"]
    if auth_method == "BASIC":
        return _auth_basic(cfg)
    elif auth_method == "HMAC":
        return _auth_hmac(cfg, method, path)
    elif auth_method == "TOKEN":
        return _auth_token(cfg)
    else:
        raise ValueError(f"Unknown FAA_NMS_AUTH_METHOD: {auth_method!r}")


# ---------------------------------------------------------------------------
# Initial load: fetch signed URL
# ---------------------------------------------------------------------------

INITIAL_LOAD_PATH = "/api/v1/initialLoad"   # TODO: confirm exact path from FAA NMS docs


def get_initial_load_url(session: requests.Session, cfg: dict) -> str:
    """
    Call NMS REST endpoint to obtain the signed GCS URL for the AIXM initial load.

    Expected response shape:
        {"status": "Success", "data": {"url": "<signed-url>"}}

    The signed URL expires in ~300 seconds — caller must download immediately.
    """
    url = urljoin(cfg["base_url"], INITIAL_LOAD_PATH)
    headers = _build_headers(cfg, method="GET", path=INITIAL_LOAD_PATH)

    try:
        resp = session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        # Scrub auth headers from exception before re-raising
        raise RuntimeError(
            f"NMS initial load request failed: {exc.response.status_code} {exc.response.reason}"
        ) from None

    body = resp.json()
    if body.get("status") != "Success":
        raise RuntimeError(f"NMS returned non-success status: {body.get('status')!r}")

    signed_url: str = body["data"]["url"]
    log.info("NMS initial load signed URL obtained (expires ~300 s)")
    return signed_url


# ---------------------------------------------------------------------------
# Download + decompress AIXM gz
# ---------------------------------------------------------------------------

def download_initial_load(signed_url: str, session: requests.Session) -> bytes:
    """
    Download the gzipped AIXM from the signed GCS URL.
    Returns raw decompressed XML bytes.
    The signed URL is credential-bearing; log only its prefix for traceability.
    """
    log.info("Downloading NMS initial load (URL prefix: %s...)", signed_url[:60])
    resp = session.get(signed_url, timeout=120, stream=True)
    resp.raise_for_status()

    chunks = []
    for chunk in resp.iter_content(chunk_size=65536):
        chunks.append(chunk)
    compressed = b"".join(chunks)

    log.info("Downloaded %.1f MB compressed", len(compressed) / 1_048_576)
    xml_bytes = gzip.decompress(compressed)
    log.info("Decompressed to %.1f MB", len(xml_bytes) / 1_048_576)
    return xml_bytes


# ---------------------------------------------------------------------------
# AIXM stub parser
# ---------------------------------------------------------------------------

# AIXM 5.1 namespace map — extend as needed
_NS = {
    "aixm":    "http://www.aixm.aero/schema/5.1",
    "gml":     "http://www.opengis.net/gml/3.2",
    "xlink":   "http://www.w3.org/1999/xlink",
    "message": "http://www.aixm.aero/schema/5.1/message",
    "event":   "http://www.aixm.aero/schema/5.1/event",
}


def parse_aixm_initial_load(xml_bytes: bytes) -> dict[str, Any]:
    """
    Stub AIXM parser — extracts a minimal inventory from the initial load.

    TODO (full parse task):
      - AirportHeliport: ICAO id, name, lat/lon, elevation, runways
      - Airspace: type (FIR/UIR/TMA/CTA/CTR/...), designator, geometry
      - Route + RouteSegment: airways with upper/lower limits
      - Navaid (VOR/DME/NDB/TACAN/VORTAC): ident, freq, lat/lon
      - DesignatedPoint (fix): ident, lat/lon
      - RunwayDirection: true bearing, ILS/LOC freq
      - Unit (ATC facility): designator, type (ARTCC/TRACON/TWR/GND/DEL)
      - AirTrafficManagementService: maps units to airspace
    """
    log.info("Parsing AIXM initial load (%d bytes XML)", len(xml_bytes))

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"AIXM XML parse error: {exc}") from exc

    inventory: dict[str, Any] = {
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "airports":  [],   # TODO: fill from AirportHeliport members
        "airspaces": [],   # TODO: fill from Airspace members
        "routes":    [],   # TODO: fill from Route members
        "navaids":   [],   # TODO: fill from Navaid members
        "fixes":     [],   # TODO: fill from DesignatedPoint members
        "atc_units": [],   # TODO: fill from Unit members
    }

    # Count top-level members as a sanity check
    members = root.findall(".//{http://www.aixm.aero/schema/5.1/message}hasMember", _NS)
    if not members:
        members = root.findall(".//hasMember")
    inventory["member_count"] = len(members)
    log.info("AIXM member count: %d (stub — data not yet extracted)", len(members))

    # TODO: iterate members, branch on child tag, extract into inventory lists
    # Example structure to implement:
    #
    # for member in members:
    #     child = list(member)[0] if len(member) else None
    #     if child is None:
    #         continue
    #     tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
    #     if tag == "AirportHeliport":
    #         inventory["airports"].append(_parse_airport(child))
    #     elif tag == "Airspace":
    #         inventory["airspaces"].append(_parse_airspace(child))
    #     # ... etc.

    return inventory


# ---------------------------------------------------------------------------
# Solace PubSub+ delta subscription stub
# ---------------------------------------------------------------------------

SWIM_BROKER = "tcps://ems2.swim.faa.gov:55443"

# Topic strings are placeholders; confirm with FAA SWIM portal after provisioning
SWIM_TOPICS = {
    "SFDPS": "SFDPS/>",   # FDPS VPN — en-route flight data (POTUS/VIP detection)
    "STDDS": "STDDS/>",   # Terminal tracks at KDCA/KIAD/KBWI
    "TFMS":  "TFMS/>",    # Traffic flow management — ground stops, GDP
}


def connect_solace(cfg: dict) -> None:
    """
    Stub for Solace PubSub+ delta subscription.

    TODO (SWIM integration task):
      1. Import solace.messaging from solace-pubsubplus
      2. Build MessagingService with:
           transport: SWIM_BROKER
           auth: BasicUserNamePassword(cfg["key"], cfg["secret"])
             OR client-cert if FAA requires PKI — check SWIM portal
      3. Connect and create a PersistentMessageReceiver or DirectMessageReceiver
         for each topic in SWIM_TOPICS
      4. Wire received messages into the ingest container's feed_state heartbeat
         and the existing SQLite WAL writer (same pattern as poller fallback)
      5. Handle reconnect with exponential backoff; heartbeat ages out after 90 s
         so poller resumes automatically on disconnect

    Auth note: SWIM PubSub+ typically uses the same key/secret as the REST API
    but sometimes requires a separate VPN credential. Confirm on the SWIM portal.
    """
    log.warning(
        "connect_solace() called but Solace integration is not yet implemented. "
        "Poller fallback will handle feeds until this stub is completed."
    )
    raise NotImplementedError("Solace PubSub+ integration stub — see TODOs above")


# ---------------------------------------------------------------------------
# Top-level entry point called by ingest container startup
# ---------------------------------------------------------------------------

def run_initial_load() -> dict[str, Any]:
    """
    Full initial load sequence:
      1. Obtain signed GCS URL from NMS REST API
      2. Download + decompress AIXM gz (must complete within ~300 s of step 1)
      3. Parse AIXM into inventory dict
      4. Return inventory for caller to persist to SQLite / feed_state

    Raises RuntimeError on any unrecoverable failure (caller should mark
    the feed as errored in feed_state and let the poller handle fallback).
    """
    cfg = _cfg()

    with requests.Session() as session:
        session.headers.update({"User-Agent": "corporatetraveldc-ingest/1.0"})

        signed_url = get_initial_load_url(session, cfg)
        xml_bytes = download_initial_load(signed_url, session)

    inventory = parse_aixm_initial_load(xml_bytes)
    log.info(
        "NMS initial load complete: %d members parsed at %s",
        inventory["member_count"],
        inventory["parsed_at"],
    )
    return inventory
