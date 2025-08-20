#!/usr/bin/env bash
# save_or_move.sh — copy or move files into $RWGK_CONFIG_SAVE_TO with a timestamped name.
#
# Usage:
#   save_or_move.sh sve [-n] FILE...
#   save_or_move.sh smv [-n] FILE...
#
# Environment:
#   RWGK_CONFIG_SAVE_TO  Destination directory (required)

set -Eeuo pipefail
IFS=$'\n\t'

die() {
    printf '%s: FATAL: %s\n' "$(basename "$0")" "$*" >&2
    exit 1
}

: "${RWGK_CONFIG_SAVE_TO:?missing \$RWGK_CONFIG_SAVE_TO: please define.}"

[[ $# -ge 2 ]] || die "need a mode (sve|smv) and at least one FILE"

mode="$1"
shift
case "$mode" in
sve | smv) ;;
*) die "unknown mode: $mode (expected 'sve' or 'smv')" ;;
esac

# optional -n flag
DRYRUN=0
if [[ "${1:-}" == "-n" ]]; then
    DRYRUN=1
    shift
fi

[[ $# -ge 1 ]] || die "no FILE arguments given"

now=$(date "+%Y-%m-%d+%H%M%S")

run() {
    # Print on one line, space-separated, regardless of global IFS
    local IFS=' '
    printf '%s\n' "$*"
    if [[ $DRYRUN -eq 0 ]]; then
        "$@"
    fi
}

timestamped_name() {
    local filename=$1 barename extension newname
    barename="${filename%.*}"
    if [[ -z "$barename" || "$barename" == "$filename" ]]; then
        newname="${filename}_${now}"
    else
        extension="${filename##*.}"
        newname="${barename}_${now}.${extension}"
    fi
    # Replace leading '.' with '_'
    printf '%s' "${newname/#./_}"
}

for arg in "$@"; do
    [[ -e "$arg" || -L "$arg" ]] || {
        printf 'Skipping non-existent path: %s\n' "$arg" >&2
        continue
    }

    filename=$(basename -- "$arg")
    newname=$(timestamped_name "$filename")
    dest="$RWGK_CONFIG_SAVE_TO/$newname"

    if [[ -L "$arg" ]]; then
        run cp -- "$arg" "$dest"
        if [[ "$mode" == "smv" ]]; then
            run rm -- "$arg"
        fi
        continue
    fi

    if [[ "$mode" == "sve" ]]; then
        run cp -a -- "$arg" "$dest"
    else
        run mv -- "$arg" "$dest"
    fi
done
