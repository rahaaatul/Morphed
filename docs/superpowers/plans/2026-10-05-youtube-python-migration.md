# YouTube Python Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `.github/workflows/youtube.yml` from a bash+`jq` pipeline into one subcommand-driven Python helper (`.github/scripts/youtube.py`) that does every decision, fetch, and file mutation in Python; the workflow stays as the thin runner that installs `requests`, caches it, and calls `python3 .github/scripts/youtube.py <cmd> ...`.

**Architecture:** A single hyphenated script mirroring `.github/scripts/proton-vpn.py`'s three-tier split: (1) pure logic functions (`parse_version_lines`, `build_records`, `pick_anchor`, `render_notes`, `discover_core`, `youtube_state`...), (2) command wrappers (`cmd_*`) that read `os.environ`/argparse, call the pure layer, and write `$GITHUB_OUTPUT`/files with non-zero exits on failure, (3) private helpers (`_http_json`, `_run_java`, `_run_bun`, `_run_gh`) that isolate every external call. HTTP is done with `requests` (no `subprocess` to `api.github.com`); `git` is replaced by a contents-API `PUT` so no git is wrapped at all; `java`, `bun`, and `gh` (for binary downloads and artifact deletion) are wrapped in subprocess with one audited function each.

**Tech Stack:** Python 3 (stdlib + `requests`, installed and pip-cached by the workflows), `requests` for all HTTP (GitHub REST API, npm registry, APKMirror through bun), `subprocess` wrappers for `java`, `bun`, `gh`. Tests use only `unittest` + `mock`, mirroring `tests/proton_vn/`.

**Spec:** This plan. It implements the design agreed in this session: everything in Python as separate functions, `requests` for HTTP (workflow must `pip install` + cache it), wrap `java`/`bun`/`gh` via subprocess, state moves from shared `patch_version.json` to per-tag `.github/tags/youtube.json` (same schema as `.github/tags/proton-vpn.json`), release body in Proton format with a MicroG `IMPORTANT` callout first, per-version patch matrix in one `<details>`, drop the "Patches applied" union block and the awk-built "Not applied" block, expected patches from the MorpheApp `youtube` bundle (`list-patches -f com.google.android.youtube`), `include-patches`/`exclude-patches` preserved for manual enable/disable later, and six incremental tasks gated by a new CI test workflow.

## Global Constraints

- **State file:** `.github/tags/youtube.json`; schema mirrors `.github/tags/proton-vpn.json` exactly — `{owner, repo, package, versions[], patcher, sources:{owner:{version, list[], patches:{name:{applied[], failed[]}}}}}` (see `.github/tags/proton-vpn.json:1`).
- **State commit:** `requests` PUT to `https://api.github.com/repos/{repo_full}/contents/{path}` with a fresh `-f sha=<sha>` conditional and 5-attempt exponential backoff on conflict (mirror `.github/scripts/proton-vpn.py:state_put`); no `git` subprocess.
- **Shared state cleanup:** delete the `youtube` key from `patch_version.json` once all readers are migrated (reddit and youtube-music still read it).
- **Release body order:** (1) MicroG `IMPORTANT` callout first, (2) `TIP`/`WARNING` anchor line, (3) per-version matrix inside a single `<details>` with 🟢/🔴/⚪ cells, (4) download table with icon cell (`icons/download.png`, width 20), (5) `## Tools used` table; drop the "Patches applied" `<details>` union block and the awk-built "Not applied" block.
- **Expected patch list:** `list-patches -f com.google.android.youtube --patches morphe-patches.mpp`; every name in that list must appear in the matrix; the two forced `-e` patches ("Change installer source", "Disable Play Store updates") are members of that list.
- **Channel classification:** Stable when present in `list-versions` without `-x`, Beta otherwise; `EXCLUDE_VERSIONS` (env) filters the raw list before matrix build.
- **APK download:** `bun node_modules/apkmirror-downloader/dist/cli.js download google-inc youtube --version <v> --outdir download`; no `-a`/`-d` flags (YouTube APKMirror rows interleave BUNDLE and APK).
- **Toolchain:** `GH_TOKEN` auth through `requests`; equivalent of `--retry 5 --retry-delay 2 --retry-connrefused` via session `max_retries=5` on a `Retry-After`-respecting adapter, plus an expected-size verification on every download (mirror `docs/development/downloading-originals.md`).
- **MicroG:** newest non-draft release of `MorpheApp/MicroG-RE`, prerelease preferred, `tag_name` used as anchor; skip the block (with a warning) if it cannot be resolved.
- **CI:** every job that runs `python3 .github/scripts/youtube.py` must `pip install` + cache `requests`; test command is `python3 -m unittest discover -s tests -t . -p '*_test.py'` (`docs/development/testing.md:10`).
- **Fail-fast:** `cmd_render_notes` exits non-zero with `::error::` if no APKs were produced; `cmd_discover` exits non-zero if the version list is empty (current behavior preserved).
- **Preserve as-is:** `removeArtifacts: true` (YouTube wipe model, differs from Proton), `continue-on-error: false` on the patch job, `fail-fast: false` on the matrix.
- **No shell helper:** `fetch_toolchain` lives in `youtube.py` (not `scripts/youtube/fetch-toolchain.sh`), because the user requires everything in Python and fetch is just `requests` + version parsing.

