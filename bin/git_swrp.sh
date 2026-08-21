# shellcheck shell=bash

# This file is sourced by bashrc so git_swrp can change the calling shell's
# directory. Keep shell options and other top-level state unchanged.

_git_swrp_usage() {
    cat <<'EOF'
Usage: git_swrp <PR-number>

Create a sibling worktree named pr<PR-number> for a pull request in the
repository configured as the current Git repository's 'upstream' remote, then
change the calling shell to that worktree.
EOF
}

_git_swrp_github_repo_from_url() {
    local url="$1"
    local repo_path=

    url="${url%/}"
    url="${url%.git}"

    case "$url" in
    https://github.com/*)
        repo_path="${url#https://github.com/}"
        ;;
    http://github.com/*)
        repo_path="${url#http://github.com/}"
        ;;
    git://github.com/*)
        repo_path="${url#git://github.com/}"
        ;;
    git@github.com:*)
        repo_path="${url#git@github.com:}"
        ;;
    ssh://git@github.com/*)
        repo_path="${url#ssh://git@github.com/}"
        ;;
    *)
        return 1
        ;;
    esac

    if [[ "$repo_path" != */* || "$repo_path" == */*/* ]]; then
        return 1
    fi
    if [[ -z "${repo_path%%/*}" || -z "${repo_path#*/}" ]]; then
        return 1
    fi

    printf '%s\n' "$repo_path"
}

_git_swrp_normalize_github_repo() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

_git_swrp_upstream_repo() {
    local upstream_url
    local upstream_repo

    if ! upstream_url=$(git config --get remote.upstream.url); then
        echo "git_swrp: remote 'upstream' is required to identify the pull-request repository." >&2
        return 1
    fi
    if [[ -z "$upstream_url" ]]; then
        echo "git_swrp: remote 'upstream' has an empty fetch URL." >&2
        return 1
    fi
    if ! upstream_repo=$(_git_swrp_github_repo_from_url "$upstream_url"); then
        echo "git_swrp: remote 'upstream' does not have a recognized github.com repository URL." >&2
        return 1
    fi

    printf '%s\n' "$upstream_repo"
}

_git_swrp_lookup_pr() {
    local upstream_repo="$1"
    local pr_number="$2"
    local gh_output
    local returned_number head_owner head_repo head_branch pr_state

    if ! command -v gh >/dev/null 2>&1; then
        echo "git_swrp: 'gh' is required for pull-request lookup but was not found on PATH." >&2
        return 127
    fi

    if ! gh_output=$(gh pr view "$pr_number" \
        --repo "$upstream_repo" \
        --json number,headRefName,headRepository,headRepositoryOwner,state \
        --jq '[(.number | tostring), (.headRepositoryOwner.login // ""), (.headRepository.name // ""), (.headRefName // ""), (.state // "")] | join("\u001f")'); then
        printf "git_swrp: could not look up PR #%s in '%s'.\n" "$pr_number" "$upstream_repo" >&2
        return 1
    fi

    IFS=$'\x1f' read -r returned_number head_owner head_repo head_branch pr_state <<<"$gh_output"
    if [[ "$returned_number" != "$pr_number" ]]; then
        printf "git_swrp: PR lookup returned number '%s' instead of '%s' for '%s'.\n" \
            "${returned_number:-<empty>}" "$pr_number" "$upstream_repo" >&2
        return 1
    fi
    if [[ -z "$head_owner" || -z "$head_repo" ]]; then
        printf "git_swrp: PR #%s exists in '%s', but its head repository is unavailable or has been deleted.\n" \
            "$pr_number" "$upstream_repo" >&2
        return 1
    fi
    if [[ -z "$head_branch" ]]; then
        printf "git_swrp: PR #%s exists in '%s', but its head branch is unavailable or has been deleted.\n" \
            "$pr_number" "$upstream_repo" >&2
        return 1
    fi

    printf '%s\t%s\t%s\t%s\n' "$head_owner" "$head_repo" "$head_branch" "$pr_state"
}

