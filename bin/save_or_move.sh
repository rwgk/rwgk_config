#! /bin/bash

set -e

if [ -z "$Save" ]; then
  echo "$0"': FATAL: missing $Save: please define.'
  exit 1
fi

mode="$1"
shift
if [ "$mode" != sve -a "$mode" != smv ]; then
  echo "$0"': FATAL: unknown mode:' "$mode"
  exit 1
fi

now=`date "+%Y-%m-%d+%H%M%S"`

for arg in "$@"; do
  if [ -L "$arg" ]; then
    cmd=cp
  else
    if [ "$mode" == "sve" ]; then
      cmd='cp -a'
    else
      cmd=mv
    fi
  fi
  barg=$(basename "$arg")
  echo $cmd "$arg" "$Save/${barg}_$now"
  $cmd "$arg" "$Save/${barg}_$now"
  if [ "$cmd" == cp -a "$mode" == smv ]; then
    rm "$arg"
  fi
done

exit 0
