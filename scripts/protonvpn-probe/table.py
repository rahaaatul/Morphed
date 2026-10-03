#!/usr/bin/env python3
"""Combine the per-version probe results and render the release body.

Each matrix leg uploads one result JSON, so this merges them, works out which
versions the patch set actually supports, and writes the release body.

Usage: table.py [results-dir]   (defaults to the current directory)
"""
import glob
import json
import os
import re
import sys

RESULTS_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
def patch_list(raw):
    """Accept either a JSON array or newline separated text.

    The workflow passes a JSON array because the list is discovered at run time,
    while a hand-maintained override is easier to write as one name per line.
    """
    raw = raw.strip()
    if raw.startswith("["):
        return [p.strip() for p in json.loads(raw) if p.strip()]
    return [p.strip() for p in raw.split("\n") if p.strip()]


EXPECTED = patch_list(os.environ.get("EXPECTED_PATCHES", ""))
GENERIC = patch_list(os.environ.get("GENERIC_PATCHES", ""))
DOOM_RECOMMENDED = os.environ.get("DOOM_RECOMMENDED", "").strip()
HOODLES_RECOMMENDED = os.environ.get("HOODLES_RECOMMENDED", "").strip()
STABLE = set(json.loads(os.environ.get("STABLE_VERSIONS", "[]")))
EXPERIMENTAL = set(json.loads(os.environ.get("EXPERIMENTAL_VERSIONS", "[]")))
RELEASE_TAG = os.environ.get("RELEASE_TAG", "")
REPO_FULL = os.environ.get("REPO_FULL", "")
ARCH = os.environ.get("ARCH", "arm64-v8a")
TOOLS = json.loads(os.environ.get("TOOLS", "[]"))

# Above this many exceptions a version list is clearer than "All except ...".
MAX_EXCEPTIONS = 3


def version_key(v):
    """Sort numerically per component, so 5.20.9.0 sorts below 5.20.57.0."""
    return [int(n) if n.isdigit() else 0 for n in re.split(r"[._-]", v)]


def fmt_size(n):
    mb = n / 1048576
    return f"{mb:.1f}MB" if mb < 100 else f"{mb/1024:.2f}GB"


records = []
for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "result-*.json"))):
    version = os.path.basename(path)[len("result-"):-len(".json")]
    with open(path) as fh:
        data = json.load(fh)
    rec = {
        "version": version,
        "applied": [p["name"] for p in data.get("appliedPatches") or []],
        # The name sits under a nested patch field; the top level is null.
        "failed": [p["patch"]["name"] for p in data.get("failedPatches") or []
                   if p.get("patch")],
        "size": 0,
        "arch": "",
    }
    meta = os.path.join(RESULTS_DIR, f"meta-{version}.json")
    if os.path.exists(meta):
        with open(meta) as fh:
            m = json.load(fh)
        rec["size"] = m.get("size_bytes", 0)
        # Prefer what the patch leg read back out of the APK over any configured
        # default, so the body cannot label an artifact with the wrong architecture.
        rec["arch"] = m.get("arch", "")
    records.append(rec)

if not records:
    sys.exit(f"no result-*.json found in {RESULTS_DIR}")

records.sort(key=lambda r: version_key(r["version"]), reverse=True)
tested = [r["version"] for r in records]

# The recommendation is the newest version where every expected patch applied.
anchor = next((r["version"] for r in records
               if all(p in r["applied"] for p in EXPECTED)), None)


def channel_of(version):
    """Stable when a bundle lists the version, Beta when a bundle lists it as
    experimental, otherwise Canary: nothing upstream claims it and --force carried it."""
    if version in STABLE:
        return "Stable"
    if version in EXPERIMENTAL:
        return "Beta"
    return "Canary"


def is_bust(rec):
    """True when nothing but the generic patches applied.

    Those two come from the shared patch set and apply to any app, so a build
    carrying nothing else is just a re-signed APK with the store mislabelled and
    the updater disabled, which is not worth shipping.
    """
    return bool(rec["applied"]) and all(p in GENERIC for p in rec["applied"])


kept = [r for r in records if not is_bust(r)]
if not kept:
    # Without this the table would be empty and every patch would read as supporting
    # nothing, which reads as a broken probe rather than a bad result.
    kept = records
kept_versions = [r["version"] for r in kept]


def applied_on(name):
    """Compact support statement: `All`, `All` except `x`, or the version list."""
    vers = [r["version"] for r in kept if name in r["applied"]]
    if not vers:
        return "&mdash;"
    if len(vers) == len(kept_versions):
        return "`All`"
    missing = [v for v in kept_versions if v not in vers]
    if len(missing) <= MAX_EXCEPTIONS:
        return "`All` except " + ", ".join(f"`{v}`" for v in missing)
    return "<br>".join(f"`{v}`" for v in vers)


body = ["## Patches", "",
        "| Patch | Applied on |", "| --- | --- |"]
for name in EXPECTED:
    body.append(f"| {name} | {applied_on(name)} |")

if DOOM_RECOMMENDED or HOODLES_RECOMMENDED:
    notes = []
    if DOOM_RECOMMENDED:
        notes.append(f"> - **[Doom's Morphe Patches](https://github.com/rushiranpise/morphe-patches/)** "
                     f"recommends `{DOOM_RECOMMENDED}`.")
    if HOODLES_RECOMMENDED:
        notes.append(f"> - **[hoodles Morphe Patches](https://github.com/hoo-dles/morphe-patches)** "
                     f"recommends `{HOODLES_RECOMMENDED}`.")

if bool(RELEASE_TAG) != bool(REPO_FULL):
    # Half-configured download links would render a table of dead links, or drop the
    # section entirely and look like there is nothing to download. Say which half is
    # missing instead.
    sys.exit("RELEASE_TAG and REPO_FULL must be set together "
             f"(RELEASE_TAG={RELEASE_TAG!r}, REPO_FULL={REPO_FULL!r})")

if RELEASE_TAG and REPO_FULL:
    body += ["", "## Downloads", "",
             "| Version | Channel | Arch | Size | Download |",
             "| --- | --- | --- | --- | --- |"]
    for r in kept:
        name = f"{RELEASE_TAG}-v{r['version']}.apk"
        url = f"https://github.com/{REPO_FULL}/releases/download/{RELEASE_TAG}/{name}"
        channel = channel_of(r["version"])
        size = fmt_size(r["size"]) if r["size"] else "&mdash;"
        arch = r["arch"] or ARCH
        body.append(f"| `{r['version']}` | {channel} | {arch} | {size} | [{name}]({url}) |")

if TOOLS:
    body += ["", "## Tools used", "", "| Tool | Version |", "| --- | --- |"]
    for name, url, version in TOOLS:
        tag_url = url.rstrip("/") + f"/releases/tag/{version}" if version else url
        body.append(f"| [{name}]({url}) | [`{version}`]({tag_url}) |")

# The recommendation and the source recommendations lead the body.
lead = []
if anchor:
    lead += ["> [!TIP]",
             f"> Install `{anchor}`. It is the newest version where every patch applied "
             "cleanly.", ""]
else:
    lead += ["> [!WARNING]", "> No tested version had every patch applied.", ""]
if DOOM_RECOMMENDED or HOODLES_RECOMMENDED:
    lead += ["> [!NOTE]"] + notes + [""]

with open("probe-body.md", "w") as fh:
    fh.write("\n".join(lead + body) + "\n")
print("\n".join(lead + body))