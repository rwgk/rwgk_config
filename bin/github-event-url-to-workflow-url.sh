#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage:
  github-event-url-to-workflow-url.sh [--job-url] [--verbose] <pr-event-url>

Options:
  --job-url   Print the best matching job/log URL instead of the workflow run URL.
  --verbose   Print event details, the best match, and other ranked candidates.
  -h, --help  Show this help.

Environment:
  WINDOW_SECONDS   Search window around the event timestamp (default: 900)
  RUN_LIMIT        Number of branch runs to inspect (default: 100)
  TOP_RUNS         Number of candidate runs to enrich with jobs/logs (default: 8)

This is a best-effort lookup. GitHub does not expose a direct
"timeline event -> workflow run" mapping, so the script ranks likely runs by
timing, job overlap, and, for label events, log matches.
EOF
}

die() {
    local message="$1"
    local status="${2:-1}"
    echo "Error: $message" >&2
    exit "$status"
}

need() {
    command -v "$1" >/dev/null 2>&1 || {
        die "Missing required command: $1" 2
    }
}

event_url=""
print_job_url=false
verbose=false

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
        --job-url)
            print_job_url=true
            shift
            ;;
        --verbose)
            verbose=true
            shift
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
            usage
            die "Unknown option: $1" 2
            ;;
        *)
            break
            ;;
        esac
    done

    if [[ $# -ne 1 ]]; then
        usage
        exit 2
    fi

    event_url="$1"
}

parse_event_url() {
    local url="$1"

    if [[ ! "$url" =~ ^https://github\.com/([^/]+)/([^/]+)/pull/([0-9]+)#event-([0-9]+)$ ]]; then
        die "Expected a GitHub PR event URL like https://github.com/OWNER/REPO/pull/NUMBER#event-ID" 2
    fi

    event_owner="${BASH_REMATCH[1]}"
    event_repo_name="${BASH_REMATCH[2]}"
    event_pr_number="${BASH_REMATCH[3]}"
    event_id="${BASH_REMATCH[4]}"
    event_repo="${event_owner}/${event_repo_name}"
}

resolve_context() {
    event_json="$(
        gh api -H "Accept: application/vnd.github+json" \
            "repos/$event_repo/issues/events/$event_id"
    )"

    event_type="$(jq -r '.event' <<<"$event_json")"
    event_time="$(jq -r '.created_at' <<<"$event_json")"
    event_epoch="$(jq -rn --arg ts "$event_time" '$ts | fromdateiso8601')"
    event_actor="$(jq -r '.actor.login // "unknown"' <<<"$event_json")"
    event_label_name="$(jq -r '.label.name // empty' <<<"$event_json")"
    event_pr_title="$(jq -r '.issue.title // empty' <<<"$event_json")"

    pr_json="$(
        gh pr view "$event_pr_number" --repo "$event_repo" \
            --json headRefName,headRefOid,title,url
    )"

    pr_branch="$(jq -r '.headRefName' <<<"$pr_json")"
    pr_head_sha="$(jq -r '.headRefOid' <<<"$pr_json")"
}

select_candidates() {
    local runs_json

    runs_json="$(
        gh run list --repo "$event_repo" --branch "$pr_branch" --limit "$RUN_LIMIT" \
            --json databaseId,createdAt,displayTitle,event,headBranch,headSha,status,conclusion,url,workflowName
    )"

    candidates_json="$(
        jq -c \
            --arg title "$event_pr_title" \
            --arg sha "$pr_head_sha" \
            --argjson event_epoch "$event_epoch" \
            --argjson window "$WINDOW_SECONDS" \
            --argjson top_runs "$TOP_RUNS" '
                map(
                    .createdEpoch = (.createdAt | fromdateiso8601) |
                    .absDelta = ((.createdEpoch - $event_epoch) | abs) |
                    .sameSha = (.headSha == $sha) |
                    .sameTitle = (.displayTitle == $title) |
                    .candidateScore = [
                        (if .sameSha then 0 else 1 end),
                        .absDelta,
                        (if .sameTitle then 0 else 1 end)
                    ]
                )
                | (map(select(.absDelta <= $window)) | sort_by(.candidateScore)) as $near
                | if ($near | length) > 0 then
                    $near[:$top_runs]
                else
                    (map(select(.sameTitle or .sameSha)) | sort_by(.candidateScore)[:$top_runs])
                end
            ' <<<"$runs_json"
    )"

    if [[ "$(jq 'length' <<<"$candidates_json")" -eq 0 ]]; then
        die "No candidate workflow runs found for branch '$pr_branch'." 1
    fi
}

