# ~/.bashrc: executed by bash(1) for non-login shells.

umask 0022

# --- Early return guard ------------------------------------------------------
# Load full shell environment if:
#   1. PRETEND_INTERACTIVE_SHELL is set (forced for special cases, e.g. pbpush/pbpull), OR
#   2. PS1 is set (normal interactive prompt), OR
#   3. Shell options string ($-) contains 'i' (interactive shell)
#
if [[ -n "$PRETEND_INTERACTIVE_SHELL" ]]; then
    shopt -s expand_aliases
else
    # If PS1 is empty, we *might* be non-interactive
    if [[ -z "$PS1" ]]; then
        case "$-" in
        *i*) : ;;    # interactive → continue
        *) return ;; # truly non-interactive → bail out early
        esac
    fi
fi
# --- end early return guard --------------------------------------------------

# Silence Ubuntu MOTD by default
[[ -d /etc/update-motd.d && ! -f ~/.hushlogin ]] && touch ~/.hushlogin

show_motd() {
    # Ubuntu/Debian MOTD (matches pam_motd behavior)
    run-parts /etc/update-motd.d/
}

export RC="$HOME/rwgk_config"
alias RC='cd "$RC"'

if [ -f "$HOME/rwgk_config/bash_maybe_use_chd_history" ]; then
    . "$HOME/rwgk_config/bash_maybe_use_chd_history"
    __maybe_use_chd_history
fi
shopt -s histappend
if ((${BASH_VERSINFO[0]} < 4 || (\
    ${BASH_VERSINFO[0]} == 4 && ${BASH_VERSINFO[1]} < 3))); then
    export HISTFILESIZE=10000000
    export HISTSIZE=10000000
else
    export HISTFILESIZE=-1
    export HISTSIZE=-1
fi
export HISTTIMEFORMAT='%Y-%m-%d+%H%M%S '
export HISTCONTROL=ignoredups
alias vihf='vim "$HISTFILE"'

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

mosh_ls() {
    pgrep -a mosh-server
    sudo ss -lunp | grep mosh-server
}

if [[ "$OSTYPE" == "msys" ]]; then
    # Git Bash
    export PS1='$(hostname):$PWD $ '
else
    export PS1='$(/bin/hostname -f):\w $ '
fi

export IGNOREEOF=9999

alias cd.='cd "$(realpath .)"'

