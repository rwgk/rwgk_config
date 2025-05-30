# ~/.bashrc: executed by bash(1) for non-login shells.

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac
[ -z "$PS1" ] && return

if ((  ${BASH_VERSINFO[0]} < 4 || \
      (${BASH_VERSINFO[0]} == 4 && ${BASH_VERSINFO[1]} < 3) )); then
  export HISTFILESIZE=10000000
  export HISTSIZE=10000000
else
  export HISTFILESIZE=-1
  export HISTSIZE=-1
fi
export HISTTIMEFORMAT='%Y-%m-%d+%H%M%S '
export HISTCONTROL=ignoredups
shopt -s histappend

shopt -s checkwinsize
if [[ ${BASH_VERSINFO[0]} -ge 4 ]]; then
  shopt -s globstar
fi

if [ -x /usr/bin/vim ]; then
  export EDITOR=/usr/bin/vim
elif [ -x /usr/bin/vi ]; then
  export EDITOR=/usr/bin/vi
else
  export EDITOR=
fi
export VISUAL="$EDITOR"

# Ctrl-7 (MacOS)
export MOSH_ESCAPE_KEY=$'\x1f'

if [ -x /bin/sed ]; then
  AbsSed=/bin/sed
elif [ -x /usr/bin/sed ]; then
  AbsSed=/usr/bin/sed
else
  AbsSed=
fi
if [ -z "$AbsSed" ]; then
  export PS1='$(/bin/hostname -f):\w $ '
else
  export PS1='$(/bin/hostname -f | '"$AbsSed"' 's/\.google\.com$//'):\w $ '
fi

export IGNOREEOF=9999

export PROMPT_COMMAND='\history -a'

alias h='\history'
alias hr='\history -r'
hh() {
  \history "$@" | /usr/bin/cut -c8-
}

export FIGNORE=".o:.so:.a:.pyc"

export COPY_EXTENDED_ATTRIBUTES_DISABLE=true # Mac OS 10.4 or older
export COPYFILE_DISABLE=true # Mac OS 10.5
DisableApplePressAndHold() {
  echo 'Re-enabling auto-repeat for a-z character keys.'
  defaults write -g ApplePressAndHoldEnabled -bool false
  echo 'Please restart affected applications.'
}
EnableAppleFastKeyRepeat() {
  echo 'Old parameter:'
  defaults read NSGlobalDomain KeyRepeat
  # 0 caused issues in gmail hangouts subpanel.
  defaults write NSGlobalDomain KeyRepeat -int 1
  echo 'New parameter:'
  defaults read NSGlobalDomain KeyRepeat
  echo 'Please restart affected applications.'
}

alias where='type -a'

