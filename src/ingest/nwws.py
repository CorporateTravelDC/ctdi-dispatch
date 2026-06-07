"""
ingest.nwws — NOAA Weather Wire (NWWS-OI) subscriber over XMPP.

Joins the NWWS-OI MUC, receives products pushed as group-chat messages, filters
by WFO, and writes alerts via db.upsert_nws_alert — the push twin of the
poller's REST `nws` fetcher.

PARSING SEAM:  NWWS-OI delivers each product as a MUC message carrying an
<x xmlns="nwws-oi"> element; the body is the raw NWS product text (large
products may be compressed/base64 in the payload). parse_product() turns one
product into zero or more upsert_nws_alert kwargs. CAP/VTEC parsing for your
specific product set goes there.
"""
from __future__ import annotations

import asyncio
import logging

from common import db
from ingest import failover
from ingest.config import NwwsConfig

log = logging.getLogger("ingest.nwws")


def parse_product(awips_id: str, source_wfo: str, body: str) -> list[dict]:
    """TODO(operator): map an NWWS-OI product to db.upsert_nws_alert kwargs:
        {alert_id, event_type, area_desc, severity, certainty,
         effective, expires, headline, description}
    Parse VTEC/CAP out of `body`. Return [] for products you don't track."""
    raise NotImplementedError("parse_product: map NWWS-OI product text to upsert_nws_alert fields")


async def run(cfg: NwwsConfig, stop: asyncio.Event, heartbeat: int) -> None:
    """Stay joined to the NWWS-OI MUC until stop is set, heartbeating health."""
    import slixmpp  # lazy import

    class _Client(slixmpp.ClientXMPP):
        def __init__(self):
            super().__init__(cfg.jid, cfg.password)
            self.register_plugin("xep_0045")  # MUC
            self.register_plugin("xep_0199")  # ping / keepalive
            self.add_event_handler("session_start", self._start)
            self.add_event_handler("groupchat_message", self._on_msg)

        async def _start(self, _):
            self.send_presence()
            await self.get_roster()
            self.plugin["xep_0045"].join_muc(cfg.muc, cfg.nick)
            log.info("NWWS-OI joined MUC %s as %s", cfg.muc, cfg.nick)

        def _on_msg(self, msg):
            if msg["mucnick"] == cfg.nick:
                return
            x = msg.xml.find("{nwws-oi}x")
            if x is None:
                return
            awips = x.get("awipsid", "") or x.get("ttaaii", "")
            wfo = x.get("cccc", "")
            if cfg.wfo_filter and wfo not in cfg.wfo_filter:
                return
            body = (x.text or "").strip()
            try:
                for kw in parse_product(awips, wfo, body):
                    db.upsert_nws_alert(**kw)
            except NotImplementedError as e:
                log.warning("NWWS parser seam: %s", e)
            except Exception as e:
                log.error("NWWS product handler error: %s", e)

    backoff = 5
    while not stop.is_set():
        client = _Client()
        beat: asyncio.Task | None = None
        try:
            client.connect((cfg.server, cfg.port))

            async def _beat():
                while not stop.is_set():
                    failover.mark_push_healthy("nws")
                    await asyncio.sleep(heartbeat)

            beat = asyncio.create_task(_beat())
            # slixmpp runs on the same asyncio loop; wait until stop or disconnect.
            disconnected = asyncio.Event()
            client.add_event_handler("disconnected", lambda _e: disconnected.set())
            await _wait_any(stop, disconnected)
            client.disconnect()
            if stop.is_set():
                return
            raise ConnectionError("NWWS-OI disconnected")
        except asyncio.CancelledError:
            client.disconnect()
            raise
        except Exception as e:
            failover.mark_push_down("nws", f"nwws: {e}")
            log.error("NWWS-OI lost (%s); reconnecting in %ds", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
        finally:
            if beat:
                beat.cancel()


async def _wait_any(*events: asyncio.Event) -> None:
    await asyncio.wait(
        [asyncio.create_task(e.wait()) for e in events],
        return_when=asyncio.FIRST_COMPLETED,
    )
