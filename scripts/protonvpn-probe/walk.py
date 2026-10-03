#!/usr/bin/env python3
"""Walk ProtonVPN releases newest first and record what each version can patch.

Stops at the first version where every expected patch actually applied, which is
the newest version safe to ship with the full patch set.

Runs from a work directory holding the patch bundles, the patcher jar, the
generated options.json and the keystore. Reads PACKAGE, UPSTREAM, EXPECTED_PATCHES
and MAX_VERSIONS from the environment.
"""
import json
import os
import subprocess
import sys

PKG = os.environ["PACKAGE"]
UPSTREAM = os.environ["UPSTREAM"]
MPPS = ["patches-morpheapp.mpp", "patches-rushiranpise.mpp",
        "patches-hoodles.mpp"]
JAR = "morphe-desktop.jar"
OPTIONS = "options.json"
KEYSTORE = "morphe.keystore"
EXPECTED = [p.strip() for p in os.environ["EXPECTED_PATCHES"].split("\n") if p.strip()]
MAXV = int(os.environ.get("MAX_VERSIONS", "8"))
# Results are only reusable while the patch bundles are unchanged. The workflow keys
# the result cache on a hash of the bundles, so a stale result is never restored and
# this only short-circuits work that would genuinely repeat.
REUSE = os.environ.get("REUSE_RESULTS") == "1"


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def api(path):
    """GET a GitHub API path. Fails loudly: a silently empty list here would look
    like "this project has no releases" rather than "the call was refused"."""
    r = sh(["gh", "api", path])
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        print(f"    api failed for {path}: {msg[-1] if msg else 'no output'}",
              flush=True)
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        print(f"    api returned unparseable JSON for {path}: {exc}", flush=True)
        return None


def release_tags():
    out = []
    releases = api(f"repos/{UPSTREAM}/releases?per_page=40")
    if releases is None:
        sys.exit(f"could not list releases for {UPSTREAM}")
    for rel in releases:
        if not rel.get("draft"):
            out.append(rel["tag_name"])
    if not out:
        sys.exit(f"{UPSTREAM} returned no published releases")
    return out[:MAXV]


def asset(tag):
    """The direct-release APK for a tag, with the size the API reports."""
    rel = api(f"repos/{UPSTREAM}/releases/tags/{tag}")
    for a in (rel or {}).get("assets", []):
        if "vanilla-direct" in a["name"] and a["name"].endswith(".apk"):
            return a["browser_download_url"], a["size"]
    return None, None


def fetch(tag, url, want):
    """Download once and verify against the reported size.

    A truncated download is the reason this check exists: the patcher accepts a
    partial APK and reports zero applied and zero failed, which is indistinguishable
    from "this version is not supported" and would silently drop the version.
    """
    path = f"apks/protonvpn-{tag}.apk"
    os.makedirs("apks", exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) == want:
        return path, True
    print(f"    downloading {tag} ({want} bytes)", flush=True)
    sh(["curl", "-fsSL", "-o", path, url])
    got = os.path.getsize(path) if os.path.exists(path) else 0
    if got != want:
        print(f"    size mismatch: got {got}, expected {want}", flush=True)
        if os.path.exists(path):
            os.remove(path)
        return None, False
    return path, False


def patch(tag):
    out, res = f"out-{tag}.apk", f"result-{tag}.json"

    # A cached result for these exact bundles already answers the only question this
    # step asks, so neither the 60MB original nor a patch run is needed again.
    if REUSE and os.path.exists(res):
        data = json.load(open(res))
        return {
            "version": tag,
            "applied": [p["name"] for p in data.get("appliedPatches") or []],
            "failed": [p["patch"]["name"] for p in data.get("failedPatches") or []
                       if p.get("patch")],
            "built": os.path.exists(out),
            "cached": True,
        }

    if os.path.exists(out) and os.path.exists(res):
        src = f"apks/protonvpn-{tag}.apk"
    else:
        url, size = asset(tag)
        if not url:
            return None
        src, _ = fetch(tag, url, size)
        if not src:
            return None
    if not (os.path.exists(out) and os.path.exists(res)):
        cmd = ["java", "-jar", JAR, "patch"]
        for m in MPPS:
            # --force is what makes an undeclared version usable: it skips the APK
            # version compatibility check, so every patch is attempted and one that
            # cannot match its fingerprint reports as a failure instead of vanishing.
            cmd.append(f"--patches={m}")
        cmd += [f"--options-file={OPTIONS}", "--striplibs=arm64-v8a",
                f"--out={out}", f"-r={res}", f"--keystore={KEYSTORE}",
                "--force", "--continue-on-error", src]
        print("    patching, this takes a minute", flush=True)
        sh(cmd)
    if not os.path.exists(res):
        return None
    data = json.load(open(res))
    return {
        "version": tag,
        "applied": [p["name"] for p in data.get("appliedPatches") or []],
        # The name sits under a nested patch field; the top level is null.
        "failed": [p["patch"]["name"] for p in data.get("failedPatches") or []
                   if p.get("patch")],
        "built": os.path.exists(out),
        "cached": False,
    }


def main():
    tags = release_tags()
    print(f"walking {len(tags)} release(s), newest first: {', '.join(tags)}\n", flush=True)
    records, anchor = [], None
    for tag in tags:
        print(f"  trying {tag}...", flush=True)
        rec = patch(tag)
        if rec is None:
            print(f"  {tag}: skipped, no usable APK\n", flush=True)
            continue
        records.append(rec)
        how = "cached result" if rec.get("cached") else "patched"
        print(f"  {tag} ({how}): {len(rec['applied'])} applied, "
              f"{len(rec['failed'])} failed, built={rec['built']}", flush=True)
        for n in rec["applied"]:
            print(f"      ok    {n}", flush=True)
        for n in rec["failed"]:
            print(f"      FAIL  {n}", flush=True)
        # An empty failed list is not success. A version the patch set does not target
        # yields zero applied AND zero failed, because the patches are filtered as
        # inapplicable rather than attempted. Require every expected patch instead.
        missing = [p for p in EXPECTED if p not in rec["applied"]]
        if not missing:
            anchor = tag
            print("  -> every expected patch applied, stopping here\n", flush=True)
            break
        print(f"  -> incomplete, missing: {', '.join(missing)}\n", flush=True)

    if not records:
        sys.exit("no version could be patched")

    payload = {"anchor": anchor, "package": PKG, "versions": records}
    with open("probe-result.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"anchor: {anchor}")
    print(f"versions recorded: {', '.join(r['version'] for r in records)}")


if __name__ == "__main__":
    main()