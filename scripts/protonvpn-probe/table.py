#!/usr/bin/env python3
"""Render probe-result.json as the Patch | Supported Version matrix.

A plain markdown table, one row per patch, with every version that supports it
on its own line inside the cell. Followed by a warning naming the versions that
could not be patched completely, and a tip pointing at the one that could.

Reads probe-result.json from the current directory and writes probe-body.md.
"""
import json
import os

data = json.load(open("probe-result.json"))
records = data["versions"]
EXPECTED = [p.strip() for p in os.environ.get("EXPECTED_PATCHES", "").split("\n") if p.strip()]

# First-seen order, applied before failed within a version, so patches that work
# lead and ones that never applied still get a row.
order, supported = [], {}
for rec in records:
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
table = "\n".join(rows)

shipped = [r["version"] for r in records if r["built"]]
body = [
    "Automated probe of the Proton VPN patch matrix. Do not edit by hand.",
    "",
    f"- Package: `{data['package']}`",
    f"- Versions built: "
    f"{', '.join(f'`{v}`' for v in shipped) if shipped else '_none_'}",
    "",
    "## Patch | Supported Version",
    "",
    table,
    "",
]

# A version that is missing patches is called out by name, so nobody installs it
# expecting the full set and silently gets less.
partial = [(r["version"], [p for p in EXPECTED if p not in r["applied"]])
           for r in records]
partial = [(v, m) for v, m in partial if m]
for v, missing in partial:
    listed = ", ".join(f"`{p}`" for p in missing)
    # The subject is the version, so it stays "is missing" however many patches.
    body += ["> [!WARNING]", f"> **`{v}`** is missing {listed}.", ""]

if data["anchor"]:
    body += ["> [!TIP]", f"> Install `{data['anchor']}`. It is the newest version where "
                        "every patch applied cleanly.", ""]
else:
    body += ["> [!WARNING]", "> No published version had every patch applied.", ""]

body += ["<details><summary>Raw probe output</summary>", "",
         "```json", json.dumps(data, indent=2), "```", "", "</details>", ""]

with open("probe-body.md", "w") as fh:
    fh.write("\n".join(body))
print("\n".join(body))