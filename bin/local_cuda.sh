#!/bin/bash
#
# local_cuda.sh — manage CUDA Toolkit activation locally
#
# Supports two modes automatically:
#  1) Symlink mode (legacy): /usr/local/cuda is a symlink. Behaves like the original script.
#  2) Conf-only mode: No /usr/local/cuda symlink. Activation is done by moving
#     /etc/ld.so.conf.d/cuda-*.conf files between /etc/ld.so.conf.d and /usr/local/__OFF__.
#
# Usage:
#   local_cuda.sh status         # no sudo needed
#   local_cuda.sh on             # legacy symlink mode only; in conf-only mode prints guidance
#   local_cuda.sh off            # symlink mode: moves cuda OFF; conf-only: removes all cuda-*.conf from /etc
#   local_cuda.sh switch 13.0    # or: switch cuda-13.0; in conf-only mode activates the desired conf only
#   local_cuda.sh list           # list all available cuda-*.conf (full paths) in /etc and OFFDIR
#
# Notes:
# - In conf-only mode, switch 13.0 ensures only /etc/ld.so.conf.d/cuda-13-0.conf remains in /etc.
# - Any other cuda-*.conf files are moved to /usr/local/__OFF__/.
# - All privileged operations are performed via sudo (can be overridden by SUDO env var).
# - Changes are logged to /usr/local/__OFF__/ChangeLog.txt.
#
# Example:
#   ./local_cuda.sh switch 13.0
#   ./local_cuda.sh status
#
set -euo pipefail

LOCAL=/usr/local
OFFDIR="$LOCAL/__OFF__"
CHANGELOG="$OFFDIR/ChangeLog.txt"
SUDO="${SUDO:-sudo}"
ETCDIR="/etc/ld.so.conf.d"

mode="${1:-}"
if [[ "$mode" != "on" && "$mode" != "off" && "$mode" != "status" && "$mode" != "switch" && "$mode" != "list" ]]; then
    echo "Usage: $0 on|off|status|switch <X.Y|cuda-X.Y>|list" >&2
    exit 1
fi

err() {
    echo "Error: $*" >&2
    exit 1
}

log_change() {
    local msg="$1"
    local ts
    ts=$(date "+%Y-%m-%d+%H%M%S")
    $SUDO sh -c "mkdir -p '$OFFDIR'; echo \"$ts $msg\" >> \"$CHANGELOG\""
}

