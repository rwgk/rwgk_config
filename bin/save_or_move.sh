#! /bin/bash

set -e

if [ -z "$RWGK_CONFIG_SAVE_TO" ]; then
  echo "$0"': FATAL: missing $RWGK_CONFIG_SAVE_TO: please define.'
  exit 1
fi

mode="$1"
shift
if [ "${mode}" != sve -a "${mode}" != smv ]; then
  echo "$0"': FATAL: unknown mode:' "${mode}"
  exit 1
fi

now=`date "+%Y-%m-%d+%H%M%S"`

for arg in "$@"; do
  if [ -L "${arg}" ]; then
    cmd=cp
  else
    if [ "${mode}" == "sve" ]; then
      cmd='cp -a'
    else
      cmd=mv
    fi
  fi
  filename=$(basename "${arg}")
  barename="${filename%.*}"
  if [ X"${barename}" = X -o X"${barename}" = X"${filename}" ]; then
    newname="${filename}_${now}"
  else
    extension="${filename##*.}"
    newname="${barename}_${now}.${extension}"
  fi
  echo ${cmd} "${arg}" "$RWGK_CONFIG_SAVE_TO/${newname}"
  ${cmd} "${arg}" "$RWGK_CONFIG_SAVE_TO/${newname}"
  if [ "${cmd}" == cp -a "${mode}" == smv ]; then
    rm "${arg}"
  fi
done

exit 0
