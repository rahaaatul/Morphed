#!/usr/bin/env bash
# Assertion helpers, sourced by tests/*_test.sh.
set -uo pipefail

tests_run=0
tests_failed=0

t_begin() { tests_run=$(( tests_run + 1 )); }
t_ok() { printf '  ok   %s\n' "$1"; }

t_fail() {
  tests_failed=$(( tests_failed + 1 ))
  printf '  FAIL %s\n' "$1"
  [[ $# -lt 2 ]] || printf '       %s\n' "$2"
}

# t_eq <name> <expected> <actual>
t_eq() {
  t_begin
  if [[ "$2" == "$3" ]]; then t_ok "$1"; else t_fail "$1" "expected: $2"$'\n'"actual:   $3"; fi
}

# t_contains <name> <needle> <haystack>
t_contains() {
  t_begin
  if [[ "$3" == *"$2"* ]]; then t_ok "$1"; else t_fail "$1" "missing: $2"$'\n'"in: $3"; fi
}

t_summary() {
  printf '%d run, %d failed\n' "$tests_run" "$tests_failed"
  [[ $tests_failed -eq 0 ]]
}
