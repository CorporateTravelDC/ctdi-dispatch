"""
dispatch_desk_memo -- "The Dispatch Desk," a weekly week-in-review memo
spanning the full RSS catalog (all six categories: corporate_intel,
marketing_intel, travel_trends, dc_area, aviation, advanced_air_mobility).
Operator directive 2026-07-23: same content/voice as the hand-drafted
first issue (dispatch-desk-office-memo.md), same weekly cadence as
aam_weekly_watch.py, "week in review" tone -- built to work three ways
at once: internal reference, paste-ready for email, and a future Substack
draft once that's back online.

Schedule: Sunday 09:30 ET (corporatetraveldc-dispatch-desk-memo.timer) --
after aam_weekly_watch (09:00, so this can lean on the same week's AAM
synthesis rather than re-deriving it) and well ahead of the second-brain
weekly compile (18:15), so Ollama jobs don't stack.

Output:
  1. /var/lib/corporatetraveldc/dispatch_desk_latest.txt -- plain text,
     paste-ready for email as-is.
  2. corporatetraveldc/04-Syntheses/weekly/dispatch-desk-<week>.md in the
     second-brain vault -- durable record.
  3. Low-priority ntfy ping to "dispatch" noting a new issue is ready
     (not the memo body itself -- that's meant to be read in one sitting,
     not skimmed off a phone lock screen).

SR-1: log_usage() in finally block.
SR-2: Exempt -- time-bounded input (last 7 days across all categories),
      inputs always new.
"""
import logging
import pathlib
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from common import config, ntfy_push as _ntfy
from common.llm import generate as llm_generate
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "dispatch-desk-memo"
OLLAMA_MODEL = "corporatetraveldc-pi5-dispatch-desk:latest"

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
LOOKBACK_DAYS = 7
CATEGORIES = [
    "corporate_intel", "marketing_intel", "travel_trends",
    "dc_area", "aviation", "advanced_air_mobility",
]

# Style exemplar embedded directly in the system prompt (few-shot) so the
# LLM's output actually lands in the drafted voice rather than drifting
# toward a generic news-roundup tone. This is the operator-approved first
# issue, 2026-07-23 -- content/voice explicitly signed off, only the
# cadence (weekly, automated) changed after that approval.
_STYLE_EXEMPLAR = """\
Every hour, the dispatch platform pulls from twenty-one sources across \
aviation, ground transport, hospitality, DC-area local news, and \
advanced air mobility. Most of that stays in the background, feeding the \
operational brief. This is the other half: the slower-moving stuff worth \
a person actually reading, once a week, in one sitting.

## This week in the air

**Advanced air mobility is moving faster than DC infrastructure.** \
Farnborough 2026 produced two notable eVTOL announcements: Joby and \
Virgin Atlantic confirmed their first planned UK routes, and Archer \
launched "Halo," a commercial variant of its Thunder logistics drone. \
Neither touches our market directly, but they're both signal -- the \
commercial eVTOL sector is past the demo phase and into route planning, \
which is exactly the phase that precedes site announcements. \
(Urban Air Mobility News)

**Still nothing in DC.** No vertiport is operational, under \
construction, or publicly announced for DCA, IAD, or BWI. [...]

## The quiet story

Nobody in the aviation or AAM trade press is writing about the \
executive-protection angle on any of this [...] Worth remembering the \
next time this memo needs a lead story of its own.
"""

SYSTEM_PROMPT = f"""You are writing "The Dispatch Desk," a weekly
week-in-review memo for [operator LLC], a boutique DC-area
executive services firm (automotive detailing, brand strategy,
executive chauffeur transportation, IT security). You will be given
this week's raw headlines across six categories: corporate intel,
marketing/hospitality intel, travel trends, DC-area local news,
aviation, and advanced air mobility.

Write in this exact voice -- here is a real prior issue as your style
guide, match its register closely:

---EXEMPLAR START---
{_STYLE_EXEMPLAR}
---EXEMPLAR END---

Structure: a one-paragraph opener, then 2-4 themed sections (use your
judgment on section titles based on what's actually notable this week --
don't force one section per category if the week's real story cuts
across categories), then a short closing observation. Markdown headers
(##) for sections, bold for the first strong claim in a paragraph, cite
the source outlet in parentheses after specific claims. One continuous
read, not a bulleted data dump -- this is meant to be read in one
sitting, then filed. If a week is genuinely quiet, say so rather than
manufacturing significance. Under 700 words total."""


