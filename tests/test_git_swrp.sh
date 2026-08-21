#!/usr/bin/env bash

set -euo pipefail

repo_root=$(
    CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P
)
helper="$repo_root/bin/git_swrp.sh"
real_git=$(command -v git)

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local expected="$1"
    local file="$2"

    if ! grep -Fq -- "$expected" "$file"; then
        printf "Contents of '%s':\n" "$file" >&2
        sed 's/^/  /' "$file" >&2
        fail "expected '$expected' in '$file'."
    fi
}

assert_not_contains() {
    local unexpected="$1"
    local file="$2"

    if grep -Fq -- "$unexpected" "$file"; then
        printf "Contents of '%s':\n" "$file" >&2
        sed 's/^/  /' "$file" >&2
        fail "did not expect '$unexpected' in '$file'."
    fi
}

assert_equal() {
    local expected="$1"
    local actual="$2"
    local description="$3"

    [[ "$actual" == "$expected" ]] ||
        fail "$description is '$actual', expected '$expected'."
}

assert_path_absent() {
    local path="$1"

    if [[ -e "$path" || -L "$path" ]]; then
        fail "path unexpectedly exists: '$path'."
    fi
}

checkout_snapshot() {
    local repository="$1"
    local path

    printf 'HEAD %s\n' "$("$real_git" -C "$repository" rev-parse HEAD)"
    printf 'SYMBOLIC_HEAD %s\n' "$("$real_git" -C "$repository" symbolic-ref HEAD)"
    printf 'STATUS\n'
    "$real_git" -C "$repository" status --porcelain=v1 --untracked-files=all
    printf 'INDEX\n'
    "$real_git" -C "$repository" ls-files --stage
    printf 'WORKTREE_DIFF\n'
    "$real_git" -C "$repository" diff --no-ext-diff --no-textconv --binary
    printf 'CACHED_DIFF\n'
    "$real_git" -C "$repository" diff --cached --no-ext-diff --no-textconv --binary
    printf 'UNTRACKED_CONTENTS\n'
    while IFS= read -r path; do
        printf '%s %s\n' "$path" "$("$real_git" -C "$repository" hash-object "$path")"
    done < <("$real_git" -C "$repository" ls-files --others --exclude-standard)
}

repository_snapshot() {
    local repository="$1"

    printf 'REFS\n'
    "$real_git" -C "$repository" for-each-ref \
        --format='%(refname) %(objectname) %(symref)' refs/heads refs/remotes
    printf 'REMOTES\n'
    "$real_git" -C "$repository" config --local --get-regexp '^remote\.' || true
    printf 'WORKTREES\n'
    "$real_git" -C "$repository" worktree list --porcelain
    printf 'CHECKOUT\n'
    checkout_snapshot "$repository"
}

make_dirty_checkout() {
    local repository="$1"

    printf 'worktree-only change\n' >>"$repository/tracked.txt"
    printf 'staged content\n' >"$repository/staged.txt"
    "$real_git" -C "$repository" add staged.txt
    printf 'untracked content\n' >"$repository/untracked.txt"
}

test_root=$(mktemp -d /tmp/git_swrp_test.XXXXXX)
trap 'rm -rf -- "$test_root"' EXIT

github_root="$test_root/github"
upstream_repo="$github_root/NVIDIA/project.git"
fork_repo="$github_root/DEKHTIARJonathan/project"
seed_repo="$test_root/seed"
fake_bin="$test_root/fake-bin"
gh_log="$test_root/gh.log"
git_log="$test_root/git.log"
mkdir -p "$(dirname -- "$upstream_repo")" "$(dirname -- "$fork_repo")" "$fake_bin"

