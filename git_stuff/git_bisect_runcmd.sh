#! /bin/bash
. Cp13WslVenv/bin/activate
cd cuda_pathfinder/
pip install -v -e .
pytest -ra -s -v tests/test_find_nvidia_headers.py |& tee /tmp/test_log.txt
grep 'INFO test_find_libname_nvshmem' /tmp/test_log.txt
if [[ $? -eq 0 ]]; then # grep found a match
    exit 0              # signals "good/old"
fi
exit 1 # signals "bad/new"
