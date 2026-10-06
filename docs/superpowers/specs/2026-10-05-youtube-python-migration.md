# YouTube Python Migration — Specification

> **Metadata**
>
> | Field | Value |
> | --- | --- |
> | Owner | engineering |
> | Status | approved — plan in `docs/superpowers/plans/2026-10-05-youtube-python-migration.md` |
> | Target app | YouTube (`com.google.android.youtube`) |
> | References | `.github/workflows/youtube.yml`, `.github/scripts/proton-vpn.py`, `.github/tags/proton-vpn.json`, `docs/development/script-style.md`, `docs/development/testing.md` |
> | Context7 | Not available in this session; latest API docs verified via webfetch (requests.readthedocs.io, github.com/actions/setup-python) |

## 1. Objective

Migrate all decision logic from `.github/workflows/youtube.yml` (`run:` blocks) into a single subcommand-driven Python helper (`.github/scripts/youtube.py`). The workflow remains the runner: it installs `requests`, caches it, and calls `python3 .github/scripts/youtube.py <subcommand>`. Every external call — HTTP, `java`, `bun`, `gh` — goes through one audited helper each. No logic lives in the YAML anymore.

## 2. Scope

**In scope:**

- `fetch-toolchain`, `select-options`, `discover`, `download-apk`, `patch`, `render-notes`, `record-state`, `cleanup-artifacts` subcommands
- Pure functions: parsing, version classification, matrix building, gate logic, records, patch-list extraction, release-body rendering, state serialization
- New test suite under `tests/youtube_vn/` with golden-file + parity tests
- Per-tag state file `.github/tags/youtube.json` and removal of the `youtube` key from shared `patch_version.json`
- Wiring: updating `.github/workflows/youtube.yml` to call the subcommands
- CI test workflow to actually run the suite

**Out of scope:**

- Arch/dpi APK picking (apkmd interleaves BUNDLE and APK rows for YouTube; the current "no `-a`/`-d`" universal selection stays untouched)
- The `actions/checkout` / `setup-python` action version pinning (use latest)
- Migration of reddit or youtube-music (they keep using `patch_version.json`)
- Per-version source APK caching strategy changes (keep cache/restore as-is)

## 3. Architecture

Three-tier split (mirrors `.github/scripts/proton-vpn.py`):

| Tier | Naming | Responsibility | Testable |
| --- | --- | --- | --- |
| Pure logic | `render_notes`, `build_records`, `pick_anchor`, `discover_core`, `gate_rebuild`, `parse_*`, `classify_*` | Takes Python data, returns Python data. No `os.environ`, no I/O, no subprocess. | Yes — `unittest` with dicts/list/str |
| Command wrappers | `cmd_*` | Reads `argparse` + `os.environ`, calls the pure layer, writes files/$GITHUB_OUTPUT, exits non-zero with `::error::` on failure. | Yes — `tempfile` fixtures + mocked subprocess |
| Private helpers | `_http_*`, `_run_java`, `_run_bun`, `_run_gh` | Isolate every external call so it can be mocked. Nothing else in the module shells out or hits the network. | Yes — `mock.patch.object` |

**Dependency policy:** youtube.py imports only stdlib and `requests`. `requests` is **not** in the runner image — the workflow must `pip install` it and `pip cache` it. `git` is **not** used at all: state commits go through the GitHub contents API (`requests` PUT), which is atomic and race-free. `java`, `bun`, and `gh` are wrapped via `subprocess`.

## 4. Files and responsibilities

```
.github/scripts/youtube.py          # all YouTube orchestration; one file, one app
.github/workflows/youtube.yml       # runner only: env + subcommand calls
.github/workflows/test.yml          # CI runner for the test suite (Task 0)
.github/tags/youtube.json           # per-tag state committed by the pipeline
tests/youtube_vn/                   # <app_slug>_vn test package
  loader.py                         # imports the hyphenated script by path
  *_test.py                         # one file per function group
tests/fixtures/youtube/             # real tool outputs + golden release body
patches_version.json                # modified: youtube key removed (shared with reddit/youtube-music)
```

