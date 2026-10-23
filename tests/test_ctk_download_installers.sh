#!/bin/bash
set -euo pipefail

repo_root=$(
    CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P
)
test_root=$(mktemp -d /tmp/ctk_download_installers_test.XXXXXX)
trap 'rm -rf -- "$test_root"' EXIT

fail() {
    echo "FAIL ($test_case): $*" >&2
    [[ ! -f "$test_root/stderr" ]] || cat "$test_root/stderr" >&2
    exit 1
}

export CTK_TEST_REAL_MD5SUM
CTK_TEST_REAL_MD5SUM=$(command -v md5sum)
export CTK_TEST_PAYLOADS="$test_root/payloads"
export CTK_TEST_MD5_LOG="$test_root/md5.log"
export CTK_TEST_CURL_LOG="$test_root/curl.log"
mkdir -p "$test_root/bin" "$CTK_TEST_PAYLOADS"

old_name=cuda_12.8.1_test.run
linux_name=cuda_12.8.2_test.run
windows_name=cuda_13.4.3_test.exe
printf 'good-data\n' >"$CTK_TEST_PAYLOADS/$linux_name"
cp "$CTK_TEST_PAYLOADS/$linux_name" "$CTK_TEST_PAYLOADS/$windows_name"
payload_size=$(stat -c %s "$CTK_TEST_PAYLOADS/$linux_name")
payload_md5=$("$CTK_TEST_REAL_MD5SUM" "$CTK_TEST_PAYLOADS/$linux_name")
payload_md5=${payload_md5%% *}
printf '%s\t%s\t%s\t%s\t%s\tfixture://%s\n' \
    12.8.1 linux-amd64 "$old_name" "$payload_md5" "$payload_size" "$old_name" \
    12.8.2 linux-amd64 "$linux_name" "$payload_md5" "$payload_size" "$linux_name" \
    13.4.3 windows-amd64 "$windows_name" "$payload_md5" "$payload_size" "$windows_name" \
    >"$test_root/catalog"

# Exercise the real CLI and implementation with small, offline catalog entries.
script="$test_root/ctk_download_installers.sh"
awk -v replacement="$test_root/catalog" '
    /^    cat <<\047CATALOG\047$/ {
        print
        while ((getline row < replacement) > 0)
            print row
        close(replacement)
        replacing = 1
        next
    }
    replacing && /^CATALOG$/ { replacing = 0 }
    !replacing { print }
' "$repo_root/bin/ctk_download_installers.sh" >"$script"
chmod +x "$script"

cat >"$test_root/bin/md5sum" <<'EOF'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >>"$CTK_TEST_MD5_LOG"
exec "$CTK_TEST_REAL_MD5SUM" "$@"
EOF

cat >"$test_root/bin/curl" <<'EOF'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >>"$CTK_TEST_CURL_LOG"
output=
url=
while (($#)); do
    case "$1" in
    --output)
        output="$2"
        shift 2
        ;;
    --continue-at | --retry)
        shift 2
        ;;
    --fail | --location | --retry-all-errors | --no-progress-meter)
        shift
        ;;
    fixture://*)
        url="${1#fixture://}"
        shift
        ;;
    *)
        echo "Unexpected curl argument: $1" >&2
        exit 1
        ;;
    esac
