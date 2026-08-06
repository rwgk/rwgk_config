#!/bin/bash
set -euo pipefail

repo_root=$(
    CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P
)
archive_command="$repo_root/bin/archive_branch_to_fork.sh"
delete_command="$repo_root/bin/git_branch_D_track_hash"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local expected="$1"
    local file="$2"

    if ! grep -Fqx "$expected" "$file"; then
        printf '%s\n' "Contents of '$file':" >&2
        sed 's/^/  /' "$file" >&2
        fail "expected '$expected' in '$file'."
    fi
}

test_root=$(mktemp -d /tmp/archive_branch_to_fork_test.XXXXXX)
trap 'rm -rf -- "$test_root"' EXIT

work_repo="$test_root/work"
remote_work_repo="$test_root/remote-work"
upstream_repo="$test_root/upstream.git"
fork_repo="$test_root/fork.git"
fake_bin="$test_root/fake-bin"
backtracking_dir="$test_root/backtracking"
mkdir -p "$fake_bin" "$backtracking_dir"

git -c init.defaultBranch=main init -q "$work_repo"
git -C "$work_repo" config user.name "Archive Test"
git -C "$work_repo" config user.email "archive-test@example.com"
printf 'base\n' >"$work_repo/content.txt"
git -C "$work_repo" add content.txt
git -C "$work_repo" commit -m base >/dev/null
base_sha=$(git -C "$work_repo" rev-parse HEAD)

git -c init.defaultBranch=main init --bare -q "$upstream_repo"
git -c init.defaultBranch=main init --bare -q "$fork_repo"
git -C "$work_repo" remote add upstream "$upstream_repo"
git -C "$work_repo" remote add origin "$upstream_repo"
git -C "$work_repo" remote set-url --push origin "$fork_repo"
git -C "$work_repo" push -q upstream main

git -C "$work_repo" switch -q -c feature/test
printf 'local\n' >>"$work_repo/content.txt"
git -C "$work_repo" commit -am "local change" >/dev/null
local_sha=$(git -C "$work_repo" rev-parse HEAD)

git clone -q "$upstream_repo" "$remote_work_repo"
git -C "$remote_work_repo" config user.name "Archive Test"
git -C "$remote_work_repo" config user.email "archive-test@example.com"
git -C "$remote_work_repo" switch -q -c feature/test
printf 'remote\n' >"$remote_work_repo/remote.txt"
git -C "$remote_work_repo" add remote.txt
git -C "$remote_work_repo" commit -m "remote change" >/dev/null
remote_sha=$(git -C "$remote_work_repo" rev-parse HEAD)
git -C "$remote_work_repo" push -q origin feature/test

git -C "$work_repo" fetch -q upstream feature/test
git -C "$work_repo" branch --set-upstream-to=upstream/feature/test feature/test >/dev/null
mkdir -p "$work_repo/.git/gh-stack"
printf 'must remain unchanged\n' >"$work_repo/.git/gh-stack/archive-test"
stack_checksum=$(git -C "$work_repo" hash-object "$work_repo/.git/gh-stack/archive-test")

cat >"$fake_bin/gh" <<'EOF'
#!/bin/bash
set -euo pipefail

if [[ "$1" == repo && "$2" == view ]]; then
    printf 'https://github.com/tester/project\ttester/project\n'
    exit
fi

if [[ "$1" == pr && "$2" == list ]]; then
    printf '42\thttps://github.com/upstream/project/pull/42\t2026-08-06T04:01:15Z\t%s\tfeature/test\tupstream-owner\tmain\t%s\t%s\n' \
        "$TEST_REMOTE_SHA" "$TEST_BASE_SHA" "$TEST_BASE_SHA"
    if [[ -n "${TEST_MULTIPLE_PRS:-}" ]]; then
        printf '41\thttps://github.com/upstream/project/pull/41\t2026-08-05T04:01:15Z\t%s\tfeature/test\tupstream-owner\tmain\t%s\t%s\n' \
            "$TEST_REMOTE_SHA" "$TEST_BASE_SHA" "$TEST_BASE_SHA"
    fi
    exit
fi

[[ "$1" == api ]] || {
    echo "Unexpected gh invocation: $*" >&2
    exit 1
}
shift