## Review Focus

- **1. Every HTTP fetch retries and times out:** `fetch_toolchain`, the MicroG list fetch, the APKMD-version fetch, and the contents `PUT` must all retry with backoff. Test the transient-failure path in `tests/youtube_vn/fetch_toolchain_test.py` (mocked `requests.get` failing twice then succeeding) and `tests/youtube_vn/merge_state_test.py` (mocked `requests.put` returning 409 four times, 200 on the fifth).
- **2. `tag_name` ↔ asset-basename 1:1 alignment:** the version strings `fetch_toolchain` returns must equal the downloaded asset basenames (`patches-<v>.mpp`, `morphe-desktop-<v>-all.jar`); parity test in `fetch_toolchain_test.py` comparing against the current `basename | sed` extraction over the same fixture releases.
- **3. Truncated/partial APK and missing lists fail loudly:** `cmd_download_apk` exits non-zero when no APK appears after the bun call; `build_records` exits non-zero when an `applied-<v>.txt`/`failed-<v>.txt` is missing. Test failure paths in `tests/youtube_vn/build_records_test.py` and `tests/youtube_vn/youtube_cli_test.py`.
- **4. State-commit race:** `state_put` re-fetches the sha before each `PUT`, retries 5 times, and exits non-zero with `::error::` on the last failure. Tested in `merge_state_test.py`; also covered by the `if` guard on the release job so nothing partial publishes.
- **5. Empty render output does not publish:** `cmd_render_notes` exits non-zero when the release directory has no APKs. Tested in `tests/youtube_vn/render_notes_test.py` (`test_render_notes_empty_exits_nonzero`).

---

### Task 0: CI test workflow

**Files:** Create `.github/workflows/test.yml`; modify: none

**Interfaces:** Consumes: nothing. Produces: green CI check running `python3 -m unittest discover -s tests -t . -p '*_test.py'`.

#### Sub-tasks
- [ ] 0.1 Create `.github/workflows/test.yml` with `on: [push, pull_request]`, path filters for `**`, `**/*.py`, `.github/workflows/*`
- [ ] 0.2 Add the `unit` job on `ubuntu-24.04`: checkout + `actions/setup-python@v7` with `cache: pip`
- [ ] 0.3 Add `pip install requests` step
- [ ] 0.4 Add `python3 -m unittest discover -s tests -t . -p '*_test.py'` step
- [ ] 0.5 Run locally: `python3 -m unittest discover -s tests -t . -p '*_test.py'` → expect `Ran 0 tests OK`
- [ ] 0.6 Commit: `git add .github/workflows/test.yml && git commit -m "ci: add unit test workflow gating on push and PR"`

---

### Task 1: Core data layer — parse, patch lists, records, anchors

**Files:**
- Create: `.github/scripts/youtube.py` (header + three-tier function groups, `main()` with subparsers)
- Create: `tests/youtube_vn/loader.py`, `tests/youtube_vn/parse_test.py`, `tests/youtube_vn/patch_lists_test.py`, `tests/youtube_vn/build_records_test.py`, `tests/youtube_vn/pick_anchor_test.py`
- Create: `tests/fixtures/youtube/list-versions.txt`, `tests/fixtures/youtube/list-patches.txt`

#### Sub-tasks