"$real_git" init --bare -q --initial-branch=main "$upstream_repo"
"$real_git" init --bare -q --initial-branch=main "$fork_repo"
"$real_git" init -q --initial-branch=main "$seed_repo"
"$real_git" -C "$seed_repo" config user.name "Switch PR Test"
"$real_git" -C "$seed_repo" config user.email switch-pr-test@example.com
"$real_git" -C "$seed_repo" config commit.gpgsign false
printf 'base\n' >"$seed_repo/tracked.txt"
"$real_git" -C "$seed_repo" add tracked.txt
"$real_git" -C "$seed_repo" commit -qm base
"$real_git" -C "$seed_repo" remote add upstream "$upstream_repo"
"$real_git" -C "$seed_repo" remote add fork "$fork_repo"
"$real_git" -C "$seed_repo" push -q upstream main
"$real_git" -C "$seed_repo" switch -qc feature/cudnn-ncll
printf 'pull request\n' >"$seed_repo/pull-request.txt"
"$real_git" -C "$seed_repo" add pull-request.txt
"$real_git" -C "$seed_repo" commit -qm "pull request head"
"$real_git" -C "$seed_repo" push -q fork feature/cudnn-ncll
pr_head_sha=$("$real_git" -C "$seed_repo" rev-parse HEAD)

cat >"$fake_bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >>"$TEST_GH_LOG"
printf '\n' >>"$TEST_GH_LOG"

[[ "${1:-}" == pr && "${2:-}" == view ]] || {
    echo "Unexpected gh invocation: $*" >&2
    exit 2
}

pr_number="${3:-}"
repo=""
previous=""
for argument in "$@"; do
    if [[ "$previous" == --repo ]]; then
        repo="$argument"
    fi
    previous="$argument"
done

[[ "$repo" == "$TEST_BASE_REPO" ]] || {
    printf "Unexpected PR repository: '%s'.\n" "$repo" >&2
    exit 2
}

case "${TEST_GH_MODE:-success}" in
success)
    printf '%s\037%s\037%s\037%s\037%s\n' \
        "$pr_number" "$TEST_HEAD_OWNER" "$TEST_HEAD_REPO" "$TEST_HEAD_BRANCH" OPEN
    ;;
lookup-failure)
    echo "simulated gh lookup failure" >&2
    exit 1
    ;;
deleted-repository)
    # Keep a branch value after adjacent empty fields. This catches parsers
    # that accidentally collapse delimiters and shift nullable gh fields.
    printf '%s\037\037\037%s\037%s\n' "$pr_number" "$TEST_HEAD_BRANCH" CLOSED
    ;;
deleted-branch)
    printf '%s\037%s\037%s\037\037%s\n' \
        "$pr_number" "$TEST_HEAD_OWNER" "$TEST_HEAD_REPO" CLOSED
    ;;
wrong-number)
    printf '%s\037%s\037%s\037%s\037%s\n' \
        9999 "$TEST_HEAD_OWNER" "$TEST_HEAD_REPO" "$TEST_HEAD_BRANCH" OPEN
    ;;
*)
    printf "Unexpected TEST_GH_MODE: '%s'.\n" "$TEST_GH_MODE" >&2
    exit 2
    ;;
esac
EOF
chmod 755 "$fake_bin/gh"

cat >"$fake_bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >>"$TEST_GIT_LOG"
printf '\n' >>"$TEST_GIT_LOG"

subcommand=""
skip_next=0
for argument in "$@"; do
    if [[ "$skip_next" -eq 1 ]]; then
        skip_next=0
        continue
    fi
    case "$argument" in
    -C | -c | --git-dir | --work-tree | --namespace)
        skip_next=1
        ;;
    --git-dir=* | --work-tree=* | --namespace=* | --bare | --no-pager)
        ;;
    -*)
        ;;
    *)
        subcommand="$argument"
        break
        ;;
    esac
done

case "$subcommand" in
fetch | ls-remote)
    exec "$TEST_REAL_GIT" \
        -c "url.file://$TEST_GITHUB_ROOT/.insteadOf=https://github.com/" \
        -c "url.file://$TEST_GITHUB_ROOT/.insteadOf=git@github.com:" \
        "$@"
    ;;
