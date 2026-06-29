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
    b"coreywsheldon@pm.me":      b"swimuser@example.com",
    b"KSLwVuvKQv2dxEUWhhqCGQ":  b"SWIM_PASSWORD_REDACTED",

    # SWIM queue UUIDs
    b"0a85a945-d6ee-478d-83a9-3a4691cc5c20": b"00000000-0000-0000-0000-000000000000",
    b"978bdb94-2630-4b83-a1bc-ece1bdec73d6": b"00000000-0000-0000-0000-000000000001",
    b"65aa6c7b-5f78-48ac-8af9-a0e4387f366e": b"00000000-0000-0000-0000-000000000002",
    b"0f3c9f94-6b52-4eea-ba63-fca285db2fe2": b"00000000-0000-0000-0000-000000000003",
    b"3b7da560-bd3e-45df-83b5-ef9673ee7608": b"00000000-0000-0000-0000-000000000004",
    b"7f221de9-79f3-4ca9-b44b-f57e7ba06707": b"00000000-0000-0000-0000-000000000005",

    # SWIM queue prefixes
    b"coreywsheldon.pm.me.FDPS":    b"swimuser.FDPS",
    b"coreywsheldon.pm.me.STDDS":   b"swimuser.STDDS",
    b"coreywsheldon.pm.me.TFMS":    b"swimuser.TFMS",
    b"coreywsheldon.pm.me.AIM_FNS": b"swimuser.AIM_FNS",
    b"coreywsheldon.pm.me.TBFM":    b"swimuser.TBFM",
    b"coreywsheldon.pm.me.ITWS":    b"swimuser.ITWS",

    # NWWS
    b"corey.sheldon@nwws-oi.weather.gov": b"nwwsuser@nwws-oi.weather.gov",

    # ntfy token pattern (tk_ prefix)
    b"tk_02rd10x8sa31x3sk4vl0ckpekzie2": b"tk_REDACTED",

    # Tailscale hostnames
    b"taile57c8d": b"tailxxxxxxx",

    # Personal email
    b"csexecservices@gmail.com": b"operator@example.com",
    b"coreywsheldon@pm.me":      b"operator@example.com",
    b"corey.sheldon@pm.me":      b"operator@example.com",
    # Tailscale IP (Pi)
    b"100.94.80.100":                   b"100.x.x.x",

    # Domain references
    b"csexecutiveservices.com":          b"example.com",
    b"csexecutiveservices.ts.net":       b"example.ts.net",
    b"dispatch.csexecutiveservices.com": b"dispatch.example.com",
    b"ops.csexecutiveservices.com":      b"ops.example.com",

    # Cloudflare tunnel UUID
    b"28bde9a2-0bb2-4cca-a207-9b759c4739f1": b"00000000-0000-0000-0000-cf0tunnel0000",

    # SWIM NMS operator email / username prefix
    b"corey.sheldon@csexecutiveservices.com": b"operator@example.com",
    b"corey.sheldon.csexecutiveservices.com": b"swimuser.example.com",

    # Amateur radio callsigns (FCC public but operator-identifying)
    b"WA1EM-5":   b"N0CALL-5",
    b"WA1EM":     b"N0CALL",
    b"WRCR715":   b"WRXXXXX",
    b"L0344":     b"LXXXX",

    # ARES/CERT identifiers
    b"District 10":           b"District XX",
    b"Fairfax+Loudoun":       b"County+County",
    b"Arlington County, VA":  b"[operator county], [state]",

    # Jumpseat tokens (both the exposed one and any future sk_adjs_ pattern)
    b"sk_adjs_27C-PdbN3ut0U-T6JNgyRdPCZaQupbCQH6dvFEUjf7Q": b"sk_adjs_REDACTED",
    b"sk_adjs_Bk-ct0MqeMCt7PAwiGOAItFFLTxLr-A_fxgOTCPETK0": b"sk_adjs_REDACTED",

    # Dispatch admin tokens (ctdc_cowork_ prefix)
    b"ctdc_cowork_5NC2G5DLI8CONLZCFWO5TLM5CEABD7OQ": b"ctdc_cowork_REDACTED",
    b"ctdc_cowork_8AXAVWO6YNOVKTSC34WC36B65G83F09U":  b"ctdc_cowork_REDACTED",

    # New ntfy token from current session
    b"tk_v82g71ytad8wtmrfwnvzlxkm5iu3b": b"tk_REDACTED",

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
