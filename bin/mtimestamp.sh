#! /bin/bash
datefmt='+%Y-%m-%d+%H%M%S'
uname="$(/usr/bin/uname 2>/dev/null)"
exit_code=0
for path in $*; do
  if [ "X$uname" == XDarwin ]; then
    mtime="$(/usr/bin/stat -f '%m' "$path")"
  else
    mtime="$(/usr/bin/stat -c '%Y' "$path")"
  fi
  if [ $? -ne 0 ]; then
    exit_code=1
  else
    if [ "X$uname" == XDarwin ]; then
      mtf="$(/bin/date -j -f '%s' "$mtime" "$datefmt")"
    else
      mtf="$(/bin/date --date="@$mtime" "$datefmt")"
    fi
    if [ $? -ne 0 ]; then
      exit_code=1
    else
      if [ -e "${path}_${mtf}" ]; then
        echo "ERROR: NOT moving \"${path}\"" \
             "because \"${path}_${mtf}\" exists already."
        exit_code=1
      else
        echo mv "${path}" "${path}_${mtf}"
        /bin/mv "${path}" "${path}_${mtf}"
        if [ $? -ne 0 ]; then
          exit_code=1
        fi
      fi
    fi
  fi
done
exit $exit_code
