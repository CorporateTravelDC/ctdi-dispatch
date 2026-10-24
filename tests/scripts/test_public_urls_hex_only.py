"""2026-10-22 (operator hard rule): any aircraft link on a public surface is keyed by ICAO hex only
(`?icao=<hex>`) -- never a tail / registration and never a flight number / callsign. Forbidden
example: https://www.airnavradar.com/flight/WN4963 .

Scans src/ (Python f-strings, str.format templates and `"..." + x` concatenation; JS/TS template
literals and `'...' + x` concatenation) for URLs to the public tracking sites and fails when
  * an interpolated expression names reg / tail / registration / callsign / ident / flight, or
  * the literal URL text carries /flight/, /registration/ or a reg= / flight= / callsign= / tail=
    query key.
Server-side API calls (aeroapi.flightaware.com) are not links and are excluded.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"

TRACKERS = re.compile(r"globe\.airplanes\.live|map\.opensky-network\.org|airnavradar\.com|flightaware\.com/live"
                      r"|flightradar24\.com|planefinder\.net|adsbexchange")
EXCLUDED_HOSTS = re.compile(r"aeroapi\.flightaware\.com")
BAD_NAME = re.compile(r"(?i)reg|tail|registration|callsign|ident|flight")
BAD_PATH = re.compile(r"(?i)/flight/|/registration/|[?&](reg|registration|tail|flight|callsign)=")
JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs"}
# 2026-10-24: + "static" -- src/runner/static is the vite build output (gitignored); scanning a 460 KB
# one-line minified bundle made this test run for an hour and timed out a relay rehearsal.
SKIP_DIRS = {"node_modules", "dist", "build", "__pycache__", ".vite", "static"}


def _names(expr: ast.AST) -> list[str]:
    out = []
    for n in ast.walk(expr):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)                                  # row["registration"]
        elif isinstance(n, ast.keyword) and n.arg:
            out.append(n.arg)
    return out


def _check(where: str, literal: str, names: list[str], out: list[str]) -> None:
    if not TRACKERS.search(literal) or EXCLUDED_HOSTS.search(literal):
        return
    if BAD_PATH.search(literal):
        out.append(f"{where}: tracker URL path/query keyed by a tail or flight: {literal!r}")
    bad = sorted({n for n in names if BAD_NAME.search(n)})
    if bad:
        out.append(f"{where}: tracker URL interpolates {bad} (hex only: ?icao=<hex>)")


def scan_python(text: str, where: str) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):                      # f"https://...{x}"
            lit = "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
                          for v in node.values)
            names = [n for v in node.values if isinstance(v, ast.FormattedValue) for n in _names(v.value)]
            _check(f"{where}:{node.lineno}", lit, names, out)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format" \
                and isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str):
            lit = node.func.value.value                          # "https://...{reg}".format(reg=x)
            fields = re.findall(r"\{([^{}!:]*)", lit)
            names = fields + [n for a in node.args for n in _names(a)] + \
                [k.arg for k in node.keywords if k.arg] + [n for k in node.keywords for n in _names(k.value)]
            _check(f"{where}:{node.lineno}", lit, names, out)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)) \
                and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            lit = node.left.value                                # "https://...?q=" + x / "...%s" % x
            _check(f"{where}:{node.lineno}", lit, _names(node.right), out)
    return out


TEMPLATE = re.compile(r"`((?:\\.|[^`\\])*)`", re.S)
CONCAT = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1\s*\+\s*([A-Za-z_$][\w$.\[\]'"]*)""")


def scan_js(text: str, where: str) -> list[str]:
    out: list[str] = []
    for m in TEMPLATE.finditer(text):
        body = m.group(1)
        exprs = re.findall(r"\$\{([^}]*)\}", body)
        lit = re.sub(r"\$\{[^}]*\}", "{}", body)
        names = [w for e in exprs for w in re.findall(r"[A-Za-z_$][\w$]*", e)]
        _check(f"{where}:{text.count(chr(10), 0, m.start()) + 1}", lit, names, out)
    for m in CONCAT.finditer(text):
        _check(f"{where}:{text.count(chr(10), 0, m.start()) + 1}", m.group(2),
               re.findall(r"[A-Za-z_$][\w$]*", m.group(3)), out)
    return out


def _files():
    for p in sorted(SRC.rglob("*")):
        if p.is_file() and not (SKIP_DIRS & set(p.relative_to(SRC).parts)) \
                and (p.suffix == ".py" or p.suffix in JS_SUFFIXES):
            yield p


def test_public_tracker_links_are_keyed_by_icao_hex_only():
    findings, links = [], 0
    for p in _files():
        text = p.read_text(errors="replace")
        if not TRACKERS.search(text):
            continue
        links += len(TRACKERS.findall(text))
        rel = str(p.relative_to(REPO))
        findings += scan_python(text, rel) if p.suffix == ".py" else scan_js(text, rel)
    assert links > 0, "scanner found no tracker links at all -- the pattern list is stale"
    assert not findings, "\n".join(findings)


def test_the_scanner_catches_the_forbidden_shapes():
    bad_py = [
        'u = f"https://www.airnavradar.com/flight/{callsign}"',
        'u = f"https://globe.airplanes.live/?icao={hex_id}&x={row[\'registration\']}"',
        'u = "https://www.flightaware.com/live/flight/{}".format(ident)',
        'u = "https://www.flightradar24.com/data/aircraft/" + tail_number',
        'u = f"https://globe.airplanes.live/?reg={x}"',
    ]
    for src in bad_py:
        assert scan_python(src, "t"), src
    assert not scan_python('u = f"https://globe.airplanes.live/?icao={hex_id}"', "t")
    assert not scan_python('u = f"https://aeroapi.flightaware.com/aeroapi/flights/{ident}"', "t")   # server-side API
    assert scan_js("const u = `https://www.airnavradar.com/flight/${flightNumber}`", "t")
    assert scan_js("const u = 'https://globe.airplanes.live/?icao=' + registration", "t")
    assert not scan_js("const u = `https://globe.airplanes.live/?icao=${hex}`", "t")
