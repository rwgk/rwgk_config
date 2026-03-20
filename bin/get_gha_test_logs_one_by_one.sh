#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: get_gha_test_logs_one_by_one.sh [-R OWNER/REPO] <run-id-or-actions-url>

Download all GitHub Actions job logs whose names start with "Test " from a
workflow run, saving one log per file in the current directory.

Accepted input forms:
  - a numeric run ID
  - a GitHub Actions run URL
  - a GitHub Actions job URL

Examples:
  get_gha_test_logs_one_by_one.sh 23353049644
  get_gha_test_logs_one_by_one.sh -R NVIDIA/cuda-python 23353049644
  get_gha_test_logs_one_by_one.sh \
    https://github.com/NVIDIA/cuda-python/actions/runs/23353049644
  get_gha_test_logs_one_by_one.sh \
    'https://github.com/NVIDIA/cuda-python/actions/runs/23353049644/job/67937671460?pr=1799'
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

sanitize_filename() {
    local raw_name="$1"
    local safe_name
    safe_name=$(
        printf '%s' "$raw_name" |
            LC_ALL=C sed -E '
        s/[^A-Za-z0-9._+-]+/_/g
        s/_+/_/g
        s/^_+//
        s/_+$//
      '
    )
    if [[ -z "$safe_name" ]]; then
        safe_name="job"
    fi
    if [[ "$safe_name" == -* ]]; then
        safe_name="_$safe_name"
    fi
    printf '%s\n' "$safe_name"
}

choose_output_path() {
    local base_name="$1"
    local job_id="$2"
    local candidate="${base_name}_log.txt"
    local n=2

    if [[ ! -e "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return
    fi

    candidate="${base_name}__job_${job_id}_log.txt"
    if [[ ! -e "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return
    fi

    while true; do
        candidate="${base_name}__job_${job_id}_${n}_log.txt"
        if [[ ! -e "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
        ((n++))
    done
}

parsed_repo=""
parsed_run_id=""

parse_run_ref() {
    local ref="$1"
    parsed_repo=""
    parsed_run_id=""

    if [[ "$ref" =~ ^https?://github\.com/([^/]+)/([^/]+)/actions/runs/([0-9]+)(/job/[0-9]+)?([?].*)?$ ]]; then
        parsed_repo="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
        parsed_run_id="${BASH_REMATCH[3]}"
        return
    fi

    if [[ "$ref" =~ ^[0-9]+$ ]]; then
        parsed_run_id="$ref"
        return
    fi

    die "Expected a run ID or GitHub Actions URL, got: $ref"
}

command -v gh >/dev/null 2>&1 || die "gh command not found in PATH"

repo=""
run_ref=""

while [[ $# -gt 0 ]]; do
    case "$1" in
    -R | --repo)
        [[ $# -ge 2 ]] || die "Missing value for $1"
        repo="$2"
        shift 2
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    --)
        shift
        break
        ;;
    -*)
        die "Unknown option: $1"
        ;;
    *)
        if [[ -n "$run_ref" ]]; then
            die "Unexpected extra argument: $1"
        fi
        run_ref="$1"
        shift
        ;;
    esac
done

[[ $# -eq 0 ]] || die "Unexpected extra arguments: $*"
[[ -n "$run_ref" ]] || {
    usage >&2
    exit 2
}

parse_run_ref "$run_ref"
run_id="$parsed_run_id"

if [[ -n "$parsed_repo" ]]; then
    if [[ -n "$repo" && "$repo" != "$parsed_repo" ]]; then
        die "Repo from -R ($repo) does not match repo in URL ($parsed_repo)"
    fi
    repo="$parsed_repo"
fi

if [[ -z "$repo" ]]; then
    repo=$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || true)
fi

[[ -n "$repo" ]] || die "Could not determine repo. Pass -R OWNER/REPO or use a full GitHub URL."

mapfile -t job_lines < <(
    gh api "repos/$repo/actions/runs/$run_id/jobs" --paginate \
        --jq '.jobs[] | select(.name | startswith("Test ")) | [.id, .name] | @tsv'
)

if [[ ${#job_lines[@]} -eq 0 ]]; then
    die "No jobs whose names start with \"Test \" were found for run $run_id in $repo"
fi

echo "Repo: $repo" >&2
echo "Run ID: $run_id" >&2
echo "Test jobs found: ${#job_lines[@]}" >&2

num_ok=0
num_failed=0

for i in "${!job_lines[@]}"; do
    IFS=$'\t' read -r job_id job_name <<<"${job_lines[$i]}"

    safe_name=$(sanitize_filename "$job_name")
    output_path=$(choose_output_path "$safe_name" "$job_id")
    tmp_path="${output_path}.tmp.$$"

    printf '[%d/%d] %s -> %s\n' "$((i + 1))" "${#job_lines[@]}" "$job_name" "$output_path" >&2

    if gh api "repos/$repo/actions/jobs/$job_id/logs" >"$tmp_path"; then
        mv "$tmp_path" "$output_path"
        ((num_ok += 1))
    else
        rm -f "$tmp_path"
        echo "FAILED job $job_id: $job_name" >&2
        ((num_failed += 1))
    fi
done

echo "Downloaded: $num_ok" >&2
echo "Failed: $num_failed" >&2

if [[ $num_failed -ne 0 ]]; then
    exit 1
fi
