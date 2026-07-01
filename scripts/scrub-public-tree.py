#!/usr/bin/env python3
"""scripts/scrub-public-tree.py
Recursively walk a git tree, replace sensitive identifiers in all blobs,
drop files that must never appear on the public mirror,
return the new scrubbed tree SHA on stdout.

Usage: python3 scripts/scrub-public-tree.py <tree-sha>
"""
import subprocess, sys

# Files to drop entirely from the public tree (relative to repo root)
DROP_FILES = {
    "dispatch-secrets.env",
    "secrets.env",           # acars-watcher/secrets.env — never public
    "STATUS.md",             # contains operator email + CF tunnel UUID
}

# Public-safe substitutions: real_value -> placeholder
SUBSTITUTIONS = {
    # SWIM credentials
    b"operator@example.com":      b"swimuser@example.com",
    b"SWIM_PASSWORD_REDACTED":  b"SWIM_PASSWORD_REDACTED",

    # SWIM queue UUIDs
    b"00000000-0000-0000-0000-000000000000": b"00000000-0000-0000-0000-000000000000",
    b"00000000-0000-0000-0000-000000000001": b"00000000-0000-0000-0000-000000000001",
    b"00000000-0000-0000-0000-000000000002": b"00000000-0000-0000-0000-000000000002",
    b"00000000-0000-0000-0000-000000000003": b"00000000-0000-0000-0000-000000000003",
    b"00000000-0000-0000-0000-000000000004": b"00000000-0000-0000-0000-000000000004",
    b"00000000-0000-0000-0000-000000000005": b"00000000-0000-0000-0000-000000000005",

    # SWIM queue prefixes
    b"swimuser.FDPS":    b"swimuser.FDPS",
    b"swimuser.STDDS":   b"swimuser.STDDS",
    b"swimuser.TFMS":    b"swimuser.TFMS",
    b"swimuser.AIM_FNS": b"swimuser.AIM_FNS",
    b"swimuser.TBFM":    b"swimuser.TBFM",
    b"swimuser.ITWS":    b"swimuser.ITWS",

    # NWWS
    b"nwwsuser@nwws-oi.weather.gov": b"nwwsuser@nwws-oi.weather.gov",

    # ntfy token pattern (tk_ prefix)
    b"tk_REDACTED": b"tk_REDACTED",

    # Tailscale hostnames
    b"tailxxxxxxx": b"tailxxxxxxx",

    # Personal email
    b"operator@example.com": b"operator@example.com",
    b"operator@example.com":      b"operator@example.com",
    b"operator@example.com":      b"operator@example.com",
    # Tailscale IP (Pi)
    b"100.x.x.x":                   b"100.x.x.x",

    # Domain references
    b"example.com":          b"example.com",
    b"example.ts.net":       b"example.ts.net",
    b"dispatch.example.com": b"dispatch.example.com",
    b"ops.example.com":      b"ops.example.com",

    # Cloudflare tunnel UUID
    b"00000000-0000-0000-0000-cf0tunnel0000": b"00000000-0000-0000-0000-cf0tunnel0000",

    # SWIM NMS operator email / username prefix
    b"corey.sheldon@example.com": b"operator@example.com",
    b"corey.sheldon.example.com": b"swimuser.example.com",

    # Amateur radio callsigns (FCC public but operator-identifying)
    b"N0CALL-5":   b"N0CALL-5",
    b"N0CALL":     b"N0CALL",
    b"WRXXXXX":   b"WRXXXXX",
    b"LXXXX":     b"LXXXX",

    # ARES/CERT identifiers
    b"District XX":           b"District XX",
    b"County+County":       b"County+County",
    b"[operator county], [state]":  b"[operator county], [state]",

    # Jumpseat tokens (both the exposed one and any future sk_adjs_ pattern)
    b"sk_adjs_REDACTED": b"sk_adjs_REDACTED",
    b"sk_adjs_REDACTED": b"sk_adjs_REDACTED",

    # Dispatch admin tokens (ctdc_cowork_ prefix)
    b"ctdc_cowork_REDACTED": b"ctdc_cowork_REDACTED",
    b"ctdc_cowork_REDACTED":  b"ctdc_cowork_REDACTED",

    # New ntfy token from current session
    b"tk_REDACTED": b"tk_REDACTED",

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
    # Regex sweep for token prefixes not caught by literal dict
    import re as _re
    new = _re.sub(rb"sk_adjs_[A-Za-z0-9_\-]{10,}", b"sk_adjs_REDACTED", new)
    new = _re.sub(rb"ctdc_cowork_[A-Z0-9]{20,}", b"ctdc_cowork_REDACTED", new)
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

        # Drop files that must never appear on public mirror
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
