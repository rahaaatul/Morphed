#!/usr/bin/env python3
"""Proton VPN release pipeline, one argparse subcommand per workflow step.

Stdlib Python only. External CLI tools (gh, java, curl) are invoked directly as
subprocesses and are never wrapped here, so nothing in this file exists only to
call a CLI from a different language.

Each subcommand reads the values the workflow owns from the environment, writes
results back through $GITHUB_OUTPUT, and exits non-zero with a ::error:: message
so a workflow step fails loudly rather than shipping a wrong APK or a wrong
release body.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

APP_SLUG = "protonvpn"
# Defaults only. Every one of these is normally supplied by the workflow's env: block,
# which stays the single source of truth for a value shared across steps.
PACKAGE = "ch.protonvpn.android"
RELEASE_TAG = "proton-vpn"
REPO_FULL = "rahaaatul/Morphed"
APK_PREFIX = "proton-vpn-v"
MB = 1024 * 1024


def version_sort_key(v: str):
    parts = []
    for piece in str(v).split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _strip_apk_version(basename: str):
    if not basename.startswith(APK_PREFIX):
        return None
    rest = basename[len(APK_PREFIX):]
    if not rest.endswith(".apk"):
        return None
    return rest[:-len(".apk")]


def parse_list_versions(text):
    return [line for line in text.splitlines() if line]


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


def choose_cover(versions, recommended, max_versions):
    """Trim the release list down to the span users actually need.

    Both patch bundles name one recommended version. We keep the newest of those
    recommendations plus a margin beneath it, so a user who wants the newest
    fully-patched build is never pushed further back than the margin, while a
    bundle's older recommendation still falls inside the published range.

    A recommendation that is not a published tag is a warning, not a failure: the
    bundle may simply be ahead of the release list we just fetched.
    """
    versions = list(versions)
    deepest = 0
    for rec in recommended:
        if not rec:
            continue
        try:
            idx = versions.index(rec) + 1
        except ValueError:
            sys.stderr.write(
                f"::warning::{rec} is not a published release tag\n")
            continue
        if idx > deepest:
            deepest = idx
    if deepest == 0:
        # Neither recommendation resolved to a published tag. Three keeps the release
        # useful rather than collapsing it to nothing on a bad fetch.
        deepest = 3
    want_n = deepest + 2
    if want_n > max_versions:
        want_n = max_versions
    return versions[:want_n]


def build_matrix(versions):
    versions = [v for v in versions if v]
    if not versions:
        # A one-entry noop matrix keeps the downstream jobs' shape valid. Without it the
        # matrix would be empty and the release would have nothing to publish.
        return '{"include":[{"noop":true}]}', "{}", 1
    entries = ",".join(f'{{"version":"{v}","channel":"Canary"}}' for v in versions)
    channels = ",".join(f'"{v}":"Canary"' for v in versions)
    matrix = '{"include":[' + entries + "]}"
    channels = "{" + channels + "}"
    return matrix, channels, 0


def classify_versions(stable, experimental):
    stable_set = set(stable)
    return list(stable), [v for v in experimental if v not in stable_set]


def build_recommended(doom_rec, hoo_rec):
    obj = {
        "Doom's Morphe Patches": {
            "url": "https://github.com/rushiranpise/morphe-patches/",
            "value": doom_rec,
        },
        "hoodles Morphe Patches": {
            "url": "https://github.com/hoo-dles/morphe-patches",
            "value": hoo_rec,
        },
    }
    return json.dumps(obj, separators=(",", ":"))


def build_expected_patches(source_lists, extra_text):
    """Every patch name the release body will later expect to see accounted for.

    Drawn from the two Proton-targeting bundles plus the forced-on extras. The body
    iterates this list, so a name missing here disappears from the published matrix
    instead of being reported as unapplied.
    """
    all_names = []
    for src in source_lists:
        all_names.extend(name for name in src if name)
    all_names.extend(parse_list_versions(extra_text))
    seen = set()
    result = []
    for name in all_names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


_VERSION_RE = re.compile(r"\s*(\d+(?:\.\d+)+)")
# Deliberately loose. `list-versions` prefixes entries with decorations we do not
# care about, so we pull the first dotted number out of each line rather than trying
# to model the exact output format, which upstream is free to change.


def parse_version_lines(text):
    out = []
    for line in text.splitlines():
        m = _VERSION_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def build_records(release_dir, applied_dir, failed_dir, arch):
    release_dir = Path(release_dir)
    dirs = {"applied": Path(applied_dir), "failed": Path(failed_dir)}
    records = []
    for apk in release_dir.glob("*.apk"):
        v = _strip_apk_version(apk.name)
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
            "size": apk.stat().st_size // MB,
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


def _channel_of(v, stable, experimental):
    if v in stable:
        return "Stable"
    if v in experimental:
        return "Beta"
    return "Canary"


def _patch_block_lines(record, expected):
    applied = set(record.get("applied", []))
    failed = set(record.get("failed", []))
    n = sum(1 for p in expected if p in applied)
    lines = [
        "<details>",
        f"<summary><b>v{record['version']}</b> - <code>{n}</code></summary>",
        "<br>",
        "",
        "|Status|Patch|",
        "| :---: | :---: |",
    ]
    for p in expected:
        # Three states, not two. A patch the patcher never reported on is not the same
        # as one that tried and failed, and the body distinguishes them so a reader can
        # tell "broken here" from "no longer offered".
        if p in applied:
            mark = "\U0001F7E2"
        elif p in failed:
            mark = "\U0001F534"
        else:
            mark = "⚪"
        lines.append(f"| {mark}|{p}|")
    lines += ["", "</details>", ""]
    return lines


def render_notes(records, expected, recommended, tools, stable, experimental, *,
                 repo_full, release_tag, icon_url, icon_width):
    order = sorted(records, key=lambda r: version_sort_key(r["version"]),
                   reverse=True)
    anchor = pick_anchor(order, expected)

    lines = ["> [!TIP]"]
    if anchor:
        lines.append(
            f"> Install `{anchor}`. It is the newest version where "
            f"every patch applied cleanly.")
    else:
        # Better an explicit warning than a tip pointing at a version with holes in it.
        lines += ["> [!WARNING]", "> No shipped version had every patch applied."]
    lines.append("")

    lines.append("> [!NOTE]")
    for name, info in recommended.items():
        lines.append(
            f"> - **[{name}]({info['url']})** recommends `{info['value']}`.")
    lines.append("")

    lines.append("## Downloads")
    lines.append("")
    lines += [
        "> [!NOTE]",
        "> - `Stable` - Tested and Recommended",
        "> - `Beta` - Works but not recommended",
        "> - `Canary` - Not tested, no guarantee to work",
        "",
    ]
    lines += [
        "<details>",
        "<summary><b>Click here</b> to see the patches applied</summary>",
        "<br>",
        "",
    ]
    for r in order:
        lines += _patch_block_lines(r, expected)
    lines += ["</details>", ""]

    lines.append("| Version | Channel | Arch | Size | Download |")
    lines.append("| :-------: | :-------: | :---------: | :-----: | :------------------------: |")
    for r in order:
        v = r["version"]
        url = (f"https://github.com/{repo_full}/releases/download/"
               f"{release_tag}/{APK_PREFIX}{v}.apk")
        lines.append(
            f"| `{v}` | {_channel_of(v, stable, experimental)} | {r['arch']} "
            f"| {r['size']}.0MB | <a href=\"{url}\"><img src=\"{icon_url}\" "
            f"width=\"{icon_width}\" alt=\"Download {v}\"></a> |")
    lines.append("")

    lines.append("## Tools used")
    lines.append("")
    lines.append("| Tool | Version |")
    lines.append("| --- | --- |")
    for name, repo_url, tag in tools:
        base = repo_url.rstrip("/")
        lines.append(f"| [{name}]({repo_url}) | [`{tag}`]({base}/releases/tag/{tag}) |")

    return "\n".join(lines) + "\n"


def _normalize_list(raw):
    """Accept a patch list as JSON or as newline-separated text.

    The workflow passes EXPECTED_PATCHES as one JSON line, but a hand-run invocation
    naturally passes a plain list. Both shapes have to work, so this sniffs rather
    than insisting on one.
    """
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    return [ln for ln in raw.splitlines() if ln.strip()]


def cmd_render_notes(args):
    arch = os.environ.get("ARCH", "Universal")
    records = build_records(args.release_dir, args.applied_dir,
                            args.failed_dir, arch)

    if not records:
        # Publishing an empty release would clear every artifact off the tag, so this
        # is the one condition that must stop the whole run.
        sys.stderr.write(
            "::error::No APKs were produced by any patch job\n")
        sys.exit(1)

    keep_path = Path(args.keep_out)
    keep_path.parent.mkdir(parents=True, exist_ok=True)
    # keep.json is the handoff between this step and record-state, which needs to know
    # what actually shipped rather than re-deriving it from the artifacts.
    keep_path.write_text(json.dumps(records, indent=2) + "\n")

    expected = _normalize_list(os.environ["EXPECTED_PATCHES"])
    recommended = json.loads(os.environ["RECOMMENDED"])
    stable = json.loads(os.environ.get("STABLE_VERSIONS", "[]"))
    experimental = json.loads(os.environ.get("EXPERIMENTAL_VERSIONS", "[]"))
    tools = json.loads(os.environ.get("TOOLS", "[]"))

    body = render_notes(
        records, expected, recommended, tools, stable, experimental,
        repo_full=os.environ["REPO_FULL"],
        release_tag=os.environ["RELEASE_TAG"],
        icon_url=os.environ["ICON_URL"],
        icon_width=os.environ["ICON_WIDTH"],
    )
    Path(args.output).write_text(body)

    versions = [r["version"] for r in records]
    vpath = Path(args.versions_out)
    vpath.parent.mkdir(parents=True, exist_ok=True)
    # indent=2 plus a trailing newline: this file is committed to the repository, so
    # its diffs get read by hand.
    vpath.write_text(json.dumps(versions, indent=2) + "\n")

    # Also on stdout so the body and version list are readable straight from the run log
    # without opening the artifact.
    print(body)
    print(vpath.read_text(), end="")


PROTON_ORG = "ProtonVPN"
PROTON_REPO = "android-app"
STATE_PATH = ".github/tags/proton-vpn.json"
# Only these two bundles ship a Proton VPN patch set, so only a version change in one
# of them can invalidate a patched APK. MorpheApp's own bundle is shared across apps
# and declares nothing for Proton, so its version is recorded but never gates a rebuild.
PROTON_TARGETING_SOURCES = ["rushiranpise", "hoo-dles"]


def record_state(records, patcher, package, sources_map):
    """Build the committed build record.

    Per patch, the versions it applied on and the versions it failed on. Storing that
    history is what lets the next run decide whether anything actually changed, so the
    record has to be built from what shipped rather than from what was attempted.
    """
    ordered = sorted(records or [], key=lambda r: version_sort_key(r["version"]),
                     reverse=True)
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
        "owner": PROTON_ORG,
        "repo": PROTON_REPO,
        "package": package,
        "versions": versions,
        "patcher": patcher,
        "sources": sources_out,
    }


def _load_last_state(state_file):
    path = Path(state_file)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return None


def gate_rebuild(last, sources_map, covered):
    """Decide whether this run has anything new to publish.

    Returns 1 to skip, 0 to rebuild. The comparison is narrow on purpose: only a
    Proton-targeting bundle moving, or the covered range changing, can alter the APKs.
    A newer patcher or a newer MorpheApp bundle changes the recorded provenance but
    leaves the patched output identical, so rebuilding on those would republish
    byte-identical files twice a day.
    """
    if not last:
        return 0
    prev_sources = last.get("sources", {}) or {}
    for src in PROTON_TARGETING_SOURCES:
        prev = prev_sources.get(src, {}) or {}
        if prev.get("version") != sources_map.get(src, {}).get("version"):
            return 0
    if last.get("versions") != list(covered):
        return 0
    return 1


def _contents_url(repo_full, path):
    return f"/repos/{repo_full}/contents/{path}"


def _gh_env(token):
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    return env


def state_put(repo_full, state_path, content_str, message, token=None):
    content_b64 = base64.b64encode(content_str.encode()).decode()
    put_args_base = [
        "gh", "api", "-X", "PUT", _contents_url(repo_full, state_path),
        "-f", f"message={message}",
        "-f", f"content={content_b64}",
    ]
    for attempt in range(1, 6):
        get = subprocess.run(
            ["gh", "api", _contents_url(repo_full, state_path),
             "--jq", ".sha"],
            capture_output=True, text=True, check=False,
            env=_gh_env(token))
        sha = None
        if get.returncode == 0 and get.stdout.strip():
            sha = get.stdout.strip()
        put_args = list(put_args_base)
        if sha:
            put_args += ["-f", f"sha={sha}"]
        put = subprocess.run(put_args, capture_output=True, text=True,
                             check=False, env=_gh_env(token))
        if put.returncode == 0:
            return
        if attempt == 5:
            sys.stderr.write(
                f"::error::could not commit {state_path} after 5 attempts: "
                f"{put.stderr.strip()}\n")
            sys.exit(1)
        sys.stderr.write(
            f"contents PUT conflicted (attempt {attempt}), "
            f"retrying on fresh sha\n")
        time.sleep(2 ** min(attempt, 4))


def cmd_record_state(args):
    patcher = os.environ["PATCHER"]
    package = os.environ.get("PACKAGE", PACKAGE)
    sources_map = json.loads(os.environ.get("SOURCES", "{}"))
    token = os.environ.get("GH_TOKEN")
    repo_full = os.environ.get("REPO_FULL", REPO_FULL)

    records = json.loads(Path(args.keep_in).read_text()) if args.keep_in else []
    if not records:
        sys.stderr.write(
            "Nothing shipped, leaving the state file alone\n")
        return 0

    record = record_state(records, patcher, package, sources_map)
    content_str = json.dumps(record, indent=2) + "\n"

    get = subprocess.run(
        ["gh", "api", _contents_url(repo_full, args.state_file),
         "--jq", ".content"],
        capture_output=True, text=True, check=False, env=_gh_env(token))
    existing_text = None
    if get.returncode == 0 and get.stdout.strip():
        existing_text = base64.b64decode(get.stdout)
    if existing_text is not None and content_str.encode() == existing_text:
        sys.stderr.write(f"{args.state_file} already current\n")
        return 0

    message = (f"Record Proton VPN build, patcher {patcher}")
    state_put(repo_full, args.state_file, content_str, message, token=token)
    sys.stderr.write(f"Committed {args.state_file}\n")
    return 0


def normalize_list(text):
    text = text or ""
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        items = [x for x in parsed if isinstance(x, str)]
    else:
        items = text.split("\n")
    return [x for x in items if x]


def enable_patches(options, want):
    want_set = set(want)
    all_names = set()
    patched = [dict(entry) for entry in options]
    for idx, entry in enumerate(options):
        patches = entry.get("patches") or {}
        all_names.update(patches.keys())
        new_patches = {}
        for key, value in patches.items():
            value = dict(value) if isinstance(value, dict) else {}
            if key in want_set:
                value["enabled"] = True
            new_patches[key] = value
        patched[idx]["patches"] = new_patches
    missing = [w for w in want if w not in all_names]
    return patched, missing


def _count_enabled(options):
    enabled = set()
    for entry in options:
        for name, patch in (entry.get("patches") or {}).items():
            if patch.get("enabled") is True:
                enabled.add(name)
    return enabled


def cmd_select_options(args):
    java = ["java", "-jar", args.jar, "options-create",
            "-f", args.package, "-o", args.options]
    for bundle in (args.patch_bundle or []):
        java += ["-p", bundle]
    subprocess.run(java, check=True)
    options = json.loads(Path(args.options).read_text())
    if not isinstance(options, list):
        sys.stderr.write("::error::options.json is not a bundle list\n")
        sys.exit(1)
    want = normalize_list(args.expected)
    sys.stderr.write("enabling: " + json.dumps(want) + "\n")
    patched, missing = enable_patches(options, want)
    Path(args.options).write_text(json.dumps(patched, indent=2) + "\n")
    enabled = _count_enabled(patched)
    if missing:
        want_n = len(want)
        sys.stderr.write(
            f"::error::enabled {len(enabled)} patch(es), wanted {want_n}: "
            f"{json.dumps(want)}\n")
        sys.stderr.write("::error::a name in the list is not present in any loaded bundle\n")
        sys.exit(1)
    sys.stderr.write(f"patch state: {len(enabled)} enabled\n")
    return 0


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


def cmd_write_patch_lists(args):
    result = json.loads(Path(args.result).read_text())
    applied, failed = extract_patch_lists(result)
    Path(args.applied_out).write_text(
        "".join(name + "\n" for name in applied))
    Path(args.failed_out).write_text(
        "".join(name + "\n" for name in failed))
    print(f"applied {len(applied)}, failed {len(failed)}")
    return 0


def _gh_releases(upstream, token=None):
    out = subprocess.run(
        ["gh", "api", f"repos/{upstream}/releases?per_page=40"],
        capture_output=True, text=True, check=True, env=_gh_env(token),
    )
    return json.loads(out.stdout)


def _java_list_versions(jar, bundle, package, flag=None):
    cmd = ["java", "-jar", jar, "list-versions"]
    if flag:
        cmd.append(flag)
    cmd += ["-f", package, f"--patches={bundle}"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return out.stdout


def _java_list_patches(jar, bundle, package):
    out = subprocess.run(
        ["java", "-jar", jar, "list-patches", "-f", package,
         f"--patches={bundle}"],
        capture_output=True, text=True, check=False,
    )
    return out.stdout


def _first_version(text):
    versions = parse_version_lines(text)
    return versions[0] if versions else ""


def _write_output(path, key, value):
    line = f"{key}={value}"
    if path:
        with open(path, "a") as fh:
            fh.write(line + "\n")
    else:
        sys.stderr.write(line + "\n")


def _bundle_supported_versions(jar, bundle, package, flag=None):
    text = _java_list_versions(jar, bundle, package, flag)
    return parse_version_lines(text)


def _bundle_patch_names(jar, bundle, package):
    return parse_list_patches(_java_list_patches(jar, bundle, package))


def cmd_discover(args):
    package = os.environ.get("PACKAGE", PACKAGE)
    upstream = os.environ["UPSTREAM"]
    extra = os.environ.get("EXTRA_PATCHES", "")
    jar = os.environ.get("MORPHED_JAR", "morphe-desktop.jar")
    token = os.environ.get("GH_TOKEN")
    github_output = os.environ.get("GITHUB_OUTPUT")

    patcher = os.environ.get("DESKTOP_VER", "")
    morphe_ver = os.environ.get("PATCHES_VER", "")
    doom_ver = os.environ.get("DOOM_VER", "")
    hoo_ver = os.environ.get("HOODLES_VER", "")

    releases = _gh_releases(upstream, token)
    versions = parse_list_versions(
        "\n".join(r["tag_name"] for r in releases if not r.get("draft")))

    doom_rec = _first_version(_java_list_versions(jar, "doom-patches.mpp", package))
    hoo_rec = _first_version(_java_list_versions(jar, "hoodles-patches.mpp", package))
    covered = choose_cover(versions, [doom_rec, hoo_rec], len(versions))
    matrix, channels, _ = build_matrix(covered)

    bundles = ["morphe-patches.mpp", "doom-patches.mpp", "hoodles-patches.mpp"]
    stable = sorted(set(
        v for bundle in bundles
        for v in _bundle_supported_versions(jar, bundle, package)))
    experimental_declared = sorted(set(
        v for bundle in bundles
        for v in _bundle_supported_versions(jar, bundle, package, "-x")))
    _, experimental = classify_versions(stable, experimental_declared)

    recommended = build_recommended(doom_rec, hoo_rec)

    doom_patches = _bundle_patch_names(jar, "doom-patches.mpp", package)
    hoo_patches = _bundle_patch_names(jar, "hoodles-patches.mpp", package)
    expected = build_expected_patches([doom_patches, hoo_patches], extra)

    sources_map = {
        "MorpheApp": {"version": morphe_ver, "list": parse_list_versions(extra)},
        "rushiranpise": {"version": doom_ver, "list": doom_patches},
        "hoo-dles": {"version": hoo_ver, "list": hoo_patches},
    }

    last = _load_last_state(args.state_file)
    skip = 1 if not covered else gate_rebuild(last, sources_map, covered)

    _write_output(github_output, "matrix", matrix)
    _write_output(github_output, "versions", json.dumps(covered, separators=(",", ":")))
    _write_output(github_output, "patcher", patcher)
    _write_output(github_output, "sources", json.dumps(sources_map, separators=(",", ":")))
    _write_output(github_output, "expected_patches",
                  json.dumps(expected, separators=(",", ":")))
    _write_output(github_output, "recommended", recommended)
    _write_output(github_output, "stable_versions",
                  json.dumps(stable, separators=(",", ":")))
    _write_output(github_output, "experimental_versions",
                  json.dumps(experimental, separators=(",", ":")))
    _write_output(github_output, "skip", str(skip))
    print(f"covering {len(covered)} version(s) (skip={skip})")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="proton-vpn")
    sub = p.add_subparsers(dest="cmd", required=True)

    rn = sub.add_parser("render-notes")
    rn.add_argument("--release-dir", default="release")
    rn.add_argument("--applied-dir", default="applied")
    rn.add_argument("--failed-dir", default="failed")
    rn.add_argument("--output", default="release-notes.md")
    rn.add_argument("--keep-out", default="ship/keep.json")
    rn.add_argument("--versions-out", default="ship/versions.json")
    rn.set_defaults(func=cmd_render_notes)

    rs = sub.add_parser("record-state")
    rs.add_argument("--keep-in", default="ship/keep.json")
    rs.add_argument("--state-file", default=STATE_PATH)
    rs.set_defaults(func=cmd_record_state)

    so = sub.add_parser("select-options")
    so.add_argument("--options", default="options.json")
    so.add_argument("--package", default=PACKAGE)
    so.add_argument("-p", "--patch-bundle", action="append", default=None)
    so.add_argument("--expected", required=True)
    so.add_argument("--jar", default="morphe-desktop.jar")
    so.set_defaults(func=cmd_select_options)

    wpl = sub.add_parser("write-patch-lists")
    wpl.add_argument("--result", default="result.json")
    wpl.add_argument("--applied-out", default="applied.txt")
    wpl.add_argument("--failed-out", default="failed.txt")
    wpl.set_defaults(func=cmd_write_patch_lists)

    dc = sub.add_parser("discover")
    dc.add_argument("--state-file", default=STATE_PATH)
    dc.set_defaults(func=cmd_discover)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
