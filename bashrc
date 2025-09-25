# ~/.bashrc: executed by bash(1) for non-login shells.

# If not running interactively, don't do anything
case $- in
*i*) ;;
*) return ;;
esac
[ -z "$PS1" ] && return

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

if [[ "$OSTYPE" == "msys" ]]; then
    # Git Bash
    export PS1='$(hostname):$PWD $ '
else
    export PS1='$(/bin/hostname -f):\w $ '
fi

export IGNOREEOF=9999

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

grep_pytest_summary() {
    local pattern='==== .*?(passed|failed|skipped|errors).* ===='

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

alias vma='. "$HOME/Venvs/misc/bin/activate"'
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

gh_download_run_logs() {
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
    else
        nfid "$HOME/logs" "$@"
    fi
}

dbgcode() {
    echo 'fflush(stderr); fprintf(stdout, "\nLOOOK %s:%d\n", __FILE__, __LINE__); fflush(stdout);'
    echo 'print(f"\nLOOOK {val=!r}", flush=True)'
    echo 'long *BAD = nullptr; *BAD = 101;'
}

mc3dwnld() (
    set -x
    wget "https://repo.anaconda.com/miniconda/Miniconda3-latest-$(uname)-$(uname -m).sh"
)

mf3dwnld() (
    set -x
    wget "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"
)

mf3path() {
    if [ -d /wrk/miniforge3 ]; then
        echo "Using /wrk/miniforge3"
        source /wrk/miniforge3/etc/profile.d/conda.sh
    elif [ -d "$HOME/miniforge3" ]; then
        echo "Using \$HOME/miniforge3"
        source "$HOME/miniforge3/etc/profile.d/conda.sh"
    else
        echo "Error: Neither /wrk/miniforge3 nor \$HOME/miniforge3 exists." >&2
        return 1
    fi
}

set_cuda_env() {
    export CUDA_HOME=/usr/local/cuda
    export LIBRARY_PATH="$CUDA_HOME/lib64:$LIBRARY_PATH"
    export LD_LIBRARY_PATH="$CUDA_HOME/lib64:$CUDA_HOME/nvvm/lib64:$LD_LIBRARY_PATH"
}

set_cuda_env_bld() {
    set_cuda_env
    export CUDA_PYTHON_PARALLEL_LEVEL=$(nproc)
    export CUDA_PATHFINDER_TEST_LOAD_NVIDIA_DYNAMIC_LIB_STRICTNESS=all_must_work
    export CUDA_PATHFINDER_TEST_FIND_NVIDIA_HEADERS_STRICTNESS=all_must_work
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
