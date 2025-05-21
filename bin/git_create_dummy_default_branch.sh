#!/bin/bash
set -e
set -x
git checkout --orphan dummy-default-branch
git rm -rf .
git commit --allow-empty -m "Dummy Default Branch"
git push origin dummy-default-branch
