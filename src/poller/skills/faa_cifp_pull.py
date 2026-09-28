"""
faa_cifp_pull -- automated weekly pull of the current FAA CIFP (Coded
Instrument Flight Procedures, ARINC 424) cycle file from AeroNav's public
download page.

Genuinely public, no login required -- unlike LADD's replacement source,
see the note below. The download page always links the CURRENT cycle's
zip under a filename that embeds the cycle's effective date
(CIFP_YYMMDD.zip, YYMMDD always a Thursday, 28-day AIRAC cadence) -- there
is no static/un-dated URL to hardcode, so this scrapes the download page
for the current link on every run rather than guessing the next cycle's
date.

2026-09-05: built alongside a LADD investigation prompted by the operator
believing both files came from "the same S3 bucket" used last time.
Neither actually does -- both are FAA's own Akamai-fronted CDN
(aeronav.faa.gov / registry.faa.gov), no S3 anywhere in either response's
headers. LADD's public registry.faa.gov endpoint is confirmed dead (was
documented as a 302, now a 503) -- FAA's current distribution for LADD is
the ADX (Aeronautical Data Exchange) portal at www.adx.faa.gov, restricted
to FAA employees/contractors/DoD personnel behind a MyAccess Workforce /
login.gov SSO login. The operator confirmed their own ADX access is
exactly that interactive login.gov flow, not an API/service credential,
and it isn't configured on this box -- so LADD automation is NOT buildable
right now (see CLAUDE.md "Known bad"; faa_registry.py's _FAA_LADD_URL
comment covers the existing non-fatal handling of that dead endpoint).
CIFP has no such restriction; it's a genuinely public zip.

Idempotent: tracks the last-pulled cycle in skill-state and skips the
download entirely if the current cycle matches what's already on disk --
this only actually changes once every 28 days; the weekly timer check
itself is cheap (one HTML page fetch) even on a no-op week.

No parser consumes this file yet (see tbfm_parser.py's one-time manual
CIFP cross-check) -- this skill's scope is just keeping a current copy
pulled and on disk, ready for whenever that changes.

SR-2: exempt -- deterministic, no LLM call.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from common import ntfy_push
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "faa-cifp-pull"

_DOWNLOAD_PAGE = (
    "https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/"
)
# Matches the current cycle's link, e.g.
# https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_260903.zip -- "Upload_313-d"
# is AeroNav's own folder name, confirmed stable across at least the two
# cycles checked live 2026-09-05; the regex doesn't assume it stays that
# exact string, only that it's a path segment before /cifp/CIFP_<date>.zip.
_CIFP_LINK_RE = re.compile(
    r'href="(https://aeronav\.faa\.gov/[^"]+?/cifp/CIFP_(\d{6})\.zip)"'
)

_STATE_DIR = "/var/lib/corporatetraveldc/skill-state"
_STATE_FILE = os.path.join(_STATE_DIR, "faa-cifp-pull.json")
_DATA_DIR = "/var/lib/corporatetraveldc/faa-cifp"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ),
}

_MAX_RETRIES = 3
_RETRY_BACKOFF_S = 5


def _load_state() -> dict:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def current_cifp_zip() -> tuple[str, str] | None:
    """Public accessor for faa_cifp_parse.py: returns (path, cycle) for the
    currently-downloaded CIFP zip, or None if nothing's been pulled yet."""
    state = _load_state()
    path = state.get("last_path")
    cycle = state.get("last_cycle")
    if not path or not cycle or not os.path.exists(path):
        return None
    return path, cycle


def _save_state(state: dict) -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, _STATE_FILE)


def _find_current_cifp() -> tuple[str, str]:
    """Returns (url, cycle) for the current CIFP zip linked on the AeroNav
    download page. Raises on failure -- there is no fallback URL to guess
    if the page layout changes."""
    resp = requests.get(_DOWNLOAD_PAGE, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    m = _CIFP_LINK_RE.search(resp.text)
    if not m:
        raise RuntimeError(
            "no CIFP_*.zip link found on AeroNav download page -- "
            "page layout may have changed"
        )
    return m.group(1), m.group(2)


def _download(url: str) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=120)
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.RequestException as e:
            last_exc = e
            log.warning("faa-cifp-pull: download attempt %d/%d failed: %s",
                        attempt, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_S * attempt)
    assert last_exc is not None
    raise last_exc


def fetch_faa_cifp() -> dict:
    """Pull the current CIFP cycle if it's new. Returns a stats dict."""
    state = _load_state()
    url, cycle = _find_current_cifp()

    dest = os.path.join(_DATA_DIR, f"CIFP_{cycle}.zip")
    if state.get("last_cycle") == cycle and os.path.exists(dest):
        log.info("faa-cifp-pull: cycle %s already current, skipping", cycle)
        return {"ok": True, "cycle": cycle, "changed": False}

    log.info("faa-cifp-pull: new cycle %s (prior: %s) -- downloading %s",
              cycle, state.get("last_cycle"), url)
    content = _download(url)

    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, dest)

    # Only the current cycle is kept on disk -- prior cycles are stale
    # AIRAC data with no ongoing use once superseded.
    prior_path = state.get("last_path")
    if prior_path and prior_path != dest and os.path.exists(prior_path):
        os.remove(prior_path)

    state["last_cycle"] = cycle
    state["last_path"] = dest
    state["last_pulled_epoch"] = time.time()
    _save_state(state)

    ntfy_push.send(
        "ops-health",
        f"Cycle {cycle} downloaded ({len(content) / 1_048_576:.1f} MB) -> {dest}",
        title="FAA CIFP: new cycle pulled",
        priority=2, tags="airplane",
    )
    return {"ok": True, "cycle": cycle, "changed": True, "bytes": len(content)}


def main() -> None:
    status = "error"
    try:
        stats = fetch_faa_cifp()
        status = "success" if stats.get("ok") else "error"
        log.info("faa-cifp-pull: %s", stats)
    except Exception as e:
        log.error("faa-cifp-pull failed: %s", e)
        ntfy_push.send(
            "ops-health",
            str(e),
            title="FAA CIFP pull FAILED",
            priority=3, tags="warning",
        )
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
