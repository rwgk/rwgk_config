#!/bin/bash
set -e
set -x
git checkout --orphan __EMPTY__
git rm -rf .
git commit --allow-empty -m "__EMPTY__ branch"
git push origin __EMPTY__
