#!/bin/bash
set -euo pipefail

script_dir=$(
    CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P
)
# shellcheck source=bin/_git_branch_archive_common.sh
source "$script_dir/_git_branch_archive_common.sh"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [--pr NUMBER] [--fork-remote REMOTE] <branch>

Archive the exact local branch tip to the authenticated user's GitHub fork.
The local branch, its tracking configuration, and its source branch are left
unchanged.

Options:
  --pr NUMBER             Use this merged PR instead of discovering it from
                          the base repository and exact branch name.
  --fork-remote REMOTE    Personal-fork remote to archive to (default: origin).
  -h, --help              Show this help.
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

format_github_timestamp_in_pacific() {
    local github_timestamp="$1"
    local result epoch

    if result=$(TZ=America/Los_Angeles date --date="$github_timestamp" '+%Y-%m-%d+%H%M%S' 2>/dev/null); then
        printf '%s\n' "$result"
        return
    fi

    if epoch=$(date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$github_timestamp" '+%s' 2>/dev/null) &&
        result=$(TZ=America/Los_Angeles date -r "$epoch" '+%Y-%m-%d+%H%M%S' 2>/dev/null); then
        printf '%s\n' "$result"
        return
    fi

    printf "Error: cannot convert GitHub timestamp '%s' to America/Los_Angeles.\n" "$github_timestamp" >&2
    return 1
}

resolve_merged_pr_info() {
    local pulls_repo="$1"
    local branch="$2"
    local github_host="$3"
    local requested_pr="$4"
    local query_output row
    local -a rows=()

    if [[ -n "$requested_pr" ]]; then
        if ! query_output=$(query_pr_details_by_number "$pulls_repo" "$requested_pr" "$github_host"); then
            return 1
        fi
        [[ -n "$query_output" ]] || {
            printf "Error: PR #%s was not found in '%s'.\n" "$requested_pr" "$pulls_repo" >&2
            return 1
        }
        printf '%s\n' "$query_output"
        return
    fi

    if ! query_output=$(query_merged_pr_details_for_branch "$pulls_repo" "$branch" "$github_host"); then
        return 1
    fi
    if [[ -n "$query_output" ]]; then
        while IFS= read -r row; do
            rows+=("$row")
        done <<<"$query_output"
    fi

    case "${#rows[@]}" in
    0)
        printf "Error: no merged PR in '%s' was found for head branch '%s'.\n" "$pulls_repo" "$branch" >&2
        echo "Use --pr NUMBER if the PR cannot be discovered from its former head branch." >&2
        return 1
        ;;
    1)
        printf '%s\n' "${rows[0]}"
        return
        ;;
    esac

    printf "Error: multiple merged PRs in '%s' use former head branch '%s'.\n" "$pulls_repo" "$branch" >&2
    echo "Use --pr NUMBER to select the intended PR." >&2
    return 1
}

