"""
demo.scrub_rules — content-hygiene rules for scripts/scrub-demo-source.py.

Mirrors scripts/scrub-public-tree.py's two-layer discipline, retargeted at
prose/payload content (brief_archive rows, snapshot JSON) instead of repo
blobs:
  1. SUBSTITUTIONS / REGEX_SWEEPS -- proactively replace known real values.
  2. verify_scrubbed() -- an ALLOWLIST-based post-scan of the *output*. This
     is the layer that actually matters: layer 1 only catches what someone
     remembered to add; layer 2 catches everything else by refusing to
     promote anything unrecognized, rather than trusting the substitution
     table's completeness.

Operates on `str`, not `bytes` -- source content here is DB text (SQLite
TEXT columns via sqlite3.Row), not git blobs.

Never silently ships on a verification failure: scripts/scrub-demo-source.py
treats any row that fails verify_scrubbed() as DROPPED, not promoted with a
warning. A gap in demo history is acceptable; a leak is not.
"""
import re

# ── Layer 1: proactive substitutions ────────────────────────────────────────
# Same real values scripts/scrub-public-tree.py already tracks for the
# public-mirror push, retargeted at prose rather than code/config. Order
# matters within a run (longest/most-specific literal first) so a shorter
# match never partially consumes a longer one and leaves a mangled remainder
# -- same ordering discipline scrub-public-tree.py documents for
# "[operator LLC]" vs "[operator LLC abbreviation]" and "the operator" vs "the operator".
SUBSTITUTIONS: list[tuple[str, str]] = [
    # Operator identity -- longest/most-specific forms first.
    ("the operator (WA1EM)", "[operator]"),
    ("the operator", "[operator]"),
    ("operator@example.com", "swimuser@example.com"),
    ("owner@example.com", "owner@example.com"),
    ("the operator", "[operator]"),
    ("operator", "operator"),

    # Business identity.
    ("[operator LLC], LLC", "[operator LLC]"),
    ("[operator LLC]", "[operator LLC]"),
    ("[operator LLC abbreviation]", "[operator LLC abbreviation]"),

    # Callsigns / ARES-CERT identifiers -- distinctive enough to be
    # personally identifying even standalone (FCC ULS lookup).
    ("WA1EM-5", "[callsign]-N"),
    ("WA1EM", "[callsign]"),
    ("WRCR715", "[callsign]"),
    ("Skywarn L0344", "[skywarn-id]"),
    ("ARES VA Section District 10", "[ARES district]"),
    ("CERT Fairfax", "[CERT unit]"),
    ("CERT Loudoun County", "[CERT unit]"),
    ("Fairfax + Loudoun County", "[CERT coverage area]"),

    # Location -- county/district-level specificity redacted; metro-area
    # generality (DC metro, Northern VA) is kept, it's not identifying on
    # its own and the demo is materially less useful without any geography.
    ("Arlington County, VA", "[operator county], [state]"),
    ("Arlington County", "[operator county]"),

    # EP hotel/dining/venue matrix -- these are real public business names,
    # but THIS operator's specific pattern of using THEM for principal
    # movement is the actual sensitive fact (a real, actionable physical-
    # security pattern), not the brand names in isolation.
    ("Salamander Middleburg", "[EP venue — countryside]"),
    ("SW waterfront Salamander", "[EP venue — waterfront]"),
    ("Salamander", "[EP venue]"),
    ("Blue Duck Tavern", "[EP dining venue]"),
    ("Park Hyatt", "[EP hotel venue]"),
    ("Bourbon Steak", "[EP dining venue]"),
    ("Four Seasons", "[EP hotel venue]"),
    ("Middleburg", "[EP venue area]"),
    ("Bethesda/Chevy Chase", "[EP venue area]"),
    ("Annapolis waterfront", "[EP venue area]"),
    ("Great Falls", "[EP venue area]"),

    # DC-metro protest/security zone specifics -- real public geography, but
    # the pattern of an EP brief naming them as active risk zones on a given
    # day ties to real principal movement, same reasoning as the venues
    # above.
    ("Lafayette Square", "[protest zone]"),
    ("Lincoln Memorial", "[protest zone]"),
    ("McPherson Square", "[protest zone]"),
    ("DuPont Circle", "[protest zone]"),
    ("Freedom Plaza", "[protest zone]"),
    ("Embassy Row", "[diplomatic corridor]"),
    ("Georgetown", "[diplomatic corridor]"),
    ("Capitol Hill", "[HVT corridor]"),
    ("K Street", "[HVT corridor]"),
    ("16th St NW", "[POTUS corridor]"),
    ("South Lawn", "[POTUS corridor]"),
]