## 5. Data models

### 5.1 Release body `Record`

```python
Record = {
    "version": str,  # e.g. "21.40.156"
    "size": int,  # APK size in MB (integer, like "163")
    "arch": str,  # "arm64-v8a"
    "applied": [str, ...],
    "failed": [str, ...],
}
```

Read from `release/*.apk` + `applied/applied-<v>.txt` + `failed/failed-<v>.txt`; newest version first (natural version sort, `21.40.156` > `21.39.522`).

### 5.2 State file `.github/tags/youtube.json`

Same schema as `.github/tags/proton-vpn.json`; YouTube is MorpheApp-only:

```json
{
  "owner": "MorpheApp",
  "repo": "morphe-patches",
  "package": "com.google.android.youtube",
  "versions": ["21.40.156", "21.39.522"],
  "patcher": "1.46.0-dev.2",
  "sources": {
    "MorpheApp": {
      "version": "1.46.0-dev.2",
      "list": ["Patch one", "Patch two"],
      "patches": {
        "Patch one": {"applied": ["21.40.156"], "failed": []},
        "Patch two": {"applied": [], "failed": ["21.40.156"]}
      }
    }
  }
}
```

The `list` is the full set of patch names the MorpheApp youtube bundle offers (`list-patches -f com.google.android.youtube --patches morphe-patches.mpp`). `versions` is newest-first.

### 5.3 Discover outputs (GITHUB_OUTPUT)

| Key | Format | Meaning |
| --- | --- | --- |
| `matrix` | `{"include":[{"version":"21.40.156","channel":"Stable"}]}` | New versions to patch |
| `channels` | `{"21.40.156":"Stable","21.39.522":"Beta"}` | Per-version channel |
| `reused` | `["21.39.522"]` | Versions already published this run |
| `nothing_to_build` | `"0"` or `"1"` | `1` ⇒ matrix has only the noop sentinel |
| `versions` | space-joined list of raw versions | Used by the patch job's cache-prune loop |
| `patches_ver` | bare version string (e.g. `1.46.0-dev.2`) | Patcher bundle version |
| `desktop_ver` | bare version string | Patcher jar version |
| `expected_patches` | compact JSON array | All patch names; every matrix row must account for each |

### 5.4 Tool row

```python
Tool = (name: str, repo_url: str, version: str)
```

Order: Morphe Desktop, Morphe Patches, APKMD, MicroG. MicroG version = the release `tag_name`; APKMD version = `registry.npmjs.org/apkmirror-downloader/latest` `.version`.

## 6. HTTP layer (`requests`)

### 6.1 Session

```python
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
import requests


def _http_session():
    retries = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods={"GET", "PUT", "DELETE", "HEAD"}
        | Retry.DEFAULT_ALLOWED_METHODS,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session
```

All calls use `timeout=DEFAULT_TIMEOUT=(10, 60)` (connect, read) unless a download specifies a larger read timeout. `raise_for_status()` is always called after a request.

### 6.2 Authorization

GitHub API calls pass the token explicitly:

```python
class _BearerAuth(requests.auth.AuthBase):
    def __init__(self, token):
        self.token = token

    def __call__(r):
        r.headers["Authorization"] = f"Bearer {self.token}"
        return r


def _auth(token):
    return _BearerAuth(token) if token else None
```

Auth via `GITHUB_TOKEN` env is NOT relied upon; the workflow always passes `GH_TOKEN` explicitly. No `requests` auth header magic, no shell auth.

### 6.3 Endpoints

