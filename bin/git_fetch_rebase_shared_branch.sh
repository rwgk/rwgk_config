#! /usr/bin/bash
set -e -x
SHARED_BRANCH_NAME="$1"
git show-ref --verify --quiet refs/heads/"$SHARED_BRANCH_NAME"
git fetch upstream
git checkout master
git merge upstream/master
git checkout "$SHARED_BRANCH_NAME"
git merge upstream/"$SHARED_BRANCH_NAME"
git rebase master
git push --force-with-lease upstream "$SHARED_BRANCH_NAME"
