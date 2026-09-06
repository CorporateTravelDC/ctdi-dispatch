#!/usr/bin/env python3
"""
corporatetraveldc web server entry point.
Run as: python3 -m web.serve
Or: uvicorn web.main:app --host 127.0.0.1 --port 8000
"""

import sys
from pathlib import Path

# Ensure src/ is on the path.
_src = Path(__file__).parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import uvicorn
from common import config

if __name__ == "__main__":
    uvicorn.run(
        "web.main:app",
        host=config.web_host(),
        port=config.web_port(),
        log_level="info",
        access_log=True,
        proxy_headers=True,    # Trust X-Forwarded-* from nginx.
        forwarded_allow_ips="*",  # nginx on same host.
    )