_git_swrp_inspect_worktrees() {
    local wanted_path="$1"
    local wanted_branch_ref="$2"
    local field
    local worktree_path=
    local worktree_branch=
    local worktree_list_file

    _git_swrp_path_registered=0
    _git_swrp_path_branch=
    _git_swrp_branch_worktree=

    if ! worktree_list_file=$(mktemp "${TMPDIR:-/tmp}/git_swrp.worktrees.XXXXXX"); then
        echo "git_swrp: could not create temporary storage for worktree inspection." >&2
        return 1
    fi
    if ! git worktree list --porcelain -z >"$worktree_list_file"; then
        rm -f -- "$worktree_list_file"
        echo "git_swrp: could not inspect the repository's registered worktrees." >&2
        return 1
    fi

    while IFS= read -r -d '' field; do
        if [[ -z "$field" ]]; then
            if [[ "$worktree_path" == "$wanted_path" ]]; then
                _git_swrp_path_registered=1
                _git_swrp_path_branch="$worktree_branch"
            fi
            if [[ "$worktree_branch" == "$wanted_branch_ref" ]]; then
                _git_swrp_branch_worktree="$worktree_path"
            fi
            worktree_path=
            worktree_branch=
            continue
        fi

        case "$field" in
        worktree\ *)
            worktree_path="${field#worktree }"
            ;;
        branch\ *)
            worktree_branch="${field#branch }"
            ;;
        detached)
            worktree_branch=DETACHED
            ;;
        esac
    done <"$worktree_list_file"

    rm -f -- "$worktree_list_file"
}

