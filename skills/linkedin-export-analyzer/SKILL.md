---
name: linkedin-export-analyzer
description: "Analyze a LinkedIn 'Get a copy of your data' ZIP export to produce a network breakdown by industry and connection tenure, comment and post topic analysis, topic co-occurrence correlations, monthly activity trends, reaction patterns, and most-mentioned people. Use this skill whenever a user uploads a LinkedIn data export (ZIP, usually named `Complete_LinkedInDataExport_*.zip` or `Basic_LinkedInDataExport_*.zip`), asks to analyze their LinkedIn network, wants to see how their network breaks down by industry or how long they've known their connections, asks about correlations between posts/comments and industries, wants to know who they engage with most, or mentions analyzing their own Connections.csv / Comments.csv / Shares.csv / Reactions.csv. Trigger this even when the user just says 'analyze my LinkedIn' or uploads the ZIP without explicit instructions — the export is the trigger."
---

# LinkedIn Export Analyzer

LinkedIn lets users download their full data via **Settings & Privacy → Data Privacy → Get a copy of your data**. This produces a ZIP containing around 60 CSV files covering connections, comments, shares, reactions, messages, endorsements, follows, and more.

This skill analyzes that export and produces a structured report. The analysis is **deterministic, local, and privacy-preserving** — nothing is sent to external services, which matters because LinkedIn exports contain email addresses, message content, and private contact info.

## When to use

Trigger this skill when:

- A user uploads a LinkedIn data export ZIP (typical filename: `Complete_LinkedInDataExport_MM-DD-YYYY_zip.zip` or `Basic_LinkedInDataExport_*.zip`)
- A user asks to analyze their LinkedIn network, connections, comments, or activity
- A user references specific files like `Connections.csv`, `Comments.csv`, `Shares.csv`, or `Reactions.csv`
- A user asks questions like "what industries is my network in", "how long have I known my connections", "what do I post about", "what topics correlate in my comments", or "who do I engage with most"
- A user asks for "changes since last analysis" or a "delta" — compare against prior report.json if available in /home/claude/linkedin-report/

Do **not** use this skill for:
- Analyzing someone else's LinkedIn profile from a URL (that requires LinkedIn API access, which this skill doesn't use)
- Scraping LinkedIn live
- Non-LinkedIn data exports

## MCP availability note

As of June 2026, **no LinkedIn MCP server exists** in the Anthropic connector registry. There is no native LinkedIn MCP connector. If a user asks to "use the LinkedIn MCP", explain this and proceed with the ZIP export analysis instead. The Zapier MCP can post/read limited LinkedIn data but does not provide network or analytics access. A LinkedIn Developer Application with the Marketing Developer Platform or Partner Program is required for API-level analytics access.

## The workflow

### 1. Confirm the input

The user must provide the ZIP file. If they haven't uploaded it yet, explain briefly how to get one: Settings & Privacy → Data Privacy → Get a copy of your data → "The works" (full export takes ~24 hours to generate; "Fast file only" is quicker but contains only Connections.csv).

### 2. Run the analyzer

Use the bundled script. It handles ZIP extraction, CSV parsing quirks (LinkedIn's `Connections.csv` has a 3-line preamble before the real header), and bad-line tolerance:

```bash
python3 <skill-path>/scripts/analyze.py <path-to-zip> \
    --out <work-dir>/linkedin-report \
    --reference-date <today-or-export-date>
```

The `--reference-date` flag controls how tenure buckets are computed. Use the export date when that's known (it's in the filename), otherwise today.

The script writes:
- `report.json` — the full structured result
- `industry_breakdown.csv`, `tenure_breakdown.csv`, `comment_topics.csv`, `topic_co_occurrence.csv` — flat CSVs

### 3. Run extended activity analysis

After the base script, run this inline Python against the ZIP to capture reactions, shares, and deep topic analysis — the base script may return empty `activity: {}` for some exports:

