"""HTTP/2 stays off everywhere (operator 2026-11-19: nginx HTTP/2 CVE; Fedora ships nginx 1.30.6,
the fixed 1.31.9 is not packaged). Re-enable only after nginx >= 1.31.9: remove the `http2 off;`
lines and this test together, in one signed change."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACTIVE_ON = re.compile(r"^\s*http2\s+on\s*;", re.M)
LEGACY_LISTEN = re.compile(r"^\s*listen[^;#\n]*\shttp2\b", re.M)
SERVER_OPEN = re.compile(r"^\s*server\s*\{\s*(#.*)?$", re.M)
OFF_LINE = re.compile(r"^\s*http2\s+off\s*;", re.M)


def _nginx_files() -> list[Path]:
    files = list((REPO / "nginx").rglob("*.conf"))
    files += list((REPO / "config").glob("nginx-*.conf"))
    files += list((REPO / "scripts" / "templates").glob("*nginx*.tmpl"))
    return sorted(f for f in files if "retired-" not in str(f))


def test_there_are_nginx_configs_to_check():
    assert len(_nginx_files()) >= 10


def test_no_active_http2_on_or_legacy_listen_http2():
    bad = []
    for f in _nginx_files():
        text = f.read_text()
        for pat in (ACTIVE_ON, LEGACY_LISTEN):
            for m in pat.finditer(text):
                bad.append(f"{f.relative_to(REPO)}: {m.group(0).strip()}")
    assert not bad, "HTTP/2 must stay off until nginx >= 1.31.9:\n" + "\n".join(bad)


def test_every_server_block_says_http2_off():
    missing = []
    for f in _nginx_files():
        text = f.read_text()
        n_server = len(SERVER_OPEN.findall(text))
        n_off = len(OFF_LINE.findall(text))
        if n_server and n_off < n_server:
            missing.append(f"{f.relative_to(REPO)}: {n_server} server blocks, {n_off} `http2 off;`")
    assert not missing, "\n".join(missing)


def test_http_level_default_file():
    f = REPO / "nginx" / "conf.d" / "00-http2-off.conf"
    assert OFF_LINE.search(f.read_text())


def test_drift_check_guards_live_nginx():
    s = (REPO / "scripts" / "check-platform-drift.sh").read_text()
    assert "HTTP/2 enabled (must stay off" in s and "/etc/nginx" in s
