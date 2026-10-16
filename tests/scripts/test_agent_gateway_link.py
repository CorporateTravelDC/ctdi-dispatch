"""2026-10-16: linking a vendor MCP client from a headless box. The consent page is fetched and parsed,
the approval signed, the status endpoint polled, and the loopback callback delivered locally -- the code
is never printed. Fake fetcher and fake approver; no network."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/agent-gateway-link.sh"
BASE = "https://gateway.example.invalid"
REQ = "ar_testreq"
AID = "00000000-0000-0000-0000-00000000000c"


def _fake(tmp: Path, redirect: str):
    """A fake curl: records every URL, answers the consent page, then the status JSON, then the callback."""
    log = tmp / "urls.log"
    page = f"<pre>scripts/approve.sh allow {AID}</pre><script>fetch('/oauth/authorize/status?req={REQ}')</script>"
    f = tmp / "fetch.sh"
    f.write_text(f"""#!/usr/bin/env bash
url="${{@: -1}}"; echo "$url" >> "{log}"
case "$url" in
  */oauth/authorize/status*) if [ -f "{tmp}/signed" ]; then printf '%s' '{{"state":"redirect","location":"{redirect}"}}'; else printf '%s' '{{"state":"pending"}}'; fi ;;
  */oauth/authorize*) printf '%s' "{page}" ;;
  *) echo ok ;;
esac
""")
    a = tmp / "approve.sh"
    a.write_text(f"#!/usr/bin/env bash\necho \"$1\" > \"{tmp}/signed\"\n")
    for p in (f, a):
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return log, f, a


def _run(tmp: Path, url: str, redirect: str):
    log, f, a = _fake(tmp, redirect)
    env = dict(os.environ, GATEWAY_BASE_URL=BASE, LINK_FETCH=str(f), LINK_APPROVE=str(a), LINK_POLL_SLEEP="0")
    r = subprocess.run(["bash", str(SCRIPT), url], env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    return r, log


def test_full_link_delivers_loopback_callback_without_printing_the_code(tmp_path):
    redirect = "http://127.0.0.1:37149/callback/abc?code=SECRETCODE123&state=s1"
    r, log = _run(tmp_path, f"{BASE}/oauth/authorize?response_type=code&resource={BASE}/mcp/codex", redirect)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "signed").read_text().strip() == AID
    urls = log.read_text().splitlines()
    assert urls[-1] == redirect and f"status?req={REQ}" in "".join(urls)
    assert "SECRETCODE123" not in r.stdout + r.stderr


def test_non_loopback_redirect_is_not_fetched(tmp_path):
    r, log = _run(tmp_path, f"{BASE}/oauth/authorize?response_type=code", "https://vendor.example.invalid/cb?code=x")
    assert r.returncode == 2 and "vendor.example.invalid" not in log.read_text().splitlines()[-1]


def test_wrong_gateway_url_refused(tmp_path):
    r, _ = _run(tmp_path, "https://other.example.invalid/oauth/authorize?x=1", "http://127.0.0.1:1/cb")
    assert r.returncode == 64