_smart_prompt_command() {
    # Always append history
    builtin history -a

    # Update terminal title in interactive terminals
    if [[ $- == *i* ]] && [[ -t 1 ]]; then
        local pwd_length=26
        local pwd_display="$PWD"

        # Replace home directory with ~
        if [[ "$PWD" == "$HOME"* ]]; then
            pwd_display="~${PWD#$HOME}"
        fi

        # Get the basename (last part of path)
        local basename="${pwd_display##*/}"

        # If basename alone is longer than pwd_length, just use basename
        if [ ${#basename} -gt $pwd_length ]; then
            pwd_display="$basename"
        # Otherwise, truncate from the beginning if the full path is too long
        elif [ ${#pwd_display} -gt $pwd_length ]; then
            pwd_display="...${pwd_display: -$(($pwd_length - 3))}"
        fi

        echo -ne "\033]0;${pwd_display}\007"
    fi
}

export PROMPT_COMMAND='_smart_prompt_command'

histsync() {
    builtin history -a
    builtin history -c
    builtin history -r
}

rebash() {
    set -x
    exec bash -l
}

hostfqdn() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import socket; print(socket.getfqdn())'
    else
        hostname -f
    fi
}

export FIGNORE=".o:.so:.a:.pyc"

export COPY_EXTENDED_ATTRIBUTES_DISABLE=true # Mac OS 10.4 or older
export COPYFILE_DISABLE=true                 # Mac OS 10.5
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
SetAppleScreenshotLocation() {
    if [ "$#" -gt 1 ]; then
        echo "usage: SetAppleScreenshotLocation [directory]" >&2
        return 1
    fi

    local location=${1:-"$HOME/Downloads"}
    echo "Setting screenshot location to: $location"
    defaults write com.apple.screencapture location "$location"
    killall SystemUIServer
    echo 'Screenshot location updated.'
}
SetAppleScreenshotLocationDownloads() {
    SetAppleScreenshotLocation "$HOME/Downloads"
}
DisableScreenshotFloatingThumbnail() {
    defaults write com.apple.screencapture show-thumbnail -bool false
    killall SystemUIServer
}

# pbff: PasteBoard From File
# Copy the contents of a file into the pasteboard (clipboard).
pbff() {
    if [ "$#" -ne 1 ]; then
        echo "pbff: exactly one filename argument required" >&2
        echo "usage: pbff <file>" >&2
        return 1
    fi

    local file=$1

    if [ ! -e "$file" ]; then
        echo "pbff: file not found: $file" >&2
        return 1
    fi

    if [ ! -r "$file" ]; then
        echo "pbff: file not readable: $file" >&2
        return 1
    fi

    # Use the system pbcopy, reading from the file
    pbcopy <"$file"
}

# pbtf: PasteBoard To File
# Write the contents of the pasteboard (clipboard) into a file.
# Overwrites the file if it exists (like `> file`).
pbtf() {
    if [ "$#" -ne 1 ]; then
        echo "pbtf: exactly one filename argument required" >&2
        echo "usage: pbtf <file>" >&2
        return 1
    fi

    local file=$1
    local dir

    dir=$(dirname -- "$file")

    # Ensure directory exists and is writable
    if [ ! -d "$dir" ]; then
        echo "pbtf: directory does not exist: $dir" >&2
        return 1
    fi

    if [ ! -w "$dir" ]; then
        echo "pbtf: directory not writable: $dir" >&2
        return 1
    fi

    # If file exists but is a directory, fail
    if [ -d "$file" ]; then
        echo "pbtf: path is a directory, not a file: $file" >&2
        return 1
    fi

    # Write clipboard contents to the file (overwrite)
    pbpaste >"$file"
}

# pbpush: push local (e.g. macOS) clipboard to remote (e.g. WSL2) clipboard
pbpush() {
    if [ "$#" -ne 1 ]; then
        echo "pbpush: exactly one hostname argument required" >&2
        echo "usage: pbpush <ssh-target>" >&2
        return 1
    fi

    local host=$1

    if ! command -v pbpaste >/dev/null 2>&1; then
        echo "pbpush: pbpaste not found on this system (expected macOS)." >&2
        return 1
    fi

    if ! command -v ssh >/dev/null 2>&1; then
        echo "pbpush: ssh not found in PATH." >&2
        return 1
    fi

    if ! command -v scp >/dev/null 2>&1; then
        echo "pbpush: scp not found in PATH." >&2
        return 1
    fi

    # Write local clipboard to a temporary file, copy that file to the remote,
    # and then let the remote pbcopy read from it. This avoids streaming data
    # over ssh stdin, which has proven unreliable in some setups.
    local tmp_local
    tmp_local=$(mktemp /tmp/pbpush_local.XXXXXX) || {
        echo "pbpush: failed to create local temporary file" >&2
        return 1
    }

    if ! pbpaste >"$tmp_local"; then
        echo "pbpush: failed to read local clipboard" >&2
        rm -f "$tmp_local"
        return 1
    fi

    # Unique-ish remote temp file path; best-effort cleanup on remote side
    local tmp_remote="/tmp/pbpush_remote.$$.$RANDOM"

    if ! scp "$tmp_local" "$host:$tmp_remote" >/dev/null 2>&1; then
        echo "pbpush: failed to copy temporary file to remote host '$host'." >&2
        rm -f "$tmp_local"
        return 1
    fi

    if ! ssh -T "$host" "
        PRETEND_INTERACTIVE_SHELL=1 . ~/.profile
        if ! command -v pbcopy >/dev/null 2>&1; then
            echo \"pbpush(remote): pbcopy not found on remote host.\" >&2
            rm -f '$tmp_remote'
            exit 1
        fi
        pbcopy <'$tmp_remote'
        rm -f '$tmp_remote'
    "; then
        echo "pbpush: failed to push clipboard to '$host'." >&2
        rm -f "$tmp_local"
        return 1
    fi

    rm -f "$tmp_local"
}

# pbpull: pull remote clipboard (e.g. WSL2) into local (e.g. macOS) clipboard
pbpull() {
    if [ "$#" -ne 1 ]; then
        echo "pbpull: exactly one hostname argument required" >&2
        echo "usage: pbpull <ssh-target>" >&2
        return 1
    fi

    local host=$1

    if ! command -v pbcopy >/dev/null 2>&1; then
        echo "pbpull: pbcopy not found on this system (expected macOS)." >&2
        return 1
    fi

    if ! command -v ssh >/dev/null 2>&1; then
        echo "pbpull: ssh not found in PATH." >&2
        return 1
    fi

    # Run remote pbpaste inside a bash login shell, then feed its output to local pbcopy.
    if ! ssh -T "$host" 'PRETEND_INTERACTIVE_SHELL=1 . ~/.profile; pbpaste' | pbcopy; then
        echo "pbpull: failed to pull clipboard from '$host'." >&2
        return 1
    fi
}

smv() {
    "$HOME"/rwgk_config/bin/save_or_move.sh smv "$@"
}

sve() {
    "$HOME"/rwgk_config/bin/save_or_move.sh sve "$@"
}

mts() {
    "$HOME"/rwgk_config/bin/mtimestamp.sh move "$@"
}

mdup() {
    "$HOME"/rwgk_config/bin/mtimestamp.sh dup "$@"
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
    __="$(pwd)"
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

find_here_between() {
    if [ "$#" -ne 2 ]; then
        echo "find_here_between: ERROR: exactly two arguments required, $# given." >&2
        echo 'Usage: find_here_between "YYYY-MM-DD HH:MM" "YYYY-MM-DD HH:MM"' >&2
        return 1
    fi

    local start="$1"
    local end="$2"
    local here
    here=$(realpath .) || return 1

    if command -v gfind >/dev/null 2>&1; then
        gfind "$here" -type f -newermt "$start" ! -newermt "$end" \
            -printf "%TY-%Tm-%Td %TH:%TM:%TS %p\n"
    elif [ "$(uname)" = "Darwin" ]; then
        find "$here" -type f -newermt "$start" ! -newermt "$end" \
            -exec stat -f '%Sm %N' -t '%Y-%m-%d %H:%M:%S' {} \;
    else
        find "$here" -type f -newermt "$start" ! -newermt "$end" \
            -printf "%TY-%Tm-%Td %TH:%TM:%TS %p\n"
    fi
}

wipe_pycache() {
    local dirs=("$@")

    # If no arguments given, default to "."
    if [ ${#dirs[@]} -eq 0 ]; then
        dirs=(".")
    fi

    # Validate arguments first
    local valid_dirs=()
    for d in "${dirs[@]}"; do
        if [ ! -e "$d" ]; then
            echo "INFO: Not a file or directory: $d"
        elif [ -d "$d" ]; then
            valid_dirs+=("$d")
        else
            echo "INFO: Not a directory: $d"
        fi
    done

    [ ${#valid_dirs[@]} -eq 0 ] && {
        echo "Nothing to do (no valid directories provided)"
        return 0
    }

    # Collect matches
    local tmp
    tmp=$(mktemp)
    find "${valid_dirs[@]}" -type d -name __pycache__ -print0 >"$tmp"

    local count
    count=$(tr -cd '\0' <"$tmp" | wc -c)

    if [ "$count" -gt 0 ]; then
        xargs -0 rm -rf <"$tmp"
        echo "$count __pycache__ director$([ "$count" -eq 1 ] && echo 'y' || echo 'ies') deleted"
    else
        echo "Nothing to do (no __pycache__ directories found)"
    fi

    rm -f "$tmp"
}

alias dirs_here_xargs='find . -maxdepth 1 -type d \! -name . -print0 | xargs -0'

xattr_clear_recursive() {
    find . \( -type d -o -type f \) -print0 | xargs -0 xattr -c
}

cmdx() {
    echo "+ $*"
    "$@"
}

gov() {
    # git overview (fetch first; show how the repo is "out of sync" or WIP)
    # Optional: limit per-category file listing (default 5)
    local MAX="${GOV_MAX:-5}"

    # Must be in a git work tree
    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
        echo "(not a git repository)"
        return 1
    }

    # Update tracking info
    git fetch --prune --quiet || echo "(fetch failed)"

    # Branch & upstream
    local branch upstream ahead=0 behind=0
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
        upstream='@{u}'
        ahead=$(git rev-list --count "${upstream}..HEAD" 2>/dev/null || echo 0)
        behind=$(git rev-list --count "HEAD..${upstream}" 2>/dev/null || echo 0)
    else
        upstream="(no upstream)"
    fi

    # Porcelain scan
    local staged=0 unstaged=0 untracked=0 conflicted=0
    local list_staged="" list_unstaged="" list_untracked="" list_conflicted=""

    # Read git status --porcelain (v1)
    while IFS= read -r line; do
        # Lines look like "XY filename" (or "?? filename" for untracked)
        # X: index status; Y: worktree status
        local X="${line:0:1}" Y="${line:1:1}" file="${line:3}"
        if [[ "$X$Y" == "??" ]]; then
            ((untracked++))
            list_untracked+="$file"$'\n'
        elif [[ "$X" == "U" || "$Y" == "U" || ("$X" == "A" && "$Y" == "A") || ("$X" == "D" && "$Y" == "D") ]]; then
            ((conflicted++))
            list_conflicted+="$file"$'\n'
        else
            if [[ "$X" != " " ]]; then
                ((staged++))
                list_staged+="$file"$'\n'
            fi
            if [[ "$Y" != " " ]]; then
                ((unstaged++))
                list_unstaged+="$file"$'\n'
            fi
        fi
    done < <(git status --porcelain)

    # Stash count (0 if none)
    local stash=0
    if git rev-parse --quiet --verify refs/stash >/dev/null 2>&1; then
        stash=$(git rev-list --count refs/stash 2>/dev/null || echo 0)
    fi

    # Summary line
    printf "%s " "$branch"
    if [[ "$upstream" == "(no upstream)" ]]; then
        printf "[no upstream]"
    else
        [[ $ahead -gt 0 ]] && printf "↑%d " "$ahead"
        [[ $behind -gt 0 ]] && printf "↓%d " "$behind"
        [[ $ahead -eq 0 && $behind -eq 0 ]] && printf "✓ "
        printf "(vs %s)" "$(git rev-parse --abbrev-ref "$upstream")"
    fi
    printf " | staged:%d unstaged:%d untracked:%d conflicted:%d stash:%d\n" \
        "$staged" "$unstaged" "$untracked" "$conflicted" "$stash"

    # Helper to print up to MAX files from a newline list
    _gov_print_list() {
        local header="$1" list="$2" count="$3"
        [[ $count -eq 0 ]] && return 0
        echo "$header:"
        # Print up to MAX lines
        printf "%s" "$list" | sed -e '/^$/d' | head -n "$MAX" | sed 's/^/  - /'
        # "N more" note if truncated
        if ((count > MAX)); then
            printf "  … (%d more)\n" "$((count - MAX))"
        fi
    }

    _gov_print_list "  Staged" "$list_staged" "$staged"
    _gov_print_list "  Unstaged" "$list_unstaged" "$unstaged"
    _gov_print_list "  Untracked" "$list_untracked" "$untracked"
    _gov_print_list "  Conflicted" "$list_conflicted" "$conflicted"
}

RWGK_CONFIG_REPOS=rwgk_config
# Extend like this:
# RWGK_CONFIG_REPOS="$RWGK_CONFIG_REPOS:something_else"

prc() {
    (
        # pull rwgk configs only if needed (polite about WIP)
        if [[ $# -ne 0 ]]; then
            echo "Usage: prc (no arguments allowed)" 1>&2
            return 1
        fi

        # Split colon-separated list into an array (like PATH)
        local IFS=:
        local repos=()
        read -r -a repos <<<"${RWGK_CONFIG_REPOS}"

        for cfg in "${repos[@]}"; do
            [[ -z "$cfg" ]] && continue # ignore empty entries (::)
            echo "$cfg"
            cd "$HOME/$cfg" || {
                echo "  (cannot cd)"
                continue
            }

            # --- your existing logic below ---
            # refresh, decide, gov, conditional pull, etc.
            git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
                echo "  (not a git repository)"
                continue
            }

            git fetch --prune --quiet || echo "  (fetch failed)"

            local branch upstream ahead=0 behind=0
            branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
            if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
                upstream='@{u}'
                ahead=$(git rev-list --count "${upstream}..HEAD" 2>/dev/null || echo 0)
                behind=$(git rev-list --count "HEAD..${upstream}" 2>/dev/null || echo 0)
            else
                upstream=""
            fi

            local dirty=0
            [[ -n "$(git status --porcelain)" ]] && dirty=1

            if [[ -z "$upstream" ]]; then
                echo "  (no upstream for $branch)"
                gov
                continue
            fi

            if ((behind > 0)); then
                if ((dirty)); then
                    echo "  ↓ behind by $behind but worktree not clean — skipping pull"
                    gov
                    continue
                fi
                gov
                if git pull --ff-only; then
                    gov
                else
                    echo "  (pull failed)"
                fi
                continue
            fi

            if ((ahead > 0 || dirty)); then
                gov
            else
                echo "  in sync with $(git rev-parse --abbrev-ref "$upstream")"
            fi
        done
    )
}

grr() {
    # -I ignores binary files
    grep -I --exclude \*.class --exclude \*.pyc --exclude-dir __pycache__ --exclude-dir .git --exclude-dir .svn --exclude-dir .mypy_cache --exclude-dir .pytest_cache --exclude-dir \*.egg-info -r "$@"
}

grep_installed_cuda() {
    echo "$@"
    grep -a -e '^Successfully built cuda-' -e '^Successfully installed cuda-' "$@"
}

grep_pytest_summary() {
    local pattern='^rootdir: |==== .*?(passed|failed|skipped|errors).* ===='

    echo "$@"
    if command -v rg >/dev/null 2>&1; then
        rg -a "$pattern" "$@"
    else
        grep -a -E "$pattern" "$@"
    fi
}

alias myshfmt='shfmt -i 4 -w'

wait_watch() {
    local target="$1"
    if [[ -z "$target" ]]; then
        echo "Usage: wait_watch <directory>"
        return 1
    fi

    echo "$(date +"%F %T") Waiting for directory to appear: $target"
    while [ ! -d "$target" ]; do sleep 1; done

    echo "$(date +"%F %T") Directory found. Starting inotifywait on: $target"
    inotifywait -m -r -e create -e modify \
        --format '%T %e %w%f' \
        --timefmt '%F %T' \
        "$target"
}

watch_pybind11_common_h() {
    if [ "$#" -ne 1 ]; then
        echo "Usage: watch_pybind11_common_h_poll <path/to/file>"
        return 1
    fi

    local file="$1"
    echo "$(date +"%F %T") Waiting for $file to exist..."
    while [ ! -f "$file" ]; do
        sleep 0.5
    done

    local prev
    prev=$(md5sum "$file" | awk '{print $1}')
    echo "$(date +"%F %T") Detected $file. md5=$prev"
    echo "$(date +"%F %T") Version macros:"
    grep '#define PYBIND11_VERSION_' "$file" || echo "none"

    echo "$(date +"%F %T") Watching for md5 changes; press Ctrl-C to stop."
    while true; do
        sleep 0.5
        if [ ! -f "$file" ]; then
            echo "$(date +"%F %T") $file removed"
            break
        fi
        hash=$(md5sum "$file" | awk '{print $1}')
        if [ "$hash" != "$prev" ]; then
            echo "$(date +"%F %T") md5 changed: $hash"
            grep '#define PYBIND11_VERSION_' "$file" || echo "none"
            prev=$hash
        fi
    done
}

cutniq() {
    grep -v 'Binary file' | cut -d: -f1 | uniq
}

mkdirown() {
    if [ $# -ne 1 ]; then
        echo "ERROR: exactly one argument required (path), $# given."
        return 1
    fi
    local path="$1"
    sudo mkdir -p "$path" && sudo chown "$(id -u):$(id -g)" "$path"
    return $?
}

cpuinfo() {
    echo -n 'logical cores:  '
    cat /proc/cpuinfo | grep '^processor' | wc -l
    echo -n 'hardware cores: '
    cat /proc/cpuinfo | egrep '^core id|^physical id' | tr -d "\n" | sed s/physical/\\nphysical/g | grep -v ^$ | sort | uniq | wc -l
    echo -n 'cpu sockets:    '
    cat /proc/cpuinfo | grep '^physical id' | sort | uniq | wc -l
}

cpumem() {
    cmdx lscpu | grep -v -e '^Flags:' -e '^Vulnerability '
    echo
    cmdx free -h
    echo
}

alias pyclean='find . -name "*.pyc" -print -delete'
alias dsclean='find . -name ".DS_Store" -print -delete'

alias grepytb='grep -i -e exception -e traceback'

if [ X$(uname) == XDarwin ]; then
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

# Helper function to create log file timestamp ("make log timestamp")
# Uses timezone offset if not in Los Angeles timezone to reduce the potential for confusion
mlt() {
    local TZ_ABBR
    TZ_ABBR="$(date +%Z 2>/dev/null || echo "")"

    # Check if we're in Los Angeles timezone (PST/PDT)
    if [[ "$TZ_ABBR" == "PST" ]] || [[ "$TZ_ABBR" == "PDT" ]]; then
        date "+%Y-%m-%d+%H%M%S"
    else
        date "+%Y-%m-%d+%H%M%S%z"
    fi
}

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

apt_gh_setup() {
    local keyring_file="/usr/share/keyrings/githubcli-archive-keyring.gpg"
    local sources_file="/etc/apt/sources.list.d/github-cli.list"

    # Check if setup is already done
    if [[ -f "$keyring_file" && -f "$sources_file" ]]; then
        echo "GitHub CLI repository already configured, skipping setup steps."
    else
        # Remove existing gh package if present
        sudo apt remove gh

        # Download and install keyring
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of="$keyring_file"
        sudo chmod go+r "$keyring_file"

        # Add repository to sources
        echo "deb [arch=$(dpkg --print-architecture) signed-by=$keyring_file] https://cli.github.com/packages stable main" | sudo tee "$sources_file"
    fi

    # Always update and install
    sudo apt update
    sudo apt install gh
}

if [ -f "$HOME/rwgk_config/git_stuff/git-completion.bash" ]; then
    . "$HOME/rwgk_config/git_stuff/git-completion.bash"
fi

loggrep() (
    if [ $# -lt 2 ]; then
        echo "Usage: loggrep PATTERN FILE" >&2
        return 1
    fi

    pattern="$1"
    shift

    # Strip NULs and CR characters, then grep
    tr -d '\000' <"$@" | sed 's/\r$//' | grep -E "$pattern"
)

alias gb='git branch'
alias gg='git grep'
alias gs='git status'
alias gsi='git status --ignored'
alias gcd='cd "$(git rev-parse --show-toplevel)"'

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

install_git_pre_push_hook() (
    # Parse options
    FORCE=false
    while [[ $# -gt 0 ]]; do
        case $1 in
        -f)
            FORCE=true
            shift
            ;;
        *)
            echo "Error: Unknown option $1"
            echo "Usage: install_git_pre_push_hook [-f]"
            return 1
            ;;
        esac
    done

    # Check if current directory is a git repo
    if [[ ! -e ".git" ]]; then
        echo "Error: Current directory is not a git repository (no .git found)"
        return 1
    fi

    # Check if pre-push hook already exists
    if [[ -f ".git/hooks/pre-push" ]]; then
        echo "Existing pre-push hook found:"
        echo "--- .git/hooks/pre-push ---"
        cat ".git/hooks/pre-push"
        echo "--- end ---"

        if [[ "$FORCE" != true ]]; then
            echo "Error: pre-push hook already exists at .git/hooks/pre-push (use -f to force overwrite)"
            return 1
        else
            echo "Force mode: overwriting existing hook"
        fi
    fi

    # Check if source hook file exists
    HOOK_SOURCE="$HOME/rwgk_config/git_stuff/pre-push"
    if [[ ! -f "$HOOK_SOURCE" ]]; then
        echo "Error: Hook source file not found: $HOOK_SOURCE"
        return 1
    fi

    # Copy and install the hook
    if cp -a "$HOOK_SOURCE" ".git/hooks/pre-push"; then
        chmod +x ".git/hooks/pre-push"
        echo "✓ Installed pre-push hook from $HOOK_SOURCE"
    else
        echo "Error: Failed to copy pre-push hook"
        return 1
    fi
)

# Auto-install pre-push hook for rwgk_config repo itself
if [[ -e "$HOME/rwgk_config/.git" && ! -f "$HOME/rwgk_config/.git/hooks/pre-push" ]]; then
    (
        cd "$HOME/rwgk_config"
        install_git_pre_push_hook
    )
fi

_git_branch_D_track_hash_helper() (
    set -euo pipefail

    if [[ $# -ne 1 ]]; then
        echo "ASSERTION FAILURE: _git_branch_D_track_hash_helper requires exactly 1 argument (got $#)" >&2
        return 1
    fi

    branch="$1"

    # Fail early if neither environment variable is set
    if [[ -z "${MY_GIT_BACKTRACKING_INFO_LOCAL:-}" && -z "${MY_GIT_BACKTRACKING_INFO_REMOTE:-}" ]]; then
        echo "Error: Neither MY_GIT_BACKTRACKING_INFO_LOCAL nor MY_GIT_BACKTRACKING_INFO_REMOTE is set." >&2
        return 1
    fi

    # Ensure the branch exists
    if ! git rev-parse --verify "$branch" >/dev/null 2>&1; then
        echo "Error: branch '$branch' not found." >&2
        return 1
    fi

    # Refuse to delete the currently checked-out branch
    current_branch=$(git symbolic-ref --short HEAD 2>/dev/null) || true
    if [[ "$branch" == "$current_branch" ]]; then
        echo "Error: '$branch' is the current branch. Switch to another branch first." >&2
        return 1
    fi

    commit=$(git rev-parse "$branch")
    repo=$(basename "$(git rev-parse --show-toplevel)")
    timestamp=$(date +%Y-%m-%d+%H%M%S)
    safe_repo="${repo//[^A-Za-z0-9._@-]/_}"
    safe_branch="${branch//[^A-Za-z0-9._@-]/_}"
    infofile="/tmp/${safe_repo}_${safe_branch}_${timestamp}.txt"

    {
        echo "Current host: '$(hostfqdn)'"
        echo "Current working directory: '$(pwd)'"
        echo "Repository: '$repo'"
        echo "Archiving \`git show --stat --summary\` output before \`git branch -D \"$branch\"\`"
        echo
        git show --stat --summary "$commit"
    } >"$infofile"

    # Copy to local destination if MY_GIT_BACKTRACKING_INFO_LOCAL is set
    if [[ -n "${MY_GIT_BACKTRACKING_INFO_LOCAL:-}" ]]; then
        cp -p "$infofile" "$MY_GIT_BACKTRACKING_INFO_LOCAL/"
        echo "Backed up branch tip to: '$MY_GIT_BACKTRACKING_INFO_LOCAL/$(basename "$infofile")'"
    # Otherwise, scp to remote destination if MY_GIT_BACKTRACKING_INFO_REMOTE is set
    elif [[ -n "${MY_GIT_BACKTRACKING_INFO_REMOTE:-}" ]]; then
        scp -p "$infofile" "$MY_GIT_BACKTRACKING_INFO_REMOTE"
        echo "Backed up branch tip to: '$MY_GIT_BACKTRACKING_INFO_REMOTE'"
    fi

    git branch -D "$branch"
)

git_branch_D_track_hash() {
    if [[ $# -eq 0 ]]; then
        echo "Usage: git_branch_D_track_hash <branch> [<branch> ...]" >&2
        return 1
    fi
    for branch in "$@"; do
        _git_branch_D_track_hash_helper "$branch" || return
    done
}

_git_branch_D_track_hash_COMPLETE() {
    # only complete if we're in a git repo
    git rev-parse --git-dir >/dev/null 2>&1 || {
        COMPREPLY=()
        return
    }

    local cur="${COMP_WORDS[COMP_CWORD]}"
    # Use newline as separator so branch names with slashes (or spaces) work
    local IFS=$'\n'
    # List local branches (same as completing for `git branch -D`)
    local branches
    mapfile -t branches < <(git for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null)

    COMPREPLY=($(compgen -W "${branches[*]}" -- "$cur"))
}

complete -o bashdefault -o default -F _git_branch_D_track_hash_COMPLETE git_branch_D_track_hash

git_log_between() {
    if [ "$#" -lt 2 ]; then
        echo "Usage: git_log_between <from> <to> [path...]" >&2
        return 1
    fi
    local from=$1
    local to=$2
    shift 2
    git log --no-merges --name-status "${from}..${to}" -- "$@"
}

git_logdiff_between() {
    if [ "$#" -lt 2 ]; then
        echo "Usage: git_logdiff_between <from> <to> [path...]" >&2
        return 1
    fi
    local from=$1
    local to=$2
    shift 2
    git log --no-merges --patch "${from}..${to}" -- "$@"
}

git_log_search() {
    git log --all --reverse --date=short --format='%ad %h %an %s' -S "$@"
}

git_log_grep() {
    git log --all --reverse --date=short --format='%ad %h %an %s' -G "$@"
}

_git_log_search_or_grep() {
    local mode="$1" # either "-S" or "-G"
    shift
    local show_patch=

    # Support optional -p for showing patches
    if [[ "$1" == "-p" ]]; then
        show_patch="-p"
        shift
    fi

    # Require at least one argument (search pattern)
    if [[ $# -eq 0 ]]; then
        echo "Usage: ${FUNCNAME[1]} [-p] <pattern>" >&2
        return 1
    fi

    git log $show_patch --all --reverse --date=short --format='%ad %h %an %s' "$mode" "$@"
}

git_log_search() {
    _git_log_search_or_grep -S "$@"
}

git_log_grep() {
    _git_log_search_or_grep -G "$@"
}

git_remote_add() {
    if [ $# -ne 1 ]; then
        echo "Usage: git_remote_add OWNER[:BRANCH]" >&2
        return 1
    fi
    local owner="${1%%:*}"
    local repo
    repo=$(basename -s .git "$(git rev-parse --show-toplevel 2>/dev/null)" || true)
    if [ -z "$repo" ]; then
        echo "Error: not inside a git repository" >&2
        return 1
    fi
    if git remote get-url "$owner" &>/dev/null; then
        echo "Remote '$owner' already exists: $(git remote get-url "$owner")"
        git fetch "$owner"
        return 0
    fi
    echo "git remote add -f \"$owner\" https://github.com/$owner/$repo"
    git remote add -f "$owner" "https://github.com/$owner/$repo"
}

git_swrp() (
    set -euo pipefail

    if [[ $# -ne 1 ]]; then
        echo "Usage: git_swrp <remote:branch>" >&2
        return 1
    fi

    local arg="$1"

    if [[ "$arg" == *:* ]]; then
        local remote="${arg%%:*}"
        local branch="${arg#*:}"
    elif [[ "$arg" == */* ]]; then
        local remote="${arg%%/*}"
        local branch="${arg#*/}"
    else
        echo "Error: argument must be in 'remote:branch' format" >&2
        return 1
    fi

    git_remote_add "$arg"

    local new_branch="${remote}→${branch}"

    echo "Creating local branch '$new_branch' from '$remote/$branch'..."
    git switch -c "$new_branch" "$remote/$branch"
)

git_show_merge_commits() {
    # Require exactly one argument
    if [[ $# -ne 1 ]]; then
        echo "Usage: git_show_merge_commits <count>" >&2
        return 1
    fi

    local n="$1"

    # Argument must be a positive integer
    if ! [[ "$n" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: <count> must be a positive integer." >&2
        return 1
    fi

    # Ensure we're inside a repo
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Error: not inside a Git repository." >&2
        return 1
    fi

    # Loop over first-parent commits
    git rev-list --first-parent -n "$n" HEAD | while read -r commit; do
        echo "================================================================================"
        git show --no-patch --pretty=fuller "$commit"
        echo
    done
}

myt() (
    files=()
    dash_dash_seen=0

    for arg in "$@"; do
        if [[ "$dash_dash_seen" -eq 0 && "$arg" == "--" ]]; then
            dash_dash_seen=1
            continue
        fi
        if [[ "$dash_dash_seen" -eq 0 && "$arg" != -* ]]; then
            files+=("$arg")
        fi
    done

    for f in "${files[@]}"; do
        if [[ -e "$f" ]]; then
            [[ -w "$f" ]] || {
                echo "myt: File exists but is not writable: $f" >&2
                exit 1
            }
        else
            parent_dir=$(dirname "$f")
            [[ -d "$parent_dir" ]] || {
                echo "myt: Parent directory does not exist: $parent_dir" >&2
                exit 1
            }
            [[ -w "$parent_dir" ]] || {
                echo "myt: Parent directory is not writable: $parent_dir" >&2
                exit 1
            }
        fi
    done

    exec tee "$@"
)

# https://chatgpt.com/share/67e8331b-6700-8008-9a09-31c9f1611a84  2025-03-29
# Bash completion for show_pr_for_branch.sh
_show_pr_for_branch_completions() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    # Get local branch names (excluding remotes)
    local branches
    branches=$(git for-each-ref --format='%(refname:short)' refs/heads/)

    COMPREPLY=($(compgen -W "$branches" -- "$cur"))
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
    /usr/bin/sudo /bin/true # Prompt for password before echo -n.
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

    if ! python3 -m venv "$venv_dir"; then
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

pip_update_all() {
    pip install --upgrade pip && pip freeze | cut -d= -f1 | xargs pip install --upgrade
}

which_venv() {
    if [ "$#" -ne 0 ]; then
        echo "which_venv: ERROR: no arguments expected, $# given." >&2
        return 1
    fi

    python3 -c "import sys; print(f'{sys.prefix=!r}')"
}

vac() {
    if [ "$#" -ne 1 ]; then
        echo "Usage: vac <venv-dir>" >&2
        return 2
    fi

    local venv_dir=$1

    if [ ! -d "$venv_dir" ]; then
        echo "Error: '$venv_dir' is not a directory" >&2
        return 1
    fi

    if [ ! -f "$venv_dir/bin/activate" ]; then
        echo "Error: '$venv_dir/bin/activate' not found" >&2
        return 1
    fi

    . "$venv_dir/bin/activate"
}

pybind11_fresh_venv_for_cmake() {
    python3 -m venv Pybind11DevVenv
    . Pybind11DevVenv/bin/activate
    pip install --upgrade pip
    pip install cmake ninja uv
}

pybind11_in_tree_cmake_all() {
    cmake --workflow venv
}

pybind11_in_tree_cmake_some() {
    cmake --preset venv -DPYBIND11_TEST_OVERRIDE="$@"
    cmake --build --preset venv
    cmake --build --preset tests
}

# https://www.commandlinefu.com/commands/view/12043/remove-color-special-escape-ansi-codes-from-text-with-sed
alias strip_ansi_esc='sed "s,\x1B\[[0-9;]*[a-zA-Z],,g"'

alias ListAllCommands='compgen -A function -abck'

alias cfd='clang-format --dry-run'
alias cfI='clang-format -i'
alias cffd='clang-format -style=file --dry-run'
alias cffI='clang-format -style=file -i'
alias cfpybind11d="clang-format --style=file:$HOME/forked/pybind11/.clang-format --dry-run"
alias cfpybind11I="clang-format --style=file:$HOME/forked/pybind11/.clang-format -i"
alias cleanup_build_dir='$W/clone/pybind11_scons/cleanup_build_dir.sh'
alias sconshold='scons extra_defines=PYBIND11_USE_SMART_HOLDER_AS_DEFAULT'
alias sconshnew='scons extra_defines=PYBIND11_RUN_TESTING_WITH_SMART_HOLDER_AS_DEFAULT_BUT_NEVER_USE_IN_PRODUCTION_PLEASE'

alias pipup='pip install --upgrade pip'

export MY_GITHUB_USERNAME=rwgk

# `gh auth login` creates ~/.config/gh/hosts.yml
alias show_github_token='yq -r '\''."github.com".oauth_token'\'' "$HOME/.config/gh/hosts.yml"'

gh_run_list() {
    if [[ $# -ne 3 ]]; then
        echo "Usage: gh_run_list OWNER/REPO workflow.yml limit" >&2
        return 1
    fi
    local repo="$1"
    local workflow="$2"
    local limit="$3"

    gh run list \
        --workflow "$workflow" \
        --limit "$limit" \
        --json databaseId,displayTitle,number,status,conclusion \
        -R "$repo"
}

gh_download_run_logs() (
    if [[ $# -ne 2 ]]; then
        echo "Usage: gh_download_run_logs OWNER/REPO <file-from-gh_run_list>" >&2
        return 1
    fi

    local repo="$1"
    local infile="$2"

    set -euo pipefail

    jq -r '.[] | .databaseId' <"$infile" | while read -r run_id; do
        echo "Downloading logs for run $run_id"
        gh api "/repos/${repo}/actions/runs/${run_id}/logs" >"log_${run_id}.zip" || {
            echo "  ! failed for run $run_id" >&2
            rm -f "log_${run_id}.zip"
        }
    done
)

get_gha_logs() {
    local script="$HOME/rwgk_config/bin/get_gha_logs"

    if [[ ! -x "$script" ]]; then
        echo "Error: helper script not found: '$script'" >&2
        return 1
    fi

    "$script" "$@"
}

gha_logs_here() {
    local script="$HOME/rwgk_config/bin/gha_logs_here"
    local cd_target_file cd_target status

    if [[ ! -x "$script" ]]; then
        echo "Error: helper script not found: '$script'" >&2
        return 1
    fi

    if ! cd_target_file=$(mktemp "${TMPDIR:-/tmp}/gha_logs_here.cd_target.XXXXXX"); then
        echo "Error: could not create temporary file for gha_logs_here." >&2
        return 1
    fi

    if "$script" --cd-target-file "$cd_target_file" "$@"; then
        if [[ -s "$cd_target_file" ]]; then
            if ! read -r cd_target <"$cd_target_file"; then
                rm -f "$cd_target_file"
                echo "Error: could not read cd target from gha_logs_here." >&2
                return 1
            fi
            rm -f "$cd_target_file"
            cd "$cd_target" || return 1
            return 0
        fi

        rm -f "$cd_target_file"
        return 0
    fi

    status=$?
    rm -f "$cd_target_file"
    return "$status"
}

gha_for_this_pr() {
    # List all GitHub Actions workflow runs for the current PR (or branch if no PR)
    # Usage: gha_for_this_pr [head_limit]
    #   head_limit: optional number of runs to show (default: 1000, use head/tail to filter)
    #
    # Note: GitHub Actions runs for PRs are typically on the "pull-request/N" branch,
    # not the original branch name. This function detects the PR and checks both.

    if ! command -v gh >/dev/null 2>&1; then
        echo "Error: 'gh' (GitHub CLI) is not installed or not on PATH." >&2
        return 127
    fi

    # Must be in a git repository
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Error: not inside a git repository" >&2
        return 1
    fi

    # Get current branch
    local branch
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [[ -z "$branch" ]]; then
        echo "Error: could not determine current branch" >&2
        return 1
    fi

    # Determine repo from git remote (prefer upstream, fallback to origin, then first remote)
    local upstream_url repo owner full_repo
    upstream_url=$(git remote get-url upstream 2>/dev/null || git remote get-url origin 2>/dev/null || git remote get-url $(git remote | head -1) 2>/dev/null)
    if [[ -z "$upstream_url" ]]; then
        echo "Error: no git remote found (tried upstream, origin, and first available remote)" >&2
        return 1
    fi

    # Normalize to owner/repo format
    # Handle both https://github.com/owner/repo.git and git@github.com:owner/repo.git
    if [[ "$upstream_url" =~ github\.com[:/]([^/]+)/([^/]+)(\.git)?$ ]]; then
        owner="${BASH_REMATCH[1]}"
        repo="${BASH_REMATCH[2]}"
        # Remove .git suffix if present
        repo="${repo%.git}"
        full_repo="${owner}/${repo}"
    else
        echo "Error: could not parse owner/repo from remote URL: $upstream_url" >&2
        return 1
    fi

    # Optional head limit (default to large number to get all runs)
    local head_limit="${1:-1000}"

    # Try to find PR number for this branch
    local pr_number pr_branch
    pr_number=$(gh pr list --head "$branch" --repo "$full_repo" --json number --jq '.[0].number' 2>/dev/null)
    if [[ -n "$pr_number" && "$pr_number" != "null" ]]; then
        pr_branch="pull-request/${pr_number}"
        echo "Repository: $full_repo"
        echo "Branch:     $branch (PR #$pr_number)"
        echo "Checking runs on: $pr_branch (and $branch if any)"
        echo ""

        # Check runs on pull-request/N branch (where GitHub Actions typically runs)
        local pr_runs
        pr_runs=$(gh run list --branch "$pr_branch" --repo "$full_repo" --json url,workflowName,status,conclusion,createdAt,displayTitle --limit "$head_limit" 2>/dev/null)

        if [[ -n "$pr_runs" && "$pr_runs" != "[]" ]]; then
            echo "=== Runs on $pr_branch ==="
            echo "$pr_runs" | jq -r '.[] | "\(.createdAt) \(.status)/\(.conclusion // "unknown") \(.workflowName)\n  \(.displayTitle)\n  \(.url)"'
        fi

        # Also check runs on the original branch (in case some workflows use it)
        local branch_runs
        branch_runs=$(gh run list --branch "$branch" --repo "$full_repo" --json url,workflowName,status,conclusion,createdAt,displayTitle --limit "$head_limit" 2>/dev/null)

        if [[ -n "$branch_runs" && "$branch_runs" != "[]" ]]; then
            if [[ -n "$pr_runs" && "$pr_runs" != "[]" ]]; then
                echo ""
            fi
            echo "=== Runs on $branch ==="
            echo "$branch_runs" | jq -r '.[] | "\(.createdAt) \(.status)/\(.conclusion // "unknown") \(.workflowName)\n  \(.displayTitle)\n  \(.url)"'
        fi

        # If no runs found on either branch
        if [[ (-z "$pr_runs" || "$pr_runs" == "[]") && (-z "$branch_runs" || "$branch_runs" == "[]") ]]; then
            echo "No workflow runs found for PR #$pr_number"
        fi
    else
        # No PR found, just check the branch
        echo "Repository: $full_repo"
        echo "Branch:     $branch (no PR found)"
        echo "Listing workflow runs..."

        gh run list \
            --branch "$branch" \
            --repo "$full_repo" \
            --json url,workflowName,status,conclusion,createdAt,displayTitle \
            --jq '.[] | "\(.createdAt) \(.status)/\(.conclusion // "unknown") \(.workflowName)\n  \(.displayTitle)\n  \(.url)"' \
            --limit "$head_limit"
    fi
}

find_pr_for_commit() {
    local short_sha="$1"
    if [[ -z "$short_sha" ]]; then
        echo "Usage: find_pr_for_commit <commit-sha>"
        return 1
    fi

    # Determine upstream repo (e.g. pybind/pybind11)
    local upstream_url
    upstream_url=$(git remote get-url upstream 2>/dev/null || git remote get-url origin 2>/dev/null)
    if [[ -z "$upstream_url" ]]; then
        echo "FATAL: no upstream/origin remote found" >&2
        return 1
    fi
    # Normalize to owner/repo
    local repo
    repo=$(basename -s .git "$upstream_url")
    local owner
    owner=$(echo "$upstream_url" | sed -E 's#.*[:/](.+)/[^/]+(\.git)?#\1#')
    local full_repo="${owner}/${repo}"

    # Resolve short SHA to full SHA if necessary
    local long_sha
    long_sha=$(git rev-parse "$short_sha^{commit}" 2>/dev/null)
    if [[ -z "$long_sha" ]]; then
        echo "FATAL: commit '$short_sha' not found" >&2
        return 1
    fi

    echo "Repository: $full_repo"
    echo "Commit:     $long_sha"
    echo "Querying GitHub API..."

    # Query GitHub API for associated PRs
    gh api \
        "repos/${full_repo}/commits/${long_sha}/pulls" \
        -H "Accept: application/vnd.github+json" |
        jq -r '.[] | "\(.number): \(.html_url)"' |
        { grep . || echo "No associated PR found"; }
}

vscode_settings_dir="$HOME/Library/Application Support/Code/User/"
alias cd_vscode_settings_dir='cd "$vscode_settings_dir"'

export CCCJ="$HOME/.cursor/cli-config.json"

# launch cursor window
lcw() (
    if ! command -v cursor >/dev/null 2>&1; then
        echo "Error: cursor command does not exist" >&2
        return 1
    fi

    # Check if ~/logs exists and is writable
    if [[ ! -d ~/logs ]]; then
        echo "Error: ~/logs directory does not exist" >&2
        return 1
    fi

    if [[ ! -w ~/logs ]]; then
        echo "Error: ~/logs directory is not writable" >&2
        return 1
    fi

    # Create log filename with current directory and timestamp
    pwd_safe=$(pwd | sed 's|/|_|g; s|[[:space:]]|_|g')
    log_file="$HOME/logs/cursor_stdout${pwd_safe}_$(mlt).txt"

    # Launch the monitoring function in background
    _lcw_impl "$log_file" "$@" &

    echo "Cursor launched in background, logging to: $log_file"
)

_lcw_impl() (
    log_file="$1"
    shift

    # Run cursor with output redirected to log file
    cursor "$@" &>"$log_file"
    exit_code=$?

    # Append exit information to log file
    echo "cursor EXIT at $(mlt) with exit code $exit_code" >>"$log_file"
)

# List log files from $L by modification time (newest first).
#   nlog      — newest file (same as nlog 1)
#   nlog N    — Nth newest file
#   nlog -N   — N newest files
#   nlog N-M  — Nth through Mth newest files
#   nlog N%C  — C files starting from the Nth newest
#   nlog N+   — all files from Nth onward
nlog() {
    if [[ -z "$L" || ! -d "$L" ]]; then
        echo "Error: \$L is not set or is not a directory" >&2
        return 1
    fi
    local dir
    dir=$(realpath "$L") || return 1
    local listing
    listing=$(ls -1t "$dir") || return 1
    if [[ -z "$listing" ]]; then
        echo "Error: no files in '$dir'" >&2
        return 1
    fi
    local fullpaths
    fullpaths=$(echo "$listing" | awk -v d="$dir" '{print d "/" $0}')
    local arg="${1:-1}"
    if [[ "$arg" =~ ^([0-9]+)-([0-9]+)$ ]]; then
        echo "$fullpaths" | head -n "${BASH_REMATCH[2]}" | tail -n +"${BASH_REMATCH[1]}"
    elif [[ "$arg" =~ ^([0-9]+)%([0-9]+)$ ]]; then
        local start=${BASH_REMATCH[1]}
        local end=$((start + BASH_REMATCH[2] - 1))
        echo "$fullpaths" | head -n "$end" | tail -n +"$start"
    elif [[ "$arg" =~ ^([0-9]+)\+$ ]]; then
        echo "$fullpaths" | tail -n +"${BASH_REMATCH[1]}"
    elif [[ "$arg" =~ ^-([0-9]+)$ ]]; then
        echo "$fullpaths" | head -n "${BASH_REMATCH[1]}"
    elif [[ "$arg" =~ ^[0-9]+$ ]]; then
        echo "$fullpaths" | head -n "$arg" | tail -n 1
    else
        echo "Usage: nlog [N | -N | N-M | N%C | N+]" >&2
        return 1
    fi
}

dbgcode() {
    echo 'fflush(stderr); fprintf(stdout, "\nLOOOK %s:%d\n", __FILE__, __LINE__); fflush(stdout);'
    echo 'print(f"\nLOOOK {val=!r}", flush=True)'
    echo 'long *BAD = nullptr; *BAD = 101;'
}

macos_brew_install() (
    set -x
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
)

mc3dwnld() (
    set -x
    wget "https://repo.anaconda.com/miniconda/Miniconda3-latest-$(uname)-$(uname -m).sh"
)

mf3dwnld() (
    set -x
    script="Miniforge3-$(uname)-$(uname -m).sh"
    wget "https://github.com/conda-forge/miniforge/releases/latest/download/$script"
    chmod 755 "$script"
)

mf3path() {
    if [[ -n "$W" && -d "$W/miniforge3" ]]; then
        echo "Using $W/miniforge3"
        source "$W/miniforge3/etc/profile.d/conda.sh"
    elif [[ -d "$HOME/miniforge3" ]]; then
        echo "Using $HOME/miniforge3"
        source "$HOME/miniforge3/etc/profile.d/conda.sh"
    else
        if [[ -z "$W" ]]; then
            echo "Error: \$W is not defined and '$HOME/miniforge3' does not exist." >&2
        else
            echo "Error: Neither '$W/miniforge3' nor '$HOME/miniforge3' exists." >&2
        fi
        return 1
    fi
}

nvmdwnld() (
    if [[ "$OSTYPE" != linux* ]]; then
        echo "nvmdwnld: intended for Linux only." >&2
        return 1
    fi

    if [[ -z "${W:-}" || ! -d "$W" ]]; then
        echo "nvmdwnld: \$W is not set or is not a directory." >&2
        return 1
    fi

    local nvm_dir="$W/.nvm"

    set -x
    mkdir -p "$nvm_dir" &&
        PROFILE=/dev/null NVM_DIR="$nvm_dir" \
            bash -c 'curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash'
)

nvmpath() {
    if [[ "$OSTYPE" != linux* ]]; then
        echo "nvmpath: intended for Linux only." >&2
        return 1
    fi

    if [[ -z "${W:-}" || ! -d "$W" ]]; then
        echo "nvmpath: \$W is not set or is not a directory." >&2
        return 1
    fi

    export NVM_DIR="$W/.nvm"

    if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
        echo "nvmpath: '$NVM_DIR/nvm.sh' not found. Run nvmdwnld first." >&2
        return 1
    fi

    # shellcheck disable=SC1090
    . "$NVM_DIR/nvm.sh"

    export npm_config_cache="$W/.npm-cache"
    mkdir -p "$npm_config_cache"
}

nvmhere() {
    nvmpath || return
    if [[ ! -f .nvmrc ]]; then
        echo "nvmhere: no .nvmrc in $(pwd)" >&2
        return 1
    fi
    nvm install
    nvm use
}

conda_rm() {
    conda remove -y --all -n "$@"
    echo "Done: conda remove -y --all -n" "$@"
}

pixiinsthere() (
    set -x
    curl -fsSL https://pixi.sh/install.sh | PIXI_HOME="$(pwd)/.pixi" PIXI_NO_PATH_UPDATE=1 sh
)

pixipath() {
    if [ -d "/wrk/.pixi/bin" ]; then
        echo "Using /wrk/.pixi/bin"
        export PATH="/wrk/.pixi/bin:$PATH"
    elif [ -d "$HOME/.pixi/bin" ]; then
        echo "Using \$HOME/.pixi/bin"
        export PATH="$HOME/.pixi/bin:$PATH"
    else
        echo "Error: Neither /wrk/.pixi/bin nor \$HOME/.pixi/bin exists." >&2
        return 1
    fi
    export PATH="$(clean_path .:"$PATH")"
}

set_cuda_env() {
    if [[ $# -ne 1 ]]; then
        echo "Usage: set_cuda_env <cuda-major-minor>" >&2
        return 1
    fi
    local ver="$1"
    set -x
    export CUDA_HOME="/usr/local/cuda-$ver"
    set +x
}

set_cuda_env_bld() {
    if [[ $# -ne 1 ]]; then
        echo "Usage: set_cuda_env_bld <cuda-major-minor>" >&2
        return 1
    fi
    local ver="$1"
    set_cuda_env "$ver"
    set -x
    export CUDA_PYTHON_PARALLEL_LEVEL="$(nproc)"
    set +x
}

set_all_must_work() {
    set -x
    export CUDA_PATHFINDER_TEST_LOAD_NVIDIA_DYNAMIC_LIB_STRICTNESS=all_must_work
    export CUDA_PATHFINDER_TEST_FIND_NVIDIA_HEADERS_STRICTNESS=all_must_work
    set +x
}

cpython_install() {
    local action="$1"
    shift || true

    case "$action" in
    activate | a)
        local name="$1"

        if [ -z "$name" ]; then
            echo "Usage: cpython_install activate|a <install_name>" >&2
            return 1
        fi

        if [ -z "$W" ]; then
            echo "cpython_install: environment variable W is not set" >&2
            return 1
        fi

        if [ -n "${CPYTHON_INSTALL_ACTIVE:-}" ]; then
            echo "cpython_install: already active for '${CPYTHON_INSTALL_NAME:-?}'. Run 'cpython_install deactivate|d' first." >&2
            return 1
        fi

        local install_dir="$W/cpython_installs/$name"
        local bin_dir="$install_dir/bin"
        local lib_dir="$install_dir/lib"

        if [ ! -d "$install_dir" ]; then
            echo "cpython_install: install directory '$install_dir' does not exist" >&2
            return 1
        fi
        if [ ! -d "$bin_dir" ]; then
            echo "cpython_install: bin directory '$bin_dir' does not exist" >&2
            return 1
        fi
        if [ ! -d "$lib_dir" ]; then
            echo "cpython_install: lib directory '$lib_dir' does not exist" >&2
            return 1
        fi

        # Ensure a 'python' symlink exists pointing to 'python3'
        if [ ! -e "$bin_dir/python" ]; then
            (cd "$bin_dir" && ln -s python3 python)
            echo "cpython_install: created symlink '$bin_dir/python' -> 'python3'"
        fi

        export CPYTHON_INSTALL_ACTIVE=1
        export CPYTHON_INSTALL_NAME="$name"
        export CPYTHON_INSTALL_OLD_PATH="$PATH"
        export CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH="${LD_LIBRARY_PATH-}"

        export PATH="$bin_dir${PATH:+:$PATH}"
        if [ -n "${LD_LIBRARY_PATH-}" ]; then
            export LD_LIBRARY_PATH="$lib_dir:$LD_LIBRARY_PATH"
        else
            export LD_LIBRARY_PATH="$lib_dir"
        fi
        ;;

    deactivate | d)
        if [ -z "${CPYTHON_INSTALL_ACTIVE:-}" ]; then
            echo "cpython_install: no active installation to deactivate" >&2
            return 1
        fi

        export PATH="$CPYTHON_INSTALL_OLD_PATH"
        export LD_LIBRARY_PATH="${CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH-}"

        unset CPYTHON_INSTALL_ACTIVE \
            CPYTHON_INSTALL_NAME \
            CPYTHON_INSTALL_OLD_PATH \
            CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH
        ;;

    list)
        if [ -z "$W" ]; then
            echo "cpython_install: environment variable W is not set" >&2
            return 1
        fi

        local base_dir="$W/cpython_installs"

        if [ ! -d "$base_dir" ]; then
            echo "cpython_install: base directory '$base_dir' does not exist" >&2
            return 1
        fi

        # List installs
        ls -1 "$base_dir"

        # Optionally show sha_info.txt if present
        if [ -f "$base_dir/sha_info.txt" ]; then
            echo
            cat "$base_dir/sha_info.txt"
        fi
        ;;

    *)
        echo "Usage: cpython_install {activate|a <install_name>|deactivate|d|list}" >&2
        return 1
        ;;
    esac
}

export NUMBA_CAPTURED_ERRORS="new_style"

if command -v wslpath >/dev/null 2>&1; then
    if [ -f "$HOME/rwgk_config/bash_wsl_tools" ]; then
        . "$HOME/rwgk_config/bash_wsl_tools"
    fi
fi

if [ -d "$HOME/Downloads" ]; then
    export D="$HOME/Downloads"
    alias D='cd "$D"'
fi

pobox() {
    if [ "$#" -ne 1 ]; then
        echo "Usage: pobox <ssh-target>" >&2
        return 1
    fi
    local host="$1"
    if [[ -z "${D:-}" || ! -d "$D" ]]; then
        echo "Error: \$D is not set or is not a directory" >&2
        return 1
    fi
    rsync --archive --verbose "$host:~/obox/" "$D/${host}_obox/"
}

[ -f "$HOME/.bashrc_org" ] && . "$HOME/.bashrc_org"
[ -f "$HOME/.bashrc_os" ] && . "$HOME/.bashrc_os"
[ -f "$HOME/.bashrc_host" ] && . "$HOME/.bashrc_host"

if [ -f "$HOME/rwgk_config/path_utility.py" ]; then
    if [ -x /usr/bin/python3 ]; then
        export PATH=$(/usr/bin/python3 "$HOME/rwgk_config/path_utility.py" tidy PATH)
    elif [ -x /usr/bin/python ]; then
        export PATH=$(/usr/bin/python "$HOME/rwgk_config/path_utility.py" tidy PATH)
    fi
fi

if [ -f "$HOME/rwgk_config/path_utility.py" ]; then
    if [ -x /usr/bin/python3 ]; then
        export PYTHONPATH=$(/usr/bin/python3 "$HOME/rwgk_config/path_utility.py" tidy PYTHONPATH)
    elif [ -x /usr/bin/python ]; then
        export PYTHONPATH=$(/usr/bin/python "$HOME/rwgk_config/path_utility.py" tidy PYTHONPATH)
    fi
fi

export PATH="$(clean_path .:"$PATH")"