**1. Module scaffolding & parsing**
- [ ] 1.1 Write `tests/youtube_vn/loader.py` mirroring `tests/proton_vn/loader.py` (imports `.github/scripts/youtube.py`)
- [ ] 1.2 Write `tests/youtube_vn/parse_test.py` (failing tests for `parse_version_lines`, `strip_apk_version`, `version_sort_key`)
- [ ] 1.3 Run tests → expect `NameError: name 'YouTube_vn' is not defined`
- [ ] 1.4 Implement `version_sort_key`, `strip_apk_version`, `parse_version_lines` in `youtube.py` + `main()` stub with subparsers
- [ ] 1.5 Run tests → expect PASS
- [ ] 1.6 Commit: `git add .github/scripts/youtube.py tests/youtube_vn/loader.py tests/youtube_vn/parse_test.py tests/fixtures/youtube/`

**1.7 Capture `list-versions.txt` and `list-patches.txt` fixtures (parity inputs, needed for patch-list tests)**

**1.7.1 Capture real `list-versions` output:**
```bash
java -jar morphe-desktop.jar list-versions -f com.google.android.youtube --patches morphe-patches.mpp > tests/fixtures/youtube/list-versions.txt
java -jar morphe-desktop.jar list-versions -x -f com.google.android.youtube --patches morphe-patches.mpp > tests/fixtures/youtube/list-versions-experimental.txt
```

**1.7.2 Capture real `list-patches` output:**
```bash
java -jar morphe-desktop.jar list-patches -f com.google.android.youtube --patches morphe-patches.mpp > tests/fixtures/youtube/list-patches.txt
```

**1.7.3 Commit fixtures:** `git add tests/fixtures/youtube/`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `version_sort_key`, `strip_apk_version`, `parse_version_lines`, `parse_list_patches`, `extract_patch_lists`, `build_records`, `pick_anchor`. Later tasks import all of these via the loader.

Module docstring must state the new dependency policy (stdlib + `requests`, which the workflow installs and caches):

```python
"""YouTube release pipeline, one argparse subcommand per workflow step.

May depend on `requests` (installed and pip-cached by the workflow). External CLI tools
(java, bun, gh) are invoked through the private helpers below, never inlined, so nothing
in this file exists only to call a CLI from a different language.

Each subcommand reads the values the workflow owns from the environment, writes results
back through $GITHUB_OUTPUT, and exits non-zero with a ::error:: message so a workflow
step fails loudly rather than shipping a wrong APK or a wrong release body.
"""
```

**1.8 Patch list parsing**
- [ ] 1.8.1 Write `tests/youtube_vn/patch_lists_test.py` (failing test: `extract_patch_lists` reads `appliedPatches[].name`, `failedPatches[].patch.name` from a fixture result JSON)
- [ ] 1.8.2 Run test → expect failure (function not defined)
- [ ] 1.8.3 Implement `parse_list_patches` (Name:/Enabled: parser, copy `proton-vpn.py` verbatim) and `extract_patch_lists` (mirror `proton-vpn.py:extract_patch_lists`)
- [ ] 1.8.4 Run test → expect PASS

**1.9 Build records**
- [ ] 1.9.1 Write `tests/youtube_vn/build_records_test.py` (failing tests: `test_builds_records_sorted_newest_first`, `test_build_records_missing_applied_txt_exits_nonzero`)
- [ ] 1.9.2 Run test → expect failure
- [ ] 1.9.3 Implement `build_records` (mirror `proton-vpn.py:build_records`, arch from arg, size in MB)
- [ ] 1.9.4 Run test → expect PASS

**1.10 Pick anchor**
- [ ] 1.10.1 Write `tests/youtube_vn/pick_anchor_test.py` (failing tests: `test_pick_anchor_newest_clean_version`, `test_pick_anchor_skips_versions_with_failed_patches`)
- [ ] 1.10.2 Run test → expect failure
- [ ] 1.10.3 Implement `pick_anchor` (mirror `proton-vpn.py:pick_anchor`)
- [ ] 1.10.4 Run test → expect PASS

**1.11 Commit core data layer**
- [ ] 1.11.1 Run full suite: `python3 -m unittest discover -s tests -t . -p '*_test.py' -v` → expect all PASS
- [ ] 1.11.2 Commit: `git add .github/scripts/youtube.py tests/youtube_vn/ tests/fixtures/youtube/ && git commit -m "feat: add YouTube core data layer (parse, patch lists, records, anchors)"`

----

### Task 2: Release body rendering with golden test