| Purpose | Method | Details |
| --- | --- | --- |
| Releases list | `GET https://api.github.com/repos/{repo}/releases?per_page=100` | Paginate via `r.links["next"]` until < 100 returned or `Next` missing |
| Release asset URL | `GET https://api.github.com/repos/{repo}/releases/tags/{tag}` | First asset whose `.name` matches the pattern |
| Contents read/write | `GET/PUT https://api.github.com/repos/{owner}/{repo}/contents/{path}` | PUT body `{message, content(base64), sha?}`; `sha` from the preceding GET |
| NPM registry | `GET https://registry.npmjs.org/<pkg>/latest` | Unauthenticated JSON |

### 6.4 Downloads

Stream with `stream=True` + `r.iter_content(8192)`; write to a temp file first, then rename to the final path so a failed download never leaves a partial APK in place. Verify `Content-Length` against the API-declared size when available (mirrors `docs/development/downloading-originals.md`).

### 6.5 Contents PUT retry (`state_put`)

```
for attempt in 1..5:
    sha = GET .sha
    PUT -f message=... -f content=... -f sha={sha}   # sha omitted on first attempt if file missing
    if 200/204: return
    if attempt == 5: stderr ::error::, exit 1
    sleep 2^attempt
```

## 7. Subprocess wrappers

One function each; all log the exact invocation with `shlex.join(cmd)` before running, so the Actions log shows what was actually executed.

```python
def _run_java(args, *, timeout=600, capture_output=True):
    subprocess.run(
        ["java", "-jar", *args],
        check=False,
        timeout=timeout,
        capture_output=capture_output,
        text=True,
    )


def _run_bun(args, *, timeout=1200, capture_output=True):
    subprocess.run(
        ["bun", *args],
        check=False,
        timeout=timeout,
        capture_output=capture_output,
        text=True,
    )


def _run_gh(args, *, token=None, capture_output=True):
    env = dict(os.environ)
    env["GH_TOKEN"] = token or ""
    subprocess.run(
        ["gh", *args],
        check=False,
        timeout=60,
        capture_output=capture_output,
        text=True,
        env=env,
    )
```

- `list-versions`/`list-patches`/`options-create` → `_run_java(capture_output=True)`.
- `patch` → `_run_java(capture_output=False)` so the patcher's own Applied/FAILED lines stay in the step log (mirrors proton's approach).
- apkmd → `_run_bun(check=False)`; the caller verifies the APK exists because apkmd returns 0 even on failure.
- Artifact cleanup → `_run_gh(["gh","api","-X","DELETE",...])`.

## 8. Function catalog

### 8.1 Pure logic

```python
def version_sort_key(v: str) -> tuple[int, ...]
# "21.40.156" -> (21, 40, 156); longest dotted prefix wins

def strip_apk_version(basename: str) -> str | None
# "youtube-21.40.156-arm64-v8a.apk" -> "21.40.156"; None otherwise

def parse_version_lines(text: str) -> list[str]
# Loose regex `\s*(\d+(?:\.\d+)+)` pulls the first dotted number per line

def parse_list_patches(text: str) -> list[str]
# Reads "Name: X\nEnabled: true\n..." output; only enabled patches

def extract_patch_lists(result: dict) -> tuple[list[str], list[str]]
# result["appliedPatches[].name"] and result["failedPatches[].patch.name"]

def build_records(release_dir, applied_dir, failed_dir, arch) -> list[Record]
# Glob release/*.apk; fail loudly (::error::) if an applied/failed txt is missing

def pick_anchor(records: list[Record], expected: list[str]) -> str
# Newest record where set(expected) <= set(applied); "" if none

def classify_versions(stable, experimental) -> tuple[list[str], list[str]]
# stable = versions present without -x; experimental = -x only

def build_matrix(versions, channels) -> tuple[str, str, int]
# '{"include":[...]}', '{"21.40.156":"Stable"}', nothing_to_build (1 iff empty)

def build_expected_patches(bundle_names, forced) -> list[str]
# Deduplicated bundle patch names; forced patches appended only if absent

def reused_versions(done, new) -> list[str]
# versions in `new` that already exist in `done` (from state)

def gate_rebuild(last, sources, covered) -> int
# 0 = rebuild, 1 = skip. YouTube: rebuild iff MorpheApp source version changed
# or the covered version range changed. No last state -> 0 (rebuild).

def render_notes(records, expected, tools, microg_tag) -> str
# Returns the full release body (str), trailing newline.

def build_patch_args(include_path, exclude_path) -> list[str]
# Reads include/exclude, strips trailing CR, skips blanks;
# include entries -> "-e {name before |}", exclude entries -> "-d {full line}"

def youtube_state(records, patcher, sources) -> dict
# Builds the state file record from what shipped

def state_put(repo_full, state_path, content_str, message, token) -> int
# Contents API PUT with sha-conditional retry (Section 6.5)
```

