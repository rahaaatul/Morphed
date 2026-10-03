#!/usr/bin/env python3
"""Combine the per-version probe results and render the patch matrix.

Each matrix leg uploads one result JSON, so this merges them, works out which
versions the patch set actually supports, and writes the release-style body.

Usage: table.py [results-dir]   (defaults to the current directory)

Reads result-<version>.json files and writes probe-body.md.
"""
import glob
import json
import os
import re
import sys

RESULTS_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
EXPECTED = [p.strip() for p in os.environ.get("EXPECTED_PATCHES", "").split("\n") if p.strip()]
GENERIC = [p.strip() for p in os.environ.get("GENERIC_PATCHES", "").split("\n") if p.strip()]
RUSH_RECOMMENDED = os.environ.get("RUSH_RECOMMENDED", "").strip()
HOODLES_RECOMMENDED = os.environ.get("HOODLES_RECOMMENDED", "").strip()


def version_key(v):
    """Sort numerically per component, so 5.20.9.0 lands above 5.20.57.0's neighbour."""
    return [int(n) if n.isdigit() else 0 for n in re.split(r"[._-]", v)]


records = []
for path in glob.glob(os.path.join(RESULTS_DIR, "result-*.json")):
    version = os.path.basename(path)[len("result-"):-len(".json")]
    with open(path) as fh:
        data = json.load(fh)
    records.append({
        "version": version,
        "applied": [p["name"] for p in data.get("appliedPatches") or []],
        # The name sits under a nested patch field; the top level is null.
        "failed": [p["patch"]["name"] for p in data.get("failedPatches") or []
                   if p.get("patch")],
    })

if not records:
    sys.exit(f"no result-*.json found in {RESULTS_DIR}")

records.sort(key=lambda r: version_key(r["version"]), reverse=True)

# The recommendation is the newest version where every expected patch applied.
anchor = next((r["version"] for r in records
               if all(p in r["applied"] for p in EXPECTED)), None)


def is_bust(rec):
    """True when nothing but the generic patches applied.

    Those two come from the shared patch set and apply to any app, so a build
    carrying nothing else is just a re-signed APK with the store mislabelled and
    the updater disabled, which is not worth shipping.
    """
    return bool(rec["applied"]) and all(p in GENERIC for p in rec["applied"])


kept = [r for r in records if not is_bust(r)]
busted = [r for r in records if is_bust(r)]
if not kept:
    # Without this the table would be empty and every patch would read as supporting
    # nothing, which reads as a broken probe rather than a bad result.
    kept = records

# First-seen order, applied before failed within a version, so patches that work lead
# and ones that never applied still get a row.
order, supported = [], {}
for rec in kept:
    for name in rec["applied"] + rec["failed"]:
        if name not in supported:
            supported[name] = []
            order.append(name)
    for name in rec["applied"]:
        supported[name].append(rec["version"])

rows = ["| Patch | Supported Version |", "| ----- | ----------------- |"]
for name in order:
    vers = supported[name]
    # A markdown cell cannot hold a literal newline, so <br> separates versions.
    cell = "<br>".join(f"`{v}`" for v in vers) if vers else "&mdash;"
    rows.append(f"| {name} | {cell} |")
if len(rows) == 2:
    # No patch was recorded as applied or failed on any version, which means the
    # probe itself did not get as far as patching. A header-only table would read as
    # "these patches support nothing" rather than "nothing was measured".
    rows.append("| _nothing was measured_ | &mdash; |")
table = "\n".join(rows)

body = []

# The recommendation leads, so it is the first thing read and stays visible.
if anchor:
    body += ["> [!TIP]", f"> Install `{anchor}`. It is the newest version where every "
                        "patch applied cleanly.", ""]
else:
    body += ["> [!WARNING]", "> No tested version had every patch applied.", ""]

if RUSH_RECOMMENDED or HOODLES_RECOMMENDED:
    body += ["> [!NOTE]"]
    if RUSH_RECOMMENDED:
        body.append(f"> rushiranpise recommends `{RUSH_RECOMMENDED}`.")
    if HOODLES_RECOMMENDED:
        body.append(f"> hoo-dles recommends `{HOODLES_RECOMMENDED}`.")
    body.append("")

body += [
    f"Package `{os.environ.get('PACKAGE', '?')}`, {len(kept)} version(s) tested.",
    "",
    "<details>",
    "<summary><b>Patch | Supported Version</b></summary>",
    "",
    table,
    "",
    "</details>",
    "",
]

if busted:
    body += ["<details>", "<summary><b>Discarded</b></summary>", ""]
    for r in busted:
        body.append(f"- `{r['version']}` only got "
                    + ", ".join(f"`{p}`" for p in r["applied"])
                    + ", which is worth nothing on its own.")
    body += ["", "</details>", ""]

# A version missing patches is called out by name, so nobody installs it expecting the
# full set and silently gets less.
partial = [(r["version"], [p for p in EXPECTED if p not in r["applied"]])
           for r in kept]
partial = [(v, m) for v, m in partial if m]
if partial:
    body += ["<details>", "<summary><b>Versions missing patches</b></summary>", ""]
    for v, missing in partial:
        listed = ", ".join(f"`{p}`" for p in missing)
        # The subject is the version, so it stays "is missing" however many patches.
        body += ["> [!WARNING]", f"> **`{v}`** is missing {listed}.", ""]
    body += ["</details>", ""]

body += ["<details><summary>Per version detail</summary>", "",
         "| Version | Applied | Failed |", "| ------- | ------- | ------ |"]
for r in records:
    a = "<br>".join(f"`{n}`" for n in r["applied"]) or "&mdash;"
    fl = "<br>".join(f"`{n}`" for n in r["failed"]) or "&mdash;"
    body.append(f"| `{r['version']}` | {a} | {fl} |")
body += ["", "</details>", ""]

with open("probe-body.md", "w") as fh:
    fh.write("\n".join(body))
print("\n".join(body))