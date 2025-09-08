#!/bin/bash
#
# Usage:
#   local_cuda.sh status      # no sudo needed
#   local_cuda.sh on
#   local_cuda.sh off
#   local_cuda.sh switch 12.9 # or: switch cuda-12.9
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
if [[ "$mode" != "on" && "$mode" != "off" && "$mode" != "status" && "$mode" != "switch" ]]; then
    echo "Usage: $0 on|off|status|switch <X.Y|cuda-X.Y>" >&2
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
    $SUDO sh -c "mkdir -p '$OFFDIR'; echo \"$ts $msg\" >> \"$CHANGELOG\""
}

version_to_conf_basename() {
    # "cuda-13.0" -> "cuda-13-0.conf"
    local version_name="$1"
    echo "${version_name//./-}.conf"
}

normalize_version_input() {
    # "12.9" -> "cuda-12.9"; "cuda-12.9" -> "cuda-12.9"
    local in="$1"
    if [[ "$in" == cuda-* ]]; then
        echo "$in"
    else
        echo "cuda-$in"
    fi
}

get_current_symlink() {
    if [[ -L "$LOCAL/cuda" ]]; then
        echo "$LOCAL/cuda"
    elif [[ -L "$OFFDIR/cuda" ]]; then
        echo "$OFFDIR/cuda"
    else
        echo ""
    fi
}

get_current_version() {
    local link
    link=$(get_current_symlink)
    if [[ -n "$link" ]]; then
        basename "$(readlink "$link")"
    else
        echo ""
    fi
}

list_available_versions() {
    # Collect cuda-* directories present either in LOCAL or OFFDIR
    local -A seen=()
    local d
    shopt -s nullglob
    for d in "$LOCAL"/cuda-* "$OFFDIR"/cuda-*; do
        [[ -d "$d" ]] || continue
        seen["$(basename "$d")"]=1
    done
    shopt -u nullglob

    # Print as space-separated list
    local k
    for k in "${!seen[@]}"; do echo "$k"; done
}

