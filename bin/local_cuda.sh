#!/bin/bash
#
# Usage:
#   local_cuda.sh status   # no sudo needed
#   local_cuda.sh on
#   local_cuda.sh off
#
# Moves /usr/local/cuda and its target directory into /usr/local/__OFF__/ and back.
# Automatically handles ld.so.conf.d entries, uses sudo for privileged ops,
# logs actions to /usr/local/__OFF__/ChangeLog.txt

set -euo pipefail

LOCAL=/usr/local
OFFDIR="$LOCAL/__OFF__"
CHANGELOG="$OFFDIR/ChangeLog.txt"
SUDO="sudo"

mode="${1:-}"
if [[ "$mode" != "on" && "$mode" != "off" && "$mode" != "status" ]]; then
    echo "Usage: $0 on|off|status" >&2
    exit 1
fi

err() {
    echo "Error: $*" >&2
    exit 1
}

log_change() {
    # usage: log_change "cuda-13.0 restored to /usr/local/cuda"
    local msg="$1"
    local ts
    ts=$(date "+%Y-%m-%d+%H%M%S")
    $SUDO sh -c "echo \"$ts $msg\" >> \"$CHANGELOG\""
}

version_to_conf() {
    # convert "cuda-13.0" -> "cuda-13-0.conf"
    local version_name="$1"
    echo "${version_name//./-}.conf"
}

get_current_version() {
    # Returns "cuda-XX.Y" if a symlink exists, or empty if none
    if [[ -L "$LOCAL/cuda" ]]; then
        basename "$(readlink "$LOCAL/cuda")"
    elif [[ -L "$OFFDIR/cuda" ]]; then
        basename "$(readlink "$OFFDIR/cuda")"
    else
        echo ""
    fi
}

status() {
    local version
    version=$(get_current_version)

    if [[ -n "$version" ]]; then
        if [[ -L "$LOCAL/cuda" ]]; then
            echo "$version is On"
            return 0
        elif [[ -L "$OFFDIR/cuda" ]]; then
            echo "$version is Off"
            return 1
        else
            echo "Unexpected state: symlink found but neither On nor Off"
            return 2
        fi
    else
        echo "No CUDA installation detected (no cuda symlink in $LOCAL or $OFFDIR)"
        return 2
    fi
}

if [[ "$mode" == "status" ]]; then
    status
    exit $?
fi

if [[ "$mode" == "off" ]]; then
    # Turn CUDA off
    if [[ ! -L "$LOCAL/cuda" ]]; then
        err "/usr/local/cuda is not a symlink, cannot switch off"
    fi

    target="$(readlink "$LOCAL/cuda")"
    version_name="$(basename "$target")"
    conf_file="/etc/ld.so.conf.d/$(version_to_conf "$version_name")"

    if [[ -e "$OFFDIR/$version_name" ]]; then
        echo "$version_name is Off already"
        exit 0
    fi

    echo "Moving $version_name to $OFFDIR ..."
    $SUDO mkdir -p "$OFFDIR"
    $SUDO mv "$LOCAL/$version_name" "$OFFDIR/"
    $SUDO mv "$LOCAL/cuda" "$OFFDIR/"

    # Move ld.so.conf file if exists
    if [[ -f "$conf_file" ]]; then
        $SUDO mv "$conf_file" "$OFFDIR/"
        log_change "$conf_file moved to $OFFDIR"
    fi

    log_change "$version_name moved to $OFFDIR"
    $SUDO ldconfig
    echo "Done."

elif [[ "$mode" == "on" ]]; then
    # Turn CUDA on
    symlink_in_off="$OFFDIR/cuda"
    if [[ ! -L "$symlink_in_off" ]]; then
        # Maybe it's already on
        if [[ -L "$LOCAL/cuda" ]]; then
            version_name="$(basename "$(readlink "$LOCAL/cuda")")"
            echo "$version_name is On already"
            exit 0
        else
            err "No cuda symlink found in $OFFDIR and /usr/local/cuda is not a symlink"
        fi
    fi

    target="$(readlink "$symlink_in_off")"
    version_name="$(basename "$target")"
    conf_file="$OFFDIR/$(version_to_conf "$version_name")"

    if [[ -e "$LOCAL/$version_name" ]]; then
        echo "$version_name is On already"
        exit 0
    fi

    echo "Restoring $version_name to $LOCAL ..."
    $SUDO mv "$OFFDIR/$version_name" "$LOCAL/"
    $SUDO mv "$OFFDIR/cuda" "$LOCAL/"

    # Restore ld.so.conf file if present
    if [[ -f "$conf_file" ]]; then
        $SUDO mv "$conf_file" "/etc/ld.so.conf.d/"
        log_change "$version_name ld.so.conf restored to /etc/ld.so.conf.d/"
    fi

    log_change "$version_name restored to $LOCAL/cuda"
    $SUDO ldconfig
    echo "Done."
fi
