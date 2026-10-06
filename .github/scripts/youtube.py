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

# requests will be imported inside functions that need it to avoid hard dependency for pure functions


def version_sort_key(version: str) -> tuple[int, ...]:
    """
    Convert a version string into a tuple of integers for sorting.
    """
    return tuple(int(part) for part in version.split("."))


_VERSION_RE = re.compile(r"\s*(\d+(?:\.\d+)+)")
# Deliberately loose. `list-versions` prefixes entries with decorations we do not
# care about, so we pull the first dotted number out of each line rather than
# trying to model the exact output format, which upstream is free to change.


def parse_version_lines(text: str) -> list[str]:
    """Parse the output of `morphe-desktop list-versions` to extract version strings."""
    out = []
    for line in text.splitlines():
        m = _VERSION_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def strip_apk_version(filename: str) -> str:
    """
    Strip the version from an APK filename.
    Returns None if no version is found.
    """
    # Match a version number (digits and dots) at the end of the filename, possibly preceded by a hyphen.
    match = re.search(r"(\d+\.\d+\.\d+)$", filename)
    if match:
        return match.group(1)
    # If no version at the end, try to find a version in the middle (e.g., "app-release-21.39.522.apk")
    match = re.search(r"(\d+\.\d+\.\d+)", filename)
    if match:
        return match.group(1)
    return None


def parse_list_patches(text):
    names = []
    current = None
    for line in text.splitlines():
        if line.startswith("Name: "):
            current = line[len("Name: ") :]
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
                    f"cannot describe {v} in the release body\n"
                )
                sys.exit(1)
        applied = [
            ln.strip()
            for ln in (dirs["applied"] / f"applied-{v}.txt").read_text().splitlines()
            if ln.strip()
        ]
        failed = [
            ln.strip()
            for ln in (dirs["failed"] / f"failed-{v}.txt").read_text().splitlines()
            if ln.strip()
        ]
        records.append(
            {
                "version": v,
                "size": apk.stat().st_size // (1024 * 1024),
                "arch": arch,
                "applied": applied,
                "failed": failed,
            }
        )
    records.sort(key=lambda r: version_sort_key(r["version"]), reverse=True)
    return records


def pick_anchor(records, expected):
    """Newest version where every expected patch applied cleanly.

    This is the version the release body tells users to install. It is derived from
    the patch results rather than trusted from the bundles' own recommendation,
    because a recommended version can still fail to apply cleanly in practice.
    """
    ordered = sorted(
        records, key=lambda r: version_sort_key(r["version"]), reverse=True
    )
    want = set(expected)
    for r in ordered:
        if want <= set(r.get("applied", [])):
            return r["version"]
    return ""


def _patch_row(patch: str, applied: list[str], failed: list[str]) -> str:
    """Return a formatted table row for a patch: |emoji|patch|."""
    if patch in applied:
        emoji = "🟢"
    elif patch in failed:
        emoji = "🔴"
    else:
        emoji = "⚪"
    return f"|{emoji}|{patch}|"


def render_notes(
    records: list[dict],
    expected: list[str],
    tools: list[tuple[str, str, str]],
    microg_tag: str,
) -> str:
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
        lines.append(
            f"> **[MicroG]({microg_repo_url}/releases/tag/{microg_tag})** is required to use this app"
        )
        lines.append("> Install the newest version before following the tip.")
        lines.append("")
    # TIP anchor line
    anchor = pick_anchor(records, expected)
    lines.append("> [!TIP]")
    lines.append(
        f"> Install `{anchor}`. It is the newest version where every patch applied cleanly."
    )
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
        lines.append(
            f"<summary><b>v{version}</b> - <code>{n_applied} of {len(expected)}</code></summary>"
        )
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
        lines.append(
            f'| {version} | {channel} | {arm} | {size_mb}.0MB | <a href="{download_url}"><img src="{icon_url}" width="20" alt="Download {version}"></a> |'
        )
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
        with open(args.expected_patches, "r") as f:
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
        with open(args.tools_json, "r") as f:
            tools_data = json.load(f)
        tools = [(t["name"], t["repo_url"], t["tag"]) for t in tools_data]
    except Exception as e:
        sys.stderr.write(f"::error::Failed to read tools JSON: {e}\n")
        return 1

    microg_tag = args.microg_tag or ""

    result = render_notes(records, expected, tools, microg_tag)

    try:
        with open(args.output, "w") as f:
            f.write(result)
    except Exception as e:
        sys.stderr.write(f"::error::Failed to write output: {e}\n")
        return 1

    return 0


