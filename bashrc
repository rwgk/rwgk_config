# [ -f "$HOME/rwgk_config/bashrc" ] && . "$HOME/rwgk_config/bashrc"

[ -z "$PS1" ] && return

export PATH="$HOME/rwgk_config/bin:$PATH"
if [ -d "$HOME/bin" ]; then
  export PATH="$HOME/bin:$PATH"
fi
export PATH=".:$PATH"
if [ -x /usr/bin/python -a -f "$HOME/rwgk_config/path_utility.py" ]; then
  export PATH=`/usr/bin/python "$HOME/rwgk_config/path_utility.py" tidy PATH`
fi

if [ -z "$PYTHONPATH" ]; then
  export PYTHONPATH="$HOME/rwgk_config/py"
else
  export PYTHONPATH="$HOME/rwgk_config/py:$PYTHONPATH"
fi
if [ -x /usr/bin/python -a -f "$HOME/rwgk_config/path_utility.py" ]; then
  export PYTHONPATH=`/usr/bin/python "$HOME/rwgk_config/path_utility.py" tidy PYTHONPATH`
fi

export EDITOR=/usr/bin/vi
export VISUAL=/usr/bin/vi

export PS1='$(hostname -f | sed 's/\.skybox\.com$//' | sed 's/\.roam\.corp\.google\.com$//'):\w $ '

export IGNOREEOF=9999

export HISTFILESIZE=1000000
export HISTSIZE=1000000
export HISTCONTROL=ignoredups
shopt -s histappend
export PROMPT_COMMAND='history -a'

export FIGNORE=".o:.so:.a:.pyc"

export COPY_EXTENDED_ATTRIBUTES_DISABLE=true # Mac OS 10.4 or older
export COPYFILE_DISABLE=true # Mac OS 10.5

alias h='history'
alias hh='history | cut -c8-'

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

xattr_clear_recursive() {
  find . \( -type d -o -type f \) -print0 | xargs -0 xattr -c
}

grr() {
  # -I ignores binary files
  grep -I --exclude \*.class --exclude \*.pyc --exclude-dir .git --exclude-dir .svn -r "$@"
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

if [ X`uname` == XDarwin ]; then
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

alias now='date "+%Y-%m-%d+%H%M%S"'

alias mir='rsync --archive --delete --force --verbose --stats'

alias pyprof='python -m cProfile -s cum'

alias svnup='svn update && svn status'
alias sst='svn status'
alias svndiffh='svn diff --diff-cmd=diff -x "-h"' # -h (does nothing)
                                                  # just to override -u
alias svndiffubw='svn diff --diff-cmd=diff -x "-u -b -w"'
alias svnmime='svn propget svn:mime-type'
alias svnignore='svn propedit svn:ignore .'

DegFtoC() {
  python -c 'import sys; print "%.2f" % ((eval(sys.argv[1])-32)*5/9)' "$@"
}
DegCtoF() {
  python -c 'import sys; print "%.2f" % (eval(sys.argv[1])*9/5+32)' "$@"
}
pound_as_kg() {
  python -c 'import sys; print "%.3f" % (eval(sys.argv[1])*0.4536)' "$@"
}
kg_as_pound() {
  python -c 'import sys; print "%.3f" % (eval(sys.argv[1])/0.4536)' "$@"
}

gd() {
  cd "$HOME/Google Drive"
}

apt_list() {
  apt --installed list
}

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

giturl() {
  git config --get remote.origin.url
}

gitstat() {
  git status -s "$@"
}

rebase() {
  git pull --rebase "$@"
}

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
