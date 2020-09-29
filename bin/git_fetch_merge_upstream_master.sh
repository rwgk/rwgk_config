#! /usr/bin/bash
set -x
set -e
git fetch upstream
git checkout master
git merge upstream/master
git push origin master
