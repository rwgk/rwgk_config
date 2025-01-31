# ~/.profile: executed by the command interpreter for login shells.
# This file is not read by bash(1), if ~/.bash_profile or ~/.bash_login
# exists.

if [ -f /.dockerenv ]; then
  if [ -d "/usr/local/cuda/bin" ]; then
    export PATH="/usr/local/cuda/bin:$PATH"
  fi
fi
if [ -d "/Applications/CMake.app/Contents/bin" ]; then
  export PATH="/Applications/CMake.app/Contents/bin:$PATH"
fi
if [ -d "/Applications/Visual Studio Code.app/Contents/Resources/app/bin" ]; then
  export PATH="/Applications/Visual Studio Code.app/Contents/Resources/app/bin:$PATH"
fi
if [ -d "$HOME/.cargo/bin" ]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi
if [ -d "$HOME/.local/bin" ]; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if [ -d "$HOME/bin" ]; then
  export PATH="$HOME/bin:$PATH"
fi
export PATH=".:$HOME/rwgk_config/bin:$PATH"

if [ -z "$PYTHONPATH" ]; then
  export PYTHONPATH="$HOME/rwgk_config/py"
else
  export PYTHONPATH="$HOME/rwgk_config/py:$PYTHONPATH"
fi

[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