save_backtracking_record() {
    local source_file="$1"
    local destination_name="$2"
    local archive_ref="$3"
    local archive_sha="$4"
    local destination target temporary_target
    local remote_destination remote_host remote_directory remote_temporary_name remote_temporary_target
    local remote_command ssh_output

    if [[ -n "${MY_GIT_BACKTRACKING_INFO_LOCAL:-}" ]]; then
        destination="$MY_GIT_BACKTRACKING_INFO_LOCAL"
        target="$destination/$destination_name"

        if [[ -e "$target" ]]; then
            if grep -Fqx "Archive ref: '$archive_ref'" "$target" &&
                grep -Fqx "Verified archive SHA: '$archive_sha'" "$target" &&
                grep -Fqx 'Backtracking record complete.' "$target"; then
                printf "Backtracking information already exists: '%s'\n" "$target"
                return
            fi
            printf "Error: refusing to overwrite unrelated backtracking information: '%s'.\n" "$target" >&2
            return 1
        fi

        if ! temporary_target=$(mktemp "$destination/.${destination_name}.XXXXXX"); then
            printf "Error: failed to create a temporary backtracking file in '%s'.\n" "$destination" >&2
            return 1
        fi
        if ! cp -p "$source_file" "$temporary_target"; then
            rm -f -- "$temporary_target"
            printf "Error: failed to save backtracking information to '%s'.\n" "$target" >&2
            return 1
        fi

        if ! mv -n "$temporary_target" "$target"; then
            rm -f -- "$temporary_target"
            printf "Error: failed to publish backtracking information to '%s'.\n" "$target" >&2
            return 1
        fi
        if [[ ! -e "$temporary_target" ]]; then
            printf "Backed up branch archive information to: '%s'\n" "$target"
            return
        fi

        rm -f -- "$temporary_target"
        if grep -Fqx "Archive ref: '$archive_ref'" "$target" &&
            grep -Fqx "Verified archive SHA: '$archive_sha'" "$target" &&
            grep -Fqx 'Backtracking record complete.' "$target"; then
            printf "Backtracking information already exists: '%s'\n" "$target"
            return
        fi
        printf "Error: refusing to overwrite unrelated backtracking information: '%s'.\n" "$target" >&2
        return 1
    fi

    remote_destination="${MY_GIT_BACKTRACKING_INFO_REMOTE%/}"
    if [[ "$remote_destination" != *:* ]]; then
        printf "Error: MY_GIT_BACKTRACKING_INFO_REMOTE must have HOST:PATH form (got '%s').\n" "$remote_destination" >&2
        return 1
    fi
    remote_host="${remote_destination%%:*}"
    remote_directory="${remote_destination#*:}"
    if [[ -z "$remote_host" || -z "$remote_directory" ]]; then
        printf "Error: MY_GIT_BACKTRACKING_INFO_REMOTE must have non-empty HOST and PATH components.\n" >&2
        return 1
    fi

    remote_temporary_name=".${destination_name}.$$.${RANDOM}.tmp"
    remote_temporary_target="$remote_host:$remote_directory/$remote_temporary_name"
    if ! scp -p "$source_file" "$remote_temporary_target"; then
        printf "Error: failed to upload temporary backtracking information to '%s'.\n" "$remote_destination" >&2
        return 1
    fi

    printf -v remote_command 'bash -s -- %q %q %q %q %q' \
        "$remote_directory" \
        "$destination_name" \
        "$remote_temporary_name" \
        "$archive_ref" \
        "$archive_sha"

    if ! ssh_output=$(
        # remote_command is assembled with printf %q for the remote Bash.
        # shellcheck disable=SC2029
        ssh "$remote_host" "$remote_command" 2>&1 <<'REMOTE_BASH'
set -euo pipefail

directory="$1"
destination_name="$2"
temporary_name="$3"
archive_ref="$4"
archive_sha="$5"
source_path="$directory/$temporary_name"
target="$directory/$destination_name"

cleanup() {
    rm -f -- "$source_path"
}
trap cleanup EXIT

record_matches() {
    grep -Fqx "Archive ref: '$archive_ref'" "$target" &&
        grep -Fqx "Verified archive SHA: '$archive_sha'" "$target" &&
        grep -Fqx 'Backtracking record complete.' "$target"
}

if [[ -e "$target" ]]; then
    if record_matches; then
        echo EXISTS
        exit
    fi
    printf "Error: refusing to overwrite unrelated backtracking information: '%s'.\n" "$target" >&2
    exit 1
fi

if ! mv -n "$source_path" "$target"; then
    printf "Error: failed to publish backtracking information to '%s'.\n" "$target" >&2
    exit 1
fi
if [[ -e "$source_path" ]]; then
    if record_matches; then
        echo EXISTS
        exit
    fi
    printf "Error: refusing to overwrite concurrently-created backtracking information: '%s'.\n" "$target" >&2
    exit 1
fi

echo SAVED
REMOTE_BASH
    ); then
        printf "Error: failed to publish backtracking information at '%s/%s'.\n" "$remote_destination" "$destination_name" >&2
        if [[ -n "$ssh_output" ]]; then
            printf '%s\n' "$ssh_output" >&2
        fi
        return 1
    fi

    case "$ssh_output" in
    EXISTS)
        printf "Backtracking information already exists: '%s/%s'\n" "$remote_destination" "$destination_name"
        ;;
    SAVED)
        printf "Backed up branch archive information to: '%s/%s'\n" "$remote_destination" "$destination_name"
        ;;
    *)
        printf "Error: unexpected response while publishing backtracking information at '%s/%s': '%s'.\n" \
            "$remote_destination" "$destination_name" "$ssh_output" >&2
        return 1
        ;;
    esac
}

