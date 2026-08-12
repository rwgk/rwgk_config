# shellcheck shell=bash

# Shared Git and GitHub inspection helpers for branch archive commands.
# This file is sourced by executables in this directory and has no main entry
# point or shell-option side effects.

get_remote_upstream_info() {
    local branch="$1"
    local remote remote_ref

    remote=$(git for-each-ref --format='%(upstream:remotename)' "refs/heads/$branch")
    remote_ref=$(git for-each-ref --format='%(upstream:remoteref)' "refs/heads/$branch")

    if [[ -z "$remote" ]] || [[ -z "$remote_ref" ]] || [[ "$remote" == "." ]]; then
        return 1
    fi
    if [[ "$remote_ref" != refs/heads/* ]]; then
        return 1
    fi

    printf '%s\t%s\n' "$remote" "${remote_ref#refs/heads/}"
}

get_remote_branch_sha() {
    local remote="$1"
    local remote_branch="$2"
    local ls_remote_output

    if ! ls_remote_output=$(git ls-remote --heads "$remote" "refs/heads/$remote_branch"); then
        echo "Error: failed to query remote branch '$remote/$remote_branch'." >&2
        return 1
    fi

    if [[ -n "$ls_remote_output" ]]; then
        printf '%s\n' "${ls_remote_output%%$'\t'*}"
    fi
}

get_remote_push_branch_sha() {
    local remote="$1"
    local remote_branch="$2"
    local push_url ls_remote_output

    if ! push_url=$(git remote get-url --push "$remote" 2>/dev/null); then
        printf "Error: failed to determine the push URL for remote '%s'.\n" "$remote" >&2
        return 1
    fi
    if ! ls_remote_output=$(git ls-remote --heads "$push_url" "refs/heads/$remote_branch" 2>/dev/null); then
        printf "Error: failed to query branch '%s/%s' through its push URL.\n" "$remote" "$remote_branch" >&2
        return 1
    fi

    if [[ -n "$ls_remote_output" ]]; then
        printf '%s\n' "${ls_remote_output%%$'\t'*}"
    fi
}

get_github_remote_repo_info() {
    local remote="$1"
    local url_kind="${2:-push}"
    local remote_url gh_output repo_metadata viewer_login repo_url repo_host repo_name is_fork repo_owner pulls_repo viewer_can_admin

    if ! command -v gh >/dev/null 2>&1; then
        echo "Error: 'gh' is not installed or not on PATH." >&2
        return 1
    fi

    case "$url_kind" in
    fetch)
        if ! remote_url=$(git remote get-url "$remote" 2>&1); then
            printf '%s\n' "$remote_url" >&2
            return 1
        fi
        ;;
    push)
        if ! remote_url=$(git remote get-url --push "$remote" 2>&1); then
            printf '%s\n' "$remote_url" >&2
            return 1
        fi
        ;;
    *)
        printf "Error: unknown Git remote URL kind '%s'.\n" "$url_kind" >&2
        return 1
        ;;
    esac

    if [[ -z "$remote_url" ]]; then
        printf "Error: remote '%s' has no %s URL.\n" "$remote" "$url_kind" >&2
        return 1
    fi

    if ! gh_output=$(gh repo view "$remote_url" \
        --json url,nameWithOwner \
        --jq '[.url, .nameWithOwner] | @tsv' 2>&1); then
        printf '%s\n' "$gh_output" >&2
        return 1
    fi

    IFS=$'\t' read -r repo_url repo_name <<<"$gh_output"
    repo_host="${repo_url#https://}"
    repo_host="${repo_host%%/*}"
    if [[ -z "$repo_host" || "$repo_host" == "$repo_url" ]]; then
        printf "Error: cannot determine GitHub host from repository URL '%s'.\n" "$repo_url" >&2
        return 1
    fi

    if ! repo_metadata=$(GH_HOST="$repo_host" gh api "repos/$repo_name" \
        --jq '[.fork, .owner.login, (if .fork then (.source.full_name // .parent.full_name // "") else .full_name end), (.permissions.admin // false)] | @tsv' 2>&1); then
        printf '%s\n' "$repo_metadata" >&2
        return 1
    fi

    IFS=$'\t' read -r is_fork repo_owner pulls_repo viewer_can_admin <<<"$repo_metadata"
    if [[ -z "$pulls_repo" ]]; then
        printf "Error: cannot determine the pull-request base repository for fork '%s'.\n" "$repo_name" >&2
        return 1
    fi

    if ! viewer_login=$(gh api --hostname "$repo_host" user --jq .login 2>&1); then
        printf '%s\n' "$viewer_login" >&2
        return 1
    fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$repo_name" \
        "$is_fork" \
        "$repo_owner" \
        "$pulls_repo" \
        "$viewer_login" \
        "$repo_host" \
        "${viewer_can_admin:-false}"
}

get_owned_fork_remote_info() {
    local remote="$1"
    local repo_info repo_name is_fork repo_owner pulls_repo viewer_login repo_host viewer_can_admin

    if ! repo_info=$(get_github_remote_repo_info "$remote" push 2>&1); then
        printf "Error: cannot determine whether archive remote '%s' is a GitHub fork owned or administered by you.\n" "$remote" >&2
        printf '%s\n' "$repo_info" >&2
        return 1
    fi

    IFS=$'\t' read -r repo_name is_fork repo_owner pulls_repo viewer_login repo_host viewer_can_admin <<<"$repo_info"
    # Push permission is intentionally insufficient here: organization members
    # can push to colleagues' forks. ADMIN is the ownership-equivalent signal
    # for an organization-owned fork selected as an archive destination.
    if [[ "$is_fork" != "true" || ("$repo_owner" != "$viewer_login" && "$viewer_can_admin" != "true") ]]; then
        printf "Error: archive remote '%s' points to '%s', which is not a GitHub fork owned or administered by your authenticated user.\n" "$remote" "$repo_name" >&2
        printf "Authenticated GitHub user: '%s'; repository owner: '%s'; is fork: '%s'; viewer can administer: '%s'.\n" \
            "$viewer_login" "$repo_owner" "$is_fork" "${viewer_can_admin:-false}" >&2
        return 1
    fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$repo_name" \
        "$is_fork" \
        "$repo_owner" \
        "$pulls_repo" \
        "$viewer_login" \
        "$repo_host" \
        "${viewer_can_admin:-false}"
}

find_owned_fork_remote_name() {
    local remote

    while IFS= read -r remote; do
        if get_owned_fork_remote_info "$remote" >/dev/null 2>&1; then
            printf '%s\n' "$remote"
            return 0
        fi
    done < <(git remote)

    return 1
}

query_pr_info_for_remote_branch() {
    local pulls_repo="$1"
    local head_repo="$2"
    local remote_branch="$3"
    local pr_state_filter="${4:-all}"
    local github_host="${5:-}"
    local head_owner gh_output row candidate_head_repo candidate_head_ref
    local pr_number pr_state pr_url is_draft is_merged
    local -a gh_args=(
        api "repos/$pulls_repo/pulls"
        --method GET
        --paginate
        -f "state=$pr_state_filter"
        -f per_page=100
        --jq '.[] | [(.head.repo.full_name // "NONE"), (.head.ref // "NONE"), .number, .state, .html_url, .draft, (.merged_at != null)] | @tsv'
    )

    if ! command -v gh >/dev/null 2>&1; then
        echo "Error: 'gh' is not installed or not on PATH." >&2
        return 1
    fi
    if [[ "$head_repo" != */* ]]; then
        printf "Error: invalid GitHub head repository name '%s'.\n" "$head_repo" >&2
        return 1
    fi

    head_owner="${head_repo%%/*}"
    gh_args+=(-f "head=$head_owner:$remote_branch")

    if [[ -n "$github_host" ]]; then
        gh_output=$(GH_HOST="$github_host" gh "${gh_args[@]}" 2>&1) || {
            printf '%s\n' "$gh_output" >&2
            return 1
        }
    elif ! gh_output=$(gh "${gh_args[@]}" 2>&1); then
        printf '%s\n' "$gh_output" >&2
        return 1
    fi

    while IFS= read -r row; do
        [[ -n "$row" ]] || continue
        IFS=$'\t' read -r candidate_head_repo candidate_head_ref pr_number pr_state pr_url is_draft is_merged <<<"$row"
        if [[ "$candidate_head_repo" == "$head_repo" && "$candidate_head_ref" == "$remote_branch" ]]; then
            printf '%s\t%s\t%s\t%s\t%s\n' "$pr_number" "$pr_state" "$pr_url" "$is_draft" "$is_merged"
            return 0
        fi
    done <<<"$gh_output"
}

