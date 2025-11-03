#! /bin/bash
thisdir="$(dirname "$0")"
set -x
git bisect start
git bisect good 38bd71134a428d641d8ca34b3ea94358a387703d  # old "Does not have STRING"
git bisect bad 76e06c89e419db196cda20e0e43171f9461139ae  # new "Does have STRING"
git bisect run "$thisdir/git_bisect_runcmd.sh"
