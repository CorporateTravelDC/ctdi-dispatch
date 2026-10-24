"""
tests/scripts/test_site_public.py -- website public mirrors (2026-10-06): keep
the brand, map infrastructure to RFC documentation addresses, never publish a
secret, an unlisted email or a real public IP; allowlist only.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("site_public", ROOT / "scripts" / "site_public.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


def test_addresses_map_to_rfc_documentation_ranges_and_cidr_stays():
    src = (b"proxy 100.x.x.x:8000; lan 10.x.x.x; home 192.168.x.x; dock 172.x.x.x; lo 127.0.0.1; "
           b"allow 100.64.0.0/10; host dispatch.tailxxxxxxx.ts.net; tunnel 00000000-0000-0000-0000-000000000000")
    out = sp.scrub(src)
    assert b"192.0.2.100:8000" in out and b"198.51.100.40" in out and b"203.0.113.22" in out and b"203.0.113.5" in out
    assert b"127.0.0.1" in out and b"allow 100.64.0.0/10" in out
    assert b"tailnet.example" in out and b"tailxxxxxxx" not in out
    assert sp.NIL_UUID in out and b"8c644990" not in out
    assert sp.scrub(b"brand example.com stays") == b"brand example.com stays"


def test_gate_refuses_secrets_unlisted_emails_and_public_ips():
    secrets = {"NTFY_TOKEN": b"tk_live_value_123456"}
    data = (b"token tk_live_value_123456\nPAT github_pat_" + b"A" * 30 + b"\nmail ops@internal.example.org\n"
            b"server 8.8.8.8\nok reservations@example.com\n")
    bad = sp.gate("f.conf", data, secrets, {"reservations@example.com"}, set())
    kinds = " ".join(bad)
    assert "live secret value (NTFY_TOKEN)" in kinds and "github-token" in kinds
    assert "email not in PUBLIC_EMAILS" in kinds and "public IP not in PUBLIC_IPS" in kinds
    assert "tk_live_value" not in kinds and "github_pat_" not in kinds          # values never printed
    assert sp.gate("ok", b"api_key=your-key-here\npassword: changeme-placeholder-x", {}, set(), set()) == []


def test_allowlist_last_rule_wins():
    pats = [(True, "contact-api/*"), (False, "contact-api/MANIFEST.sha256*"), (True, "www/*")]
    files = ["contact-api/main.py", "contact-api/MANIFEST.sha256", "articles/x.md", "www/index.html"]
    assert sp.selected(files, pats) == ["contact-api/main.py", "www/index.html"]


def _git_repo(tmp_path, files):
    import subprocess
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "t"]):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)
    return sorted(files)


BODY = "The paid paragraph only members should read.\n"
FRONT = '---\ntitle: "Secret Title"\nexcerpt: "member-only dek"\n---\n' + BODY


def test_ledger_single_glob_keeps_the_original_layout(tmp_path):
    files = _git_repo(tmp_path, {"articles/a.md": BODY})
    out = sp.ledger(tmp_path, sp.ledger_files({"LEDGER_GLOB": "articles/*.md"}, files))
    assert out.count("```") == 2 and "## `" not in out
    assert out.rstrip().splitlines()[-2].endswith("  a")


def test_ledger_both_sources_hashed_never_text(tmp_path):
    """2026-10-21 (operator): articles/ AND _md/, hashed only -- no body, title or excerpt,
    so the public ledger is never a bypass of the subscription or the members invite."""
    import hashlib
    files = _git_repo(tmp_path, {"articles/a.md": BODY, "_md/a.md": FRONT, "_md/new-piece.md": FRONT})
    out = sp.ledger(tmp_path, sp.ledger_files({"LEDGER_GLOB": "articles/*.md _md/*.md"}, files))
    for leak in ("paid paragraph", "Secret Title", "member-only dek", "title:", "excerpt"):
        assert leak not in out
    assert "## `articles/*.md`" in out and "## `_md/*.md`" in out
    assert hashlib.sha256(BODY.encode()).hexdigest() in out
    assert hashlib.sha256(FRONT.encode()).hexdigest() in out
    assert out.count("  new-piece") == 1


def test_executive_standard_ledger_covers_the_publish_directory():
    """The publisher writes _md/<slug>.md; a ledger that does not cover _md/ silently froze on
    2026-10-06 while every publish re-signed it."""
    conf = Path("/opt/corporatetraveldc/private/executivestandard-website/PUBLIC.conf")
    if not conf.exists():
        import pytest
        pytest.skip("site repo not on this host")
    globs = sp.ledger_globs(sp._conf(conf.parent))
    assert "_md/*.md" in globs and "articles/*.md" in globs
