#!/usr/bin/env python3
"""scripts/scrub-public-tree.py
Recursively walk a git tree, replace sensitive identifiers in all blobs,
return the new scrubbed tree SHA on stdout.
Usage: python3 scripts/scrub-public-tree.py <tree-sha>

Add new substitutions to SUBSTITUTIONS below.
"""
import subprocess, sys

# Public-safe substitutions: real_value -> placeholder
SUBSTITUTIONS = {
    b"taile57c8d": b"tailxxxxxxx",
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
    if new == content:
        return sha
    r = subprocess.run(["git", "hash-object", "-w", "--stdin"], input=new, capture_output=True)
    return r.stdout.decode().strip()

def scrub_tree(tree_sha):
    entries = []
    raw = git_out("ls-tree", tree_sha).decode()
    for line in raw.splitlines():
        mode_type, name = line.split("\t", 1)
        mode, obj_type, sha = mode_type.split()
        if obj_type == "blob":
            sha = scrub_blob(sha)
        elif obj_type == "tree":
            sha = scrub_tree(sha)
        entries.append(f"{mode} {obj_type} {sha}\t{name}")
    return git_out("mktree", stdin=("\n".join(entries) + "\n").encode()).decode().strip()

if __name__ == "__main__":
    print(scrub_tree(sys.argv[1]))