query_merged_pr_info_for_head_sha() {
    local repo_name="$1"
    local head_repo="$2"
    local remote_branch="$3"
    local head_sha="$4"
    local github_host="${5:-}"
    local head_owner gh_output row candidate_head_repo candidate_head_ref candidate_head_sha
    local pr_number pr_url merged_at
    local -a gh_args=(
        api "repos/$repo_name/pulls"
        --method GET
        --paginate
        -f state=all
        -f per_page=100
        --jq '.[] | [(.head.repo.full_name // "NONE"), (.head.ref // "NONE"), (.head.sha // "NONE"), .number, .html_url, (.merged_at // "NONE")] | @tsv'
    )

    if ! command -v gh >/dev/null 2>&1; then
        echo "Error: 'gh' is not installed or not on PATH." >&2
        return 1
    fi
    if [[ "$head_repo" != */* ]]; then
        printf "Error: invalid GitHub head repository name '%s'.\n" "$head_repo" >&2
        return 1
    fi

    head_owner="${head_repo%%/*}"
    gh_args+=(-f "head=$head_owner:$remote_branch")

    if [[ -n "$github_host" ]]; then
        gh_output=$(GH_HOST="$github_host" gh "${gh_args[@]}" 2>&1) || {
            printf '%s\n' "$gh_output" >&2
            return 1
        }
    elif ! gh_output=$(gh "${gh_args[@]}" 2>&1); then
        printf '%s\n' "$gh_output" >&2
        return 1
    fi

    while IFS= read -r row; do
        [[ -n "$row" ]] || continue
        IFS=$'\t' read -r candidate_head_repo candidate_head_ref candidate_head_sha pr_number pr_url merged_at <<<"$row"
        if [[ "$candidate_head_repo" == "$head_repo" &&
            "$candidate_head_ref" == "$remote_branch" &&
            "$candidate_head_sha" == "$head_sha" &&
            "$merged_at" != "NONE" ]]; then
            printf '%s\tMERGED\t%s\t%s\n' "$pr_number" "$pr_url" "$candidate_head_sha"
            return 0
        fi
    done <<<"$gh_output"
}

query_merged_pr_details_for_branch() {
    local pulls_repo="$1"
    local remote_branch="$2"
    local github_host="${3:-}"
    local gh_output
    local -a gh_args=(
        pr list
        --repo "$pulls_repo"
        --state merged
        --head "$remote_branch"
        --limit 100
        --json 'number,url,mergedAt,headRefOid,headRefName,headRepositoryOwner,baseRefName,baseRefOid,mergeCommit'
        --jq '.[] | [.number, .url, .mergedAt, .headRefOid, .headRefName, (.headRepositoryOwner.login // "NONE"), .baseRefName, .baseRefOid, (.mergeCommit.oid // "NONE")] | @tsv'
    )

    if [[ -n "$github_host" ]]; then
        gh_output=$(GH_HOST="$github_host" gh "${gh_args[@]}" 2>&1) || {
            printf '%s\n' "$gh_output" >&2
            return 1
        }
    elif ! gh_output=$(gh "${gh_args[@]}" 2>&1); then
        printf '%s\n' "$gh_output" >&2
        return 1
    fi

    printf '%s\n' "$gh_output"
}

query_pr_details_by_number() {
    local pulls_repo="$1"
    local pr_number="$2"
    local github_host="${3:-}"
    local gh_output
    local -a gh_args=(
        api "repos/$pulls_repo/pulls/$pr_number"
        --jq '[.number, .html_url, (.merged_at // "NONE"), (.head.sha // "NONE"), (.head.ref // "NONE"), (.head.repo.owner.login // "NONE"), (.base.ref // "NONE"), (.base.sha // "NONE"), (.merge_commit_sha // "NONE")] | @tsv'
    )

    if [[ -n "$github_host" ]]; then
        gh_output=$(GH_HOST="$github_host" gh "${gh_args[@]}" 2>&1) || {
            printf '%s\n' "$gh_output" >&2
            return 1
        }
    elif ! gh_output=$(gh "${gh_args[@]}" 2>&1); then
        printf '%s\n' "$gh_output" >&2
        return 1
    fi

    printf '%s\n' "$gh_output"
}
