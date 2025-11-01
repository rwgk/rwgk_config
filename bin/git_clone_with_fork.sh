#!/bin/bash

set -euo pipefail

SCRIPT_NAME=$(basename "$0")

usage() {
    cat <<EOF
Usage:
  $SCRIPT_NAME <upstream-git-url> [fork-git-url] [--branch <branch-name>]

Examples:
  $SCRIPT_NAME https://github.com/pybind/pybind11.git
  $SCRIPT_NAME https://github.com/pybind/pybind11.git https://github.com/rwgk/pybind11.git
  $SCRIPT_NAME https://github.com/pybind/pybind11.git --branch specific-upstream-branch
  $SCRIPT_NAME https://github.com/pybind/pybind11.git https://github.com/rwgk/pybind11.git --branch specific-upstream-branch
EOF
}

# Parse args
UPSTREAM_URL=""
FORK_URL=""
BRANCH_NAME=""

# Collect at most two positionals (upstream, optional fork) and handle flags anywhere.
positional_count=0
while [[ $# -gt 0 ]]; do
    case "$1" in
    -b | --branch)
        shift
        [[ $# -gt 0 ]] || {
            echo "Error: --branch requires a value"
            usage
            exit 1
        }
        BRANCH_NAME="$1"
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    --) # end of options
        shift
        while [[ $# -gt 0 ]]; do
            if [[ $positional_count -eq 0 ]]; then
                UPSTREAM_URL="$1"
            elif [[ $positional_count -eq 1 ]]; then
                FORK_URL="$1"
            else
                echo "Error: too many positional arguments"
                usage
                exit 1
            fi
            positional_count=$((positional_count + 1))
            shift
        done
        ;;
    -*)
        echo "Error: unknown option: $1"
        usage
        exit 1
        ;;
    *)
        if [[ $positional_count -eq 0 ]]; then
            UPSTREAM_URL="$1"
        elif [[ $positional_count -eq 1 ]]; then
            FORK_URL="$1"
        else
            echo "Error: too many positional arguments"
            usage
            exit 1
        fi
        positional_count=$((positional_count + 1))
        shift
        ;;
    esac
done

# Validate required arg
if [[ -z "$UPSTREAM_URL" ]]; then
    echo "Error: missing <upstream-git-url>"
    usage
    exit 1
fi

export GIT_PAGER=

REPO_NAME=$(basename -s .git "$UPSTREAM_URL")

# Determine FORK_URL (if not explicitly provided)
if [[ -z "${FORK_URL:-}" ]]; then
    if [[ -z "${MY_GITHUB_USERNAME:-}" ]]; then
        echo "Error: MY_GITHUB_USERNAME must be set in your environment (e.g. in .bashrc) if no fork URL is provided."
        exit 1
    fi
    FORK_URL="https://github.com/${MY_GITHUB_USERNAME}/${REPO_NAME}.git"
fi

# Choose clone directory
if [[ -n "$BRANCH_NAME" ]]; then
    CLONE_DIR="$BRANCH_NAME"
else
    CLONE_DIR="$REPO_NAME"
fi

echo "Cloning upstream: $UPSTREAM_URL"
if [[ -n "$BRANCH_NAME" ]]; then
    echo "Using branch: $BRANCH_NAME"
    git clone --branch "$BRANCH_NAME" "$UPSTREAM_URL" "$CLONE_DIR"
else
    git clone "$UPSTREAM_URL" "$CLONE_DIR"
fi
cd "$CLONE_DIR"

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

# Add pre-push hook
SCRIPT_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
HOOK_SOURCE="${SCRIPT_DIR}/git_stuff/pre-push"
HOOK_DEST=".git/hooks/pre-push"

if [[ ! -f "$HOOK_SOURCE" ]]; then
    echo "Warning: Hook source file not found: $HOOK_SOURCE"
    echo "Skipping pre-push hook installation."
elif [[ -f "$HOOK_DEST" ]]; then
    echo "Warning: Hook destination already exists: $HOOK_DEST"
    echo "Skipping pre-push hook installation to avoid overwriting."
else
    if cp -a "$HOOK_SOURCE" "$HOOK_DEST"; then
        chmod +x "$HOOK_DEST"
        echo "✓ Installed pre-push hook from $HOOK_SOURCE"
    else
        echo "Error: Failed to copy pre-push hook"
        exit 1
    fi
fi
echo
