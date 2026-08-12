#!/bin/bash
set -euo pipefail

repo_root=$(
    CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P
)
delete_command="$repo_root/bin/git_branch_D_track_hash"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_file_contains() {
    local expected="$1"
    local file="$2"

    if ! grep -Fq -- "$expected" "$file"; then
        printf "Contents of '%s':\n" "$file" >&2
        sed 's/^/  /' "$file" >&2
        fail "expected '$expected' in '$file'."
    fi
}

assert_ref_sha() {
    local repository="$1"
    local ref="$2"
    local expected_sha="$3"
    local actual_sha

    if ! actual_sha=$(git --git-dir="$repository" rev-parse --verify "$ref" 2>/dev/null); then
        fail "expected ref '$ref' in '$repository'."
    fi
    [[ "$actual_sha" == "$expected_sha" ]] ||
        fail "ref '$ref' is '$actual_sha', expected '$expected_sha'."
}

assert_ref_absent() {
    local repository="$1"
    local ref="$2"

    if git --git-dir="$repository" show-ref --verify --quiet "$ref"; then
        fail "ref '$ref' unexpectedly exists in '$repository'."
    fi
}

assert_local_ref_sha() {
    local ref="$1"
    local expected_sha="$2"
    local actual_sha

    if ! actual_sha=$(git -C "$work_repo" rev-parse --verify "$ref" 2>/dev/null); then
        fail "expected local ref '$ref'."
    fi
    [[ "$actual_sha" == "$expected_sha" ]] ||
        fail "local ref '$ref' is '$actual_sha', expected '$expected_sha'."
}

assert_local_ref_absent() {
    local ref="$1"

    if git -C "$work_repo" show-ref --verify --quiet "$ref"; then
        fail "local ref '$ref' unexpectedly exists."
    fi
}

create_remote_branch() {
    local branch="$1"
    local remote="$2"

    git -C "$work_repo" branch "$branch" "$base_sha"
    git -C "$work_repo" push -q "$remote" "$branch:refs/heads/$branch"
    git -C "$work_repo" fetch -q "$remote" "$branch"
    git -C "$work_repo" branch --set-upstream-to="$remote/$branch" "$branch" >/dev/null
}

run_expect_failure() {
    local stdout_file="$1"
    local stderr_file="$2"
    shift 2

    if (
        cd "$work_repo"
        "$delete_command" "$@"
    ) >"$stdout_file" 2>"$stderr_file"; then
        fail "command unexpectedly succeeded: $*"
    fi
}

test_root=$(mktemp -d /tmp/git_branch_D_track_hash_test.XXXXXX)
trap 'rm -rf -- "$test_root"' EXIT

work_repo="$test_root/work"
upstream_repo="$test_root/upstream.git"
org_fork_repo="$test_root/org-fork.git"
personal_fork_repo="$test_root/personal-fork.git"
colleague_fork_repo="$test_root/colleague-fork.git"
unrelated_fork_repo="$test_root/unrelated-fork.git"
fake_bin="$test_root/fake-bin"
backtracking_dir="$test_root/backtracking"
gh_log="$test_root/gh.log"
mkdir -p "$fake_bin" "$backtracking_dir"

git -c init.defaultBranch=main init -q "$work_repo"
git -C "$work_repo" config user.name "Branch Delete Test"
git -C "$work_repo" config user.email branch-delete-test@example.com
git -C "$work_repo" config commit.gpgsign false
printf 'base\n' >"$work_repo/content.txt"
git -C "$work_repo" add content.txt
git -C "$work_repo" commit -m base >/dev/null
base_sha=$(git -C "$work_repo" rev-parse HEAD)

for repository in \
    "$upstream_repo" \
    "$org_fork_repo" \
    "$personal_fork_repo" \
    "$colleague_fork_repo" \
    "$unrelated_fork_repo"; do
    git -c init.defaultBranch=main init --bare -q "$repository"
done

git -C "$work_repo" remote add upstream "$upstream_repo"
git -C "$work_repo" remote add upstream-alias "$upstream_repo"
git -C "$work_repo" remote add org-fork "$org_fork_repo"
git -C "$work_repo" remote add personal-fork "$personal_fork_repo"
git -C "$work_repo" remote add colleague-fork "$colleague_fork_repo"
git -C "$work_repo" remote add unrelated-fork "$unrelated_fork_repo"
git -C "$work_repo" push -q upstream main