```python
import zipfile, csv, io, collections, re
from datetime import datetime

z = zipfile.ZipFile('<path-to-zip>')

TOPICS = {
    'Technology/IT':        r'\b(tech|software|ai|cyber|cloud|data|digital|linux|security|network|it |infosec|hack|code|developer|programming)\b',
    'Aviation/Transport':   r'\b(aviation|flight|pilot|aircraft|fly|airport|airline|charter|private\s+jet|helicopter|fbo|easa|faa|part\s*135)\b',
    'Hospitality':          r'\b(hotel|hospitality|resort|valet|concierge|guest|front\s+desk|gm|general\s+manager|marriott|hilton|starwood)\b',
    'Executive/Leadership': r'\b(leader|ceo|executive|management|strategy|board|c-suite|vp|director|chief)\b',
    'Security/EP':          r'\b(security|protection|ep\b|executive\s+prot|threat|risk|guard|protective|bodyguard|surveillance)\b',
    'Veterans/Military':    r'\b(veteran|military|marine|army|navy|service\s+member|usmc|deployment|combat|vets)\b',
    'Entrepreneurship':     r'\b(startup|entrepreneur|founder|business\s+owner|venture|small\s+biz|hustle|build)\b',
    'Networking/Career':    r'\b(network|connect|career|opportunity|hire|job|recruit|linkedin|professional\s+dev)\b',
}

# Read all three activity files
def read_activity(name):
    members = z.namelist()
    match = next((m for m in members if name.lower() in m.lower()), None)
    if not match: return []
    with z.open(match) as f:
        return list(csv.DictReader(io.StringIO(f.read().decode('utf-8', errors='replace'))))

comments  = read_activity('Comments')
reactions = read_activity('Reactions')
shares    = read_activity('Shares')

# Topic counts + co-occurrence
topic_counts = collections.Counter()
co_occur     = collections.Counter()
for c in comments:
    msg = (c.get('Message') or '').lower()
    matched = [t for t, pat in TOPICS.items() if re.search(pat, msg, re.I)]
    for t in matched: topic_counts[t] += 1
    for i in range(len(matched)):
        for j in range(i+1, len(matched)):
            co_occur[tuple(sorted([matched[i], matched[j]]))] += 1

# Reaction types
rxn_types = collections.Counter(r.get('Type','') for r in reactions)

# Monthly series (2024+)
def monthly(rows, date_field='Date'):
    c = collections.Counter()
    for r in rows:
        try:
            d = datetime.strptime(r[date_field][:7], '%Y-%m')
            c[d.strftime('%Y-%m')] += 1
        except: pass
    return {k: v for k, v in sorted(c.items()) if k >= '2024-01'}

import json
print(json.dumps({
    'comments_total': len(comments),
    'comment_topics': dict(topic_counts.most_common(10)),
    'topic_cooccurrence': {str(k): v for k, v in co_occur.most_common(12)},
    'comment_monthly': monthly(comments),
    'reactions_total': len(reactions),
    'reaction_types': dict(rxn_types),
    'reaction_monthly': monthly(reactions),
    'shares_total': len(shares),
    'share_topics': dict(
        collections.Counter(
            t for s in shares
            for t, pat in TOPICS.items()
            if re.search(pat, ((s.get('ShareCommentary') or '') + ' ' + (s.get('SharedUrl') or '')).lower(), re.I)
        ).most_common(8)
    ),
    'share_monthly': monthly(shares),
}, indent=2))
```

### 4. Generate PDF report

After analysis, always generate a PDF using reportlab. The PDF includes:
- Cover block with firm name, export date, and preparer
- Executive summary table (key metrics at a glance)
- Key findings bullet list (5–7 actionable insights)
- Section 1: Network composition (industry hbar, tenure vbar, growth vbar, top companies table)
- Section 2: Engagement analysis (topics table, co-occurrence hbar, reaction breakdown table)
- Section 3: Activity trend (dual-line chart comments+reactions, 2026 monthly detail table)
- Section 4: Period comparison table (YoY metrics with trend arrows)
- Footer: firm name, date, Confidential

Use `reportlab.platypus` (SimpleDocTemplate + Paragraph + Table + HRFlowable) for layout.
Use `reportlab.graphics.charts` for charts (HorizontalBarChart, VerticalBarChart, HorizontalLineChart).
Output to `/mnt/user-data/outputs/{firm}_linkedin_analysis_{YYYY-MM-DD}.pdf`.

**Critical reportlab rules:**
- Never use Unicode subscript/superscript characters — use `<sub>` and `<super>` XML tags in Paragraph
- Use `colors.HexColor('#...')` for brand colors
- All chart fonts at 6–8pt; axis labels rotated 30–45 degrees for bar charts
- For dual-axis line charts, scale one series (e.g. reactions ÷ 5) and note it in the caption

### 5. Delta analysis (if prior report exists)

If `/home/claude/linkedin-report/report.json` exists from a previous run, load it and compare:
- New connections since last export (by connected-on date)
- Industry mix shift (% point change per category)
- Activity trend change (comment/reaction volume YoY)
- New top companies appearing

```python
import json
with open('/home/claude/linkedin-report/report.json') as f:
    prior = json.load(f)
# Compare prior['connections']['total'] to current, etc.
```

### 6. Executive summary of changes

When a prior `report.json` exists OR when the analysis covers multiple visible periods, produce a concise executive summary structured as:

**Period:** [prior export date] → [current export date]

**Network:**
- Total connections: [prior] → [current] ([delta] [▲/▼])
- New connections this period: [N] (top industries: ...)
- Growth rate vs prior period: [X]%

