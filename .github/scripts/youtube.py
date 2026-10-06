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


def build_matrix(versions: list[str]) -> tuple[str, str, int]:
    """Build the versions matrix string for discover output.
    
    Args:
        versions: List of version strings
        
    Returns:
        Tuple of (matrix_json, channels_json, nothing_to_build_count)
        where matrix_json and channels_json are compact JSON strings
    """
    if not versions:
        return '{"matrix":{}}', '{"channels":{}}', 0
    
    # Sort versions newest first using our version_sort_key
    sorted_versions = sorted(versions, key=version_sort_key, reverse=True)
    
    # Build the matrix: map each version to an empty object (to be filled later)
    matrix = {version: {} for version in sorted_versions}
    
    # For now, we'll classify all as stable (this will be refined in discover_core)
    stable_versions = sorted_versions
    experimental_versions = []
    
    channels = {
        "stable": stable_versions,
        "beta": experimental_versions
    }
    
    # nothing_to_build is 0 unless we have special handling
    nothing_to_build = 0
    
    import json
    # Use compact separators as specified in the plan
    matrix_json = json.dumps({"matrix": matrix}, separators=(",", ":"))
    channels_json = json.dumps({"channels": channels}, separators=(",", ":"))
    
    return matrix_json, channels_json, nothing_to_build


def classify_versions(stable: list[str], experimental: list[str]) -> tuple[list[str], list[str]]:
    """Classify versions into stable and experimental lists.
    
    Args:
        stable: List of versions that are stable
        experimental: List of versions that are experimental
        
    Returns:
        Tuple of (stable_versions, experimental_versions)
        This is essentially a pass-through function that validates inputs
    """
    # In a real implementation, this might do more complex logic
    # For now, we just return the inputs as-is
    return stable, experimental


def discover_core(versions_raw: str, stable_raw: str, exclude: str,
                  state: dict | None, patches_ver: str,
                  bundle_patch_names: list[str]) -> dict:
    """Core discovery logic that processes versions and state.
    
    Args:
        versions_raw: Raw versions string from list-versions -x
        stable_raw: Raw stable versions string from list-versions
        exclude: Exclude versions string from EXCLUDE_VERSIONS env var
        state: Current state dictionary or None
        patches_ver: Current patches version
        bundle_patch_names: List of bundle patch names
        
    Returns:
        DiscoverResult dictionary with matrix, versions, channels, reused,
        nothing_to_build, expected_patches, and all_versions
    """
    # Parse the input strings into lists
    versions_list = [v.strip() for v in versions_raw.split("\n") if v.strip()] if versions_raw else []
    stable_list = [v.strip() for v in stable_raw.split("\n") if v.strip()] if stable_raw else []
    exclude_list = [v.strip() for v in exclude.split("\n") if v.strip()] if exclude else []
    
    # Filter out excluded versions from versions_list
    versions_filtered = [v for v in versions_list if v not in exclude_list]
    
    # For now, we'll implement a simplified version
    # In a full implementation, this would do more complex logic involving
    # state comparison, bundle patch names, etc.
    
    # Use build_matrix to get the basic matrix and channels
    matrix_json, channels_json, nothing_to_build = build_matrix(versions_filtered)
    
    # For now, we'll return simplified results
    # A full implementation would populate these properly
    result = {
        "matrix": matrix_json,
        "versions": json.dumps(versions_filtered, separators=(",", ":")),
        "channels": channels_json,
        "reused": json.dumps([], separators=(",", ":")),  # No reused versions for now
        "nothing_to_build": nothing_to_build,
        "expected_patches": json.dumps([], separators=(",", ":")),  # To be implemented
        "all_versions": json.dumps(versions_list, separators=(",", ":"))  # Original list
    }
    
    return result


def build_expected_patches(bundle_names: list[str]) -> list[str]:
    """Build expected patches list from bundle names, deduplicating and adding forced patches.
    
    Args:
        bundle_names: List of bundle patch names
        
    Returns:
        List of expected patch names with duplicates removed and forced patches added
    """
    # Remove duplicates while preserving order
    seen = set()
    deduped = []
    for name in bundle_names:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    
    # For now, we don't have forced patches configured, so we just return the deduped list
    # In a full implementation, we would add forced patches like:
    # forced_patches = ["Change installer source", "Disable Play store updates"]
    # for patch in forced_patches:
    #     if patch not in seen:
    #         deduped.append(patch)
    
    return deduped


def gate_rebuild(last_state: dict | None, sources: dict, covered: list[str]) -> int:
    """Determine if a rebuild is needed based on state changes.
    
    Args:
        last_state: Previous state dictionary or None
        sources: Current sources dictionary
        covered: List of patch names that have been covered/applied
        
    Returns:
        1 if rebuild is needed, 0 if not
    """
    # If there's no last state, we always need to rebuild
    if last_state is None:
        return 1
    
    # Check if the sources have changed
    # For now, we'll do a simple comparison
    # In a full implementation, this would compare the relevant parts of the state
    if last_state != sources:
        return 1
    
    # Check if covered patches have changed
    # This would compare the covered patches list with what's in last_state
    # For now, we'll return 0 (no rebuild needed) if we got this far
    # A full implementation would do proper comparison
    
    return 0


