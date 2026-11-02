"""poller/skills/pia_designator_watch.py -- keep the PIA call-sign prefixes current (2026-11-02).

Operator 2026-11-02: watch FAA Order JO 7340.2 (Contractions) for updates and keep the
withheld PIA call-sign prefixes in step with it.

What the FAA publishes, and how this follows it:
  * JO 7340.2 and its changes publish every 112 days (Basic, Change 1-3), on the web 6
    days before each effective date (7340.2 section 1-1-6). Out-of-cycle notices
    (N JO 7340.9xx) cancel designators between editions. The 3-letter designator table
    (chapter 3 section 1) does not mark which designators are PIA providers.
  * The approved PIA third-party call-sign providers are named only on the FAA ADS-B
    Privacy page ("a third-party call sign can be obtained from FlightPlan, ForeFlight,
    FlightAware, or ARINCDirect").

Each run (daily, from the poller's SKILL_SCHEDULE):
  1. Fetch the 7340.2 index page (about 10 KB) and record its edition / change /
     effective date. That is the monitor: a new edition shows up within a day.
  2. When that signature changes, when the last full check is 28+ days old (the monthly
     backstop that also covers out-of-cycle notices once incorporated), or with --force:
     fetch the designator table and the privacy page, then
       - a provider named on the privacy page whose designator is not yet withheld is
         ADDED to pia_callsign_prefixes at once (withholding more is the safe direction);
       - a provider that cannot be matched to a designator, a withheld prefix missing
         from the table, or a provider dropped from the privacy page is ALERTED only.
         Nothing is ever removed automatically; db.PIA_CALLSIGN_PREFIXES stays the floor.
  3. Write a status report to <state_dir>/pia-designator-watch.txt.
Alerts go to ntfy ops-health and the board's coord thread. Fetch failures are logged;
14 days without a successful index check raises one alert.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import pathlib
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from common import config, db

SKILL_NAME = "pia-designator-watch"
log = logging.getLogger(SKILL_NAME)

INDEX_URL = "https://www.faa.gov/air_traffic/publications/atpubs/cnt_html/"
TABLE_URL = "https://www.faa.gov/air_traffic/publications/atpubs/cnt_html/chap3_section_1.html"
PRIVACY_URL = "https://www.faa.gov/air_traffic/technology/equipadsb/privacy"
USER_AGENT = "corporatetraveldc-dispatch/1.0 (+pia-designator-watch)"
FULL_CHECK_S = 28 * 86400
INDEX_MIN_S = 20 * 3600
STALE_ALERT_S = 14 * 86400
CYCLE_DAYS = 112
CYCLE_ANCHOR = date(2026, 10, 29)        # 7340.2R Basic, per the 7340.2P publication schedule

# Provider name on the privacy page -> words that identify its row in the designator table.
_ALIASES = {"FLIGHTPLAN": "FLTPLAN", "FLTPLAN": "FLTPLAN", "ARINCDIRECT": "ARINC", "ARINC": "ARINC",
            "FOREFLIGHT": "FOREFLIGHT", "FLIGHTAWARE": "FLIGHTAWARE"}


def _ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return                                   # pg_schema/0086_pia_designator_watch.sql
    c.execute("""CREATE TABLE IF NOT EXISTS pia_callsign_prefixes (prefix TEXT PRIMARY KEY, provider TEXT,
        telephony TEXT, source TEXT NOT NULL, added_at REAL NOT NULL, last_seen_at REAL, missing_since REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS pia_watch_state (key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at REAL NOT NULL)""")


def _q(sql: str, args: tuple = ()):
    with db.conn() as c:
        _ensure(c)
        cur = c.execute(sql, args)
        if sql.lstrip().upper().startswith("SELECT"):
            return [dict(r) for r in cur.fetchall()]
        return cur.rowcount


def _get_state() -> dict:
    return {r["key"]: r["value"] for r in _q("SELECT key, value FROM pia_watch_state")}


def _set_state(**kv) -> None:
    for k, v in kv.items():
        _q("DELETE FROM pia_watch_state WHERE key = ?", (k,))
        _q("INSERT INTO pia_watch_state (key, value, updated_at) VALUES (?, ?, ?)", (k, str(v), time.time()))


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 -- fixed https URLs
        return r.read().decode("utf-8", errors="replace")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_edition(index_html: str) -> str:
    """'7340.2P Change 3 Effective 7/9/2026' style signature ('' if unrecognised)."""
    t = _text(index_html)
    ed = re.search(r"7340\.2\s?([A-Z])\b", t)
    ch = re.search(r"Change:?\s*(Change\s*)?(\d|Basic)", t, re.I)
    eff = re.search(r"Effective:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", t, re.I)
    if not ed:
        return ""
    return " ".join(x for x in (f"7340.2{ed.group(1)}", f"Change {ch.group(2)}" if ch else "",
                                f"Effective {eff.group(1)}" if eff else "") if x)


def parse_designators(table_html: str) -> dict[str, dict]:
    """{3LD: {company, country, telephony}} from the chapter 3 section 1 table."""
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.S):
        cells = [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)]
        if len(cells) >= 4 and re.fullmatch(r"[A-Z]{3}", cells[-1] or ""):
            out[cells[-1]] = {"company": cells[0], "country": cells[1], "telephony": cells[2]}
    return out


def parse_providers(privacy_html: str) -> list[str]:
    """Provider names from 'a third-party call sign can be obtained from A, B, C, or D.'"""
    t = _text(privacy_html)
    m = re.search(r"third[- ]party call ?sign (?:can|may) be obtained from (.+?)\.", t, re.I)
    if not m:
        return []
    names = re.split(r",\s*(?:or\s+|and\s+)?|\s+or\s+|\s+and\s+", m.group(1))
    return [n.strip() for n in names if n.strip()]


def match_designator(provider: str, designators: dict[str, dict]) -> str | None:
    key = re.sub(r"[^A-Z]", "", provider.upper())
    word = _ALIASES.get(key, key)
    hits = [d for d, row in designators.items() if row.get("country") == "UNITED STATES"
            and (re.sub(r"[^A-Z]", "", row["company"].upper()).startswith(word)
                 or f"({word})" in re.sub(r"[^A-Z()]", "", row["company"].upper())
                 or re.sub(r"[^A-Z]", "", row["telephony"].upper()) == word)]
    return hits[0] if len(hits) == 1 else None


def withheld_prefixes() -> dict[str, dict]:
    rows = {r["prefix"]: r for r in _q("SELECT * FROM pia_callsign_prefixes")}
    for p in db.PIA_CALLSIGN_PREFIXES:
        rows.setdefault(p, {"prefix": p, "source": "code", "provider": None, "missing_since": None})
    return rows


def expected_effective(today: date | None = None) -> date:
    """The next scheduled 7340.2 effective date on the 112-day cycle (today or later)."""
    today = today or datetime.now(timezone.utc).date()
    n = max(0, -(-(today - CYCLE_ANCHOR).days // CYCLE_DAYS))
    return CYCLE_ANCHOR + timedelta(days=CYCLE_DAYS * n)


def alert(title: str, body: str) -> None:
    log.warning("%s: %s -- %s", SKILL_NAME, title, body)
    try:
        from common import ntfy_push
        ntfy_push.send("ops-health", body, title=title, priority=3, tags="airplane,lock")
    except Exception as e:  # noqa: BLE001
        log.warning("ntfy failed: %s", type(e).__name__)
    try:
        db.board_insert("pia-watch", "dispatch", "coord", title, body)
    except Exception as e:  # noqa: BLE001
        log.warning("board post failed: %s", type(e).__name__)


def full_check(designators: dict[str, dict], providers: list[str], now: float) -> list[str]:
    """Reconcile; returns report lines. Adds, never removes."""
    lines = []
    held = withheld_prefixes()
    if not providers:
        alert("PIA watch: provider list not found", f"The FAA ADS-B Privacy page no longer has the third-party "
              f"call-sign sentence ({PRIVACY_URL}). Nothing changed; check the page by hand.")
    seen_prefixes = set()
    for name in providers:
        d = match_designator(name, designators)
        if d is None:
            alert("PIA watch: provider without a designator", f"'{name}' is listed as a PIA call-sign provider "
                  "but no single US designator in JO 7340.2 matches it. Add its prefix by hand.")
            lines.append(f"provider {name}: NO DESIGNATOR MATCH")
            continue
        seen_prefixes.add(d)
        lines.append(f"provider {name}: {d} ({designators[d]['telephony']})")
        if d not in held:
            _q("INSERT INTO pia_callsign_prefixes (prefix, provider, telephony, source, added_at, last_seen_at) "
               "VALUES (?, ?, ?, ?, ?, ?)", (d, name, designators[d]["telephony"], "faa-privacy-page", now, now))
            alert("PIA watch: new provider withheld", f"{name} ({d}, telephony {designators[d]['telephony']}) "
                  "is now an FAA PIA call-sign provider; its call signs are withheld from now on.")
        else:
            _q("UPDATE pia_callsign_prefixes SET last_seen_at = ?, missing_since = NULL WHERE prefix = ?", (now, d))
    for p, row in held.items():
        if p not in designators:
            if not row.get("missing_since"):
                _q("UPDATE pia_callsign_prefixes SET missing_since = ? WHERE prefix = ?", (now, p))
                alert("PIA watch: withheld prefix missing from JO 7340.2", f"{p} is no longer in the designator "
                      "table (cancelled or renamed). It stays withheld until you remove it by hand.")
            lines.append(f"prefix {p}: MISSING from 7340.2 (still withheld)")
        elif providers and p not in seen_prefixes:
            lines.append(f"prefix {p}: not named on the privacy page (still withheld)")
    return lines


def main(force: bool = False) -> int:
    now = time.time()
    st = _get_state()
    last_index = float(st.get("last_index_ok", 0) or 0)
    if not force and now - float(st.get("last_index_try", 0) or 0) < INDEX_MIN_S:
        return 0
    _set_state(last_index_try=now)
    report = [f"{SKILL_NAME} run {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(now))}"]
    try:
        sig = parse_edition(fetch(INDEX_URL))
    except Exception as e:  # noqa: BLE001
        log.warning("index fetch failed: %s", type(e).__name__)
        if last_index and now - last_index > STALE_ALERT_S and st.get("stale_alerted") != "1":
            alert("PIA watch: FAA index unreachable", "No successful JO 7340.2 check in 14 days.")
            _set_state(stale_alerted=1)
        return 1
    _set_state(last_index_ok=now, stale_alerted=0)
    changed = bool(sig) and sig != st.get("edition", "")
    due = now - float(st.get("last_full_ok", 0) or 0) >= FULL_CHECK_S
    report.append(f"edition: {sig or 'UNRECOGNISED'} (previous: {st.get('edition') or 'none'})")
    report.append(f"next scheduled effective date on the 112-day cycle: {expected_effective().isoformat()}")
    if changed or due or force:
        try:
            designators = parse_designators(fetch(TABLE_URL, timeout=90))
            providers = parse_providers(fetch(PRIVACY_URL))
        except Exception as e:  # noqa: BLE001
            log.warning("full check fetch failed: %s", type(e).__name__)
            return 1
        if len(designators) < 1000:
            alert("PIA watch: designator table unreadable", f"Parsed only {len(designators)} designators from "
                  f"{TABLE_URL}; nothing changed.")
            return 1
        report += full_check(designators, providers, now)
        _set_state(last_full_ok=now, providers=json.dumps(providers),
                   table_sha=hashlib.sha256(json.dumps(designators, sort_keys=True).encode()).hexdigest()[:16])
        if changed and st.get("edition"):
            alert("PIA watch: JO 7340.2 updated", f"{st.get('edition')} -> {sig}. PIA prefixes re-checked: "
                  + "; ".join(report[3:]))
    if sig:
        _set_state(edition=sig)
    report.append("withheld prefixes: " + ", ".join(sorted(withheld_prefixes())))
    p = pathlib.Path(config.state_dir()) / "pia-designator-watch.txt"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(report) + "\n")
    except OSError as e:
        log.warning("report write failed: %s", e)
    log.info("%s", " | ".join(report))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="fetch and reconcile now")
    raise SystemExit(main(force=ap.parse_args().force))