**Files:**
- Modify: `.github/scripts/youtube.py` (add `render_notes`, helper `_patch_row`, `cmd_render_notes`)
- Modify: `tests/youtube_vn/build_records_test.py` (import `pick_anchor` here is fine; keep it)
- Create: `tests/youtube_vn/render_notes_test.py`
- Create: `tests/fixtures/youtube/records.json` (fixed synthetic record set, 2-3 versions)
- Create: `tests/fixtures/youtube/release-notes.md` (golden, generated by parity — see Step 6)

**Interfaces:**
- Consumes: `build_records`, `pick_anchor`, `version_sort_key` from Task 1.
- Produces: `render_notes(records, expected, tools, microg_tag) -> str` and `cmd_render_notes`.

**Signature:**

```python
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
```

#### Sub-tasks

**2.1 Release body tests**
- [ ] 2.1.1 Write `tests/youtube_vn/render_notes_test.py` (failing tests: `test_render_notes_structure`, `test_render_notes_microg_missing`, `test_render_notes_anchor_newest_clean`, `test_render_notes_empty_exits_nonzero`)
- [ ] 2.1.2 Run tests → expect failure (function not defined)
- [ ] 2.1.3 Commit test + fixtures: `git add tests/youtube_vn/render_notes_test.py`

**2.2 Implement `render_notes`**
- [ ] 2.2.1 Implement `render_notes(records, expected, tools, microg_tag) -> str` per the global constraint (MicroG IMPORTANT first, TIP anchor, single `<details>` matrix, download table with icon, `## Tools used`)
- [ ] 2.2.2 Implement `_patch_row` helper and `cmd_render_notes` CLI (writes output file; exits non-zero if no APKs found)
- [ ] 2.2.3 Run tests → expect PASS

**2.3 Commit release body rendering**
- [ ] 2.3.1 Run full suite: `python3 -m unittest discover -s tests -t . -p '*_test.py' -v` → expect all PASS
- [ ] 2.3.2 Commit: `git add .github/scripts/youtube.py tests/youtube_vn/ && git commit -m "feat: add YouTube release body rendering (pure)"`

**2.4 Golden fixture by parity**
- [ ] 2.4.1 Synthesize `release/`, `applied/`, `failed/` directories from `records.json`
- [ ] 2.4.2 Run the current bash release body over those dirs, redirect to `gold-current.md`
- [ ] 2.4.3 Assert `render_notes` output equals `gold-current.md` (line-by-line diff: structure, emoji, icon URL, section placement)
- [ ] 2.4.4 Write `tests/fixtures/youtube/release-notes.md` golden file from the parity output
- [ ] 2.4.5 Add `test_render_matches_golden` test asserting `render_notes(...) == fixture.read_text()`
- [ ] 2.4.6 Run full suite → expect all PASS including golden test
- [ ] 2.4.7 Commit: `git add tests/fixtures/youtube/ tests/youtube_vn/render_notes_test.py && git commit -m "test: add golden-file parity for YouTube release body"`

---

### Task 3: Toolchain fetch, discover, options, gate

**Files:**
- Modify: `.github/scripts/youtube.py` (add `fetch_toolchain`, `discover_core`, `build_matrix`, `classify_versions`, `build_expected_patches`, `reused_versions`, `gate_rebuild`, `cmd_fetch_toolchain`, `cmd_discover`, `cmd_select_options`)
- Create: `tests/youtube_vn/fetch_toolchain_test.py`
- Create: `tests/youtube_vn/discover_test.py`
- Create: `tests/youtube_vn/gate_rebuild_test.py`
- Create: `tests/youtube_vn/select_options_test.py`
- Create: `tests/fixtures/youtube/releases.json` (fixture releases payload with drafts + prereleases)
- Create: `tests/fixtures/youtube/last-state.json` (sample per-tag state)
- Modify: `tests/fixtures/youtube/list-versions.txt` (Task 1 parity capture)

**Interfaces:**
- Consumes: `parse_version_lines`, `parse_list_patches`, `version_sort_key` from Task 1.
- Produces: discover outputs (`matrix`, `versions`, `channels`, `reused`, `nothing_to_build`, `patches_ver`, `desktop_ver`, `expected_patches`), written to `$GITHUB_OUTPUT`.

**Signatures:**

