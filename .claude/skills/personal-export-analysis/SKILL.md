---
name: personal-export-analysis
description: This skill should be used when the operator uploads or mentions a personal data export -- Uber/Lyft/DoorDash/Grubhub gig-platform data, a LinkedIn "Get a copy of your data" export, or similar -- and asks for analysis, a writing-voice profile, or recurring-viewpoints synthesis. Distinct from the corporatetraveldc dispatch-business skills in ../../../skills/ -- this is personal-side tooling and its output must never enter the business vault.
version: 1.0.0
---

# Personal Export Analysis

## What this is

A repeatable workflow for analyzing the operator's own personal data
exports (gig-platform trip/earnings history, LinkedIn network/content/
engagement history) using the **agentic-management-tooling-mcp** toolkit
-- a separate, real, public-repo project at
`/opt/corporatetraveldc/public/agentic-management-tooling-mcp`, not
anything in this repo. Originated 2026-08-24 the first time this analysis
was run ad hoc (Uber "Download your data" export + a LinkedIn export);
this file exists so the next run doesn't have to re-derive the same
findings and gotchas from scratch.

**This is personal-side, not dispatch-business.** It lives under
`.claude/skills/` rather than `skills/` (the dispatch-domain skill
directory, e.g. `flight-hifi-track`) deliberately -- matching this
session's own established rule that personal content and business
content never share a location. Never write this skill's output into
`corporatetraveldc/`'s general vault areas; see "Where findings go" below.

## Why direct import, not MCP

The MCP bridges that used to expose tools like these to a live MCP client
(`mcpo`/`mcpo-public`) were retired 2026-08-18 -- see this repo's own
CLAUDE.md. The toolkit's code is still real and current; it just isn't
reachable via the MCP protocol on this box anymore. So: import its Python
modules directly and call the underlying functions, rather than standing
the bridge back up for one-off analysis.

```bash
MCP_REPO=/opt/corporatetraveldc/public/agentic-management-tooling-mcp
cd "$MCP_REPO"
"$MCP_REPO/.venv/bin/python3" -c "
import sys; sys.path.insert(0, '.')
from gig_mobility.gig_analysis import gig_normalize_export, gig_revenue_analysis, gig_cluster_analysis, gig_availability_heatmap
from intelligence.linkedin_analysis import linkedin_network_breakdown, linkedin_content_analysis, linkedin_engagement_patterns
# ... call directly
"
```

The venv already has the toolkit's dependencies (`mcp`, `httpx`,
`xmltodict`, `maidenhead`). Neither module needs `AGENTIC_MCP_STATE_DIR`
set -- that's only required by `config.get_state_dir()`, which the
functions used here never call.

## Step 1 -- extract source files to the scratchpad only

Never extract into this repo or into any vault path. Use the session
scratchpad directory. `gig_normalize_export`/`linkedin_*` all accept a
`.zip` path directly (LinkedIn) or need individual CSVs extracted first
(gig_mobility -- `gig_normalize_export` takes one CSV/JSON file, not a
zip). Delete extracted files from the scratchpad once the analysis is
written up -- they're real personal financial/network data, don't leave
copies lying around after the task ends.

## Step 2 -- known export-format gotchas (fixed once, 2026-08-24)

Both fixes below are already applied in the toolkit repo as of this
writing (present in the working tree, unstaged and uncommitted --
verified 2026-08-24, `git status` there shows ` M` on both files; the
operator commits toolkit changes himself, same rule as every other
repo). **Before trusting either "fix"
is still sufficient, diff the export's actual CSV headers against
`_PLATFORM_MAPS["uber"]` in `gig_mobility/gig_analysis.py`** -- export
schemas drift, and this fix was scoped to one real Uber export snapshot,
not guaranteed forward-compatible.

1. **Uber's newer "Download your data" export** (`driver_lifetime_trips-0.csv`,
   `rider_lifetime_trips-0.csv`, etc.) uses lowercase snake_case columns
   (`begintrip_timestamp_utc`, `dropoff_lat`/`dropoff_lng`,
   `original_fare_usd`, no trip-id column at all) that matched neither
   the toolkit's original `"uber"` map (built for an older export style)
   nor its `"generic"` fallback -- `_resolve_field` is exact-key lookup,
   not substring, so close-but-not-exact names silently miss and every
   record gets skipped. Fixed by extending (additively) the `"uber"`
   candidate lists in `_PLATFORM_MAPS`. Old-style columns are still
   listed first, so an older export still resolves correctly too.