*)
    exec "$TEST_REAL_GIT" "$@"
    ;;
esac
EOF
chmod 755 "$fake_bin/git"

export TEST_REAL_GIT="$real_git"
export TEST_GITHUB_ROOT="$github_root"
export TEST_GH_LOG="$gh_log"
export TEST_GIT_LOG="$git_log"
export TEST_BASE_REPO=NVIDIA/project
export TEST_HEAD_OWNER=DEKHTIARJonathan
export TEST_HEAD_REPO=project
export TEST_HEAD_BRANCH=feature/cudnn-ncll
export TEST_GH_MODE=success
export PATH="$fake_bin:$PATH"

[[ -f "$helper" ]] || fail "git_swrp helper is missing: '$helper'."
# shellcheck source=bin/git_swrp.sh
source "$helper"
declare -F git_swrp >/dev/null || fail "sourcing '$helper' did not define git_swrp."

fixture_number=0
new_fixture() {
    local description="$1"

    fixture_number=$((fixture_number + 1))
    fixture="$test_root/${fixture_number}-${description}"
    work_repo="$fixture/project"
    destination="$fixture/pr2680"
    mkdir -p "$fixture"
    "$real_git" clone -q "$upstream_repo" "$work_repo"
    "$real_git" -C "$work_repo" remote rename origin upstream
    "$real_git" -C "$work_repo" remote set-url upstream https://github.com/NVIDIA/project.git
    "$real_git" -C "$work_repo" remote add origin https://github.com/OriginOnly/project.git
    "$real_git" -C "$work_repo" config user.name "Switch PR Test"
    "$real_git" -C "$work_repo" config user.email switch-pr-test@example.com
    "$real_git" -C "$work_repo" config commit.gpgsign false
}

run_success() {
    local output_file="$1"
    shift

    builtin cd -- "$work_repo"
    if git_swrp "$@" >"$output_file" 2>&1; then
        run_pwd=$PWD
    else
        run_pwd=$PWD
        printf "git_swrp output from '%s':\n" "$output_file" >&2
        sed 's/^/  /' "$output_file" >&2
        fail "git_swrp unexpectedly failed: $*"
    fi
    builtin cd -- "$test_root"
}

run_failure() {
    local output_file="$1"
    shift

    builtin cd -- "$work_repo"
    if git_swrp "$@" >"$output_file" 2>&1; then
        run_pwd=$PWD
        builtin cd -- "$test_root"
        printf "git_swrp output from '%s':\n" "$output_file" >&2
        sed 's/^/  /' "$output_file" >&2
        fail "git_swrp unexpectedly succeeded: $*"
    fi
    run_pwd=$PWD
    builtin cd -- "$test_root"
}

local_branch="DEKHTIARJonathan→feature/cudnn-ncll"

# The interface is intentionally PR-only and rejects zero, nonnumbers, and the
# remote/branch spellings supported by the older checkout-only helper.
new_fixture usage
usage_output="$fixture/usage.output"
run_failure "$usage_output"
assert_contains "Usage: git_swrp" "$usage_output"
assert_equal "$work_repo" "$run_pwd" "working directory after usage failure"
for invalid_argument in 0 abc DEKHTIARJonathan:feature/cudnn-ncll DEKHTIARJonathan/feature/cudnn-ncll; do
    invalid_output="$fixture/invalid-${invalid_argument//\//_}.output"
    run_failure "$invalid_output" "$invalid_argument"
    assert_contains "positive PR number" "$invalid_output"
    assert_equal "$work_repo" "$run_pwd" "working directory after invalid argument"
done
help_output="$fixture/help.output"
run_success "$help_output" --help
assert_contains "Usage: git_swrp" "$help_output"
assert_equal "$work_repo" "$run_pwd" "working directory after help"

