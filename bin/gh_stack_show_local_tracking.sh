#!/bin/bash

set -euo pipefail

readonly PROGRAM=${0##*/}

usage() {
    cat <<EOF
Usage: $PROGRAM [WORKTREE ...]

Show local gh stack tracking associated with each worktree's repository. A
linked worktree is resolved to its main worktree, and each main worktree is
inspected only once. With no arguments, inspect the current directory. This
command is read-only.
EOF
}

describe_tracking_file() {
    local tracking_file="$1"
    local common_dir="$2"
    local main_worktree="$3"
    local worktree_kind worktree_path gitdir_file repository stack_json

    if [[ "$tracking_file" == "$common_dir/gh-stack" ]]; then
        worktree_kind="main worktree"
        worktree_path=$main_worktree
    else
        worktree_kind="linked worktree"
        gitdir_file="$(dirname "$tracking_file")/gitdir"
        if [[ -f "$gitdir_file" ]]; then
            worktree_path=$(<"$gitdir_file")
            worktree_path=${worktree_path%/.git}
        else
            worktree_path="unknown"
        fi
    fi

    repository=$(jq -r '.repository' "$tracking_file")
    while IFS= read -r stack_json; do
        describe_stack \
            "$tracking_file" \
            "$worktree_kind" \
            "$worktree_path" \
            "$repository" \
            "$stack_json"
    done < <(jq -c '.stacks[]' "$tracking_file")
}

get_remote_stack() {
    local repository="$1"
    local stack_number="$2"
    local repository_name

    if [[ "$repository" != github.com:* ]] || ! command -v gh >/dev/null 2>&1; then
        return 1
    fi
    repository_name=${repository#github.com:}
    gh api "repos/$repository_name/stacks/$stack_number" 2>/dev/null
}

describe_stack() {
    local tracking_file="$1"
    local worktree_kind="$2"
    local worktree_path="$3"
    local repository="$4"
    local stack_json="$5"
    local stack_number trunk branches_heading remote_json remote_state
    local local_snapshot remote_snapshot remote_base total_prs merged_prs missing_branches

    stack_number=$(jq -r '.number // empty' <<<"$stack_json")
    trunk=$(jq -r '.trunk.branch' <<<"$stack_json")

    if [[ -z "$stack_number" ]]; then
        branches_heading="Branches (local-only snapshot):"
    elif ! remote_json=$(get_remote_stack "$repository" "$stack_number"); then
        branches_heading="Branches (local snapshot; remote state unavailable):"
    else
        local_snapshot=$(jq -Sc \
            '[.branches[] | {
                branch: .branch,
                pull_request: (.pullRequest.number // null),
                merged: (.pullRequest.merged // false)
            }] | sort_by(.branch)' <<<"$stack_json")
        remote_snapshot=$(jq -Sc \
            '[.pull_requests[] | {
                branch: .head.ref,
                pull_request: .number,
                merged: (.merged_at != null)
            }] | sort_by(.branch)' <<<"$remote_json")
        remote_base=$(jq -r '.base.ref' <<<"$remote_json")

        if [[ "$local_snapshot" == "$remote_snapshot" && "$trunk" == "$remote_base" ]]; then
            branches_heading="Branches (local snapshot; matches GitHub):"
        else
            branches_heading="Branches (stale local snapshot):"
        fi
    fi

    printf 'Tracking file: %s\n' "$tracking_file"
    printf 'Worktree:     %s: %s\n' "$worktree_kind" "$worktree_path"
    printf 'Repository:   %s\n' "$repository"
    printf 'Stack:        %s\n' "${stack_number:-local-only}"
    printf 'Trunk:        %s\n' "$trunk"
    echo "$branches_heading"
    jq -r '
        .branches[]
        | "  - \(.branch)"
          + (if .pullRequest.number then
               "  PR #\(.pullRequest.number)"
               + (if .pullRequest.merged then "  [merged]" else "" end)
             else "" end)
        ' <<<"$stack_json"

    if [[ -z ${remote_json:-} ]]; then
        return
    fi

    remote_state=$(jq -r 'if .open then "open" else "closed" end' <<<"$remote_json")
    total_prs=$(jq '.pull_requests | length' <<<"$remote_json")
    merged_prs=$(jq '[.pull_requests[] | select(.merged_at != null)] | length' <<<"$remote_json")
    if ((total_prs > 0 && merged_prs == total_prs)); then
        printf 'GitHub stack: %s [%s; all %s PRs merged]\n' \
            "$stack_number" "$remote_state" "$total_prs"
    else
        printf 'GitHub stack: %s [%s; %s/%s PRs merged]\n' \
            "$stack_number" "$remote_state" "$merged_prs" "$total_prs"
    fi

    missing_branches=$(jq -r --argjson local "$stack_json" '
        .pull_requests[]
        | select(.head.ref as $branch
            | ($local.branches | map(.branch) | index($branch)) == null)
        | "  - \(.head.ref)  PR #\(.number)"
          + (if .merged_at then "  [merged]" else "" end)
        ' <<<"$remote_json")
    if [[ -n "$missing_branches" ]]; then
        echo "Missing from local snapshot:"
        echo "$missing_branches"
    fi
    if [[ "$remote_state" == "closed" ]]; then
        printf 'Hint: From %s, run: gh stack unstack %s --local\n' \
            "$worktree_path" "$stack_number"
    fi
}

inspect_main_worktree() {
    local main_worktree="$1"
    local common_dir="$2"
    local tracking_file
    local -a tracking_files=()

    if [[ -f "$common_dir/gh-stack" ]]; then
        tracking_files+=("$common_dir/gh-stack")
    fi
    for tracking_file in "$common_dir"/worktrees/*/gh-stack; do
        if [[ -f "$tracking_file" ]]; then
            tracking_files+=("$tracking_file")
        fi
    done

    printf 'Main worktree: %s\n' "$main_worktree"
    if ((${#tracking_files[@]} == 0)); then
        echo "No local gh stack tracking found."
        return
    fi

    for tracking_file in "${tracking_files[@]}"; do
        echo
        describe_tracking_file "$tracking_file" "$common_dir" "$main_worktree"
    done
}

main() {
    local path main_worktree common_dir status=0
    local -a paths
    local -A inspected_common_dirs=()

    if (($# == 0)); then
        paths=("$PWD")
    else
        paths=("$@")
    fi

    for path in "${paths[@]}"; do
        if ! common_dir=$(git -C "$path" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
            printf '%s: not a Git worktree: %s\n' "$PROGRAM" "$path" >&2
            status=1
            continue
        fi
        if [[ -n ${inspected_common_dirs["$common_dir"]:-} ]]; then
            continue
        fi
        inspected_common_dirs["$common_dir"]=1

        main_worktree=$(git -C "$path" worktree list --porcelain | sed -n '1s/^worktree //p')
        if [[ -z "$main_worktree" ]]; then
            printf '%s: failed to locate the main worktree for: %s\n' "$PROGRAM" "$path" >&2
            status=1
            continue
        fi

        inspect_main_worktree "$main_worktree" "$common_dir"
        echo
    done

    return "$status"
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    exit 0
fi

main "$@"
