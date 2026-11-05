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

for request in pr mr; do
    cd "$test_root/from"
    WT_TEST_RESULT="https://forge.example/project/$request/42"
    wt "$request" >"$test_root/output"
    [[ "$PWD" == "$test_root/from" ]] || fail 'URL lookup changed directories'
    [[ "$(cat "$WT_TEST_ARGUMENTS")" == "$request" ]] || fail 'URL lookup requested a navigation path'
    [[ "$(cat "$test_root/output")" == "$WT_TEST_RESULT" ]] || fail 'URL lookup did not print exactly the saved URL'
done
WT_TEST_RESULT="$test_root/destination with spaces"

# Stack overviews print in place without asking the engine for a navigation path.
for action in '' view; do
    cd "$test_root/from"
    if [[ -n "$action" ]]; then
        wt stack "$action" >"$test_root/output"
        expected=$'stack\n'"$action"
    else
        wt stack >"$test_root/output"
        expected=stack
    fi
    [[ "$PWD" == "$test_root/from" ]] || fail 'stack overview changed directories'
    [[ "$(cat "$WT_TEST_ARGUMENTS")" == "$expected" ]] || fail 'stack overview requested a navigation path'
    [[ "$(cat "$test_root/output")" == "$WT_TEST_RESULT" ]] || fail 'stack overview output was hidden'
done

cd "$test_root/from"
wt new example
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'new did not enter its worktree'
[[ "$(tail -1 "$WT_TEST_ARGUMENTS")" == --print-path ]] || fail 'navigation did not request a path'

for request in pr mr; do
    cd "$test_root/from"
    wt "$request" 607
    [[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'request selector did not enter its worktree'
    [[ "$(head -1 "$WT_TEST_ARGUMENTS")" == "$request" ]] || fail 'request command was altered'
    [[ "$(tail -1 "$WT_TEST_ARGUMENTS")" == --print-path ]] || fail 'request navigation did not ask for a path'
done

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

# Housekeeping shares cleanup navigation while preview and JSON stay put.
cd "$test_root/from"
wt housekeeping >"$test_root/output"
[[ "$PWD" == "$test_root/from" ]] || fail 'housekeeping preview changed directories'
wt housekeeping --apply --json >"$test_root/output"
[[ "$PWD" == "$test_root/from" ]] || fail 'housekeeping JSON changed directories'
wt housekeeping --apply --discard-builds >"$test_root/output"
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'housekeeping apply did not leave the removed checkout'
[[ "$(head -1 "$WT_TEST_ARGUMENTS")" == housekeeping ]] || fail 'housekeeping was altered'

# Pull updates the current worktree without moving out of a nested directory.
cd "$test_root/from"
wt pull >"$test_root/output"
[[ "$PWD" == "$test_root/from" ]] || fail 'pull changed directories'
[[ "$(cat "$WT_TEST_ARGUMENTS")" == pull ]] || fail 'pull was altered'
[[ "$(cat "$test_root/output")" == "$WT_TEST_RESULT" ]] || fail 'pull output was hidden'

# Synchronization commands preserve the current directory and all mode flags.
for mode in --rebase --reset-to-remote-head --continue --abort; do
    wt pull "$mode" >"$test_root/output"
    [[ "$PWD" == "$test_root/from" ]] || fail 'pull mode changed directories'
    [[ "$(tail -1 "$WT_TEST_ARGUMENTS")" == "$mode" ]] || fail 'pull mode was altered'
done
wt push >"$test_root/output"
[[ "$PWD" == "$test_root/from" ]] || fail 'push changed directories'
[[ "$(cat "$WT_TEST_ARGUMENTS")" == push ]] || fail 'push was altered'

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

# A partially failed housekeeping pass must also recover from a removed CWD.
mkdir -- "$test_root/housekeeping-from"
cd "$test_root/housekeeping-from"
export WT_TEST_REMOVE_ROOT="$test_root/housekeeping-from" WT_TEST_STATUS=19
if wt housekeeping --apply >"$test_root/output" 2>"$test_root/error"; then
    fail 'late housekeeping failure succeeded'
else
    [[ "$?" -eq 19 ]] || fail 'late housekeeping failure status was lost'
fi
[[ "$PWD" == "$WT_TEST_RESULT" ]] || fail 'late housekeeping failure left the shell in a deleted directory'
unset WT_TEST_REMOVE_ROOT
WT_TEST_STATUS=0

# Standalone deletion completes exact local branch names, even when a tag has
# the same name. Extract only the shared completer and its registration.
awk '/^_complete_local_git_branches\(\) \{/ { copy=1 } copy { print } copy && /^}$/ { exit }' \
    "$repo_root/bashrc" >"$test_root/completion.sh"
awk '$1 == "complete" && $NF == "git_branch_D_track_hash.py" { print }' \
    "$repo_root/bashrc" >>"$test_root/completion.sh"
source "$test_root/completion.sh"
[[ "$(complete -p git_branch_D_track_hash.py)" == *'-F _complete_local_git_branches '* ]] || fail 'standalone branch completion not registered'
mkdir -- "$test_root/completion"
cd "$test_root/completion"
git init --quiet --initial-branch=main
git -c user.name='Shell fixture' -c user.email='fixture@example.invalid' \
    -c commit.gpgsign=false -c core.hooksPath=/dev/null \
    commit --quiet --allow-empty -m Base
git branch rwgk/topic-name
git tag rwgk/topic-name
git branch 'upstream→topic'
COMP_WORDS=(git_branch_D_track_hash.py rwgk/)
COMP_CWORD=1
_complete_local_git_branches
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == rwgk/topic-name ]] || fail 'slash branch completion was ambiguous'
COMP_WORDS=(git_branch_D_track_hash.py 'upstream→')
_complete_local_git_branches
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == 'upstream→topic' ]] || fail 'unicode branch completion failed'
printf 'PASS: standalone branch deletion completion\n'

