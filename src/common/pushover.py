"""
common.pushover — Pushover Emergency co-fire for max-priority dispatch alerts.

All priority-5 (hot) ntfy pushes are co-fired here as Pushover Emergency
(priority 2), which retries every PUSHOVER_RETRY_SEC seconds until
acknowledged or PUSHOVER_EXPIRE_SEC total seconds elapse.

If a callback number is configured, it is appended to the message body
so the recipient has a fallback contact if they see the alert but cannot
acknowledge from the app.

Environment (from dispatch-secrets.env):
  PUSHOVER_TOKEN           — Application API token (from pushover.net dashboard)
  PUSHOVER_USER_KEY        — Recipient user or group key
  PUSHOVER_CALLBACK_NUMBER — Phone number appended as fallback (optional)
  PUSHOVER_RETRY_SEC       — Retry interval in seconds (default 60, min 30)
  PUSHOVER_EXPIRE_SEC      — Total window before giving up (default 120 = 2 cycles)
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

_API     = "https://api.pushover.net/1/messages.json"
_TOKEN   = os.environ.get("PUSHOVER_TOKEN", "")
_USER    = os.environ.get("PUSHOVER_USER_KEY", "")
_CBNUM   = os.environ.get("PUSHOVER_CALLBACK_NUMBER", "")
_RETRY   = max(30, int(os.environ.get("PUSHOVER_RETRY_SEC", "60")))
_EXPIRE  = max(_RETRY, int(os.environ.get("PUSHOVER_EXPIRE_SEC", "120")))


def enabled() -> bool:
    """Return True if Pushover is configured and will attempt delivery."""
    return bool(_TOKEN and _USER)


def send_emergency(title: str, message: str) -> bool:
    """
    Fire a Pushover Emergency notification co-fired with ntfy on hot alerts.

    Retries every _RETRY seconds up to _EXPIRE seconds total.
    Appends callback number to body when PUSHOVER_CALLBACK_NUMBER is set.
    Returns True on HTTP 2xx with status==1.
    """
    if not enabled():
        log.debug("pushover: not configured — skipping (set PUSHOVER_TOKEN + PUSHOVER_USER_KEY)")
        return False

    body = message
    if _CBNUM:
        body += f"\n\nNot acknowledged? Call: {_CBNUM}"

    try:
        resp = requests.post(
            _API,
            data={
                "token":    _TOKEN,
                "user":     _USER,
                "title":    title,
                "message":  body,
                "priority": 2,       # Emergency — retries until acknowledged
                "retry":    _RETRY,
                "expire":   _EXPIRE,
                "sound":    "siren",
            },
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == 1:
            log.info("pushover: emergency sent receipt=%s retry=%ds expire=%ds",
                     result.get("receipt", "?"), _RETRY, _EXPIRE)
            return True
        log.error("pushover: API error status=%s errors=%s",
                  result.get("status"), result.get("errors"))
        return False
    except Exception as exc:
        log.error("pushover: send_emergency failed: %s", exc)
        return False
