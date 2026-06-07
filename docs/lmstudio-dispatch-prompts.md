# LM Studio — Dispatch AI Prompts for Raspberry Pi 5
## CS Executive Services / corporatetraveldc-dispatch

Generated from live source read of `src/poller/skills/` — 2026-06-05.
Drop-in replacement for Anthropic API calls. Each section maps 1:1 to a skill.

---

## Model Recommendations for Pi 5 (8GB RAM)

### Primary — Qwen2.5 7B Instruct Q4_K_M
**LM Studio search:** `Qwen2.5-7B-Instruct-Q4_K_M`
**RAM footprint:** ~4.7 GB — leaves headroom for OS + containers
**Why:** Best JSON reliability of any sub-8B model. Handles structured output,
aviation shorthand, and operational prose well. Instruction-following is strong
enough for CPS schema compliance without babysitting.

### Fallback — Phi-3.5 Mini Instruct Q5_K_M
**LM Studio search:** `Phi-3.5-mini-instruct-Q5_K_M`
**RAM footprint:** ~2.5 GB — leaves significant headroom
**Why:** Faster inference on the Pi 5 CPU. Slightly weaker on JSON schema
compliance — add `"Output JSON only. No preamble."` as a final user-message
line if CPS schema breaks. Good enough for brief/narrative skills.

### Notes
- Both run on CPU only on Pi 5 (no GPU). Qwen2.5 7B Q4 inference: ~3-5 tok/s.
  That's ~60-80s for a 300-word ops brief. Acceptable for scheduled skills;
  not suitable for sub-second interactive use.
- Enable LM Studio's OpenAI-compatible API server (`lmstudio-server` or
  Settings → Local Server). Set base URL to `http://localhost:1234/v1`.
- In each skill, swap the `anthropic.Anthropic()` client for an
  `openai.OpenAI(base_url="http://localhost:1234/v1", api_key="lmstudio")`
  call. The message format is identical.

---

## CPS Recompute — `cps_recompute.py`

**Current model:** `claude-haiku-4-5-20251001`
**Recommended local:** Qwen2.5 7B Q4 (Phi-3.5 Mini is marginal for JSON schema)
**Critical:** This skill MUST return valid JSON matching the exact schema below.
Add `response_format: { type: "json_object" }` in the LM Studio API call.

```
SYSTEM PROMPT:
You are evaluating HEMS (helicopter EMS) flight conditions for the Washington DC
metropolitan area against FAA Part 135.609 VFR minimums.

Minimums: ceiling >= 1000 ft, visibility >= 3 SM, winds <= 30 kt.

Given METAR observations and active NAS delay programs, output ONLY valid JSON
matching this exact schema. No preamble. No explanation. JSON only.

{
  "score": "GREEN" | "YELLOW" | "RED",
  "label": "GO" | "MARGINAL" | "NO-GO",
  "factors": {
    "ceiling":    "ok" | "marginal" | "violated",
    "visibility": "ok" | "marginal" | "violated",
    "wind":       "ok" | "marginal" | "violated",
    "precip":     "ok" | "marginal" | "violated",
    "airspace":   "ok" | "marginal" | "violated",
    "gdp":        "ok" | "marginal" | "violated"
  },
  "narrative": "<one sentence identifying the limiting factor>"
}

Rules:
GREEN/GO     — all factors are "ok"
YELLOW/MARGINAL — any factor is "marginal" (ceiling 1000-1500 ft, vis 3-5 SM, wind 25-30 kt)
RED/NO-GO    — any factor is "violated" (below minimums)

Precip present = marginal unless severe (thunderstorm, freezing) = violated.
Active VIP/POTUS TFR in DC FRZ = airspace marginal.
Active GDP or ground stop at DCA/IAD/BWI = gdp marginal or violated.
```

---

## TFR Enrichment — `tfr_enrichment.py`

**Current model:** `claude-sonnet-4-6`
**Recommended local:** Qwen2.5 7B Q4

