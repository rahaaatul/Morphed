#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

rc=0
for t in tests/*_test.sh; do
  printf '== %s\n' "$t"
  bash "$t" || rc=1
done
exit $rc
