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

# If on Windows (Git Bash or similar), strip credential helper blocks
if grep -qiE 'mingw|msys' <<<"$(uname -s)"; then
    sed -i.bak '/^\[credential "https:\/\/github\.com"\]/,/^\s*helper = !\/usr\/bin\/gh auth git-credential/d' "$DEST"
    sed -i.bak '/^\[credential "https:\/\/gist\.github\.com"\]/,/^\s*helper = !\/usr\/bin\/gh auth git-credential/d' "$DEST"
fi

# Cleanup backup file
rm -f "$DEST.bak"

echo "Updated ~/.gitconfig from $SOURCE"
