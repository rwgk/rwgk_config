#!/usr/bin/env bash

set -euo pipefail

repo_root=$(
    CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P
)
update_command="$repo_root/bin/git_update_branch.sh"
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

assert_equal() {
    local expected="$1"
    local actual="$2"
    local description="$3"

    [[ "$actual" == "$expected" ]] ||
        fail "$description is '$actual', expected '$expected'."
}

test_root=$(mktemp -d /tmp/git_update_branch_test.XXXXXX)
trap 'rm -rf -- "$test_root"' EXIT

fixture_number=0
new_fixture() {
    local description="$1"
    local default_branch="${2:-main}"

    fixture_number=$((fixture_number + 1))
    fixture="$test_root/${fixture_number}-${description}"
    remote_repo="$fixture/upstream.git"
    seed_repo="$fixture/seed"
    work_repo="$fixture/work"
    mkdir -p "$fixture"

    git init --bare -q --initial-branch="$default_branch" "$remote_repo"
    git init -q --initial-branch="$default_branch" "$seed_repo"
    git -C "$seed_repo" config user.name "Pull Branch Test"
    git -C "$seed_repo" config user.email pull-branch-test@example.com
    git -C "$seed_repo" config commit.gpgsign false
    printf 'base\n' >"$seed_repo/tracked.txt"
    git -C "$seed_repo" add tracked.txt
    git -C "$seed_repo" commit -qm base
    git -C "$seed_repo" remote add origin "$remote_repo"
    git -C "$seed_repo" push -qu origin "$default_branch"

    git clone -q "$remote_repo" "$work_repo"
    git -C "$work_repo" remote rename origin upstream
    git -C "$work_repo" config user.name "Pull Branch Test"
    git -C "$work_repo" config user.email pull-branch-test@example.com
    git -C "$work_repo" config commit.gpgsign false
}

advance_remote_branch() {
    local branch="$1"
    local start_point="${2:-main}"

    if git -C "$seed_repo" show-ref --verify --quiet "refs/heads/$branch"; then
        git -C "$seed_repo" switch -q "$branch"
    else
        git -C "$seed_repo" switch -qc "$branch" "$start_point"
    fi
    git -C "$seed_repo" commit --allow-empty -qm "advance $branch"
    git -C "$seed_repo" push -q -u origin "$branch"
    advanced_sha=$(git -C "$seed_repo" rev-parse HEAD)
}

run_success() {
    local output_file="$1"
    shift

    if ! (
        cd "$work_repo"
        "$update_command" "$@"
    ) >"$output_file" 2>&1; then
        printf "Command output from '%s':\n" "$output_file" >&2
        sed 's/^/  /' "$output_file" >&2
        fail "command unexpectedly failed: $update_command $*"
    fi
}

run_failure() {
    local output_file="$1"
    shift

    if (
        cd "$work_repo"
        "$update_command" "$@"
    ) >"$output_file" 2>&1; then
        printf "Command output from '%s':\n" "$output_file" >&2
        sed 's/^/  /' "$output_file" >&2
        fail "command unexpectedly succeeded: $update_command $*"
    fi
}

checkout_snapshot() {
    local repository="$1"
    local symbolic_head path

    printf 'HEAD %s\n' "$(git -C "$repository" rev-parse HEAD)"
    if symbolic_head=$(git -C "$repository" symbolic-ref --quiet HEAD); then
        printf 'SYMBOLIC_HEAD %s\n' "$symbolic_head"
    else
        printf 'SYMBOLIC_HEAD DETACHED\n'
    fi
    printf 'STATUS\n'
    git -C "$repository" status --porcelain=v1 --untracked-files=all
    printf 'INDEX\n'
    git -C "$repository" ls-files --stage
    printf 'WORKTREE_DIFF\n'
    git -C "$repository" diff --no-ext-diff --no-textconv --binary
    printf 'CACHED_DIFF\n'
    git -C "$repository" diff --cached --no-ext-diff --no-textconv --binary
    printf 'UNTRACKED_CONTENTS\n'
    while IFS= read -r path; do
        printf '%s %s\n' "$path" "$(git -C "$repository" hash-object "$path")"
    done < <(git -C "$repository" ls-files --others --exclude-standard)
}

ref_snapshot() {
    local repository="$1"

    git -C "$repository" for-each-ref \
        --format='%(refname) %(objectname)' refs/heads refs/remotes
}

make_dirty_checkout() {
    local repository="$1"

    printf 'worktree-only change\n' >>"$repository/tracked.txt"
    printf 'staged content\n' >"$repository/staged.txt"
    git -C "$repository" add staged.txt
    printf 'untracked content\n' >"$repository/untracked.txt"
}

[[ -x "$update_command" ]] || fail "update command is not executable: $update_command"

