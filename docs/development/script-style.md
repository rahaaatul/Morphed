# Script style

Conventions for the scripts a workflow calls: `.github/scripts/*.py` and
`scripts/**/*.sh`.

Read an existing pair before writing a new one. They are deliberately small, and that is
the point.

## Why scripts at all

A `run:` block cannot be unit tested without parsing YAML and regexing out shell. Logic in
a script can be tested directly, run locally against real files, and reused by more than
one workflow.

The rule: **if code decides something, it belongs in a script.** A `run:` block should
call a script, or call one CLI to do one thing.

## Layout

| Path | Holds |
| --- | --- |
| `.github/scripts/<app>.py` | All orchestration and data processing for one app |
| `scripts/<app>/*.sh` | Shell helpers with no control flow: download, unpack, verify |
| `tests/<app>_vn/` | That app's tests |
| `tests/fixtures/<app-slug>/` | Test inputs and golden files |
| `src/patches/<slug>-<bundle>/` | Per-app patch include/exclude lists |
| `src/keystore/` | Signing keystores |

**One Python file per app**, named to match its workflow. Do not split an app's logic
across `discover.py`, `render.py`, `state.py` — the workflow then has to know which file
owns which step, and the subcommands stop being greppable.

`scripts/<app>/` is for shell only. If you need a conditional, a loop over parsed data,
or JSON, it belongs in the Python file.

### Naming across the three layers

Keep the app's slug consistent, because that is what ties the layers together:

```
.github/workflows/<app-slug>.yml
.github/scripts/<app-slug>.py
scripts/<app-slug>/*.sh
tests/<app_slug>_vn/
.github/tags/<app-slug>.json
```

The workflow and script are hyphenated because they are filenames. The test package is
underscored because a hyphen is not a legal Python identifier — that difference is why
the test loader imports the script by path rather than by name.

## Python

### Module docstring

State scope and the dependency policy, so nobody has to read the imports to learn the
rules:

```python
#!/usr/bin/env python3
"""<App> release pipeline, one argparse subcommand per workflow step.

Stdlib Python only. External CLI tools are invoked directly as subprocesses and are
never wrapped here, so nothing in this file exists only to call a CLI from a different
language.

Each subcommand reads the values the workflow owns from the environment, writes results
back through $GITHUB_OUTPUT, and exits non-zero with a ::error:: message so a workflow
step fails loudly rather than shipping a wrong artifact or a wrong release body.
"""
```

The three-stdlib-only rule is worth enforcing in the docstring because it is the kind of
thing that erodes: one `pip install` in a workflow and the script stops being runnable
locally with a bare `python3`.

### Structure

Three kinds of function. The split is what makes the file testable:

| Kind | Naming | Responsibility |
| --- | --- | --- |
| Pure logic | `render_notes`, `choose_cover`, `record_state`, `gate_rebuild` | Take arguments, return a value. No `os.environ`, no I/O, no subprocess. |
| Command wrappers | `cmd_render_notes`, `cmd_discover` | Read inputs, call pure logic, write outputs, set exit code |
| Private helpers | `_channel_of`, `_write_output`, `_gh_env` | Implementation detail of the above |

The testable unit is the pure layer. If a function you want to test reads the environment
or shells out, the logic that needs testing is probably in the wrong place.

Wire subcommands in `main()`, one `add_parser` per workflow step:

```python
def main(argv=None):
    p = argparse.ArgumentParser(prog="<app>")
    sub = p.add_subparsers(dest="cmd", required=True)

    rn = sub.add_parser("render-notes")
    rn.add_argument("--release-dir", default="release")
    rn.add_argument("--output", default="release-notes.md")
    rn.set_defaults(func=cmd_render_notes)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`required=True` on the subparsers means a bare invocation fails with usage rather than
silently doing nothing.

Every argument gets a `--default`, so each subcommand can be invoked bare. `prog=`
matches the filename.

### Where values come from

- **Paths and modes** → `argparse`.
- **Values the workflow owns** → `os.environ`. The workflow's `env:` block stays the
  single source of truth and the step command line stays short.
- **Results for later steps** → `$GITHUB_OUTPUT`, never stdout.

```python
def _write_output(path, key, value):
    line = f"{key}={value}"
    if path:
        with open(path, "a") as fh:
            fh.write(line + "\n")
    else:
        sys.stderr.write(line + "\n")
```

Falling back to stderr when `GITHUB_OUTPUT` is absent is what lets the same function be
called from a test or a local run without a workflow.

Reading from `os.environ` with a module constant as fallback (`os.environ.get("PACKAGE",
PACKAGE)`) means the script works both under a workflow and standalone.

### Calling other programs

`subprocess.run` with a **list**, never `shell=True` and never a command string:

```python
cmd = [
    "java",
    "-jar",
    args.jar,
    "options-create",
    "-f",
    args.package,
    "-o",
    args.options,
]
for bundle in args.patch_bundle or []:
    cmd += ["-p", bundle]
