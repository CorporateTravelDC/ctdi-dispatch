#!/usr/bin/env python3
"""scripts/scrub-public-tree.example.py
Reference template for scripts/scrub-public-tree.py -- ships with zero real
values on purpose. Copy this file (and push-public.example.sh) into your own
repo, drop the ".example" suffix, then replace DROP_FILES and SUBSTITUTIONS
below with your own real secrets/identifiers. This file is never touched by
CTDI's own push-public.sh -- it exists only as a working reference for other
operators running a similar private<>public repo split.

Mechanism (unchanged from the real script): walks a git tree object via
plumbing commands only (ls-tree / cat-file / hash-object / mktree) -- it
never touches your working directory or checks anything out. The input tree
stays exactly as committed; a new, separate sanitized tree object is built
and returned. Your real branch is never mutated.

Usage: python3 scripts/scrub-public-tree.py <tree-sha>
"""
import subprocess, sys

# Files to drop entirely from the public tree (relative to repo root, matched
# by basename). Put anything here that must never appear in public history,
# even scrubbed -- full credential files, operator-identifying status pages,
# real config with no safe placeholder form.
DROP_FILES = {
    "secrets.env",            # example: your real credentials file
    "credentials.env",        # example: a second real credentials file
    "STATUS.md",              # example: internal status page with contact info
}

# Public-safe substitutions: real_value -> placeholder.
# Every entry below is an illustrative example, not a real value -- replace
# with your own. Grouped by category so the pattern is obvious to extend.
SUBSTITUTIONS = {
    # Service account / integration credentials
    b"realuser@yourcompany.com": b"serviceuser@example.com",

    # Internal queue/resource UUIDs
    b"11111111-1111-1111-1111-111111111111": b"00000000-0000-0000-0000-000000000000",

    # Internal hostname prefixes (e.g. a queue-naming convention)
    b"realuser.QUEUE_A": b"serviceuser.QUEUE_A",

    # VPN / mesh-network hostnames (e.g. Tailscale MagicDNS)
    b"real-tailnet-name.ts.net": b"tailxxxxxxx.ts.net",

    # Personal email and real name
    b"jane.doe@yourcompany.com": b"operator@example.com",
    b"Jane Doe": b"the operator",

    # Third-party partner/vendor relationship
    b"Real Vendor Name LLC": b"[vendor partner]",

    # Real internal IP or VPN-assigned address
    b"10.20.30.40": b"10.x.x.x",

    # Business domain and subdomains
    b"yourrealdomain.com": b"example.com",

    # External tunnel/ingress UUID (e.g. Cloudflare Tunnel)
    b"22222222-2222-2222-2222-222222222222": b"00000000-0000-0000-0000-000000000000",
}


def git_out(*args, stdin=None):
    r = subprocess.run(["git"] + list(args), capture_output=True, input=stdin)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr.decode()}")
    return r.stdout


def scrub_blob(sha):
    content = git_out("cat-file", "blob", sha)
    new = content
    for old, repl in SUBSTITUTIONS.items():
        new = new.replace(old, repl)
    # Regex sweep example -- catches a whole token-prefix family instead of
    # hardcoding every issued token individually. Replace the prefix with
    # your own.
    import re as _re
    new = _re.sub(rb"yourapp_token_[A-Za-z0-9_\-]{10,}", b"yourapp_token_REDACTED", new)
    # General domain sweep -- catches every subdomain of the real business
    # domain (present and future), not just the handful hardcoded above.
    # Preserves the subdomain label, only swaps the root.
    new = _re.sub(rb"([A-Za-z0-9-]+\.)?yourrealdomain\.com", lambda m: (m.group(1) or b"") + b"example.com", new)
    if new == content:
        return sha
    r = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input=new, capture_output=True
    )
    return r.stdout.decode().strip()


def scrub_tree(tree_sha, path_prefix=""):
    entries = []
    raw = git_out("ls-tree", tree_sha).decode()
    for line in raw.splitlines():
        mode_type, name = line.split("\t", 1)
        mode, obj_type, sha = mode_type.split()

        rel_path = f"{path_prefix}{name}" if not path_prefix else f"{path_prefix}/{name}"

        # Drop files that must never appear on the public mirror
        if obj_type == "blob" and name in DROP_FILES:
            print(f"[scrub] DROP: {rel_path}", file=sys.stderr)
            continue

        if obj_type == "blob":
            sha = scrub_blob(sha)
        elif obj_type == "tree":
            sha = scrub_tree(sha, rel_path)

        entries.append(f"{mode} {obj_type} {sha}\t{name}")

    return git_out(
        "mktree",
        stdin=("\n".join(entries) + "\n").encode()
    ).decode().strip()


if __name__ == "__main__":
    print(scrub_tree(sys.argv[1]))