2. **Timestamps in that same newer export** are ISO 8601 with
   milliseconds and a literal `Z` suffix (`"2025-10-24T12:31:17.000Z"`),
   which `_parse_time`'s explicit format list didn't match -- every row
   silently skipped on `start_time is None` even with a valid fare.
   Fixed by trying `datetime.fromisoformat(value.replace("Z", "+00:00"))`
   first. Local-time columns (`*_timestamp_local`) are prioritized ahead
   of `*_timestamp_utc` ones in the candidate lists for day/hour pattern
   analysis, since wall-clock local time is what actually matters there.
3. **LinkedIn's Comments/Shares/Reactions "Date" column carries a full
   timestamp**, not a bare date (`"2026-08-22 23:29:00"`) -- none of
   `intelligence/linkedin_analysis.py`'s `_DATE_FORMATS` matched it, so
   every `comment_monthly`/`share_monthly`/`reaction_monthly` series came
   back silently empty `{}` despite thousands of real dated rows. Fixed
   by adding `"%Y-%m-%d %H:%M:%S"` to `_DATE_FORMATS`, kept first so a
   full timestamp matches before the bare-date fallbacks.

## Step 3 -- consent gates, ask every time, never assume

These are real decisions with real consequences that a prior run's answer
does not carry forward to a new one. Ask explicitly:

- **Reverse-geocoding.** `gig_normalize_export` sends dropoff lat/lon to
  Nominatim (openstreetmap.org) over the network to derive neighborhood/
  postcode -- real trip-coordinate data leaving the box to a third party.
  Default recommendation: skip it (revenue/time-pattern analysis needs no
  geo call at all). If the operator wants geo-clustering, confirm the
  Maidenhead precision (`gig_normalize_export`'s own guardrail: >= 8
  chars requires explicit `own_data=True` + `pre_sanitized=True`
  attestation; default 6 is neighborhood-level, ~4.6km x 2.3km).
- **Depth: aggregate-only vs. deliberate full-corpus raw-text read.**
  `intelligence/linkedin_analysis.py` is deliberately built to never
  surface raw comment/post/message text -- only topic labels, counts, and
  classifications. That's the safe default. A genuine writing-voice or
  recurring-viewpoints profile needs an actual read of the raw text,
  which bypasses that design on purpose -- only do this when the operator
  explicitly asks for voice/style/viewpoints, not by default, and treat
  it as reading the operator's own authored content (comments, shares,
  public articles), not a general license to dump third parties' message
  content anywhere.
  - For a **full-corpus** read (not a sample) of a large export
    (thousands of comments), delegate to a forked subagent rather than
    reading the whole raw CSV into the main session's own context --
    instruct it to actually read every row in batches (e.g. grouped by
    year), not just run a keyword-frequency script and read the counts.
    Have it return a synthesized report (style profile + evidenced
    throughpoints with short verbatim quotes), not a transcript dump.
  - Note LinkedIn's comment-export quirk: some rows prepend a name (the
    person replied to, usually) with no reliable field distinguishing
    that from the export owner's own name appearing in a nested-reply
    row. Flag authorship on any ambiguous row as unconfirmed rather than
    assumed.
- **Second-brain write.** Ask before writing anything to the vault at
  all, and never default to the general business-content areas -- see
  below.

## Step 4 -- where findings go

**Corrected 2026-08-24, same day this skill was written -- the first
version of this section drew a false distinction.** It originally split
findings into "general personal notes" (`01-Sources/personal-notes/`,
assumed NOT for content reuse) vs. "voice profile" (`04-Syntheses/`,
assumed the only thing Cowork needed to see). The operator corrected
this directly: **which vault subfolder a note lives in does not control
whether Cowork can use it** -- Cowork reads the second brain via the
board/dispatch relay regardless of physical path, and the operator
explicitly wants raw synthesized data (not just a style profile) treated
as real ghostwriting material -- hard trip/earnings numbers as
substantiating evidence for a piece, 16 years of consistent LinkedIn
themes as the credibility record, not just prose style. Don't repeat
the mistake of deciding on the model's behalf what counts as "content
material" -- ask, or default to treating everything from this skill as
potentially ghostwriting-relevant.

