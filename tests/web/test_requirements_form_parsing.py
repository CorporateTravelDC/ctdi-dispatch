"""
2026-10-05: /oauth/token and every /console form call request.form(), which
needs python-multipart. The web image did not install it (the dev host did), so
Claude's token exchange 500'd in production while every test passed. Any route
that parses form data must have python-multipart in requirements.txt.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def test_form_parsing_dependency_is_declared():
    users = [p for p in (ROOT / "src" / "web").rglob("*.py")
             if "request.form()" in p.read_text() or "Form(" in p.read_text()]
    assert users, "expected at least the OAuth token route to parse forms"
    reqs = (ROOT / "requirements.txt").read_text().lower()
    assert "python-multipart" in reqs, f"python-multipart missing; form parsers: {[str(u) for u in users]}"