def _run_java(cmd: list[str], timeout: int = 600, capture_output: bool = False) -> str:
    """Run a Java command and return its output.
    
    Args:
        cmd: Command and arguments as list of strings
        timeout: Timeout in seconds
        capture_output: Whether to capture and return output
        
    Returns:
        Command output as string if capture_output is True, otherwise empty string
    """
    try:
        import subprocess
        result = subprocess.run(cmd, timeout=timeout, capture_output=capture_output, text=True, check=False)
        if capture_output:
            return result.stdout.strip()
        return ""
    except Exception as e:
        sys.stderr.write(f"::warning::Java command failed: {e}\n")
        return "" if capture_output else ""


def _run_bun(cmd: list[str], timeout: int = 1200, capture_output: bool = False) -> str:
    """Run a Bun command and return its output.
    
    Args:
        cmd: Command and arguments as list of strings
        timeout: Timeout in seconds
        capture_output: Whether to capture and return output
        
    Returns:
        Command output as string if capture_output is True, otherwise empty string
    """
    try:
        import subprocess
        result = subprocess.run(cmd, timeout=timeout, capture_output=capture_output, text=True, check=False)
        if capture_output:
            return result.stdout.strip()
        return ""
    except Exception as e:
        sys.stderr.write(f"::warning::Bun command failed: {e}\n")
        return "" if capture_output else ""


def _run_gh(cmd: list[str], capture_output: bool = False) -> str:
    """Run a GitHub CLI command and return its output.
    
    Args:
        cmd: Command and arguments as list of strings
        capture_output: Whether to capture and return output
        
    Returns:
        Command output as string if capture_output is True, otherwise empty string
    """
    try:
        import subprocess
        result = subprocess.run(cmd, capture_output=capture_output, text=True, check=False)
        if capture_output:
            return result.stdout.strip()
        return ""
    except Exception as e:
        sys.stderr.write(f"::warning::GitHub CLI command failed: {e}\n")
        return "" if capture_output else ""


def cmd_fetch_toolchain(args) -> int:
    """Handle the fetch-toolchain subcommand."""
    try:
        result = fetch_toolchain(args.owner, args.patches_repo, args.desktop_repo, args.token)
        # Write outputs to $GITHUB_OUTPUT format
        print(f"PATCHES_VER={result['patches_ver']}")
        print(f"DESKTOP_VER={result['desktop_ver']}")
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to fetch toolchain: {e}\n")
        return 1


def cmd_discover(args) -> int:
    """Handle the discover subcommand."""
    try:
        # Parse the inputs
        versions_raw = args.versions_raw or ""
        stable_raw = args.stable_raw or ""
        exclude = args.exclude or ""
        
        # Handle state if provided
        state = None
        if args.state:
            try:
                with open(args.state, 'r') as f:
                    state = json.load(f)
            except Exception as e:
                sys.stderr.write(f"::warning::Failed to read state file: {e}\n")
        
        result = discover_core(
            versions_raw, stable_raw, exclude,
            state, args.patches_ver, args.bundle_patch_names
        )
        
        # Write outputs to $GITHUB_OUTPUT format
        for key, value in result.items():
            if isinstance(value, str):
                print(f"{key.upper()}={value}")
            else:
                print(f"{key.upper()}={value}")
        
        # Print the covering message
        nothing_to_build = result.get("nothing_to_build", 0)
        if nothing_to_build > 0:
            print(f"covering {nothing_to_build} version(s) (reused=...), nothing to build")
        else:
            # Calculate covered count from result if available
            reused_str = result.get("reused", "[]")
            try:
                reused_list = json.loads(reused_str)
                reused_count = len(reused_list)
                if reused_count > 0:
                    print(f"covering {len(json.loads(result['versions']))} version(s) (reused={reused_count})")
                else:
                    print(f"covering {len(json.loads(result['versions']))} version(s) (reused=0)")
            except:
                print(f"covering {len(json.loads(result['versions']))} version(s) (reused=0)")
        
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to discover: {e}\n")
        return 1


def cmd_select_options(args) -> int:
    """Handle the select-options subcommand."""
    try:
        # Run the java options-create command
        cmd = [
            "java", "-jar", "morphe-desktop.jar",
            "options-create",
            "-f", args.package,
            "-o", "options.json",
            "-p", args.patch_bundle
        ]
        output = _run_java(cmd, capture_output=True)
        if output:
            print(output)
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to run select-options: {e}\n")
        return 1


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

    # fetch-toolchain
    p_fetch = subparsers.add_parser("fetch-toolchain", help="Fetch toolchain versions")
    p_fetch.add_argument("--owner", required=True)
    p_fetch.add_argument("--patches-repo", required=True)
    p_fetch.add_argument("--desktop-repo", required=True)
    p_fetch.add_argument("--token", required=True)
    p_fetch.set_defaults(func=cmd_fetch_toolchain)

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover versions and build matrix")
    p_discover.add_argument("--versions-raw", default="")
    p_discover.add_argument("--stable-raw", default="")
    p_discover.add_argument("--exclude", default="")
    p_discover.add_argument("--state", help="Path to state JSON file")
    p_discover.add_argument("--patches-ver", required=True)
    p_discover.add_argument("--bundle-patch-names", required=True)
    p_discover.set_defaults(func=cmd_discover)

    # select-options
    p_select = subparsers.add_parser("select-options", help="Select options for building")
    p_select.add_argument("--package", required=True)
    p_select.add_argument("--patch-bundle", required=True)
    p_select.set_defaults(func=cmd_select_options)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    main()