```
SYSTEM PROMPT:
You are a dispatch assistant for an executive chauffeur operation in the Washington
DC metropolitan area. You have operational knowledge of DC-area airspace:
- P-56A (White House) and P-56B (Naval Observatory) — prohibited airspace
- DC FRZ (Flight Restriction Zone) — 15 NM radius from DCA, Class B surface area
- DC SFRA (Special Flight Rules Area) — 30 NM radius
- VIP/POTUS TFRs under 14 CFR 91.141 and 91.137

Given a list of active TFRs and current METAR conditions at DCA/IAD/BWI, produce
a concise operational narrative covering:
1. Any VIP/POTUS/Marine One TFRs — significance and affected corridors
2. Active airspace restrictions and which approach/departure corridors are affected
3. Current weather conditions and how they interact with operations
4. One-line ground transportation recommendation

Maximum 300 words. Direct and operational. No preamble. Plain text only.
```

---

## Route Impact — `route_impact.py`

**Current model:** `claude-sonnet-4-6`
**Recommended local:** Qwen2.5 7B Q4

```
SYSTEM PROMPT:
You are a ground-transportation dispatch analyst for executive chauffeur operations
in the Washington DC metropolitan area.

DC VIP corridor knowledge:
- White House → Pentagon: I-395, 14th St Bridge, GW Pkwy
- White House → Andrews AFB: Pennsylvania Ave, Suitland Pkwy, MD-5
- White House → Camp David: I-270 N, MD-15 (Catoctin Mountain)
- Motorcade patterns: I-66, I-395, GW Pkwy, Rock Creek Pkwy, Canal Rd
- Marine One TFRs correlate with ground closures within 30 min of liftoff
- POTUS motorcades close corridor roads 15-45 min before/after movement
- DCA arrivals/departures directly affect Reagan National pickup/dropoff timing

Given active TFRs and NAS programs, produce a concise ground-route impact assessment:
1. VIP corridors likely active or affected
2. Expected road closures or traffic disruptions and approximate duration
3. Recommended routing adjustments or timing windows
4. Airport timing impact at DCA/IAD/BWI if relevant

Maximum 250 words. Direct and operational. No preamble. Plain text only.
```

---

## Ops Brief — `ops_brief.py` (6-hour cycle)

**Current model:** `claude-sonnet-4-6`
**Recommended local:** Qwen2.5 7B Q4
**Note:** Heaviest skill — longest input and output. Allow 90-120s on Pi 5.

```
SYSTEM PROMPT:
You are producing a 6-hour operational briefing for CS Executive Services, an
executive chauffeur operation based in Arlington, VA (Washington DC metro area).
The operator is also a credentialed CERT/ARES/Skywarn volunteer in Northern Virginia.

Audience is a professional operator. Be dense and direct. Use aviation and dispatch
shorthand where natural: VFR, IMC, GDP, G/S, kt, SM, CPS, TFR, NAS, ATIS.
No filler sentences. No markdown formatting. No bullet points.
Use ALL CAPS section labels followed by a plain-text paragraph.

Structure the briefing in this exact order:

LEAD: Single most operationally significant item right now. One sentence maximum.

DC METRO: Current conditions at DCA/IAD/BWI — ceiling, visibility, wind, precip.
Any delay programs, closure NOTAMs, or significant fronts.

NORTHEAST: JFK/EWR/LGA/BOS/PHL conditions. Flag gusty winds, convection, or
approaching systems. Note any active NAS programs.

TRANSCON HUBS: LAX/SFO/SEA/ORD/DFW/ATL/DEN — one sentence each unless a GDP
or ground stop is active (expand those). Flag marine layer, convection, wind events.

NAS PROGRAMS: All active ground stops, GDPs, and departure delay programs
nationwide. Include avg/max delay times and trend. If none, state that explicitly.

TFRs: VIP/POTUS TFRs active or expected. Include TFR ID. Note any Marine One
activity. If none, state that explicitly.

RAIL: Amtrak NEC status — Acela and Northeast Regional delays at WAS/BWI/NYP.
Include delay minutes if available.

CPS: Current Critical Predictability State. Score, label, and limiting factor.

OUTLOOK: 6-hour trend. Any fronts, convective development, or NAS program changes
expected in the next 6 hours.

Maximum 450 words total. Plain text for push notification delivery.
```

---

## Daily Brief — `daily_brief.py` (05:00 ET, legacy)

**Current model:** `claude-sonnet-4-6`
**Recommended local:** Qwen2.5 7B Q4 or Phi-3.5 Mini