main() {
    local fork_remote=origin
    local requested_pr=""
    local arg branch local_ref local_sha current_branch dirty_warning=""
    local fork_info fork_repo pulls_repo github_host
    local pr_info pr_number pr_url merged_at pr_head_sha pr_head_ref pr_head_owner
    local base_ref base_sha merge_commit_sha archive_timestamp
    local archive_branch archive_ref existing_archive_sha verified_archive_sha push_output
    local upstream_info tracking_remote="" tracking_branch="" tracking_ref=""
    local cached_tracking_sha="" live_tracking_sha="" tracking_lookup_note=""
    local comparison_sha comparison_label comparison_counts local_only_count remote_only_count
    local local_commit_noun remote_commit_noun remote_commit_verb comparison_line warning_line
    local comparison_info="" comparison_warning=""
    local repo_root repo_name safe_repo safe_branch destination_name infofile host_name
    local recovery_ref
    local comparison_index
    local -a positional=() comparison_shas=() comparison_labels=()

    while [[ $# -gt 0 ]]; do
        arg="$1"
        shift
        case "$arg" in
        --pr=*)
            requested_pr="${arg#--pr=}"
            ;;
        --pr)
            [[ $# -gt 0 ]] || die "--pr requires a PR number."
            requested_pr="$1"
            shift
            ;;
        --fork-remote=*)
            fork_remote="${arg#--fork-remote=}"
            ;;
        --fork-remote)
            [[ $# -gt 0 ]] || die "--fork-remote requires a remote name."
            fork_remote="$1"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        --)
            positional+=("$@")
            break
            ;;
        -*)
            die "unknown option '$arg'."
            ;;
        *)
            positional+=("$arg")
            ;;
        esac
    done

    if [[ "${#positional[@]}" -ne 1 ]]; then
        usage
        exit 1
    fi
    branch="${positional[0]}"

    if [[ -n "$requested_pr" && ! "$requested_pr" =~ ^[1-9][0-9]*$ ]]; then
        die "--pr requires a positive integer (got '$requested_pr')."
    fi
    [[ -n "$fork_remote" ]] || die "--fork-remote requires a non-empty remote name."
    [[ "$fork_remote" != upstream ]] || die "refusing to push through the 'upstream' remote."

    git rev-parse --git-dir >/dev/null 2>&1 || die "current directory is not in a Git repository."
    local_ref="refs/heads/$branch"
    git show-ref --verify --quiet "$local_ref" || die "local branch '$branch' was not found."
    local_sha=$(git rev-parse --verify "${local_ref}^{commit}")

    if [[ -z "${MY_GIT_BACKTRACKING_INFO_LOCAL:-}" && -z "${MY_GIT_BACKTRACKING_INFO_REMOTE:-}" ]]; then
        die "neither MY_GIT_BACKTRACKING_INFO_LOCAL nor MY_GIT_BACKTRACKING_INFO_REMOTE is set."
    fi
    if [[ -n "${MY_GIT_BACKTRACKING_INFO_LOCAL:-}" && ! -d "$MY_GIT_BACKTRACKING_INFO_LOCAL" ]]; then
        die "backtracking information directory does not exist: '$MY_GIT_BACKTRACKING_INFO_LOCAL'."
    fi

    if ! fork_info=$(get_owned_fork_remote_info "$fork_remote" 2>&1); then
        printf '%s\n' "$fork_info" >&2
        exit 1
    fi
    IFS=$'\t' read -r fork_repo _ _ pulls_repo _ github_host <<<"$fork_info"

    if ! pr_info=$(resolve_merged_pr_info "$pulls_repo" "$branch" "$github_host" "$requested_pr"); then
        exit 1
    fi
    IFS=$'\t' read -r pr_number pr_url merged_at pr_head_sha pr_head_ref pr_head_owner base_ref base_sha merge_commit_sha <<<"$pr_info"

    [[ "$merged_at" != NONE ]] || die "PR #$pr_number is not merged."
    [[ "$pr_head_ref" == "$branch" ]] ||
        die "PR #$pr_number head branch '$pr_head_ref' does not match local branch '$branch'."
    archive_timestamp=$(format_github_timestamp_in_pacific "$merged_at")

    archive_branch="archive/pr${pr_number}_${archive_timestamp}_${branch}"
    archive_ref="refs/heads/$archive_branch"
    git check-ref-format "$archive_ref" >/dev/null ||
        die "generated archive ref is invalid: '$archive_ref'."

    current_branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    if [[ "$current_branch" == "$branch" ]] && [[ -n "$(git status --porcelain)" ]]; then
        dirty_warning="WARNING: the current worktree was dirty; uncommitted changes were not archived."
        printf '%s\n' "$dirty_warning" >&2
    fi

    if upstream_info=$(get_remote_upstream_info "$branch"); then
        IFS=$'\t' read -r tracking_remote tracking_branch <<<"$upstream_info"
        tracking_ref=$(git for-each-ref --format='%(upstream)' "$local_ref")
        cached_tracking_sha=$(git rev-parse --verify "${tracking_ref}^{commit}" 2>/dev/null || true)
        if ! live_tracking_sha=$(get_remote_branch_sha "$tracking_remote" "$tracking_branch" 2>&1); then
            tracking_lookup_note="$live_tracking_sha"
            live_tracking_sha=""
        fi
    fi

    if [[ -n "$live_tracking_sha" ]]; then
        comparison_shas+=("$live_tracking_sha")
        comparison_labels+=("live remote branch '$tracking_remote/$tracking_branch'")
    fi
    if [[ -n "$cached_tracking_sha" &&
        "$cached_tracking_sha" != "$live_tracking_sha" &&
        "$cached_tracking_sha" != "$pr_head_sha" ]]; then
        comparison_shas+=("$cached_tracking_sha")
        comparison_labels+=("cached remote-tracking ref '$tracking_ref'")
    fi
    if [[ "$pr_head_sha" != "$live_tracking_sha" ]]; then
        comparison_shas+=("$pr_head_sha")
        comparison_labels+=("PR head snapshot '$pr_head_owner/$pr_head_ref'")
    fi

    for comparison_index in "${!comparison_shas[@]}"; do
        comparison_sha="${comparison_shas[comparison_index]}"
        comparison_label="${comparison_labels[comparison_index]}"
        warning_line=""

        if git cat-file -e "${comparison_sha}^{commit}" 2>/dev/null; then
            comparison_counts=$(git rev-list --left-right --count "$local_sha...$comparison_sha")
            read -r local_only_count remote_only_count <<<"$comparison_counts"
            local_commit_noun=commits
            remote_commit_noun=commits
            remote_commit_verb=were
            [[ "$local_only_count" -eq 1 ]] && local_commit_noun=commit
            if [[ "$remote_only_count" -eq 1 ]]; then
                remote_commit_noun=commit
                remote_commit_verb=was
            fi
            comparison_line="INFO: $comparison_label was $remote_only_count $remote_commit_noun ahead of and $local_only_count $local_commit_noun behind the local branch."
            if [[ "$remote_only_count" -gt 0 ]]; then
                warning_line="WARNING: only the local branch was archived; $remote_only_count remote-only $remote_commit_noun from $comparison_label $remote_commit_verb not preserved by the archive ref."
            fi
        else
            comparison_line="INFO: ahead/behind counts for $comparison_label are unavailable because commit '$comparison_sha' is not present locally."
            warning_line="WARNING: only the local branch was archived; whether $comparison_label has remote-only history could not be determined."
        fi

        if [[ -n "$comparison_info" ]]; then
            comparison_info+=$'\n'
        fi
        comparison_info+="$comparison_line"
        if [[ -n "$warning_line" ]]; then
            if [[ -n "$comparison_warning" ]]; then
                comparison_warning+=$'\n'
            fi
            comparison_warning+="$warning_line"
        fi
    done

    printf '%s\n' "$comparison_info" >&2
    if [[ -n "$comparison_warning" ]]; then
        printf '%s\n' "$comparison_warning" >&2
    fi

    if ! existing_archive_sha=$(get_remote_push_branch_sha "$fork_remote" "$archive_branch"); then
        exit 1
    fi
    if [[ -n "$existing_archive_sha" && "$existing_archive_sha" != "$local_sha" ]]; then
        die "archive ref '$fork_remote/$archive_branch' already exists at '$existing_archive_sha', not local tip '$local_sha'; refusing to overwrite it."
    fi

    if [[ -z "$existing_archive_sha" ]]; then
        if ! push_output=$(git push --porcelain \
            --force-with-lease="$archive_ref:" \
            "$fork_remote" \
            "$local_sha:$archive_ref" 2>&1); then
            printf "Error: failed to create archive ref '%s/%s'.\n" "$fork_remote" "$archive_branch" >&2
            printf '%s\n' "$push_output" >&2
            exit 1
        fi
        printf '%s\n' "$push_output"
    else
        printf "Archive ref already exists at the local tip: '%s/%s'.\n" "$fork_remote" "$archive_branch"
    fi

    if ! verified_archive_sha=$(get_remote_push_branch_sha "$fork_remote" "$archive_branch"); then
        exit 1
    fi
    if [[ "$verified_archive_sha" != "$local_sha" ]]; then
        die "archive verification failed: '$fork_remote/$archive_branch' is at '$verified_archive_sha', expected '$local_sha'."
    fi

    repo_root=$(git rev-parse --show-toplevel)
    repo_name=$(basename "$repo_root")
    safe_repo="${repo_name//[^A-Za-z0-9._@-]/_}"
    safe_branch="${branch//[^A-Za-z0-9._@-]/_}"
    destination_name="${safe_repo}_${safe_branch}_pr${pr_number}_${archive_timestamp}.txt"
    infofile=$(mktemp "/tmp/${destination_name}.XXXXXX")
    trap 'rm -f "${infofile:-}"' EXIT
    host_name=$(hostfqdn 2>/dev/null || hostname -f 2>/dev/null || hostname)
    recovery_ref="refs/heads/recovered/pr${pr_number}_${safe_branch}"

    {
        echo "Current host: '$host_name'"
        echo "Current working directory: '$(pwd)'"
        echo "Repository: '$repo_name'"
        echo "Archived the exact local branch tip to the authenticated user's fork without deleting the local or source branch."
        echo
        echo "Local branch: '$branch'"
        echo "Local branch HEAD SHA: '$local_sha'"
        echo "Fork repository: '$fork_repo'"
        echo "Fork remote: '$fork_remote'"
        echo "Archive branch: '$archive_branch'"
        echo "Archive ref: '$archive_ref'"
        echo "Verified archive SHA: '$verified_archive_sha'"
        echo "Archive timestamp: '$archive_timestamp' (PR merge time in America/Los_Angeles)"
        echo
        echo "Pull request: '$pr_url'"
        echo "PR head branch: '$pr_head_owner/$pr_head_ref'"
        echo "PR head SHA: '$pr_head_sha'"
        echo "PR head ref: 'refs/pull/$pr_number/head'"
        echo "PR merge commit SHA: '$merge_commit_sha'"
        echo "PR merged at: '$merged_at'"
        echo "PR base branch: '$base_ref'"
        echo "PR base SHA: '$base_sha'"
        echo
        if [[ -n "$tracking_ref" ]]; then
            echo "Configured remote-tracking ref: '$tracking_ref'"
            echo "Cached remote-tracking SHA: '${cached_tracking_sha:-not found}'"
            echo "Live remote branch SHA: '${live_tracking_sha:-not found}'"
            if [[ -n "$tracking_lookup_note" ]]; then
                echo "Remote branch lookup note: '$tracking_lookup_note'"
            fi
        else
            echo "Configured remote-tracking ref: 'none'"
        fi
        echo "$comparison_info"
        if [[ -n "$comparison_warning" ]]; then
            echo "$comparison_warning"
        fi
        if [[ -n "$dirty_warning" ]]; then
            echo "$dirty_warning"
        fi
        echo
        echo "Recovery command:"
        printf '  git fetch %q %q\n' "$fork_remote" "$archive_ref:$recovery_ref"
        echo
        echo "Commits reachable from the archived local tip but not the PR base:"
        if git cat-file -e "${base_sha}^{commit}" 2>/dev/null; then
            git log --oneline --decorate "$base_sha..$local_sha"
        else
            echo "Unavailable because PR base commit '$base_sha' is not present locally."
        fi
        echo
        echo "Archived local branch tip (git show --stat --summary):"
        git show --stat --summary "$local_sha"
        echo
        echo "Backtracking record complete."
    } >"$infofile"

    if ! save_backtracking_record "$infofile" "$destination_name" "$archive_ref" "$verified_archive_sha"; then
        printf "The archive ref was created and verified, but its backtracking record was not saved.\n" >&2
        echo "Rerunning this command is safe." >&2
        exit 1
    fi

    printf "Archived local branch '%s' at '%s' to '%s/%s'.\n" "$branch" "$local_sha" "$fork_remote" "$archive_branch"
}

main "$@"
