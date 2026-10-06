# Testing

Conventions for the test suite. `unittest` from the standard library, no `pytest`, no
`requirements.txt`, no runner script — a contributor should be able to clone the
repository and run everything with a bare `python3`.

## Running

```bash
python3 -m unittest discover -s tests -t . -p '*_test.py'
```

The pattern is a **suffix**, `*_test.py`, not the `test_*.py` prefix. Test file names must
match it or they will be silently skipped, which is the worst failure mode a test suite
has.

## What needs a test

Anything that decides something:

- Which versions to build, and how many
- Which channel or tier a version belongs to
- Whether a rebuild is warranted, or the run is a no-op
- Parsing output from an external tool
- State file contents and shape
- The rendered release body
- Any file mutation, and its failure mode

What does not need one:

- A `curl`
- An API call
- A cache key string
- A workflow trigger

**If a change can silently ship a wrong artifact or a wrong release body, it needs a
test.** Those two are what this repository produces, and both are consumed by people who
cannot tell from the outside that something is wrong.

Tests do not prove a pipeline works end to end. Only a real run does — see
[Verify your change](../../CONTRIBUTING.md#verify-your-change).

## Layout

```
tests/
  fixtures/<app-slug>/       # inputs and golden files, named after the app
  <app_slug>_vn/             # tests
    loader.py
    <function>_test.py
```

Test file names follow the unit under test: one file per function or per coherent group,
named `<function>_test.py`.

### Loading a hyphenated script

Workflow and script filenames are hyphenated; Python identifiers cannot be. The test
package is therefore underscored, and a small loader imports the script by path:

```python
"""Load the hyphenated .github/scripts/<app>.py as an importable module."""

import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE = _ROOT / ".github" / "scripts" / "<app>.py"


def load():
    spec = importlib.util.spec_from_file_location("app_vn", _MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

Each test module bootstraps `sys.path` before importing it, which is why that block
precedes the import everywhere:

```python
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

app = load()
```

Keep the `# noqa: E402`. No linter is configured to need it, but a contributor who adds
one should not have to fix every file.

## Style

Plain `unittest.TestCase`. No mocks unless you are genuinely testing a subprocess
boundary. Most tests pass real data in and assert on real data out, because the fixtures
are real output from real runs — a failure then means something real broke.

- Name tests after the behaviour, not the implementation:
  `test_experimental_excludes_stable_preserving_order`, not `test_classify_2`.
- One behaviour per test. Two asserts are fine when they are the same claim from two
  directions; three unrelated asserts in one test means three failures to diagnose.
- Use realistic data. Real version numbers, real patch names, real edge cases from
  production.
- `assertEqual` on whole structures, not field-by-field, so an added field does not fail
  every test.

## Four kinds of test worth knowing

### 1. Golden-file tests for generated output

A rendered release body is compared against a committed fixture, byte for byte:

```python
def test_render_matches_golden(self):
    body = app.render_notes(...)
    self.assertEqual(body, (FIXTURES / "release-notes.md").read_text())
```

Generated output has structure that no assertion list captures — ordering, section
placement, escaping. A golden file is the only practical way to notice that a refactor
moved a section or dropped a row.

**When you deliberately change the output, regenerate the fixture and read the diff before
committing it.** A regenerated golden file with no review attached is exactly how a silent
regression gets in — the test still passes, and passes for the wrong reason.

The same approach works for any committed artifact: matrix JSON, state files, options
files.

### 2. Parity tests against the implementation being replaced

When logic moves from one language or tool to another, compute the expected answer by
running the original and compare:

```python
expected = subprocess.run(
    ["some-tool", ...],
    input=raw,
    capture_output=True,
    text=True,
    check=True,
).stdout.splitlines()
self.assertEqual(pure_function(raw), expected)
```

This works because the previous implementation was already trusted in production, so the
test asserts the replacement still agrees with something real.

Keep the test afterwards. It is cheap, and it documents *why* the function behaves the way
it does — which is the question the next reader will otherwise have to answer by
experiment.

### 3. CLI tests for the command wrappers

Argument parsing, exit codes, and files written:

```python
def test_exits_nonzero_when_a_name_is_missing(self):
    with tempfile.TemporaryDirectory() as tmp:
        ...
        self.assertNotEqual(rc, 0)
```

Two conventions that matter more here than elsewhere:

- **Test the failure path as carefully as the success path.** A command that silently
  succeeds while doing less than asked is the worst failure mode available: the pipeline
  reports success and publishes something incomplete. Assert on the non-zero exit, and
  assert on what the error message says.
- **A test that writes to a real directory must clean up.** Use `tempfile`, never a path
  in the repository. A test that leaves files behind will eventually delete or overwrite
  something.

### 4. Data-shape tests

Small tests asserting the structure of the JSON that crosses step boundaries — a required
key is present, a list is never `null`, a field that consumers index into always exists.

These are cheap and they catch the class of bug where a producer changes shape and the
consumer fails later, in a different job, with a confusing error.

## Testing external tools

When a script shells out, test the parsing separately from the invocation:

- **Parsing** — pure function, takes text, returns structure. Test it with captured real
  output as a fixture. This is where the bugs are.
- **Invocation** — argument construction. Test that the right arguments are built, using a
  stub or by inspecting a list you assembled.
- **The tool itself** — do not test it. Its behaviour is upstream's problem, and a test
  asserting it breaks when they release.

## Adding tests for a new app

1. `mkdir tests/<app_slug>_vn` — underscored form of the workflow slug.
2. Copy `loader.py` and point `_MODULE` at that app's script.
3. Put fixtures in `tests/fixtures/<app-slug>/`, captured from a real run.
4. If the app produces generated output, add the golden-file test **on the first commit
   that produces it**, not later. Retrofitting one means reconstructing what the output
   used to be.
5. Cover the decision functions first: version selection, the rebuild gate, and the
   failure paths. Those are where a wrong answer ships a wrong artifact.

## Open gaps

Stated plainly so nobody assumes coverage that is not there:

- **CI does not run this suite.** The existing CI workflow detects new upstream patches;
  it is not a test workflow. Until one exists, the suite runs only when someone runs it
  locally. Adding one — triggered on push and pull request, a single
  `python3 -m unittest discover` step — is a small, self-contained contribution and the
  highest-value gap on this page.
- **Most app pipelines have no tests yet.** Where their logic still lives in `run:` blocks
  it cannot be tested without the refactor in [Script style](script-style.md) first.

## Checklist for new tests

- [ ] File named `<function>_test.py`, matching the discovery pattern
- [ ] In `tests/<app_slug>_vn/`, imported through `loader.py`
- [ ] Behaviour named, not implementation
- [ ] Fixtures from real runs, in `tests/fixtures/<app-slug>/`
- [ ] Failure paths covered as thoroughly as success paths
- [ ] Any temporary files created with `tempfile`, and cleaned up
- [ ] No assertion on an external tool's own behaviour
- [ ] Golden fixture regenerated deliberately, with the diff read before committing
