#!/bin/bash

set -euo pipefail

readonly PROGRAM=${0##*/}

usage() {
    cat <<EOF
Usage: $PROGRAM [WORKTREE ...]

Show the shared gh stack catalog for each repository, once per repository.
With no arguments, inspect the current directory. Compare PR order, merged
status and trunk with GitHub. This command is read-only and targets gh stack
v0.2.0; use gh_stack_check.py to verify branch tips and saved replay bases.
EOF
}

describe_stack() {
    local repository="$1" stack_json="$2"
    local stack_number trunk branches_heading remote_json="" remote_state
    local total_prs merged_prs

    # v0.2.0 checkout imports can leave the catalog's repository field empty.
    repository=$(jq -r --arg repository "$repository" '
        if $repository != "" then $repository else
            [.branches[].pullRequest.url // empty
             | capture("^https://github.com/(?<repo>[^/]+/[^/]+)/pull/[0-9]+$")
             | "github.com:" + .repo] | unique
            | if length == 1 then .[0] else "" end
        end' <<<"$stack_json")
    stack_number=$(jq -r '.number // empty' <<<"$stack_json")
    trunk=$(jq -r '.trunk.branch' <<<"$stack_json")

    if [[ -z "$stack_number" ]]; then
        branches_heading="Branches (local-only snapshot):"
    elif [[ "$repository" != github.com:* ]] ||
        ! remote_json=$(gh api "repos/${repository#github.com:}/stacks/$stack_number" 2>/dev/null); then
        remote_json=""
        branches_heading="Branches (local snapshot; remote state unavailable):"
    elif jq -e --argjson local "$stack_json" '
        .base.ref == $local.trunk.branch and
        ([.pull_requests[] | {
            branch: .head.ref,
            pull_request: .number,
            merged: (.merged_at != null)
        }] == [$local.branches[] | {
            branch: .branch,
            pull_request: (.pullRequest.number // null),
            merged: (.pullRequest.merged // false)
        }])' <<<"$remote_json" >/dev/null; then
        branches_heading="Branches (local snapshot; matches GitHub):"
    else
        branches_heading="Branches (stale local snapshot):"
    fi

    printf 'Repository: %s\n' "${repository:-unknown}"
    printf 'Stack:      %s\n' "${stack_number:-local-only}"
    printf 'Trunk:      %s\n' "$trunk"
    echo "$branches_heading"
    jq -r '
        .branches[]
        | "  - \(.branch)"
          + (if .pullRequest.number then
               "  PR #\(.pullRequest.number)"
               + (if .pullRequest.merged then "  [merged]" else "" end)
             else "" end)
        ' <<<"$stack_json"

    if [[ -z "$remote_json" ]]; then
        return
    fi
    remote_state=$(jq -r 'if .open then "open" else "closed" end' <<<"$remote_json")
    total_prs=$(jq '.pull_requests | length' <<<"$remote_json")
    merged_prs=$(jq '[.pull_requests[] | select(.merged_at != null)] | length' <<<"$remote_json")
    printf 'GitHub stack: %s [%s; %s/%s PRs merged]\n' \
        "$stack_number" "$remote_state" "$merged_prs" "$total_prs"
    if [[ "$remote_state" == "closed" ]]; then
        printf 'Hint: In this repository, run: gh stack unstack %s --local\n' "$stack_number"
    fi
}

inspect_repository() {
    local tracking_file="$1"
    local catalog repository stacks stack_json

    printf 'Shared catalog: %s\n' "$tracking_file"
    if [[ ! -f "$tracking_file" ]]; then
        echo "No local gh stack tracking found."
        return
    fi
    catalog=$(jq -c '
        if .schemaVersion == 1 then .
        else error("unsupported gh stack catalog schema") end' "$tracking_file")
    repository=$(jq -r '.repository // ""' <<<"$catalog")
    stacks=$(jq -c '.stacks[]' <<<"$catalog")
    if [[ -z "$stacks" ]]; then
        echo "No locally tracked stacks."
        return
    fi
    while IFS= read -r stack_json; do
        echo
        describe_stack "$repository" "$stack_json"
    done <<<"$stacks"
}

main() {
    local path common_dir status=0
    local -A inspected_common_dirs=()

    unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR
    if (($# == 0)); then
        set -- "$PWD"
    fi
    for path in "$@"; do
        if ! common_dir=$(git -C "$path" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
            printf '%s: not a Git repository: %s\n' "$PROGRAM" "$path" >&2
            status=1
            continue
        fi
        if [[ -n ${inspected_common_dirs["$common_dir"]:-} ]]; then
            continue
        fi
        inspected_common_dirs["$common_dir"]=1
        inspect_repository "$common_dir/gh-stack"
        echo
    done
    return "$status"
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    exit 0
fi

main "$@"
