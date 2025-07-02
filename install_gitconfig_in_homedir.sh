#!/bin/bash

set -euo pipefail

SOURCE="$HOME/rwgk_config/git_stuff/_gitconfig"
DEST="$HOME/.gitconfig"

if [[ ! -f "$SOURCE" ]]; then
    echo "Error: Expected file '$SOURCE' not found. Is rwgk_config checked out?" >&2
    exit 1
fi

# Copy _gitconfig to .gitconfig
cp "$SOURCE" "$DEST"

# If user is 'rgrossekunst' or hostname ends with .nvidia.com, update email
if [[ "$USER" == "rgrossekunst" || "$(hostname -f)" == *.nvidia.com ]]; then
    sed -i.bak 's/^\(\s*email = \).*$/\1rgrossekunst@nvidia.com/' "$DEST"
fi

# Track whether credential helpers were removed
removed_credential_helpers=false

# If GitHub CLI is not available, strip credential helper blocks
if ! command -v gh >/dev/null 2>&1; then
    echo "INFO: GitHub CLI (gh command) is not available."
    sed -i.bak '/^\[credential "https:\/\/github\.com"\]/,/^\s*helper = !\/usr\/bin\/gh auth git-credential/d' "$DEST"
    sed -i.bak '/^\[credential "https:\/\/gist\.github\.com"\]/,/^\s*helper = !\/usr\/bin\/gh auth git-credential/d' "$DEST"
    removed_credential_helpers=true
fi

# Cleanup backup
rm -f "$DEST.bak"

# Final message
if $removed_credential_helpers; then
    echo "Updated ~/.gitconfig from $SOURCE ([credential] sections omitted)"
else
    echo "Updated ~/.gitconfig from $SOURCE"
fi