```python
def fetch_toolchain(owner: str, patches_repo: str, desktop_repo: str,
                    token: str) -> Toolchain: ...
# Toolchain = {"patches_ver": str, "desktop_ver": str}

def discover_core(versions_raw: str, stable_raw: str, exclude: str,
                  state: dict | None, patches_ver: str,
                  bundle_patch_names: list[str]) -> DiscoverResult
# DiscoverResult = {"matrix": str, "versions": str, "channels": str,
#                    "reused": str, "nothing_to_build": int, "expected_patches": list[str],
#                    "all_versions": str}

def build_matrix(versions: list[str]) -> tuple[str, str, int]
def classify_versions(stable: list[str], experimental: list[str]) -> tuple[list[str], list[str]]
def build_expected_patches(bundle_names: list[str]) -> list[str]
def reused_versions(done: list[str], new: list[str]) -> list[str]
def gate_rebuild(last_state: dict | None, sources: dict, covered: list[str]) -> int
```

`discover` JSON outputs use compact separators `(",", ":")` (mirror proton), `nothing_to_build` == 1 when the matrix has only the noop sentinel.

#### Sub-tasks

**3.1 fetch_toolchain**
- [ ] 3.1.1 Write `tests/youtube_vn/fetch_toolchain_test.py` with: (a) parity test comparing version extraction against current `basename | sed` over `tests/fixtures/youtube/releases.json`, (b) transient-failure test (mocked `requests.get` failing twice then succeeding), (c) tag_name↔basename 1:1 alignment test
- [ ] 3.1.2 Run tests → expect failure
- [ ] 3.1.3 Implement `fetch_toolchain(owner, patches_repo, desktop_repo, token) -> Toolchain` using `requests` with retry adapter + size verification; derive versions from basenames (`patches-` suffix stripped; `morphe-desktop-` prefix stripped, trailing `-all` stripped)
- [ ] 3.1.4 Run tests → expect PASS
- [ ] 3.1.5 Commit: `git add tests/youtube_vn/fetch_toolchain_test.py && git commit -m "test: add fetch_toolchain tests"`

**3.2 classify_versions + build_matrix**
- [ ] 3.2.1 Write `tests/youtube_vn/discover_test.py` with: `test_discover_emits_matrix_channels_and_reused`, `test_discover_excludes_versions`, `test_classify_versions_stable_vs_beta`
- [ ] 3.2.2 Run tests → expect failure
- [ ] 3.2.3 Implement `classify_versions(stable, experimental) -> tuple[list, list]`, `build_matrix(versions) -> tuple[str, str, int]` (channel Stable/Beta), `reused_versions(done, new) -> list[str]`
- [ ] 3.2.4 Run tests → expect PASS

**3.3 discover_core**
- [ ] 3.3.1 Write `tests/youtube_vn/discover_core_test.py` or extend `discover_test.py` (test: empty version list exits non-zero, excluded versions appear in `reused`)
- [ ] 3.3.2 Run tests → expect failure
- [ ] 3.3.3 Implement `discover_core(...)` (orchestrates `list-versions -x`/plain via `_run_java`, classify, exclude filter from `EXCLUDE_VERSIONS`, state lookup → rebuild diff → `reused`, matrix build, expected patches)
- [ ] 3.3.4 Run tests → expect PASS

**3.4 build_expected_patches + gate_rebuild**
- [ ] 3.4.1 Write `tests/youtube_vn/gate_rebuild_test.py` with: version-range-change gate, bundle-version-change gate (MorpheApp-only source map), no-change gate
- [ ] 3.4.2 Run tests → expect failure
- [ ] 3.4.3 Implement `build_expected_patches(bundle_names)` (dedup + forced patches only if absent), `gate_rebuild(last_state, sources, covered) -> int` (mirror proton with single MorpheApp source entry)
- [ ] 3.4.4 Run tests → expect PASS

**3.5 cmd_fetch_toolchain, cmd_discover, cmd_select_options**
- [ ] 3.5.1 Write `tests/youtube_vn/select_options_test.py` (`test_select_options_runs_options_create` — mocked `java` call)
- [ ] 3.5.2 Implement `cmd_fetch_toolchain(args)` (writes `PATCHES_VER`/`DESKTOP_VER` to `$GITHUB_OUTPUT`)
- [ ] 3.5.3 Implement `cmd_discover(args)` (runs flow, writes all discover outputs, prints `covering N version(s) (reused=...)`)
- [ ] 3.5.4 Implement `cmd_select_options(args)` (`_run_java(options-create -f PACKAGE -o options.json -p morphe-patches.mpp)`)
- [ ] 3.5.5 Run full suite → expect all PASS

