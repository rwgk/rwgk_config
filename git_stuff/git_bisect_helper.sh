#! /usr/bin/bash
git grep 'PYBIND11_INTERNALS_ID'
if [[ $? -eq 0 ]]; then
  exit 1
fi
exit 0