subprocess.run(cmd, check=True)
```

- **Invoke CLIs directly.** Do not shell out to a CLI from a shell script that Python then
  called, and do not write a Python wrapper around a CLI just to call it from shell. Pick
  the language for that step and stay there.
- Use `check=True` when a non-zero exit must stop the step, and handle the return code
  yourself when it does not.
- `check=False` is for calls where a failure is expected and handled — a probe, or a list
  endpoint that 404s when the state file does not exist yet.
- Capture output with `capture_output=True, text=True` and check `returncode` explicitly
  when you need to branch on it.
- Credentials go through the environment, never an argument:
  ```python
  def _gh_env(token):
      env = dict(os.environ)
      if token:
          env["GH_TOKEN"] = token
      return env
  ```

### Failure

Non-zero exit means the run should stop, and the message should say what broke:

```python
sys.stderr.write("::error::no artifacts were produced by any build job\n")
sys.exit(1)
```

- `::error::` for a failure, `::warning::` for something worked around. Both render with
  the intended severity in the Actions log.
- Messages name the specific thing that is wrong. "No APKs produced" and "applied 6 of 7
  patches, missing X" send the reader in different directions.
- Distinguish *cannot proceed* from *carried on*. A condition that would publish something
  wrong must `sys.exit(1)`; one that is informational should not.

### Retrying

Retry what is genuinely transient, and say so in a comment. Do not wrap a whole
subcommand in a blanket retry — that hides the failures you most want to see.

```python
for attempt in range(1, 6):
    ...
    if put.returncode == 0:
        return
    if attempt == 5:
        sys.stderr.write(f"::error::could not commit after 5 attempts\n")
        sys.exit(1)
    time.sleep(2 ** min(attempt, 4))
```

Back off exponentially, cap the delay, and give up with a clear error. Retry on a
*specific* known-racy operation — a read-modify-write through an API that another
workflow can commit underneath — not on everything.

### Other rules

- **No third-party imports.** If one seems necessary, that is a design signal. Shell out
  to a CLI instead, or do it in the workflow.
- Constants are `UPPER_SNAKE` at module top.
- `str.splitlines()` over `str.split("\n")`, and filter empties explicitly.
- `indent=2` plus a trailing newline for any JSON written to a file that gets committed.
  Those diffs get read by hand.
- Use `separators=(",", ":")` for JSON that goes into a workflow output on one line.

### Comments and docstrings

Explain **why**, never what. Both are allowed here, and both earn their place the same
way:

- A module docstring stating scope and the dependency policy
- A docstring on a function whose *contract* is not obvious from its signature — what it
  returns, what it refuses to do, what a caller must have done first
- A comment recording a decision, a constraint, or a trap

Not worth writing:

- A docstring restating the function name
- A comment describing the next line
- A comment explaining what a well-named variable holds

## Shell

### Header

Shebang, a comment block covering what it does and anything non-obvious about how, the
environment it reads, then strict mode:

```bash
#!/usr/bin/env bash
# Download the patch bundles and tool binary into the current directory.
#
# Newest release carrying the wanted asset is chosen, prereleases preferred, which is
# what the discover job then reports as the toolchain version.
#
# Env: GH_TOKEN, <OWNER_REPO_VARS...>.
set -euo pipefail
```

Strict mode comes **after** the header, not on line 2.

Document every environment variable the script reads. It is the only interface the
workflow has to it.

### Rules

- Functions use `local` for everything they declare:
  ```bash
  newest_asset() {
    local repo=$1 ext=$2
    gh api "repos/${repo}/releases?per_page=30" | jq -r --arg ext "$ext" '
      ...' | head -1
  }
  ```
- Take a repeated rule out into one function. If a shell script has the same expression
  three times, that expression is a function.
- Pass `jq` variables with `--arg`, never by interpolating into the program text. An
  apostrophe inside a jq program breaks shell quoting, which is why a display name with
  an apostrophe goes in as `--arg label "..."`.
- Verify every download before using it, checking non-empty rather than merely present:
  ```bash
  for f in bundle-a.mpp bundle-b.mpp tool.jar; do
    [[ -s "$f" ]] || { echo "::error::$f missing or empty" >&2; exit 1; }
  done
  ```
- End with something that prints the result (`ls -la`, a count). A step whose log ends in
  silence is a step nobody can debug from the sidebar.
- `[[ ]]` and `shopt -s nullglob` before testing a glob's length.
- No unquoted expansions of anything that could be empty or contain spaces.
- Prefer an array when the same flags repeat across calls, so all calls stay identical and
  there is one place to change them.

### Shell is not for parsing

If the step needs to select from JSON, merge two lists, or decide something per item, that
is Python or `jq` — not a `while read` loop. A shell script that grows a loop over parsed
data wants to become a Python subcommand.

## Checklist for a new script

- [ ] Named to match its workflow
- [ ] Module docstring states scope and the dependency policy
- [ ] Decisions live in pure functions that touch neither `os.environ` nor subprocesses
- [ ] One `add_parser` per workflow step, all arguments defaulted
- [ ] Workflow-owned values read from `os.environ` with a constant fallback
- [ ] Results written to `$GITHUB_OUTPUT`, not stdout
- [ ] `subprocess.run` with a list, never `shell=True`
- [ ] Credentials via `env:`, never arguments
- [ ] Failures exit non-zero with a `::error::` message naming the specific problem
- [ ] Retries are narrow, backed off, and commented with what is being retried
- [ ] No third-party imports
- [ ] JSON written with `indent=2` and a trailing newline
- [ ] Comments and docstrings explain why, and only where there is a why
- [ ] Shell scripts document the environment they read
