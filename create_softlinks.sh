#!/bin/bash
set -e
set -x

# Check if running as Administrator on Windows
if [[ "$OSTYPE" == "msys" ]]; then
    # Use 'net session' command - only works for administrators
    if ! net session >/dev/null 2>&1; then
        echo "FATAL: This script must be run as Administrator on Windows"
        echo "Right-click Git Bash and select 'Run as administrator'"
        exit 1
    fi
fi

cd "$HOME"

# List of dotfiles to check/create
dotfiles=(.profile .bashrc .inputrc .vimrc)
targets=(rwgk_config/profile rwgk_config/bashrc rwgk_config/inputrc rwgk_config/vimrc)

# Check for existing regular files
found_files=()
for f in "${dotfiles[@]}"; do
    if [ -f "$f" ] && [ ! -L "$f" ]; then  # file exists and is NOT a symlink
        found_files+=("$f")
    fi
done

# If any regular files found, report and exit
if [ ${#found_files[@]} -gt 0 ]; then
    echo "FATAL: These files exist and are not symlinks:"
    printf "  %s\n" "${found_files[@]}"
    echo "Please (re)move them first."
    exit 1
fi

# Remove any existing symlinks
for f in "${dotfiles[@]}"; do
    if [ -L "$f" ]; then  # if it's a symlink
        rm "$f"
    fi
done

# Create new symlinks
for i in "${!dotfiles[@]}"; do
    if [[ "$OSTYPE" == "msys" ]]; then
        # Windows - convert forward slashes to backslashes for mklink
        target_win="${targets[$i]//\//\\}"
        cmd //c "mklink ${dotfiles[$i]} $target_win"
    else
        ln -s "${targets[$i]}" "${dotfiles[$i]}"
    fi
done

echo "Created symlinks:"
ls -l "${dotfiles[@]}"