### 8.2 Command wrappers (argparse subcommands, one per workflow step)

```python
def cmd_fetch_toolchain(args) -> int      # --owner --patches-repo --desktop-repo
def cmd_select_options(args) -> int       # --package --options --jar -p <bundle>
def cmd_discover(args) -> int             # --state-file
def cmd_download_apk(args) -> int         # --org --repo --version --outdir --cache-dir --arch
def cmd_patch(args) -> int                # --jar --options --patches --in --out --result
                                        #   --striplibs --keystore --include-patches
                                        #   --exclude-patches --force --continue-on-error
def cmd_render_notes(args) -> int         # --release-dir --applied-dir --failed-dir
                                        #   --output --keep-out --icon-url --icon-width
def cmd_record_state(args) -> int         # --keep-in --state-file --owner --repo
def cmd_cleanup_artifacts(args) -> int    # --run-id
def main(argv=None) -> int
```

Every subcommand prints a one-line `covering N version(s) (reused=M)` or `applied X, failed Y` progress line to stderr and exits 0 on success, non-zero with `::error::` on any failure.

### 8.3 `cmd_fetch_toolchain` contract

Downloads (via `_http_download` with retry + size verification):

- `https://github.com/{owner}/{patches-repo}/releases/latest/download/morphe-patches-{patches_ver}.mpp` → `morphe-patches.mpp`
- `.../morphe-desktop-{desktop_ver}-all.jar` → `morphe-desktop.jar`

Version derivation (parity with the current workflow):

- `patches_ver` = asset basename minus `.mpp`, minus leading `patches-`
- `desktop_ver` = asset basename minus `.jar`, minus leading `morphe-desktop-`, minus trailing `-all`

Emits `PATCHES_VER` and `DESKTOP_VER` to GITHUB_OUTPUT. Fails if either asset is missing.

**Release selection order:** drafts excluded; within all releases, prereleases sorted before stable releases by `published_at` (mirror the current youtube.yml meta ordering).

### 8.4 `cmd_discover` contract

1. Run `java list-versions -x -f PACKAGE --patches morphe-patches.mpp` and plain (parse both with `parse_version_lines`).
2. Filter `EXCLUDE_VERSIONS` (space-separated; `grep -qxF` semantics).
3. Read `.github/tags/youtube.json`; versions built from `patcher` in `last["sources"]["MorpheApp"]["list"]` are skipped.
4. Classify stable vs experimental; build matrix + channels + reused.
5. `build_expected_patches`: run `java list-patches -f PACKAGE --patches morphe-patches.mpp` (enabled names), dedupe, append the two forced patches if absent.
6. `gate_rebuild`; set `nothing_to_build=1` iff covered is empty.
7. Write all discover outputs to GITHUB_OUTPUT.

### 8.5 `cmd_download_apk` contract

1. Build `cache_key = "{org}-{repo}-{APP_SLUG}-{version}-{DOWNLOAD_TYPE}"`; if `apk-cache/<cache_key>` exists and is < 30 days old, `cp` it to `./download/cached-original` and skip the download.
2. Otherwise run apkmd:
   ```
   bun node_modules/apkmirror-downloader/dist/cli.js download {org} {repo} \
     --version {version} --outdir {outdir}
   ```
   (No `-a`/`-d` — YouTube rows interleave BUNDLE and APK.)