# PR lookup is intentionally anchored to upstream. An origin-only checkout is
# rejected instead of treating the user's fork as the pull-request base.
new_fixture origin-only
"$real_git" -C "$work_repo" remote remove upstream
origin_only_before=$(repository_snapshot "$work_repo")
: >"$gh_log"
origin_only_output="$fixture/origin-only.output"
run_failure "$origin_only_output" 2680
assert_contains "remote 'upstream' is required" "$origin_only_output"
assert_equal "" "$(sed -n '1p' "$gh_log")" "gh log after origin-only rejection"
assert_equal "$origin_only_before" "$(repository_snapshot "$work_repo")" \
    "repository after origin-only rejection"

# A successful gh command must still identify the exact requested PR number.
new_fixture wrong-pr-number
wrong_number_before=$(repository_snapshot "$work_repo")
export TEST_GH_MODE=wrong-number
wrong_number_output="$fixture/wrong-number.output"
run_failure "$wrong_number_output" 2680
assert_contains "returned number '9999' instead of '2680'" "$wrong_number_output"
assert_equal "$wrong_number_before" "$(repository_snapshot "$work_repo")" \
    "repository after mismatched PR lookup"
export TEST_GH_MODE=success

# A new remote and worktree are created from upstream PR metadata. The caller's
# dirty checkout is untouched, origin is never consulted, and cd persists in
# the shell that sourced the helper.
new_fixture new-remote
make_dirty_checkout "$work_repo"
caller_before=$(checkout_snapshot "$work_repo")
: >"$gh_log"
new_output="$fixture/new.output"
run_success "$new_output" 2680
assert_equal "$destination" "$run_pwd" "working directory after creating worktree"
assert_contains "--repo NVIDIA/project" "$gh_log"
assert_not_contains "OriginOnly/project" "$gh_log"
assert_equal "$caller_before" "$(checkout_snapshot "$work_repo")" \
    "dirty caller checkout after creating worktree"
assert_equal "https://github.com/DEKHTIARJonathan/project" \
    "$("$real_git" -C "$work_repo" config --get remote.DEKHTIARJonathan.url)" \
    "new remote URL"
assert_equal "$local_branch" "$("$real_git" -C "$destination" branch --show-current)" \
    "created worktree branch"
assert_equal "DEKHTIARJonathan/feature/cudnn-ncll" \
    "$("$real_git" -C "$destination" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}')" \
    "created branch upstream"
assert_equal "$pr_head_sha" "$("$real_git" -C "$destination" rev-parse HEAD)" \
    "created worktree HEAD"
assert_equal "$pr_head_sha" \
    "$("$real_git" -C "$work_repo" rev-parse refs/remotes/DEKHTIARJonathan/feature/cudnn-ncll)" \
    "fetched PR head"

# An already configured remote with the expected GitHub repository is reused.
new_fixture existing-remote
"$real_git" -C "$work_repo" remote add DEKHTIARJonathan \
    git@github.com:DEKHTIARJonathan/project
existing_output="$fixture/existing.output"
run_success "$existing_output" 2680
assert_equal "$destination" "$run_pwd" "working directory with existing remote"
assert_equal "$local_branch" "$("$real_git" -C "$destination" branch --show-current)" \
    "branch created from existing remote"

# Exact reuse fetches the new live remote tip but never moves or cleans the
# existing local branch. Test a locally-ahead, dirty checkout against a remote
# that advances independently.
printf 'local-only commit\n' >"$destination/local-only.txt"
"$real_git" -C "$destination" add local-only.txt
"$real_git" -C "$destination" commit -qm "local-only commit"
printf 'dirty after local commit\n' >>"$destination/tracked.txt"
printf 'staged dirty state\n' >"$destination/staged-dirty.txt"
"$real_git" -C "$destination" add staged-dirty.txt
printf 'untracked dirty state\n' >"$destination/untracked-dirty.txt"
existing_before=$(checkout_snapshot "$destination")
"$real_git" -C "$seed_repo" commit --allow-empty -qm "advanced live PR head"
"$real_git" -C "$seed_repo" push -q fork feature/cudnn-ncll
pr_head_sha=$("$real_git" -C "$seed_repo" rev-parse HEAD)
reuse_output="$fixture/reuse.output"
run_success "$reuse_output" 2680
assert_equal "$destination" "$run_pwd" "working directory after idempotent reuse"
assert_equal "$existing_before" "$(checkout_snapshot "$destination")" \
    "existing worktree state after idempotent reuse"
