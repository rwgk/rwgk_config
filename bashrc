# ~/.bashrc: executed by bash(1) for non-login shells.

# --- Early return guard ------------------------------------------------------
# Load full shell environment if:
#   1. PRETEND_INTERACTIVE_SHELL is set (forced for special cases, e.g. pbpush/pbpull), OR
#   2. PS1 is set (normal interactive prompt), OR
#   3. Shell options string ($-) contains 'i' (interactive shell)
#
if [[ -z "$PRETEND_INTERACTIVE_SHELL" ]]; then
    # If PS1 is empty, we *might* be non-interactive
    if [[ -z "$PS1" ]]; then
        case "$-" in
        *i*) : ;;    # interactive → continue
        *) return ;; # truly non-interactive → bail out early
        esac
    fi
fi
# --- end early return guard --------------------------------------------------

alias RC='cd "$HOME/rwgk_config"'

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

    # Read macOS clipboard, send via ssh to remote bash login shell, which runs pbcopy.
    # -T: no pty, better for pure stdin/stdout data
    if ! pbpaste | ssh -T "$host" 'PRETEND_INTERACTIVE_SHELL=1 . ~/.profile; pbcopy'; then
        echo "pbpush: failed to push clipboard to '$host'." >&2
        return 1
    fi
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
        # “N more” note if truncated
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

