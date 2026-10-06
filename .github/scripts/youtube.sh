#!/usr/bin/env bash
# .github/scripts/youtube.sh — shell helpers for youtube.yml
set -euo pipefail

: "${APP_SLUG:?}" "${ARCH:?}" "${PACKAGE:?}" "${ORG:?}" "${REPO:?}" "${DOWNLOAD_TYPE:=apk}"

fetch_toolchain() {
  local order releases patches_url desktop_url
  order='([.[] | select(.draft == false)]
           | sort_by(.published_at) | reverse) as $all
           | ([$all[] | select(.prerelease == true)] + [$all[] | select(.prerelease == false)])'

  releases() {
    curl -fsSL "https://api.github.com/repos/$1/releases?per_page=30"
  }

  patches_url=$(releases "$MORPHE_OWNER/$MORPHE_PATCHES_REPO" \
    | jq -r "$order"' | .[].assets[]? | select(.name | endswith(".mpp")) | .browser_download_url' | head -1)

  desktop_url=$(releases "$MORPHE_OWNER/$MORPHE_DESKTOP_REPO" \
    | jq -r "$order"' | .[].assets[]? | select(.name | endswith(".jar")) | .browser_download_url' | head -1)

  [[ -n "$patches_url" && "$patches_url" != "null" ]] || { echo "No .mpp asset found"; exit 1; }
  [[ -n "$desktop_url" && "$desktop_url" != "null" ]] || { echo "No .jar asset found"; exit 1; }

  curl -fsSL -o patches.mpp "$patches_url"
  curl -fsSL -o morphe-desktop.jar "$desktop_url"

  echo "patches_ver=$(basename "$patches_url" .mpp | sed 's/^patches-//')" >> "$GITHUB_OUTPUT"
  echo "desktop_ver=$(basename "$desktop_url" .jar | sed 's/^morphe-desktop-//;s/-all$//')" >> "$GITHUB_OUTPUT"

  echo "patches: $(basename "$patches_url")"
  echo "patcher: $(basename "$desktop_url")"
  ls -la patches.mpp morphe-desktop.jar
}

list_versions() {
  java -jar morphe-desktop.jar list-versions -x -f "$PACKAGE" --patches patches.mpp | tee versions-raw.txt
  java -jar morphe-desktop.jar list-versions    -f "$PACKAGE" --patches patches.mpp | tee versions-stable.txt
  java -jar morphe-desktop.jar list-patches -f "$PACKAGE" --patches patches.mpp \
    | grep -E '^[+-]' | sed 's/^[+-] //' > patch-names.txt
}