alias llb='ls -l'
alias ll='ls -lh'
alias lllb='ls -aAl'
alias lll='ls -aAlh'
lln() {
  ls -aAlt "$@" | head -20
}
llf() {
  ls -l "$@" | grep -v "^d" | grep -v "^total"
}
lld() {
  ls -l "$@" | grep "^d" | grep -v "^total"
}
llg() {
  __="$1"
  shift
  ls -aAl "$@" | grep -i "$__"
  __=
}
fullpath() {
  __="`pwd`"
  for arg in "$@"; do
    echo "$__/$arg"
  done
  __=
}
vman() {
  man "$@" | col -b | vi -R -
}
gtab() {
  egrep -n $'\t| $' "$@"
}
ff() {
  if [ $# -ne 1 ]; then
    echo "ff: ERROR: exactly one argument required, $# given."
    return 1
  fi
  find . -name "$@" -print
}
ffRM() {
  if [ $# -ne 1 ]; then
    echo "ffRM: ERROR: exactly one argument required, $# given."
    return 1
  fi
  find . -name "$@" -print -delete
}

alias dirs_here_xargs='find . -maxdepth 1 -type d \! -name . -print0 | xargs -0'

xattr_clear_recursive() {
  find . \( -type d -o -type f \) -print0 | xargs -0 xattr -c
}

grr() {
  # -I ignores binary files
  grep -I --exclude \*.class --exclude \*.pyc --exclude-dir __pycache__ --exclude-dir .git --exclude-dir .svn --exclude-dir .mypy_cache --exclude-dir .pytest_cache --exclude-dir \*.egg-info -r "$@"
}

cutniq() {
  grep -v 'Binary file' | cut -d: -f1 | uniq
}

cpuinfo() {
  echo -n 'logical cores:  '; cat /proc/cpuinfo | grep '^processor' | wc -l
  echo -n 'hardware cores: '; cat /proc/cpuinfo | egrep '^core id|^physical id' | tr -d "\n" | sed s/physical/\\nphysical/g | grep -v ^$ | sort | uniq | wc -l
  echo -n 'cpu sockets:    '; cat /proc/cpuinfo | grep '^physical id' | sort | uniq | wc -l
}

alias pyclean='find . -name "*.pyc" -print -delete'
alias dsclean='find . -name ".DS_Store" -print -delete'

alias grepytb='grep -i -e exception -e traceback'

if [ X`uname` == XDarwin ]; then
  export BASH_SILENCE_DEPRECATION_WARNING=1
  alias psu='env COLUMNS=100000 ps -U $USER'
  alias psfu='env COLUMNS=100000 ps -f -U $USER'
else
  alias psu='env COLUMNS=100000 ps -u $USER'
  alias psfu='env COLUMNS=100000 ps -f -u $USER'
fi

alias show_ports_in_use='netstat -tulpn'

pid_info() {
  for pid in "$@"; do
    for f in limits status; do
      fp="/proc/$pid/$f"
      echo "${fp}:"
      cat "$fp"
      echo ""
    done
  done
}

alias todate='date "+%Y-%m-%d"'
alias now='date "+%Y-%m-%d+%H%M%S"'
alias nowish='date "+%Y-%m-%d+%H%M"'

alias mir='rsync --archive --delete --force --verbose --stats'

alias svnup='svn update && svn status'
alias sst='svn status'
alias svndiffh='svn diff --diff-cmd=diff -x "-h"' # -h (does nothing)
                                                  # just to override -u
alias svndiffubw='svn diff --diff-cmd=diff -x "-u -b -w"'
alias svnmime='svn propget svn:mime-type'
alias svnignore='svn propedit svn:ignore .'

gd() {
  if [ -d "$HOME/Google Drive/My Drive/" ]; then
    cd "$HOME/Google Drive/My Drive/"
  fi
}

apt_list() {
  apt --installed list
}

if [ -f "$HOME/rwgk_config/git_stuff/git-completion.bash" ]; then
  . "$HOME/rwgk_config/git_stuff/git-completion.bash"
fi

rwgk_gitconfig() {
  if [ $# -ne 1 ]; then
    echo "rwgk_gitconfig: ERROR: exactly one argument required (email), $# given."
    return 1
  fi
  git config --global user.name "Ralf W. Grosse-Kunstleve"
  git config --global user.email "$@"
  git config --global core.editor vi
  git config --global push.default matching
}

alias gb='git branch'
alias gg='git grep'

giturl() {
  git config --get remote.origin.url
}

gls() {
  local num_commits="${1:-10}"
  git log -n "$num_commits" --format="%H %<(100,trunc)%s"
}

gitstat() {
  git status -s "$@"
}

rebase() {
  git pull --rebase "$@"
}

mbd() {
  git diff --merge-base "$@"
}

mbdno() {
  git diff --merge-base "$@" --name-only
}

# https://chatgpt.com/share/67e8331b-6700-8008-9a09-31c9f1611a84  2025-03-29
# Bash completion for show_pr_for_branch.sh
_show_pr_for_branch_completions() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    # Get local branch names (excluding remotes)
    local branches
    branches=$(git for-each-ref --format='%(refname:short)' refs/heads/)

    COMPREPLY=( $(compgen -W "$branches" -- "$cur") )
}
# Register completion function
complete -F _show_pr_for_branch_completions show_pr_for_branch.sh

gerpush() {
  git push origin HEAD:refs/for/master
}

pplint() {
  puppet-lint --no-80chars-check --no-documentation-check "$@"
}

disable() {
  if [ $# -ne 1 ]; then
    echo "disable: ERROR: exactly one argument required (service name), $# given."
    return 1
  fi
  local base="/etc/init/$1"
  local conf="${base}.conf"
  local over="${base}.override"
  if [ ! -f $conf ]; then
    echo "disable: ERROR: $conf does not exist."
    return 1
  fi
  /usr/bin/sudo /bin/true  # Prompt for password before echo -n.
  echo -n "${conf}: "
  echo manual | /usr/bin/sudo /usr/bin/tee $over
}

sum_file_sizes() {
  find . \! -regex '^\./\.git/.*' -type f -print0 | wc -c --files0-from=- | sort -n | tail
}

oc() {
  octave -q "$@"
}

setup_nim() {
  export PATH="$HOME/clone/Nim/bin:$PATH"
  alias toast="$HOME/clone/nimterop/nimterop/toast"
}

fresh_venv() {
  if [ "$#" -ne 1 ]; then
    echo "fresh_venv: ERROR: exactly one argument required (venv name), $# given." >&2
    return 1
  fi

  local venv_dir="$1"

  if [ -d "$venv_dir" ]; then
    echo "fresh_venv: ERROR: directory '$venv_dir' already exists. Please remove it first." >&2
    return 1
  fi

  if ! python -m venv "$venv_dir"; then
    echo "fresh_venv: ERROR: failed to create virtual environment." >&2
    return 1
  fi

  # shellcheck disable=SC1090
  . "$venv_dir/bin/activate"

  pip install --upgrade pip

  if [ -f requirements.txt ]; then
    pip install -r requirements.txt
  else
    echo "fresh_venv: NOTE: no requirements.txt found, skipping dependency installation."
  fi
}

alias vba='. "$HOME/venvs/$(echo "$HOSTNAME" | cut -d'.' -f1)/base/bin/activate"'
alias acd='. "$HOME/cccl/python/devenv/bin/activate"'

# https://www.commandlinefu.com/commands/view/12043/remove-color-special-escape-ansi-codes-from-text-with-sed
alias strip_ansi_esc='sed "s,\x1B\[[0-9;]*[a-zA-Z],,g"'

alias ListAllCommands='compgen -A function -abck'

alias cfd='clang-format --dry-run'
alias cfI='clang-format -i'
alias cffd='clang-format -style=file --dry-run'
alias cffI='clang-format -style=file -i'
alias cfpybind11d="clang-format --style=file:$HOME/forked/pybind11/.clang-format --dry-run"
alias cfpybind11I="clang-format --style=file:$HOME/forked/pybind11/.clang-format -i"
alias cleanup_build_dir='$HOME/clone/pybind11_scons/cleanup_build_dir.sh'
alias sconshold='scons extra_defines=PYBIND11_USE_SMART_HOLDER_AS_DEFAULT'
alias sconshnew='scons extra_defines=PYBIND11_RUN_TESTING_WITH_SMART_HOLDER_AS_DEFAULT_BUT_NEVER_USE_IN_PRODUCTION_PLEASE'

alias pipup='pip install --upgrade pip'

export MY_GITHUB_USERNAME=rwgk

# `gh auth login` creates ~/.config/gh/hosts.yml
alias show_github_token='yq -r '\''."github.com".oauth_token'\'' "$HOME/.config/gh/hosts.yml"'

vscode_settings_dir="$HOME/Library/Application Support/Code/User/"
alias cd_vscode_settings_dir='cd "$vscode_settings_dir"'

alias mfini='source "$HOME/miniforge3/etc/profile.d/conda.sh"'

export NUMBA_CAPTURED_ERRORS="new_style"

[ -f "$HOME/.bashrc_org" ] && . "$HOME/.bashrc_org"
[ -f "$HOME/.bashrc_os" ] && . "$HOME/.bashrc_os"
[ -f "$HOME/.bashrc_host" ] && . "$HOME/.bashrc_host"

if [ -f "$HOME/rwgk_config/path_utility.py" ]; then
  if [ -x /usr/bin/python3 ]; then
    export PATH=`/usr/bin/python3 "$HOME/rwgk_config/path_utility.py" tidy PATH`
  elif [ -x /usr/bin/python ]; then
    export PATH=`/usr/bin/python  "$HOME/rwgk_config/path_utility.py" tidy PATH`
  fi
fi

if [ -f "$HOME/rwgk_config/path_utility.py" ]; then
  if [ -x /usr/bin/python3 ]; then
    export PYTHONPATH=`/usr/bin/python3 "$HOME/rwgk_config/path_utility.py" tidy PYTHONPATH`
  elif [ -x /usr/bin/python ]; then
    export PYTHONPATH=`/usr/bin/python  "$HOME/rwgk_config/path_utility.py" tidy PYTHONPATH`
  fi
fi