endpoint=""
for arg in "$@"; do
    case "$arg" in
    user | repos/*)
        endpoint="$arg"
        break
        ;;
    esac
done

case "$endpoint" in
user)
    echo tester
    ;;
repos/tester/project)
    printf 'true\ttester\tupstream/project\n'
    ;;
repos/upstream/project/pulls | repos/upstream/project/pulls/42)
    if [[ "$*" == *'.draft'* ]]; then
        printf '42\tclosed\thttps://github.com/upstream/project/pull/42\tfalse\ttrue\n'
    else
        printf '42\thttps://github.com/upstream/project/pull/42\t2026-08-06T04:01:15Z\t%s\tfeature/test\tupstream-owner\tmain\t%s\t%s\n' \
            "$TEST_REMOTE_SHA" "$TEST_BASE_SHA" "$TEST_BASE_SHA"
    fi
    ;;
*)
    echo "Unexpected gh api endpoint: '$endpoint' ($*)" >&2
    exit 1
    ;;
esac
EOF
chmod 755 "$fake_bin/gh"

cat >"$fake_bin/scp" <<'EOF'
#!/bin/bash
set -euo pipefail

[[ "$#" -eq 3 && "$1" == -p && "$3" == testhost:* ]] || {
    echo "Unexpected scp invocation: $*" >&2
    exit 1
}
cp -p "$2" "${3#testhost:}"
EOF
chmod 755 "$fake_bin/scp"

cat >"$fake_bin/ssh" <<'EOF'
#!/bin/bash
set -euo pipefail

[[ "$#" -eq 2 && "$1" == testhost ]] || {
    echo "Unexpected ssh invocation: $*" >&2
    exit 1
}
exec bash -c "$2"
EOF
chmod 755 "$fake_bin/ssh"

export TEST_BASE_SHA="$base_sha"
export TEST_REMOTE_SHA="$remote_sha"
export MY_GIT_BACKTRACKING_INFO_LOCAL="$backtracking_dir"
unset MY_GIT_BACKTRACKING_INFO_REMOTE
export PATH="$fake_bin:$PATH"

first_stdout="$test_root/first.stdout"
first_stderr="$test_root/first.stderr"
(
    cd "$work_repo"
    "$archive_command" feature/test
) >"$first_stdout" 2>"$first_stderr"

archive_branch="archive/pr42_2026-08-05+210115_feature/test"
archive_ref="refs/heads/$archive_branch"
archived_sha=$(git --git-dir="$fork_repo" rev-parse "$archive_ref")
[[ "$archived_sha" == "$local_sha" ]] ||
    fail "archive ref has '$archived_sha', expected local tip '$local_sha'."
[[ "$(git --git-dir="$upstream_repo" rev-parse refs/heads/feature/test)" == "$remote_sha" ]] ||
    fail "source branch changed."
[[ "$(git -C "$work_repo" rev-parse refs/heads/feature/test)" == "$local_sha" ]] ||
    fail "local branch changed."
[[ "$(git -C "$work_repo" hash-object "$work_repo/.git/gh-stack/archive-test")" == "$stack_checksum" ]] ||
    fail ".git/gh-stack changed."

record="$backtracking_dir/work_feature_test_pr42_2026-08-05+210115.txt"
[[ -f "$record" ]] || fail "backtracking record was not created."
assert_contains "Local branch HEAD SHA: '$local_sha'" "$record"
assert_contains "PR head branch: 'upstream-owner/feature/test'" "$record"
assert_contains "PR head SHA: '$remote_sha'" "$record"
assert_contains "Live remote branch SHA: '$remote_sha'" "$record"
assert_contains "INFO: live remote branch 'upstream/feature/test' was 1 commit ahead of and 1 commit behind the local branch." "$record"
assert_contains "WARNING: only the local branch was archived; 1 remote-only commit from live remote branch 'upstream/feature/test' was not preserved by the archive ref." "$record"
assert_contains "INFO: live remote branch 'upstream/feature/test' was 1 commit ahead of and 1 commit behind the local branch." "$first_stderr"

second_stdout="$test_root/second.stdout"
(
    cd "$work_repo"
    "$archive_command" --pr 42 feature/test
) >"$second_stdout" 2>"$test_root/second.stderr"
assert_contains "Archive ref already exists at the local tip: 'origin/$archive_branch'." "$second_stdout"
assert_contains "Backtracking information already exists: '$record'" "$second_stdout"

export TEST_MULTIPLE_PRS=1
if (
    cd "$work_repo"
    "$archive_command" feature/test
) >"$test_root/ambiguous.stdout" 2>"$test_root/ambiguous.stderr"; then
    fail "ambiguous merged-PR discovery unexpectedly succeeded."
fi
assert_contains "Error: multiple merged PRs in 'upstream/project' use former head branch 'feature/test'." "$test_root/ambiguous.stderr"
unset TEST_MULTIPLE_PRS

printf 'unfetched remote tip\n' >>"$remote_work_repo/remote.txt"
git -C "$remote_work_repo" commit -am "unfetched remote tip" >/dev/null
unfetched_remote_sha=$(git -C "$remote_work_repo" rev-parse HEAD)
git -C "$remote_work_repo" push -q origin feature/test
cached_tracking_sha=$(
    printf 'cached remote-tracking tip\n' |
        git -C "$work_repo" commit-tree "$(git -C "$work_repo" rev-parse "${base_sha}^{tree}")" -p "$base_sha"
)
git -C "$work_repo" update-ref refs/remotes/upstream/feature/test "$cached_tracking_sha"
rm -f "$record"
(
    cd "$work_repo"
    "$archive_command" --pr 42 feature/test
) >"$test_root/unavailable.stdout" 2>"$test_root/unavailable.stderr"
assert_contains "Live remote branch SHA: '$unfetched_remote_sha'" "$record"
assert_contains "Cached remote-tracking SHA: '$cached_tracking_sha'" "$record"
assert_contains "INFO: ahead/behind counts for live remote branch 'upstream/feature/test' are unavailable because commit '$unfetched_remote_sha' is not present locally." "$record"
assert_contains "WARNING: only the local branch was archived; whether live remote branch 'upstream/feature/test' has remote-only history could not be determined." "$record"
assert_contains "INFO: cached remote-tracking ref 'refs/remotes/upstream/feature/test' was 1 commit ahead of and 1 commit behind the local branch." "$record"
assert_contains "WARNING: only the local branch was archived; 1 remote-only commit from cached remote-tracking ref 'refs/remotes/upstream/feature/test' was not preserved by the archive ref." "$record"
assert_contains "INFO: PR head snapshot 'upstream-owner/feature/test' was 1 commit ahead of and 1 commit behind the local branch." "$record"

remote_backtracking_dir="$test_root/remote-backtracking"
mkdir "$remote_backtracking_dir"
git -C "$work_repo" branch --unset-upstream feature/test
unset MY_GIT_BACKTRACKING_INFO_LOCAL
export MY_GIT_BACKTRACKING_INFO_REMOTE="testhost:$remote_backtracking_dir"
(
    cd "$work_repo"
    "$archive_command" --pr 42 feature/test
) >"$test_root/remote-first.stdout" 2>"$test_root/remote-first.stderr"
remote_record="$remote_backtracking_dir/$(basename "$record")"
[[ -f "$remote_record" ]] || fail "remote backtracking record was not created."
assert_contains "Archive ref: '$archive_ref'" "$remote_record"
assert_contains "Configured remote-tracking ref: 'none'" "$remote_record"
assert_contains "INFO: PR head snapshot 'upstream-owner/feature/test' was 1 commit ahead of and 1 commit behind the local branch." "$remote_record"
(
    cd "$work_repo"
    "$archive_command" --pr 42 feature/test
) >"$test_root/remote-second.stdout" 2>"$test_root/remote-second.stderr"
assert_contains "Backtracking information already exists: 'testhost:$remote_backtracking_dir/$(basename "$record")'" "$test_root/remote-second.stdout"
if compgen -G "$remote_backtracking_dir/.*.tmp" >/dev/null; then
    fail "remote backtracking publication left a temporary file behind."
fi
export MY_GIT_BACKTRACKING_INFO_LOCAL="$backtracking_dir"
unset MY_GIT_BACKTRACKING_INFO_REMOTE
git -C "$work_repo" branch --set-upstream-to=upstream/feature/test feature/test >/dev/null

git -C "$work_repo" switch -q main
if (
    cd "$work_repo"
    "$delete_command" feature/test
) >"$test_root/preflight.stdout" 2>"$test_root/preflight.stderr"; then
    fail "git_branch_D_track_hash default preflight unexpectedly succeeded."
fi
assert_contains "Local branch 'feature/test' has configured remote tracking branch 'upstream/feature/test'." "$test_root/preflight.stderr"
[[ "$(git -C "$work_repo" rev-parse refs/heads/feature/test)" == "$local_sha" ]] ||
    fail "git_branch_D_track_hash default preflight changed the local branch."
git -C "$work_repo" switch -q feature/test

printf 'new local tip\n' >>"$work_repo/content.txt"
git -C "$work_repo" commit -am "new local tip" >/dev/null
new_local_sha=$(git -C "$work_repo" rev-parse HEAD)
if (
    cd "$work_repo"
    "$archive_command" feature/test
) >"$test_root/collision.stdout" 2>"$test_root/collision.stderr"; then
    fail "archive collision unexpectedly succeeded."
fi
assert_contains "Error: archive ref 'origin/$archive_branch' already exists at '$local_sha', not local tip '$new_local_sha'; refusing to overwrite it." "$test_root/collision.stderr"
[[ "$(git --git-dir="$fork_repo" rev-parse "$archive_ref")" == "$local_sha" ]] ||
    fail "archive collision changed the existing ref."

git -C "$work_repo" branch delete-me "$base_sha"
(
    cd "$work_repo"
    "$delete_command" --local-only delete-me
) >"$test_root/delete.stdout" 2>"$test_root/delete.stderr"
if git -C "$work_repo" show-ref --verify --quiet refs/heads/delete-me; then
    fail "git_branch_D_track_hash no longer deletes a local-only branch."
fi
compgen -G "$backtracking_dir/work_delete-me_*.txt" >/dev/null ||
    fail "git_branch_D_track_hash did not create its backtracking record."

echo "PASS: archive_branch_to_fork.sh integration checks"