def _fetch_category(category: str) -> list[dict]:
    # Retry once after a short backoff -- see aam_weekly_watch.py's
    # _fetch_week_items() for the same fix and reasoning (2026-07-26).
    items = None
    last_err = None
    for attempt in range(2):
        try:
            resp = httpx.get(
                f"{RUNNER_BASE_URL}/api/rss",
                params={"category": category, "limit": 50},
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
        e = last_err
        log.warning("%s: RSS fetch failed for %s: %s", SKILL_NAME, category, e)
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
        if pub_dt is None or pub_dt >= cutoff:
            recent.append(it)
    return recent[:15]  # cap per category so the prompt doesn't balloon


def _week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def main() -> None:
    status = "error"
    today = date.today()

    try:
        by_category: dict[str, list[dict]] = {}
        total_items = 0
        for cat in CATEGORIES:
            items = _fetch_category(cat)
            by_category[cat] = items
            total_items += len(items)

        blocks = []
        for cat, items in by_category.items():
            label = cat.replace("_", " ").title()
            if not items:
                blocks.append(f"=== {label} ===\n(no items in the last {LOOKBACK_DAYS} days)")
                continue
            lines = "\n".join(
                f"- {it.get('title', '').strip()} ({it.get('source', it.get('feed', 'unknown'))})"
                for it in items
            )
            blocks.append(f"=== {label} ===\n{lines}")
        prompt = "\n\n".join(blocks)

        ollama_result = llm_generate(
            system=None, prompt=prompt,  # dedicated Modelfile carries this now
            ollama_model=OLLAMA_MODEL, max_tokens=1100, temperature=0.4,
            # Explicit timeout added 2026-07-26: this is the largest prompt
            # of the group (90 items across 6 categories) and legitimately
            # ran ~11 minutes (660s observed 2026-07-23 live test), but was
            # silently inheriting the shared OLLAMA_TIMEOUT=60s. 800s gives
            # headroom under the container's TimeoutStartSec=950.
            timeout=800,
        )
        if ollama_result:
            memo_body = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            status = "ok"
            log.info("%s: memo generated via Ollama/%s (%d items across %d categories)",
                     SKILL_NAME, OLLAMA_MODEL, total_items, len(CATEGORIES))
        else:
            # Deterministic fallback: raw headline digest, not the
            # narrative voice -- clearly labeled as such so it's never
            # mistaken for a real issue.
            memo_body = (
                "(Ollama unavailable -- raw headline digest follows, "
                "not the usual narrative format)\n\n" + prompt[:4000]
            )
            status = "fallback"
            log.info("%s: Ollama unavailable -- using raw headline fallback", SKILL_NAME)

        week_label = _week_label(today)
        generated_at = datetime.now(timezone.utc).isoformat()
        title = f"The Dispatch Desk — {week_label}"
        full_text = f"# {title}\n\n{memo_body.strip()}\n\n---\n*[operator LLC]*\n"

        # 1. Email-ready plain-text copy.
        state = pathlib.Path(config.state_dir())
        state.mkdir(parents=True, exist_ok=True)
        (state / "dispatch_desk_latest.txt").write_text(full_text)
        log.info("%s: wrote email-ready copy", SKILL_NAME)

        # 2. Durable vault copy.
        frontmatter = (
            "---\n"
            f"week: {week_label}\n"
            "ingest_method: dispatch-desk-memo\n"
            f"generated_at: {generated_at}\n"
            f"total_items: {total_items}\n"
            "---\n\n"
        )
        note = frontmatter + full_text
        rel_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/weekly/dispatch-desk-{week_label}.md"
        webdav_client.put(rel_path, note)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=title, content=note,
            tags="weekly,dispatch-desk,digest,synthesis,auto",
            ingest_method="dispatch-desk-memo",
        )
        conn.close()
        log.info("%s: wrote %s (status=%s)", SKILL_NAME, rel_path, status)

        # 3. Low-priority ping -- points at the issue, doesn't dump the
        # body onto a phone lock screen (this is meant to be read in one
        # sitting, not skimmed as a push notification).
        try:
            _ntfy.send(
                "dispatch",
                f"New issue ready: {title} ({total_items} items across "
                f"{len(CATEGORIES)} categories). Full text in "
                f"dispatch_desk_latest.txt / second-brain vault.",
                title=title, priority=2, tags="newspaper",
            )
        except Exception as ntfy_err:
            log.warning("%s: ntfy ping failed (non-fatal): %s", SKILL_NAME, ntfy_err)

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, OLLAMA_MODEL if status == "ok" else "deterministic",
                   0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
