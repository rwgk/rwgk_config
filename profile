# ~/.profile: executed by the command interpreter for login shells.
# This file is not read by bash(1), if ~/.bash_profile or ~/.bash_login
# exists.

umask 0022

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

if [ -f "$HOME/rwgk_config/local_dotdirs_env" ]; then
    . "$HOME/rwgk_config/local_dotdirs_env"
fi

check_rwgkscrts_permissions() {
    local secrets_dir="$HOME/.rwgkscrts"
    local path

    [ -e "$secrets_dir" ] || return 0

    while IFS= read -r path; do
        echo "WARNING: insecure permissions on secret path: $path" >&2
        ls -ld "$path" >&2
    done < <(find "$secrets_dir" -perm /077 -print 2>/dev/null)
}

check_rwgkscrts_permissions

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
export -f check_rwgkscrts_permissions
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