_git_swrp_ref_namespace_conflict() {
    local wanted_ref="$1"
    local existing_ref

    while IFS= read -r existing_ref; do
        [[ "$existing_ref" == "$wanted_ref" ]] && continue
        if [[ "$existing_ref" == "$wanted_ref"/* || "$wanted_ref" == "$existing_ref"/* ]]; then
            printf '%s\n' "$existing_ref"
            return 0
        fi
    done < <(git for-each-ref --format='%(refname)' "${wanted_ref%%/*}")

    return 1
}

_git_swrp_validate_existing_remote() {
    local remote="$1"
    local expected_repo="$2"
    local remote_url
    local actual_repo
    local normalized_actual
    local normalized_expected

    if ! remote_url=$(git config --get "remote.$remote.url"); then
        printf "git_swrp: remote '%s' exists but has no configured fetch URL.\n" "$remote" >&2
        return 1
    fi
    if ! actual_repo=$(_git_swrp_github_repo_from_url "$remote_url"); then
        printf "git_swrp: remote '%s' does not have a recognized github.com repository URL.\n" "$remote" >&2
        return 1
    fi

    normalized_actual=$(_git_swrp_normalize_github_repo "$actual_repo") || return
    normalized_expected=$(_git_swrp_normalize_github_repo "$expected_repo") || return
    if [[ "$normalized_actual" != "$normalized_expected" ]]; then
        printf "git_swrp: remote '%s' points to '%s', but the PR head repository is '%s'.\n" \
            "$remote" "$actual_repo" "$expected_repo" >&2
        echo "git_swrp: refusing to rewrite or fetch the mismatched remote." >&2
        return 1
    fi
}

_git_swrp_remote_exists() {
    local wanted_remote="$1"
    local remotes
    local remote

    if ! remotes=$(git remote); then
        echo "git_swrp: could not inspect configured Git remotes." >&2
        return 2
    fi
    while IFS= read -r remote; do
        if [[ "$remote" == "$wanted_remote" ]]; then
            return 0
        fi
    done <<<"$remotes"

    return 1
}

_git_swrp_validate_registered_worktree() {
    local destination="$1"
    local local_branch="$2"
    local expected_upstream="$3"
    local common_dir="$4"
    local wanted_branch_ref="refs/heads/$local_branch"
    local destination_common_dir
    local actual_upstream

    if [[ "$_git_swrp_path_branch" != "$wanted_branch_ref" ]]; then
        printf "git_swrp: destination '%s' is already registered for '%s', not '%s'.\n" \
            "$destination" "${_git_swrp_path_branch:-a detached HEAD}" "$wanted_branch_ref" >&2
        return 1
    fi
    if [[ -L "$destination" || ! -d "$destination" ]]; then
        printf "git_swrp: destination '%s' is registered as a worktree, but its directory is missing, stale, or a symbolic link.\n" \
            "$destination" >&2
        echo "git_swrp: inspect 'git worktree list' and repair or prune it manually." >&2
        return 1
    fi
    if ! destination_common_dir=$(git -C "$destination" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) ||
        [[ "$destination_common_dir" != "$common_dir" ]]; then
        printf "git_swrp: destination '%s' no longer belongs to the current Git repository.\n" "$destination" >&2
        return 1
    fi
    if ! git show-ref --verify --quiet "$wanted_branch_ref"; then
        printf "git_swrp: worktree '%s' names missing local branch '%s'.\n" "$destination" "$local_branch" >&2
        return 1
    fi

    actual_upstream=$(git for-each-ref --format='%(upstream:short)' "$wanted_branch_ref")
    if [[ "$actual_upstream" != "$expected_upstream" ]]; then
        printf "git_swrp: local branch '%s' tracks '%s', not '%s'.\n" \
            "$local_branch" "${actual_upstream:-<nothing>}" "$expected_upstream" >&2
        return 1
    fi
}

git_swrp() {
    if [[ $# -eq 1 && ("$1" == -h || "$1" == --help) ]]; then
        _git_swrp_usage
        return 0
    fi
    if [[ $# -ne 1 ]]; then
        _git_swrp_usage >&2
        return 1
    fi

    local pr_number="$1"
    local repo_root repo_parent common_dir
    local upstream_repo pr_info
    local head_owner head_repo head_branch pr_state
    local local_branch local_branch_ref remote_tracking_ref expected_upstream
    local expected_head_repo expected_remote_url
    local destination
    local ref_conflict
    local existing_remote=0
    local initial_existing_worktree=0
    local remote_exists_status
    local remote_query_target
    local live_head_output live_head
    local fetched_head
    local actual_branch actual_upstream actual_head
    local lookup_status
    local _git_swrp_path_registered _git_swrp_path_branch _git_swrp_branch_worktree

    if [[ ! "$pr_number" =~ ^[1-9][0-9]*$ ]]; then
        printf "git_swrp: expected a positive PR number, got '%s'.\n" "$pr_number" >&2
        _git_swrp_usage >&2
        return 1
    fi
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "git_swrp: not inside a Git worktree." >&2
        return 1
    fi
    if ! repo_root=$(git rev-parse --show-toplevel); then
        echo "git_swrp: could not determine the current worktree root." >&2
        return 1
    fi
    if ! common_dir=$(git rev-parse --path-format=absolute --git-common-dir); then
        echo "git_swrp: could not determine the current repository's common Git directory." >&2
        return 1
    fi
    if [[ "$repo_root" != */* ]]; then
        printf "git_swrp: worktree root '%s' has no parent directory component.\n" "$repo_root" >&2
        return 1
    fi
    repo_parent="${repo_root%/*}"
    [[ -n "$repo_parent" ]] || repo_parent=/

    destination="$repo_parent/pr$pr_number"
    if ! upstream_repo=$(_git_swrp_upstream_repo); then
        return 1
    fi
    if pr_info=$(_git_swrp_lookup_pr "$upstream_repo" "$pr_number"); then
        :
    else
        lookup_status=$?
        return "$lookup_status"
    fi
    IFS=$'\t' read -r head_owner head_repo head_branch pr_state <<<"$pr_info"

    local_branch="${head_owner}→${head_branch}"
    local_branch_ref="refs/heads/$local_branch"
    remote_tracking_ref="refs/remotes/$head_owner/$head_branch"
    expected_upstream="$head_owner/$head_branch"
    expected_head_repo="$head_owner/$head_repo"
    expected_remote_url="https://github.com/$expected_head_repo"

    if ! git check-ref-format --branch "$local_branch" >/dev/null 2>&1; then
        printf "git_swrp: PR #%s produces invalid local branch name '%s'.\n" "$pr_number" "$local_branch" >&2
        return 1
    fi
    if ! git check-ref-format "refs/heads/$head_branch" >/dev/null 2>&1 ||
        ! git check-ref-format "$remote_tracking_ref" >/dev/null 2>&1; then
        printf "git_swrp: PR #%s reports invalid head branch '%s' for remote '%s'.\n" \
            "$pr_number" "$head_branch" "$head_owner" >&2
        return 1
    fi

    if ! _git_swrp_inspect_worktrees "$destination" "$local_branch_ref"; then
        return 1
    fi
    if [[ "$_git_swrp_path_registered" -eq 1 ]]; then
        if ! _git_swrp_validate_registered_worktree \
            "$destination" "$local_branch" "$expected_upstream" "$common_dir"; then
            return 1
        fi
        if [[ "$_git_swrp_branch_worktree" != "$destination" ]]; then
            printf "git_swrp: local branch '%s' is checked out in '%s' as well as '%s'.\n" \
                "$local_branch" "$_git_swrp_branch_worktree" "$destination" >&2
            return 1
        fi
        initial_existing_worktree=1
    else
        if [[ -e "$destination" || -L "$destination" ]]; then
            printf "git_swrp: destination '%s' already exists but is not a registered worktree for this repository.\n" \
                "$destination" >&2
            return 1
        fi
        if git show-ref --verify --quiet "$local_branch_ref"; then
            if [[ -n "$_git_swrp_branch_worktree" ]]; then
                printf "git_swrp: local branch '%s' is already checked out in worktree '%s'.\n" \
                    "$local_branch" "$_git_swrp_branch_worktree" >&2
            else
                printf "git_swrp: local branch '%s' already exists; refusing to reuse or reset it.\n" "$local_branch" >&2
            fi
            return 1
        fi
        if [[ -n "$_git_swrp_branch_worktree" ]]; then
            printf "git_swrp: stale worktree metadata says local branch '%s' is checked out in '%s'.\n" \
                "$local_branch" "$_git_swrp_branch_worktree" >&2
            echo "git_swrp: inspect 'git worktree list' and repair or prune it manually." >&2
            return 1
        fi
        if ref_conflict=$(_git_swrp_ref_namespace_conflict "$local_branch_ref"); then
            printf "git_swrp: local branch '%s' conflicts with existing ref '%s'.\n" "$local_branch" "$ref_conflict" >&2
            return 1
        fi
    fi

    if ref_conflict=$(_git_swrp_ref_namespace_conflict "$remote_tracking_ref"); then
        printf "git_swrp: remote-tracking branch '%s' conflicts with existing ref '%s'.\n" \
            "$expected_upstream" "$ref_conflict" >&2
        return 1
    fi

    if _git_swrp_remote_exists "$head_owner"; then
        existing_remote=1
        if ! _git_swrp_validate_existing_remote "$head_owner" "$expected_head_repo"; then
            return 1
        fi
        remote_query_target="$head_owner"
    else
        remote_exists_status=$?
        if [[ "$remote_exists_status" -eq 2 ]]; then
            return 1
        fi
        remote_query_target="$expected_remote_url"
    fi

    if ! live_head_output=$(git ls-remote --exit-code --heads \
        "$remote_query_target" "refs/heads/$head_branch"); then
        printf "git_swrp: PR #%s exists, but live head branch '%s/%s' is unavailable or could not be read.\n" \
            "$pr_number" "$head_owner" "$head_branch" >&2
        return 1
    fi
    live_head="${live_head_output%%$'\t'*}"
    if [[ -z "$live_head" || "$live_head" == "$live_head_output" ]]; then
        printf "git_swrp: live head query for '%s/%s' returned an invalid result.\n" \
            "$head_owner" "$head_branch" >&2
        return 1
    fi

    if [[ "$existing_remote" -eq 0 ]]; then
        printf "Adding remote '%s' for '%s'...\n" "$head_owner" "$expected_head_repo"
        if ! git remote add "$head_owner" "$expected_remote_url"; then
            printf "git_swrp: failed to add remote '%s' with URL '%s'.\n" "$head_owner" "$expected_remote_url" >&2
            return 1
        fi
    fi

    printf "Fetching PR #%s head '%s' from remote '%s'...\n" "$pr_number" "$head_branch" "$head_owner"
    # Remote-tracking refs are disposable mirrors. Allow a PR author's
    # force-push while never forcing, resetting, or moving a local branch.
    if ! git fetch --no-tags "$head_owner" "+refs/heads/$head_branch:$remote_tracking_ref"; then
        printf "git_swrp: failed to fetch live head branch '%s/%s' for PR #%s.\n" \
            "$head_owner" "$head_branch" "$pr_number" >&2
        if [[ "$existing_remote" -eq 0 ]]; then
            printf "git_swrp: remote '%s' was added successfully and has been left in place.\n" "$head_owner" >&2
        fi
        return 1
    fi
    if ! fetched_head=$(git rev-parse --verify "$remote_tracking_ref^{commit}" 2>/dev/null); then
        printf "git_swrp: fetch completed but did not create remote-tracking ref '%s'.\n" "$remote_tracking_ref" >&2
        return 1
    fi

    if ! _git_swrp_inspect_worktrees "$destination" "$local_branch_ref"; then
        return 1
    fi
    if [[ "$initial_existing_worktree" -eq 1 ]]; then
        if [[ "$_git_swrp_path_registered" -ne 1 ]] ||
            ! _git_swrp_validate_registered_worktree \
                "$destination" "$local_branch" "$expected_upstream" "$common_dir"; then
            echo "git_swrp: the existing worktree changed while the PR head was being fetched." >&2
            return 1
        fi
        printf "Using existing worktree '%s'; local branch '%s' was not moved.\n" "$destination" "$local_branch"
    else
        if [[ "$_git_swrp_path_registered" -eq 1 || -e "$destination" || -L "$destination" ]]; then
            printf "git_swrp: destination '%s' appeared while the PR head was being fetched; refusing to overwrite it.\n" \
                "$destination" >&2
            return 1
        fi
        if git show-ref --verify --quiet "$local_branch_ref"; then
            printf "git_swrp: local branch '%s' appeared while the PR head was being fetched; refusing to reuse it.\n" \
                "$local_branch" >&2
            return 1
        fi
        if ref_conflict=$(_git_swrp_ref_namespace_conflict "$local_branch_ref"); then
            printf "git_swrp: local branch '%s' now conflicts with existing ref '%s'.\n" "$local_branch" "$ref_conflict" >&2
            return 1
        fi

        printf "Creating worktree '%s' with local branch '%s' tracking '%s'...\n" \
            "$destination" "$local_branch" "$expected_upstream"
        if ! git worktree add --track -b "$local_branch" "$destination" "$remote_tracking_ref"; then
            printf "git_swrp: failed to create worktree '%s'.\n" "$destination" >&2
            echo "git_swrp: inspect 'git worktree list' and local branches for any partial state." >&2
            return 1
        fi

        if ! actual_branch=$(git -C "$destination" symbolic-ref --quiet --short HEAD) ||
            [[ "$actual_branch" != "$local_branch" ]]; then
            printf "git_swrp: created worktree '%s', but it checked out '%s' instead of '%s'.\n" \
                "$destination" "${actual_branch:-a detached HEAD}" "$local_branch" >&2
            return 1
        fi
        actual_upstream=$(git -C "$destination" for-each-ref --format='%(upstream:short)' "$local_branch_ref")
        if [[ "$actual_upstream" != "$expected_upstream" ]]; then
            printf "git_swrp: created branch '%s', but it tracks '%s' instead of '%s'.\n" \
                "$local_branch" "${actual_upstream:-<nothing>}" "$expected_upstream" >&2
            return 1
        fi
        if ! actual_head=$(git -C "$destination" rev-parse --verify HEAD) || [[ "$actual_head" != "$fetched_head" ]]; then
            printf "git_swrp: created worktree HEAD '%s' does not match fetched PR head '%s'.\n" \
                "${actual_head:-<unavailable>}" "$fetched_head" >&2
            return 1
        fi
    fi

    if ! builtin cd -- "$destination"; then
        printf "git_swrp: worktree is ready, but could not change directory to '%s'.\n" "$destination" >&2
        return 1
    fi
    printf "Now in worktree '%s' for PR #%s (%s).\n" "$destination" "$pr_number" "$pr_state"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "git_swrp.sh defines a shell function and must be sourced; use 'git_swrp <PR-number>' from an initialized shell." >&2
    exit 1
fi