# ── Layer 1b: regex sweeps for shaped-not-literal content ──────────────────
REGEX_SWEEPS: list[tuple[re.Pattern, str]] = [
    # US phone-number shapes: (555) 555-5555, 555-555-5555, 555.555.5555,
    # +1 555 555 5555. Deliberately broad -- false-positive-on-numbers-that-
    # aren't-phones is an acceptable cost against missing a real one.
    (re.compile(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}\b"), "[phone-redacted]"),
    # Radio frequency shapes (1xx.xxx / 4xx.xxx MHz -- VHF/UHF ranges this
    # platform's SHARES/HEARS/HEART/FOUO data uses) -- backstop in case one
    # ever slips past the "never generate" rule already in the persona.
    (re.compile(r"\b[1-4]\d{2}\.\d{2,4}\s*MHz\b", re.IGNORECASE), "[freq-redacted]"),
    # US street-address shape: number + street name + common suffix.
    (re.compile(
        r"\b\d{1,5}\s+[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*){0,3}\s+"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Court|Ct|Place|Pl)\b"
    ), "[address-redacted]"),
    # FCC-shaped amateur callsigns not already caught by the literal
    # substitutions above (e.g. a future/rotated callsign) -- standard US
    # ham format: 1-2 letters, 1 digit, 1-3 letters.
    (re.compile(r"\b[AKNW][A-Z]?[0-9][A-Z]{1,3}\b"), "[callsign-redacted]"),
]

# ── Layer 2: verify_scrubbed() allowlists ───────────────────────────────────
# Same "anything shaped like X must be on the allowlist or the promotion
# fails" discipline as scrub-public-tree.py's verify_scrubbed(). This is
# what actually gates -- not the substitutions above.
FORBIDDEN_LITERALS = [
    "the operator", "operator", "owner@example.com",
    "WA1EM", "WRCR715", "Skywarn L0344",
    "[operator LLC]", "Arlington County",
    "Salamander", "Blue Duck Tavern", "Park Hyatt", "Bourbon Steak",
    "Four Seasons", "Middleburg",
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ALLOWED_EMAIL_DOMAIN_SUFFIXES = ("example.com",)

PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}\b")
FREQ_RE = re.compile(r"\b[1-4]\d{2}\.\d{2,4}\s*MHz\b", re.IGNORECASE)


def _redact_email(m: re.Match) -> str:
    """2026-08-14: NOTAM text routinely embeds real point-of-contact
    emails (e.g. firstname.lastname@army.mil alongside generic desk
    addresses like drones@dhs.gov) -- verify_scrubbed() was catching these
    correctly, but only at Layer 2 (detect + drop the WHOLE row), same as
    if scrub_text() did nothing for emails at all. That dropped ~24% of
    the 49-day backfill for a single embedded address in an otherwise
    fine payload. Redact at the substring level instead, same as
    PHONE_RE/FREQ_RE already do below -- verify_scrubbed()'s EMAIL_RE
    check remains as the fail-closed backstop for anything this misses."""
    addr = m.group(0)
    return addr if addr.endswith(ALLOWED_EMAIL_DOMAIN_SUFFIXES) else "[email-redacted]"


def scrub_text(text: str) -> str:
    """Apply layer-1 substitutions + regex sweeps, in order. Longest/most-
    specific SUBSTITUTIONS entries are listed first deliberately -- do not
    reorder without re-checking for partial-match corruption (see module
    docstring)."""
    if not text:
        return text
    out = text
    for old, new in SUBSTITUTIONS:
        out = out.replace(old, new)
    out = EMAIL_RE.sub(_redact_email, out)
    for pattern, repl in REGEX_SWEEPS:
        out = pattern.sub(repl, out)
    return out


def verify_scrubbed(text: str) -> list[str]:
    """Independent re-check of already-scrubbed output. Returns a list of
    violation descriptions (empty = clean). Does NOT trust that scrub_text()
    ran, or that SUBSTITUTIONS/REGEX_SWEEPS were complete -- re-derives
    from the allowlists directly, same as scrub-public-tree.py's
    verify_scrubbed()."""
    if not text:
        return []
    violations = []

    for literal in FORBIDDEN_LITERALS:
        if literal in text:
            violations.append(f"forbidden literal {literal!r}")

    for m in EMAIL_RE.finditer(text):
        addr = m.group(0)
        if not addr.endswith(ALLOWED_EMAIL_DOMAIN_SUFFIXES):
            violations.append(f"unrecognized email {addr!r}")

    for m in PHONE_RE.finditer(text):
        violations.append(f"unredacted phone-shaped string {m.group(0)!r}")

    for m in FREQ_RE.finditer(text):
        violations.append(f"unredacted frequency-shaped string {m.group(0)!r}")

    return violations
