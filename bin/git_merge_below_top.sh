#!/bin/bash
set -euo pipefail

usage() {
    echo "Usage: $(basename "$0") <branch-name>"
    exit 1
}

if [[ $# -ne 1 ]]; then
    echo "Error: Expected exactly one argument (branch name)."
    usage
fi

BRANCH="$1"

# Verify the branch exists and is reachable
if ! git rev-parse --verify --quiet "$BRANCH" >/dev/null; then
    echo "Error: Branch '$BRANCH' not found or not valid."
    exit 1
fi

# Save top commit as patch
TMP_PATCH="/tmp/git_merge_below_top_$(date '+%Y-%m-%d+%H%M%S').patch"
git format-patch -1 HEAD --stdout >"$TMP_PATCH"

# Drop top commit
git reset --hard HEAD^

# Merge the target branch
git merge "$BRANCH"

# Re-apply saved commit
git am "$TMP_PATCH"