export TEST_UPSTREAM_REPO="$upstream_repo"
export TEST_ORG_FORK_REPO="$org_fork_repo"
export TEST_PERSONAL_FORK_REPO="$personal_fork_repo"
export TEST_COLLEAGUE_FORK_REPO="$colleague_fork_repo"
export TEST_UNRELATED_FORK_REPO="$unrelated_fork_repo"
export TEST_GH_LOG="$gh_log"

cat >"$fake_bin/gh" <<'EOF'
#!/bin/bash
set -euo pipefail

printf '%q ' "$@" >>"$TEST_GH_LOG"
printf '\n' >>"$TEST_GH_LOG"

if [[ "$1" == repo && "$2" == view ]]; then
    case "$3" in
    "$TEST_UPSTREAM_REPO")
        printf 'https://github.com/NVIDIA-dev/project\tNVIDIA-dev/project\n'
        ;;
    "$TEST_ORG_FORK_REPO")
        printf 'https://github.com/NVIDIA-dev/project-rwgk\tNVIDIA-dev/project-rwgk\n'
        ;;
    "$TEST_PERSONAL_FORK_REPO")
        printf 'https://github.com/tester/project\ttester/project\n'
        ;;
    "$TEST_COLLEAGUE_FORK_REPO")
        printf 'https://github.com/colleague/project\tcolleague/project\n'
        ;;
    "$TEST_UNRELATED_FORK_REPO")
        printf 'https://github.com/tester/unrelated\ttester/unrelated\n'
        ;;
    *)
        printf "Unexpected repository URL: '%s'.\n" "$3" >&2
        exit 1
        ;;
    esac
    exit
fi

[[ "$1" == api ]] || {
    echo "Unexpected gh invocation: $*" >&2
    exit 1
}
shift

