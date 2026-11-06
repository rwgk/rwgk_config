#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: install_gitconfig_in_homedir.sh [--apply]

Preview the host-adjusted Git configuration from ~/rwgk_config.
Use --apply to install it in ~/.gitconfig.
An unchanged configuration is reported and left untouched in either mode.
EOF
}

apply=false
for arg in "$@"; do
    case "$arg" in
    --apply) apply=true ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "Error: Unknown argument '$arg'" >&2
        usage >&2
        exit 2
        ;;
    esac
done

SOURCE="$HOME/rwgk_config/git_stuff/_gitconfig"
DEST="$HOME/.gitconfig"

if [[ ! -f "$SOURCE" ]]; then
    echo "Error: Expected file '$SOURCE' not found. Is rwgk_config checked out?" >&2
    exit 1
fi

# Prepare the exact configuration that both installation and preview use.
candidate=$(mktemp)
trap 'rm -f "$candidate" "$candidate.bak"' EXIT
cp "$SOURCE" "$candidate"

# If user is 'rgrossekunst' or hostname ends with .nvidia.com, update email
actual_user="${USER:-${USERNAME:-}}"

if [[ "$actual_user" == "rgrossekunst" || "$(hostname -f 2>/dev/null || hostname)" == *.nvidia.com ]]; then
    sed -i.bak 's/^\(\s*email = \).*$/\1rgrossekunst@nvidia.com/' "$candidate"
fi

# Track whether credential helpers were removed
removed_credential_helpers=false

# If GitHub CLI is not available, strip credential helper blocks
if ! command -v gh >/dev/null 2>&1; then
    echo "INFO: GitHub CLI (gh command) is not available."
    sed -i.bak '/^\[credential "https:\/\/github\.com"\]/,/^\s*helper = !\/usr\/bin\/gh auth git-credential/d' "$candidate"
    sed -i.bak '/^\[credential "https:\/\/gist\.github\.com"\]/,/^\s*helper = !\/usr\/bin\/gh auth git-credential/d' "$candidate"
    removed_credential_helpers=true
fi

if [[ -f "$DEST" ]] && cmp -s "$DEST" "$candidate"; then
    echo "No changes needed. ~/.gitconfig is already up to date."
    exit 0
fi

if ! $apply; then
    if [[ ! -e "$DEST" ]]; then
        echo "No ~/.gitconfig exists; would create it from $SOURCE."
    else
        diff_status=0
        diff -u --label "$DEST" --label "$DEST (proposed)" "$DEST" "$candidate" || diff_status=$?
        if ((diff_status > 1)); then
            exit "$diff_status"
        fi
    fi
    echo "Use --apply to install these changes."
    exit 0
fi

cp "$candidate" "$DEST"

# Final message
if $removed_credential_helpers; then
    echo "Updated ~/.gitconfig from $SOURCE ([credential] sections omitted)"
else
    echo "Updated ~/.gitconfig from $SOURCE"
fi
