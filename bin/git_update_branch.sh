#!/usr/bin/env bash

set -euo pipefail

script_name=${0##*/}

usage() {
    cat <<EOF
Usage: $script_name [BRANCH]

Update BRANCH from its configured remote-tracking branch. BRANCH defaults to
the repository's default branch.

When BRANCH is checked out, the command run is:
  git pull REMOTE REMOTE_BRANCH:BRANCH

Otherwise, BRANCH is fast-forwarded without changing the current checkout:
  git fetch REMOTE REMOTE_BRANCH:BRANCH

BRANCH must already exist locally. A non-current branch that has diverged from
its remote branch is left unchanged so it can be reconciled explicitly.
EOF
}

die() {
    printf '%s: %s\n' "$script_name" "$*" >&2
    exit 2
}

run() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    "$@"
}

add_default_candidate() {
    local candidate="$1"
    local existing

    for existing in "${default_candidates[@]}"; do
        [[ "$existing" == "$candidate" ]] && return
    done
    default_candidates+=("$candidate")
}

resolve_default_branch() {
    local current_branch="$1"
    local head_ref target remote_prefix candidate
    local tracking_remote remote_head_output value name
    local remote
    local remotes=()
    local default_candidates=()

    while IFS=$'\t' read -r head_ref target; do
        [[ -n "$target" && "$head_ref" == refs/remotes/*/HEAD ]] || continue

        remote_prefix=${head_ref%/HEAD}/
        [[ "$target" == "$remote_prefix"* ]] || continue

        candidate=${target#"$remote_prefix"}
        [[ -n "$candidate" ]] && add_default_candidate "$candidate"
    done < <(git for-each-ref --format='%(refname)%09%(symref)' refs/remotes)

    if ((${#default_candidates[@]} == 1)); then
        printf '%s\n' "${default_candidates[0]}"
        return
    fi
    if ((${#default_candidates[@]} > 1)); then
        printf "%s: remotes disagree about the default branch:" "$script_name" >&2
        printf " '%s'" "${default_candidates[@]}" >&2
        printf '. Pass BRANCH explicitly.\n' >&2
        return 1
    fi

    tracking_remote=""
    if [[ -n "$current_branch" ]]; then
        tracking_remote=$(git for-each-ref \
            --format='%(upstream:remotename)' \
            "refs/heads/$current_branch")
    fi

    if [[ -z "$tracking_remote" || "$tracking_remote" == "." ]]; then
        while IFS= read -r remote; do
            remotes+=("$remote")
        done < <(git remote)

        if ((${#remotes[@]} != 1)); then
            printf '%s: no cached remote default branch and no single remote to query. Pass BRANCH explicitly.\n' \
                "$script_name" >&2
            return 1
        fi
        tracking_remote=${remotes[0]}
    fi

    if ! remote_head_output=$(git ls-remote --symref "$tracking_remote" HEAD); then
        printf "%s: could not query remote '%s' for its default branch. Pass BRANCH explicitly.\n" \
            "$script_name" "$tracking_remote" >&2
        return 1
    fi

    while IFS=$'\t' read -r value name; do
        if [[ "$name" == "HEAD" && "$value" == "ref: refs/heads/"* ]]; then
            candidate=${value#"ref: refs/heads/"}
            if [[ -n "$candidate" ]]; then
                printf '%s\n' "$candidate"
                return
            fi
        fi
    done <<<"$remote_head_output"

    printf "%s: remote '%s' did not advertise a default branch. Pass BRANCH explicitly.\n" \
        "$script_name" "$tracking_remote" >&2
    return 1
}

case "${1:-}" in
-h | --help)
    if [[ $# -ne 1 ]]; then
        usage >&2
        exit 2
    fi
    usage
    exit 0
    ;;
esac

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "not inside a Git work tree"
fi

current_branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || :)

if [[ $# -eq 1 ]]; then
    branch=$1
elif ! branch=$(resolve_default_branch "$current_branch"); then
    exit 2
fi

if ! git check-ref-format --branch "$branch" >/dev/null 2>&1; then
    die "invalid branch name: '$branch'"
fi
if ! git show-ref --verify --quiet "refs/heads/$branch"; then
    die "local branch not found: '$branch'"
fi

upstream_info=$(git for-each-ref \
    --format='%(upstream:remotename)%09%(upstream:remoteref)' \
    "refs/heads/$branch")
IFS=$'\t' read -r remote remote_ref <<<"$upstream_info"

if [[ -z "$remote" || -z "$remote_ref" ]]; then
    die "branch '$branch' has no configured remote-tracking branch"
fi
if [[ "$remote" == "." ]]; then
    die "branch '$branch' tracks another local branch, not a remote branch"
fi
if [[ "$remote_ref" != refs/heads/* ]]; then
    die "branch '$branch' tracks unsupported ref '$remote_ref'"
fi
if ! git remote get-url "$remote" >/dev/null 2>&1; then
    die "configured remote '$remote' does not exist"
fi

remote_branch=${remote_ref#refs/heads/}
if [[ "$branch" == "$current_branch" ]]; then
    run git pull "$remote" "$remote_branch:$branch"
else
    run git fetch "$remote" "$remote_branch:$branch"
fi
