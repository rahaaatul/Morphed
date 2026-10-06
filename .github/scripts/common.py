#!/usr/bin/env python3
"""Shared MorpheApp toolchain fetcher.

Downloads the latest Morphe patches bundle and patcher jar into the current
directory, preferring prereleases. Used by both the YouTube and Proton VPN
pipelines so the download logic lives in one place.

Env: GH_TOKEN (GitHub token for API auth and release downloads).
"""

import os
import subprocess
import sys


def _gh(args: list[str], token: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, GH_TOKEN=token or os.environ.get("GH_TOKEN", ""))
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, env=env
    )


def newest_tag(repo: str, token: str | None = None) -> str:
    """Return the newest non-draft release tag for repo, prereleases preferred."""
    r = _gh(
        [
            "api", f"repos/{repo}/releases",
            "--jq",
            "[.[] | select(.draft == false)] "
            "| sort_by(.published_at) | reverse | .[0].tag_name",
        ],
        token,
    )
    if r.returncode != 0:
        sys.stderr.write(f"::error::gh api {repo} failed: {r.stderr}\n")
        return ""
    return r.stdout.strip()


def download_asset(
    repo: str,
    tag: str,
    pattern: str,
    dest: str = ".",
    token: str | None = None,
) -> str:
    """Download the release asset matching pattern into dest.

    Raises RuntimeError naming repo, tag, and expected pattern if the asset
    is missing, so the failure is diagnosable without re-running gh.
    """
    r = _gh(
        [
            "release", "download", tag,
            "--repo", repo,
            "--pattern", pattern,
            "--dir", dest,
        ],
        token,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"download failed for {repo}@{tag}/{pattern}: {r.stderr}"
        )
    return pattern


def download_morphe_patches(
    repo: str,
    dest: str = ".",
    token: str | None = None,
) -> str:
    """Download the latest patches-*.mpp bundle from repo into dest.

    repo is "owner/repo" (e.g. "MorpheApp/morphe-patches"). Resolves the
    newest non-draft tag, then downloads the matching .mpp asset. Returns the
    version string with a leading "v" stripped.
    """
    tag = newest_tag(repo, token)
    if not tag:
        raise RuntimeError(f"no patches release found for {repo}")
    ver = tag.removeprefix("v")
    pattern = f"patches-{ver}.mpp"
    download_asset(repo, tag, pattern, dest, token)
    return ver


def download_morphe_desktop(
    repo: str = "MorpheApp/morphe-desktop",
    dest: str = ".",
    token: str | None = None,
) -> str:
    """Download the latest morphe-desktop-*-all.jar from repo into dest.

    Inspects the release's assets and selects the first matching *-all.jar
    rather than guessing the filename from the tag, so a release whose tag and
    asset version disagree still resolves correctly. Returns the version
    string with a leading "v" stripped.
    """
    tag = newest_tag(repo, token)
    if not tag:
        raise RuntimeError(f"no desktop release found for {repo}")

    r = _gh(
        [
            "api", f"repos/{repo}/releases/tags/{tag}",
            "--jq", "[.assets[].name | select(endswith(\"-all.jar\"))][0]",
        ],
        token,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"asset lookup failed for {repo}@{tag}: {r.stderr}"
        )
    asset = r.stdout.strip()
    if not asset:
        raise RuntimeError(
            f"no -all.jar asset found on {repo}@{tag}"
        )
    download_asset(repo, tag, asset, dest, token)
    return asset.removeprefix("morphe-desktop-").removesuffix("-all.jar")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--patches-repo", default="MorpheApp/morphe-patches")
    p.add_argument("--desktop-repo", default="MorpheApp/morphe-desktop")
    p.add_argument("--token", default=os.environ.get("GH_TOKEN", ""))
    p.add_argument("--dest", default=".")
    args = p.parse_args()
    patches_ver = download_morphe_patches(args.patches_repo, args.dest, args.token)
    desktop_ver = download_morphe_desktop(args.desktop_repo, args.dest, args.token)
    print(f"patches_ver={patches_ver}")
    print(f"desktop_ver={desktop_ver}")