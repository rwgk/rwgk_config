#!/bin/bash
set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "Usage: purge_old_cursor_versions.sh (no arguments allowed)" >&2
    exit 1
fi

versions_dir="$HOME/.local/share/cursor-agent/versions"

if [[ ! -d "$versions_dir" ]]; then
    echo "No Cursor Agent versions directory: $versions_dir"
    exit 0
fi

mapfile -t versions < <(ls -1dt "$versions_dir"/*/ 2>/dev/null || true)

if [[ ${#versions[@]} -le 1 ]]; then
    echo "Nothing to purge: ${#versions[@]} Cursor Agent version(s) found."
    exit 0
fi

echo "Keeping newest Cursor Agent version: ${versions[0]}"

for version_dir in "${versions[@]:1}"; do
    echo "Removing old Cursor Agent version: $version_dir"
    rm -rf -- "$version_dir"
done