assert_equal "$pr_head_sha" \
    "$("$real_git" -C "$work_repo" rev-parse refs/remotes/DEKHTIARJonathan/feature/cudnn-ncll)" \
    "refreshed remote-tracking ref during reuse"
assert_contains "local branch '$local_branch' was not moved" "$reuse_output"

# PR authors commonly force-push. A non-fast-forward remote update refreshes
# only the disposable remote-tracking ref; the local review branch stays put.
"$real_git" -C "$seed_repo" reset -q --hard HEAD~1
"$real_git" -C "$seed_repo" commit --allow-empty -qm "replacement PR head"
"$real_git" -C "$seed_repo" push -q --force fork feature/cudnn-ncll
pr_head_sha=$("$real_git" -C "$seed_repo" rev-parse HEAD)
force_push_before=$(checkout_snapshot "$destination")
force_push_output="$fixture/force-push.output"
run_success "$force_push_output" 2680
assert_equal "$destination" "$run_pwd" "working directory after force-pushed reuse"
assert_equal "$force_push_before" "$(checkout_snapshot "$destination")" \
    "existing worktree state after force-pushed reuse"
assert_equal "$pr_head_sha" \
    "$("$real_git" -C "$work_repo" rev-parse refs/remotes/DEKHTIARJonathan/feature/cudnn-ncll)" \
    "force-updated remote-tracking ref during reuse"

# A failed gh lookup and nullable metadata leave all repository state alone.
new_fixture lookup-failure
failure_before=$(repository_snapshot "$work_repo")
export TEST_GH_MODE=lookup-failure
lookup_output="$fixture/lookup.output"
run_failure "$lookup_output" 2680
assert_contains "could not look up PR #2680" "$lookup_output"
assert_equal "$failure_before" "$(repository_snapshot "$work_repo")" \
    "repository after gh lookup failure"

export TEST_GH_MODE=deleted-repository
deleted_repo_output="$fixture/deleted-repo.output"
run_failure "$deleted_repo_output" 2680
assert_contains "head repository" "$deleted_repo_output"
assert_equal "$failure_before" "$(repository_snapshot "$work_repo")" \
    "repository after deleted head repository"

export TEST_GH_MODE=deleted-branch
deleted_branch_output="$fixture/deleted-branch.output"
run_failure "$deleted_branch_output" 2680
assert_contains "head branch" "$deleted_branch_output"
assert_equal "$failure_before" "$(repository_snapshot "$work_repo")" \
    "repository after deleted head branch"
export TEST_GH_MODE=success

# A live-looking PR whose branch is absent from the head repository is rejected
# without adding its remote, refs, worktree registration, or destination.
new_fixture missing-head
export TEST_HEAD_BRANCH=feature/missing
missing_before=$(repository_snapshot "$work_repo")
missing_output="$fixture/missing.output"
run_failure "$missing_output" 2680
assert_contains "feature/missing" "$missing_output"
assert_equal "$missing_before" "$(repository_snapshot "$work_repo")" \
    "repository after missing PR head branch"
assert_path_absent "$destination"
export TEST_HEAD_BRANCH=feature/cudnn-ncll

# A same-named remote for another GitHub repository is never rewritten or
# fetched, and all other local state remains unchanged.
new_fixture wrong-remote
"$real_git" -C "$work_repo" remote add DEKHTIARJonathan \
    https://github.com/SomebodyElse/project.git
