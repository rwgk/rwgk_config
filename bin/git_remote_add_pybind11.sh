#! /usr/bin/bash
set -e
set -x
git remote add "$1" "https://github.com/$1/pybind11"
git fetch "$1"
