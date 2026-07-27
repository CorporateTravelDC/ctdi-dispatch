"""
aam_weekly_watch -- weekly advanced air mobility (vertiport / eVTOL /
Part 108) watch. Operator directive 2026-07-23: two-part structure --
"here's what matters today" (a maintained infrastructure/regulatory
status block, since there's no API to scrape for "is there a DC
vertiport yet") plus "here's last week's worth of developments" (real
items pulled from the advanced_air_mobility RSS category, filtered to
the last 7 days).

Same-day follow-up directive: ops_brief.py and ep_advance_brief.py each
append this section as a raw post-synthesis appendix -- it never passes
back through either brief's own Ollama call, so a single shared version
would read identically in both. This skill now generates TWO framings in
one Ollama call (split by explicit markers in the response), so ops gets
logistics/ground-transport analysis and ep gets security/counter-UAS
analysis instead of one generic version copied into both.

Schedule: Sunday 09:00 ET (corporatetraveldc-aam-weekly-watch.timer) --
ahead of both the second-brain weekly compile (18:15) and the Dispatch
Desk memo (09:30), so the AAM section is fresh before either downstream
consumer runs.

Output:
  1. /var/lib/corporatetraveldc/aam_weekly_watch_ops.txt and
     aam_weekly_watch_ep.txt -- plain text caches read by
     common.aam_watch.get_aam_watch_section(flavor), which ops_brief.py
     and ep_advance_brief.py each fold into their hourly output. This is
     the actual point of running this weekly instead of hourly: the
     scrape+synthesis happens once, the hourly briefs just read a file.
  2. corporatetraveldc/04-Syntheses/weekly/aam-watch-<week>.md in the
     second-brain vault -- durable, searchable record (both framings),
     same pattern as second_brain_weekly.py.

SR-1: log_usage() in finally block.
SR-2: Exempt -- time-bounded input (last 7 days of RSS), inputs always new.
"""
import logging
import pathlib
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from common import config
from common.llm import generate as llm_generate
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "aam-weekly-watch"
OLLAMA_MODEL = "corporatetraveldc-pi5-osint:latest"

# Reach the runner's RSS API via its Tailscale-bound address. Per
# docs/COMPLIANCE_SECURITY.md (Container Network Isolation): a service
# bound to a real routable, non-loopback host IP (this one, and Ollama's
# Tailscale address) is reachable via normal outbound NAT with NO opt-in
# needed -- host.containers.internal is the WRONG mechanism here (that's
# only for 0.0.0.0-bound services under pasta:--map-gw, and per the same
# doc can never reach a loopback-bound one either). Corrected 2026-07-26:
# a prior edit switched this to host.containers.internal on a mistaken
# analogy to the second-brain WebDAV fix, which made things worse
# (instant connection-refused instead of an intermittent timeout).
# Reverted to the documented-correct direct-IP pattern; the intermittent
# RSS fetch failures are believed to be transient Wi-Fi link congestion
# (see docs/DATA_SOURCES.md / prior SWIM-bandwidth note), addressed below
# with a short retry instead of a URL change.
RUNNER_BASE_URL = "http://100.x.x.x:8001"
RSS_CATEGORY = "advanced_air_mobility"
LOOKBACK_DAYS = 7

_OPS_MARKER = "=== OPS FRAMING ==="
_EP_MARKER = "=== EP FRAMING ==="

# Maintained by hand, not scraped -- there's no reliable API for "is there
# a DC-area vertiport yet." Review and update this block roughly quarterly
# (see ops-brief-addition-aam.md / ep-brief-addition-aam.md, 2026-07-23
# drafts, for the reasoning behind each line). If this goes stale, the
# weekly output will say so via the last-updated stamp below.
_TODAY_STATUS_ASOF = "2026-07-23"
_TODAY_STATUS = """\
DC-area vertiport infrastructure: none operational, none under
construction, none publicly announced for DCA, IAD, or BWI.

FBO positioning to watch: Atlantic Aviation (operates the IAD FBO)
acquired Ferrovial Vertiports Jan 2025, rebranded "VertiPorts by
Atlantic" -- announced target markets are LA, SF Bay Area, NYC/Long
Island, Newark, South Florida, not DC, but their existing IAD presence
makes them the most likely first mover into this market. Signature
Aviation (operates both DCA and IAD FBOs) has a similar story via its
UrbanV joint venture, also not currently targeting DC.

Regulatory track: Part 108 (powered-lift commercial operations) remains
in NPRM, no final rule yet. A Part 108 final rule is the closest thing
to a starting gun for DC-area vertiport siting -- watch the Federal
Register for FAA-2023-1275 docket action specifically.
"""

