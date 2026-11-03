#!/bin/bash
# Session commands and completion work outside repositories without Git queries.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT
mkdir -p "$test_root/bin" "$test_root/non-repository" "$test_root/alternative"
awk '/^wt\(\) \{/ { copy=1 } copy { print } copy && /^}$/ { exit }' \
    "$repo_root/bashrc" >"$test_root/functions.sh"
awk '/^_wt_complete\(\) \{/ { copy=1 } copy { print } copy && /^}$/ { exit }' \
    "$repo_root/bashrc" >>"$test_root/functions.sh"
source "$test_root/functions.sh"
cat >"$test_root/bin/git-wt" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" >"$WT_NICKNAME_ARGUMENTS"
printf '%s\n' '{"nicknames":[{"name":"wtdesign","session_id":"one"},{"name":"wt-review","session_id":"two"},{"name":"other","session_id":"three"}]}'
STUB
cat >"$test_root/bin/git" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" >"$WT_NICKNAME_GIT_QUERIES"
exit 1
STUB
chmod +x "$test_root/bin/git-wt" "$test_root/bin/git"
export PATH="$test_root/bin:$PATH"
export WT_NICKNAME_ARGUMENTS="$test_root/arguments"
export WT_NICKNAME_GIT_QUERIES="$test_root/git-queries"
cd "$test_root/non-repository"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

complete_wt() {
    rm -f -- "$WT_NICKNAME_ARGUMENTS"
    COMP_WORDS=("$@")
    COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
    _wt_complete
}

# Nickname commands pass through without requesting paths or moving the shell.
for command in codex nickname; do
    wt "$command" wtdesign >"$test_root/output"
    [[ "$PWD" == "$test_root/non-repository" ]] || fail "$command changed directories"
    [[ "$(cat "$WT_NICKNAME_ARGUMENTS")" == "$command"$'\n'wtdesign ]] || fail "$command arguments were changed"
done
wt codex wtdesign --cwd "$test_root/alternative" >"$test_root/output"
[[ "$PWD" == "$test_root/non-repository" ]] || fail 'explicit session directory changed the invoking shell'
[[ "$(tail -1 "$WT_NICKNAME_ARGUMENTS")" == "$test_root/alternative" ]] || fail 'session cwd override was lost'
wt nickname --delete wtdesign >"$test_root/output"
[[ "$(cat "$WT_NICKNAME_ARGUMENTS")" == $'nickname\n--delete\nwtdesign' ]] || fail 'nickname deletion arguments were changed'

complete_wt wt nick
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == nickname ]] || fail 'nickname command was missing'
for command in codex nickname; do
    complete_wt wt "$command" wt
    [[ " ${COMPREPLY[*]} " == *' wtdesign '* && " ${COMPREPLY[*]} " == *' wt-review '* ]] || fail "$command did not complete saved names"
    [[ "$(cat "$WT_NICKNAME_ARGUMENTS")" == $'nickname\n--json' ]] || fail "$command requested the wrong metadata"
    complete_wt wt "$command" wtd
    [[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == wtdesign ]] || fail "$command prefix completion failed"
    complete_wt wt "$command" missing
    [[ "${#COMPREPLY[@]}" -eq 0 ]] || fail "$command offered a mismatched name"
done
complete_wt wt nickname --delete wtd
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == wtdesign ]] || fail 'nickname deletion did not complete a name'
complete_wt wt codex --list wtd
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == wtdesign ]] || fail 'list mode did not complete a name'
complete_wt wt codex --cwd "$test_root/alternative" wtd
[[ "${#COMPREPLY[@]}" -eq 1 && "${COMPREPLY[0]}" == wtdesign ]] || fail 'cwd option consumed the nickname slot'
complete_wt wt codex --cwd ''
[[ "${#COMPREPLY[@]}" -eq 0 && ! -e "$WT_NICKNAME_ARGUMENTS" ]] || fail 'cwd values received nickname completion'
complete_wt wt codex wtdesign ''
[[ " ${COMPREPLY[*]} " == *' --list '* && " ${COMPREPLY[*]} " == *' --cwd '* ]] || fail 'consumed nickname did not offer session options'
[[ " ${COMPREPLY[*]} " != *' wtdesign '* && ! -e "$WT_NICKNAME_ARGUMENTS" ]] || fail 'consumed nickname offered another name'
for command in codex nickname; do
    complete_wt wt "$command" --
    [[ " ${COMPREPLY[*]} " == *' --json '* && " ${COMPREPLY[*]} " != *' --print-path '* ]] || fail "$command offered invalid output options"
done
complete_wt wt nickname --
[[ " ${COMPREPLY[*]} " == *' --delete '* ]] || fail 'nickname deletion flag was missing'
[[ ! -e "$WT_NICKNAME_GIT_QUERIES" ]] || fail 'session command completion queried Git'
printf 'PASS: wt nickname shell pass-through and completion\n'