grep_built_cuda() {
    echo "$@"
    grep '^Successfully built cuda-' "$@"
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
alias dotdevnow='date +".dev%Y%m%d%H%M"'

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

git_branch_D_track_hash() (
    set -euo pipefail

    if [[ $# -ne 1 ]]; then
        echo "Usage: git_branch_D_track_hash <branchname>" >&2
        return 1
    fi

    branch="$1"

    # Check MY_GIT_BACKTRACKING_INFO
    if [[ -z "${MY_GIT_BACKTRACKING_INFO:-}" ]]; then
        echo "Error: Environment variable MY_GIT_BACKTRACKING_INFO is not set." >&2
        return 1
    fi

    if [[ ! -d "$MY_GIT_BACKTRACKING_INFO" ]]; then
        echo "Error: MY_GIT_BACKTRACKING_INFO='$MY_GIT_BACKTRACKING_INFO' is not a directory." >&2
        return 1
    fi

    # Ensure the branch exists
    if ! git rev-parse --verify "$branch" >/dev/null 2>&1; then
        echo "Error: branch '$branch' not found." >&2
        return 1
    fi

    commit=$(git rev-parse "$branch")
    repo=$(basename "$(git rev-parse --show-toplevel)")
    timestamp=$(date +%Y-%m-%d+%H%M%S)
    safe_repo="${repo//[^A-Za-z0-9._@-]/_}"
    safe_branch="${branch//[^A-Za-z0-9._@-]/_}"
    infofile="$MY_GIT_BACKTRACKING_INFO/${safe_repo}_${safe_branch}_${timestamp}.txt"

    {
        echo "Current host: '$(hostfqdn)'"
        echo "Current working directory: '$(pwd)'"
        echo "Repository: '$repo'"
        echo "Archiving \`git show --stat --summary\` output before \`git branch -D \"$branch\"\`"
        echo
        git show --stat --summary "$commit"
    } >"$infofile"

    echo "Backed up branch tip to: '$infofile'"
    git branch -D "$branch"
)

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
        echo "Usage: git_remote_add OWNER" >&2
        return 1
    fi
    local owner="$1"
    # Get the repo name (last path component without .git)
    local repo
    repo=$(basename -s .git "$(git rev-parse --show-toplevel 2>/dev/null)" || true)
    if [ -z "$repo" ]; then
        echo "Error: not inside a git repository" >&2
        return 1
    fi
    echo "git remote add -f \"$owner\" https://github.com/$owner/$repo"
    git remote add -f "$owner" "https://github.com/$owner/$repo"
}

git_swrp() (
    set -euo pipefail

    if [[ $# -ne 1 ]]; then
        echo "Usage: git_swrp <remote/branch>" >&2
        return 1
    fi

    local arg="$1"

    if [[ "$arg" != */* ]]; then
        echo "Error: argument must contain '/'" >&2
        return 1
    fi

    local remote="${arg%%/*}"
    local branch="${arg#*/}"

    if ! git remote get-url "$remote" &>/dev/null; then
        echo "Error: '$remote' is not a known remote" >&2
        return 1
    fi

    local new_branch="${remote}→${branch}"

    echo "Creating local branch '$new_branch' from '$arg'..."
    git switch -c "$new_branch" "$arg"
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

_get_gh_run_logs_usage() {
    cat >&2 <<'EOF'
Usage:
  get_gh_run_logs org/repo RUN_ID [annotation]
  get_gh_run_logs https://github.com/org/repo/actions/runs/RUN_ID[/...] [annotation]

Examples:
  get_gh_run_logs NVIDIA/cuda-python 17475795472
  get_gh_run_logs pybind/pybind11 18144031622
  get_gh_run_logs pybind/pybind11 18144031622 ci
  get_gh_run_logs https://github.com/NVIDIA/cuda-python/actions/runs/19652804989/job/56283268350?pr=1284
EOF
}

get_gh_run_logs() {
    if ! command -v gh >/dev/null 2>&1; then
        echo "Error: 'gh' (GitHub CLI) is not installed or not on PATH." >&2
        return 127
    fi

    local repo run_id annotation url_mode=false

    if [[ $# -ge 1 && "$1" == http* ]]; then
        # URL mode: get_gh_run_logs URL [annotation]
        if [[ $# -lt 1 || $# -gt 2 ]]; then
            _get_gh_run_logs_usage
            return 2
        fi
        url_mode=true
        local url="$1"
        annotation="${2:-}"

        # Parse: scheme://host/path
        local host_and_path host path
        host_and_path="${url#*://}"
        host="${host_and_path%%/*}"
        path="${host_and_path#*/}"

        if [[ "$host" != "github.com" ]]; then
            echo "Error: URL host must be github.com (got: '$host')." >&2
            return 2
        fi

        # path is: org/repo/actions/runs/RUN_ID[/...]
        local org repo_name rest after_runs
        org="${path%%/*}"
        rest="${path#*/}"
        repo_name="${rest%%/*}"
        rest="${rest#*/}" # now starts with actions/...

        if [[ -z "$org" || -z "$repo_name" || "$rest" != actions/* ]]; then
            echo "Error: could not parse org/repo/actions/... from URL path '$path'." >&2
            return 2
        fi

        # Extract run_id from ".../actions/runs/RUN_ID[/...]"
        after_runs="${path#*/actions/runs/}"
        if [[ "$after_runs" == "$path" ]]; then
            echo "Error: URL does not contain '/actions/runs/RUN_ID'." >&2
            return 2
        fi
        if [[ "$after_runs" =~ ^([0-9]+) ]]; then
            run_id="${BASH_REMATCH[1]}"
        else
            echo "Error: could not extract numeric RUN_ID from URL '$url'." >&2
            return 2
        fi

        repo="${org}/${repo_name}"
    else
        # Isolated args mode: get_gh_run_logs org/repo RUN_ID [annotation]
        if [[ $# -lt 2 || $# -gt 3 ]]; then
            _get_gh_run_logs_usage
            return 2
        fi

        repo="$1"
        run_id="$2"
        annotation="${3:-}"
    fi

    if [[ "$repo" != */* ]]; then
        echo "Error: repo must be in 'org/repo' form (got: '$repo')." >&2
        return 2
    fi

    if [[ ! "$run_id" =~ ^[0-9]+$ ]]; then
        echo "Error: RUN_ID must be numeric (got: '$run_id')." >&2
        return 2
    fi

    # Sanitize annotation for a filename (spaces -> _, slashes -> -)
    if [[ -n "$annotation" ]]; then
        annotation="${annotation// /_}"
        annotation="${annotation//\//-}"
        annotation="_${annotation}"
    fi

    local endpoint="/repos/${repo}/actions/runs/${run_id}/logs"
    local outfile="logs_${run_id}${annotation}.zip"

    # ---- show and run ----
    echo "+ gh api \"$endpoint\" > \"$outfile\""
    gh api "$endpoint" >"$outfile"
    local status=$?

    if [[ $status -eq 0 ]]; then
        echo "Saved: $outfile"
    else
        echo "Failed to fetch logs for run $run_id from $repo (exit $status)." >&2
    fi

    return $status
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
    log_file="$HOME/logs/cursor_stdout${pwd_safe}_$(now).txt"

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
    echo "cursor EXIT at $(now) with exit code $exit_code" >>"$log_file"
)

nfid() {
    local dir=$1
    local pattern=${2:-*}

    if [[ ! -d $dir ]]; then
        echo "Error: '$dir' is not a directory" >&2
        return 1
    fi

    # Collect matching files
    local matches=()
    while IFS= read -r -d '' file; do
        matches+=("$file")
    done < <(find "$dir" -maxdepth 1 -type f -name "$pattern" -print0)

    if [[ ${#matches[@]} -eq 0 ]]; then
        echo "Error: no files matching '$pattern' in '$dir'" >&2
        return 1
    fi

    local newest=""
    local newest_mtime=0
    local mtime

    for file in "${matches[@]}"; do
        if [[ $(uname) == "Darwin" ]]; then
            mtime=$(stat -f %m "$file") # macOS
        else
            mtime=$(stat -c %Y "$file") # Linux / GNU
        fi

        if ((mtime > newest_mtime)); then
            newest_mtime=$mtime
            newest=$file
        fi
    done

    echo "$newest"
}

nlog() {
    if [ -d /wrk/logs ]; then
        nfid /wrk/logs "$@"
    elif [ -d "$HOME/wrk/logs" ]; then
        nfid "$HOME/wrk/logs" "$@"
    else
        nfid "$HOME/logs" "$@"
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
    export PATH="$(clean_path $PATH)"
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

export NUMBA_CAPTURED_ERRORS="new_style"

if command -v wslpath >/dev/null 2>&1; then
    if [ -f "$HOME/rwgk_config/bash_wsl_tools" ]; then
        . "$HOME/rwgk_config/bash_wsl_tools"
    fi
fi

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
