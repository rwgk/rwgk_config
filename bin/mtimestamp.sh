#! /bin/bash
datefmt='+%Y-%m-%d+%H%M%S'
uname="$(/usr/bin/uname 2>/dev/null)"

die() {
    printf '%s: FATAL: %s\n' "$(basename "$0")" "$*" >&2
    exit 1
}

mode="$1"
shift
case "$mode" in
move | dup) ;;
*) die "unknown mode: $mode (expected 'move' or 'dup')" ;;
esac

[[ $# -ge 1 ]] || die "no FILE arguments given"

exit_code=0
for path in "$@"; do
    if [ "X$uname" = XDarwin ]; then
        mtime="$(/usr/bin/stat -f '%m' "${path}")"
    else
        mtime="$(/usr/bin/stat -c '%Y' "${path}")"
    fi
    if [ $? -ne 0 ]; then
        exit_code=1
    else
        if [ "X${uname}" = XDarwin ]; then
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

            if [ "$mode" = move ]; then
                verb="moving"
                cmd_label="mv"
                cmd="/bin/mv"
            else
                verb="duplicating"
                cmd_label="cp -a"
                cmd="/bin/cp -a"
            fi

            if [ -e "${newpath}" ]; then
                echo "ERROR: NOT ${verb} \"${path}\" because \"${newpath}\" exists already."
                exit_code=1
            else
                echo ${cmd_label} "${path}" "${newpath}"
                ${cmd} "${path}" "${newpath}"
                if [ $? -ne 0 ]; then
                    exit_code=1
                fi
            fi
        fi
    fi
done
exit $exit_code
