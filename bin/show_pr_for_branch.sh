#!/bin/bash

# https://chatgpt.com/share/67e8331b-6700-8008-9a09-31c9f1611a84  2025-03-29

set -euo pipefail

# Get the remote branch name for PR lookup
# If branch has an upstream, use that (strips remote prefix)
# Otherwise fall back to the local branch name
get_pr_branch_name() {
    local branch="$1"
    local upstream

    # Try to get upstream tracking branch (e.g., "b-pass/pytype-leaked-in-finalize")
    upstream=$(git rev-parse --abbrev-ref "$branch@{u}" 2>/dev/null) || true

    if [[ -n "$upstream" ]]; then
        # Extract branch name after the remote prefix (everything after first /)
        echo "${upstream#*/}"
    else
        echo "$branch"
    fi
}

# Show PR URL for a branch name in the current repo context
show_pr_for_branch() {
    local branch="$1"
    local pr_branch
    pr_branch=$(get_pr_branch_name "$branch")

    local pr_info
    pr_info=$(gh pr list \
        --head "$pr_branch" \
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
