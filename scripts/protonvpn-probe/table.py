#!/usr/bin/env python3
"""Render probe-result.json as the Patch | Supported Version matrix.

A plain markdown table, one row per patch, with every version that supports it
on its own line inside the cell. The recommendation leads the body; the table and
the per-version warnings sit in collapsible blocks underneath it.

A version where only the generic patches landed is dropped. Change installer
source and disable-updates come from the shared patch set and apply to anything,
so a build carrying nothing but those is just a re-signed APK with the store
mislabelled and the updater disabled, which is not worth shipping.

Reads probe-result.json from the current directory and writes probe-body.md.
"""
import json
import os

data = json.load(open("probe-result.json"))
records = data["versions"]
EXPECTED = [p.strip() for p in os.environ.get("EXPECTED_PATCHES", "").split("\n") if p.strip()]
GENERIC = [p.strip() for p in os.environ.get("GENERIC_PATCHES", "").split("\n") if p.strip()]


def is_bust(rec):
    """True when nothing but the generic patches applied."""
    return bool(rec["applied"]) and all(p in GENERIC for p in rec["applied"])


kept = [r for r in records if not is_bust(r)]
busted = [r for r in records if is_bust(r)]
if not kept:
    # Without this the table would be empty and every patch would read as
    # supporting nothing, which reads as a broken probe rather than a bad result.
    kept = records

# First-seen order, applied before failed within a version, so patches that work
# lead and ones that never applied still get a row.
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
table = "\n".join(rows)

shipped = [r["version"] for r in kept if r["built"]]
fresh = sum(1 for r in records if r.get("cached") is False)

body = []

# The recommendation leads, so it is the first thing read and stays visible.
if data["anchor"]:
    body += ["> [!TIP]", f"> Install `{data['anchor']}`. It is the newest version where "
                        "every patch applied cleanly.", ""]
else:
    body += ["> [!WARNING]", "> No published version had every patch applied.", ""]

body += [
    f"Package `{data['package']}`, "
    f"{len(kept)} version(s) considered"
    + (f", {fresh} patched this run" if fresh else ", all results from cache")
    + ".",
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

# A version missing patches is called out by name, so nobody installs it expecting
# the full set and silently gets less.
partial = [(r["version"], [p for p in EXPECTED if p not in r["applied"]])
           for r in kept]
partial = [(v, m) for v, m in partial if m]
if partial:
    body += ["<details>", "<summary><b>Versions missing patches</b></summary>", ""]
    for v, missing in partial:
        listed = ", ".join(f"`{p}`" for p in missing)
        # The subject is the version, so it stays "is missing" however many patches.
        body += [f"> [!WARNING]", f"> **`{v}`** is missing {listed}.", ""]
    body += ["</details>", ""]

body += ["<details><summary>Raw probe output</summary>", "",
         "```json", json.dumps(data, indent=2), "```", "", "</details>", ""]

with open("probe-body.md", "w") as fh:
    fh.write("\n".join(body))
print("\n".join(body))