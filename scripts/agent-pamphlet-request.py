#!/usr/bin/env python3
"""Request the fixed root-managed delivery action; no supplied command content."""
import os
from pathlib import Path
import stat
import sys
import time


def request(path):
    # A fixed pathname in a root-owned directory; no account-supplied target.
    expected = Path('/var/lib/ctdc-agent-pamphlets/refresh-request')
    if Path(path) != expected:
        raise ValueError('unexpected request path')
    fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'w') as f:
        meta = os.fstat(f.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0:
            raise ValueError('root-managed delivery service is not installed')
        epoch = time.time()
        f.write(str(epoch) + '\n')
        f.flush()
        os.fsync(f.fileno())
    return epoch


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('one request path required')
    print(request(sys.argv[1]))