3. Find `./download/*.apk`; if none but `cached-original` exists, use it. Fail if none at all.
4. Rename to `{APP_SLUG}-{version}-{ARCH}.apk`; `cp` the completed file to `apk-cache/<cache_key>` (never cache a partial).
5. Write `name=` and `size=` (human-readable) to GITHUB_OUTPUT.

### 8.6 `cmd_patch` contract

1. `build_patch_args(include, exclude)` → arg list.
2. Run:
   ```
   java -jar {jar} patch \
     --options-file={options.json} \
     --striplibs={striplibs} \
     --out={out} -r={result} --keystore={keystore} \
     --force --continue-on-error \
     -e "Change installer source" -e "Disable Play Store updates" \
     <patch args> \
     {in}
   ```
   (capture_output=False — patcher log lines must stay in the step output)
3. Fail if `{out}` is empty.
4. Call `extract_patch_lists` on `result.json` and write `applied-<v>.txt` / `failed-<v>.txt`.

### 8.7 `cmd_render_notes` contract

1. `build_records(release_dir, applied_dir, failed_dir, ARCH)`. Fail if zero APKs produced.
2. `expected = json.loads(os.environ["EXPECTED_PATCHES"])`; fail if empty (no patcher output).
3. Fetch MicroG `tag_name` from `MorpheApp/MicroG-RE` releases (prerelease-preferred); block omitted (with warning) if unavailable.
4. Emit body to `--output`, hand off `ship/keep.json` (json.dumps of records, indent=2 + newline) for record-state.
5. Print body to stdout.

### 8.8 `cmd_record_state` contract

1. Load `ship/keep.json`; if empty, exit 0 ("Nothing shipped").
2. `youtube_state(records, patcher=os.environ["PATCHER"], sources=...)`.
3. `state_put(repo_full, state_path, content, "Record youtube build, patcher {patcher}")` — if content equals the current committed content, skip.
4. Exit 1 with `::error::` on final failure.

### 8.9 `cmd_cleanup_artifacts` contract

```
ids = _run_gh(["gh","api", f"repos/{REPO_FULL}/actions/runs/{run_id}/artifacts",
               "--paginate", "--jq", ".artifacts[].id"])
for id in ids: DELETE /repos/.../actions/artifacts/{id}  (warning on failure)
```

## 9. Workflow changes (youtube.yml)

The three jobs become thin runners. Only these `run:` blocks remain verbatim:

- **discover:** JDK setup; `fetch-toolchain`; `select-options`; `discover`. Upload artifact step stays (toolchain only).
- **patch:** JDK setup; cache restore/save; setup-bun; `download-apk`; `patch`; upload artifacts (APK + applied + failed lists).
- **release:** checkout; download artifacts; `render-notes`; `ncipollo/release-action`; `record-state`; `cleanup-artifacts`.

Global env stays (`REPO_FULL`, `APP_SLUG`, `ARCH`, `PACKAGE`, `RELEASE_TAG`, `MICROG_REPO`, `GH_TOKEN` where needed). `patcher`/`tools` values come from subcommand outputs. The `STATE_FILE` env vars and all inline bash (jq matrix construction, awk Not-applied build, git rebase loop) are removed.

**New `pip install requests` + cache step** in every job that runs a subcommand (checkout, setup-python `cache: pip`, `pip install --cache-dir .pip-cache requests`). Action versions: `actions/checkout@v7`, `actions/setup-python@v7`, `actions/setup-java@v5.2.0` (unchanged), `actions/cache/restore@v6` (unchanged), `actions/cache/save@v6` (unchanged), `actions/upload-artifact@v7.0.1` (unchanged), `actions/download-artifact@v8.0.1` (unchanged), `oven-sh/setup-bun@v2` (unchanged), `ncipollo/release-action@v1.21.0` (unchanged).

