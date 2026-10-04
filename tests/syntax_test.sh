#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0

for wf in .github/workflows/*.yml; do
  if ! python3 -c 'import sys,yaml; yaml.safe_load(open(sys.argv[1]))' "$wf"; then
    echo "FAIL: $wf is not valid YAML"
    fail=1
    continue
  fi

  python3 - "$wf" <<'PY' || fail=1
import sys, yaml, pathlib, tempfile, subprocess
wf = pathlib.Path(sys.argv[1])
doc = yaml.safe_load(wf.read_text())

def blocks(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "run" and isinstance(v, str):
                yield v
            else:
                yield from blocks(v)
    elif isinstance(node, list):
        for item in node:
            yield from blocks(item)

rc = 0
for i, body in enumerate(blocks(doc)):
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(body)
        path = fh.name
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAIL: {wf} run block #{i}: {r.stderr.strip()}")
        rc = 1
    pathlib.Path(path).unlink()
sys.exit(rc)
PY
done

[[ $fail -eq 0 ]] && echo "PASS: workflow syntax"

# Review Focus #1: a release with no vanilla-direct asset must fail loudly rather
# than write a zero-byte APK.
grep -q '::error::could not resolve the direct-release APK' .github/workflows/protonvpn.yml \
  || { echo "FAIL: protonvpn.yml has no guard for a missing release asset"; exit 1; }

exit $fail
