#!/bin/bash

set -euo pipefail

SCRIPT_NAME=$(basename "$0")

# Check for input
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $SCRIPT_NAME <upstream-git-url> [fork-git-url]"
    echo " E.g.: $SCRIPT_NAME https://github.com/pybind/pybind11.git"
    echo "   or: $SCRIPT_NAME https://github.com/pybind/pybind11.git https://github.com/rwgk/pybind11.git"
    exit 1
fi

UPSTREAM_URL="$1"
REPO_NAME=$(basename -s .git "$UPSTREAM_URL")

# Determine FORK_URL
if [[ $# -eq 2 ]]; then
    FORK_URL="$2"
else
    if [[ -z "${MY_GITHUB_USERNAME:-}" ]]; then
        echo "Error: MY_GITHUB_USERNAME must be set in your environment (e.g. in .bashrc) if no fork URL is provided."
        exit 1
    fi
    FORK_URL="https://github.com/${MY_GITHUB_USERNAME}/${REPO_NAME}.git"
fi

echo "Cloning upstream: $UPSTREAM_URL"
git clone "$UPSTREAM_URL"
cd "$REPO_NAME"

echo "Renaming 'origin' to 'upstream'"
git remote rename origin upstream

echo "Adding fork as 'origin': $FORK_URL"
git remote add origin "$FORK_URL"
git fetch origin
echo

echo "Your remotes:"
git remote -v
echo

echo "Your branches:"
git branch --all
echo
