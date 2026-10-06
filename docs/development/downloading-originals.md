# Downloading originals

How to fetch an app's unmodified APK, and the conventions that keep the fetch honest.

Nothing here names a specific app. The point is the method and the rules, so they hold
for an app you are adding today and for one added next year.

## Pick a source, in this order

Work down this list and stop at the first one that works.

**1. The developer's own releases.** Check the app's GitHub releases for a build
artifact. This is the best option when it exists: no third party in the path, a stable
asset name, a size to verify against, and no rate limiting.

**2. A package mirror.** Only when the developer does not publish artifacts. Slower,
rate-limited, and its metadata is generated rather than declared, which is where most
of the friction in this document comes from.

**3. Anywhere else.** Treat as a last resort and verify the artifact carefully.

## Source 1: developer releases

Resolve the asset through the API rather than guessing a URL, and record the expected
size at the same time so you can verify what you got:

```bash
read -r url size < <(gh api \
  "repos/${UPSTREAM}/releases/tags/$VERSION" \
  | jq -r '[.assets[] | select(.name == "app-release.apk")][0]
           | "\(.browser_download_url) \(.size)"')

# Guard the resolution before spending bandwidth on a URL that may be empty or null.
[[ "$url" == "http"* && "$size" =~ ^[0-9]+$ ]] \
  || { echo "::error::could not resolve the release artifact"; exit 1; }

curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused --retry-max-time 300 \
  -o "${CACHE_DIR}/original.apk" "$url"

got=$(stat -c%s "${CACHE_DIR}/original.apk")
if [[ "$got" != "$size" ]]; then
  echo "::error::truncated download: got $got, expected $size"
  exit 1
fi
```

**The size check is the single most important line in this document.**

A truncated download is not detected by the downloader, and it is not detected by the
patcher either — the patcher sees a malformed archive, produces an empty result, and
that empty result is indistinguishable from "this version is not supported." The
version then disappears from the release with no error anywhere in the run.

Comparing against the size the API reported costs one `stat` and turns a silent
under-report into a failed step.

### Pick the asset by exact match

Prefer `==` over `contains` or a regex. Asset names change — vendors add suffixes,
insert architecture names, and ship several near-identical files per release. A
substring match will eventually select a debug build, a signed-and-unsigned pair, or a
per-architecture variant when you wanted the universal one.

When no exact match exists, inspect the asset list for the release before choosing, and
leave a comment naming the pattern you matched and why it is the right one.

## Source 2: a package mirror

### Setup

```yaml
- name: Set up Bun
  uses: oven-sh/setup-bun@v2

- name: Install the downloader
  run: |
    set -euo pipefail
    bun add apkmirror-downloader
    echo "installed version:"
    grep -o 'apkmirror-downloader@[0-9.]*' bun.lock | head -1
```

Install it per run and commit the lockfile, so a run is reproducible without vendoring
the dependency.

### Invoke through the runtime explicitly

```bash
bun node_modules/apkmirror-downloader/dist/cli.js download \
  "$ORG" "$REPO" \
  --version "$VERSION" \
  --outdir ./download
```

The published entry point has no shebang, so calling the binary in `node_modules/.bin`
directly does not work. Go through the runtime and the full path.

### A downloader that exits 0 on failure

Mirror tooling commonly returns success even when nothing was downloaded. If yours does,
the exit status is not a usable check and the presence of a file is:

```bash
shopt -s nullglob
files=(./download/*.apk)
shopt -u nullglob

if [[ ${#files[@]} -eq 0 && -s ./download/original ]]; then
  files=(./download/original)
fi

if [[ ${#files[@]} -eq 0 ]]; then
  echo "::error::No artifact downloaded for $VERSION"
  exit 1
fi
```

**Verify this before you rely on it** for any downloader you have not used before: fetch
a version that cannot exist and check the exit code. If it returns 0, every check in
this section is mandatory rather than advisory.

Never let a missing file fall through to the patcher.

### Filters are not portable

The mirror's filter flags (`-a` architecture, `-d` DPI, `-t` type) interact with how
that particular app's entries happen to be laid out, and the layout is not standardised.
**There is no correct set of flags. There is only what works for one app, discovered by
trying.**

Work out the flags for each new app in this order:

**1. Fetch with no filters at all.** See what you get. Many apps have a universal build
that already contains every architecture and DPI you would otherwise filter for, and
adding a filter is what breaks it.

**2. Decide what you actually need**, then check whether the unfiltered artifact
satisfies it. Inspect the archive rather than assuming:

```bash
unzip -l original.apk | head -40          # what is actually inside
```

Look for `lib/<abi>/` directories to see which architectures are bundled, and check
`AndroidManifest.xml` for `minSdkVersion`.

**3. Only then add a filter**, one at a time, re-fetching after each change.

**4. Record the outcome as a comment** explaining why those flags and not others. The
next person will otherwise assume the flags are arbitrary and "helpfully" remove one.

### Cases that break filters

Three failure modes worth recognising, all of which look like "the app is not on the
mirror":

- **Interleaved row types.** When an app's entries mix bundle and standalone rows, a
  parser that pairs architecture and DPI cells positionally can misalign them against
  the wrong row. Filtering on either then rejects *every* variant. Symptom: an
  unfiltered fetch works, adding any filter matches nothing.
- **Strict equality against a range.** Many bundles publish a DPI *range* rather than a
  single value, and a tool that defaults to comparing one exact value will reject all
  of them. Symptom: `-d nodpi` matches nothing while the build is plainly available.
- **Type defaults that no longer match.** When a publisher switches from standalone APKs
  to bundles, a tool that defaults to standalone silently matches nothing. Symptom: it
  worked for older versions and fails for new ones.

### Fall back rather than fail

