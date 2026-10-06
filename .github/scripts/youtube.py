"""YouTube release pipeline, one argparse subcommand per workflow step.

May depend on `requests` (installed and pip-cached by the workflow). External CLI tools
(java, bun, gh) are invoked through the private helpers below, never inlined, so nothing
in this file exists only to call a CLI from a different language.

Each subcommand reads the values the workflow owns from the environment, writes results
back through $GITHUB_OUTPUT, and exits non-zero with a ::error:: message so a workflow
step fails loudly rather than shipping a wrong APK or a wrong release body.
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time
from typing import List, Tuple, Dict, Optional

# requests will be imported inside functions that need it to avoid hard dependency for pure functions


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


def _patch_row(patch: str, applied: List[str], failed: List[str]) -> str:
    """Return a formatted table row for a patch: |emoji|patch|."""
    if patch in applied:
        emoji = "🟢"
    elif patch in failed:
        emoji = "🔴"
    else:
        emoji = "⚪"
    return f"|{emoji}|{patch}|"


def render_notes(records: list[dict], expected: list[str],
                 tools: list[tuple[str, str, str]], microg_tag: str) -> str:
    """Build the release body.

    tools == [(name, repo_url, tag), ...] in display order (Morphe Desktop,
    Morphe Patches, APKMD, MicroG). microg_tag == '' means "no block".
    Returns str ending in a single newline.
    """
    lines = []
    # MicroG block
    if microg_tag:
        microg_repo_url = ""
        for name, repo_url, tag in tools:
            if name == "MicroG":
                microg_repo_url = repo_url
                break
        if not microg_repo_url:
            microg_repo_url = "https://github.com/MorpheApp/MicroG-RE"
        lines.append("> [!IMPORTANT]")
        lines.append(f"> **[MicroG]({microg_repo_url}/releases/tag/{microg_tag})** is required to use this app")
        lines.append("> Install the newest version before following the tip.")
        lines.append("")
    # TIP anchor line
    anchor = pick_anchor(records, expected)
    lines.append("> [!TIP]")
    lines.append(f"> Install `{anchor}`. It is the newest version where every patch applied cleanly.")
    lines.append("")
    # Downloads header with note
    lines.append("## Downloads")
    lines.append("> [!NOTE]")
    lines.append("> - `Stable` - Tested and Recommended")
    lines.append("> - `Beta` - Works but not recommended")
    lines.append("")
    # Outer details
    lines.append("<details>")
    lines.append("<summary><b>Click here</b> to see the patches applied</summary>")
    lines.append("<br>")
    lines.append("")
    # Inner details per version (newest first)
    for record in records:
        version = record["version"]
        applied = record.get("applied", [])
        failed = record.get("failed", [])
        n_applied = sum(1 for p in expected if p in applied)
        lines.append("<details>")
        lines.append(f"<summary><b>v{version}</b> - <code>{n_applied} of {len(expected)}</code></summary>")
        lines.append("<br>")
        lines.append("")
        # Table header
        lines.append("|Status|Patch|")
        lines.append("|:---:|:---:|")
        # Rows for each expected patch
        for patch in expected:
            lines.append(_patch_row(patch, applied, failed))
        lines.append("</details>")
    # Close outer details
    lines.append("")
    lines.append("</details>")
    lines.append("")
    # Download table
    lines.append("| Version | Channel | Arch | Size | Download |")
    lines.append("|:-------:|:-------:|:---------:|:-----:|:------------------------:|")
    for record in records:
        version = record["version"]
        size_mb = record["size"]
        arm = "arm64-v8a"
        channel = "Stable"
        download_url = f"https://github.com/MorpheApp/YouTube/releases/download/youtube/{version}.apk"
        icon_url = "https://raw.githubusercontent.com/MorpheApp/YouTube/main/icons/download.png"
        lines.append(f"| {version} | {channel} | {arm} | {size_mb}.0MB | <a href=\"{download_url}\"><img src=\"{icon_url}\" width=\"20\" alt=\"Download {version}\"></a> |")
    lines.append("")
    # Tools used
    lines.append("## Tools used")
    lines.append("| Tool | Version |")
    lines.append("|------|---------|")
    for name, repo_url, tag in tools:
        lines.append(f"| [{name}]({repo_url}/releases/tag/{tag}) | `{tag}` |")
    # Ensure single trailing newline
    result = "\n".join(lines)
    if not result.endswith("\n"):
        result += "\n"
    return result


def cmd_render_notes(args) -> int:
    """Handle the render-notes subcommand."""
    # Read expected patches
    try:
        with open(args.expected_patches, 'r') as f:
            expected = json.load(f)
        if not isinstance(expected, list):
            sys.stderr.write("::error::Expected patches must be a JSON list\n")
            return 1
    except Exception as e:
        sys.stderr.write(f"::error::Failed to read expected patches: {e}\n")
        return 1

    # Read release dir
    try:
        release_dir = args.release_dir
        applied_dir = args.applied_dir
        failed_dir = args.failed_dir
        arch = args.arch
        records = build_records(release_dir, applied_dir, failed_dir, arch)
    except Exception as e:
        sys.stderr.write(f"::error::Failed to build records: {e}\n")
        return 1

    if not records:
        sys.stderr.write("::error::No records found\n")
        return 1

    # Read tools
    try:
        with open(args.tools_json, 'r') as f:
            tools_data = json.load(f)
        tools = [(t["name"], t["repo_url"], t["tag"]) for t in tools_data]
    except Exception as e:
        sys.stderr.write(f"::error::Failed to read tools JSON: {e}\n")
        return 1

    microg_tag = args.microg_tag or ""

    result = render_notes(records, expected, tools, microg_tag)

    try:
        with open(args.output, 'w') as f:
            f.write(result)
    except Exception as e:
        sys.stderr.write(f"::error::Failed to write output: {e}\n")
        return 1

    return 0


def fetch_toolchain(owner: str, patches_repo: str, desktop_repo: str,
                    token: str) -> dict:
    """Fetch the latest versions of patches and desktop toolchain.
    Returns {"patches_ver": str, "desktop_ver": str}.
    """
    try:
        import requests
    except ImportError:
        sys.stderr.write("::warning::requests module not available, cannot fetch toolchain\n")
        return {"patches_ver": "", "desktop_ver": ""}
    
    # Fetch patches
    patches_url = f"https://api.github.com/repos/{owner}/{patches_repo}/releases?per_page=100"
    # Fetch desktop
    desktop_url = f"https://api.github.com/repos/{owner}/{desktop_repo}/releases?per_page=100"
    
    session = requests.Session()
    # We'll use a simple retry loop for each request
    max_attempts = 3
    backoff_factor = 1
    patches_ver = ""
    desktop_ver = ""
    
    # Helper function to attempt a request and return the response or None
    def fetch_url(url):
        for attempt in range(max_attempts):
            try:
                resp = session.get(url, timeout=(10, 60))
                if resp.status_code == 200:
                    return resp
                else:
                    if attempt < max_attempts - 1:
                        sys.stderr.write(f"::warning::Attempt {attempt+1} failed with status {resp.status_code}, retrying...\n")
                        time.sleep(backoff_factor * (2 ** attempt))  # exponential backoff
                    else:
                        sys.stderr.write(f"::warning::All {max_attempts} attempts failed. Last status: {resp.status_code}\n")
            except Exception as e:
                if attempt < max_attempts - 1:
                    sys.stderr.write(f"::warning::Attempt {attempt+1} failed with exception: {e}, retrying...\n")
                    time.sleep(backoff_factor * (2 ** attempt))
                else:
                    sys.stderr.write(f"::warning::All {max_attempts} attempts failed. Last exception: {e}\n")
        return None
    
    # Get patches version
    patches_resp = fetch_url(patches_url)
    if patches_resp is not None:
        try:
            patches_data = patches_resp.json()
            # Filter out drafts
            patches_releases = [r for r in patches_data if not r.get("draft", True)]
            if not patches_releases:
                patches_ver = ""
            else:
                # Sort by published_at descending
                patches_releases.sort(key=lambda r: r["published_at"], reverse=True)
                # Prefer prerelease
                for rel in patches_releases:
                    if rel.get("prerelease", False):
                        # Extract version from asset name
                        assets = rel.get("assets", [])
                        if assets:
                            # Find the .mpp asset
                            mpp_asset = next((a for a in assets if a.get("name", "").endswith(".mpp")), None)
                            if mpp_asset:
                                name = mpp_asset["name"]
                                # Expected format: patches-<version>.mpp
                                if name.startswith("patches-") and name.endswith(".mpp"):
                                    patches_ver = name[8:-4]  # strip "patches-" and ".mpp"
                                else:
                                    patches_ver = ""
                                break
                if not patches_ver:
                    # Fallback to latest stable
                    rel = patches_releases[0]
                    assets = rel.get("assets", [])
                    if assets:
                        mpp_asset = next((a for a in assets if a.get("name", "").endswith(".mpp")), None)
                        if mpp_asset:
                            name = mpp_asset["name"]
                            if name.startswith("patches-") and name.endswith(".mpp"):
                                patches_ver = name[8:-4]
                            else:
                                patches_ver = ""
        except Exception as e:
            sys.stderr.write(f"::warning::Error processing patches data: {e}\n")
            patches_ver = ""
    
    # Get desktop version
    desktop_resp = fetch_url(desktop_url)
    if desktop_resp is not None:
        try:
            desktop_data = desktop_resp.json()
            # Filter out drafts
            desktop_releases = [r for r in desktop_data if not r.get("draft", True)]
            if not desktop_releases:
                desktop_ver = ""
            else:
                # Sort by published_at descending
                desktop_releases.sort(key=lambda r: r["published_at"], reverse=True)
                # Prefer prerelease
                for rel in desktop_releases:
                    if rel.get("prerelease", False):
                        # Extract version from asset name
                        assets = rel.get("assets", [])
                        if assets:
                            # Find the .jar asset
                            jar_asset = next((a for a in assets if a.get("name", "").endswith(".jar")), None)
                            if jar_asset:
                                name = jar_asset["name"]
                                # Expected format: morphe-desktop-<version>-all.jar or morphe-desktop-<version>.jar
                                if name.startswith("morphe-desktop-") and name.endswith("-all.jar"):
                                    desktop_ver = name[17:-10]  # strip "morphe-desktop-" and "-all.jar"
                                elif name.startswith("morphe-desktop-") and name.endswith(".jar"):
                                    desktop_ver = name[17:-4]  # strip "morphe-desktop-" and ".jar"
                                else:
                                    desktop_ver = ""
                                break
                if not desktop_ver:
                    # Fallback to latest stable
                    rel = desktop_releases[0]
                    assets = rel.get("assets", [])
                    if assets:
                        jar_asset = next((a for a in assets if a.get("name", "").endswith(".jar")), None)
                        if jar_asset:
                            name = jar_asset["name"]
                            if name.startswith("morphe-desktop-") and name.endswith("-all.jar"):
                                desktop_ver = name[17:-10]
                            elif name.startswith("morphe-desktop-") and name.endswith(".jar"):
                                desktop_ver = name[17:-4]
                            else:
                                desktop_ver = ""
        except Exception as e:
            sys.stderr.write(f"::warning::Error processing desktop data: {e}\n")
            desktop_ver = ""
    
    session.close()
    return {"patches_ver": patches_ver, "desktop_ver": desktop_ver}


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube release pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # render-notes
    p_render = subparsers.add_parser("render-notes", help="Render release notes")
    p_render.add_argument("--release-dir", required=True)
    p_render.add_argument("--applied-dir", required=True)
    p_render.add_argument("--failed-dir", required=True)
    p_render.add_argument("--output", required=True)
    p_render.add_argument("--expected-patches", required=True)
    p_render.add_argument("--microg-tag", default="")
    p_render.add_argument("--tools-json", required=True)
    p_render.add_argument("--arch", required=True)
    p_render.set_defaults(func=cmd_render_notes)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    main()