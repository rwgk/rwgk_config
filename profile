# ~/.profile: executed by the command interpreter for login shells.
# This file is not read by bash(1), if ~/.bash_profile or ~/.bash_login
# exists.

if [ -d "/Applications/CMake.app/Contents/bin" ]; then
  export PATH="/Applications/CMake.app/Contents/bin:$PATH"
fi
if [ -d "/Applications/Visual Studio Code.app/Contents/Resources/app/bin" ]; then
  export PATH="/Applications/Visual Studio Code.app/Contents/Resources/app/bin:$PATH"
fi
if [ -d "$HOME/.cargo/bin" ]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi
if [ -d "$HOME/bin" ]; then
  export PATH="$HOME/bin:$PATH"
fi
export PATH=".:$HOME/rwgk_config/bin:$PATH"
if [ -f "$HOME/rwgk_config/path_utility.py" ]; then
  if [ -x /usr/bin/python3 ]; then
    export PATH=`/usr/bin/python3 "$HOME/rwgk_config/path_utility.py" tidy PATH`
  elif [ -x /usr/bin/python ]; then
    export PATH=`/usr/bin/python  "$HOME/rwgk_config/path_utility.py" tidy PATH`
  fi
fi

if [ -z "$PYTHONPATH" ]; then
  export PYTHONPATH="$HOME/rwgk_config/py"
else
  export PYTHONPATH="$HOME/rwgk_config/py:$PYTHONPATH"
fi
if [ -f "$HOME/rwgk_config/path_utility.py" ]; then
  if [ -x /usr/bin/python3 ]; then
    export PYTHONPATH=`/usr/bin/python3 "$HOME/rwgk_config/path_utility.py" tidy PYTHONPATH`
  elif [ -x /usr/bin/python ]; then
    export PYTHONPATH=`/usr/bin/python  "$HOME/rwgk_config/path_utility.py" tidy PYTHONPATH`
  fi
fi

[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
