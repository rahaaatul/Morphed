# Workflow style

Conventions for anything under `.github/workflows/` and `.github/actions/`.

The existing app pipelines are the worked examples. Read one before writing a new one —
they differ in details, and the rules below are what they agree on.

## Shape

One workflow per app. Each has its own release tag, its own state file, and its own
concurrency group, so an app can be re-run, rolled back, or removed independently of the
others.

Three jobs, in this order:

| Job | Responsibility |
| --- | --- |
| discover | Work out which versions to build, and everything downstream needs to know about them |
| patch | Fan out, one leg per version, and build the artifact |
| release | Gather the fan-out's output and publish |

Do not add a fourth job to fix one thing. Put the logic in a script and call it from one
of the three.

Job ids are lowercase (`discover`, `patch`, `release`). The `name:` is what a human sees
in the Actions sidebar, and it should be a phrase — the id and the name are not the same
thing.

## Header

Fixed order, blank line between each block:

```yaml
name: <Human App Name>

on:
  workflow_dispatch:
  schedule:
    # Why this cadence, and what it buys.
    - cron: '0 */12 * * *'

permissions:
  contents: write
  actions: write

concurrency:
  group: <app-slug>
  cancel-in-progress: false

env:
  APP_SLUG: <slug>
  ...

jobs:
```

- `name:` matches the release name.
- Always keep `workflow_dispatch:`. Every pipeline must be re-runnable by hand, and a
  scheduled run is not something you can wait for when something needs checking now.
- `permissions:` explicit and minimal. Never a blanket grant. Publishing needs
  `contents: write`; deleting run artifacts needs `actions: write`. Ask for the
  narrowest set that works.
- `concurrency.group` is the app slug, so two apps never block each other. With
  `cancel-in-progress: false` an overlapping run queues instead of killing work in
  flight. Queueing is almost always the right choice here: these pipelines commit a
  state file and republish a release at the end, so cancelling part-way wastes work that
  was already paid for and leaves the release untouched.
- The cron gets a comment. A bare `- cron:` is indistinguishable from an accident.

A pipeline that is too short to overlap — a couple of runs a day, each finishing in
minutes — does not need a concurrency group at all. Add one when runs can realistically
collide, not by default.

## Workflow-level `env:`

Every constant lives here, `UPPER_SNAKE`, never inline in a step:

```yaml
env:
  REPO_FULL: ${{ github.repository }}
  APP_SLUG: <slug>
  APP_TITLE: '<Human App Name>'
  RELEASE_TAG: <tag>
  PACKAGE: <upstream.package.name>
  UPSTREAM: <owner>/<repo>
  STATE_FILE: .github/tags/<slug>.json
```

Quote anything that could be misread as YAML — a title with a space, a numeric-looking
string, a cron expression. A multi-line list uses a `|` block; scripts read it from
`os.environ` and split on newlines.

This block is the single source of truth for a value used in more than one step. Changing
`APP_SLUG` should be a one-line edit, not a search across the file.

Values a *run* discovers — the version matrix, tool versions — are job outputs, not
`env:` entries. See below.

`github.repository` is available at workflow level, so prefer it over a hardcoded
`owner/repo`.

## The `env` context restriction

From GitHub's context-availability table:

| Workflow key | `${{ env.NAME }}` |
| --- | --- |
| `env` (workflow level) | **Not available** — only `github`, `secrets`, `inputs`, `vars` |
| `jobs.<job_id>.env` | **Not available** — only `github`, `needs`, `strategy`, `matrix`, `vars`, `secrets`, `inputs` |
| `jobs.<job_id>.steps[*].env` | Available |
| `jobs.<job_id>.steps[*].with` | Available |
| `jobs.<job_id>.steps[*].if` | Available |
| `jobs.<job_id>.outputs.<output_id>` | Available |

The `env` context does not exist while `env:` is being assembled, so a workflow-level or
job-level `env:` cannot reference itself. Re-declaring a workflow constant inside a
job's `env:` looks reasonable and GitHub rejects the **entire file** with
`Invalid workflow file` — the run never starts, and the error names no key.

Step-level `env:` is the exception and may read `env.*`.

Shell steps receive `env:` values as ordinary environment variables. House style is the
plain shell form, `"$STATE_FILE"` and `--package "$PACKAGE"`, not
`${{ env.STATE_FILE }}`. Both evaluate; the plain form survives copy-paste into a
terminal, which matters when you are reproducing a failure locally.

## Passing values between jobs

Declare every output explicitly on the producing job, read it as `needs.<job>.outputs.<name>`:

```yaml
outputs:
  matrix: ${{ steps.discover.outputs.matrix }}
  versions: ${{ steps.discover.outputs.versions }}
  skip: ${{ steps.discover.outputs.skip }}
```

