#!/bin/bash

# https://chatgpt.com/share/67e8331b-6700-8008-9a09-31c9f1611a84  2025-03-29

set -euo pipefail

# Show PR URL for a branch name in the current repo context
show_pr_for_branch() {
    local branch="$1"

    local pr_info
    pr_info=$(gh pr list \
        --head "$branch" \
        --state all \
        --limit 1000 \
        --json number,url \
        --jq '.[0] // empty')

    if [[ -n "$pr_info" ]]; then
        local pr_url
        pr_url=$(echo "$pr_info" | jq -r '.url')
        echo "$branch → $pr_url"
    else
        echo "$branch → No PR found"
    fi
}

# Entry point
if [[ $# -eq 0 ]]; then
    show_pr_for_branch "$(git branch --show-current)"
else
    for branch in "$@"; do
        show_pr_for_branch "$branch"
    done
fi
