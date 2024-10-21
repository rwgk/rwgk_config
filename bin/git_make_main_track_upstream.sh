#! /bin/bash
set -x
git remote add upstream "$1"
git fetch upstream
git branch "$2" --set-upstream-to upstream/"$2"