**The real rule: physical location is about vault organization only.
Cowork visibility is a separate, always-do step -- announce on the
board.**

1. Write the note wherever it best fits the vault's existing taxonomy --
   `01-Sources/personal-notes/` for general personal findings (same
   location `second_brain_personal_notes_import.py`'s own sanctioned
   personal-content import already uses), or
   `04-Syntheses/personal-voice-profile/` for a synthesized voice/style
   profile (sibling to the existing `04-Syntheses/entity-tracking/`
   pattern -- its own clearly-named subfolder, deliberately NOT
   `04-Syntheses/daily/` or `weekly/`, since `second_brain_weekly.py`'s
   scan is hardcoded to only those two paths and a standing reference
   doc shouldn't get swept into a weekly digest). Either way, run the
   note through `second_brain.scrub_gate.gate()` first (Tier-0-adjacent
   surface, same as every vault write), via
   `second_brain.remember_text()`'s `dest_subdir` parameter (added
   2026-08-24 for this skill) if targeting `01-Sources/`:

   ```bash
   export NEXTCLOUD_ADMIN_USER=corporatetraveldc   # required, no default -- see webdav_client.py
   cat <<'EOF' | PYTHONPATH=src python3 -m second_brain.remember \
       --stdin --tags "personal,uber,linkedin" --dest-subdir personal-notes
   <findings text, with a ## Provenance section per this repo's standing convention>
   EOF
   ```

2. **Then, always, announce it on the Cowork board** -- this is the step
   that actually makes it "available to Cowork," not the folder choice.
   Mirror `second_brain_research_board_mirror.py`'s exact real precedent:
   `db.board_insert("dispatch", "cowork", "research", subject, body,
   refs=[rel_path])`, `thread="research"` (an established real thread
   name, not invented here), body = a short excerpt (truncate if long)
   plus the vault path, matching how that mirror skill announces its own
   posts. Do this for every note this skill writes that the operator
   might want Cowork to draw on -- which, per the correction above,
   defaults to all of them unless told otherwise.

**Why quoting verbatim text is an intentional, documented exception
here, not an oversight**: `export_analysis.py`'s own standing policy
(item 5, its module docstring) is "never quote long runs of comment/
share text verbatim -- reference by topic + date instead," written for
its own automated topic-extraction digest. A voice profile is
structurally different -- it is USELESS for ghostwriting without real
example sentences -- and the operator explicitly asked for exactly that
depth this time (see Step 3's consent-gate note). State this plainly in
the note itself so a future reader isn't confused about why this
particular note breaks the usual no-verbatim-quotes convention: it's a
deliberate, requested exception for this content type, not a precedent
for every export-analysis digest going forward.

**Never** `source` `dispatch-secrets.env`/`dispatch.env` directly in
bash to get `NEXTCLOUD_ADMIN_USER` or anything else -- see CLAUDE.md's
quoting-gotcha entry; export the one variable directly instead.

## Step 5 -- output as a private Artifact

Build the report as a published Artifact, not a plain chat dump -- run
through the `artifact-design` skill first (this is a data/report
deliverable: polished-utilitarian treatment, real typographic hierarchy
and a considered palette, not an editorial/landing-page treatment). Keep
it **private** (the Artifact default) -- this is the operator's real
financial and network data, never publish or share it from this skill's
own initiative. Two-tier structure worked well the first time: aggregate
stats/tables/simple bar visualizations up top, an optional deeper
voice/viewpoints section below it only when that depth was actually
requested and run.

## Known limitations (not yet addressed)

- No fixture/test coverage exists for the two `gig_mobility` fixes above
  against a real anonymized sample -- they were validated against one
  real export, not regression-tested. If this skill runs again against a
  materially different Uber export shape, expect to re-diff headers.
- `gig_mobility`'s `restaurant`/`cuisine` normalization fields are
  DoorDash/Grubhub-shaped and mostly irrelevant to a rides-only Uber
  export (no restaurant data in that case) -- harmless (`cuisine_type`
  just comes back `"other"`), not worth stripping out for a rides-only
  run.
- No automated trigger wiring exists (this is a manually-invoked Claude
  Code skill, not a poller timer skill under `src/poller/skills/`) -- by
  design, since personal export uploads are occasional and operator-
  initiated, not a scheduled job.
