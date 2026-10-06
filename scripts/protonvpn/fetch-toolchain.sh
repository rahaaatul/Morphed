#!/usr/bin/env bash
# Download the Morphe patch bundles + patcher jar into the current directory.
#
# Newest release carrying the wanted asset is chosen, prereleases preferred, which is
# what the discover job then reports as the toolchain version. ProtonVPN only ships
# universal APKs, so architecture never matters here.
#
# Env: GH_TOKEN, DOOM_OWNER_REPO, HOODLES_OWNER_REPO.
set -euo pipefail

newest_asset() {
  local repo=$1 ext=$2
  gh api "repos/${repo}/releases?per_page=30" | jq -r --arg ext "$ext" '
    ([.[] | select(.draft == false)] | sort_by(.published_at) | reverse) as $all
    | ([$all[] | select(.prerelease == true)] + [$all[] | select(.prerelease == false)])
    | .[].assets[]? | select(.name | endswith($ext)) | .browser_download_url' | head -1
}

CURL=(curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused --retry-max-time 300)

"${CURL[@]}" -o morphe-patches.mpp         "$(newest_asset MorpheApp/morphe-patches .mpp)"
"${CURL[@]}" -o doom-patches.mpp     "$(newest_asset "$DOOM_OWNER_REPO" .mpp)"
"${CURL[@]}" -o hoodles-patches.mpp  "$(newest_asset "$HOODLES_OWNER_REPO" .mpp)"
"${CURL[@]}" -o morphe-desktop.jar   "$(newest_asset MorpheApp/morphe-desktop .jar)"

for f in morphe-patches.mpp doom-patches.mpp hoodles-patches.mpp morphe-desktop.jar; do
  # curl exits non-zero on a truncated transfer, but an empty success looks identical
  # to a real download until the patcher refuses to load the bundle.
  [[ -s "$f" ]] || { echo "::error::$f missing or empty" >&2; exit 1; }
done
ls -la *.mpp morphe-desktop.jar