Scripts write these to `$GITHUB_OUTPUT`. **Never parse a script's stdout to get a value**
— stdout is for humans reading the log. That split is what lets the same script be tested
locally and used in a run.

Gate on a single flag rather than duplicating the condition across jobs:

```yaml
patch:
  needs: discover
  if: needs.discover.outputs.skip != '1'

release:
  needs: [discover, patch]
  if: always() && needs.discover.outputs.skip != '1' && needs.patch.result == 'success'
```

Two things to know about that pattern:

- `always()` on the last job means it still runs when an upstream job failed, which is
  how a failure gets reported rather than silently skipped. It must then be guarded, or
  it will publish on failure.
- For a matrix job, `needs.<job>.result` is the **aggregate** across every leg. One leg
  failing fails the aggregate. That is usually what you want — a partial matrix should
  not reach a release — but it means one flaky version can block the whole run.

## Runner and action versions

Pin the runner image, and pin every action to an exact version:

```yaml
runs-on: ubuntu-24.04
```

```yaml
- uses: actions/checkout@v6.0.2
- uses: actions/setup-java@v5.2.0      # with: distribution: 'zulu', java-version: '21'
- uses: actions/cache/save@v6
- uses: actions/cache/restore@v6
- uses: actions/download-artifact@v8.0.1
- uses: actions/upload-artifact@v7.0.1
- uses: ncipollo/release-action@v1.21.0
- uses: oven-sh/setup-bun@v2
```

No `@main`, no `@v4`, no floating tags. Patch tooling is sensitive to the runtime, so a
runner or JDK bump is a deliberate change that should be made with a run attached to it.

## Timeouts

Set `timeout-minutes` on every job.

GitHub's default is 360 minutes, which is how long a hung process or a stalled connection
holds a runner. A pipeline that normally finishes in minutes does not need six hours of
grace. Pick a value with a large multiple of the observed runtime, and err high: a
timeout that kills a legitimate slow run is worse than the hang it prevents, because it
turns a passing run red with no explanation.

```yaml
jobs:
  patch:
    runs-on: ubuntu-24.04
    timeout-minutes: 30
```

A job-level timeout sends SIGINT, then SIGKILL after a grace period. A step killed
mid-build wastes that work but publishes nothing, provided publishing is the last job.

## Steps

Verb phrases, present tense: `Checkout`, `Set up JDK`, `Restore toolchain`,
`Download original`, `Verify and generate release body`, `Clean up run artifacts`.

### Shell steps

`run: |` block scalar, strict mode on the first line, then the call:

```yaml
- name: Verify and generate release body
  run: |
    set -euo pipefail
    python3 .github/scripts/<app>.py render-notes \
      --output release-notes.md
```

- `set -euo pipefail` is the first line of every multi-line shell step. The exception is
  a best-effort cleanup step, which drops `-e` so one failed delete cannot strand the
  rest.
- Keep the block short. If it needs a loop over parsed data, a conditional over parsed
  data, or a merge, that logic belongs in a script — see
  [Script style](script-style.md).
- `[[ ]]`, not `[ ]`. `shopt -s nullglob` before any glob whose emptiness you then test
  with `${#arr[@]}` — without it an unmatched glob stays a literal string and the
  emptiness check passes on the wrong thing.
- Declare a step's inputs as step-level `env:`, never inline `${{ }}`:
  ```yaml
  env:
    GH_TOKEN: ${{ github.token }}
    EXPECTED_PATCHES: ${{ needs.discover.outputs.expected_patches }}
  ```
  **Step `env:` does not carry across steps.** If a later step needs a value an earlier
  step computed, declare it again — and say so in a comment, or the next reader assumes
  it leaked.
- Credentials arrive via `env:`. Never put a token on a command line: it lands in the log.
- Fail loudly with workflow commands — `::error::` for a failure, `::warning::` for
  something worked around. Both render with the severity you intended, and both are
  greppable in the logs after the fact.

### Comments

Explain **why**, never what. A comment earns its place only if it records a decision, a
constraint, or a trap that is not visible in the code.

Worth a comment:

- Why a non-default flag is present, and what breaks without it
- Why a check exists that looks redundant
- Why an apparently wrong-looking value is correct here
- Why something is allowed to fail silently

Not worth a comment:

- What the next line does
- What a variable holds, when the name says it
- A restatement of the language

If a block reads fine without the comment, delete the comment.

## Caching

Use `actions/cache/restore` and `actions/cache/save` explicitly rather than the implicit
`actions/cache`. Save from the job that downloads, restore in the job that consumes, and
make both keys the same string.

```yaml
key: <slug>-toolchain-${{ env.APP_SLUG }}-${{ steps.meta.outputs.tool_version }}
```

- Namespace every key by app. `${{ env.APP_SLUG }}` inside the key is cheap insurance
  against two apps colliding on a similarly-shaped cache.
