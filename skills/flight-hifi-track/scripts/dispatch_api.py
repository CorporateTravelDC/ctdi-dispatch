"""Small on-box API client for this skill; never reads shared/operator secrets.

Callers choose the authorized action and sanitize returned data before display.
This module does not add watches, grant permissions or send notifications itself.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import stat
from urllib import error, request

BASE_URL = "http://100.x.x.x:8000"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class DispatchError(RuntimeError):
    """Sanitized failure: no bearer value, raw response, URL or exception text."""


def _token_path() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config/ctdc/api-token"


def personal_token() -> str:
    try:
        fd = os.open(_token_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise DispatchError("Personal token owner/type/permissions invalid; no request sent")
            value = handle.read(4097).strip()
        if not value or len(value) > 4096 or any(c.isspace() for c in value):
            raise DispatchError("Personal token empty or malformed; no request sent")
        return value
    except DispatchError:
        raise
    except (OSError, UnicodeError):
        raise DispatchError("Personal token unavailable; request operator provisioning") from None


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DispatchError("Dispatch redirect refused; bearer was not forwarded")


def request_json(path: str, *, method: str = "GET", payload: dict | None = None,
                 auth: bool = True, timeout: float = 20) -> tuple[int, dict | list]:
    """One request, no retries. auth=False permits genuinely public reads only.

    A write timeout means unknown outcome: inspect the target if authorized
    before retrying. A 201/202 has only its route-specific meaning.
    """
    if not path.startswith("/") or path.startswith("//") or "#" in path:
        raise DispatchError("Use a relative dispatch route, never another API base")
    if method not in {"GET", "POST"} or (method == "GET" and payload is not None):
        raise DispatchError("Unsupported request method/body")
    if method == "POST" and not auth:
        raise DispatchError("Unauthenticated writes are not supported")
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = "Bearer " + personal_token()
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with request.build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise DispatchError("Dispatch response too large; body withheld")
            result = json.loads(raw)
            if not isinstance(result, (dict, list)):
                raise DispatchError("Unexpected dispatch response shape; body withheld")
            return response.status, result
    except error.HTTPError as exc:
        raise DispatchError(f"Dispatch returned HTTP {exc.code}; body withheld") from None
    except (error.URLError, OSError, TimeoutError):
        raise DispatchError("No confirmed response; write outcome may be unknown; do not blindly retry") from None
    except (ValueError, UnicodeError):
        raise DispatchError("Invalid dispatch JSON; body withheld") from None
