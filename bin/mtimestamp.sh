#! /bin/bash
datefmt='+%Y-%m-%d+%H%M%S'
uname="$(/usr/bin/uname 2>/dev/null)"
exit_code=0
for path in "$@"; do
  if [ "X$uname" == XDarwin ]; then
    mtime="$(/usr/bin/stat -f '%m' "${path}")"
  else
    mtime="$(/usr/bin/stat -c '%Y' "${path}")"
  fi
  if [ $? -ne 0 ]; then
    exit_code=1
  else
    if [ "X${uname}" == XDarwin ]; then
      mtf="$(/bin/date -j -f '%s' "${mtime}" "${datefmt}")"
    else
      mtf="$(/bin/date --date="@${mtime}" "${datefmt}")"
    fi
    if [ $? -ne 0 ]; then
      exit_code=1
    else
      dirname="$(dirname "${path}")"
      filename="$(basename "${path}")"
      barename="${filename%.*}"
      if [ X"${barename}" = X -o X"${barename}" = X"${filename}" ]; then
        newpath="${filename}_${mtf}"
      else
        extension="${filename##*.}"
        newpath="${barename}_${mtf}.${extension}"
      fi
      if [ -n "${dirname}" ]; then
        newpath="${dirname}/${newpath}"
      fi
      if [ -e "${newpath}" ]; then
        echo "ERROR: NOT moving \"${path}\"" \
             "because \"${newpath}\" exists already."
        exit_code=1
      else
        echo mv "${path}" "${newpath}"
        /bin/mv "${path}" "${newpath}"
        if [ $? -ne 0 ]; then
          exit_code=1
        fi
      fi
    fi
  fi
done
exit $exit_code