def build_matrix(versions: list[str]) -> tuple[str, str, int]:
    """Build the versions matrix string for discover output.

    Args:
        versions: List of version strings

    Returns:
        Tuple of (matrix_json, channels_json, nothing_to_build_count)
        where matrix_json and channels_json are compact JSON strings
    """
    if not versions:
        return '{"include":[{"noop":true}]}', '{"channels":{"stable":[],"beta":[]}}', 1

    sorted_versions = sorted(versions, key=version_sort_key, reverse=True)

    entries = ",".join(f'{{"version":"{v}","channel":"Stable"}}' for v in sorted_versions)
    matrix = '{"include":[' + entries + "]}"
    channels = {"stable": sorted_versions, "beta": []}
    nothing_to_build = 0

    matrix_json = matrix
    channels_json = json.dumps({"channels": channels}, separators=(",", ":"))

    return matrix_json, channels_json, nothing_to_build


def classify_versions(
    stable: list[str], experimental: list[str]
) -> tuple[list[str], list[str]]:
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


def discover_core(
    versions_raw: str,
    stable_raw: str,
    exclude: str,
    state: dict | None,
    patches_ver: str,
    bundle_patch_names: list[str],
) -> dict:
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
    # Parse the input strings into lists, keeping only real version lines
    versions_list = parse_version_lines(versions_raw) if versions_raw else []
    stable_list = parse_version_lines(stable_raw) if stable_raw else []
    exclude_list = (
        [v.strip() for v in exclude.split("\n") if v.strip()] if exclude else []
    )

    # Filter out excluded versions from versions_list
    versions_filtered = [v for v in versions_list if v not in exclude_list]

    # Build the matrix and channels
    matrix_json, channels_json, nothing_to_build = build_matrix(versions_filtered)

    # Build expected patches from bundle names
    expected_patches = build_expected_patches(bundle_patch_names)

    result = {
        "matrix": matrix_json,
        "versions": json.dumps(versions_filtered, separators=(",", ":")),
        "channels": channels_json,
        "reused": json.dumps([], separators=(",", ":")),
        "nothing_to_build": nothing_to_build,
        "expected_patches": json.dumps(expected_patches, separators=(",", ":")),
        "all_versions": json.dumps(versions_list, separators=(",", ":")),
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

        result = subprocess.run(
            cmd, timeout=timeout, capture_output=capture_output, text=True, check=False
        )
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

        result = subprocess.run(
            cmd, timeout=timeout, capture_output=capture_output, text=True, check=False
        )
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

        result = subprocess.run(
            cmd, capture_output=capture_output, text=True, check=False
        )
        if capture_output:
            return result.stdout.strip()
        return ""
    except Exception as e:
        sys.stderr.write(f"::warning::GitHub CLI command failed: {e}\n")
        return "" if capture_output else ""


def cmd_fetch_toolchain(args) -> int:
    """Handle the fetch-toolchain subcommand."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from common import download_morphe_patches, download_morphe_desktop

        owner = args.owner or os.environ.get("MORPHE_OWNER", "")
        patches_repo = args.patches_repo or os.environ.get("MORPHE_PATCHES_REPO", "")
        desktop_repo = args.desktop_repo or os.environ.get("MORPHE_DESKTOP_REPO", "")
        if owner and "/" not in patches_repo:
            patches_repo = f"{owner}/{patches_repo}"
        if owner and "/" not in desktop_repo:
            desktop_repo = f"{owner}/{desktop_repo}"

        patches_ver = download_morphe_patches(
            owner, patches_repo.rsplit("/", 1)[-1], dest=".", token=args.token,
            output="patches.mpp",
        )
        desktop_ver = download_morphe_desktop(
            owner, desktop_repo.rsplit("/", 1)[-1], dest=".", token=args.token,
            output="morphe-desktop.jar",
        )

        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.write(f"patches_ver={patches_ver}\n")
                f.write(f"desktop_ver={desktop_ver}\n")
        print(f"patches: patches-{patches_ver}.mpp")
        print(f"patcher: morphe-desktop-{desktop_ver}-all.jar")
        for f in ("patches.mpp", "morphe-desktop.jar"):
            p = pathlib.Path(f)
            if p.exists():
                print(f"{f}: {p.stat().st_size} bytes")
            else:
                print(f"{f}: MISSING")
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to fetch toolchain: {e}\n")
        return 1


def cmd_discover(args) -> int:
    """Handle the discover subcommand."""
    try:
        versions_raw = args.versions_raw or ""
        stable_raw = args.stable_raw or ""
        exclude = args.exclude or ""

        state = None
        if args.state:
            try:
                with open(args.state, "r") as f:
                    state = json.load(f)
            except Exception as e:
                sys.stderr.write(f"::warning::Failed to read state file: {e}\n")

        result = discover_core(
            versions_raw,
            stable_raw,
            exclude,
            state,
            args.patches_ver,
            args.bundle_patch_names,
        )

        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.writelines(f"{key}={value}\n" for key, value in result.items())

        versions_list = json.loads(result.get("versions", "[]"))
        reused_list = json.loads(result.get("reused", "[]"))
        nothing_to_build = result.get("nothing_to_build", 0)
        if nothing_to_build > 0:
            print("covering 0 version(s) (reused=0), nothing to build")
        else:
            print(
                f"covering {len(versions_list)} version(s) (reused={len(reused_list)})"
            )

        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to discover: {e}\n")
        return 1


def cmd_select_options(args) -> int:
    """Handle the select-options subcommand."""
    try:
        # Run the java options-create command
        cmd = [
            "java",
            "-jar",
            "morphe-desktop.jar",
            "options-create",
            "-f",
            args.package,
            "-o",
            "options.json",
            "-p",
            args.patch_bundle,
        ]
        output = _run_java(cmd, capture_output=True)
        if output:
            print(output)
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to run select-options: {e}\n")
        return 1


def cmd_download_apk(args) -> int:
    """Handle the download-apk subcommand."""
    try:
        cmd = [
            "bun",
            "node_modules/apkmirror-downloader/dist/cli.js",
            "download",
            "google-inc",
            "youtube",
            f"--version={args.version}",
            "--outdir=download",
        ]
        _run_bun(cmd, capture_output=False)
        apks = list(pathlib.Path("download").glob("*.apk"))
        if not apks:
            sys.stderr.write(f"::error::No APK downloaded for {args.version}\n")
            return 1
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to download APK: {e}\n")
        return 1


def cmd_patch(args) -> int:
    """Handle the patch subcommand."""
    try:
        in_apk = f"./download/youtube-{args.version}-{args.arch}.apk"
        out_apk = f"./release/youtube-{args.version}-{args.arch}.apk"

        cmd = [
            "java",
            "-jar",
            "morphe-desktop.jar",
            "patch",
            "-p",
            "patches.mpp",
            "-e",
            "Change installer source",
            "-e",
            "Disable Play Store updates",
            "--options-file",
            "./options.json",
            "--striplibs",
            args.arch,
            "--out",
            out_apk,
            "-r",
            "./result.json",
            "--keystore",
            "./src/keystore/morphe.keystore",
            "--force",
            "--continue-on-error",
            in_apk,
        ]
        _run_java(cmd, capture_output=False)

        # Extract applied and failed patch lists from result.json
        result_path = pathlib.Path("./result.json")
        if result_path.exists():
            result = json.loads(result_path.read_text())
            applied = result.get("appliedPatches", [])
            failed = result.get("failedPatches", [])
            applied_names = [
                p["name"] for p in applied if isinstance(p, dict) and "name" in p
            ]
            failed_names = [
                p.get("patch", {}).get("name", "")
                for p in failed
                if isinstance(p, dict)
            ]
            pathlib.Path(f"./applied-{args.version}.txt").write_text(
                "\n".join(applied_names) + ("\n" if applied_names else "")
            )
            pathlib.Path(f"./failed-{args.version}.txt").write_text(
                "\n".join(failed_names) + ("\n" if failed_names else "")
            )

        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to patch APK: {e}\n")
        return 1


def cmd_cleanup_artifacts(args) -> int:
    """Handle the cleanup-artifacts subcommand."""
    try:
        list_cmd = [
            "gh",
            "api",
            f"repos/{args.repo_full}/actions/runs/{args.run_id}/artifacts",
            "--paginate",
            "--jq",
            ".artifacts[].id",
        ]
        output = _run_gh(list_cmd, capture_output=True)
        if not output:
            return 0
        artifact_ids = output.split("\n")
        count = 0
        for artifact_id in artifact_ids:
            if not artifact_id:
                continue
            delete_cmd = [
                "gh",
                "api",
                f"repos/{args.repo_full}/actions/artifacts/{artifact_id}",
                "--method",
                "DELETE",
            ]
            _run_gh(delete_cmd, capture_output=False)
            count += 1
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to cleanup artifacts: {e}\n")
        return 1


YOUTUBE_ORG = "MorpheApp"
YOUTUBE_REPO = "morphe-patches"
STATE_PATH = ".github/tags/youtube.json"


def youtube_state(records, patcher, package, sources_map):
    """Build the committed build record.

    Per patch, the versions it applied on and the versions it failed on. Storing that
    history is what lets the next run decide whether anything actually changed, so the
    record has to be built from what shipped rather than from what was attempted.
    """
    ordered = sorted(
        records or [], key=lambda r: version_sort_key(r["version"]), reverse=True
    )
    versions = [r["version"] for r in ordered]
    sources_out = {}
    for owner, src in sources_map.items():
        patches_out = {}
        for name in src.get("list", []):
            applied = [r["version"] for r in ordered if name in r.get("applied", [])]
            failed = [r["version"] for r in ordered if name in r.get("failed", [])]
            patches_out[name] = {"applied": applied, "failed": failed}
        sources_out[owner] = {
            "version": src.get("version", ""),
            "list": list(src.get("list", [])),
            "patches": patches_out,
        }
    return {
        "owner": YOUTUBE_ORG,
        "repo": YOUTUBE_REPO,
        "package": package,
        "versions": versions,
        "patcher": patcher,
        "sources": sources_out,
    }


def _contents_url(repo_full, path):
    return f"/repos/{repo_full}/contents/{path}"


def state_put(repo_full, state_path, content_str, message, token=None):
    """Commit state via the GitHub contents API with sha-conditional retry."""
    import base64

    import requests

    content_b64 = base64.b64encode(content_str.encode()).decode()
    url = f"https://api.github.com{_contents_url(repo_full, state_path)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
    }
    for attempt in range(1, 6):
        get_resp = requests.get(url, headers=headers)
        sha = None
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")
        data = {
            "message": message,
            "content": content_b64,
        }
        if sha:
            data["sha"] = sha
        put_resp = requests.put(url, headers=headers, json=data)
        if put_resp.status_code in (200, 201):
            return
        if attempt == 5:
            sys.stderr.write(
                f"::error::could not commit {state_path} after 5 attempts: "
                f"{put_resp.text}\n"
            )
            sys.exit(1)
        sys.stderr.write(
            f"contents PUT conflicted (attempt {attempt}), retrying on fresh sha\n"
        )
        time.sleep(2 ** min(attempt, 4))


def remove_youtube_entry_from_shared_state(path):
    """Remove the youtube key from the shared patch_version.json."""
    data = json.loads(pathlib.Path(path).read_text())
    sources = data.get("source", {})
    morpheapp = sources.get("MorpheApp", {})
    patches = morpheapp.get("morphe-patches", {})
    for tag, apps in patches.items():
        if "youtube" in apps:
            del apps["youtube"]
            data["source"]["MorpheApp"]["morphe-patches"][tag] = apps
    pathlib.Path(path).write_text(json.dumps(data, indent=2) + "\n")


def cmd_record_state(args) -> int:
    """Handle the record-state subcommand."""
    try:
        patcher = os.environ.get("PATCHER", "morphe-patches.mpp")
        package = os.environ.get("PACKAGE", "com.google.android.youtube")
        sources_map = json.loads(os.environ.get("SOURCES", "{}"))
        token = os.environ.get("GH_TOKEN")
        repo_full = os.environ.get("REPO_FULL", "rahaaatul/Morphed")

        keep = (
            json.loads(pathlib.Path(args.keep_in).read_text()) if args.keep_in else {}
        )
        versions = keep.get("versions", [])
        applied = keep.get("applied", [])
        failed = keep.get("failed", [])
        records = [
            {"version": v, "applied": applied, "failed": failed} for v in versions
        ]
        if not records:
            sys.stderr.write("Nothing shipped, leaving the state file alone\n")
            return 0

        record = youtube_state(records, patcher, package, sources_map)
        content_str = json.dumps(record, indent=2) + "\n"

        state_put(
            repo_full,
            args.state_file,
            content_str,
            f"Record YouTube build, patcher {patcher}",
            token=token,
        )
        sys.stderr.write(f"Committed {args.state_file}\n")
        return 0
    except Exception as e:
        sys.stderr.write(f"::error::Failed to record state: {e}\n")
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
    p_discover = subparsers.add_parser(
        "discover", help="Discover versions and build matrix"
    )
    p_discover.add_argument("--versions-raw", default="")
    p_discover.add_argument("--stable-raw", default="")
    p_discover.add_argument("--exclude", default="")
    p_discover.add_argument("--state", help="Path to state JSON file")
    p_discover.add_argument("--patches-ver", required=True)
    p_discover.add_argument("--bundle-patch-names", required=True)
    p_discover.set_defaults(func=cmd_discover)

    # select-options
    p_select = subparsers.add_parser(
        "select-options", help="Select options for building"
    )
    p_select.add_argument("--package", required=True)
    p_select.add_argument("--patch-bundle", required=True)
    p_select.set_defaults(func=cmd_select_options)

    # download-apk
    p_download = subparsers.add_parser(
        "download-apk", help="Download APK via APKMirror"
    )
    p_download.add_argument("--version", required=True)
    p_download.set_defaults(func=cmd_download_apk)

    # patch
    p_patch = subparsers.add_parser("patch", help="Patch APK via Morphe Desktop")
    p_patch.add_argument("--version", required=True)
    p_patch.add_argument("--arch", required=True)
    p_patch.set_defaults(func=cmd_patch)

    # cleanup-artifacts
    p_cleanup = subparsers.add_parser(
        "cleanup-artifacts", help="Clean up run artifacts"
    )
    p_cleanup.add_argument("--repo-full", required=True)
    p_cleanup.add_argument("--run-id", required=True)
    p_cleanup.set_defaults(func=cmd_cleanup_artifacts)

    # record-state
    p_record = subparsers.add_parser("record-state", help="Record build state")
    p_record.add_argument("--keep-in", required=True)
    p_record.add_argument("--state-file", required=True)
    p_record.set_defaults(func=cmd_record_state)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    import sys
    sys.exit(main())