wrong_remote_before=$(repository_snapshot "$work_repo")
wrong_remote_output="$fixture/wrong-remote.output"
run_failure "$wrong_remote_output" 2680
assert_contains "remote 'DEKHTIARJonathan' points to" "$wrong_remote_output"
assert_contains "DEKHTIARJonathan/project" "$wrong_remote_output"
assert_equal "$wrong_remote_before" "$(repository_snapshot "$work_repo")" \
    "repository after mismatched remote rejection"

# Unregistered destinations include directories and broken symbolic links.
new_fixture destination-directory
mkdir "$destination"
printf 'do not overwrite\n' >"$destination/marker"
destination_before=$(repository_snapshot "$work_repo")
destination_output="$fixture/destination.output"
run_failure "$destination_output" 2680
assert_contains "destination '$destination' already exists" "$destination_output"
assert_equal "do not overwrite" "$(sed -n '1p' "$destination/marker")" \
    "destination collision marker"
assert_equal "$destination_before" "$(repository_snapshot "$work_repo")" \
    "repository after destination directory collision"

new_fixture broken-symlink
ln -s nowhere "$destination"
symlink_before=$(repository_snapshot "$work_repo")
symlink_output="$fixture/symlink.output"
run_failure "$symlink_output" 2680
assert_contains "destination '$destination' already exists" "$symlink_output"
[[ -L "$destination" ]] || fail "broken destination symlink was changed."
assert_equal "$symlink_before" "$(repository_snapshot "$work_repo")" \
    "repository after destination symlink collision"

# A local branch is not silently reused, whether it is currently unchecked or
# checked out in another linked worktree.
new_fixture existing-branch
"$real_git" -C "$work_repo" branch "$local_branch" main
branch_before=$(repository_snapshot "$work_repo")
branch_output="$fixture/branch.output"
run_failure "$branch_output" 2680
assert_contains "local branch '$local_branch' already exists" "$branch_output"
assert_equal "$branch_before" "$(repository_snapshot "$work_repo")" \
    "repository after existing local branch collision"

new_fixture branch-elsewhere
elsewhere="$fixture/elsewhere"
"$real_git" -C "$work_repo" worktree add -q -b "$local_branch" "$elsewhere" main
elsewhere_before=$(repository_snapshot "$work_repo")
elsewhere_output="$fixture/elsewhere.output"
run_failure "$elsewhere_output" 2680
assert_contains "already checked out in worktree '$elsewhere'" "$elsewhere_output"
assert_equal "$elsewhere_before" "$(repository_snapshot "$work_repo")" \
    "repository after checked-out branch collision"
assert_path_absent "$destination"

# A registration at the expected destination is reusable only for the exact
# owner-arrow-branch name. A different branch gets a specific preflight error.
new_fixture wrong-registered-branch
"$real_git" -C "$work_repo" worktree add -q -b unrelated "$destination" main
registered_before=$(repository_snapshot "$work_repo")
registered_output="$fixture/registered.output"
run_failure "$registered_output" 2680
assert_contains "destination '$destination' is already registered" "$registered_output"
assert_contains "refs/heads/unrelated" "$registered_output"
assert_equal "$registered_before" "$(repository_snapshot "$work_repo")" \
    "repository after wrong registered branch collision"

# A missing directory with a still-live registration is reported as stale and
# is not pruned or repaired automatically.
new_fixture stale-registration
"$real_git" -C "$work_repo" worktree add -q -b "$local_branch" "$destination" main
mv "$destination" "$fixture/moved-worktree"
stale_before=$(repository_snapshot "$work_repo")
stale_output="$fixture/stale.output"
run_failure "$stale_output" 2680
assert_contains "missing, stale" "$stale_output"
assert_equal "$stale_before" "$(repository_snapshot "$work_repo")" \
    "repository after stale worktree rejection"
assert_path_absent "$destination"

printf 'All git_swrp tests passed.\n'
