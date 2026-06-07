"""
ingest.config — environment-driven configuration for the push-ingest service.

All values come from the environment (populated via the systemd EnvironmentFile,
i.e. /etc/corporatetraveldc/dispatch-secrets.env + dispatch.env). Nothing secret
is hard-coded here. Each source has an *_ENABLED flag so you can bring sources
online one at a time as you repopulate credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _b(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class SwimFeedConfig:
    """Per-product SWIM/SCDS connection parameters."""
    hosts: list[str]   # comma-separated in env; rotated on reconnect
    port: int
    username: str
    password: str
    tls: bool
    topics: list[str]  # MQTT topic strings to subscribe (wildcards OK)


def _swim_feed(prefix: str) -> "SwimFeedConfig":
    return SwimFeedConfig(
        hosts=_list(f"{prefix}_HOST"),
        port=_i(f"{prefix}_PORT", 8883),
        username=os.getenv(f"{prefix}_USERNAME", ""),
        password=os.getenv(f"{prefix}_PASSWORD", ""),
        tls=_b(f"{prefix}_TLS", True),
        topics=_list(f"{prefix}_TOPIC"),
    )


@dataclass(frozen=True)
class SwimConfig:
    enabled: bool = field(default_factory=lambda: _b("SWIM_ENABLED", False))
    # One SwimFeedConfig per SCDS product subscription.
    stdds: SwimFeedConfig = field(default_factory=lambda: _swim_feed("SWIM_STDDS"))  # TFRs
    sfdps: SwimFeedConfig = field(default_factory=lambda: _swim_feed("SWIM_SFDPS"))  # Flight data
    fns:   SwimFeedConfig = field(default_factory=lambda: _swim_feed("SWIM_FNS"))    # NOTAMs
    tbfm:  SwimFeedConfig = field(default_factory=lambda: _swim_feed("SWIM_TBFM"))  # Flow mgmt
    tfms:  SwimFeedConfig = field(default_factory=lambda: _swim_feed("SWIM_TFMS"))  # NAS/GDPs
    itws:  SwimFeedConfig = field(default_factory=lambda: _swim_feed("SWIM_ITWS"))  # Terminal wx


@dataclass(frozen=True)
class NwwsConfig:
    enabled: bool = field(default_factory=lambda: _b("NWWS_ENABLED", False))
    server: str = field(default_factory=lambda: os.getenv("NWWS_XMPP_SERVER", "nwws-oi.weather.gov"))
    port: int = field(default_factory=lambda: _i("NWWS_XMPP_PORT", 5222))
    jid: str = field(default_factory=lambda: os.getenv("NWWS_JID", ""))
    password: str = field(default_factory=lambda: os.getenv("NWWS_PASSWORD", ""))
    muc: str = field(default_factory=lambda: os.getenv("NWWS_MUC", "nwws@conference.nwws-oi.weather.gov"))
    nick: str = field(default_factory=lambda: os.getenv("NWWS_NICK", "corporatetraveldc"))
    # AWIPS product-id prefixes / WFO to keep (others ignored). e.g. "LWX,AKQ,CTP"
    wfo_filter: list[str] = field(default_factory=lambda: _list("NWWS_WFO_FILTER"))


@dataclass(frozen=True)
class AmtrakConfig:
    enabled: bool = field(default_factory=lambda: _b("AMTRAK_ENABLED", True))
    feed_url: str = field(default_factory=lambda: os.getenv("AMTRAK_FEED_URL", ""))
    filter_station: str = field(default_factory=lambda: os.getenv("AMTRAK_FILTER_STATION", "WAS"))
    poll_interval: int = field(default_factory=lambda: _i("AMTRAK_POLL_INTERVAL_SECS", 300))


@dataclass(frozen=True)
class Config:
    swim: SwimConfig = field(default_factory=SwimConfig)
    nwws: NwwsConfig = field(default_factory=NwwsConfig)
    amtrak: AmtrakConfig = field(default_factory=AmtrakConfig)
    nms: "NmsConfig" = field(default_factory=lambda: NmsConfig())
    # How often each healthy source stamps its heartbeat into feed_state.
    heartbeat_interval: int = field(default_factory=lambda: _i("INGEST_HEARTBEAT_INTERVAL_SECS", 30))
    log_level: str = field(default_factory=lambda: os.getenv("INGEST_LOG_LEVEL", "INFO"))


def load() -> Config:
    return Config()


# ── NMS / Solace PubSub+ config (replaces FNS/AMQP) ─────────────────────────

@dataclass(frozen=True)
class NmsFeedConfig:
    """Connection parameters for one NMS/SCDS feed (one Solace VPN)."""
    host: str        # e.g. "tcps://ems2.swim.faa.gov:55443"
    vpn: str         # e.g. "FDPS"
    username: str    # empty string = credentials not yet provisioned
    password: str
    queue_name: str  # pre-provisioned durable exclusive queue name


def _nms_feed(vpn_key: str) -> NmsFeedConfig:
    """Build a NmsFeedConfig from env vars for the given feed key (e.g. 'FDPS')."""
    host = os.getenv("SWIM_NMS_HOST", "tcps://ems2.swim.faa.gov:55443")
    vpn = os.getenv(f"SWIM_NMS_VPN_{vpn_key}", vpn_key)
    username = os.getenv(f"SWIM_NMS_USER_{vpn_key}", "")
    password = os.getenv(f"SWIM_NMS_PASS_{vpn_key}", "")
    queue_name = os.getenv(f"SWIM_NMS_QUEUE_{vpn_key}", "")
    return NmsFeedConfig(host=host, vpn=vpn, username=username,
                         password=password, queue_name=queue_name)


@dataclass(frozen=True)
class NmsConfig:
    enabled: bool = field(
        default_factory=lambda: _b("SWIM_NMS_ENABLED", True)
    )
    fdps:  NmsFeedConfig = field(default_factory=lambda: _nms_feed("FDPS"))
    stdds: NmsFeedConfig = field(default_factory=lambda: _nms_feed("STDDS"))
    tfms:  NmsFeedConfig = field(default_factory=lambda: _nms_feed("TFMS"))
    aim:   NmsFeedConfig = field(default_factory=lambda: _nms_feed("AIM"))
    tbfm:  NmsFeedConfig = field(default_factory=lambda: _nms_feed("TBFM"))
    itws:  NmsFeedConfig = field(default_factory=lambda: _nms_feed("ITWS"))
