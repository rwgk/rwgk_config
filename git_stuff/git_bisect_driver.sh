#! /bin/bash
thisdir="$(dirname "$0")"
set -x
git bisect start
git bisect good d64120bb6733391989174456daaf18643ea29711  # old "Does not have STRING"
git bisect bad 365bf079f54d10993c1e036e280c6217a20969cf  # new "Does have STRING"
git bisect run "$thisdir/git_bisect_runcmd.sh"
