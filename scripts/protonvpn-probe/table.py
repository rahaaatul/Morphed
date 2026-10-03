#!/usr/bin/env python3
"""Render probe-result.json as the Patch | Supported Version matrix.

One row per patch, with the version column collapsed using rowspan so a version
is written once instead of once per patch that supports it.

A version's patches need not be contiguous, and rowspan spans rows, so a version
is emitted once per maximal run of consecutive patches that support it. That way
a version is never visually attached to a patch it does not support.

Reads probe-result.json from the current directory and writes probe-body.md.
"""
import json

data = json.load(open("probe-result.json"))
records = data["versions"]

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

version_rows = {}
for v in {v for vers in supported.values() for v in vers}:
    version_rows[v] = {i for i, name in enumerate(order) if v in supported[name]}


def runs(indices):
    """Split patch indices into maximal consecutive runs."""
    out = []
    for i in sorted(indices):
        if out and i == out[-1][-1] + 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


# A cell belongs on the first row of its run; rows that start mid-run emit nothing.
start_of = {}
for v, idxs in version_rows.items():
    for run in runs(idxs):
        start_of.setdefault(run[0], []).append((v, len(run)))

rows = []
for i, name in enumerate(order):
    vers = supported[name]
    cells = "".join(
        ('<td>' if span == 1 else f'<td rowspan="{span}">') + f"`{v}`</td>"
        for v, span in start_of.get(i, []))
    if not cells and not vers:
        cells = "<td>&mdash;</td>"
    rows.append(f"    <tr><td>{name}</td>{cells}</tr>")

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
    "A version is listed once and spans every patch it supports. `5.20.57.0` sits "
    "across the first two rows because it has no working premium unlock.",
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