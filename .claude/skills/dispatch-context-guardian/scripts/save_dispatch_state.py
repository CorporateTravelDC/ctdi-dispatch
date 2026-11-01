#!/usr/bin/env python3
"""Manual checkpoint save; reads the lifecycle event JSON from stdin."""
import sys
from context_hook import main
if __name__=="__main__":
    sys.argv.extend(["--action","save"])
    raise SystemExit(main())
