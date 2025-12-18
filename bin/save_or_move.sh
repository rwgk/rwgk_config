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
declare -a SKIP_ACTION=()

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
    SKIP_ACTION+=(0)
done

if [[ ${#SRC_PATHS[@]} -eq 0 ]]; then
    exit 0
fi

collision_count=0
identical_existing_count=0

# Check collisions with existing paths in target directory
for i in "${!DEST_PATHS[@]}"; do
    dest=${DEST_PATHS[$i]}
    if [[ -e "$dest" || -L "$dest" ]]; then
        src=${SRC_PATHS[$i]}
        if [[ -f "$src" && -f "$dest" ]]; then
            # Both regular files: treat as identical if contents match
            if cmp -s -- "$src" "$dest"; then
                printf '   Existing & identical path: %s (source: %s)\n' \
                    "$dest" "$src" >&2
                identical_existing_count=$((identical_existing_count + 1))
                SKIP_ACTION[$i]=1
            else
                printf 'Collision with existing path: %s (source: %s)\n' \
                    "$dest" "$src" >&2
                collision_count=$((collision_count + 1))
            fi
        else
            printf 'Collision with existing path: %s (source: %s)\n' \
                "$dest" "$src" >&2
            collision_count=$((collision_count + 1))
        fi
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

if ((identical_existing_count > 0)); then
    printf 'INFO: %d existing & identical target(s) skipped\n' "$identical_existing_count" >&2
fi

if ((collision_count > 0)); then
    printf 'ABORT: %d collisions detected → NO ACTIONS TAKEN\n' "$collision_count" >&2
    exit 1
fi

files_saved=0
dirs_saved=0
files_moved=0
dirs_moved=0

for i in "${!SRC_PATHS[@]}"; do
    src=${SRC_PATHS[$i]}
    dest=${DEST_PATHS[$i]}

    # Skip planned actions where an identical existing target was found
    if [[ "${SKIP_ACTION[$i]:-0}" -eq 1 ]]; then
        continue
    fi

    # Symlinks (that are NOT directories at this point) — copy referent; if smv, remove link
    if [[ -L "$src" && ! -d "$src" ]]; then
        run cp -- "$src" "$dest"
        if [[ "$mode" == "smv" ]]; then
            run rm -- "$src"
            files_moved=$((files_moved + 1))
        else
            files_saved=$((files_saved + 1))
        fi
        continue
    fi

    if [[ "$mode" == "sve" ]]; then
        # For directories with -D, preserve attributes; for files, same
        run cp -a -- "$src" "$dest"
        if [[ -d "$src" ]]; then
            dirs_saved=$((dirs_saved + 1))
        else
            files_saved=$((files_saved + 1))
        fi
    else
        run mv -- "$src" "$dest"
        if [[ -d "$src" ]]; then
            dirs_moved=$((dirs_moved + 1))
        else
            files_moved=$((files_moved + 1))
        fi
    fi
done

if [[ "$mode" == "sve" ]]; then
    if ((files_saved > 0)); then
        printf 'INFO: %d file(s) saved\n' "$files_saved" >&2
    fi
    if ((dirs_saved > 1)); then
        printf 'INFO: %d directories saved\n' "$dirs_saved" >&2
    elif ((dirs_saved > 0)); then
        printf 'INFO: 1 directory saved\n' >&2
    fi
else
    if ((files_moved > 0)); then
        printf 'INFO: %d file(s) moved\n' "$files_moved" >&2
    fi
    if ((dirs_moved > 1)); then
        printf 'INFO: %d directories moved\n' "$dirs_moved" >&2
    elif ((dirs_moved > 0)); then
        printf 'INFO: 1 directory moved\n' >&2
    fi
fi
