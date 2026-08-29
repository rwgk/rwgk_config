#!/bin/bash
if [[ $# -ne 2 ]]; then
    echo "Error: expected exactly 2 arguments (columns and rows), got $#." >&2
    echo "Usage: ${0##*/} <columns> <rows>" >&2
    exit 2
fi

osascript \
    -e "tell application \"iTerm2\" to tell current session of current window to set columns to $1" \
    -e "tell application \"iTerm2\" to tell current session of current window to set rows to $2"