SYSTEM_PROMPT = f"""You are writing the weekly Advanced Air Mobility watch
section for an executive dispatch platform serving a DC-area chauffeur
and executive-protection business. You will be given a maintained
"current status" block and a list of this week's raw RSS headlines from
AAM/vertiport/UAS trade press.

Produce TWO separate versions back to back, each with the same two
labeled sub-sections (WHAT MATTERS TODAY / THIS WEEK'S DEVELOPMENTS),
but different analytical framing in THIS WEEK'S DEVELOPMENTS. Use these
exact section markers, each on its own line, in this exact order:

{_OPS_MARKER}
WHAT MATTERS TODAY: a tight 2-4 sentence summary of the current status
block, in plain operational language.
THIS WEEK'S DEVELOPMENTS: 3-6 sentences focused on logistics and
ground-transport relevance -- route planning, ground infrastructure
timing, airspace advisories that could affect existing chauffeur
operations near DCA/IAD/BWI. If nothing this week is DC-area-relevant,
say so plainly rather than manufacturing significance.

{_EP_MARKER}
WHAT MATTERS TODAY: the same status summary, in plain operational
language.
THIS WEEK'S DEVELOPMENTS: 3-6 sentences focused on the executive-
protection and security angle -- new low-altitude air traffic as a
surveillance or access consideration, counter-UAS relevance, VIP
movement exposure, or security-adjacent regulatory activity. If nothing
this week is EP-relevant, say so plainly rather than manufacturing a
security angle that isn't there.

Plain text within each section, no markdown headers beyond the labels
above, no filler."""