# Complete managed selectors locally, with option values and positional arguments
# excluded. The stub above supplies catalog JSON without contacting a forge.
awk '/^_wt_complete\(\) \{/ { copy=1 } copy { print } copy && /^}$/ { exit }' \
    "$repo_root/bashrc" >"$test_root/wt-completion.sh"
source "$test_root/wt-completion.sh"
git branch legacy/topic
git branch 123
WT_TEST_RESULT='{
  "config": {"provider": "github", "upstream_repo": "Acme/project"},
  "branches": [
    {"branch": "rwgk/topic-name", "pr": {"number": 3182, "base_repo": "Acme/project"}},
    {"branch": "rwgk/123"}
  ]
}'
complete_wt() {
    COMP_WORDS=("$@")
    COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
    _wt_complete
}
for command in go path clean drop; do
    complete_wt wt "$command" 31
    [[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == 3182 ]] || fail "bare-number completion failed for $command"
done
complete_wt wt clean pr31
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == pr3182 ]] || fail 'PR alias completion failed'
complete_wt wt path topic
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == topic-name ]] || fail 'shortened branch completion failed'
complete_wt wt go rwgk/topic
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == rwgk/topic-name ]] || fail 'full branch completion failed'
complete_wt wt drop --local-only legacy/
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == legacy/topic ]] || fail 'unpaired local branch completion failed'
complete_wt wt go legacy/
[[ "${#COMPREPLY[@]}" -eq 0 ]] || fail 'navigation offered an unmanaged branch'
complete_wt wt drop 12
[[ "${#COMPREPLY[@]}" -eq 0 ]] || fail 'numeric local branch was offered as a request number'
complete_wt wt name rwgk/topic
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == rwgk/topic-name ]] || fail 'wt name completion was ambiguous with a tag'
git branch 'rwgk/$(touch${IFS}completion-injected)'
complete_wt wt name 'rwgk/$'
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == 'rwgk/$(touch${IFS}completion-injected)' ]] || fail 'literal branch syntax was not preserved'
[[ ! -e completion-injected ]] || fail 'completion evaluated shell syntax in a branch name'
COMP_WORDS=(git_branch_D_track_hash.py 'rwgk/$')
COMP_CWORD=1
_complete_local_git_branches
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == 'rwgk/$(touch${IFS}completion-injected)' ]] || fail 'standalone completion changed literal branch syntax'
[[ ! -e completion-injected ]] || fail 'standalone completion evaluated shell syntax in a branch name'

WT_TEST_RESULT='{
  "config": {"provider": "gitlab", "upstream_repo": "gitlab.example/group/project"},
  "branches": [
    {"branch": "rwgk/review-mr607", "pr": {"number": 607, "base_repo": "gitlab.example/group/project"}}
  ]
}'
complete_wt wt clean mr6
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == mr607 ]] || fail 'MR alias completion failed'
complete_wt wt go 6
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == 607 ]] || fail 'bare MR number completion failed'
complete_wt wt go pr6
[[ "${#COMPREPLY[@]}" -eq 0 ]] || fail 'GitLab completion offered a GitHub alias'

for argument in 'clean --resume' 'publish --title' 'publish --body-file' 'stack pull --stack'; do
    read -r -a arguments <<<"$argument"
    rm -f -- "$WT_TEST_ARGUMENTS"
    complete_wt wt "${arguments[@]}" ''
    [[ "${#COMPREPLY[@]}" -eq 0 ]] || fail "option value received selector completion: $argument"
    [[ ! -e "$WT_TEST_ARGUMENTS" ]] || fail "option value queried branch metadata: $argument"
done
complete_wt wt clean --resume saved-record ''
[[ " ${COMPREPLY[*]} " == *' --apply '* ]] || fail 'resume did not offer cleanup options'
complete_wt wt clean mr607 ''
[[ " ${COMPREPLY[*]} " == *' --apply '* && " ${COMPREPLY[*]} " != *' mr607 '* ]] || fail 'consumed selector received another selector'
complete_wt wt clean --apply mr6
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == mr607 ]] || fail 'option before selector prevented completion'
complete_wt wt stack go do
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == down ]] || fail 'stack direction completion failed'
complete_wt wt stack go down ''
[[ " ${COMPREPLY[*]} " != *' down '* ]] || fail 'consumed stack direction received another direction'
complete_wt wt stack vi
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == view ]] || fail 'stack overview completion was missing'
for argument in 'stack --' 'stack view --'; do
    read -r -a arguments <<<"$argument"
    complete_wt wt "${arguments[@]}"
    [[ " ${COMPREPLY[*]} " == *' --json '* && " ${COMPREPLY[*]} " != *' --print-path '* ]] || fail 'stack overview offered invalid navigation options'
done
printf 'PASS: wt selector and option completion\n'
printf 'PASS: wt shell navigation\n'
