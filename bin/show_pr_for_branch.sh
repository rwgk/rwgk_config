#!/bin/bash

# https://chatgpt.com/share/67d5cb11-50a8-8008-a729-a8a614bf7c76  2025-03-15

set -euo pipefail

show_pr_for_branch() {
    local branch="$1"
    PR_NUMBER=$(gh pr list --head "$branch" --json number --jq '.[0].number')

    if [[ -n "$PR_NUMBER" ]]; then
        echo "$branch → https://github.com/pybind/pybind11/pull/$PR_NUMBER"
    else
        echo "$branch → No open PR found"
    fi
}

if [[ $# -eq 0 ]]; then
    show_pr_for_branch "$(git branch --show-current)"
else
    for branch in "$@"; do
        show_pr_for_branch "$branch"
    done
fi
