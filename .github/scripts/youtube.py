"""YouTube release pipeline, one argparse subcommand per workflow step.

May depend on `requests` (installed and pip-cached by the workflow). External CLI tools
(java, bun, gh) are invoked through the private helpers below, never inlined, so nothing
in this file exists only to call a CLI from a different language.

Each subcommand reads the values the workflow owns from the environment, writes results
back through $GITHUB_OUTPUT, and exits non-zero with a ::error:: message so a workflow
step fails loudly rather than shipping a wrong APK or a wrong release body.
"""

import argparse
import os
import pathlib
import re
import sys
from typing import List, Tuple


def version_sort_key(version: str) -> Tuple[int, ...]:
    """
    Convert a version string into a tuple of integers for sorting.
    """
    return tuple(int(part) for part in version.split('.'))


def parse_version_lines(text: str) -> List[str]:
    """
    Parse the output of `morphe-desktop list-versions` to extract version strings.
    """
    versions = []
    for line in text.splitlines():
        # Look for lines that contain a version number (digits and dots) possibly preceded by a tab, 
        # followed by whitespace, an opening parenthesis, patch count, space, the word "patches", and a closing parenthesis.
        match = re.search(r'\t?(\d+\.\d+\.\d+)\s+\(\d+\s+patches?\)', line)
        if match:
            versions.append(match.group(1))
    return versions


def strip_apk_version(filename: str) -> str:
    """
    Strip the version from an APK filename.
    Returns None if no version is found.
    """
    # Match a version number (digits and dots) at the end of the filename, possibly preceded by a hyphen.
    match = re.search(r'(\d+\.\d+\.\d+)$', filename)
    if match:
        return match.group(1)
    # If no version at the end, try to find a version in the middle (e.g., "app-release-21.39.522.apk")
    match = re.search(r'(\d+\.\d+\.\d+)', filename)
    if match:
        return match.group(1)
    return None


def parse_list_patches(text):
    names = []
    current = None
    for line in text.splitlines():
        if line.startswith("Name: "):
            current = line[len("Name: "):]
        elif line.startswith("Enabled: "):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "true" and current is not None:
                names.append(current)
    return names


def extract_patch_lists(result):
    applied = []
    applied_raw = result.get("appliedPatches")
    if isinstance(applied_raw, list):
        for patch in applied_raw:
            if not isinstance(patch, dict):
                continue
            name = patch.get("name")
            if name is None or name is False:
                continue
            applied.append(name if isinstance(name, str) else str(name))

    failed = []
    failed_raw = result.get("failedPatches")
    if isinstance(failed_raw, list):
        for patch in failed_raw:
            if not isinstance(patch, dict):
                continue
            inner = patch.get("patch")
            if not isinstance(inner, dict):
                continue
            name = inner.get("name")
            if name is None or name is False:
                continue
            failed.append(name if isinstance(name, str) else str(name))

    return applied, failed


def build_records(release_dir, applied_dir, failed_dir, arch):
    """
    Build a list of version records from the release directory and patch status files.
    Mirrors proton-vpn.py:build_records.
    """
    release_dir = pathlib.Path(release_dir)
    dirs = {"applied": pathlib.Path(applied_dir), "failed": pathlib.Path(failed_dir)}
    records = []
    for apk in release_dir.glob("*.apk"):
        v = strip_apk_version(apk.name)
        if v is None:
            continue
        for kind, d in dirs.items():
            path = d / f"{kind}-{v}.txt"
            if not path.exists():
                # Fatal on purpose. The body states per-patch status for every version,
                # so a missing list means the run would publish a table that silently
                # omits this version's outcome. Better to fail than to under-report.
                sys.stderr.write(
                    f"::error::{kind}/{kind}-{v}.txt is missing, "
                    f"cannot describe {v} in the release body\n")
                sys.exit(1)
        applied = [ln.strip() for ln in (dirs["applied"] /
                     f"applied-{v}.txt").read_text().splitlines() if ln.strip()]
        failed = [ln.strip() for ln in (dirs["failed"] /
                   f"failed-{v}.txt").read_text().splitlines() if ln.strip()]
        records.append({
            "version": v,
            "size": apk.stat().st_size // (1024 * 1024),
            "arch": arch,
            "applied": applied,
            "failed": failed,
        })
    records.sort(key=lambda r: version_sort_key(r["version"]), reverse=True)
    return records


def pick_anchor(records, expected):
    """Newest version where every expected patch applied cleanly.

    This is the version the release body tells users to install. It is derived from
    the patch results rather than trusted from the bundles' own recommendation,
    because a recommended version can still fail to apply cleanly in practice.
    """
    ordered = sorted(records, key=lambda r: version_sort_key(r["version"]),
                     reverse=True)
    want = set(expected)
    for r in ordered:
        if want <= set(r.get("applied", [])):
            return r["version"]
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube release pipeline")
    subparsers = parser.add_subparsers(dest='command')

    # We'll add subcommands later in subsequent tasks.
    # For now, we just have a stub.

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()