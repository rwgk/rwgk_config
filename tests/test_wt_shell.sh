#!/bin/bash
# Exercise shell navigation without sourcing the rest of the interactive config.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d)
test_root=$(cd -- "$test_root" && pwd -P)
trap 'rm -rf -- "$test_root"' EXIT
mkdir -p "$test_root/bin" "$test_root/from" "$test_root/destination with spaces"
awk '/^wt\(\) \{/ { copy=1 } copy { print } copy && /^}$/ { exit }' \
    "$repo_root/bashrc" >"$test_root/wrapper.sh"
source "$test_root/wrapper.sh"
cat >"$test_root/bin/git-wt" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" >"$WT_TEST_ARGUMENTS"
if [[ "${1:-}" == trunk ]]; then
    printf '%s\n' "${WT_TEST_PRIMARY_RESULT:-$WT_TEST_RESULT}"
    exit "${WT_TEST_PRIMARY_STATUS:-0}"
fi
if [[ -n "${WT_TEST_REMOVE_ROOT:-}" ]]; then
    rmdir -- "$WT_TEST_REMOVE_ROOT"
fi
printf '%s\n' "$WT_TEST_RESULT"
exit "$WT_TEST_STATUS"
STUB
chmod +x "$test_root/bin/git-wt"
export PATH="$test_root/bin:$PATH"
export WT_TEST_ARGUMENTS="$test_root/arguments" WT_TEST_STATUS=0
export WT_TEST_RESULT="$test_root/destination with spaces"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

cd "$test_root/from"
wt new example
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'new did not enter its worktree'
[[ "$(tail -1 "$WT_TEST_ARGUMENTS")" == --print-path ]] || fail 'navigation did not request a path'

cd "$test_root/from"
wt go example --json >"$test_root/output"
[[ "$PWD" == "$test_root/from" ]] || fail 'JSON output changed directories'
[[ "$(tail -1 "$WT_TEST_ARGUMENTS")" == --json ]] || fail 'JSON request was altered'

WT_TEST_STATUS=7
if wt go example >"$test_root/output" 2>"$test_root/error"; then
    fail 'failed engine call succeeded'
else
    [[ "$?" -eq 7 ]] || fail 'engine exit status was lost'
fi
[[ "$PWD" == "$test_root/from" ]] || fail 'failed call changed directories'
WT_TEST_STATUS=0

for WT_TEST_RESULT in relative-path "$test_root/missing" $'/one\n/two'; do
    if wt go example >"$test_root/output" 2>"$test_root/error"; then
        fail 'unsafe engine output accepted'
    fi
    [[ "$PWD" == "$test_root/from" ]] || fail 'invalid output changed directories'
done

# Literal shell syntax in an existing path must remain data.
WT_TEST_RESULT="$test_root/\$(touch injected)"
mkdir -- "$WT_TEST_RESULT"
wt stack add example
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'literal path was not preserved'
[[ ! -e "$test_root/from/injected" ]] || fail 'engine output was evaluated'

cd "$test_root/from"
WT_TEST_RESULT="$test_root/destination with spaces"
wt clean example >"$test_root/output"
[[ "$PWD" == "$test_root/from" ]] || fail 'cleanup preview changed directories'
wt clean example --apply
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'cleanup did not leave the removed checkout'

cd "$test_root/from"
wt trunk update >"$test_root/output"
[[ "$PWD" == "$test_root/from" ]] || fail 'trunk update navigated'
wt trunk
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'trunk navigation did not enter primary'

# Pull updates the current worktree without moving out of a nested directory.
cd "$test_root/from"
for subcommand in pull refresh; do
    wt "$subcommand" >"$test_root/output"
    [[ "$PWD" == "$test_root/from" ]] || fail "$subcommand changed directories"
    [[ "$(cat "$WT_TEST_ARGUMENTS")" == "$subcommand" ]] || fail "$subcommand was altered"
    [[ "$(cat "$test_root/output")" == "$WT_TEST_RESULT" ]] || fail "$subcommand output was hidden"
done

# A late journal/catalog failure can occur after cleanup removes our CWD.
cd "$test_root/from"
export WT_TEST_REMOVE_ROOT="$test_root/from" WT_TEST_STATUS=17
if wt clean example --apply >"$test_root/output" 2>"$test_root/error"; then
    fail 'late cleanup failure succeeded'
else
    [[ "$?" -eq 17 ]] || fail 'late cleanup failure status was lost'
fi
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'late cleanup failure left the shell in a deleted directory'
unset WT_TEST_REMOVE_ROOT
WT_TEST_STATUS=0
printf 'PASS: wt shell navigation\n'