done
[[ -n "$output" && -n "$url" && "$url" != */* ]] || exit 1
offset=0
[[ ! -f "$output" ]] || offset=$(stat -c %s -- "$output")
tail -c "+$((offset + 1))" "$CTK_TEST_PAYLOADS/$url" >>"$output"
EOF
chmod +x "$test_root/bin/md5sum" "$test_root/bin/curl"
export PATH="$test_root/bin:$PATH"

new_case() {
    test_case="$1"
    case_dir="$test_root/$test_case"
    mkdir "$case_dir"
    : >"$CTK_TEST_MD5_LOG"
    : >"$CTK_TEST_CURL_LOG"
    printf 'good-data\n' >"$CTK_TEST_PAYLOADS/$linux_name"
}

seed_final_files() {
    cp "$CTK_TEST_PAYLOADS/$linux_name" "$case_dir/$linux_name"
    cp "$CTK_TEST_PAYLOADS/$windows_name" "$case_dir/$windows_name"
}

run_cli() {
    (
        cd "$case_dir"
        "$script" "$@"
    ) >"$test_root/stdout" 2>"$test_root/stderr"
}

expect_success() {
    run_cli "$@" || fail "command failed: $*"
}

expect_failure() {
    if run_cli "$@"; then
        fail "command unexpectedly succeeded: $*"
    fi
}

assert_calls() {
    local expected_md5="$1"
    local expected_curl="$2"
    [[ $(wc -l <"$CTK_TEST_MD5_LOG") -eq "$expected_md5" ]] ||
        fail "expected $expected_md5 MD5 calls"
    [[ $(wc -l <"$CTK_TEST_CURL_LOG") -eq "$expected_curl" ]] ||
        fail "expected $expected_curl curl calls"
}

new_case rejected-sync
seed_final_files
expect_failure sync
assert_calls 0 0

for mode in fast-sync full-sync; do
    new_case "$mode-existing"
    seed_final_files
    expect_success "$mode"
    if [[ "$mode" == fast-sync ]]; then
        assert_calls 0 0
    else
        assert_calls 2 0
    fi

    new_case "$mode-existing-same-size-corruption"
    seed_final_files
    printf 'evil-data\n' >"$case_dir/$linux_name"
    touch "$case_dir/$old_name"
    if [[ "$mode" == fast-sync ]]; then
        expect_success "$mode"
        assert_calls 0 0
    else
        expect_failure "$mode" --purge
        assert_calls 1 0
    fi
    [[ -f "$case_dir/$old_name" ]] || fail "purged after failed verification"

    new_case "$mode-existing-wrong-size"
    seed_final_files
    printf 'short' >"$case_dir/$linux_name"
    touch "$case_dir/$old_name"
    expect_failure "$mode" --purge
    assert_calls 0 0
    [[ -f "$case_dir/$old_name" ]] || fail "purged after a size mismatch"

    new_case "$mode-complete-parts"
    cp "$CTK_TEST_PAYLOADS/$linux_name" "$case_dir/$linux_name.part"
    cp "$CTK_TEST_PAYLOADS/$windows_name" "$case_dir/$windows_name.part"
    expect_success "$mode"
    assert_calls 2 0
    [[ -f "$case_dir/$linux_name" && -f "$case_dir/$windows_name" ]] ||
        fail "did not promote completed parts"
    [[ ! -e "$case_dir/$linux_name.part" && ! -e "$case_dir/$windows_name.part" ]] ||
        fail "completed parts remain after promotion"

    new_case "$mode-incomplete-parts"
    printf 'good-' >"$case_dir/$linux_name.part"
    printf 'good-' >"$case_dir/$windows_name.part"
    expect_success "$mode"
    assert_calls 2 2
    cmp -s "$CTK_TEST_PAYLOADS/$linux_name" "$case_dir/$linux_name" ||
        fail "resumed download contents differ"

    new_case "$mode-download-and-purge"
    touch "$case_dir/$old_name" "$case_dir/cuda_13.4.2_old.exe"
    touch "$case_dir/unrelated.run" "$case_dir/cuda_11.0_old.run" "$case_dir/$old_name.part"
    mkdir -p "$case_dir/nested" "$case_dir/cuda_12_directory.run"
    touch "$case_dir/nested/$old_name" "$case_dir/cuda_12_directory.run/keep"
    expect_success "$mode" --purge
    assert_calls 2 2
    [[ ! -e "$case_dir/$old_name" && ! -e "$case_dir/cuda_13.4.2_old.exe" ]] ||
        fail "did not purge obsolete top-level installers"
    for preserved in unrelated.run cuda_11.0_old.run "$old_name.part" \
        "nested/$old_name" cuda_12_directory.run/keep "$linux_name" "$windows_name"; do
        [[ -f "$case_dir/$preserved" ]] || fail "removed $preserved"
    done

    new_case "$mode-corrupt-download"
    printf 'evil-data\n' >"$CTK_TEST_PAYLOADS/$linux_name"
    touch "$case_dir/$old_name"
    expect_failure "$mode" --purge
    assert_calls 1 1
    [[ ! -e "$case_dir/$linux_name" && -f "$case_dir/$linux_name.part" ]] ||
        fail "promoted corrupt download or discarded its part"
    [[ -f "$case_dir/$old_name" ]] || fail "purged after a corrupt download"

    new_case "$mode-corrupt-complete-part"
    printf 'evil-data\n' >"$case_dir/$linux_name.part"
    touch "$case_dir/$old_name"
    expect_failure "$mode" --purge
    [[ $(wc -l <"$CTK_TEST_MD5_LOG") -ge 1 ]] || fail "did not hash completed part"
    [[ ! -e "$case_dir/$linux_name" && -f "$case_dir/$linux_name.part" ]] ||
        fail "promoted corrupt completed part or discarded it"
    [[ -f "$case_dir/$old_name" ]] || fail "purged after a corrupt completed part"

    new_case "$mode-invalid-arguments"
    expect_failure "$mode" --unexpected
    expect_failure "$mode" --purge --purge
    assert_calls 0 0
done

new_case version-download
seed_final_files
expect_success 12.8.2 linux-amd64
assert_calls 1 0
printf 'evil-data\n' >"$case_dir/$linux_name"
expect_failure 12.8.2 linux-amd64
assert_calls 2 0

echo 'PASS: CUDA installer fast-sync/full-sync offline regression tests'