## 10. Release body format

YouTube's body = Proton body format with a MicroG `IMPORTANT` callout anchored first. Exact structure (byte-for-byte reproduced by `render_notes`; golden-tested in `render_notes_test.py`):

```
> [!IMPORTANT]
> **[MicroG](https://github.com/MorpheApp/MicroG-RE/releases/tag/<tag>)** is required to use this app
> Install the newest version before following the tip.

> [!TIP]
> Install `<anchor>`. It is the newest version where every patch applied cleanly.

## Downloads

> [!NOTE]
> - `Stable` - Tested and Recommended
> - `Beta` - Works but not recommended

<details>
<summary><b>Click here</b> to see the patches applied</summary>
<br>

<details>
<summary><b>v<version></b> - <code><n applied of expected></code></summary>
<br>

|Status|Patch|
| :---: | :---: |
| 🟢|<patch>|    <!-- n applied == len(expected) -->
| 🔴|<patch>|    <!-- failed -->
| ⚪|<patch>|    <!-- never offered / not reported -->
</details>
... (one per version, newest first)
</details>

| Version | Channel | Arch | Size | Download |
| :-------: | :-------: | :---------: | :-----: | :------------------------: |
| `<version>` | `channel` | `arm64-v8a` | `<n>.0MB` | <a href="..."><img src=".../icons/download.png" width="20" alt="Download <version>"></a> |

## Tools used

| Tool | Version |
| --- | --- |
| [Morphe Desktop](https://github.com/MorpheApp/morphe-desktop) | [`<v>`](https://github.com/MorpheApp/morphe-desktop/releases/tag/<v>) |
| [Morphe Patches](https://github.com/MorpheApp/morphe-patches/) | [`<v>`](...) |
| [APKMD](https://github.com/tanishqmanuja/apkmirror-downloader) | [`<v>`](...) |
| [MicroG](https://github.com/MorpheApp/MicroG-RE) | [`<tag>`](...) |
```

**Dropped:** the "Patches applied" union `<details>`, the awk-built "Not applied" `<details>`, and the old `## Tools` list format. Channel classification: `Stable` if in the non-`-x` list else `Beta`. Per-version cell count `n` = number of `expected` patches whose name is in `record["applied"]`.

## 11. Test plan

Run: `python3 -m unittest discover -s tests -t . -p '*_test.py'` (`docs/development/testing.md:10`).

| File | Covers | Pattern |
| --- | --- | --- |
| `parse_test.py` | `parse_version_lines`, `parse_list_patches` | Real fixture `list-versions.txt`/`list-patches.txt` |
| `patch_lists_test.py` | `extract_patch_lists` | Parity vs `jq -r '.appliedPatches[].name'` over a fixture `result.json` |
| `build_records_test.py` | `build_records`, `strip_apk_version`, `pick_anchor` | Synthetic temp dirs; missing-txt → non-zero exit |
| `fetch_toolchain_test.py` | `fetch_toolchain` | Fixture `releases.json`; parity vs current `basename \| sed`; prerelease-preferred; missing asset → failure |
| `discover_test.py` | `cmd_discover` / `discover_core` | Mocked subprocess; parity vs current bash matrix-construction jq; `EXCLUDE_VERSIONS` |
| `gate_rebuild_test.py` | `gate_rebuild` | Ported from `tests/proton_vn/record_state_test.py::GateRebuildTest`, YouTube semantics |
| `render_notes_test.py` | `render_notes`, `cmd_render_notes` | Golden file `release-notes.md`; structure asserts; empty APK dir → non-zero; microg_tag="" → no block |
| `youtube_cli_test.py` | `cmd_download_apk` (no-APK → failure), `cmd_select_options` (options.json created) | Temp dirs, mocked bun |
| `record_state_test.py` | `youtube_state` | Builds schema, newest-first, per-patch attribution |
| `merge_state_test.py` | `state_put` retry-on-409, final-failure exit, shared-state key removal | Mocked `requests.Session.get/put` |

