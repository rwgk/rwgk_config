# ~/.profile: executed by the command interpreter for login shells.
# This file is not read by bash(1), if ~/.bash_profile or ~/.bash_login
# exists.

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

# Silent setup: sets cache dirs without echoing
silent_use_tmp_user_caches() {
    XDG_CACHE_HOME="/tmp/${USER}-xdg-cache"
    PIP_CACHE_DIR="/tmp/${USER}-pip-cache"
    TMPDIR="/tmp/${USER}-tmp"

    export XDG_CACHE_HOME PIP_CACHE_DIR TMPDIR

    mkdir -p "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" "$TMPDIR"
}

show_tmp_user_caches() {
    echo "XDG_CACHE_HOME=$XDG_CACHE_HOME"
    echo "PIP_CACHE_DIR=$PIP_CACHE_DIR"
    echo "TMPDIR=$TMPDIR"
}

# Verbose version: prints what's being set
use_tmp_user_caches() {
    silent_use_tmp_user_caches
    echo "✅ Using temporary per-user cache directories:"
    show_tmp_user_caches
}

# Cleanup function to delete the temp dirs
wipe_tmp_user_caches() {
    echo "🧹 Removing temporary per-user cache directories..."
    rm -rf "/tmp/${USER}-xdg-cache" "/tmp/${USER}-pip-cache" "/tmp/${USER}-tmp"
    echo "✅ All cleaned up."
}

# Automatically use temp caches if $HOME is on NFS
fs_device=$(df -P "$HOME" | awk 'NR==2 { print $1 }')
case "$fs_device" in
*:/*)
    silent_use_tmp_user_caches
    ;;
esac

# Export functions so they’re available in child shells
export -f prepend_maybe
export -f venv_activate_maybe
export -f silent_use_tmp_user_caches
export -f show_tmp_user_caches
export -f use_tmp_user_caches
export -f wipe_tmp_user_caches

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
