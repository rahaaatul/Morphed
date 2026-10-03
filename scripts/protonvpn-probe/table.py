#!/usr/bin/env python3
"""Render probe-result.json as the Patch | Supported Version matrix.

One row per patch, and a single cell listing every version that supports it,
rather than one row per patch per version. Reads probe-result.json from the
current directory and writes probe-body.md.
"""
import json

data = json.load(open("probe-result.json"))
records = data["versions"]

# Keep first-seen order: applied before failed within a version, so patches that
# work lead and the ones that never applied still appear with an empty cell.
order, supported = [], {}
for rec in records:
    for name in rec["applied"] + rec["failed"]:
        if name not in supported:
            supported[name] = []
            order.append(name)
    for name in rec["applied"]:
        supported[name].append(rec["version"])

rows = []
for name in order:
    vers = supported[name]
    cell = "<br>".join(f"`{v}`" for v in vers) if vers else "&mdash;"
    rows.append(f"    <tr><td>{name}</td><td>{cell}</td></tr>")

table = ("<table>\n"
         "  <tr><th>Patch</th><th>Supported Version</th></tr>\n"
         + "\n".join(rows) + "\n"
         " </table>")

shipped = [r["version"] for r in records if r["built"]]
body = [
    "Automated probe of the Proton VPN patch matrix. Do not edit by hand.",
    "",
    f"- Anchor, newest version where every patch applied: `{data['anchor']}`",
    f"- Versions with a built APK: "
    f"{', '.join(f'`{v}`' for v in shipped) if shipped else '_none_'}",
    f"- Package: `{data['package']}`",
    "",
    "## Patch | Supported Version",
    "",
    table,
    "",
    "## Per version detail",
    "",
    "| Version | Applied | Failed | APK built |",
    "| ------- | ------- | ------ | --------- |",
]
for r in records:
    a = "<br>".join(f"`{n}`" for n in r["applied"]) or "&mdash;"
    fl = "<br>".join(f"`{n}`" for n in r["failed"]) or "&mdash;"
    body.append(f"| `{r['version']}` | {a} | {fl} | {'yes' if r['built'] else 'no'} |")

body += ["", "<details><summary>Raw probe output</summary>", "",
         "```json", json.dumps(data, indent=2), "```", "", "</details>", ""]

with open("probe-body.md", "w") as fh:
    fh.write("\n".join(body))
print("\n".join(body))