#!/bin/bash

set -euo pipefail

# Ensure MY_GITHUB_USERNAME is defined
if [[ -z "${MY_GITHUB_USERNAME:-}" ]]; then
    echo "Error: Please set MY_GITHUB_USERNAME in your environment (e.g. in .bashrc)"
    exit 1
fi

# Check for input
if [[ $# -ne 1 ]]; then
    echo "Usage: $(basename "$0") <upstream-git-url>"
    echo " E.g.: $(basename "$0") https://github.com/pybind/pybind11.git"
    exit 1
fi

UPSTREAM_URL="$1"
REPO_NAME=$(basename -s .git "$UPSTREAM_URL")
FORK_URL="https://github.com/${MY_GITHUB_USERNAME}/${REPO_NAME}.git"

echo "Cloning upstream: $UPSTREAM_URL"
git clone "$UPSTREAM_URL"
cd "$REPO_NAME"

echo "Renaming 'origin' to 'upstream'"
git remote rename origin upstream

echo "Adding fork as 'origin': $FORK_URL"
git remote add origin "$FORK_URL"

echo "Done. Your remotes:"
git remote -v
