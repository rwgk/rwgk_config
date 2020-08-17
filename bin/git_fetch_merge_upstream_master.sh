#! /usr/bin/bash
set -e -x
git fetch upstream
git checkout master
git merge upstream/master
