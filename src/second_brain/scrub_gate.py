"""
second_brain.scrub_gate -- CUI/PII scrub gate for second-brain ingestion.

Non-negotiable per the original second-brain plan (2026-07-18): every
ingestion path -- daily/weekly automated pulls, manual "remember this"
captures, and the RSS poller -- runs content through this gate before it's
written to the vault. A compounding knowledge base is exactly the wrong
place for CUI/FOUO radio data (SHARES/HEARS/HEART) to quietly persist.

This is a BLOCK gate, not a launder gate: if it finds something that looks
like CUI radio data or PII, it refuses to write the content at all and
raises ScrubGateBlocked, rather than silently redacting it. Redacting
CUI-shaped content in an automated pipeline hides the fact that a human
needs to look at it -- worse than just stopping and saying so.

Honest scope note: this is a first-pass heuristic gate (regex-based),
not exhaustive. It catches the specific, known shapes called out in the
project's CUI handling rules. Extend `_scan` if a new CUI/PII shape is
found getting through.
"""
import re


class ScrubGateBlocked(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("content blocked by CUI/PII scrub gate: " + "; ".join(reasons))


# Program-name triggers -- SHARES/HEARS/HEART are the CUI radio programs
# named in this project's CUI handling rules. Matching the name alone is
# too broad (this very docstring mentions them) -- only flag when a
# frequency-shaped token also appears nearby.
_CUI_PROGRAM_NAMES = re.compile(r"\b(SHARES|HEARS|HEART)\b")

# Frequency-shaped tokens: NNN.NNNN or NNN.NNN MHz-style, the actual
# sensitive payload in radio reference material.
_FREQ_SHAPED = re.compile(r"\b\d{3}\.\d{3,4}\b")

# PII: SSN-shaped tokens.
_SSN_SHAPED = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def scan(text: str) -> list[str]:
    """Return a list of human-readable reasons content was flagged. Empty list = clean."""
    reasons = []
    if _CUI_PROGRAM_NAMES.search(text) and _FREQ_SHAPED.search(text):
        reasons.append(
            "CUI radio program name (SHARES/HEARS/HEART) co-occurring with a "
            "frequency-shaped token"
        )
    if _SSN_SHAPED.search(text):
        reasons.append("SSN-shaped token")
    return reasons


def gate(text: str, *, source: str = "unknown") -> str:
    """Raise ScrubGateBlocked if text fails the scan; otherwise return text unchanged."""
    reasons = scan(text)
    if reasons:
        raise ScrubGateBlocked([f"[{source}] {r}" for r in reasons])
    return text
