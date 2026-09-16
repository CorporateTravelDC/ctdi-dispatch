"""
common/ground_news_client.py -- authenticated Ground News client.

Credential model (operator directive, 2026-09):

  - This is NOT a scraping-as-a-service or resale integration. Every
    deployment authenticates with the OPERATOR'S OWN Ground News account
    credentials (or a session token derived from it), exactly the same
    trust model this repo already uses for FAA SWIM/NMS (per-operator
    username/password/queue) and NWWS-OI (per-operator JID/password) --
    see docs/DATA_SOURCES.md. Nobody's credentials or fetched content are
    shared across deployments, redistributed, or proxied through a
    third-party scraping vendor.
  - Each self-hosted instance of this platform pulls only the content the
    operator's own account can already see when they're logged in at
    ground.news -- their saved interests/topics, their "My Feed", and
    public Blindspot/bias-rated story listings. Nothing here attempts to
    access another user's account or bypass a paywall/subscription tier
    the operator hasn't paid for themselves.
  - This module intentionally does NOT hardcode a reverse-engineered
    private API contract. Ground News has no published public API (see
    docs/GROUND_NEWS_ACCESS_REQUEST.md) -- the request/response shapes
    below are a clean placeholder interface pending an explicit access
    agreement with Ground News. Filling in _login()/_fetch_my_feed() with
    a real, sanctioned endpoint is the one piece intentionally left for
    after that conversation happens, same posture this codebase already
    takes with EUROCONTROL/JASDAT ("endpoint path and response shape are
    placeholders pending a live sandbox response" -- see
    poller/fetchers/jasdat.py). Do not fill these in by reverse-engineering
    ground.news's private frontend calls without Ground News's sign-off;
    that is exactly the outcome this branch exists to avoid.

Auth modes supported (operator picks ONE via dispatch-secrets.env):

  1. GROUND_NEWS_EMAIL + GROUND_NEWS_PASSWORD
     Standard credential login. Fragile against CAPTCHA/MFA challenges
     that a headless client can't solve -- see NOTE in _login().

  2. GROUND_NEWS_SESSION_TOKEN
     A session/cookie value the operator copies from their own already-
     authenticated browser session (DevTools -> Application -> Cookies,
     or Network tab on an authenticated request). Preferred once Ground
     News confirms which cookie/header actually carries the session --
     avoids re-implementing their login flow (and any MFA/CAPTCHA it
     may include) entirely. This is the same "copy a token out of your
     own already-authenticated session" pattern many personal-RSS /
     personal-API integrations use when a service has no first-class
     API keys.

Either mode is single-operator, non-resold, self-hosted -- consistent
with every other credentialed source in this repo.
"""
from __future__ import annotations

import logging
import time

import requests

from common import config

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 20
# Placeholder base URL -- ground.news publishes no developer API today.
# Do not point this at reverse-engineered private endpoints; see the
# module docstring and docs/GROUND_NEWS_ACCESS_REQUEST.md. Update this
# once Ground News confirms a real, sanctioned base URL/contract.
GROUND_NEWS_BASE_URL = "https://ground.news"

_session_cache: dict[str, tuple[float, requests.Session]] = {}
_SESSION_TTL = 3600  # re-auth hourly rather than holding one session forever


class GroundNewsAuthError(Exception):
    """Raised when neither credential mode is configured, or auth fails."""


def credentials_configured() -> bool:
    """True if the operator has supplied either supported auth mode."""
    return bool(_session_token()) or bool(_email() and _password())


def _email() -> str:
    return config.get("GROUND_NEWS_EMAIL", "")


def _password() -> str:
    return config.get("GROUND_NEWS_PASSWORD", "")


def _session_token() -> str:
    return config.get("GROUND_NEWS_SESSION_TOKEN", "")


def _login(session: requests.Session) -> None:
    """Authenticate `session` using the operator's own credentials.

    NOTE (placeholder -- see module docstring): the real login request
    shape (endpoint, payload fields, CSRF handling, MFA/CAPTCHA
    challenge) is unknown until Ground News confirms a sanctioned access
    path. This raises rather than guessing, so a misconfigured deployment
    fails loudly (awaiting_credentials-style) instead of silently no-op'ing.
    """
    raise GroundNewsAuthError(
        "Ground News credential-login is not yet wired to a confirmed "
        "endpoint -- see docs/GROUND_NEWS_ACCESS_REQUEST.md. Use "
        "GROUND_NEWS_SESSION_TOKEN in the meantime if you already have an "
        "authenticated browser session, or wait for sign-off before "
        "enabling this feed."
    )


def _authenticated_session() -> requests.Session:
    """Return a cached, authenticated requests.Session for the operator's
    own account. Re-authenticates every _SESSION_TTL seconds."""
    cache_key = _session_token() or _email()
    cached = _session_cache.get(cache_key)
    now = time.time()
    if cached and (now - cached[0]) < _SESSION_TTL:
        return cached[1]

    session = requests.Session()
    session.headers["User-Agent"] = "ctdi-dispatch/1.0 (operator-credentialed; self-hosted)"

    token = _session_token()
    if token:
        # Cookie/header name is a placeholder -- confirm the real one with
        # Ground News (or via the operator's own browser devtools) before
        # relying on this in production.
        session.cookies.set("session", token, domain="ground.news")
    else:
        _login(session)

    _session_cache[cache_key] = (now, session)
    return session


def fetch_my_feed(limit: int = 50) -> list[dict]:
    """Fetch the operator's own personalized Ground News feed --
    bias-rated stories from their saved interests/topics.

    Returns a list of normalized items in the SAME shape the rest of this
    platform's RSS pipeline already uses (see runner/main.py::_parse_rss):

        {"title": str, "link": str, "summary": str, "published": iso8601,
         "source": "Ground News"}

    plus Ground News-specific extras carried alongside (not required by
    the generic RSS consumers, but useful to anything bias-aware):

        {"bias_distribution": {"left": int, "center": int, "right": int},
         "factuality": str | None, "blindspot": bool}

    Raises GroundNewsAuthError if no credentials are configured, or
    requests.RequestException on a transport/HTTP failure -- callers
    (poller/fetchers/ground_news.py) are responsible for translating
    those into the platform's usual awaiting_credentials / error feed
    states, same as every other fetcher in this repo.
    """
    if not credentials_configured():
        raise GroundNewsAuthError("awaiting_credentials")

    session = _authenticated_session()

    # Placeholder request -- real path/params pending Ground News sign-off.
    # Kept as a single, obvious call site so wiring the real contract in
    # later is a one-function change, not a re-architecture.
    resp = session.get(
        f"{GROUND_NEWS_BASE_URL}/api/my-feed",
        params={"limit": limit},
        timeout=FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()

    items: list[dict] = []
    for story in payload.get("stories", []):
        sources = story.get("sources", {})
        items.append({
            "title": story.get("title", ""),
            "link": story.get("url", ""),
            "summary": (story.get("summary") or "")[:280],
            "published": story.get("published_at", ""),
            "source": "Ground News",
            "bias_distribution": {
                "left": sources.get("left", 0),
                "center": sources.get("center", 0),
                "right": sources.get("right", 0),
            },
            "factuality": story.get("factuality"),
            "blindspot": bool(story.get("is_blindspot", False)),
        })
    return items[:limit]
