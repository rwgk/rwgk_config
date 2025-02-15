#! /bin/bash
git grep 'PYBIND11_INTERNALS_ID'
if [[ $? -eq 0 ]]; then # grep found a match
  exit 1                # signals "bad/new"
fi
exit 0 # signals "good/old"