download_apk() {
  local cache_key age src apks dest
  cache_key="${ORG}-${REPO}-${APP_SLUG}-${1}-${DOWNLOAD_TYPE}"

  if [[ -s "$CACHE_DIR/$cache_key" ]] \
     && [[ $(( $(date +%s) - $(stat -c%Y "$CACHE_DIR/$cache_key") )) -lt 2592000 ]]; then
    age=$(( ($(date +%s) - $(stat -c%Y "$CACHE_DIR/$cache_key")) / 86400 ))
    echo "Reusing cached original (${age}d old)"
    cp "$CACHE_DIR/$cache_key" ./download/cached-original
  fi

  if [[ ! -s ./download/cached-original ]]; then
    bun node_modules/apkmirror-downloader/dist/cli.js download \
      "$ORG" "$REPO" \
      --version "${1}" \
      --outdir ./download \
      || echo "apkmd exited non-zero"
  fi

  shopt -s nullglob
  apks=(./download/*.apk)
  shopt -u nullglob

  if [[ ${#apks[@]} -eq 0 && -s ./download/cached-original ]]; then
    apks=(./download/cached-original)
  fi

  if [[ ${#apks[@]} -eq 0 ]]; then
    echo "::error::No APK downloaded for ${1}"
    exit 1
  fi

  src="${apks[0]}"
  cp "$src" "$CACHE_DIR/$cache_key"
  dest="./download/${APP_SLUG}-${1}-${ARCH}.apk"
  mv "$src" "$dest"
  echo "name=$dest" >> "$GITHUB_OUTPUT"
  echo "size=$(du -h "$dest" | cut -f1)" >> "$GITHUB_OUTPUT"
  ls -la "$dest"
}

patch_apk() {
  local in_apk out_apk args line
  in_apk="./download/${APP_SLUG}-${1}-${ARCH}.apk"
  out_apk="./release/${APP_SLUG}-${1}-${ARCH}.apk"

  args=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -n "$line" ]] || continue
    args+=(-e "${line%%|*}")
  done < src/patches/${APP_SLUG}-morphe/include-patches

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -n "$line" ]] || continue
    args+=(-d "$line")
  done < src/patches/${APP_SLUG}-morphe/exclude-patches

  echo "patch selection: ${#args[@]} flags"
  java -jar morphe-desktop.jar patch \
    -p patches.mpp \
    "${args[@]}" \
    -e "Change installer source" \
    -e "Disable Play Store updates" \
    --options-file ./options.json \
    --striplibs="$ARCH" \
    --out="$out_apk" \
    -r ./result.json \
    --keystore=./src/keystore/morphe.keystore \
    --force \
    --continue-on-error \
    "$in_apk"

  if [[ ! -s "$out_apk" ]]; then
    echo "::error::Patch produced no output for ${1}"
    exit 1
  fi

  jq -r '.appliedPatches[].name' result.json > "applied-${1}.txt"
  jq -r '.failedPatches[]?.patch.name // empty' result.json > "failed-${1}.txt"
  echo "applied patches: $(grep -c . "applied-${1}.txt" || true)"
  echo "failed patches: $(grep -c . "failed-${1}.txt" || true)"

  ls -la "$out_apk"
}

prune_cache() {
  shopt -s nullglob
  local f v keep
  for f in apk-cache/*; do
    keep=0
    while read -r v; do
      [[ "$f" == *"-${v}-"* ]] && { keep=1; break; }
    done < <(jq -r '.[]' <<< "$VERSIONS")
    if (( keep )); then echo "keeping $(basename "$f")"
    else echo "dropping $(basename "$f")"; rm -f "$f"; fi
  done
  shopt -u nullglob
}

reuse_published() {
  local v name
  while read -r v; do
    [[ -n "$v" ]] || continue
    name="${APP_SLUG}-${v}-${ARCH}.apk"
    if gh release download "$RELEASE_TAG" --repo "$REPO_FULL" \
         --pattern "$name" --dir release --clobber 2>/dev/null; then
      echo "  reused $name"
    else
      echo "::warning::Could not reuse $name, it will be dropped from the release"
    fi
  done < <(jq -r '.[]' <<< "$REUSED")
}

record_state() {
  local versions_json applied_json failed_json
  versions_json=$(printf '%s\n' "${versions[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')
  applied_json=$(jq -Rsc 'split("\n") | map(select(length > 0))' < applied-final.txt)
  failed_json='[]'
  if [[ -s failed-final.txt ]]; then
    failed_json=$(cut -f2 failed-final.txt | sort -u \
      | jq -Rsc 'split("\n") | map(select(length > 0))')
  fi

  jq --arg o "$MORPHE_OWNER" --arg r "$MORPHE_PATCHES_REPO" \
     --arg t "$PATCHES_VER" --arg app "$APP_SLUG" \
     --arg org "$ORG" --arg repo "$REPO" \
     --arg pkg "$PACKAGE" --arg desktop "$DESKTOP_VER" \
     --argjson versions "$versions_json" \
     --argjson applied "$applied_json" \
     --argjson failed "$failed_json" \
     '.source[$o][$r][$t][$app] = {
       org: $org,
       repo: $repo,
       package: $pkg,
       desktop: $desktop,
       patches_applied: $applied,
       patches_failed: $failed,
       versions: $versions
     }' "$STATE_FILE" > "$STATE_FILE.tmp"
  mv "$STATE_FILE.tmp" "$STATE_FILE"
  jq . "$STATE_FILE" > /dev/null

  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.com"
  git add "$STATE_FILE"

  if git diff --cached --quiet; then
    echo "$STATE_FILE already current"
    exit 0
  fi

  git commit -q -m "Record ${APP_SLUG} build from Morphe patches v${PATCHES_VER}"
  if git push -q origin HEAD:main; then
    echo "Committed $STATE_FILE"
    exit 0
  fi

  echo "Push conflicted, retrying on fresh main"
  sleep 5
  git fetch origin main
  git checkout -q -B main origin/main
  git add "$STATE_FILE"
  git commit -q -m "Record ${APP_SLUG} build from Morphe patches v${PATCHES_VER}"
  git push -q origin HEAD:main
}

cleanup_artifacts() {
  local ids count
  ids=$(gh api "repos/${REPO_FULL}/actions/runs/${GITHUB_RUN_ID}/artifacts" \
    --paginate --jq '.artifacts[].id')

  if [[ -z "$ids" ]]; then
    echo "No artifacts to remove"
    exit 0
  fi

  count=0
  while read -r id; do
    [[ -n "$id" ]] || continue
    if gh api "repos/${REPO_FULL}/actions/artifacts/${id}" --method DELETE; then
      count=$((count + 1))
    else
      echo "::warning::Could not delete artifact ${id}"
    fi
  done <<< "$ids"

  echo "Removed ${count} artifact(s) from this run"
}

"$@"