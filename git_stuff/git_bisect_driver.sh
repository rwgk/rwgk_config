#! /bin/bash
thisdir="$(dirname "$0")"
set -x
git bisect start
git bisect old 38bd71134a428d641d8ca34b3ea94358a387703d  # "Does not have STRING"
git bisect new 66c3774a6402224b1724329c81c880e76633a92b  # "Does have STRING"
git bisect run "$thisdir/git_bisect_runcmd.sh"