# An explicit noncurrent local branch may track a differently named remote
# branch. Both names may contain slashes. Fetch must advance the target and its
# remote-tracking ref without changing the current checkout.
new_fixture explicit-mapping
advance_remote_branch feature/remote
git -C "$work_repo" fetch -q upstream feature/remote
git -C "$work_repo" switch -qc topic/local --track upstream/feature/remote
git -C "$work_repo" switch -q main
advance_remote_branch feature/remote
explicit_checkout_before=$(checkout_snapshot "$work_repo")
explicit_output="$fixture/explicit.output"
run_success "$explicit_output" topic/local
assert_contains "+ git fetch upstream feature/remote:topic/local" "$explicit_output"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/heads/topic/local)" \
    "explicitly fetched local branch tip"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/remotes/upstream/feature/remote)" \
    "explicitly fetched remote-tracking tip"
assert_equal "$explicit_checkout_before" "$(checkout_snapshot "$work_repo")" \
    "current checkout after explicit noncurrent fetch"

# A target checked out in another linked worktree must be rejected before any
# local or remote-tracking ref, checkout, index, or status changes.
linked_worktree="$fixture/linked-worktree"
git -C "$work_repo" worktree add -q "$linked_worktree" topic/local
advance_remote_branch feature/remote
linked_refs_before=$(ref_snapshot "$work_repo")
primary_checkout_before=$(checkout_snapshot "$work_repo")
linked_checkout_before=$(checkout_snapshot "$linked_worktree")
linked_output="$fixture/linked-worktree.output"
run_failure "$linked_output" topic/local
assert_equal "$linked_refs_before" "$(ref_snapshot "$work_repo")" \
    "refs after rejecting a target checked out in another worktree"
assert_equal "$primary_checkout_before" "$(checkout_snapshot "$work_repo")" \
    "primary checkout after linked-worktree rejection"
assert_equal "$linked_checkout_before" "$(checkout_snapshot "$linked_worktree")" \
    "linked checkout after linked-worktree rejection"

# A noncurrent target with local and remote-only commits must not be merged or
# rewritten behind the user's back. Fetch may refresh the remote-tracking ref,
# but it must reject the non-fast-forward local-branch update.
new_fixture divergent-target
advance_remote_branch topic/diverged
git -C "$work_repo" fetch -q upstream topic/diverged
git -C "$work_repo" switch -qc topic/diverged --track upstream/topic/diverged
git -C "$work_repo" commit --allow-empty -qm "local-only topic commit"
divergent_target_before=$(git -C "$work_repo" rev-parse refs/heads/topic/diverged)
git -C "$work_repo" switch -q main
advance_remote_branch topic/diverged
divergent_checkout_before=$(checkout_snapshot "$work_repo")
divergent_output="$fixture/divergent.output"
run_failure "$divergent_output" topic/diverged
assert_contains "+ git fetch upstream topic/diverged:topic/diverged" "$divergent_output"
assert_equal "$divergent_target_before" "$(git -C "$work_repo" rev-parse refs/heads/topic/diverged)" \
    "divergent local branch after rejected fetch"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/remotes/upstream/topic/diverged)" \
    "divergent remote-tracking branch after rejected fetch"
assert_equal "$divergent_checkout_before" "$(checkout_snapshot "$work_repo")" \
    "current checkout after rejected divergent fetch"

# A selected branch that is currently checked out is updated with pull.
new_fixture current-branch-pull
advance_remote_branch main
current_output="$fixture/current.output"
run_success "$current_output" main
assert_contains "+ git pull upstream main:main" "$current_output"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse HEAD)" \
    "current branch tip after pull"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/remotes/upstream/main)" \
    "current branch remote-tracking tip after pull"

# Primary no-argument use: from a dirty local-only feature, cached remote HEAD
# discovery selects main and fetches it without touching HEAD, worktree, index,
# or status in the current checkout.
new_fixture cached-default-from-feature
git -C "$work_repo" switch -qc feature/local-only
make_dirty_checkout "$work_repo"
advance_remote_branch main
feature_checkout_before=$(checkout_snapshot "$work_repo")
cached_output="$fixture/cached-default.output"
run_success "$cached_output"
assert_contains "+ git fetch upstream main:main" "$cached_output"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/heads/main)" \
    "cached-default local branch tip"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/remotes/upstream/main)" \
    "cached-default remote-tracking tip"
assert_equal "$feature_checkout_before" "$(checkout_snapshot "$work_repo")" \
    "dirty feature checkout after cached-default fetch"

# If no refs/remotes/*/HEAD symref is cached, query the current branch's
# tracking remote with ls-remote --symref and then perform the same pull.
new_fixture ls-remote-fallback
git -C "$work_repo" symbolic-ref --delete refs/remotes/upstream/HEAD
advance_remote_branch main
fake_bin="$fixture/fake-bin"
git_log="$fixture/git.log"
mkdir "$fake_bin"
cat >"$fake_bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >>"$TEST_GIT_LOG"
printf '\n' >>"$TEST_GIT_LOG"
exec "$TEST_REAL_GIT" "$@"
EOF
chmod 755 "$fake_bin/git"
fallback_output="$fixture/fallback.output"
if ! (
    cd "$work_repo"
    PATH="$fake_bin:$PATH" TEST_GIT_LOG="$git_log" TEST_REAL_GIT="$real_git" \
        "$update_command"
) >"$fallback_output" 2>&1; then
    sed 's/^/  /' "$fallback_output" >&2
    fail "ls-remote fallback unexpectedly failed."
