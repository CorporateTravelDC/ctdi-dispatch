"""Validate deployment profiles against schemas/profile.schema.json.

2026-10-09 (STAGED.md note 3): the strict JSON Schema check is REQUIRED. Before,
a missing `jsonschema` silently skipped it and still printed PASS. Now a missing
library is an error (exit 2) unless --structural-only is given, which says so.

Checks every profile in profiles/, the platform's bundled reference profile
(src/common/profiles/), and the private ledger profiles (private/) when present.
"""
from pathlib import Path
import json
import sys

root = Path(__file__).resolve().parent
repo = root.parents[1]
schema = json.loads((root / "schemas/profile.schema.json").read_text())
structural_only = "--structural-only" in sys.argv[1:]
try:
    import jsonschema
except ImportError:
    jsonschema = None
    if not structural_only:
        print("FAIL: jsonschema is not installed; the strict schema check cannot run "
              "(install it, or pass --structural-only to run the invariant checks alone)")
        sys.exit(2)

files = sorted((root / "profiles").glob("*.json")) + sorted((repo / "src/common/profiles").glob("*.json")) \
    + sorted((root / "private").glob("*.json"))
errors = []
for p in files:
    d = json.loads(p.read_text())
    rel = p.relative_to(repo)
    if set(d) != set(schema["properties"]):
        errors.append(f"{rel}: top-level keys")
    if p.parent.name == "profiles" and p.stem != "base" and p.parent.parent.name == "deployment-profiles" \
            and d["deployment"]["profile"] != p.stem:
        errors.append(f"{rel}: profile id")
    if not d["security"]["default_deny"] or d["security"]["network_exposure"] != "private_overlay":
        errors.append(f"{rel}: unsafe security defaults")
    if jsonschema is not None:
        try:
            jsonschema.validate(d, schema)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{rel}: {str(e).splitlines()[0]}")
print(f"Checked {len(files)} profiles" + (" (STRUCTURAL ONLY: schema not checked)" if jsonschema is None else ""))
if errors:
    print(*errors, sep="\n")
    sys.exit(1)
print("PASS: " + ("security invariants only" if jsonschema is None else "schema and security invariants"))