enrich_candidate() {
    local run_id="$1"
    local jobs_json run_json merged_json
    local log_match=false
    local matched_jobs_json='[]'

    jobs_json="$(
        gh api -H "Accept: application/vnd.github+json" \
            "repos/$event_repo/actions/runs/$run_id/jobs?per_page=100"
    )"

    run_json="$(jq --argjson id "$run_id" '.[] | select(.databaseId == $id)' <<<"$candidates_json")"

    merged_json="$(
        jq -n \
            --argjson run "$run_json" \
            --argjson jobs "$(jq '.jobs' <<<"$jobs_json")" \
            --argjson event_epoch "$event_epoch" '
                def epoch($x):
                    if ($x // "") == "" then null else ($x | fromdateiso8601) end;

                $run + {
                    jobs: (
                        $jobs | map(. + {
                            spansEvent: (
                                (epoch(.started_at) != null) and
                                (epoch(.completed_at) != null) and
                                (epoch(.started_at) <= $event_epoch) and
                                (epoch(.completed_at) >= $event_epoch)
                            )
                        })
                    ),
                    spansEvent: any($jobs[]?;
                        (epoch(.started_at) != null) and
                        (epoch(.completed_at) != null) and
                        (epoch(.started_at) <= $event_epoch) and
                        (epoch(.completed_at) >= $event_epoch)
                    )
                }
            '
    )"

    if [[ -n "$event_label_name" ]]; then
        while IFS=$'\t' read -r job_id job_url; do
            [[ -n "$job_id" ]] || continue

            if gh run view "$run_id" --repo "$event_repo" --job "$job_id" --log 2>/dev/null | grep -Fq "$event_label_name"; then
                log_match=true
                matched_jobs_json="$(
                    jq -c --arg id "$job_id" --arg url "$job_url" \
                        '. + [{"id": ($id | tonumber), "url": $url}]' <<<"$matched_jobs_json"
                )"
            fi
        done < <(jq -r '.jobs[] | [.id, .html_url] | @tsv' <<<"$jobs_json")
    fi

    jq -c -n \
        --argjson run "$merged_json" \
        --argjson matched "$matched_jobs_json" \
        --argjson log_match "$log_match" \
        '$run + {logMatch: $log_match, logMatchedJobs: $matched}'
}

enrich_candidates() {
    local run_id

    enriched_json='[]'
    while IFS= read -r run_id; do
        enriched_json="$(
            jq -c --argjson item "$(enrich_candidate "$run_id")" \
                '. + [$item]' <<<"$enriched_json"
        )"
    done < <(jq -r '.[].databaseId' <<<"$candidates_json")

    ranked_json="$(
        jq -c '
            sort_by(
                (if .logMatch then 0 else 1 end),
                (if .spansEvent then 0 else 1 end),
                .absDelta
            )
        ' <<<"$enriched_json"
    )"

    if [[ "$(jq 'length' <<<"$ranked_json")" -eq 0 ]]; then
        die "Failed to rank candidate workflow runs." 1
    fi

    best_json="$(jq '.[0]' <<<"$ranked_json")"
}

best_job_url() {
    jq -r '
        if (.logMatchedJobs | length) > 0 then
            .logMatchedJobs[0].url
        elif (.jobs | length) > 0 then
            .jobs[0].html_url
        else
            .url
        end
    ' <<<"$best_json"
}

print_verbose() {
    jq -r \
        --arg event_url "$event_url" \
        --arg repo "$event_repo" \
        --arg pr_number "$event_pr_number" \
        --arg event_type "$event_type" \
        --arg event_actor "$event_actor" \
        --arg event_label_name "$event_label_name" \
        --arg event_time "$event_time" \
        --arg pr_branch "$pr_branch" \
        --arg pr_head_sha "$pr_head_sha" '
            . as $sorted
            | $sorted[0] as $best
            | "Event:       \($event_url)",
              "Repo/PR:     \($repo) #\($pr_number)",
              "Type:        \($event_type)",
              "Actor:       \($event_actor)",
              (if $event_label_name != "" then "Label:       \($event_label_name)" else empty end),
              "Timestamp:   \($event_time)",
              "Branch/SHA:  \($pr_branch) @ \($pr_head_sha)",
              "",
              "Best match:",
              "  workflow:  \($best.workflowName)",
              "  run:       \($best.url)",
              (
                  if ($best.logMatchedJobs | length) > 0 then
                      "  job/log:   \($best.logMatchedJobs[0].url)"
                  elif ($best.jobs | length) > 0 then
                      "  job/log:   \($best.jobs[0].html_url)"
                  else
                      empty
                  end
              ),
              "  reason:    logMatch=\($best.logMatch) spansEvent=\($best.spansEvent) absDelta=\($best.absDelta)s",
              "",
              "Other candidates:",
              (
                  $sorted[]
                  | "  - \(.workflowName) | logMatch=\(.logMatch) spansEvent=\(.spansEvent) absDelta=\(.absDelta)s | \(.url)"
              )
        ' <<<"$ranked_json"
}

main() {
    need gh
    need jq
    need grep

    parse_args "$@"
    parse_event_url "$event_url"

    WINDOW_SECONDS="${WINDOW_SECONDS:-900}"
    RUN_LIMIT="${RUN_LIMIT:-100}"
    TOP_RUNS="${TOP_RUNS:-8}"

    resolve_context
    select_candidates
    enrich_candidates

    if [[ "$verbose" == true ]]; then
        print_verbose
        return 0
    fi

    if [[ "$print_job_url" == true ]]; then
        best_job_url
        return 0
    fi

    jq -r '.url' <<<"$best_json"
}

main "$@"