When a set of filters cannot be pinned down, try candidates in order and keep the first
that produces a file. Each entry is a fallback, not an alternative:

```bash
fetch() {
  bun node_modules/apkmirror-downloader/dist/cli.js download \
    "$ORG" "$REPO" \
    --version "$VERSION" \
    "$@" \
    --outdir ./download
}

for dpi in 480-640dpi nodpi any; do
  echo "Trying -d $dpi"
  if fetch -t bundle -a "$ARCH" -d "$dpi"; then
    src=$(got)
    if [[ -n "$src" ]]; then
      echo "Resolved with -d $dpi -> $(basename "$src")"
      break
    fi
  fi
  rm -f ./download/*
done
```

Order them by preference. Add to the end when a new release stops resolving; do not
replace the list, because the older entries are what keep older versions building.

### Extension varies by type

Bundles are not `.apk`, so a `*.apk` glob misses them:

```bash
find ./download -maxdepth 1 -type f \
  \( -name '*.apk' -o -name '*.apkm' -o -name '*.apks' \) | head -1
```

Name the result after the type you actually downloaded. A bundle saved with an `.apk`
extension will confuse every later step that inspects the file.

## Cache the original, never the patched build

Originals are large, slow to fetch, and stable. Cache them; do not refetch.

The cache key must cover everything that changes the bytes: the coordinates, the
filters, and the version. A key missing the filters can serve a download made under
different assumptions, which is the same class of bug as an unverified download:

```bash
cache_key="${ORG}-${REPO}-${APP_SLUG}-${VERSION}-${DOWNLOAD_TYPE}${DPI_ARG:+-$DPI_ARG}"

if [[ -f "$CACHE_DIR/$cache_key" ]] \
   && [[ -s "$CACHE_DIR/$cache_key" ]] \
   && [[ $(( $(date +%s) - $(stat -c%Y "$CACHE_DIR/$cache_key") )) -lt 2592000 ]]; then
  echo "Using cached original (age $(( ( $(date +%s) - $(stat -c%Y "$CACHE_DIR/$cache_key") ) / 86400 ))d)"
  cp "$CACHE_DIR/$cache_key" ./download/original
fi
```

Rules:

- **Bound reuse by age.** Thirty days (`2592000` seconds) is a reasonable default: long
  enough to make repeat runs cheap, short enough that a republished artifact is picked
  up.
- **Check `-s`, not just `-f`.** An interrupted run can leave a zero-byte file with a
  fresh timestamp. Both the age check and `cp` will happily accept it. The `-s` test is
  what prevents a partial file from becoming permanent.
- **Cache only completed downloads.** Copy the file after the verification, never
  before, so a partial file is never stored.
- **Clear the directory between attempts.** `rm -f ./download/*` before retrying with
  different filters, or a failed attempt's file gets picked up as the result.
- **Prune what the current run will not use.** When support for old targets is dropped,
  their cached originals are dead weight. Deleting them during the run keeps the cache
  from growing without bound.

**Never cache a patched build.** The release is the artifact. A cached patched build is
indistinguishable from a fresh one and will be published as though it were current.

## Retry settings worth copying

```bash
curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused --retry-max-time 300
```

- `--retry` plus `--retry-delay` handle a dropped connection or a rate-limited API.
- `--retry-connrefused` is separate from the default because a refused connection is
  normally treated as permanent.
- `--retry-max-time` caps the *whole* transfer. Without it, a server that accepts the
  connection and then goes silent holds the job until its job timeout, because a stalled
  transfer produces neither an error nor progress.
- **Not `--retry-all-errors`.** A 404, a forbidden token, or a malformed URL will fail
  identically on every attempt. Retrying those only multiplies the log and buries the
  real cause.

Count and time limits are independent; whichever is reached first stops the retrying.

## Naming

Rename to a predictable path before patching, and hand it to the next step explicitly
rather than letting it guess:

```bash
dest="./download/${APP_SLUG}-${VERSION}-${ARCH}.apk"
mv "$src" "$dest"
echo "name=$dest" >> "$GITHUB_OUTPUT"
echo "size=$(du -h "$dest" | cut -f1)" >> "$GITHUB_OUTPUT"
ls -la "$dest"
```

`${APP_SLUG}-${version}-${ARCH}.apk` becomes the release asset name and the download
link in the README. Changing that pattern breaks every link already published, so treat
it as user-facing and do not change it casually.

## Signing

Every build is signed with the repository's shared keystore, at the fixed path every
pipeline expects:

```bash
--keystore=src/keystore/<signer>.keystore
```

Sign every variant. A debug-signed artifact among signed ones installs cleanly and then
fails signature checks on upgrade, which users report as "the update is corrupted."

Builds that share a package name overwrite each other on install. If you publish more
than one channel for an app, say so in the release body.

## Credit the downloader

Record the exact version of any non-trivial download tool in the release body's tools
table:

```bash
TOOL_VER=$(curl -fsSL "https://registry.npmjs.org/<package>/latest" | jq -r '.version')
```

A reader who has a problem needs to know which version of the tool produced the file.

## Checklist for a new app

- [ ] Source chosen by the priority list above, and the reason is obvious from the step name.
- [ ] Asset resolved through the API, matched exactly, not by substring.
- [ ] Downloaded size verified against the size the source reported.
- [ ] Exit status verified to be meaningful; if not, a file-presence check is in place.
- [ ] Filters either absent or each one justified by a comment.
- [ ] The archive was inspected to confirm the filters produce what you intended.
- [ ] Cache key covers coordinates, filters, and version.
- [ ] Only completed downloads are cached.
- [ ] Output renamed to the predictable pattern.
- [ ] Signer applied.
- [ ] Tool version recorded in the release body.
