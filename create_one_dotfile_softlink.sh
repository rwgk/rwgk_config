#!/bin/bash
set -e

# Usage: create_one_dotfile_softlink.sh <dotfile_name> <target_path>
# Example: create_one_dotfile_softlink.sh .bashrc rwgk_config/bashrc

if [ $# -ne 2 ]; then
    echo "Usage: $0 <dotfile_name> <target_path>"
    echo "Example: $0 .bashrc rwgk_config/bashrc"
    exit 1
fi

dotfile="$1"
target="$2"

# Check if running as Administrator on Windows
if [[ "$OSTYPE" == "msys" ]]; then
    if ! net session >/dev/null 2>&1; then
        echo "FATAL: This script must be run as Administrator on Windows"
        exit 1
    fi
fi

cd "$HOME"

if [ -L "$dotfile" ]; then
    # Remove existing symlink if it exists
    rm "$dotfile"
elif [ -f "$dotfile" ]; then
    # Check if dotfile exists as a regular file (not a symlink)
    echo "FATAL: $dotfile already exists as a regular file"
    echo "Please remove or rename it before creating the symlink"
    echo "Current file: $(ls -l "$dotfile")"
    exit 1
fi

# Create new symlink
if [[ "$OSTYPE" == "msys" ]]; then
    # Windows - convert forward slashes to backslashes
    target_win="${target//\//\\}"
    cmd //c "mklink $dotfile $target_win"
else
    ln -s "$target" "$dotfile"
fi

ls -l "$dotfile"
