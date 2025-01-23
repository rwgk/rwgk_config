#! /bin/bash
if [ $# -ne 2 ]; then
  echo "usage: $0 upstream_url main_branch_name  # exactly two arguments required, $# given."
  exit 1
fi
set -e
set -x
git remote add upstream "$1"
git fetch upstream
git branch "$2" --set-upstream-to upstream/"$2"
