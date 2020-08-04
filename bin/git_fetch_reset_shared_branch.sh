#! /usr/bin/bash
set -e -x
SHARED_BRANCH_NAME="$1"
git show-ref --verify --quiet refs/heads/"$SHARED_BRANCH_NAME"
git fetch upstream
git checkout "$SHARED_BRANCH_NAME"
git reset upstream/"$SHARED_BRANCH_NAME"