endpoint=""
state=""
head=""
for arg in "$@"; do
    case "$arg" in
    user | repos/*)
        if [[ -z "$endpoint" ]]; then
            endpoint="$arg"
        fi
        ;;
    state=*)
        state="${arg#state=}"
        ;;
    head=*)
        head="${arg#head=}"
        ;;
    esac
done

case "$endpoint" in
user)
    echo tester
    ;;
repos/NVIDIA-dev/project)
    printf 'false\tNVIDIA-dev\tNVIDIA-dev/project\tfalse\n'
    ;;
repos/NVIDIA-dev/project-rwgk)
    printf 'true\tNVIDIA-dev\tNVIDIA-dev/project\ttrue\n'
    ;;
repos/tester/project)
    printf 'true\ttester\tNVIDIA-dev/project\ttrue\n'
    ;;
repos/colleague/project)
    printf 'true\tcolleague\tNVIDIA-dev/project\tfalse\n'
    ;;
repos/tester/unrelated)
    printf 'true\ttester\tother/base\ttrue\n'
    ;;
repos/NVIDIA-dev/project/pulls)
    branch="${head#*:}"
    case "${TEST_PR_MODE:-none}" in
    none)
        ;;
    exact-open | other-open)
        if [[ "$state" == open || "$state" == all ]]; then
            printf '%s\t%s\t510\topen\thttps://github.com/NVIDIA-dev/project/pull/510\tfalse\tfalse\n' \
                "$TEST_PR_HEAD_REPO" "$branch"
        fi
        ;;
    exact-merged)
        if [[ "$*" == *'(.head.sha // "NONE")'* ]]; then
            printf '%s\t%s\t%s\t510\thttps://github.com/NVIDIA-dev/project/pull/510\t2026-08-10T06:19:35Z\n' \
                "$TEST_PR_HEAD_REPO" "$branch" "$TEST_PR_HEAD_SHA"
        elif [[ "$state" == all ]]; then
            printf '%s\t%s\t510\tclosed\thttps://github.com/NVIDIA-dev/project/pull/510\tfalse\ttrue\n' \
                "$TEST_PR_HEAD_REPO" "$branch"
        fi
        ;;
    *)
        printf "Unexpected TEST_PR_MODE: '%s'.\n" "$TEST_PR_MODE" >&2
        exit 1
        ;;
    esac
    ;;
*)
    echo "Unexpected gh api endpoint: '$endpoint' ($*)" >&2
    exit 1
    ;;
esac
EOF
chmod 755 "$fake_bin/gh"

cat >"$fake_bin/mlt" <<'EOF'
#!/bin/bash
set -euo pipefail
echo 2026-08-11+120000
EOF
chmod 755 "$fake_bin/mlt"

cat >"$fake_bin/hostfqdn" <<'EOF'
#!/bin/bash
set -euo pipefail
echo testhost.example.com
EOF
chmod 755 "$fake_bin/hostfqdn"

export MY_GIT_BACKTRACKING_INFO_LOCAL="$backtracking_dir"
unset MY_GIT_BACKTRACKING_INFO_REMOTE
export PATH="$fake_bin:$PATH"
export TEST_PR_MODE=none
export TEST_PR_HEAD_REPO=NONE
export TEST_PR_HEAD_SHA=NONE

personal_branch=personal-clean
create_remote_branch "$personal_branch" personal-fork
(
    cd "$work_repo"
    "$delete_command" --archive-to-remote=personal-fork "$personal_branch"
) >"$test_root/personal.stdout" 2>"$test_root/personal.stderr"
assert_ref_sha "$personal_fork_repo" "refs/heads/archive/2026-08-11+120000_$personal_branch" "$base_sha"
assert_ref_absent "$personal_fork_repo" "refs/heads/$personal_branch"
assert_local_ref_absent "refs/heads/$personal_branch"

org_branch=org-clean
create_remote_branch "$org_branch" org-fork
export TEST_PR_MODE=other-open
export TEST_PR_HEAD_REPO=NVIDIA-dev/project
export TEST_PR_HEAD_SHA="$base_sha"
run_expect_failure \
    "$test_root/org-preflight.stdout" \
    "$test_root/org-preflight.stderr" \
    "$org_branch"
assert_file_contains "organization-owned GitHub fork 'NVIDIA-dev/project-rwgk', administered by your authenticated user" "$test_root/org-preflight.stderr"
assert_ref_sha "$org_fork_repo" "refs/heads/$org_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$org_branch" "$base_sha"
(
    cd "$work_repo"
    "$delete_command" --archive-to-remote=org-fork "$org_branch"
) >"$test_root/org.stdout" 2>"$test_root/org.stderr"
assert_ref_sha "$org_fork_repo" "refs/heads/archive/2026-08-11+120000_$org_branch" "$base_sha"
assert_ref_absent "$org_fork_repo" "refs/heads/$org_branch"
assert_local_ref_absent "refs/heads/$org_branch"
assert_file_contains "head=NVIDIA-dev:$org_branch" "$gh_log"

upstream_branch=upstream-clean
create_remote_branch "$upstream_branch" upstream
export TEST_PR_MODE=exact-merged
export TEST_PR_HEAD_REPO=NVIDIA-dev/project
export TEST_PR_HEAD_SHA="$base_sha"
(
    cd "$work_repo"
    "$delete_command" --archive-to-remote=org-fork "$upstream_branch"
) >"$test_root/upstream.stdout" 2>"$test_root/upstream.stderr"
assert_ref_sha "$org_fork_repo" "refs/heads/archive/pr510_2026-08-11+120000_$upstream_branch" "$base_sha"
assert_ref_absent "$upstream_repo" "refs/heads/$upstream_branch"
assert_local_ref_absent "refs/heads/$upstream_branch"

unavailable_scan_branch=unavailable-scan
create_remote_branch "$unavailable_scan_branch" upstream
git -C "$work_repo" remote add unavailable "$test_root/unavailable.git"
export TEST_PR_MODE=none
(
    cd "$work_repo"
    "$delete_command" --archive-to-remote=org-fork "$unavailable_scan_branch"
) >"$test_root/unavailable-scan.stdout" 2>"$test_root/unavailable-scan.stderr"
assert_file_contains "could not inspect remote 'unavailable'" "$test_root/unavailable-scan.stderr"
assert_ref_absent "$upstream_repo" "refs/heads/$unavailable_scan_branch"
assert_local_ref_absent "refs/heads/$unavailable_scan_branch"

implicit_source_branch=implicit-source-with-unavailable
git -C "$work_repo" branch "$implicit_source_branch" "$base_sha"
git -C "$work_repo" push -q upstream "$implicit_source_branch:refs/heads/$implicit_source_branch"
run_expect_failure \
    "$test_root/implicit-source.stdout" \
    "$test_root/implicit-source.stderr" \
    --archive-to-remote=org-fork \
    "$implicit_source_branch"
assert_file_contains "has no configured remote tracking branch, and not every remote could be inspected" "$test_root/implicit-source.stderr"
assert_ref_sha "$upstream_repo" "refs/heads/$implicit_source_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$implicit_source_branch" "$base_sha"
git -C "$work_repo" remote remove unavailable

rerun_branch=rerun-existing-archive
create_remote_branch "$rerun_branch" upstream
rerun_archive_branch="archive/2026-08-11+120000_$rerun_branch"
rerun_recovery_branch="archive/2026-08-11+120000_retry1_$rerun_branch"
git -C "$work_repo" push -q org-fork "$base_sha:refs/heads/$rerun_archive_branch"
export TEST_PR_MODE=none
(
    cd "$work_repo"
    "$delete_command" --archive-to-remote=org-fork "$rerun_branch"
) >"$test_root/rerun.stdout" 2>"$test_root/rerun.stderr"
assert_file_contains "using fresh recovery ref 'org-fork/$rerun_recovery_branch'" "$test_root/rerun.stderr"
assert_ref_sha "$org_fork_repo" "refs/heads/$rerun_archive_branch" "$base_sha"
assert_ref_sha "$org_fork_repo" "refs/heads/$rerun_recovery_branch" "$base_sha"
assert_ref_absent "$upstream_repo" "refs/heads/$rerun_branch"
assert_local_ref_absent "refs/heads/$rerun_branch"

missing_source_branch=rerun-missing-source
create_remote_branch "$missing_source_branch" upstream
missing_source_archive_branch="archive/2026-08-10+120001+0000_$missing_source_branch"
missing_source_recovery_branch="archive/2026-08-11+120000_$missing_source_branch"
git -C "$work_repo" push -q org-fork "$base_sha:refs/heads/$missing_source_archive_branch"
git -C "$work_repo" push -q upstream ":refs/heads/$missing_source_branch"
(
    cd "$work_repo"
    "$delete_command" --archive-to-remote=org-fork "$missing_source_branch"
) >"$test_root/missing-source.stdout" 2>"$test_root/missing-source.stderr"
assert_file_contains "was already deleted" "$test_root/missing-source.stderr"
assert_ref_sha "$org_fork_repo" "refs/heads/$missing_source_archive_branch" "$base_sha"
assert_ref_sha "$org_fork_repo" "refs/heads/$missing_source_recovery_branch" "$base_sha"
assert_local_ref_absent "refs/heads/$missing_source_branch"

colleague_branch=reject-colleague
create_remote_branch "$colleague_branch" upstream
export TEST_PR_MODE=none
run_expect_failure \
    "$test_root/colleague.stdout" \
    "$test_root/colleague.stderr" \
    --archive-to-remote=colleague-fork \
    "$colleague_branch"
assert_file_contains "which is not a GitHub fork owned or administered by your authenticated user" "$test_root/colleague.stderr"
assert_ref_sha "$upstream_repo" "refs/heads/$colleague_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$colleague_branch" "$base_sha"

nonfork_branch=reject-nonfork
create_remote_branch "$nonfork_branch" org-fork
run_expect_failure \
    "$test_root/nonfork.stdout" \
    "$test_root/nonfork.stderr" \
    --archive-to-remote=upstream \
    "$nonfork_branch"
assert_file_contains "is fork: 'false'" "$test_root/nonfork.stderr"
assert_ref_sha "$org_fork_repo" "refs/heads/$nonfork_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$nonfork_branch" "$base_sha"

unrelated_branch=reject-unrelated
create_remote_branch "$unrelated_branch" upstream
run_expect_failure \
    "$test_root/unrelated.stdout" \
    "$test_root/unrelated.stderr" \
    --archive-to-remote=unrelated-fork \
    "$unrelated_branch"
assert_file_contains "which is not in the same GitHub fork network" "$test_root/unrelated.stderr"
assert_ref_sha "$upstream_repo" "refs/heads/$unrelated_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$unrelated_branch" "$base_sha"

open_branch=reject-open-pr
create_remote_branch "$open_branch" org-fork
export TEST_PR_MODE=exact-open
export TEST_PR_HEAD_REPO=NVIDIA-dev/project-rwgk
export TEST_PR_HEAD_SHA="$base_sha"
run_expect_failure \
    "$test_root/open.stdout" \
    "$test_root/open.stderr" \
    --archive-to-remote=org-fork \
    "$open_branch"
assert_file_contains "still has open PR #510" "$test_root/open.stderr"
assert_ref_sha "$org_fork_repo" "refs/heads/$open_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$open_branch" "$base_sha"

duplicate_branch=reject-duplicate
create_remote_branch "$duplicate_branch" upstream
git -C "$work_repo" push -q org-fork "$duplicate_branch:refs/heads/$duplicate_branch"
export TEST_PR_MODE=none
run_expect_failure \
    "$test_root/duplicate.stdout" \
    "$test_root/duplicate.stderr" \
    --archive-to-remote=org-fork \
    "$duplicate_branch"
assert_file_contains "resolves to multiple live remote branches" "$test_root/duplicate.stderr"
assert_file_contains "repository 'NVIDIA-dev/project'" "$test_root/duplicate.stderr"
assert_file_contains "repository 'NVIDIA-dev/project-rwgk'" "$test_root/duplicate.stderr"
assert_ref_sha "$upstream_repo" "refs/heads/$duplicate_branch" "$base_sha"
assert_ref_sha "$org_fork_repo" "refs/heads/$duplicate_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$duplicate_branch" "$base_sha"

shared_remote_branch=shared-remote-source
first_alias_branch=first-alias-local
second_alias_branch=second-alias-local
git -C "$work_repo" branch "$first_alias_branch" "$base_sha"
git -C "$work_repo" branch "$second_alias_branch" "$base_sha"
git -C "$work_repo" push -q upstream "$base_sha:refs/heads/$shared_remote_branch"
git -C "$work_repo" fetch -q upstream "$shared_remote_branch"
git -C "$work_repo" fetch -q upstream-alias "$shared_remote_branch"
git -C "$work_repo" branch --set-upstream-to="upstream/$shared_remote_branch" "$first_alias_branch" >/dev/null
git -C "$work_repo" branch --set-upstream-to="upstream-alias/$shared_remote_branch" "$second_alias_branch" >/dev/null
run_expect_failure \
    "$test_root/shared-plan.stdout" \
    "$test_root/shared-plan.stderr" \
    --archive-to-remote=org-fork \
    "$first_alias_branch" \
    "$second_alias_branch"
assert_file_contains "would both mutate source ref 'NVIDIA-dev/project:refs/heads/$shared_remote_branch'" "$test_root/shared-plan.stderr"
assert_ref_sha "$upstream_repo" "refs/heads/$shared_remote_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$first_alias_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$second_alias_branch" "$base_sha"

bad_backup_branch=bad-backup-destination
create_remote_branch "$bad_backup_branch" upstream
export MY_GIT_BACKTRACKING_INFO_LOCAL="$test_root/does-not-exist"
run_expect_failure \
    "$test_root/bad-backup.stdout" \
    "$test_root/bad-backup.stderr" \
    --archive-to-remote=org-fork \
    "$bad_backup_branch"
assert_file_contains "local backtracking directory does not exist" "$test_root/bad-backup.stderr"
assert_ref_sha "$upstream_repo" "refs/heads/$bad_backup_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$bad_backup_branch" "$base_sha"
export MY_GIT_BACKTRACKING_INFO_LOCAL="$backtracking_dir"

multi_first_branch=multi-first
multi_worktree_branch=multi-worktree
create_remote_branch "$multi_first_branch" upstream
create_remote_branch "$multi_worktree_branch" upstream
git -C "$work_repo" worktree add -q "$test_root/attached-worktree" "$multi_worktree_branch"
run_expect_failure \
    "$test_root/worktree.stdout" \
    "$test_root/worktree.stderr" \
    --archive-to-remote=org-fork \
    "$multi_first_branch" \
    "$multi_worktree_branch"
assert_file_contains "is checked out in worktree '$test_root/attached-worktree'" "$test_root/worktree.stderr"
assert_ref_sha "$upstream_repo" "refs/heads/$multi_first_branch" "$base_sha"
assert_local_ref_sha "refs/heads/$multi_first_branch" "$base_sha"

compgen -G "$backtracking_dir/work_personal-clean_*.txt" >/dev/null ||
    fail "personal-fork cleanup did not create backtracking information."
compgen -G "$backtracking_dir/work_org-clean_*.txt" >/dev/null ||
    fail "intra-org-fork cleanup did not create backtracking information."

echo "PASS: git_branch_D_track_hash integration checks"