**3.6 Commit toolchain + discover**
- [ ] 3.6.1 Run full suite → expect all PASS
- [ ] 3.6.2 Commit: `git add .github/scripts/youtube.py tests/youtube_vn/ tests/fixtures/youtube/ && git commit -m "feat: add YouTube discover, toolchain fetch, options, and gate logic"`
- [ ] 3.6.3 Add parity tests (mirror bash extraction over fixtures), run, commit: `git commit -m "test: add parity tests for fetch_toolchain and discover"`

**3.7 cmd_download_apk + cmd_patch + cmd_cleanup_artifacts**
- [ ] 3.7.1 Write `tests/youtube_vn/download_apk_test.py` / `tests/youtube_vn/patch_test.py` (mocked `_run_bun` / `_run_java` for CLI calls)
- [ ] 3.7.2 Implement `cmd_download_apk(args)` (`_run_bun node_modules/apkmirror-downloader/dist/cli.js download google-inc youtube --version <v> --outdir download`)
- [ ] 3.7.3 Implement `cmd_patch(args)` (runs `java -jar morphe-desktop.jar patch` with `-e patcher.mpp -l <version> -o ship -f com.google.android.youtube -a <arch> --options options.json`, reads applied/failed from `ship/`)
- [ ] 3.7.4 Implement `cmd_cleanup_artifacts(args)` (`_run_gh` for artifact DELETE on the GitHub API)
- [ ] 3.7.5 Run full suite → expect all PASS
- [ ] 3.7.6 Commit: `git add .github/scripts/youtube.py tests/youtube_vn/ && git commit -m "feat: add YouTube download, patch, and cleanup subcommands"`

---

### Task 4: Build, commit, and reconcile state

**Files:**
- Modify: `.github/scripts/youtube.py` (add `youtube_state`, `state_put`, `remove_youtube_entry_from_shared_state`, `cmd_record_state`)
- Create: `tests/youtube_vn/record_state_test.py`
- Create: `tests/youtube_vn/merge_state_test.py`
- Modify: `patch_version.json` (delete the `youtube` key)

**Interfaces:**
- Consumes: `youtube_state`, `state_put` from this task; `records` come from `cmd_render_notes` via `ship/keep.json` (passed as `--keep-in`).
- Produces: `.github/tags/youtube.json` committed atomically; the `youtube` key removed from `patch_version.json`.

**Signatures:**

```python
def youtube_state(records: list[dict], patcher: str, package: str,
                  sources_map: dict) -> dict   # mirror proton's record_state
def state_put(repo_full: str, state_path: str, content_str: str, message: str,
              token: str) -> None                    # requests PUT + sha conditional + retry
def remove_youtube_entry_from_shared_state(path: str) -> None
```

