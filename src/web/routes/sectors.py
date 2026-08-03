"""
web.routes.sectors — sector/corridor alert-coalescing query + silence API.

Built 2026-07-20 alongside shared.sector_coalesce (Task #20). Exposes both
directions the operator asked for: query by sector (what's happening in
New York/Atlanta/etc. right now) and the reverse, by feed (which sectors
is this feed/incident-type touching). Also exposes the opt-in silence
toggles so an operator (or another operator) can quiet routine traffic
per-sector or per-feed without losing genuine escalating-trend visibility.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from shared.sector_coalesce import (
    get_sector_summary,
    get_feed_summary,
    set_sector_silence,
    set_feed_silence,
    set_topic_throttle,
    set_topic_enabled,
    set_topic_sanitize,
    get_topic_settings,
)

router = APIRouter()


@router.get("/api/v1/sectors")
def get_sectors():
    """
    Per-sector rollup: rolling 15-min window count, prior-window count,
    whether the sector is currently 'escalating' (>=3x the prior window,
    with a floor so single events never trivially escalate), silence
    state, and which feeds have contributed events to it recently.
    """
    return {"sectors": get_sector_summary()}


@router.get("/api/v1/sectors/by-feed")
def get_sectors_by_feed():
    """
    Reverse view: per-feed rollup of which sectors it's touching right now
    and how many events each, plus feed-level silence state.
    """
    return {"feeds": get_feed_summary()}


class SilenceRequest(BaseModel):
    silenced: bool


@router.post("/api/v1/sectors/{sector}/silence")
def silence_sector(sector: str, body: SilenceRequest):
    """Opt a sector in/out of alert silencing. Silencing suppresses ntfy
    pushes for that sector's routine traffic but does NOT stop counting --
    escalation detection keeps running underneath, so unsilencing later
    shows the true trend rather than a blank slate."""
    set_sector_silence(sector.upper(), body.silenced)
    return {"sector": sector.upper(), "silenced": body.silenced}


@router.post("/api/v1/sectors/feed/{feed_name}/silence")
def silence_feed(feed_name: str, body: SilenceRequest):
    """Opt a feed (e.g. 'tfms', 'tfms_aptc', 'tfms_gadv') in/out of alert
    silencing, independent of sector silencing."""
    set_feed_silence(feed_name, body.silenced)
    return {"feed": feed_name, "silenced": body.silenced}


class ThrottleRequest(BaseModel):
    min_interval_secs: float


class EnableRequest(BaseModel):
    enabled: bool


class SanitizeRequest(BaseModel):
    sanitize: bool


@router.get("/api/v1/sectors/topic/{topic}")
def get_topic(topic: str):
    """Effective throttle/enable/sanitize settings for one literal ntfy
    topic (e.g. 'tfms-zdc', 'tbfm-alerts'), plus seconds since it last
    fired -- for verifying an override actually took effect."""
    return get_topic_settings(topic)


@router.post("/api/v1/sectors/topic/{topic}/throttle")
def throttle_topic(topic: str, body: ThrottleRequest):
    """Override the minimum interval (seconds) between pushes for one
    topic. Independent of every other topic -- the family-wide aggregate
    ('<family>-alerts') and each per-zone topic ('<family>-<zone>') are
    separate topics and must be set separately. Pass <=0 to clear the
    override and revert to the 60s default."""
    set_topic_throttle(topic, body.min_interval_secs)
    return get_topic_settings(topic)


@router.post("/api/v1/sectors/topic/{topic}/enabled")
def enable_topic(topic: str, body: EnableRequest):
    """Turn one topic's pushes on/off entirely, independent of sector/feed
    silencing (which are a separate, broader mechanism) and independent of
    any sibling topic for the same feed."""
    set_topic_enabled(topic, body.enabled)
    return get_topic_settings(topic)


@router.post("/api/v1/sectors/topic/{topic}/sanitize")
def sanitize_topic(topic: str, body: SanitizeRequest):
    """Mark one topic's pushes to be identifier-masked (N-numbers, ICAO
    hex) before firing -- for reusing a real alert stream as a demo/
    reporting source-of-truth without exposing real tail numbers."""
    set_topic_sanitize(topic, body.sanitize)
    return get_topic_settings(topic)