**Fixtures:** `tests/fixtures/youtube/{releases.json, list-versions.txt, list-versions-experimental.txt, list-patches.txt, result-21.40.156.json, options.json, records.json}` — capture `list-*` and `result-*` from a live toolchain run; `release-notes.md` golden regenerated from the current workflow body, diff-reviewed before commit.

**Patterns from `docs/development/testing.md`:** golden-file test (`:115`), parity test against the implementation being replaced (`:132`), CLI test with failure paths (`:155`), data-shape tests, `tempfile` cleanup, no assertions on an external tool's own behavior.

## 12. Migration of `patch_version.json`

Current key shape (verified live): `.source.MorpheApp.morphe-patches.<tag>.youtube`, shared by `reddit` and `youtube-music`. YouTube stops reading/writing it:

1. `record-state` writes to `.github/tags/youtube.json` instead.
2. Remove the key: `jq --arg tag "$PATCHES_VER" 'del(.source.MorpheApp."morphe-patches"[$tag].youtube)' patch_version.json`.
3. Verify reddit and youtube-music entries are intact; no workflow reads the deleted key.
4. The removed entry is functionally equivalent to `versions=[]` (always rebuild), which YouTube's new state file provides.

## 13. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| `requests` not on runner | Workflow `pip install --cache-dir .pip-cache requests` (verified: `import requests` fails in a bare `python3`) |
| Network flakiness on toolchain/npm/MicroG/apkmd | Session retry adapter (5 attempts, 429/5xx) + `(10, 60)` timeouts everywhere; size verification on downloads |
| `tag_name` ≠ asset basename | Parity test in `fetch_toolchain_test.py` asserting `tag_name == asset-derived version` |
| Truncated APK → empty result → silent drop | Size check vs API `size`; `cmd_download_apk` fails if no APK; `build_records` fails if a txt list is missing |
| apkmd exit 0 regardless of failure | File-existence check after the bun call; never trust the exit code |
| State-commit race with concurrent runs | sha-conditional contents PUT with 5-attempt backoff; idempotent skip if content unchanged |
| Empty expected patch list (bundle offers nothing) | `render_notes` fails if `EXPECTED_PATCHES` is empty |
| `gh` not installed / token invalid | GH_TOKEN passed explicitly; `_run_gh` errors loudly; cleanup is best-effort (warnings, not failures) |

## 14. Decisions log

1. `requests` for all HTTP (workflow installs + caches it). Cost: a pip step per job; benefit: simpler, more testable than `gh api --jq` for the API surface.
2. Subprocess wrappers for `java`, `bun`, `gh` only. `git` is replaced by the contents-PUT (`state_put`) — atomic, no merge-base race, no `git fetch/checkout/push` dance in a retry loop.
3. Per-tag state at `.github/tags/youtube.json` (schema parity with `proton-vpn.json`); `youtube` key removed from `patch_version.json` once `record-state` moves.
4. Release body: Proton format with MicroG `IMPORTANT` callout first; drop the old union/applied-not-applied blocks.
5. Expected patch list from the MorpheApp youtube bundle (`list-patches -f com.google.android.youtube --patches morphe-patches.mpp`); the two forced patches are members of that list.
6. No MicroG block if the release can't be resolved (warning, not failure) — the release should still publish.
7. Latest action versions: `actions/checkout@v7`, `actions/setup-python@v7`. Python `3.12`.
8. YouTube-specific: `cmd_select_options` runs `options-create` only (no `--expected` enabling — YouTube uses `include-patches`/`exclude-patches` for control).
9. `cmd_download_apk` wraps bun and mirrors the current `cached-original` semantics (never cache a partial).