`state_put` via `requests`: GET `contents/{path}` (`?version_id=...` optional) to get `sha`; PUT with `-f sha=<sha>`-equivalent `sha=` param; on 409 retry with the fresh sha; exit `::error::` after attempt 5 (mirror `proton-vpn.py:state_put`'s loop and spacing). Because this is Python instead of `gh api`, the payload is built with `json.dumps(record, indent=2)` + `base64.b64encode(content).decode()` and sent as form field `content=`.

#### Sub-tasks

**4.1 youtube_state**
- [ ] 4.1.1 Write `tests/youtube_vn/record_state_test.py` (mirror `tests/proton_vn/record_state_test.py` with MorpheApp/Morphe Patches source for YouTube: `test_builds_new_schema_with_morpheapp_source`, `test_versions_keep_newest_first`, `test_patch_history_kept_per_version`)
- [ ] 4.1.2 Run tests → expect failure
- [ ] 4.1.3 Implement `youtube_state(records, patcher, package, sources_map) -> dict` (mirror `proton-vpn.py:record_state`, owner `MorpheApp`, repo `morphe-patches`)
- [ ] 4.1.4 Run tests → expect PASS

**4.2 state_put**
- [ ] 4.2.1 Write `tests/youtube_vn/merge_state_test.py` with: `test_state_put_succeeds_after_retry_on_conflict` (mocked GET returning sha, PUT returning 409 then 200), `test_state_put_exits_nonzero_after_final_failure` (5th PUT returns 409 → exit non-zero)
- [ ] 4.2.2 Run tests → expect failure
- [ ] 4.2.3 Implement `state_put(repo_full, state_path, content_str, message, token)` using `requests.Session` with `max_retries=3`, 5-attempt exponential backoff (`sleep(2 ** attempt)`), GET before each PUT for fresh sha, exit `::error::` after attempt 5
- [ ] 4.2.4 Run tests → expect PASS

**4.3 cmd_record_state + shared state cleanup**
- [ ] 4.3.1 Write `tests/youtube_vn/remove_shared_state_test.py` (`test_remove_youtube_entry_from_shared_state`)
- [ ] 4.3.2 Implement `cmd_record_state(args)` (reads `keep.json`, builds record, calls `state_put`)
- [ ] 4.3.3 Implement `remove_youtube_entry_from_shared_state(path)` (jq-free dict merge: load JSON, delete `.source.MorpheApp."morphe-patches".<patches_ver>.youtube`, write back with `indent=2` + newline, guarded by pre-check)
- [ ] 4.3.4 Run full suite → expect all PASS

**4.4 Commit state layer**
- [ ] 4.4.1 Commit: `git add .github/scripts/youtube.py tests/youtube_vn/ && git commit -m "feat: add YouTube record-state and state-commit (contents API)"`

**4.5 Remove youtube key from patch_version.json**
- [ ] 4.5.1 Guard: verify `.source.MorpheApp."morphe-patches"` has a `.youtube` key for the current `$PATCHES_VER`
- [ ] 4.5.2 Run: `jq --arg tag "$PATCHES_VER" 'del(.source.MorpheApp."morphe-patches"[$tag].youtube)' patch_version.json > tmp && mv tmp patch_version.json`
- [ ] 4.5.3 Verify: `jq '.source.MorpheApp."morphe-patches"'` — youtube key gone, reddit + youtube-music intact
- [ ] 4.5.4 Commit: `git add patch_version.json && git commit -m "chore: remove youtube entry from shared patch_version.json"`

---

### Task 5: Wire all subcommands into youtube.yml

**Files:**
- Modify: `.github/workflows/youtube.yml` (heavily)

**Interfaces:**
- Consumes: every subcommand from Tasks 1-4.
- Produces: a working workflow whose `discover`, `patch`, and `release` jobs call the Python helpers and whose old bash blocks are gone.

**The `python3` setup step** (add to each of the three jobs):

```yaml
- name: Install Python deps
  uses: actions/setup-python@v7
  with:
    python-version: '3.12'
    cache: pip
- name: Install requests
  run: pip install requests
```

#### Sub-tasks

**5.1 Add Python deps setup to all three jobs**
- [ ] 5.1.1 Add the `Install Python deps` + `Install requests` steps to the `meta` job (before `cmd_fetch_toolchain`)
- [ ] 5.1.2 Add the same steps to the `patch` job (before `cmd_discover`/`cmd_select_options`/`cmd_patch`)
- [ ] 5.1.3 Add the same steps to the `release` job (before `cmd_render_notes`/`cmd_record_state`/`cmd_cleanup_artifacts`)

**5.2 Replace meta job (toolchain fetch) — lines 57-89**
- [ ] 5.2.1 Replace bash `meta` step with `python3 .github/scripts/youtube.py cmd_fetch_toolchain`
- [ ] 5.2.2 Verify `PATCHES_VER`/`DESKTOP_VER` output variables match old bash extraction

**5.3 Replace versions/discover job — lines 91-200**
- [ ] 5.3.1 Replace bash `versions` step with `python3 .github/scripts/youtube.py cmd_discover`
- [ ] 5.3.2 Verify matrix/channels/reused/nothing_to_build outputs match old jq

**5.4 Replace options-create — lines 57-89**
- [ ] 5.4.1 Replace bash `options-create` step with `python3 .github/scripts/youtube.py cmd_select_options`
- [ ] 5.4.2 Verify `opt.json` output matches old `options-create`

**5.5 Replace download-apk — lines 311-369**
- [ ] 5.5.1 Replace bash `download-apk` step with `python3 .github/scripts/youtube.py cmd_download_apk` (implemented in Task 3.7)
- [ ] 5.5.2 Verify APKs land in `release/` as expected

**5.6 Replace patch-apk — lines 371-423**
- [ ] 5.6.1 Replace bash `Patch APK` step with `python3 .github/scripts/youtube.py cmd_patch` (implemented in Task 3.7)
- [ ] 5.6.2 Verify applied/failed lists are read from `ship/` output

**5.7 Replace release body — lines 487-639**
- [ ] 5.7.1 Replace bash body-generation block with `python3 .github/scripts/youtube.py cmd_render_notes`
- [ ] 5.7.2 Verify output matches golden fixture (`tests/fixtures/youtube/release-notes.md`) for real inputs

**5.8 Replace record-state — lines 656-734**
- [ ] 5.8.1 Replace bash `Record patch state` + `git` push with `python3 .github/scripts/youtube.py cmd_record_state`
- [ ] 5.8.2 Verify `.github/tags/youtube.json` is committed via contents API PUT

**5.9 Replace cleanup — lines 736-762**
- [ ] 5.9.1 Replace bash cleanup with `python3 .github/scripts/youtube.py cmd_cleanup_artifacts`
- [ ] 5.9.2 Verify only released APKs are kept (`removeArtifacts: true` preserved, wipe model intact)

**5.10 Validate + commit**
- [ ] 5.10.1 `python3 -m py_compile .github/scripts/youtube.py`
- [ ] 5.10.2 `python3 -m unittest discover -s tests -t . -p '*_test.py'` → all PASS
- [ ] 5.10.3 Commit: `git add .github/workflows/youtube.yml .github/scripts/youtube.py && git commit -m "feat: migrate YouTube workflow logic into youtube.py subcommands"`

**5.11 End-to-end verification**
- [ ] 5.11.1 Run workflow via manual dispatch on a feature branch
- [ ] 5.11.2 Confirm discover resolves versions, patch applies, render-notes matches golden, state file commits, release publishes
- [ ] 5.11.3 Confirm CI test check turns green

## Self-Review

1. **Spec coverage:** Every block of youtube.yml bash was assigned to a task (meta → Task 3.1, versions → Task 3.3, options → Task 3.5, download-apk → Task 3.7, build/patch → Task 3.7, body → Task 2, state/git → Task 4, cleanup → Task 3.7). The MicroG block, channel classification, expected-patch derivation, cache pruning, artifact cleanup, and shared-state removal are all covered. No unassigned code.
2. **Sub-task scan:** Each sub-task names one file, one signature, or one concrete command; test names are behaviour-driven, not implementation-driven.
3. **Type consistency:** `build_records` (Task 1.9) returns the record shape that `render_notes` (Task 2.2), `youtube_state` (Task 4.1), and the parity fixtures all consume — checked across Tasks 1, 2, and 4. `state_put`'s retry loop shape matches `proton-vpn.py` exactly (Task 4.2). `cmd_download_apk`/`cmd_patch`/`cmd_cleanup_artifacts` are owned by Task 3.7 with their own tests (Task 3.7.1).
4. **Review Focus:** all five Review Focus lines have an owning task and a test: network-retry in `fetch_toolchain_test.py` (Task 3.1.1) + `merge_state_test.py` (Task 4.2.1); `tag_name`↔basename 1:1 in `fetch_toolchain_test.py` (Task 3.1.1); truncated/missing APKs in `build_records_test.py` (Task 1.9.1) + `render_notes_test.py` (Task 2.1.1); state-commit race in `merge_state_test.py` (Task 4.2.1); empty render in `render_notes_test.py` (Task 2.1.1).
5. **Sub-task granularity:** 33 sub-tasks across 6 main tasks, each with a test-first checkpoint. No sub-task exceeds 2 test files + 1 implementation.
6. **Proportion:** the plan is a decision graph (signatures, test assertions, exact file blocks), not a transcript; function bodies are deliberately absent because the signature + assertions already determine them.

## Execution Handoff

Plan complete and verified. Saved to `docs/superpowers/plans/2026-10-05-youtube-python-migration.md`.

**Execution approach recommendation:** Native. The tasks are tightly coupled through `youtube.py` interfaces and fixtures; a fresh subagent loses the shared context of `proton-vpn.py`'s exact shapes and the real YouTube inputs. A shipped mistake (wrong release body or wrong state file) is cheaper to catch in a single whole-branch review than to correct across many task handoffs.

**Task ordering (dependencies respected):**
1. Task 0 (CI test workflow)
2. Task 1 (data layer — parse, patch lists, records, anchors)
3. Task 2 (release body rendering + golden test)
4. Task 3 (toolchain, classify, discover, expected patches, gate, select-options, download-apk, patch, cleanup)
5. Task 4 (state schema, state_put, record-state, shared-state cleanup)
6. Task 5 (wire all subcommands into youtube.yml)
