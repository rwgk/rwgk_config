# ~/.profile: executed by the command interpreter for login shells.
# This file is not read by bash(1), if ~/.bash_profile or ~/.bash_login
# exists.

# 2025-03-17+1607
# https://chatgpt.com/share/67d88b5f-4060-8008-9afa-e401cca1f8f0

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

# Export functions so they’re available in child shells
export -f prepend_maybe
export -f venv_activate_maybe

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

venv_activate_maybe "$HOME/venvs/$(echo "$HOSTNAME" | cut -d'.' -f1)/base"

[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
