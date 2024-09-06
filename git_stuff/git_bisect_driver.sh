#! /usr/bin/bash
set -x
git bisect start
git bisect good 38bd71134a428d641d8ca34b3ea94358a387703d
git bisect bad 66c3774a6402224b1724329c81c880e76633a92b
git bisect run $HOME/git_bisect_helper.sh
