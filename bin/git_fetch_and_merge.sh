#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: git_fetch_and_merge.sh REMOTE_REPO [REMOTE_BRANCH]

Fetch REMOTE_BRANCH from REMOTE_REPO and fast-forward merge FETCH_HEAD
into the currently checked-out local branch.

REMOTE_BRANCH defaults to the current local branch name.

Example:
  git_fetch_and_merge.sh mmc:/wrk/forked/pybind11
  git_fetch_and_merge.sh mmc:/wrk/forked/pybind11 remote-branch-name
EOF
}

run() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    "$@"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
    usage >&2
    exit 2
fi

remote_repo=$1

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: not inside a git work tree" >&2
    exit 2
fi

if ! current_branch=$(git symbolic-ref --quiet --short HEAD); then
    echo "error: current checkout is detached; pass through a local branch checkout first" >&2
    exit 2
fi

remote_branch=${2:-$current_branch}

echo "Local branch:  $current_branch"
echo "Remote repo:   $remote_repo"
echo "Remote branch: $remote_branch"

run git fetch "$remote_repo" "$remote_branch"
run git merge --ff-only FETCH_HEAD