fi
assert_contains "ls-remote --symref upstream HEAD" "$git_log"
assert_contains "+ git pull upstream main:main" "$fallback_output"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse HEAD)" \
    "ls-remote fallback branch tip"

# With no cached remote HEAD and no tracking remote on the current branch, a
# sole configured remote is used for default discovery, then its noncurrent
# default branch is fetched without disturbing the current checkout.
new_fixture sole-remote-fallback
git -C "$work_repo" symbolic-ref --delete refs/remotes/upstream/HEAD
git -C "$work_repo" switch -qc local-only
make_dirty_checkout "$work_repo"
advance_remote_branch main
sole_checkout_before=$(checkout_snapshot "$work_repo")
fake_bin="$fixture/fake-bin"
git_log="$fixture/git.log"
mkdir "$fake_bin"
cat >"$fake_bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >>"$TEST_GIT_LOG"
printf '\n' >>"$TEST_GIT_LOG"
exec "$TEST_REAL_GIT" "$@"
EOF
chmod 755 "$fake_bin/git"
sole_remote_output="$fixture/sole-remote.output"
if ! (
    cd "$work_repo"
    PATH="$fake_bin:$PATH" TEST_GIT_LOG="$git_log" TEST_REAL_GIT="$real_git" \
        "$update_command"
) >"$sole_remote_output" 2>&1; then
    sed 's/^/  /' "$sole_remote_output" >&2
    fail "sole-remote fallback unexpectedly failed."
fi
assert_contains "ls-remote --symref upstream HEAD" "$git_log"
assert_contains "+ git fetch upstream main:main" "$sole_remote_output"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/heads/main)" \
    "sole-remote fallback local branch tip"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/remotes/upstream/main)" \
    "sole-remote fallback remote-tracking tip"
assert_equal "$sole_checkout_before" "$(checkout_snapshot "$work_repo")" \
    "dirty feature checkout after sole-remote fallback"

# Distinct cached remote defaults are ambiguous, even when each one maps to a
# configured local branch.
new_fixture ambiguous-default
main_sha=$(git -C "$work_repo" rev-parse refs/heads/main)
git -C "$work_repo" remote add origin "$remote_repo"
git -C "$work_repo" update-ref refs/remotes/origin/develop "$main_sha"
git -C "$work_repo" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/develop
git -C "$work_repo" branch develop "$main_sha"
git -C "$work_repo" config branch.develop.remote origin
git -C "$work_repo" config branch.develop.merge refs/heads/develop
ambiguous_refs_before=$(ref_snapshot "$work_repo")
ambiguous_output="$fixture/ambiguous.output"
run_failure "$ambiguous_output"
assert_equal "$ambiguous_refs_before" "$(ref_snapshot "$work_repo")" \
    "refs after ambiguous default rejection"

# With no cached remote default, no tracking remote on the current branch, and
# multiple configured remotes, default discovery must fail rather than guess.
new_fixture no-default-remote
git -C "$work_repo" symbolic-ref --delete refs/remotes/upstream/HEAD
git -C "$work_repo" remote add origin "$remote_repo"
git -C "$work_repo" switch -qc local-only
no_default_refs_before=$(ref_snapshot "$work_repo")
no_default_output="$fixture/no-default.output"
run_failure "$no_default_output"
assert_equal "$no_default_refs_before" "$(ref_snapshot "$work_repo")" \
    "refs after unresolved default rejection"

# A checked-out local branch without upstream configuration cannot be pulled,
# and a nonexistent branch must not be accepted.
new_fixture validation
git -C "$work_repo" switch -qc local-only
no_upstream_output="$fixture/no-upstream.output"
run_failure "$no_upstream_output" local-only
missing_output="$fixture/missing.output"
run_failure "$missing_output" missing/branch
invalid_output="$fixture/invalid.output"
run_failure "$invalid_output" invalid..branch
extra_args_output="$fixture/extra-args.output"
run_failure "$extra_args_output" main extra

# A detached checkout may still update a cached default branch via fetch. Its
# detached HEAD, dirty worktree, index, and status must remain untouched.
new_fixture detached-default
git -C "$work_repo" switch -q --detach
make_dirty_checkout "$work_repo"
advance_remote_branch main
detached_checkout_before=$(checkout_snapshot "$work_repo")
detached_output="$fixture/detached.output"
run_success "$detached_output"
assert_contains "+ git fetch upstream main:main" "$detached_output"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/heads/main)" \
    "detached-default local branch tip"
assert_equal "$advanced_sha" "$(git -C "$work_repo" rev-parse refs/remotes/upstream/main)" \
    "detached-default remote-tracking tip"
assert_equal "$detached_checkout_before" "$(checkout_snapshot "$work_repo")" \
    "detached checkout after default fetch"

help_output="$test_root/help.output"
if ! "$update_command" --help >"$help_output" 2>&1; then
    sed 's/^/  /' "$help_output" >&2
    fail "--help unexpectedly failed."
fi
assert_contains "Usage:" "$help_output"

echo "PASS: git_update_branch.sh"
