# ~/.profile: executed by the command interpreter for login shells.
# This file is not read by bash(1), if ~/.bash_profile or ~/.bash_login
# exists.

# Export WHOME (Windows home in WSL path form) for all shells, only on WSL.
# - Safe if cmd.exe isn't on PATH
# - Won't overwrite if WHOME already set
if [ -z "${WHOME:-}" ] && command -v wslpath >/dev/null 2>&1; then
    # Locate cmd.exe explicitly (PATH is not reliable in WSL)
    CMD_EXE=""
    for _c in /mnt/c/Windows/System32/cmd.exe /mnt/c/Windows/system32/cmd.exe; do
        [ -x "$_c" ] && CMD_EXE="$_c" && break
    done

    if [ -n "$CMD_EXE" ]; then
        # Prefer %USERPROFILE%, fall back to %HOMEDRIVE%%HOMEPATH%
        _win_userprofile="$("$CMD_EXE" /C "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')"
        if [ -z "$_win_userprofile" ]; then
            _win_userprofile="$("$CMD_EXE" /C "echo %HOMEDRIVE%%HOMEPATH%" 2>/dev/null | tr -d '\r')"
        fi

        if [ -n "$_win_userprofile" ]; then
            export WHOME="$(wslpath "$_win_userprofile")"
        fi

        unset _win_userprofile
    fi
    unset CMD_EXE _c
fi

if [ -f "$HOME/rwgk_config/bash_maybe_use_chd_history" ]; then
    . "$HOME/rwgk_config/bash_maybe_use_chd_history"
    __maybe_use_chd_history
fi

# Remove duplicates, empty fields (::), trailing :, preserve order.
clean_path() {
    echo "$1" | tr ':' '\n' | awk 'NF && !seen[$0]++' | tr '\n' ':' | sed 's/:$//'
}

prepend_maybe() {
    if [ "$#" -ne 2 ]; then
        echo "Usage: prepend_maybe <VARNAME> <VALUE>" >&2
        return 1
    fi

    if [ -d "$2" ]; then
        if [ -z "${!1}" ]; then
            eval "export $1=\"$2\""
        else
            eval "export $1=\"$2:\${$1}\""
        fi
    fi
}

venv_activate_maybe() {
    if [ "$#" -ne 1 ]; then
        echo "Usage: venv_activate_maybe <VENV_PATH>" >&2
        return 1
    fi

    if [ -d "$1" ]; then
        . "$1/bin/activate"
    fi
}

# Silent setup: only sets cache dirs if not already set
silent_use_tmp_user_caches() {
    [ -n "$XDG_CACHE_HOME" ] || XDG_CACHE_HOME="/tmp/${USER}-xdg-cache"
    [ -n "$PIP_CACHE_DIR" ] || PIP_CACHE_DIR="/tmp/${USER}-pip-cache"
    [ -n "$TMPDIR" ] || TMPDIR="/tmp/${USER}-tmp"

    export XDG_CACHE_HOME PIP_CACHE_DIR TMPDIR

    mkdir -p "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" "$TMPDIR"
}

# Show current values (can be useful even outside setup)
show_tmp_user_caches() {
    echo "XDG_CACHE_HOME=$XDG_CACHE_HOME"
    echo "PIP_CACHE_DIR=$PIP_CACHE_DIR"
    echo "TMPDIR=$TMPDIR"
}

# Verbose setup with user feedback
use_tmp_user_caches() {
    silent_use_tmp_user_caches
    echo "✅ Using temporary per-user cache directories:"
    show_tmp_user_caches
}

# Safe cleanup: only removes per-user temp dirs under /tmp
wipe_tmp_user_caches() {
    for d in "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" "$TMPDIR"; do
        case "$d" in
        /tmp/*)
            echo "🧹 Removing $d"
            rm -rf -- "$d"
            ;;
        *)
            echo "⚠️ Skipping non-/tmp path: $d"
            ;;
        esac
    done
    echo "✅ All cleaned up (safe mode)."
}

# Automatically use temp caches if $HOME is on NFS
fs_device=$(df -P "$HOME" | awk 'NR==2 { print $1 }')
case "$fs_device" in
*:/*)
    silent_use_tmp_user_caches
    ;;
esac

setup_vim_dirs() {
    mkdir -p "$HOME/.vim/backup" "$HOME/.vim/swap" "$HOME/.vim/undo"
    echo "✅ Vim directories created"
}

# One-time vim directory setup
if [ ! -d "$HOME/.vim/backup" ]; then
    setup_vim_dirs
fi

# Export functions for use in child bash shells
export -f clean_path
export -f prepend_maybe
export -f venv_activate_maybe
export -f silent_use_tmp_user_caches
export -f show_tmp_user_caches
export -f use_tmp_user_caches
export -f wipe_tmp_user_caches
export -f setup_vim_dirs

if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

if [ -f /.dockerenv ]; then
    prepend_maybe PATH "/usr/local/cuda/bin"
fi
prepend_maybe PATH "/Applications/CMake.app/Contents/bin"
prepend_maybe PATH "/Applications/Visual Studio Code.app/Contents/Resources/app/bin"
prepend_maybe PATH "$HOME/.cargo/bin"
prepend_maybe PATH "$HOME/.local/bin"
prepend_maybe PATH "$HOME/bin"
prepend_maybe PATH "$HOME/rwgk_config/bin"
prepend_maybe PATH .

prepend_maybe PYTHONPATH "$HOME/rwgk_config/py"

[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
