#! /bin/bash
for path in $*; do
  atime="$(/usr/bin/stat -c '%Y' "$path")"
  if [ $? -eq 0 ]; then
    atf="$(/bin/date --date="@$atime" '+%Y-%m-%d+%H%M%S')"
    if [ $? -eq 0 ]; then
      echo mv "${path}" "${path}_${atf}"
      /bin/mv "${path}" "${path}_${atf}"
    fi
  fi
done
exit 0