have_symlink_mode() {
    # Return 0 if either /usr/local/cuda or /usr/local/__OFF__/cuda exists as a symlink
    if [[ -L "$LOCAL/cuda" || -L "$OFFDIR/cuda" ]]; then
        return 0
    fi
    return 1
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

version_to_conf_basename() {
    # "cuda-13.0" -> "cuda-13-0.conf"
    local version_name="$1"
    echo "${version_name//./-}.conf"
}

normalize_version_input() {
    # "13.0" -> "cuda-13.0"; "cuda-13.0" -> "cuda-13.0"
    local in="$1"
    if [[ "$in" == cuda-* ]]; then
        echo "$in"
    else
        echo "cuda-$in"
    fi
}

# ---------- COMMON (both modes) ----------

list_available_versions() {
    # Collect cuda-* directories present either in LOCAL or OFFDIR (for symlink mode)
    # plus list conf files in /etc and OFFDIR (for conf-only mode visibility).
    local -A seen=()
    local d f
    shopt -s nullglob
    for d in "$LOCAL"/cuda-* "$OFFDIR"/cuda-*; do
        [[ -d "$d" ]] || continue
        seen["$(basename "$d")"]=1
    done
    # Also record conf file basenames (converted to version-like names) for discoverability.
    for f in "$ETCDIR"/cuda-*.conf "$OFFDIR"/cuda-*.conf; do
        [[ -f "$f" ]] || continue
        local base confver
        base="$(basename "$f")"   # cuda-13-0.conf
        confver="${base%.conf}"   # cuda-13-0
        confver="${confver//-/.}" # cuda.13.0  (fix the first dash back to '-' below)
        # Replace the first dot after 'cuda' back to '-'
        confver="${confver/cuda./cuda-}" # cuda-13.0
        seen["$confver"]=1
    done
    shopt -u nullglob

    local k
    for k in "${!seen[@]}"; do echo "$k"; done
}

status_symlink_mode() {
    local version
    version=$(get_current_version)
    if [[ -n "$version" ]]; then
        if [[ -L "$LOCAL/cuda" ]]; then
            echo "$version is On (symlink mode)"
        elif [[ -L "$OFFDIR/cuda" ]]; then
            echo "$version is Off (symlink mode)"
        else
            echo "Unexpected state: symlink present but neither On nor Off (symlink mode)"
        fi
    else
        echo "No CUDA installation detected (no cuda symlink in $LOCAL or $OFFDIR)"
    fi
    local active="cuda-none"
    [[ -n "$version" ]] && active="$version"

    local versions
    mapfile -t versions < <(list_available_versions)
    if ((${#versions[@]})); then
        local out=()
        local v
        for v in "${versions[@]}"; do
            [[ "$v" == "$active" ]] && continue
            out+=("$v")
        done
        if ((${#out[@]})); then
            printf "Available to switch: %s\n" "$(
                IFS=', '
                echo "${out[*]}"
            )"
        fi
        echo "Active: $active"
    fi
}

# ---------- CONF-ONLY MODE HELPERS ----------

conf_move_non_target_out() {
    local target_conf_basename="$1"
    local f
    shopt -s nullglob
    for f in "$ETCDIR"/cuda-*.conf; do
        if [[ "$(basename "$f")" != "$target_conf_basename" ]]; then
            $SUDO mkdir -p "$OFFDIR"
            $SUDO mv "$f" "$OFFDIR/"
            log_change "$f moved to $OFFDIR"
        fi
    done
    shopt -u nullglob
}

conf_restore_target_if_off() {
    local target_conf_basename="$1"
    if [[ -f "$OFFDIR/$target_conf_basename" && ! -f "$ETCDIR/$target_conf_basename" ]]; then
        $SUDO mv "$OFFDIR/$target_conf_basename" "$ETCDIR/"
        log_change "$ETCDIR/$target_conf_basename restored from $OFFDIR"
    fi
}

conf_only_switch() {
    local want_input="$1"
    local want_version
    want_version="$(normalize_version_input "$want_input")"
    local target_conf_basename
    target_conf_basename="$(version_to_conf_basename "$want_version")"

    # Validate existence somewhere
    if [[ ! -f "$ETCDIR/$target_conf_basename" && ! -f "$OFFDIR/$target_conf_basename" ]]; then
        err "Config not found for $want_version. Expect one of: $ETCDIR/$target_conf_basename or $OFFDIR/$target_conf_basename"
    fi

    echo "Conf-only mode: activating $want_version via $target_conf_basename"

    conf_move_non_target_out "$target_conf_basename"
    conf_restore_target_if_off "$target_conf_basename"

    # Ensure exactly one target exists in /etc now
    if [[ ! -f "$ETCDIR/$target_conf_basename" ]]; then
        err "Unexpected: failed to place $ETCDIR/$target_conf_basename"
    fi

    $SUDO ldconfig
    echo "Done."
}

conf_only_off() {
    local moved=0
    shopt -s nullglob
    for f in "$ETCDIR"/cuda-*.conf; do
        $SUDO mkdir -p "$OFFDIR"
        $SUDO mv "$f" "$OFFDIR/"
        log_change "$f moved to $OFFDIR (off)"
        moved=1
    done
    shopt -u nullglob
    if [[ "$moved" -eq 1 ]]; then
        $SUDO ldconfig
        echo "All CUDA confs removed from $ETCDIR."
    else
        echo "No CUDA confs present in $ETCDIR."
    fi
}

conf_only_status() {
    local act=()
    shopt -s nullglob
    for f in "$ETCDIR"/cuda-*.conf; do
        act+=("$(basename "$f")")
    done
    shopt -u nullglob
    if ((${#act[@]} == 0)); then
        echo "Conf-only mode: No active CUDA confs in $ETCDIR. Use: $0 switch <X.Y>"
    else
        echo "Conf-only mode: Active in $ETCDIR:"
        printf "  %s\n" "${act[@]}"
    fi
}

conf_only_list() {
    shopt -s nullglob
    local any=0
    for f in "$ETCDIR"/cuda-*.conf "$OFFDIR"/cuda-*.conf; do
        [[ -f "$f" ]] || continue
        echo "$f"
        any=1
    done
    shopt -u nullglob
    if [[ "$any" -eq 0 ]]; then
        echo "No cuda-*.conf files found in $ETCDIR or $OFFDIR."
    fi
}

# ---------- SYMLINK MODE ACTIONS (legacy; unchanged logic) ----------

ensure_target_dir_present_locally() {
    local version_name="$1"
    if [[ -d "$OFFDIR/$version_name" && ! -d "$LOCAL/$version_name" ]]; then
        $SUDO mv "$OFFDIR/$version_name" "$LOCAL/"
        log_change "$version_name restored to $LOCAL/"
    fi
}

conf_cleanup_and_activate() {
    local version_name="$1"
    local target_conf_basename
    target_conf_basename=$(version_to_conf_basename "$version_name")

    conf_move_non_target_out "$target_conf_basename"
    conf_restore_target_if_off "$target_conf_basename"
    $SUDO ldconfig
}

do_switch_symlink_mode() {
    local want_input="$1"
    local want_version
    want_version="$(normalize_version_input "$want_input")"

    local cur_link cur_version
    cur_link=$(get_current_symlink)
    cur_version=$(get_current_version)

    if [[ -z "$cur_link" || -z "$cur_version" ]]; then
        err "No CUDA symlink found (neither $LOCAL/cuda nor $OFFDIR/cuda). Use '$0 on' or install CUDA."
    fi

    if [[ -L "$LOCAL/cuda" && "$cur_version" == "$want_version" ]]; then
        echo "${want_version#cuda-} is active already"
        return 0
    fi

    if [[ ! -d "$LOCAL/$want_version" && ! -d "$OFFDIR/$want_version" ]]; then
        echo "${want_version#cuda-} does not exist"
        return 1
    fi

    ensure_target_dir_present_locally "$want_version"

    echo "switching from ${cur_version#cuda-} → ${want_version#cuda-} (symlink mode)"
    if [[ "$cur_link" == "$OFFDIR/cuda" ]]; then
        if [[ -L "$OFFDIR/cuda" ]]; then
            $SUDO rm -f "$OFFDIR/cuda"
            log_change "removed $OFFDIR/cuda symlink"
        fi
        $SUDO ln -sfn "$want_version" "$LOCAL/cuda"
        log_change "$want_version restored to $LOCAL/cuda (via switch)"
    else
        $SUDO ln -sfn "$want_version" "$LOCAL/cuda"
        log_change "$LOCAL/cuda -> $want_version (switched)"
    fi

    conf_cleanup_and_activate "$want_version"
    echo "Done."
}

# -------- dispatch --------

if [[ "$mode" == "status" ]]; then
    if have_symlink_mode; then
        status_symlink_mode
    else
        conf_only_status
    fi
    exit 0
elif [[ "$mode" == "list" ]]; then
    conf_only_list
    exit 0
elif [[ "$mode" == "switch" ]]; then
    shift || true
    [[ $# -ge 1 ]] || err "Usage: $0 switch <X.Y|cuda-X.Y>"
    if have_symlink_mode; then
        do_switch_symlink_mode "$1"
    else
        conf_only_switch "$1"
    fi
    exit $?
fi

if [[ "$mode" == "off" ]]; then
    if have_symlink_mode; then
        if [[ ! -L "$LOCAL/cuda" ]]; then
            err "/usr/local/cuda is not a symlink, cannot switch off (symlink mode)"
        fi
        target="$(readlink "$LOCAL/cuda")"
        version_name="$(basename "$target")"
        conf_file="$ETCDIR/$(version_to_conf_basename "$version_name")"

        if [[ -e "$OFFDIR/$version_name" ]]; then
            echo "$version_name is Off already"
            exit 0
        fi

        echo "Moving $version_name to $OFFDIR ... (symlink mode)"
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
    else
        conf_only_off
    fi
    exit 0
elif [[ "$mode" == "on" ]]; then
    if have_symlink_mode; then
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

        echo "Restoring $version_name to $LOCAL ... (symlink mode)"
        $SUDO mv "$OFFDIR/$version_name" "$LOCAL/"
        $SUDO mv "$OFFDIR/cuda" "$LOCAL/"

        if [[ -f "$conf_file" ]]; then
            $SUDO mv "$conf_file" "$ETCDIR/"
            log_change "$version_name ld.so.conf restored to $ETCDIR"
        fi

        log_change "$version_name restored to $LOCAL/cuda"
        $SUDO ldconfig
        echo "Done."
    else
        echo "Conf-only mode: No /usr/local/cuda symlink. Please use '$0 switch <X.Y>' to choose a version."
    fi
    exit 0
fi
