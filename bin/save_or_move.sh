#!/usr/bin/env bash
# save_or_move.sh — copy or move files into $RWGK_CONFIG_SAVE_TO with a timestamped name.
#
# Usage:
#   save_or_move.sh sve [-n] [-D] FILE...
#   save_or_move.sh smv [-n] [-D] FILE...
#
# Flags:
#   -n   dry-run (print actions only)
#   -D   allow directories (copy/move them); otherwise directories are skipped with a message
#
# Environment:
#   RWGK_CONFIG_SAVE_TO  Destination directory (required)

set -Eeuo pipefail
IFS=$'\n\t'

DATEFMT='+%Y-%m-%d+%H%M%S'
UNAME="$(/usr/bin/uname 2>/dev/null || true)"

die() {
    printf '%s: FATAL: %s\n' "$(basename "$0")" "$*" >&2
    exit 1
}

file_mtime_timestamp() {
    local path=$1 mtime mtf
    if [[ "$UNAME" == "Darwin" ]]; then
        if ! mtime="$(/usr/bin/stat -f '%m' -- "$path")"; then
            return 1
        fi
        if ! mtf="$(/bin/date -j -f '%s' "$mtime" "$DATEFMT")"; then
            return 1
        fi
    else
        if ! mtime="$(/usr/bin/stat -c '%Y' -- "$path")"; then
            return 1
        fi
        if ! mtf="$(/bin/date --date="@$mtime" "$DATEFMT")"; then
            return 1
        fi
    fi
    printf '%s\n' "$mtf"
}

: "${RWGK_CONFIG_SAVE_TO:?missing \$RWGK_CONFIG_SAVE_TO: please define.}"

[[ $# -ge 2 ]] || die "need a mode (sve|smv) and at least one FILE"

mode="$1"
shift
case "$mode" in
sve | smv) ;;
*) die "unknown mode: $mode (expected 'sve' or 'smv')" ;;
esac

# optional flags after mode: -n (dry-run), -D (allow directories)
DRYRUN=0
DIRS_OK=0
while [[ $# -gt 0 ]]; do
    case "${1:-}" in
    -n)
        DRYRUN=1
        shift
        ;;
    -D)
        DIRS_OK=1
        shift
        ;;
    --)
        shift
        break
        ;;
    -*) die "unknown option: $1 (allowed: -n, -D)" ;;
    *) break ;;
    esac
done

[[ $# -ge 1 ]] || die "no FILE arguments given"

run() {
    # Print on one line, space-separated, regardless of global IFS
    local IFS=' '
    printf '%s\n' "$*"
    if [[ $DRYRUN -eq 0 ]]; then
        "$@"
    fi
}

timestamped_name() {
    local filename=$1 timestamp=$2 barename extension newname
    barename="${filename%.*}"
    if [[ -z "$barename" || "$barename" == "$filename" ]]; then
        newname="${filename}_${timestamp}"
    else
        extension="${filename##*.}"
        newname="${barename}_${timestamp}.${extension}"
    fi
    # Replace leading '.' with '_'
    printf '%s' "${newname/#./_}"
}

declare -a SRC_PATHS=()
declare -a DEST_PATHS=()

for arg in "$@"; do
    [[ -e "$arg" || -L "$arg" ]] || {
        printf 'Skipping non-existent path: %s\n' "$arg" >&2
        continue
    }

    # Safety: directories (including symlinks to directories) are skipped unless -D is provided
    if [[ -d "$arg" ]]; then
        if [[ $DIRS_OK -eq 0 ]]; then
            printf 'Skipping directory (use -D to include): %s\n' "$arg" >&2
            continue
        fi
    fi

    filename=$(basename -- "$arg")
    if ! mtf=$(file_mtime_timestamp "$arg"); then
        die "failed to obtain mtime-based timestamp for: $arg"
    fi
    newname=$(timestamped_name "$filename" "$mtf")
    dest="$RWGK_CONFIG_SAVE_TO/$newname"

    SRC_PATHS+=("$arg")
    DEST_PATHS+=("$dest")
done

if [[ ${#SRC_PATHS[@]} -eq 0 ]]; then
    exit 0
fi

collision_count=0

# Check collisions with existing paths in target directory
for i in "${!DEST_PATHS[@]}"; do
    dest=${DEST_PATHS[$i]}
    if [[ -e "$dest" || -L "$dest" ]]; then
        printf 'Collision with existing path: %s (source: %s)\n' \
            "$dest" "${SRC_PATHS[$i]}" >&2
        collision_count=$((collision_count + 1))
    fi
done

# Check collisions among planned targets themselves
for i in "${!DEST_PATHS[@]}"; do
    for j in "${!DEST_PATHS[@]}"; do
        if ((j <= i)); then
            continue
        fi
        if [[ "${DEST_PATHS[$i]}" == "${DEST_PATHS[$j]}" ]]; then
            printf 'Collision between planned targets:\n' >&2
            printf '  %s -> %s\n' "${SRC_PATHS[$i]}" "${DEST_PATHS[$i]}" >&2
            printf '  %s -> %s\n' "${SRC_PATHS[$j]}" "${DEST_PATHS[$j]}" >&2
            collision_count=$((collision_count + 1))
        fi
    done
done

if ((collision_count > 0)); then
    printf 'ABORT: %d collisions detected → NO ACTIONS TAKEN\n' "$collision_count" >&2
    exit 1
fi

for i in "${!SRC_PATHS[@]}"; do
    src=${SRC_PATHS[$i]}
    dest=${DEST_PATHS[$i]}

    # Symlinks (that are NOT directories at this point) — copy referent; if smv, remove link
    if [[ -L "$src" && ! -d "$src" ]]; then
        run cp -- "$src" "$dest"
        if [[ "$mode" == "smv" ]]; then
            run rm -- "$src"
        fi
        continue
    fi

    if [[ "$mode" == "sve" ]]; then
        # For directories with -D, preserve attributes; for files, same
        run cp -a -- "$src" "$dest"
    else
        run mv -- "$src" "$dest"
    fi
done
