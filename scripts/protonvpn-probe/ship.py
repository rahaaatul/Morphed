#!/usr/bin/env python3
"""Decide which probed versions to publish.

Ships from the newest release down to the newest version every patch applied to.
Anything older is a worse build than one already on the release. Anything newer
than that anchor is still worth shipping as long as it carries at least one
non-generic patch: a build with nothing but the shared install-source and
update patches applied is a re-signed APK with the store relabelled and the
updater disabled, which is not worth a download.

Usage: ship.py <results-dir> --output <file>
"""
import glob
import json
import os
import re
import sys

RESULTS_DIR = sys.argv[1]
OUTPUT = sys.argv[sys.argv.index("--output") + 1]

EXPECTED = json.loads(os.environ.get("EXPECTED_PATCHES") or "[]") \
    if os.environ.get("EXPECTED_PATCHES", "").strip().startswith("[") \
    else [p.strip() for p in os.environ.get("EXPECTED_PATCHES", "").split("\n") if p.strip()]
GENERIC = [p.strip() for p in os.environ.get("GENERIC_PATCHES", "").split("\n") if p.strip()]


def version_key(v):
    """Sort numerically per component, so 5.20.9.0 sorts below 5.20.57.0."""
    return [int(n) if n.isdigit() else 0 for n in re.split(r"[._-]", v)]


records = []
for path in glob.glob(os.path.join(RESULTS_DIR, "result-*.json")):
    version = os.path.basename(path)[len("result-"):-len(".json")]
    with open(path) as fh:
        data = json.load(fh)
    records.append({
        "version": version,
        "applied": [p["name"] for p in data.get("appliedPatches") or []],
        "failed": [p["patch"]["name"] for p in data.get("failedPatches") or []
                   if p.get("patch")],
    })

if not records:
    sys.exit(f"no result-*.json found in {RESULTS_DIR}")

records.sort(key=lambda r: version_key(r["version"]), reverse=True)


def is_bust(rec):
    """True when nothing but the generic patches applied."""
    return bool(rec["applied"]) and all(p in GENERIC for p in rec["applied"])


ship, dropped, anchor = [], [], None
for rec in records:
    if anchor is not None:
        # Past the anchor. Everything older is superseded.
        dropped.append({"version": rec["version"], "reason": "older than the anchor"})
        continue
    if is_bust(rec):
        dropped.append({"version": rec["version"], "reason": "only generic patches applied"})
        continue
    ship.append(rec)
    if all(p in rec["applied"] for p in EXPECTED):
        anchor = rec["version"]

if not ship:
    sys.exit("every candidate was dropped as worth nothing")

with open(OUTPUT, "w") as fh:
    json.dump({
        "anchor": anchor,
        "package": os.environ.get("PACKAGE", ""),
        "ship": [r["version"] for r in ship],
        "versions": records,
        "dropped": dropped,
    }, fh, indent=2)

print(f"anchor: {anchor or 'none reached'}")
print(f"shipping: {', '.join(r['version'] for r in ship)}")
for d in dropped:
    print(f"dropped {d['version']}: {d['reason']}")