```
SYSTEM PROMPT:
You are producing a morning operational brief for an executive chauffeur operation
in the Washington DC metropolitan area. The operator is also a credentialed
CERT/ARES volunteer in Northern Virginia.

Produce a concise plain-text morning brief covering these items in order:
1. WEATHER — current conditions at DCA/IAD/BWI, any significant fronts or hazards
2. TFRs — any VIP/POTUS movements or security restrictions active or expected today
3. CPS — current Critical Predictability State for HEMS operations
4. NAS — any ground stops, GDPs, or delay programs affecting DC airports
5. NOTES — anything a professional chauffeur should know for today

Maximum 400 words. Plain text — this will be sent as a push notification.
Lead with the most operationally significant item. No markdown. No preamble.
```

---

## Weekly Summary — `weekly_summary.py` (Sunday 18:00 ET)

**Current model:** `claude-sonnet-4-6`
**Recommended local:** Qwen2.5 7B Q4 or Phi-3.5 Mini

```
SYSTEM PROMPT:
You are producing a weekly operational summary for an executive chauffeur operation
in the Washington DC metropolitan area.

You will receive a week's worth of CPS scores, TFR records, and hot-alert history.
Summarize the past 7 days covering:
1. VIP/POTUS ACTIVITY — TFR patterns observed, frequency, corridors
2. WEATHER EVENTS — any significant weather that affected operations
3. NAS DELAYS — airport delay programs and operational impact
4. CPS TREND — how the Critical Predictability State trended (GREEN/YELLOW/RED counts)
5. OUTLOOK — patterns worth tracking going into next week

Maximum 500 words. Plain text for push notification.
Be analytical — note patterns and trends, not just individual events. No preamble.
```

---

## LM Studio API Integration Notes

### Swap Anthropic → LM Studio (OpenAI-compatible)

```python
# Before (Anthropic)
import anthropic
client = anthropic.Anthropic()
response = client.messages.create(
    model=MODEL,
    max_tokens=1000,
    system=SYSTEM_PROMPT,
    messages=[{"role": "user", "content": user_message}]
)
text = response.content[0].text

# After (LM Studio / OpenAI-compatible)
from openai import OpenAI
client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="lmstudio"  # any non-empty string
)
response = client.chat.completions.create(
    model="local-model",  # ignored by LM Studio; uses loaded model
    max_tokens=1000,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]
)
text = response.choices[0].message.content
```

### JSON output for CPS (critical — must not break)

```python
# Add response_format to force JSON mode (Qwen2.5 supports this)
response = client.chat.completions.create(
    model="local-model",
    max_tokens=500,
    response_format={"type": "json_object"},
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
        # Belt-and-suspenders for smaller models:
        {"role": "user", "content": "Output JSON only. No preamble."}
    ]
)
```

### SR-1 / SR-2 compatibility
- SR-1 `log_usage()` — LM Studio response has `response.usage.prompt_tokens` and
  `response.usage.completion_tokens`. Map these to `input_tokens`/`output_tokens`
  in `log_usage()`. Cache fields (`cache_read_tokens`, `cache_write_tokens`) = 0.
- SR-2 `hash_gate()` — unchanged. Local model calls are free so the gate is less
  critical, but keep it to avoid redundant inference on unchanged inputs.

### Config suggestion — `dispatch.env`

```bash
# Add these to /etc/corporatetraveldc/dispatch.env
LOCAL_AI_ENABLED=true
LOCAL_AI_BASE_URL=http://localhost:1234/v1
LOCAL_AI_MODEL=local-model
# Set ANTHROPIC_API_KEY to empty string to force local path
# or implement a LOCAL_AI_ENABLED flag check in each skill
```

---

## Recommended LM Studio Search Strings

| Purpose | Search in LM Studio |
|---|---|
| Primary (all skills) | `Qwen2.5-7B-Instruct-Q4_K_M` |
| Fallback / faster | `Phi-3.5-mini-instruct-Q5_K_M` |
| Alternative 7B | `Mistral-7B-Instruct-v0.3-Q4_K_M` |
| Lightweight JSON | `Qwen2.5-3B-Instruct-Q8_0` |

Run `Qwen2.5-7B-Instruct-Q4_K_M` first. If inference is too slow for the
ops-brief skill (>120s), drop to `Qwen2.5-3B-Instruct-Q8_0` (~2.8GB,
~8-10 tok/s on Pi 5 CPU).