- **Cache inputs, never outputs.** Tool bundles, tool binaries, untouched source
  artifacts. Never a patched or built artifact — the release is the artifact, and a stale
  cached build is indistinguishable from a fresh one.
- Include every input that invalidates the cache in the key. A key missing a version
  component will happily read new content out of an old cache.
- A cache saved earlier in the same run is not guaranteed to be visible to a downstream
  job, so the consuming job has to be able to rebuild what it needs:
  ```yaml
  - name: Fetch toolchain on cache miss
    if: ${{ !hashFiles('tool.jar') }}
    run: bash scripts/<app>/fetch-toolchain.sh
  ```
- Prune what the current run will not reuse, during the run. Version support gets
  dropped upstream, and without pruning the cache only grows.

## Artifacts

```yaml
- uses: actions/upload-artifact@v7.0.1
  with:
    name: <something>-${{ matrix.version }}
    path: ./<something>-${{ matrix.version }}.txt
    retention-days: 1
    if-no-files-found: error
```

- `retention-days: 1` — these are transit data; the durable copy is the release.
- `if-no-files-found: error` — **required, not optional.** Without it a step that
  produces nothing passes quietly, and whatever consumes that artifact later is working
  from an incomplete set. The error flag is what turns silence into a failure.
- Upload per-version artifacts from the fan-out, then gather them in the publish job with
  `actions/download-artifact` plus `pattern:` and `merge-multiple: true`.
- Give related artifacts a common prefix so `pattern:` can select a group.

Delete run artifacts once they are on the release, in an `if: always()` step. Retention
is a backstop, not a plan — a single fan-out over large files leaves hundreds of
megabytes behind.

```yaml
- name: Clean up run artifacts
  if: always()
  env:
    GH_TOKEN: ${{ github.token }}
  run: |
    set -uo pipefail
    ids=$(gh api "repos/${REPO_FULL}/actions/runs/${GITHUB_RUN_ID}/artifacts" \
          --paginate --jq '.artifacts[].id')
    ...
```

`--paginate` is required: the artifact list endpoint is one page, and a run with many
legs can exceed it.

## Publishing

```yaml
- uses: ncipollo/release-action@v1.21.0
  with:
    tag: ${{ env.RELEASE_TAG }}
    name: ${{ env.APP_TITLE }}
    bodyFile: release-notes.md
    artifacts: |
      release/*.apk
    allowUpdates: true
    removeArtifacts: true
    prerelease: false
    makeLatest: false
```

Each app owns one rolling tag and updates it in place rather than creating a release per
version. That is what keeps download links stable.

- `makeLatest: false` so a per-app pipeline does not displace the repository's headline
  release.
- `removeArtifacts: true` is correct **only** while the pipeline republishes its entire
  version range every run. If you ever change it to publish just newly discovered
  versions, this must become a keep-list reconcile — leaving it on means every version
  you did not rebuild gets deleted from the tag. If you cannot state which of the two
  modes the pipeline is in, the flag is wrong.

Render the release body into a file with a script and pass `bodyFile`. Never assemble
Markdown in a `run:` block: it cannot be tested, it is user-facing copy that gets
diffed against the live release, and the diff is the only review it gets.

## Composite actions

Use `.github/actions/*/action.yml` for steps genuinely shared by more than one pipeline.
If only one pipeline uses it, it is a script, not an action.

Declare every input with a `description` and a quoted `default`:

```yaml
inputs:
  install_python_deps:
    description: 'Install Python dependencies'
    required: false
    default: 'false'
runs:
  using: "composite"
```

- Guard optional steps with a **string** comparison — that is how GitHub delivers inputs:
  `if: inputs.install_python_deps == 'true'`. Comparing against a boolean silently never
  matches.
- Composite `run:` steps need an explicit `shell: bash`.
- Do not pass credentials as inputs. Reference `github.token` inside the action.

## Checklist for a new workflow

- [ ] Copied from the closest existing pipeline, with app-specific names replaced
- [ ] `workflow_dispatch:` kept
- [ ] `permissions:` is the narrowest set that works
- [ ] `concurrency:` added if runs can realistically overlap
- [ ] Every constant in workflow-level `env:`, quoted where YAML could misread it
- [ ] No `${{ env.* }}` inside any `env:` block
- [ ] Job outputs declared explicitly; values read from `needs`, never parsed from stdout
- [ ] `timeout-minutes` on every job
- [ ] Every action pinned to an exact version
- [ ] `set -euo pipefail` first line of every multi-line shell step
- [ ] Secrets via `env:`, never on a command line
- [ ] `if-no-files-found: error` on every upload
- [ ] Cache keys namespaced by app and include every invalidating input
- [ ] No cached build outputs
- [ ] Comments explain why, and only where there is a why
- [ ] Release body rendered by a script, passed via `bodyFile`
- [ ] `removeArtifacts` setting matches the pipeline's publish mode
