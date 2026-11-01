#!/usr/bin/env python3
"""Install/read the single shared context; never signs or publishes a repo."""
import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from common.platform_context import CONTEXT_PATH, ContextUnavailable, read_context, signed_source


def install():
    if os.getuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != "corporatetraveldc":
        raise ContextUnavailable("installation requires the corporatetraveldc operator terminal")
    # Operator only: the existing verifier validates the signature and pin.
    checked = subprocess.run([str(ROOT / "scripts/verify-manifest.sh"),
                              "config/platform-context.md", "src/common/platform_context.py"],
                             cwd=ROOT, capture_output=True)
    if checked.returncode:
        raise ContextUnavailable("native signature/manifest verification failed; context not installed")
    data = signed_source(ROOT)
    parent = CONTEXT_PATH.parent
    if parent.is_symlink():
        raise ContextUnavailable("shared context directory must not be a symlink")
    parent.mkdir(mode=0o2750, exist_ok=True)
    info = parent.stat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ContextUnavailable("shared context directory must be private and operator-owned")
    gid = grp.getgrnam("ctdc-dev").gr_gid
    os.chown(parent, -1, gid)
    os.chmod(parent, 0o2750)
    if CONTEXT_PATH.is_symlink():
        raise ContextUnavailable("shared context target must not be a symlink")
    fd, name = tempfile.mkstemp(prefix=".platform-context-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o640)
            os.fchown(handle.fileno(), -1, gid)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, CONTEXT_PATH)
        dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return read_context(root=ROOT)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["read", "json", "hook", "install"])
    action = ap.parse_args().action
    try:
        out = install() if action == "install" else read_context(root=ROOT)
        if action == "hook":
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                             "additionalContext": out["content"]}}))
        elif action in ("json", "install"):
            print(json.dumps({k: v for k, v in out.items() if k != "content"}, indent=2))
        else:
            print(out["content"], end="")
        return 0
    except (ContextUnavailable, OSError) as exc:
        print("Shared platform context unavailable: " +
              (str(exc) if isinstance(exc, ContextUnavailable) else type(exc).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