status() {
    local version
    version=$(get_current_version)
    if [[ -n "$version" ]]; then
        if [[ -L "$LOCAL/cuda" ]]; then
            echo "$version is On"
        elif [[ -L "$OFFDIR/cuda" ]]; then
            echo "$version is Off"
        else
            echo "Unexpected state: symlink found but neither On nor Off"
        fi
    else
        echo "No CUDA installation detected (no cuda symlink in $LOCAL or $OFFDIR)"
    fi

    # Also show available versions to switch to
    local active="cuda-none"
    [[ -n "$version" ]] && active="$version"

    local versions
    versions=($(list_available_versions))
    if ((${#versions[@]})); then
        # Build comma-separated list excluding active
        local out=()
        local v
        for v in "${versions[@]}"; do
            [[ "$v" == "$active" ]] && continue
            out+=("$v")
        done
        if ((${#out[@]})); then
            IFS=', ' read -r -a _ <<<""
            echo -n "Available to switch: "
            local i
            for i in "${!out[@]}"; do
                [[ $i -gt 0 ]] && printf ", "
                printf "%s" "${out[$i]}"
            done
            echo
        fi
        echo -n "Active: "
        echo "$active"
    fi
}

ensure_target_dir_present_locally() {
    # If desired version dir is in OFFDIR, move it back to LOCAL
    local version_name="$1"
    if [[ -d "$OFFDIR/$version_name" && ! -d "$LOCAL/$version_name" ]]; then
        $SUDO mv "$OFFDIR/$version_name" "$LOCAL/"
        log_change "$version_name restored to $LOCAL/"
    fi
}

conf_cleanup_and_activate() {
    # Ensure only target conf is in /etc/ld.so.conf.d; move others out to OFFDIR.
    # Restore target conf from OFFDIR if present there.
    local version_name="$1"
    local target_conf_basename
    target_conf_basename=$(version_to_conf_basename "$version_name")
    local etcdir="/etc/ld.so.conf.d"

    # Move non-target cuda-*.conf out to OFFDIR
    local f
    shopt -s nullglob
    for f in "$etcdir"/cuda-*.conf; do
        if [[ "$(basename "$f")" != "$target_conf_basename" ]]; then
            $SUDO mkdir -p "$OFFDIR"
            $SUDO mv "$f" "$OFFDIR/"
            log_change "$f moved to $OFFDIR"
        fi
    done
    shopt -u nullglob

    # Restore target conf if it's in OFFDIR
    if [[ -f "$OFFDIR/$target_conf_basename" && ! -f "$etcdir/$target_conf_basename" ]]; then
        $SUDO mv "$OFFDIR/$target_conf_basename" "$etcdir/"
        log_change "$version_name ld.so.conf restored to $etcdir/"
    fi

    # Refresh linker cache
    $SUDO ldconfig
}

do_switch() {
    local want_input="$1"
    local want_version
    want_version="$(normalize_version_input "$want_input")"

    # Validate current state
    local cur_link cur_version
    cur_link=$(get_current_symlink)
    cur_version=$(get_current_version)

    if [[ -z "$cur_link" || -z "$cur_version" ]]; then
        err "No CUDA symlink found (neither $LOCAL/cuda nor $OFFDIR/cuda). Use '$0 on' or install CUDA."
    fi

    # Already active?
    if [[ -L "$LOCAL/cuda" && "$cur_version" == "$want_version" ]]; then
        echo "${want_version#cuda-} is active already"
        return 0
    fi

    # Does requested version exist?
    if [[ ! -d "$LOCAL/$want_version" && ! -d "$OFFDIR/$want_version" ]]; then
        echo "${want_version#cuda-} does not exist"
        return 1
    fi

    # Make sure desired version dir is in LOCAL
    ensure_target_dir_present_locally "$want_version"

    # Switch message (nice and concise)
    echo "switching from ${cur_version#cuda-} → ${want_version#cuda-}"

    # If CUDA is currently Off (symlink in OFFDIR), we need to create/update symlink in LOCAL
    if [[ "$cur_link" == "$OFFDIR/cuda" ]]; then
        # Remove OFF symlink if present; create new symlink at LOCAL to desired version
        if [[ -L "$OFFDIR/cuda" ]]; then
            $SUDO rm -f "$OFFDIR/cuda"
            log_change "removed $OFFDIR/cuda symlink"
        fi
        $SUDO ln -sfn "$want_version" "$LOCAL/cuda"
        log_change "$want_version restored to $LOCAL/cuda (via switch)"
    else
        # Standard On case: atomically retarget symlink
        $SUDO ln -sfn "$want_version" "$LOCAL/cuda"
        log_change "$LOCAL/cuda -> $want_version (switched)"
    fi

    # ld.so.conf housekeeping (only target kept in /etc)
    conf_cleanup_and_activate "$want_version"

    echo "Done."
}

# -------- dispatch --------

if [[ "$mode" == "status" ]]; then
    status
    exit 0
elif [[ "$mode" == "switch" ]]; then
    shift || true
    [[ $# -ge 1 ]] || err "Usage: $0 switch <X.Y|cuda-X.Y>"
    do_switch "$1"
    exit $?
fi

# Existing on/off flows (unchanged)

if [[ "$mode" == "off" ]]; then
    # Turn CUDA off
    if [[ ! -L "$LOCAL/cuda" ]]; then
        err "/usr/local/cuda is not a symlink, cannot switch off"
    fi

    target="$(readlink "$LOCAL/cuda")"
    version_name="$(basename "$target")"
    conf_file="/etc/ld.so.conf.d/$(version_to_conf_basename "$version_name")"

    if [[ -e "$OFFDIR/$version_name" ]]; then
        echo "$version_name is Off already"
        exit 0
    fi

    echo "Moving $version_name to $OFFDIR ..."
    $SUDO mkdir -p "$OFFDIR"
    $SUDO mv "$LOCAL/$version_name" "$OFFDIR/"
    $SUDO mv "$LOCAL/cuda" "$OFFDIR/"

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
    conf_file="$OFFDIR/$(version_to_conf_basename "$version_name")"

    if [[ -e "$LOCAL/$version_name" ]]; then
        echo "$version_name is On already"
        exit 0
    fi

    echo "Restoring $version_name to $LOCAL ..."
    $SUDO mv "$OFFDIR/$version_name" "$LOCAL/"
    $SUDO mv "$OFFDIR/cuda" "$LOCAL/"

    if [[ -f "$conf_file" ]]; then
        $SUDO mv "$conf_file" "/etc/ld.so.conf.d/"
        log_change "$version_name ld.so.conf restored to /etc/ld.so.conf.d/"
    fi

    log_change "$version_name restored to $LOCAL/cuda"
    $SUDO ldconfig
    echo "Done."
fi