def _fetch_week_items() -> list[dict]:
    """Pull the advanced_air_mobility RSS category and filter to the last
    LOOKBACK_DAYS days. Returns [] on any fetch failure -- this is a
    weekly watch, not a critical feed; missing this week's items degrades
    gracefully to the status block alone."""
    # Retry once after a short backoff: this same-host call has shown
    # transient connection-refused/timeout failures believed to be Wi-Fi
    # link congestion on this Pi (see docs/DATA_SOURCES.md), not a broken
    # endpoint -- a single 15s attempt was failing the whole weekly run
    # over what is often a momentary blip. Corrected 2026-07-26 alongside
    # reverting a mistaken host.containers.internal URL change.
    items = None
    last_err = None
    for attempt in range(2):
        try:
            resp = httpx.get(
                f"{RUNNER_BASE_URL}/api/rss",
                params={"category": RSS_CATEGORY, "limit": 100},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items", [])
            break
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(3)
    if items is None:
        log.warning("%s: RSS fetch failed after retry: %s", SKILL_NAME, last_err)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    recent = []
    for it in items:
        pub_raw = it.get("pubDate") or it.get("published") or ""
        pub_dt = None
        try:
            if pub_raw[:4].isdigit():
                pub_dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
            else:
                pub_dt = parsedate_to_datetime(pub_raw)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pub_dt = None
        # Keep undated items too (better to include than silently drop --
        # some feeds omit pubDate on aggregator-normalized entries) but
        # sort them last.
        if pub_dt is None or pub_dt >= cutoff:
            recent.append(it)
    return recent


def _week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _split_framings(raw: str) -> tuple[str, str]:
    """Split the Ollama response on the OPS/EP markers. Returns
    (ops_text, ep_text), each stripped of the marker line itself. If the
    model didn't follow the marker format, both come back as the full raw
    text -- degraded (duplicated, not blank) rather than losing content."""
    ops_idx = raw.find(_OPS_MARKER)
    ep_idx = raw.find(_EP_MARKER)
    if ops_idx == -1 or ep_idx == -1 or ep_idx < ops_idx:
        log.warning("%s: response didn't contain expected framing markers -- "
                     "using full text for both flavors", SKILL_NAME)
        return raw.strip(), raw.strip()
    ops_text = raw[ops_idx + len(_OPS_MARKER):ep_idx].strip()
    ep_text = raw[ep_idx + len(_EP_MARKER):].strip()
    return ops_text, ep_text


def main() -> None:
    status = "error"
    today = date.today()

    try:
        items = _fetch_week_items()
        headline_block = "\n".join(
            f"- {it.get('title', '').strip()} ({it.get('source', it.get('feed', 'unknown'))})"
            for it in items
        ) or "(no items in the last 7 days -- quiet week for this category)"

        prompt = (
            f"CURRENT STATUS (as of {_TODAY_STATUS_ASOF}):\n{_TODAY_STATUS}\n\n"
            f"THIS WEEK'S RAW HEADLINES ({len(items)} items, "
            f"last {LOOKBACK_DAYS} days):\n{headline_block}"
        )

        ollama_result = llm_generate(
            system=SYSTEM_PROMPT, prompt=prompt,
            ollama_model=OLLAMA_MODEL, max_tokens=700, temperature=0.25,
            # Explicit timeout added 2026-07-26: this call legitimately runs
            # ~5-6 minutes (300-360s observed 2026-07-23 live test), but was
            # silently inheriting the shared OLLAMA_TIMEOUT=60s tuned for a
            # completely different, much faster skill chain -- every real
            # run was failing well before Ollama could finish, falling back
            # to raw-headline text with no visible error. 500s gives
            # headroom under the container's TimeoutStartSec=950.
            timeout=500,
        )
        if ollama_result:
            gated = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            ops_synthesis, ep_synthesis = _split_framings(gated)
            status = "ok"
            log.info("%s: split synthesis generated via Ollama/%s (%d items)",
                     SKILL_NAME, OLLAMA_MODEL, len(items))
        else:
            fallback = (
                "WHAT MATTERS TODAY:\n" + _TODAY_STATUS.strip() +
                "\n\nTHIS WEEK'S DEVELOPMENTS (Ollama unavailable -- raw headlines):\n" +
                headline_block
            )
            ops_synthesis = ep_synthesis = fallback
            status = "fallback"
            log.info("%s: Ollama unavailable -- using raw headline fallback for both flavors",
                     SKILL_NAME)

        week_label = _week_label(today)
        generated_at = datetime.now(timezone.utc).isoformat()
        header = f"ADVANCED AIR MOBILITY WATCH -- {week_label} (generated {generated_at})\n\n"
        ops_full = header + ops_synthesis.strip() + "\n"
        ep_full = header + ep_synthesis.strip() + "\n"

        # 1. Caches for the hourly briefs (common.aam_watch reads these).
        state = pathlib.Path(config.state_dir())
        state.mkdir(parents=True, exist_ok=True)
        (state / "aam_weekly_watch_ops.txt").write_text(ops_full)
        (state / "aam_weekly_watch_ep.txt").write_text(ep_full)
        # Keep the legacy combined filename too (harmless, and it's the
        # fallback common.aam_watch reaches for if a flavor cache is
        # somehow missing) -- ops framing as the default single version.
        (state / "aam_weekly_watch.txt").write_text(ops_full)
        log.info("%s: wrote ops + ep caches for hourly briefs", SKILL_NAME)

        # 2. Durable copy in the second-brain vault -- both framings.
        frontmatter = (
            "---\n"
            f"week: {week_label}\n"
            "ingest_method: aam-weekly-watch\n"
            f"generated_at: {generated_at}\n"
            f"rss_items: {len(items)}\n"
            "---\n\n"
        )
        note = (
            frontmatter
            + f"# Advanced Air Mobility Watch — {week_label}\n\n"
            + "## Ops framing\n\n" + ops_synthesis.strip() + "\n\n"
            + "## EP framing\n\n" + ep_synthesis.strip() + "\n"
        )
        rel_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/weekly/aam-watch-{week_label}.md"
        webdav_client.put(rel_path, note)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"AAM Watch — {week_label}", content=note,
            tags="weekly,aam,vertiport,evtol,synthesis,auto",
            ingest_method="aam-weekly-watch",
        )
        conn.close()
        log.info("%s: wrote %s (status=%s)", SKILL_NAME, rel_path, status)

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, OLLAMA_MODEL if status == "ok" else "deterministic",
                   0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