**Engagement:**
- Comments: [prior annual avg] → [current annual avg] ([▲/▼ trend])
- Reactions: [prior annual avg] → [current annual avg]
- Peak month this period: [month] ([N] comments, [N] reactions)
- Notable dip: [month range if any]

**Composition shifts:**
- [Any industry that moved >2 percentage points]
- [Any new company cluster appearing]

**Intersection space:**
- Dominant co-occurrence unchanged/changed: [pair] at [N]
- Engagement gap: network [X]% [industry] but comments [Y]% [topic]

**Standout signals:**
- [1–3 specific actionable observations, e.g. "May 2026 spike warrants investigation — what campaign or event drove 2x engagement?"]

This summary goes in the PDF as Section 4 when comparing periods, and is also surfaced as a text response before presenting the PDF link.



Structure the response in this order (skip sections that are empty):

1. **Network size and date range** — total, earliest/latest, YoY growth rate
2. **Industry breakdown** — bar chart, top 8-10 industries, note the gap between network composition and engagement topics
3. **Length of connection (tenure)** — bar chart, note where the mass is and what it implies about network age
4. **Connection growth by year** — bar chart, call out acceleration years
5. **What they post vs. what they comment on** — grouped bar chart comparing comment topics to share topics; the gap is usually the most interesting finding
6. **Topic co-occurrence** — horizontal bar chart, top 10-12 pairs; these reveal the user's actual intersection space
7. **Activity trend** — line chart, comments + reactions monthly 2024+; call out peak months and dips
8. **Reaction breakdown** — doughnut chart of reaction types; LIKE dominance vs. empathy/praise ratio signals engagement style
9. **Delta from prior run** — if prior report available, surface new connections, mix shifts, activity changes
10. **Top companies** — table, top 10-15 companies in network

Use `show_widget` for all charts. Keep it conversational — surface patterns they'd miss, not facts they already know.

## Key patterns to call out proactively

- **Network vs. engagement gap**: If top network industry ≠ top comment topic, flag it explicitly. This is almost always the most actionable insight.
- **Co-occurrence dominant pair**: The #1 co-occurrence pair reveals the user's actual professional intersection. Name it directly.
- **Activity acceleration/deceleration**: Month-over-month trend breaks tell you when engagement strategy changed.
- **Reaction type distribution**: Very high LIKE% (>85%) with low Empathy/Praise suggests transactional engagement rather than relational. Worth noting.
- **Tenure mass**: If >30% of connections are 1-2 years old, the network is mid-build. If >30% are 4+, it's established but may be stagnating.

## How the classification works

**Connection industries** — classified from the free-text `Company` + `Position` fields using a keyword-matched priority list. First match wins. About 15-20% of connections fall to "Other / Unclassified" — expected and worth mentioning.

**Comment and post topics** — classified from free prose using regex patterns with word boundaries. Comments can match multiple topics at once (enabling co-occurrence analysis). "General / Other" is excluded from co-occurrence pairs.

Both classifiers are in `scripts/analyze.py` and the inline Python above. They can be extended for specialized networks.

## Files in the LinkedIn export worth knowing about

| File | Contents |
| --- | --- |
| `Connections.csv` | All 1st-degree connections. **Has a 3-line notes preamble — real header on line 4.** |
| `Comments_XXXXXXXX.csv` | Every comment: date, post URL, message text. Filename includes numeric user ID. |
| `Shares_XXXXXXXX.csv` | Every original post: date, URL, commentary, shared URL. |
| `Reactions_XXXXXXXX.csv` | Every reaction: date, type (LIKE/EMPATHY/PRAISE/etc.), link. |
| `Invitations.csv` | Sent/received connection requests with direction and date. |
| `Member_Follows_XXXXXXXX.csv` | Accounts followed (not connected). |
| `Company Follows.csv` | Companies followed. |
| `Hashtag_Follows_XXXXXXXX.csv` | Hashtags followed — good signal for topic interest. |
| `Endorsement_Given_Info.csv` | Skills endorsed for others — another engagement signal. |

**Note on numeric suffixes**: Comments, Shares, Reactions, Reactions, Votes, and several other files include the user's numeric LinkedIn ID in the filename (e.g. `Comments_70127804.csv`). Use `z.namelist()` with a substring match rather than hardcoded filenames.

Files **not** analyzed by default (but available on request):
- `messages.csv` — full DM content, extremely sensitive
- `Ads Clicked.csv`, `SearchQueries.csv` — privacy audit use only
- `Votes_XXXXXXXX.csv` — poll votes
- `InstantReposts_XXXXXXXX.csv` — reposts without commentary

## Privacy handling

- Do all analysis in the working directory — do not copy to `/mnt/user-data/outputs/` unless the user asks for downloadable artifacts
- Do not echo email addresses, phone numbers, or raw message content into the response
- Do not quote long runs of comment or share text — reference by topic or date instead
- If asked to analyze someone else's export, confirm consent first
