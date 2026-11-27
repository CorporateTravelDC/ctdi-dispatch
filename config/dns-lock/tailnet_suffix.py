#!/usr/bin/env python3
"""tailnet_suffix.py -- print this tailnet's MagicDNS suffix (or, with --self, this host's MagicDNS name) from a
saved `tailscale status --json` file. Used by config/dns-lock/apply.sh. Prints the one name only; exit 1 if absent."""
import json
import sys

st = json.load(open(sys.argv[1]))
suffix = (st.get("MagicDNSSuffix") or (st.get("CurrentTailnet") or {}).get("MagicDNSSuffix") or "").strip(".")
if not suffix:
    sys.exit(1)
if "--self" in sys.argv[2:]:
    name = ((st.get("Self") or {}).get("DNSName") or "").strip(".")
    if not name.endswith("." + suffix):
        sys.exit(1)
    print(name)
else:
    print(suffix)
